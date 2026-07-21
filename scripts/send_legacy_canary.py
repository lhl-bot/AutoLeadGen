#!/usr/bin/env python3
"""Send exactly one approval-bound legacy outreach canary.

The persistent workflow must remain paused.  This script creates a detached,
in-memory workflow view for one reviewed lead, runs the existing outbound
preflight and sender, and then verifies that exactly one message was recorded.
It is intentionally fail-closed and safe to re-run: a successful first run
makes the expected prior-outbound count mismatch on every later run.
"""
from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timezone
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
from types import SimpleNamespace
import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# These are process-local canary limits.  They never unpause the stored
# workflow or any long-running worker.
os.environ["OUTBOUND_HARD_PAUSE"] = "false"
os.environ["EMAIL_MAX_DAILY_PER_ACCOUNT"] = "1"
os.environ["EMAIL_REQUIRE_VERIFIED"] = "true"
os.environ["EMAIL_REQUIRE_VALID_RESEARCH"] = "true"
os.environ["EMAIL_REQUIRE_RECIPIENT_TIMEZONE"] = "true"
os.environ["EMAIL_CHECK_RECIPIENT_MX"] = "true"
os.environ["EMAIL_CHECK_RECIPIENT_DOMAIN_DNS"] = "true"
os.environ["EMAIL_REQUIRE_MIN_FIT_SCORE"] = "true"
os.environ["EMAIL_MIN_FIT_SCORE"] = "90"
os.environ["EMAIL_SAME_DOMAIN_COOLDOWN_HOURS"] = "168"
os.environ["CREDITS_ENABLED"] = "true"

from database import SessionLocal
from models import CreditWallet, EmailLog, Lead, LeadBrief, Workflow
from services.email_content import prepare_email_content
from services.email_preflight import (
    quality_gate_reason,
    temporary_send_block_reason,
    validate_lead_before_send,
)
from services.outbound_engine import (
    _workflow_bounce_pause_reason,
    send_lead_email,
)
from services.research_quality import outbound_content_quality_reason
from services.sender_accounts import select_sender_account


_DOMAIN = re.compile(r"^[a-z0-9.-]+\.[a-z]{2,}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


def _draft_digest(value: str) -> str:
    return hashlib.sha256((value or "").encode("utf-8")).hexdigest()


def _fail(reason: str) -> None:
    print(json.dumps({"status": "blocked", "reason": reason}, separators=(",", ":")))
    raise SystemExit(1)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workflow-id", type=int, required=True)
    parser.add_argument("--lead-id", type=int, required=True)
    parser.add_argument("--expected-domain", required=True)
    parser.add_argument("--expected-draft-sha256", required=True)
    parser.add_argument("--expected-prior-outbound", type=int, default=0)
    parser.add_argument(
        "--evaluate-at",
        help="ISO-8601 UTC time for dry-run window evaluation; forbidden with --execute",
    )
    parser.add_argument("--execute", action="store_true")
    return parser


def _evaluation_time(value: str | None) -> datetime:
    if not value:
        return datetime.now(timezone.utc)
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        _fail("evaluate_at_requires_timezone")
    return parsed.astimezone(timezone.utc)


def main() -> int:
    args = _parser().parse_args()
    expected_domain = args.expected_domain.strip().lower()
    expected_hash = args.expected_draft_sha256.strip().lower()
    if args.workflow_id <= 0 or args.lead_id <= 0:
        _fail("ids_must_be_positive")
    if args.expected_prior_outbound != 0:
        _fail("canary_requires_zero_prior_outbound")
    if not _DOMAIN.fullmatch(expected_domain):
        _fail("invalid_expected_domain")
    if not _SHA256.fullmatch(expected_hash):
        _fail("invalid_expected_draft_sha256")
    if args.execute and args.evaluate_at:
        _fail("execute_forbids_simulated_time")
    if args.execute and os.environ.get("LEGACY_CANARY_APPROVED", "").lower() != "true":
        _fail("legacy_canary_approval_missing")

    evaluated_at = _evaluation_time(args.evaluate_at)
    db = SessionLocal()
    try:
        workflow = db.query(Workflow).filter(Workflow.id == args.workflow_id).first()
        lead = db.query(Lead).filter(Lead.id == args.lead_id).first()
        if workflow is None or lead is None:
            _fail("workflow_or_lead_missing")
        if lead.workflow_id != workflow.id:
            _fail("lead_workflow_mismatch")
        if not workflow.email_sending_paused:
            _fail("persistent_workflow_must_be_paused")
        if lead.has_replied:
            _fail("lead_already_replied")
        if lead.status in {"bounced", "rejected", "unsubscribed"}:
            _fail("lead_status_not_sendable")
        if (lead.domain or "").strip().lower() != expected_domain:
            _fail("lead_domain_mismatch")
        if not hmac.compare_digest(_draft_digest(lead.ai_draft), expected_hash):
            _fail("draft_hash_mismatch")

        prior_outbound = db.query(EmailLog).filter(
            EmailLog.lead_id == lead.id,
            EmailLog.direction == "outbound",
        ).count()
        if prior_outbound != args.expected_prior_outbound:
            _fail("prior_outbound_mismatch")
        if (lead.followup_count or 0) != 0:
            _fail("followup_count_must_be_zero")

        brief = db.query(LeadBrief).filter(LeadBrief.lead_id == lead.id).first()
        if brief is None or brief.research_status != "valid":
            _fail("research_not_valid")

        checks = {
            "preflight": validate_lead_before_send(lead, db),
            "quality": quality_gate_reason(lead, db),
            "temporary": temporary_send_block_reason(
                lead,
                db,
                now=evaluated_at,
                require_timezone=True,
            ),
            "bounce": _workflow_bounce_pause_reason(workflow.id, db),
        }
        prepared = prepare_email_content(
            lead.ai_draft,
            company_name=lead.company_name,
            first_name=lead.first_name,
            sender_name="sender",
        )
        checks["content"] = outbound_content_quality_reason(
            f"{prepared.subject}\n\n{prepared.body}"
        )
        for name, reason in checks.items():
            if reason:
                _fail(f"{name}:{reason}")

        sender = select_sender_account(db, workflow, per_account_daily_cap=1)
        if sender.account is None:
            _fail("sender_unavailable_or_daily_cap_reached")

        if not args.execute:
            print(
                json.dumps(
                    {
                        "status": "ready",
                        "workflow_id": workflow.id,
                        "lead_id": lead.id,
                        "prior_outbound": prior_outbound,
                        "workflow_paused": True,
                        "message_sent": False,
                    },
                    separators=(",", ":"),
                )
            )
            return 0

        wallet = db.query(CreditWallet).filter(CreditWallet.user_id == workflow.user_id).first()
        if wallet is None or wallet.balance < 1:
            _fail("credit_wallet_unavailable")
        balance_before = wallet.balance
        workflow_outbound_before = db.query(EmailLog).join(Lead).filter(
            Lead.workflow_id == workflow.id,
            EmailLog.direction == "outbound",
        ).count()

        # A detached view permits one reviewed canary while the real workflow
        # stays paused for every background worker and all other leads.
        canary_workflow = SimpleNamespace(
            id=workflow.id,
            user_id=workflow.user_id,
            name=workflow.name,
            email_sending_paused=False,
            email_pause_reason=None,
            email_signature=workflow.email_signature,
        )
        result = asyncio.run(
            send_lead_email(
                lead,
                canary_workflow,
                db,
                charge_credits=True,
                raise_on_credit_error=True,
                manual_reviewed=False,
            )
        )
        if not result.get("success"):
            _fail(f"send_failed:{result.get('message') or 'unknown'}")

        db.expire_all()
        stored_workflow = db.query(Workflow).filter(Workflow.id == args.workflow_id).first()
        stored_lead = db.query(Lead).filter(Lead.id == args.lead_id).first()
        outbound_logs = db.query(EmailLog).filter(
            EmailLog.lead_id == args.lead_id,
            EmailLog.direction == "outbound",
        ).order_by(EmailLog.id.desc()).all()
        workflow_outbound_after = db.query(EmailLog).join(Lead).filter(
            Lead.workflow_id == args.workflow_id,
            EmailLog.direction == "outbound",
        ).count()
        stored_wallet = db.query(CreditWallet).filter(
            CreditWallet.user_id == stored_workflow.user_id
        ).first()

        if not stored_workflow.email_sending_paused:
            _fail("post_send_workflow_not_paused")
        if len(outbound_logs) != prior_outbound + 1:
            _fail("post_send_lead_log_delta_invalid")
        if workflow_outbound_after != workflow_outbound_before + 1:
            _fail("post_send_workflow_log_delta_invalid")
        if not outbound_logs[0].message_id:
            _fail("post_send_message_id_missing")
        if stored_wallet.balance != balance_before - 1:
            _fail("post_send_credit_delta_invalid")
        if stored_lead.status != "sent":
            _fail("post_send_lead_status_invalid")

        print(
            json.dumps(
                {
                    "status": "sent_and_verified",
                    "workflow_id": stored_workflow.id,
                    "lead_id": stored_lead.id,
                    "new_outbound_logs": 1,
                    "message_id_present": True,
                    "credit_delta": -1,
                    "workflow_paused": True,
                },
                separators=(",", ":"),
            )
        )
        return 0
    except SystemExit:
        db.rollback()
        raise
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
