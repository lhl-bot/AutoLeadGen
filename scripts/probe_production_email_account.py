#!/usr/bin/env python3
"""Approval-bound, no-send production SMTP/IMAP account probe."""
from __future__ import annotations

import argparse
import hmac
import json
import os
from pathlib import Path
import re
import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from database import SessionLocal
from product_v2 import models
from product_v2.enums import Channel, ChannelAccountHealth
from product_v2.production import (
    current_migration_revision,
    database_identity_fingerprint,
    expected_migration_head,
)
from product_v2.runtime.email_account_probe import (
    EmailAccountProbeError,
    load_email_probe_credentials,
    probe_email_credentials,
)
from product_v2.services.channel_accounts import record_trusted_channel_account_health
from runtime_config import environment, read_flag


_IDENTIFIER = re.compile(r"^[a-zA-Z0-9_.:/-]{3,255}$")


def _require_runtime_approval() -> None:
    if environment() not in {"staging", "production"}:
        raise SystemExit("Email account probe requires staging or production")
    if not read_flag("EMAIL_ACCOUNT_PROBE_APPROVED", default=False):
        raise SystemExit("EMAIL_ACCOUNT_PROBE_APPROVED=true is required")
    if not read_flag("OUTBOUND_HARD_PAUSE", default=True):
        raise SystemExit("Email account probe requires outbound hard pause")
    if not read_flag("ALLOW_REAL_EXTERNAL_CALLS", default=False):
        raise SystemExit("Email account probe requires external-call approval")
    if not read_flag("PRODUCT_V2_LEGACY_WRITERS_FROZEN", default=False):
        raise SystemExit("Email account probe requires frozen legacy writers")
    if os.environ.get("AUTOLEADGEN_CONNECTOR_MODE", "").strip().lower() != "real":
        raise SystemExit("Email account probe requires real connector mode")
    for name in ("PRODUCTION_CHANGE_ID", "RELEASE_SHA"):
        if not _IDENTIFIER.fullmatch(os.environ.get(name, "").strip()):
            raise SystemExit(f"{name} is required and must be a safe identifier")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--owner-id", type=int, required=True)
    parser.add_argument("--channel-account-id", type=int, required=True)
    parser.add_argument("--probe-id", required=True)
    args = parser.parse_args()
    if args.owner_id <= 0 or args.channel_account_id <= 0:
        raise SystemExit("Owner and channel-account ids must be positive")
    if not _IDENTIFIER.fullmatch(args.probe_id):
        raise SystemExit("--probe-id must be a safe, unique identifier")
    _require_runtime_approval()

    approved_fingerprint = os.environ.get(
        "PRODUCT_V2_APPROVED_DATABASE_FINGERPRINT", ""
    ).strip().lower()
    if not re.fullmatch(r"[0-9a-f]{64}", approved_fingerprint):
        raise SystemExit("A reviewed production database fingerprint is required")

    db = SessionLocal()
    try:
        if db.get_bind().dialect.name != "mysql":
            raise SystemExit("Email account probe requires MySQL")
        observed_fingerprint = database_identity_fingerprint(db)
        if not hmac.compare_digest(observed_fingerprint, approved_fingerprint):
            raise SystemExit("Connected database identity is not approved for this change")
        if current_migration_revision(db) != expected_migration_head():
            raise SystemExit("Database is not at the release migration head")

        correlation_id = (
            f"{os.environ['PRODUCTION_CHANGE_ID']}:{args.probe_id}"
        )
        if len(correlation_id) > 255:
            raise SystemExit("Combined change and probe identifier is too long")
        account = db.query(models.ChannelAccount).filter_by(
            id=args.channel_account_id,
            owner_id=args.owner_id,
            channel=Channel.EMAIL,
            provider="smtp",
            archived_at=None,
        ).first()
        try:
            loaded_account, credentials = load_email_probe_credentials(
                db,
                owner_id=args.owner_id,
                channel_account_id=args.channel_account_id,
            )
            account = loaded_account
            probe_email_credentials(credentials)
        except EmailAccountProbeError as exc:
            if account is not None:
                record_trusted_channel_account_health(
                    db,
                    account=account,
                    status=ChannelAccountHealth.UNHEALTHY,
                    error_code=exc.code,
                    source="production_email_probe",
                    correlation_id=correlation_id,
                )
                db.commit()
            print(
                json.dumps(
                    {
                        "status": "failed",
                        "channel_account_id": args.channel_account_id,
                        "error_code": exc.code,
                    },
                    separators=(",", ":"),
                )
            )
            return 1

        record_trusted_channel_account_health(
            db,
            account=account,
            status=ChannelAccountHealth.HEALTHY,
            error_code=None,
            source="production_email_probe",
            correlation_id=correlation_id,
        )
        db.commit()
        print(
            json.dumps(
                {
                    "status": "healthy",
                    "channel_account_id": account.id,
                    "smtp_authenticated": True,
                    "imap_authenticated": True,
                    "message_sent": False,
                },
                separators=(",", ":"),
            )
        )
        return 0
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
