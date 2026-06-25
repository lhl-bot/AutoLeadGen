import asyncio
import time
import random
import logging
import threading
import re
from datetime import datetime, timezone, timedelta
from typing import Optional, Dict, Any, List
from database import SessionLocal, db_retry, db_retry_async
from models import Workflow, Lead, EmailLog, ProcessedDomain
from services.search_engine import is_domain_quality_candidate, search_domain_results
from services.snovio_client import SnovioClient
from services.ai_writer import generate_email, build_persona_few_shot
from services.research_agent import build_and_save_lead_brief
from services.email_sender import send_email
from services.auth import decrypt_smtp_pass
from services.credits import InsufficientCreditsError, consume_credits, refund_credits
from services.email_content import build_email_html, prepare_email_content
from services.email_preflight import (
    is_email_good_for_lead,
    is_lead_sendable_now,
    temporary_send_block_reason,
    validate_lead_before_send,
)
from services.lead_scoring import apply_lead_score, build_outreach_context
from services.send_results import record_send_failure, record_send_success
from services.sender_accounts import select_sender_account
from services.suppression import generate_unsubscribe_token, suppression_reason

# ─── Global Snovio Client ───
import os
_snovio_client = None
def get_snovio_client():
    global _snovio_client
    if not _snovio_client:
        snovio_id = os.environ.get("SNOVIO_CLIENT_ID", "")
        snovio_secret = os.environ.get("SNOVIO_CLIENT_SECRET", "")
        _snovio_client = SnovioClient(snovio_id, snovio_secret)
    return _snovio_client

# ─── Logging Setup ───
logger = logging.getLogger("outbound_engine")
logger.setLevel(logging.INFO)

# File handler (auto-rotate at 5MB, keep 3 backups)
from logging.handlers import RotatingFileHandler
fh = RotatingFileHandler("outbound_engine.log", maxBytes=5*1024*1024, backupCount=3, encoding="utf-8")
fh.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
logger.addHandler(fh)

# Console handler
ch = logging.StreamHandler()
ch.setFormatter(logging.Formatter("[OUTBOUND] %(message)s"))
logger.addHandler(ch)

# Error counter per workflow
_error_counts = {}
_backoff_times = {}
_workflow_search_locks: Dict[int, threading.Lock] = {}
_workflow_search_locks_guard = threading.Lock()
_workflow_last_search_at: Dict[int, float] = {}
_linkedin_cooldown_until = 0.0
# Per-workflow cooldown for LeadContact search. LeadContact bills per contact
# returned, not per *new* lead — and the search doesn't paginate, so re-running it
# returns the same page-1 contacts (all duplicates) and bills again for zero new
# leads. When a search yields no new leads we back this workflow off so we stop
# paying for the same data.
_leadcontact_backoff_until: Dict[int, float] = {}
# Per-workflow LeadContact pagination cursor so each search fetches a NEW page
# instead of re-paying for page 1. {wf_id: {"query": <attempt>, "token": <nextPageToken>}}
_leadcontact_cursor: Dict[int, Dict[str, Any]] = {}
MAX_CONSECUTIVE_ERRORS = 5

def _int_env(name: str, default: int, min_value: int = 1, max_value: int = 100000) -> int:
    try:
        value = int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default
    return max(min_value, min(max_value, value))


def _bool_env(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.lower() not in {"0", "false", "no", "off"}


def _public_base_url() -> Optional[str]:
    for name in ("PUBLIC_APP_URL", "FRONTEND_BASE_URL", "APP_BASE_URL"):
        value = os.environ.get(name, "").strip().rstrip("/")
        if value:
            return value
    return None


def _unsubscribe_url_for_lead(lead: Lead) -> Optional[str]:
    if not lead.id or not lead.email:
        return None
    base_url = _public_base_url()
    if not base_url:
        return None
    token = generate_unsubscribe_token(lead.id, lead.email)
    return f"{base_url}/api/unsubscribe/{token}"


def _get_workflow_search_lock(wf_id: int) -> threading.Lock:
    with _workflow_search_locks_guard:
        if wf_id not in _workflow_search_locks:
            _workflow_search_locks[wf_id] = threading.Lock()
        return _workflow_search_locks[wf_id]


def is_workflow_search_running(wf_id: int) -> bool:
    return _get_workflow_search_lock(wf_id).locked()


def _workflow_search_cooldown_remaining(wf_id: int) -> int:
    cooldown = _int_env("SEARCH_WORKFLOW_COOLDOWN_SECONDS", 600, 0, 86400)
    if cooldown <= 0:
        return 0

    remaining = 0
    last_started_at = _workflow_last_search_at.get(wf_id, 0.0)
    remaining = max(remaining, int((last_started_at + cooldown) - time.time()))

    db = SessionLocal()
    try:
        latest_processed = db.query(ProcessedDomain.created_at).filter(
            ProcessedDomain.workflow_id == wf_id
        ).order_by(ProcessedDomain.created_at.desc()).first()
        if latest_processed and latest_processed[0]:
            latest_dt = latest_processed[0]
            if latest_dt.tzinfo is None:
                latest_dt = latest_dt.replace(tzinfo=timezone.utc)
            elapsed = (datetime.now(timezone.utc) - latest_dt).total_seconds()
            remaining = max(remaining, int(cooldown - elapsed))
    except Exception as e:
        logger.warning(f"Could not read persistent search cooldown for workflow #{wf_id}: {e}")
    finally:
        db.close()

    return max(0, remaining)


def launch_workflow_search(
    wf_id: int,
    batch_lead_limit: Optional[int] = None,
    max_domains: Optional[int] = None,
    ignore_cooldown: bool = False,
) -> bool:
    if is_workflow_search_running(wf_id):
        return False
    if not ignore_cooldown and _workflow_search_cooldown_remaining(wf_id) > 0:
        return False

    def runner():
        result = search_and_extract_leads(
            wf_id,
            batch_lead_limit=batch_lead_limit,
            max_domains=max_domains,
        )
        logger.info(f"Background search result for workflow #{wf_id}: {result}")

    _workflow_last_search_at[wf_id] = time.time()
    thread = threading.Thread(target=runner, name=f"workflow-search-{wf_id}", daemon=True)
    thread.start()
    return True


def _reset_search_offset(wf_id: int, reason: str) -> None:
    db = SessionLocal()
    try:
        wf = db.query(Workflow).filter(Workflow.id == wf_id).first()
        if not wf:
            return
        wf.search_offset = 0
        if _bool_env("ENABLE_KEYWORD_AUTO_MUTATION", False):
            from services.ai_writer import rotate_country_in_keyword
            new_keyword = rotate_country_in_keyword(wf.search_keywords)
            if new_keyword and new_keyword.lower() != wf.search_keywords.lower() and len(new_keyword) < 100:
                logger.info(f"Auto-mutated search keyword from '{wf.search_keywords}' to '{new_keyword}'")
                wf.search_keywords = new_keyword
        db.commit()
        logger.info(f"Reset search_offset to 0 for workflow '{wf.name}' ({reason})")
    finally:
        db.close()


def _advance_search_offset(wf_id: int, current_offset: int, step: int = 50) -> int:
    next_offset = current_offset + step
    db = SessionLocal()
    try:
        wf = db.query(Workflow).filter(Workflow.id == wf_id).first()
        if wf:
            wf.search_offset = next_offset
            db.commit()
            logger.info(f"Finished page, incremented search_offset to {wf.search_offset} for workflow '{wf.name}'")
    finally:
        db.close()
    return next_offset


def _add_company_only_lead(
    db,
    wf_id: int,
    pool_id: Optional[int],
    domain: str,
    company_name: Optional[str] = None,
    source_channel: str = "web",
    data_sources: str = "web search, website",
) -> bool:
    if not _bool_env("SEARCH_SAVE_COMPANIES_WITHOUT_EMAIL", True):
        return False

    query = db.query(Lead).filter(Lead.domain == domain)
    if pool_id:
        query = query.filter(Lead.client_pool_id == pool_id)
    else:
        query = query.filter(Lead.workflow_id == wf_id)
    if query.first():
        return False

    db.add(Lead(
        workflow_id=wf_id,
        client_pool_id=pool_id,
        domain=domain,
        company_name=company_name or domain,
        status="needs_email",
        source_channel=source_channel,
        data_sources=data_sources,
    ))
    return True


def _mark_lead_suppressed(lead: Lead, reason: str, db) -> None:
    if reason == "missing_email":
        lead.status = "needs_email"
    elif reason and reason.startswith("fit_score_too_low"):
        lead.status = "low_score"
    elif reason and reason.startswith("email_not_verified"):
        lead.status = "needs_email"  # Can re-verify later
    elif reason and reason.startswith("suppressed:"):
        lead.status = "unsubscribed"
    elif reason in {"lead_status_unsubscribed", "lead_status_rejected"}:
        lead.status = reason.replace("lead_status_", "")
    else:
        lead.status = "invalid_email"
    lead.reply_snippet = f"Suppressed before sending: {reason}"
    db.commit()


def _workflow_bounce_pause_reason(wf_id: int, db) -> Optional[str]:
    threshold_raw = os.environ.get("EMAIL_BOUNCE_RATE_PAUSE_THRESHOLD", "0.08")
    try:
        threshold = float(threshold_raw)
    except (TypeError, ValueError):
        threshold = 0.08
    if threshold <= 0:
        return None

    min_sent = _int_env("EMAIL_MIN_SENT_FOR_BOUNCE_PAUSE", 20, 1, 10000)
    total_sent = db.query(EmailLog).join(Lead).filter(
        Lead.workflow_id == wf_id,
        EmailLog.direction == "outbound",
    ).count()
    if total_sent < min_sent:
        return None

    bounced = db.query(Lead).filter(
        Lead.workflow_id == wf_id,
        Lead.status == "bounced",
    ).count()
    bounce_rate = bounced / total_sent if total_sent else 0
    if bounce_rate >= threshold:
        return f"bounce_rate {bounce_rate:.1%} >= {threshold:.1%} ({bounced}/{total_sent})"
    return None

async def run_outbound_loop():
    """
    Background daemon that continuously checks active workflows
    and executes Search -> Draft -> Send -> Followup stages based on limits.
    """
    logger.info("Outbound engine started")
    while True:
        try:
            await process_active_workflows()
        except Exception as e:
            logger.error(f"Error in outbound engine loop: {e}")
        
        await asyncio.sleep(60)  # Run cycle every minute


async def run_prospecting_loop():
    """
    Background daemon dedicated to finding new leads only.
    This keeps customer discovery running even when email sending workers are disabled.
    """
    interval = _int_env("PROSPECTING_LOOP_INTERVAL_SECONDS", 90, 20, 3600)
    logger.info("Prospecting engine started")
    while True:
        try:
            await process_prospecting_workflows()
        except Exception as e:
            logger.error(f"Error in prospecting engine loop: {e}")

        await asyncio.sleep(interval)


@db_retry_async()
async def process_prospecting_workflows():
    pipeline_target = _int_env("SEARCH_PIPELINE_TARGET", 50, 1, 1000)
    email_target = _int_env("SEARCH_WORKFLOW_EMAIL_TARGET", 200, 1, 100000)
    total_target = _int_env("SEARCH_WORKFLOW_TOTAL_TARGET", 250, 1, 100000)
    db = SessionLocal()
    try:
        active_workflows = db.query(Workflow.id, Workflow.name).filter(Workflow.status == "active").all()
        workflows_to_process = [(wf.id, wf.name) for wf in active_workflows]
    finally:
        db.close()

    for wf_id, wf_name in workflows_to_process:
        if is_workflow_search_running(wf_id):
            logger.info(f"Search already running for workflow #{wf_id} '{wf_name}', skipping this tick")
            continue
        cooldown_remaining = _workflow_search_cooldown_remaining(wf_id)
        if cooldown_remaining > 0:
            logger.info(f"Search cooldown active for workflow #{wf_id} '{wf_name}' ({cooldown_remaining}s remaining)")
            continue

        db = SessionLocal()
        try:
            pipeline_count = db.query(Lead).filter(
                Lead.workflow_id == wf_id,
                Lead.status.in_(["found", "drafted"])
            ).count()
            contactable_count = db.query(Lead).filter(
                Lead.workflow_id == wf_id,
                Lead.status.in_(["found", "drafted", "sent", "send_failed"]),
                Lead.email.isnot(None),
                Lead.email != "",
            ).count()
            total_search_count = db.query(Lead).filter(
                Lead.workflow_id == wf_id,
                Lead.status.in_(["found", "drafted", "sent", "send_failed", "needs_email"]),
            ).count()
        finally:
            db.close()

        if contactable_count >= email_target:
            logger.info(
                f"Prospecting workflow #{wf_id} '{wf_name}' has enough contactable leads "
                f"({contactable_count}/{email_target}); skipping search"
            )
            continue

        if total_search_count >= total_target:
            logger.info(
                f"Prospecting workflow #{wf_id} '{wf_name}' reached total lead cap "
                f"({total_search_count}/{total_target}); skipping search"
            )
            continue

        if pipeline_count >= pipeline_target:
            continue

        logger.info(
            f"Prospecting workflow #{wf_id} '{wf_name}' needs leads "
            f"(pipeline {pipeline_count}/{pipeline_target}, email {contactable_count}/{email_target}, total {total_search_count}/{total_target})"
        )
        launch_workflow_search(wf_id)

@db_retry_async()
async def process_active_workflows():
    db = SessionLocal()
    try:
        active_workflows = db.query(Workflow.id, Workflow.name).filter(Workflow.status == "active").all()
        workflows_to_process = [(wf.id, wf.name) for wf in active_workflows]
    finally:
        db.close()
        
    for wf_id, wf_name in workflows_to_process:
        # Check exponential backoff
        if wf_id in _backoff_times:
            if time.time() < _backoff_times[wf_id]:
                continue
            else:
                del _backoff_times[wf_id]
                # Give it one chance. If it errors, it will increment and backoff again next tick.
                await process_workflow(wf_id)
                continue

        if _error_counts.get(wf_id, 0) >= MAX_CONSECUTIVE_ERRORS:
            backoff_minutes = min(2 ** (_error_counts[wf_id] - MAX_CONSECUTIVE_ERRORS), 60)
            _backoff_times[wf_id] = time.time() + (backoff_minutes * 60)
            logger.warning(f"Workflow #{wf_id} '{wf_name}' backing off for {backoff_minutes} mins due to {_error_counts[wf_id]} errors")
            _error_counts[wf_id] += 1
            continue
        await process_workflow(wf_id)

@db_retry_async()
async def process_workflow(wf_id: int):
    try:
        # Load workflow data with a fresh session
        db = SessionLocal()
        try:
            wf = db.query(Workflow).filter(Workflow.id == wf_id).first()
            if not wf or wf.status != "active":
                return
            # Cache workflow data we need
            wf_daily_limit = wf.daily_limit
            wf_send_interval_min = wf.send_interval_min or 300
            wf_send_interval_max = wf.send_interval_max or 600
            wf_auto_followup = wf.auto_followup
            wf_followup_steps = wf.followup_steps
            wf_template_id = wf.template_id
            from services.followup_sequence import effective_max_followups
            wf_max_followups = effective_max_followups(wf_followup_steps, wf.max_followups or 3)
            wf_name = wf.name
            wf_user_id = wf.user_id
            wf_ai_prompt = wf.ai_prompt or "Introduce our company and ask for a quick meeting."
            wf_search_keywords = wf.search_keywords or ""
            wf_product_focus = wf.product_focus or ""
        finally:
            db.close()

        # Tokens used to decide whether a lead is on-target enough to spend a paid
        # LeadContact email/phone lookup (10 / 30 credits) on it.
        email_lookup_tokens = _email_lookup_target_tokens(wf_search_keywords, wf_product_focus)

        # 1. Check email daily limits. Do not return early here: LinkedIn and
        # WhatsApp have separate limits and should keep running when email is capped.
        email_limit_reached = False
        db = SessionLocal()
        try:
            today = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
            sent_today = db.query(EmailLog).join(Lead).filter(
                Lead.workflow_id == wf_id,
                EmailLog.direction == "outbound",
                EmailLog.sent_at >= today
            ).count()
            email_limit_reached = sent_today >= wf_daily_limit
        finally:
            db.close()

        # 2. Stage: Sending Drafted Emails (Respecting interval)
        db = SessionLocal()
        try:
            drafted_leads = []
            auto_send_drafts = _bool_env("OUTBOUND_AUTO_SEND_DRAFTS", False)
            if not auto_send_drafts:
                logger.info(f"Workflow #{wf_id} '{wf_name}' is in review mode; drafted emails will not auto-send.")
            if auto_send_drafts and not email_limit_reached:
                q = db.query(Lead).filter(
                    Lead.workflow_id == wf_id,
                    Lead.status == "drafted"
                )
                # Pre-filter low-score leads at query level for efficiency
                if _bool_env("EMAIL_REQUIRE_MIN_FIT_SCORE", False):
                    min_score = _int_env("EMAIL_MIN_FIT_SCORE", 60, 0, 100)
                    from sqlalchemy import or_
                    q = q.filter(or_(Lead.fit_score >= min_score, Lead.fit_score.is_(None)))
                drafted_leads = q.all()
            
            lead_to_send_id = None
            if drafted_leads:
                last_sent_log = db.query(EmailLog).join(Lead).filter(
                    Lead.workflow_id == wf_id,
                    EmailLog.direction == "outbound"
                ).order_by(EmailLog.sent_at.desc()).first()

                can_send = True
                if last_sent_log and last_sent_log.sent_at:
                    seconds_since_last = (datetime.now(timezone.utc) - last_sent_log.sent_at.replace(tzinfo=timezone.utc)).total_seconds()
                    if wf_send_interval_max < wf_send_interval_min:
                        wf_send_interval_max = wf_send_interval_min
                    required_interval = random.randint(wf_send_interval_min, wf_send_interval_max)

                    # Burst cooldown: after every N emails, enforce a longer pause
                    burst_size = _int_env("EMAIL_BURST_SIZE", 5, 2, 20)
                    burst_cooldown_min = _int_env("EMAIL_BURST_COOLDOWN_MIN", 1200, 300, 7200)
                    burst_cooldown_max = _int_env("EMAIL_BURST_COOLDOWN_MAX", 2400, 600, 14400)
                    today_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
                    sent_today_count = db.query(EmailLog).join(Lead).filter(
                        Lead.workflow_id == wf_id,
                        EmailLog.direction == "outbound",
                        EmailLog.sent_at >= today_start
                    ).count()
                    if sent_today_count > 0 and sent_today_count % burst_size == 0:
                        required_interval = random.randint(burst_cooldown_min, burst_cooldown_max)
                        logger.info(f"Burst cooldown active: {sent_today_count} emails sent today, waiting {required_interval}s")

                    if seconds_since_last < required_interval:
                        can_send = False
                
                if can_send:
                    # Temporary reasons that shouldn't suppress a lead
                    _TEMP_REASONS = {"outside_working_hours", "domain_cooldown"}
                    for candidate in drafted_leads:
                        is_sendable, reason = is_lead_sendable_now(candidate, db)
                        if is_sendable:
                            lead_to_send_id = candidate.id
                            break
                        elif reason not in _TEMP_REASONS:
                            # Permanently unsendable — mark so it doesn't block the queue
                            _mark_lead_suppressed(candidate, reason or "unknown", db)
                            logger.info(f"Suppressed drafted lead {candidate.id} ({candidate.email}): {reason}")
        finally:
            db.close()
        
        if lead_to_send_id:
            # Send with fresh session
            db = SessionLocal()
            try:
                lead = db.query(Lead).filter(Lead.id == lead_to_send_id).first()
                wf = db.query(Workflow).filter(Workflow.id == wf_id).first()
                if lead and wf:
                    await send_lead_email(lead, wf, db)
                    _error_counts[wf_id] = 0
            finally:
                db.close()
            return

        # 2.5 Stage: Send LinkedIn Invitations (if enabled)
        db = SessionLocal()
        try:
            wf = db.query(Workflow).filter(Workflow.id == wf_id).first()
            enable_linkedin = wf.enable_linkedin if wf else False
            linkedin_template = wf.linkedin_invite_message if wf else ""
            linkedin_daily_limit = getattr(wf, 'linkedin_daily_limit', 20) or 20
        finally:
            db.close()

        if enable_linkedin:
            await _send_linkedin_invites(wf_id, wf_name, linkedin_template, linkedin_daily_limit, wf_ai_prompt)

        # 2.6 Stage: Send WhatsApp Messages (if enabled)
        db = SessionLocal()
        try:
            wf = db.query(Workflow).filter(Workflow.id == wf_id).first()
            enable_whatsapp = wf.enable_whatsapp if wf else False
            whatsapp_template = wf.whatsapp_message_template if wf else ""
        finally:
            db.close()

        if enable_whatsapp:
            await _send_whatsapp_messages(wf_id, wf_name, whatsapp_template, wf_ai_prompt)
                
        # 3. Stage: Drafting found leads
        db = SessionLocal()
        try:
            found_leads = db.query(Lead).filter(
                Lead.workflow_id == wf_id,
                Lead.status == "found"
            ).limit(5).all()
            lead_infos = [(lead.id, lead.domain, lead.company_name, lead.job_title, lead.email, lead.first_name, lead.last_name, lead.linkedin_url, lead.whatsapp_number) for lead in found_leads]
        finally:
            db.close()
        
        if lead_infos:
            async def process_single_lead(lead_id, domain, company_name, job_title, email, first_name, last_name, linkedin_url, whatsapp_number):
                credit_charged = False
                try:
                    # 0. Try LeadContact email enrichment if no email or unverified.
                    #    Email lookups cost 10 credits each (and bill even on a miss),
                    #    so only spend on leads that look on-target.
                    has_usable_email = bool(email and email.strip())
                    require_relevance = _bool_env("LEADCONTACT_ENRICH_REQUIRE_RELEVANCE", True)
                    worth_lookup = (not require_relevance) or _lead_worth_email_lookup(
                        company_name, job_title, email_lookup_tokens
                    )
                    _prescore = None  # cheap rule-based fit score, computed lazily; shared by email+phone gates

                    # #2 Reuse an email already obtained for this same person — free (no LeadContact charge).
                    if (not has_usable_email) and linkedin_url:
                        reused = _find_existing_email(linkedin_url, exclude_lead_id=lead_id)
                        if reused:
                            email = reused
                            has_usable_email = True
                            db_lc = SessionLocal()
                            try:
                                lead_lc = db_lc.query(Lead).filter(Lead.id == lead_id).first()
                                if lead_lc:
                                    lead_lc.email = reused
                                    if (not lead_lc.domain or not lead_lc.domain.strip()) and "@" in reused:
                                        lead_lc.domain = reused.split("@")[-1]
                                        domain = lead_lc.domain
                                    lead_lc.data_sources = (lead_lc.data_sources or "") + ",reuse"
                                    db_lc.commit()
                                    logger.info(f"Reused existing email for {linkedin_url}: {reused} (no LeadContact charge)")
                            finally:
                                db_lc.close()

                    if (not has_usable_email) and linkedin_url and not worth_lookup:
                        logger.info(
                            f"[LeadContact] Skipping paid email lookup for off-target lead "
                            f"{lead_id} ({company_name or '?'} / {job_title or '?'})"
                        )

                    if (not has_usable_email) and linkedin_url and worth_lookup:
                        proceed_email = True
                        # #3 Fit gate: don't spend 10 credits on a clearly low-fit lead.
                        email_min = _int_env("LEADCONTACT_ENRICH_MIN_FIT_SCORE", 30, 0, 100)
                        if email_min > 0:
                            _prescore = _prescore_lead(lead_id, wf_id)
                            if _prescore is not None and _prescore < email_min:
                                proceed_email = False
                                logger.info(f"[LeadContact] Skipping email lookup for low-fit lead {lead_id} (score {_prescore} < {email_min})")
                        # #5 Daily budget cap.
                        if proceed_email and not _lc_budget_check(wf_id, "email"):
                            proceed_email = False
                            logger.warning(f"[LeadContact] Daily email-lookup budget reached for workflow {wf_id}; skipping")

                        if proceed_email:
                            _lc_key = os.environ.get("LEADCONTACT_API_KEY", "").strip()
                            if _lc_key:
                                from services.leadcontact_client import LeadContactClient
                                lc = LeadContactClient(_lc_key)
                                lc_result = lc.query_email_with_validation(linkedin_url)
                                lc_email = lc_result.get("email")
                                if lc_email:
                                    _lc_budget_record(wf_id, "email")  # count only billed hits
                                    email = lc_email
                                    has_usable_email = True
                                    db_lc = SessionLocal()
                                    try:
                                        lead_lc = db_lc.query(Lead).filter(Lead.id == lead_id).first()
                                        if lead_lc:
                                            lead_lc.email = lc_email
                                            if (not lead_lc.domain or not lead_lc.domain.strip()) and "@" in lc_email:
                                                lead_lc.domain = lc_email.split("@")[-1]
                                                domain = lead_lc.domain
                                            lead_lc.email_validation_status = "valid" if lc_result.get("valid") else "catch-all"
                                            lead_lc.email_verified = bool(lc_result.get("valid"))
                                            lead_lc.data_sources = (lead_lc.data_sources or "") + ",leadcontact"
                                            db_lc.commit()
                                            logger.info(f"[LeadContact] Email found for {linkedin_url}: {lc_email}")
                                    finally:
                                        db_lc.close()
                                else:
                                    logger.info(f"[LeadContact] No email found for {linkedin_url}: {lc_result.get('error', 'unknown')}")

                    # 0.1. Try LeadContact phone enrichment if WhatsApp channel is enabled.
                    #      Phone lookups cost 30 credits each (3x email) — gate harder:
                    #      relevance + a higher fit bar (#4) + daily budget (#5).
                    if linkedin_url and not whatsapp_number and enable_whatsapp and worth_lookup:
                        proceed_phone = True
                        phone_min = _int_env("LEADCONTACT_PHONE_MIN_FIT_SCORE", 60, 0, 100)
                        if phone_min > 0:
                            if _prescore is None:
                                _prescore = _prescore_lead(lead_id, wf_id)
                            if _prescore is not None and _prescore < phone_min:
                                proceed_phone = False
                                logger.info(f"[LeadContact] Skipping phone lookup for lead {lead_id} (score {_prescore} < {phone_min})")
                        if proceed_phone and not _lc_budget_check(wf_id, "phone"):
                            proceed_phone = False
                            logger.warning(f"[LeadContact] Daily phone-lookup budget reached for workflow {wf_id}; skipping")

                        _lc_key = os.environ.get("LEADCONTACT_API_KEY", "").strip()
                        if proceed_phone and _lc_key:
                            from services.leadcontact_client import LeadContactClient
                            lc = LeadContactClient(_lc_key)
                            phone_result = lc.query_phone(linkedin_url)
                            phones = phone_result.get("phones", [])
                            if phones:
                                _lc_budget_record(wf_id, "phone")  # count only billed hits
                                # Find the first valid phone
                                best = next((p for p in phones if p["valid"]), phones[0])
                                whatsapp_number = best["phone"]
                                db_lc = SessionLocal()
                                try:
                                    lead_lc = db_lc.query(Lead).filter(Lead.id == lead_id).first()
                                    if lead_lc:
                                        lead_lc.whatsapp_number = whatsapp_number
                                        db_lc.commit()
                                        logger.info(f"[LeadContact] Phone found for {linkedin_url}: {whatsapp_number}")
                                finally:
                                    db_lc.close()

                    # Cost gate: with no usable email this lead can't be emailed, so
                    # don't spend a research brief + AI draft (LLM + credits) on it.
                    # Park it as needs_email — it leaves the "found" queue so we never
                    # re-pay LeadContact to look it up again.
                    if not has_usable_email:
                        db_ne = SessionLocal()
                        try:
                            lead_ne = db_ne.query(Lead).filter(Lead.id == lead_id).first()
                            if lead_ne:
                                lead_ne.status = "needs_email"
                                if not lead_ne.email_validation_status:
                                    lead_ne.email_validation_status = "no_email"
                                db_ne.commit()
                        finally:
                            db_ne.close()
                        logger.info(f"Lead {lead_id} has no usable email; marked needs_email (skipped brief/draft)")
                        return

                    # 0.2. Verify email before research & drafting
                    if has_usable_email:
                        from services.email_verifier import verify_email
                        verif = await verify_email(email)
                        v_status = verif.get("status", "unknown")
                        
                        db_v = SessionLocal()
                        try:
                            lead_obj = db_v.query(Lead).filter(Lead.id == lead_id).first()
                            if lead_obj:
                                lead_obj.email_validation_status = v_status
                                lead_obj.email_verified = (v_status == "valid")
                                if v_status == "invalid":
                                    lead_obj.status = "invalid_email"
                                    db_v.commit()
                                    logger.info(f"Skipping research and drafting for invalid email: {email} (Lead {lead_id})")
                                    return
                                db_v.commit()
                        finally:
                            db_v.close()

                    # 0.3. Template mode: if the workflow uses an email template (A/B group),
                    # render a variant directly — no research brief or AI credit needed.
                    if wf_template_id:
                        template_used = False
                        db_tpl = SessionLocal()
                        try:
                            from models import EmailTemplate
                            from services.email_templates import pick_variant, render_template
                            base_tpl = db_tpl.query(EmailTemplate).filter(EmailTemplate.id == wf_template_id).first()
                            chosen = None
                            if base_tpl and base_tpl.ab_group:
                                variants = db_tpl.query(EmailTemplate).filter(
                                    EmailTemplate.user_id == base_tpl.user_id,
                                    EmailTemplate.ab_group == base_tpl.ab_group,
                                    EmailTemplate.is_active.is_(True),
                                ).all()
                                chosen = pick_variant(variants) or (base_tpl if base_tpl.is_active else None)
                            elif base_tpl and base_tpl.is_active:
                                chosen = base_tpl

                            if chosen:
                                lead_obj = db_tpl.query(Lead).filter(Lead.id == lead_id).first()
                                if lead_obj:
                                    rendered = render_template(chosen, lead_obj)
                                    subject = rendered["subject"].strip()
                                    body = rendered["body"].strip()
                                    lead_obj.ai_draft = f"Subject: {subject}\n\n{body}" if subject else body
                                    lead_obj.status = "drafted"
                                    lead_obj.template_id = chosen.id
                                    lead_obj.template_variant = chosen.variant_label
                                    db_tpl.commit()
                                    template_used = True
                                    logger.info(f"Template draft (variant {chosen.variant_label}) for {email} (workflow: {wf_name})")
                        finally:
                            db_tpl.close()
                        # Template produced the draft → skip research + AI for this lead.
                        # If the template was missing/inactive, fall through to AI generation.
                        if template_used:
                            return

                    # 1. Generate Deep Research Brief
                    await build_and_save_lead_brief(lead_id, domain)
                    
                    # 2. Retrieve Brief from DB
                    brief_summary = "No detailed brief available."
                    outreach_context = ""
                    db_b = SessionLocal()
                    try:
                        from models import CustomerPersona, LeadBrief
                        lead_obj = db_b.query(Lead).filter(Lead.id == lead_id).first()
                        workflow_obj = db_b.query(Workflow).filter(Workflow.id == wf_id).first()
                        persona_obj = None
                        if workflow_obj and workflow_obj.persona_id:
                            persona_obj = db_b.query(CustomerPersona).filter(CustomerPersona.id == workflow_obj.persona_id).first()
                        brief = db_b.query(LeadBrief).filter(LeadBrief.lead_id == lead_id).first()
                        if brief:
                            brief_parts = []
                            if brief.company_overview:
                                brief_parts.append(f"Company Overview: {brief.company_overview}")
                            if getattr(brief, 'specific_products', None):
                                brief_parts.append(f"Their Specific Products: {brief.specific_products}")
                            if getattr(brief, 'recent_activity', None):
                                brief_parts.append(f"Recent Activity: {brief.recent_activity}")
                            if brief.pain_points:
                                brief_parts.append(f"Likely Pain Points: {brief.pain_points}")
                            if getattr(brief, 'personalization_hook', None):
                                brief_parts.append(f"KEY PERSONALIZATION HOOK (use this!): {brief.personalization_hook}")
                            if brief.value_proposition_alignment:
                                brief_parts.append(f"Why We Can Help: {brief.value_proposition_alignment}")
                            brief_summary = "\n".join(brief_parts) if brief_parts else brief_summary
                        if lead_obj:
                            score = apply_lead_score(db_b, lead_obj, workflow=workflow_obj, persona=persona_obj)
                            db_b.commit()
                            outreach_context = build_outreach_context(workflow_obj, persona_obj, score)
                            persona_id = workflow_obj.persona_id if workflow_obj else None
                            # ── Score gate: skip drafting for low-fit leads ──
                            if _bool_env("EMAIL_REQUIRE_MIN_FIT_SCORE", False):
                                min_score = _int_env("EMAIL_MIN_FIT_SCORE", 60, 0, 100)
                                if score is not None and score < min_score:
                                    lead_obj.status = "low_score"
                                    lead_obj.reply_snippet = f"Skipped: fit_score {score} < {min_score}"
                                    db_b.commit()
                                    logger.info(f"Skipping draft for {email}: fit_score={score} < {min_score}")
                                    return
                        else:
                            persona_id = None
                    finally:
                        db_b.close()

                    # 3. Generate hyper-personalized draft
                    enriched_prompt = wf_ai_prompt
                    if outreach_context:
                        enriched_prompt = f"{wf_ai_prompt}\n\nAI LEAD QUALIFICATION CONTEXT:\n{outreach_context}"
                    
                    db_credit = SessionLocal()
                    try:
                        consume_credits(
                            db_credit,
                            wf_user_id,
                            "ai_email_draft",
                            description=f"AI email draft for lead #{lead_id}",
                            reference_type="lead",
                            reference_id=lead_id,
                            metadata={"workflow_id": wf_id, "workflow_name": wf_name},
                        )
                        credit_charged = True
                    except InsufficientCreditsError as credit_err:
                        logger.warning(
                            f"Skipping AI draft for lead {lead_id}: insufficient credits "
                            f"(required={credit_err.required}, balance={credit_err.balance})"
                        )
                        return
                    finally:
                        db_credit.close()

                    # Build few-shot examples with a short-lived session, then
                    # release the connection BEFORE the slow LLM call so we don't
                    # hold a MySQL connection idle for the whole generation.
                    db_shot = SessionLocal()
                    try:
                        few_shot_prompt = build_persona_few_shot(db_shot, persona_id)
                    finally:
                        db_shot.close()

                    draft = await asyncio.to_thread(
                        generate_email,
                        first_name=first_name or "",
                        last_name=last_name or "",
                        company_name=company_name,
                        target_role=job_title,
                        website_summary=brief_summary,
                        template=enriched_prompt,
                        few_shot_prompt=few_shot_prompt,
                    )
                    if draft:
                        db2 = SessionLocal()
                        try:
                            lead_obj = db2.query(Lead).filter(Lead.id == lead_id).first()
                            if lead_obj:
                                lead_obj.ai_draft = draft
                                lead_obj.status = "drafted"
                                db2.commit()
                                logger.info(f"Drafted email for {email} (workflow: {wf_name})")
                        finally:
                            db2.close()
                    elif credit_charged:
                        db_refund = SessionLocal()
                        try:
                            refund_credits(
                                db_refund,
                                wf_user_id,
                                "ai_email_draft",
                                description=f"Refund empty AI email draft for lead #{lead_id}",
                                reference_type="lead",
                                reference_id=lead_id,
                            )
                            credit_charged = False
                        finally:
                            db_refund.close()
                except Exception as e:
                    if credit_charged:
                        db_refund = SessionLocal()
                        try:
                            refund_credits(
                                db_refund,
                                wf_user_id,
                                "ai_email_draft",
                                description=f"Refund failed AI email draft for lead #{lead_id}",
                                reference_type="lead",
                                reference_id=lead_id,
                            )
                        finally:
                            db_refund.close()
                    logger.error(f"Error drafting for {email}: {e}")

            # Run all draft tasks concurrently
            tasks = [process_single_lead(lid, d, cn, jt, e, fn, ln, li, wn) for lid, d, cn, jt, e, fn, ln, li, wn in lead_infos]
            await asyncio.gather(*tasks)
            return

        # 4. Stage: Auto-Followup for replied leads (with full conversation context)
        if wf_auto_followup:
            db = SessionLocal()
            try:
                replied_leads = db.query(Lead).filter(
                    Lead.workflow_id == wf_id,
                    Lead.status == "replied",
                    Lead.followup_count < wf_max_followups
                ).all()
                replied_infos = [(l.id, l.email, l.first_name, l.company_name, l.reply_snippet, l.last_reply_at, l.followup_count) for l in replied_leads]
            finally:
                db.close()
            
            for lead_id, email, first_name, company_name, reply_snippet, last_reply_at, current_followup_count in replied_infos:
                credit_charged = False
                if last_reply_at:
                    hours_since_reply = (datetime.now(timezone.utc) - last_reply_at.replace(tzinfo=timezone.utc)).total_seconds() / 3600
                    if hours_since_reply < 24:
                        continue
                
                try:
                    from services.followup_engine import draft_followup_email, analyze_reply_intent
                    
                    intent_data = await asyncio.to_thread(analyze_reply_intent, reply_snippet or "")
                    intent = intent_data.get("intent", "other")
                    
                    db2 = SessionLocal()
                    try:
                        lead_obj = db2.query(Lead).filter(Lead.id == lead_id).first()
                        if not lead_obj:
                            continue
                            
                        if intent == "not_interested":
                            lead_obj.status = "rejected"
                            db2.commit()
                            logger.info(f"Lead {email} marked rejected (not interested)")
                            continue
                        
                        if intent in ("interested", "more_info"):
                            # Build full conversation history from email_logs
                            conversation_history = []
                            email_logs = db2.query(EmailLog).filter(
                                EmailLog.lead_id == lead_id
                            ).order_by(EmailLog.sent_at.asc()).all()
                            for log in email_logs:
                                conversation_history.append({
                                    "direction": log.direction,
                                    "subject": log.subject or "",
                                    "body": log.body or "",
                                    "sent_at": str(log.sent_at) if log.sent_at else "",
                                })
                            
                            followup_round = current_followup_count + 1

                            try:
                                consume_credits(
                                    db2,
                                    wf_user_id,
                                    "ai_email_draft",
                                    description=f"AI follow-up draft for lead #{lead_id}",
                                    reference_type="lead",
                                    reference_id=lead_id,
                                    metadata={"workflow_id": wf_id, "workflow_name": wf_name},
                                )
                                credit_charged = True
                            except InsufficientCreditsError as credit_err:
                                logger.warning(
                                    f"Skipping follow-up draft for lead {lead_id}: insufficient credits "
                                    f"(required={credit_err.required}, balance={credit_err.balance})"
                                )
                                continue
                            
                            # Release the DB connection back to the pool before the
                            # slow LLM call (consume_credits commits when credits are
                            # enabled; commit here too so a no-credit run doesn't hold
                            # an open transaction idle across the generation).
                            db2.commit()

                            followup_draft = await asyncio.to_thread(
                                draft_followup_email,
                                lead_data={"first_name": first_name, "company_name": company_name},
                                reply_text=reply_snippet or "",
                                intent=intent,
                                conversation_history=conversation_history,
                                followup_round=followup_round,
                            )
                            if followup_draft:
                                lead_obj.ai_draft = followup_draft
                                lead_obj.status = "drafted"
                                lead_obj.followup_count += 1
                                db2.commit()
                                logger.info(f"Auto-followup drafted for {email} (round #{followup_round}, strategy: {'value' if followup_round==1 else 'social_proof' if followup_round==2 else 'urgency'})")
                                return
                            elif credit_charged:
                                refund_credits(
                                    db2,
                                    wf_user_id,
                                    "ai_email_draft",
                                    description=f"Refund empty AI follow-up draft for lead #{lead_id}",
                                    reference_type="lead",
                                    reference_id=lead_id,
                                )
                                credit_charged = False
                    finally:
                        db2.close()
                except Exception as e:
                    if credit_charged:
                        db_refund = SessionLocal()
                        try:
                            refund_credits(
                                db_refund,
                                wf_user_id,
                                "ai_email_draft",
                                description=f"Refund failed AI follow-up draft for lead #{lead_id}",
                                reference_type="lead",
                                reference_id=lead_id,
                            )
                        finally:
                            db_refund.close()
                    logger.error(f"Followup error for {email}: {e}")

        # 4.7 Stage: Auto-Followup for non-replied leads (Cold Drip sequence)
        if wf_auto_followup:
            db = SessionLocal()
            try:
                # Find leads that were emailed (status = 'sent'), have not replied, and have followup_count < wf_max_followups
                cold_leads = db.query(Lead).filter(
                    Lead.workflow_id == wf_id,
                    Lead.status == "sent",
                    Lead.followup_count < wf_max_followups
                ).limit(5).all()
                cold_infos = [
                    (l.id, l.email, l.first_name, l.company_name, l.followup_count)
                    for l in cold_leads
                ]
            finally:
                db.close()

            for lead_id, email, first_name, company_name, current_followup_count in cold_infos:
                credit_charged = False
                try:
                    db2 = SessionLocal()
                    try:
                        lead_obj = db2.query(Lead).filter(Lead.id == lead_id).first()
                        if not lead_obj or lead_obj.status != "sent":
                            continue

                        # Check elapsed time since last outbound email log
                        last_outbound = db2.query(EmailLog).filter(
                            EmailLog.lead_id == lead_id,
                            EmailLog.direction == "outbound"
                        ).order_by(EmailLog.sent_at.desc()).first()

                        if not last_outbound:
                            continue

                        # Interval: configured per-step sequence if present, else the global env default.
                        from services.followup_sequence import interval_hours_for_round, instruction_for_round
                        default_interval_hours = float(os.environ.get("EMAIL_FOLLOWUP_INTERVAL_HOURS", 72))
                        next_round = current_followup_count + 1
                        interval_hours = interval_hours_for_round(wf_followup_steps, next_round, default_interval_hours)
                        elapsed_hours = (datetime.now(timezone.utc) - last_outbound.sent_at.replace(tzinfo=timezone.utc)).total_seconds() / 3600

                        if elapsed_hours >= interval_hours:
                            # Build conversation history from email_logs
                            conversation_history = []
                            email_logs = db2.query(EmailLog).filter(
                                EmailLog.lead_id == lead_id
                            ).order_by(EmailLog.sent_at.asc()).all()
                            for log in email_logs:
                                conversation_history.append({
                                    "direction": log.direction,
                                    "subject": log.subject or "",
                                    "body": log.body or "",
                                    "sent_at": str(log.sent_at) if log.sent_at else "",
                                })

                            followup_round = current_followup_count + 1

                            # Append this step's custom instruction (if configured) to the AI prompt.
                            step_instruction = instruction_for_round(wf_followup_steps, followup_round)
                            round_ai_prompt = wf_ai_prompt or ""
                            if step_instruction:
                                round_ai_prompt = f"{round_ai_prompt}\n\nFor this follow-up specifically: {step_instruction}".strip()

                            from services.followup_engine import draft_cold_followup_email
                            try:
                                consume_credits(
                                    db2,
                                    wf_user_id,
                                    "ai_email_draft",
                                    description=f"AI cold follow-up draft for lead #{lead_id}",
                                    reference_type="lead",
                                    reference_id=lead_id,
                                    metadata={"workflow_id": wf_id, "workflow_name": wf_name},
                                )
                                credit_charged = True
                            except InsufficientCreditsError as credit_err:
                                logger.warning(
                                    f"Skipping cold follow-up draft for lead {lead_id}: insufficient credits "
                                    f"(required={credit_err.required}, balance={credit_err.balance})"
                                )
                                continue

                            followup_draft = await asyncio.to_thread(
                                draft_cold_followup_email,
                                lead_data={"first_name": first_name, "company_name": company_name},
                                conversation_history=conversation_history,
                                followup_round=followup_round,
                                ai_prompt=round_ai_prompt
                            )

                            if followup_draft:
                                lead_obj.ai_draft = followup_draft
                                lead_obj.status = "drafted"
                                lead_obj.followup_count += 1
                                db2.commit()
                                logger.info(f"Cold follow-up #{followup_round} drafted for {email} (workflow: {wf_name})")
                            elif credit_charged:
                                refund_credits(
                                    db2,
                                    wf_user_id,
                                    "ai_email_draft",
                                    description=f"Refund empty AI cold follow-up draft for lead #{lead_id}",
                                    reference_type="lead",
                                    reference_id=lead_id,
                                )
                                credit_charged = False
                    finally:
                        db2.close()
                except Exception as e:
                    if credit_charged:
                        db_refund = SessionLocal()
                        try:
                            refund_credits(
                                db_refund,
                                wf_user_id,
                                "ai_email_draft",
                                description=f"Refund failed AI cold follow-up draft for lead #{lead_id}",
                                reference_type="lead",
                                reference_id=lead_id,
                            )
                        finally:
                            db_refund.close()
                    logger.error(f"Cold followup error for lead {lead_id}: {e}")

        # 5. Stage: Searching for new leads. This is independently switchable
        # so outreach/LinkedIn workers can run without consuming search credits.
        if not _bool_env("ENABLE_WORKFLOW_SEARCH_STAGE", True):
            return

        db = SessionLocal()
        try:
            pipeline_count = db.query(Lead).filter(
                Lead.workflow_id == wf_id,
                Lead.status.in_(["found", "drafted"])
            ).count()
            contactable_count = db.query(Lead).filter(
                Lead.workflow_id == wf_id,
                Lead.status.in_(["found", "drafted", "sent", "send_failed"]),
                Lead.email.isnot(None),
                Lead.email != "",
            ).count()
            total_search_count = db.query(Lead).filter(
                Lead.workflow_id == wf_id,
                Lead.status.in_(["found", "drafted", "sent", "send_failed", "needs_email"]),
            ).count()
        finally:
            db.close()

        if (
            pipeline_count < _int_env("SEARCH_PIPELINE_TARGET", 50, 1, 1000)
            and contactable_count < _int_env("SEARCH_WORKFLOW_EMAIL_TARGET", 200, 1, 100000)
            and total_search_count < _int_env("SEARCH_WORKFLOW_TOTAL_TARGET", 250, 1, 100000)
        ):
            cooldown_remaining = _workflow_search_cooldown_remaining(wf_id)
            if cooldown_remaining > 0:
                logger.info(f"Search cooldown active for workflow #{wf_id} '{wf_name}' ({cooldown_remaining}s remaining)")
                return
            _workflow_last_search_at[wf_id] = time.time()
            result = await asyncio.to_thread(search_and_extract_leads, wf_id)
            if isinstance(result, dict) and result.get("status") in ("ok", "busy", "no_domains", "all_duplicates", "rotated_keyword"):
                _error_counts[wf_id] = 0
            else:
                logger.warning(f"Workflow #{wf_id} search returned status={result.get('status') if isinstance(result, dict) else 'unknown'}, not resetting error count")

    except Exception as e:
        _error_counts[wf_id] = _error_counts.get(wf_id, 0) + 1
        logger.error(f"Workflow #{wf_id} error ({_error_counts[wf_id]}/{MAX_CONSECUTIVE_ERRORS}): {e}")

@db_retry()
def search_and_extract_leads(
    wf_id: int,
    batch_lead_limit: Optional[int] = None,
    max_domains: Optional[int] = None,
) -> Dict[str, Any]:
  lock = _get_workflow_search_lock(wf_id)
  if not lock.acquire(blocking=False):
      return {"workflow_id": wf_id, "status": "busy", "new_leads": 0}

  try:
      return _search_and_extract_leads(wf_id, batch_lead_limit=batch_lead_limit, max_domains=max_domains)
  finally:
      lock.release()


def _search_and_extract_leads(
    wf_id: int,
    batch_lead_limit: Optional[int] = None,
    max_domains: Optional[int] = None,
) -> Dict[str, Any]:
  import os
  batch_lead_limit = batch_lead_limit or _int_env("SEARCH_BATCH_LEAD_LIMIT", 25, 1, 200)
  max_domains = max_domains or _int_env("SEARCH_MAX_DOMAINS_PER_BATCH", 80, 10, 250)

  # Apollo takes priority if configured
  apollo_api_key = os.environ.get("APOLLO_API_KEY")
  if apollo_api_key:
      return _apollo_search_and_extract(wf_id, apollo_api_key, batch_lead_limit=batch_lead_limit)

  # LeadContact: run a targeted employee search in parallel with web search
  _lc_key = os.environ.get("LEADCONTACT_API_KEY", "").strip()
  if _lc_key:
      from services.leadcontact_client import LeadContactClient
      lc = LeadContactClient(_lc_key)
      if lc.get_credits() >= 50:
          try:
              lc_stats = _leadcontact_search_and_extract(wf_id, lc, batch_lead_limit=batch_lead_limit)
              if lc_stats.get("new_leads", 0) > 0:
                  return lc_stats
          except Exception as e:
              logger.warning(f"LeadContact search failed (will fall back to web search): {e}")
      else:
          logger.info(f"LeadContact credits too low ({lc.get_credits()}), skipping search stage")

  stats: Dict[str, Any] = {
      "workflow_id": wf_id,
      "status": "ok",
      "domains_found": 0,
      "domains_checked": 0,
      "domains_skipped": 0,
      "domains_with_no_snov_data": 0,
      "new_leads": 0,
      "company_only_leads": 0,
      "offset": 0,
  }
  try:
    db = SessionLocal()
    try:
        wf = db.query(Workflow).filter(Workflow.id == wf_id).first()
        if not wf:
            stats["status"] = "workflow_not_found"
            return stats
            
        snovio = get_snovio_client()
        
        # Build excluded domains set from client pool
        excluded_domains = set()
        pool_id = wf.client_pool_id
        if pool_id:
            from models import ClientPool
            pool = db.query(ClientPool).filter(ClientPool.id == pool_id).first()
            if pool and pool.excluded_domains:
                excluded_domains = {d.strip().lower() for d in pool.excluded_domains.split(",") if d.strip()}
        
        offset = getattr(wf, 'search_offset', 0)
        stats["offset"] = offset
        search_keywords = wf.search_keywords
        search_sources = getattr(wf, 'search_sources', None)
        target_region = getattr(wf, 'target_region', None)
        competitor_names = getattr(wf, 'competitor_names', None)
        trade_show_names = getattr(wf, 'trade_show_names', None)
        roles = [r.strip() for r in wf.target_positions.replace(";", ",").split(",") if r.strip()]
    finally:
        db.close()
    
    # Search phase - no DB needed
    domain_results = search_domain_results(
        search_keywords,
        offset=offset,
        max_domains=max_domains,
        search_sources=search_sources,
        target_region=target_region,
        competitor_names=competitor_names,
        trade_show_names=trade_show_names,
    )
    domains = [item["domain"] for item in domain_results]
    stats["domains_found"] = len(domains)
    
    if not domains:
        logger.warning(f"No domains found for keywords: {search_keywords} at offset {offset}. Search exhausted.")
        stats["status"] = "no_domains"
        _reset_search_offset(wf_id, "search exhausted")
        return stats
        
    logger.info(f"Search found {len(domains)} domains for '{search_keywords}' (offset: {offset})")
    
    new_leads_this_batch = 0
    new_domains_processed = 0
    reached_limit = False
    for domain_info in domain_results:
        domain = domain_info["domain"]
        source_channel = domain_info.get("source_channel") or "web"
        data_sources = domain_info.get("data_sources") or "web search, website"
        if not is_domain_quality_candidate(domain, search_keywords):
            logger.info(f"[QUALITY] Skipping low-quality/non-target domain before enrichment: {domain}")
            stats["domains_skipped"] += 1
            continue

        # Check domain blacklist
        if domain.lower() in excluded_domains:
            logger.info(f"[DEDUP] Skipping blacklisted domain: {domain}")
            stats["domains_skipped"] += 1
            continue
        
        # Quick DB check - open, check, close
        skip = False
        db = SessionLocal()
        try:
            processed = db.query(ProcessedDomain).filter(
                ProcessedDomain.workflow_id == wf_id,
                ProcessedDomain.domain == domain
            ).first()
            if processed:
                skip = True
                if _add_company_only_lead(db, wf_id, pool_id, domain, source_channel=source_channel, data_sources=data_sources):
                    stats["company_only_leads"] += 1
                    db.commit()
                    logger.info(f"Backfilled company lead from processed domain: {domain}")
        finally:
            db.close()
        
        if skip:
            logger.debug(f"Skipping already processed domain: {domain}")
            stats["domains_skipped"] += 1
            continue
            
        new_domains_processed += 1
        stats["domains_checked"] += 1

        if _bool_env("SNOVIO_SKIP_DOMAINS_WITHOUT_EMAILS", True):
            available_email_count = snovio.get_domain_emails_count(domain)
            if available_email_count == 0:
                logger.info(f"Snov.io has no emails for {domain}; skipping enrichment.")
                stats["domains_with_no_snov_data"] += 1
                db = SessionLocal()
                try:
                    db.add(ProcessedDomain(workflow_id=wf_id, domain=domain))
                    db.commit()
                finally:
                    db.close()
                continue
            
        # Snov.io calls - NO DB connection held open
        # We strictly enforce target roles. If no prospects match the desired roles, we skip the domain
        # rather than falling back to generic emails (which ruins deliverability).
        prospect_emails = []
        prospects = snovio.search_prospects_by_domain(domain, roles)
        
        if not prospects:
            logger.info(f"No prospects matching positions {roles} found for {domain}.")
        else:
            # Collect emails from prospects (still no DB connection)
            leads_per_domain = _int_env("SEARCH_LEADS_PER_DOMAIN", 5, 1, 25)
            max_prospect_attempts = _int_env("SEARCH_MAX_PROSPECT_ATTEMPTS_PER_DOMAIN", 15, 1, 100)
            attempts = 0
            for p in prospects:
                if len(prospect_emails) >= leads_per_domain:
                    logger.info(f"Reached limit of {leads_per_domain} leads for domain: {domain}")
                    break
                attempts += 1
                if attempts > max_prospect_attempts:
                    logger.info(f"Reached max prospect attempts ({max_prospect_attempts}) for domain: {domain}, skipping remaining prospects.")
                    break
                search_url = p.get("search_emails_start")
                email = snovio.get_prospect_email(search_url, domain=domain) if search_url else None
                if email and is_email_good_for_lead(email, domain):
                    prospect_emails.append((email, p))
                elif email:
                    logger.info(f"[QUALITY] Skipping low-quality or mismatched email {email} for {domain}")

        if not prospect_emails and _bool_env("SNOVIO_ALLOW_VERIFIED_DOMAIN_EMAIL_FALLBACK", True):
            for email in snovio.get_verified_domain_emails(domain):
                if is_email_good_for_lead(email, domain):
                    prospect_emails.append((
                        email,
                        {
                            "company_name": domain,
                            "position": "Company contact",
                        }
                    ))
            if prospect_emails:
                logger.info(f"Found {len(prospect_emails)} verified company email(s) for {domain}")
        
        # NOW open DB briefly to write results
        db = SessionLocal()
        try:
            # Record that we have processed this domain
            db.add(ProcessedDomain(workflow_id=wf_id, domain=domain))
            if not prospect_emails and _add_company_only_lead(db, wf_id, pool_id, domain, source_channel=source_channel, data_sources=data_sources):
                stats["company_only_leads"] += 1
                logger.info(f"Saved company lead without email: {domain}")
            
            for email, p in prospect_emails:
                # Client pool level deduplication
                if pool_id:
                    dup_email = db.query(Lead).filter(
                        Lead.client_pool_id == pool_id,
                        Lead.email == email
                    ).first()
                    if dup_email:
                        logger.info(f"[DEDUP] Skipping duplicate email in pool: {email}")
                        continue
                    
                    first_name = p.get('first_name', '')
                    last_name = p.get('last_name', '')
                    if first_name and last_name:
                        dup_person = db.query(Lead).filter(
                            Lead.client_pool_id == pool_id,
                            Lead.domain == domain,
                            Lead.first_name == first_name,
                            Lead.last_name == last_name
                        ).first()
                        if dup_person:
                            logger.info(f"[DEDUP] Skipping duplicate person in pool: {first_name} {last_name} @ {domain}")
                            continue
                else:
                    dup_email = db.query(Lead).filter(
                        Lead.workflow_id == wf_id,
                        Lead.email == email
                    ).first()
                    if dup_email:
                        logger.info(f"[DEDUP] Skipping duplicate email in workflow: {email}")
                        continue

                new_lead = Lead(
                    workflow_id=wf_id,
                    client_pool_id=pool_id,
                    domain=domain,
                    company_name=p.get('company_name', domain),
                    email=email,
                    first_name=p.get('first_name'),
                    last_name=p.get('last_name'),
                    job_title=p.get('position'),
                    linkedin_url=p.get('source_page'),
                    status="found",
                    source_channel=source_channel,
                    data_sources=f"{data_sources}, snovio",
                )
                # Auto-assign timezone from domain TLD
                try:
                    from services.timezone_resolver import guess_timezone_from_domain
                    tz = guess_timezone_from_domain(domain)
                    if tz:
                        new_lead.timezone = tz
                except Exception:
                    pass
                db.add(new_lead)
                new_leads_this_batch += 1
                stats["new_leads"] += 1
                logger.info(f"New lead: {email} @ {domain}")
            
            db.commit()
        finally:
            db.close()
            
        if new_leads_this_batch >= batch_lead_limit:
            logger.info(f"Reached batch limit of {batch_lead_limit} new leads, stopping search for now.")
            reached_limit = True
            break
                
    if not reached_limit:
        if stats["domains_found"] >= 5 and new_domains_processed == 0:
            logger.info(
                f"All {stats['domains_found']} domains at offset {offset} were already processed. "
                f"Advancing offset to skip exhausted range."
            )
            stats["next_offset"] = _advance_search_offset(wf_id, offset)
            stats["status"] = "all_duplicates"
        else:
            stats["next_offset"] = _advance_search_offset(wf_id, offset)

    return stats
                
  except Exception as e:
        logger.error(f"Search stage error: {e}")
        stats["status"] = "error"
        stats["error"] = str(e)
        return stats

# product-focus substring → LeadContact industry label. Extend/override at runtime
# with LEADCONTACT_INDUSTRY_MAP (a JSON object of substring -> industry label).
_DEFAULT_LC_INDUSTRY_MAP = {
    "padel": "Sporting Goods", "pickleball": "Sporting Goods", "tennis": "Sporting Goods",
    "sport": "Sporting Goods", "medical": "Medical Device", "pharma": "Pharmaceuticals",
    "software": "Computer Software", "saas": "Computer Software", "hardware": "Computer Hardware",
    "fintech": "Financial Services", "logistics": "Logistics & Supply Chain",
    "construction": "Construction", "education": "Education Management",
}


def _lc_industry_map() -> Dict[str, str]:
    import json
    mapping = dict(_DEFAULT_LC_INDUSTRY_MAP)
    raw = os.environ.get("LEADCONTACT_INDUSTRY_MAP", "").strip()
    if raw:
        try:
            mapping.update({str(k).lower(): str(v) for k, v in json.loads(raw).items()})
        except Exception as e:
            logger.warning(f"Invalid LEADCONTACT_INDUSTRY_MAP env (ignored): {e}")
    return mapping


# Generic commerce words that don't help LeadContact target an industry/role.
_LC_GENERIC_KEYWORDS = {
    "wholesale", "retail", "factory", "manufacturer", "manufacturers", "supplier",
    "suppliers", "sets", "set", "oem", "odm", "custom", "customized", "company",
    "companies", "products", "product", "b2b", "export", "exporter", "exporters",
    "import", "importer", "importers", "trade", "trading", "goods", "quality",
    "supply", "brand", "brands", "series", "collection", "style", "the", "and", "for",
}


def _distinctive_keyword(keywords: str, product_focus: str) -> str:
    """Pick a single distinctive word for LeadContact search.

    LeadContact matches single words far better than multi-word phrases
    (e.g. "bedding" returns results; "wholesale bedding sets" returns 0). Prefer
    the first non-generic word from the keywords, then the product focus.
    """
    for src in (keywords, product_focus):
        for w in re.split(r"[,\s]+", (src or "").lower()):
            w = w.strip()
            if len(w) > 2 and w not in _LC_GENERIC_KEYWORDS:
                return w
    return ""


def _build_leadcontact_query_plan(keywords, target_role, target_region, product_focus, company_size=None):
    """Ordered LeadContact search attempts, most specific first.

    The previous mapping over-constrained the query (a guessed industry label +
    a concatenated multi-keyword string) and returned 0 even though the DB had
    thousands of matches. This builds a relaxation ladder instead: try the
    specific query, and only if it returns nothing fall back to looser ones.
    A 0-result LeadContact search costs no points, so the ladder is cost-safe.
    """
    keywords = (keywords or "").strip()
    target_role = (target_role or "").strip()
    target_region = (target_region or "").strip()
    product_focus = (product_focus or "").strip()

    job_titles = [t.strip() for t in re.split(r"[,;]", target_role) if t.strip()]

    # Single distinctive keyword — LeadContact returns 0 for multi-word phrases.
    kw_tokens = [k.strip() for k in re.split(r"[,;]", keywords) if k.strip()]
    primary_kw = kw_tokens[0] if kw_tokens else ""
    single_kw = _distinctive_keyword(keywords, product_focus) or primary_kw
    # Alternate broad word from the product focus (e.g. "microfiber").
    focus_kw = ""
    if product_focus:
        focus_kw = product_focus.split(",")[0].strip().split(" ")[0].strip()

    haystack = (keywords + " " + product_focus).lower()
    industries = []
    for k, v in _lc_industry_map().items():
        if k in haystack and v not in industries:
            industries.append(v)

    # Discrete locations — LeadContact expects a list of places, not one
    # comma-joined blob string ("UK, Germany, France, ..." matches nothing).
    locations = [c.strip() for c in re.split(r"[,;]", target_region) if c.strip()] or None
    use_industry = _bool_env("LEADCONTACT_USE_INDUSTRY", True)
    size = company_size or None

    plan = []
    if industries and use_industry:
        # 1. Most specific: titles + industry + keyword.
        plan.append({"job_titles": job_titles or None, "locations": locations,
                     "industries": industries, "company_size": size, "keyword": single_kw or None})
        # 2. Keep INDUSTRY, drop titles + size. Titles over-constrain industry
        #    results to zero, while industry + keyword alone returns on-target
        #    companies (verified: Textiles + "bedding" → real textile firms).
        plan.append({"job_titles": None, "locations": locations,
                     "industries": industries, "company_size": None, "keyword": single_kw or None})
    # 3. Drop industry, keep titles + keyword.
    plan.append({"job_titles": job_titles or None, "locations": locations,
                 "industries": None, "company_size": size, "keyword": single_kw or None})
    # 4. Keyword + region only (broad — drop size so we still get results).
    plan.append({"job_titles": None, "locations": locations,
                 "industries": None, "company_size": None, "keyword": single_kw or None})
    # 5. Broad last resort: product-focus word (size dropped too).
    if focus_kw and focus_kw.lower() != (single_kw or "").lower():
        plan.append({"job_titles": None, "locations": locations,
                     "industries": None, "company_size": None, "keyword": focus_kw})

    deduped = []
    for p in plan:
        if p not in deduped:
            deduped.append(p)
    return deduped


_EMAIL_LOOKUP_STOPWORDS = {
    "the", "and", "for", "with", "company", "companies", "manager", "sales",
    "distributor", "distributors", "equipment", "supplier", "suppliers", "buyer",
    "owner", "director", "international", "group", "ltd", "inc", "co", "operator",
}


def _email_lookup_target_tokens(search_keywords: str, product_focus: str) -> List[str]:
    """Distinctive tokens (e.g. 'padel', 'club') used to judge lead relevance."""
    raw = f"{search_keywords or ''} {product_focus or ''}".lower()
    tokens = [t for t in re.split(r"[,;/&\s]+", raw) if len(t) >= 3 and t not in _EMAIL_LOOKUP_STOPWORDS]
    return list(dict.fromkeys(tokens))


# In-process daily budget for paid LeadContact lookups. Keyed by (date, wf_id, kind).
# Resets on restart — a spend ceiling/guardrail, not exact accounting.
_lc_daily_lookups: Dict[tuple, int] = {}


def _lc_budget_cap(kind: str) -> int:
    if kind == "phone":
        return _int_env("LEADCONTACT_MAX_PHONE_LOOKUPS_PER_DAY", 50, 0, 1000000)
    return _int_env("LEADCONTACT_MAX_EMAIL_LOOKUPS_PER_DAY", 300, 0, 1000000)


def _lc_budget_check(wf_id: int, kind: str) -> bool:
    """True if under today's daily budget for paid lookups (no increment). 0 = unlimited.

    kind = 'email' (10 credits/hit) or 'phone' (30 credits/hit).
    """
    cap = _lc_budget_cap(kind)
    if cap <= 0:
        return True
    day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    return _lc_daily_lookups.get((day, wf_id, kind), 0) < cap


def _lc_budget_record(wf_id: int, kind: str) -> None:
    """Count one actually-charged lookup (call only on a successful/billed hit)."""
    day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    key = (day, wf_id, kind)
    _lc_daily_lookups[key] = _lc_daily_lookups.get(key, 0) + 1


def _find_existing_email(linkedin_url: Optional[str], exclude_lead_id: Optional[int] = None) -> Optional[str]:
    """Reuse an email already obtained for the same person (same LinkedIn URL) so we
    don't pay LeadContact again for a contact we've already enriched."""
    if not linkedin_url:
        return None
    db = SessionLocal()
    try:
        q = db.query(Lead).filter(
            Lead.linkedin_url == linkedin_url,
            Lead.email.isnot(None), Lead.email != "",
        )
        if exclude_lead_id:
            q = q.filter(Lead.id != exclude_lead_id)
        other = q.first()
        return other.email if other else None
    except Exception:
        return None
    finally:
        db.close()


def _prescore_lead(lead_id: int, wf_id: int) -> Optional[int]:
    """Cheap (rule-based, no LLM) fit score 0-100 for a lead, used to gate paid
    lookups. Returns None if it can't score (then callers should not block)."""
    db = SessionLocal()
    try:
        lead = db.query(Lead).filter(Lead.id == lead_id).first()
        wf = db.query(Workflow).filter(Workflow.id == wf_id).first()
        if not lead or not wf:
            return None
        persona = None
        if wf.persona_id:
            from models import CustomerPersona
            persona = db.query(CustomerPersona).filter(CustomerPersona.id == wf.persona_id).first()
        score = apply_lead_score(db, lead, workflow=wf, persona=persona)
        db.commit()
        return int(score.score) if score and score.score is not None else None
    except Exception as e:
        logger.warning(f"Prescore failed for lead {lead_id}: {e}")
        return None
    finally:
        db.close()


def _lead_worth_email_lookup(company_name: Optional[str], job_title: Optional[str], target_tokens: List[str]) -> bool:
    """Whether to spend a paid email lookup on this lead.

    Errs toward NOT spending only when there is a clear off-target signal: a real
    company name that matches none of the target tokens. Leads with no company
    info are allowed (we can't judge), so we never silently drop ambiguous ones —
    a skipped lead is simply kept as needs_email rather than enriched.
    """
    if not target_tokens:
        return True
    hay = f"{company_name or ''} {job_title or ''}".lower().strip()
    if not hay:
        return True
    return any(tok in hay for tok in target_tokens)


def _leadcontact_search_and_extract(wf_id: int, lc, batch_lead_limit: Optional[int] = None) -> Dict[str, Any]:
    """Search for targeted professionals via LeadContact advanced employee query."""
    from services.leadcontact_client import LeadContactClient

    batch_lead_limit = batch_lead_limit or _int_env("SEARCH_BATCH_LEAD_LIMIT", 25, 1, 200)
    max_lc_leads = _int_env("LEADCONTACT_MAX_LEADS_PER_SEARCH", 10, 2, 50)
    stats: Dict[str, Any] = {
        "workflow_id": wf_id,
        "status": "ok",
        "source": "leadcontact",
        "new_leads": 0,
    }

    # Skip (and stop spending) while this workflow is backed off from a prior
    # search that returned only duplicates / nothing.
    backoff_until = _leadcontact_backoff_until.get(wf_id, 0.0)
    if time.time() < backoff_until:
        remaining = int(backoff_until - time.time())
        logger.info(f"[LeadContact] workflow #{wf_id} backed off ({remaining}s left); skipping search")
        stats["status"] = "backoff"
        return stats

    try:
        db = SessionLocal()
        try:
            wf = db.query(Workflow).filter(Workflow.id == wf_id).first()
            if not wf:
                return {"status": "error", "error": "workflow_not_found", "source": "leadcontact"}
            pool_id = wf.client_pool_id  # so new leads land in the workflow's client pool
            keywords = wf.search_keywords or ""
            target_role = wf.target_positions or ""
            target_region = wf.target_region or ""
            product_focus = wf.product_focus or ""
            # Company-size filter comes from the workflow's persona (if any).
            company_size = []
            if wf.persona_id:
                from models import CustomerPersona
                persona = db.query(CustomerPersona).filter(CustomerPersona.id == wf.persona_id).first()
                if persona and getattr(persona, "company_size", None):
                    company_size = [s.strip() for s in re.split(r"[,;]", persona.company_size) if s.strip()]
        finally:
            db.close()

        # Relaxation ladder: try specific query first, loosen only if it returns 0.
        # Cursor pagination: when we already have a nextPageToken for a query, fetch
        # the NEXT page of that same query instead of re-paying for page 1.
        plan = _build_leadcontact_query_plan(keywords, target_role, target_region, product_focus, company_size or None)
        cursor = _leadcontact_cursor.get(wf_id)
        per_page = min(max_lc_leads, batch_lead_limit)
        employees = []
        chosen = None
        next_token = ""
        for attempt in plan:
            token = cursor.get("token") if (cursor and cursor.get("query") == attempt) else None
            logger.info(f"[LeadContact] Searching — {attempt}{' (next page)' if token else ''}")
            result = lc.search_employees(
                job_titles=attempt["job_titles"],
                locations=attempt["locations"],
                industries=attempt["industries"],
                company_size=attempt.get("company_size"),
                keyword=attempt["keyword"],
                current_titles_only=True,
                per_page=per_page,
                next_page_token=token,
            )
            emps = result.get("employees", []) if isinstance(result, dict) else []
            if emps:
                employees = emps
                chosen = attempt
                next_token = (result.get("nextPageToken") or "") if isinstance(result, dict) else ""
                break
            if isinstance(result, dict) and result.get("error"):
                logger.warning(f"[LeadContact] search error, stopping ladder: {result.get('error')}")
                break

        if not employees:
            logger.info(f"[LeadContact] No employees found after {len(plan)} query tiers")
            _leadcontact_cursor.pop(wf_id, None)
            _leadcontact_backoff_until[wf_id] = time.time() + _int_env(
                "LEADCONTACT_EMPTY_BACKOFF_SECONDS", 86400, 300, 604800
            )
            return {"status": "ok", "source": "leadcontact", "new_leads": 0}
        logger.info(f"[LeadContact] {len(employees)} employees via tier: {chosen}")

        new_leads = 0
        for emp in employees:
            emp_email = emp.get("email") or ""
            emp_name = (emp.get("fullName") or "").strip()
            emp_title = emp.get("title") or ""
            emp_company = emp.get("companyName") or ""
            linkedin_url = emp.get("linkedinUrl", "")

            if not emp_email and not linkedin_url:
                continue

            domain = ""
            if "@" in emp_email:
                domain = emp_email.split("@")[-1]

            # Deduplicate by domain + company or LinkedIn URL
            db_check = SessionLocal()
            try:
                from sqlalchemy import or_
                conds = []
                if linkedin_url:
                    conds.append(Lead.linkedin_url == linkedin_url)
                if emp_email:
                    conds.append(Lead.email == emp_email)
                
                existing = None
                if conds:
                    existing = db_check.query(Lead).filter(
                        Lead.workflow_id == wf_id,
                        or_(*conds)
                    ).first()
                if existing:
                    continue
            finally:
                db_check.close()

            # Split name
            name_parts = emp_name.split(" ", 1)
            first = name_parts[0] if name_parts else ""
            last = name_parts[1] if len(name_parts) > 1 else ""

            db_insert = SessionLocal()
            try:
                lead = Lead(
                    workflow_id=wf_id,
                    client_pool_id=pool_id,
                    email=emp_email if emp_email else None,
                    first_name=first,
                    last_name=last,
                    company_name=emp_company or domain,
                    domain=domain,
                    job_title=emp_title,
                    linkedin_url=linkedin_url,
                    source_channel="leadcontact",
                    data_sources="leadcontact",
                    email_validation_status="valid" if emp_email else None,
                    email_verified=bool(emp_email),
                    status="found",
                )
                db_insert.add(lead)
                db_insert.commit()
                new_leads += 1
                logger.info(f"[LeadContact] New lead: {emp_email or linkedin_url} @ {emp_company}")
            finally:
                db_insert.close()

        stats["new_leads"] = new_leads
        stats["next_page"] = bool(next_token)
        logger.info(f"[LeadContact] Added {new_leads} leads from LeadContact search")

        # Advance the pagination cursor so the next run fetches the next page.
        if chosen is not None and next_token:
            _leadcontact_cursor[wf_id] = {"query": chosen, "token": next_token}
        else:
            # No more pages for this query — clear cursor so a future run can restart.
            _leadcontact_cursor.pop(wf_id, None)

        if new_leads == 0:
            # This page yielded only duplicates (e.g. cursor exhausted and we
            # restarted at page 1). Back off so we stop re-paying for the same data.
            cooldown = _int_env("LEADCONTACT_DEDUP_BACKOFF_SECONDS", 21600, 300, 604800)
            _leadcontact_backoff_until[wf_id] = time.time() + cooldown
            logger.warning(
                f"[LeadContact] workflow #{wf_id}: search returned {len(employees)} contacts "
                f"but 0 new leads (all duplicates); backing off {cooldown}s to avoid wasted credits"
            )
    except Exception as e:
        logger.error(f"[LeadContact] Search stage error: {e}")
        stats["status"] = "error"
        stats["error"] = str(e)

    return stats


def _apollo_search_and_extract(wf_id: int, api_key: str, batch_lead_limit: Optional[int] = None) -> Dict[str, Any]:
    batch_lead_limit = batch_lead_limit or _int_env("SEARCH_BATCH_LEAD_LIMIT", 25, 1, 200)
    per_page = _int_env("APOLLO_SEARCH_PER_PAGE", 25, 10, 100)
    stats: Dict[str, Any] = {
        "workflow_id": wf_id,
        "status": "ok",
        "people_found": 0,
        "new_leads": 0,
        "offset": 0,
    }
    try:
        from services.apollo_client import ApolloClient
        apollo = ApolloClient(api_key)
        
        db = SessionLocal()
        try:
            wf = db.query(Workflow).filter(Workflow.id == wf_id).first()
            if not wf:
                stats["status"] = "workflow_not_found"
                return stats
            pool_id = wf.client_pool_id
            offset = getattr(wf, 'search_offset', 0)
            stats["offset"] = offset
            search_keywords = wf.search_keywords
            roles = [r.strip() for r in wf.target_positions.replace(";", ",").split(",") if r.strip()]
            
            excluded_domains = set()
            if pool_id:
                from models import ClientPool
                pool = db.query(ClientPool).filter(ClientPool.id == pool_id).first()
                if pool and pool.excluded_domains:
                    excluded_domains = {d.strip().lower() for d in pool.excluded_domains.split(",") if d.strip()}
        finally:
            db.close()
            
        page = (offset // per_page) + 1 if offset >= 0 else 1
        people = apollo.search_people(search_keywords, roles, page=page, per_page=per_page)
        stats["people_found"] = len(people)
        
        if not people:
            logger.warning(f"Apollo found no people for keywords: {search_keywords} at page {page}. Search exhausted.")
            stats["status"] = "no_people"
            _reset_search_offset(wf_id, "Apollo search exhausted")
            return stats
            
        new_leads_this_batch = 0
        reached_limit = False
        
        for p in people:
            person_id = p.get("id")
            first_name = p.get("first_name", "")
            last_name = p.get("last_name", "")
            title = p.get("title", "")
            
            org = p.get("organization", {})
            company_name = org.get("name", "")
            domain = org.get("primary_domain", "")
            if not domain:
                domain = org.get("website_url", "")
                if domain:
                    domain = domain.replace("https://", "").replace("http://", "").replace("www.", "").split("/")[0]
            if not domain:
                domain = company_name.lower().replace(" ", "") + ".com"
                
            if domain.lower() in excluded_domains:
                continue
                
            # Enrich to get verified email
            enriched = apollo.enrich_person(person_id)
            if not enriched:
                continue
                
            email = enriched.get("email")
            email_status = enriched.get("email_status")
            
            if not email or email_status != "verified":
                logger.info(f"Skipping Apollo person {person_id}: email_status is '{email_status}'")
                continue
                
            first_name = enriched.get("first_name", first_name)
            last_name = enriched.get("last_name", last_name)
            linkedin_url = enriched.get("linkedin_url", "")
            
            db = SessionLocal()
            try:
                if pool_id:
                    dup_email = db.query(Lead).filter(Lead.client_pool_id == pool_id, Lead.email == email).first()
                    if dup_email:
                        continue
                        
                db.add(ProcessedDomain(workflow_id=wf_id, domain=domain))
                
                new_lead = Lead(
                    workflow_id=wf_id,
                    client_pool_id=pool_id,
                    domain=domain,
                    company_name=company_name,
                    email=email,
                    first_name=first_name,
                    last_name=last_name,
                    job_title=title,
                    linkedin_url=linkedin_url,
                    status="found",
                    source_channel="apollo",
                    data_sources="apollo, website",
                )
                db.add(new_lead)
                db.commit()
                new_leads_this_batch += 1
                stats["new_leads"] += 1
                logger.info(f"New lead from Apollo: {email} @ {domain}")
            finally:
                db.close()
                
            if new_leads_this_batch >= batch_lead_limit:
                reached_limit = True
                break
                
        if not reached_limit:
            stats["next_offset"] = _advance_search_offset(wf_id, offset, step=per_page)
        return stats
    except Exception as e:
        logger.error(f"Apollo search stage error: {e}")
        stats["status"] = "error"
        stats["error"] = str(e)
        return stats

def _has_recent_outbound(lead_id: int, db, window_seconds: int) -> bool:
    """True if an outbound email to this lead was logged within the window.

    Send idempotency guard: the workers hold only in-process locks, so the same
    lead can be picked up twice — by overlapping ticks, a crash-and-retry after the
    send succeeded but the status write failed, or a second API instance. Because a
    successful send always writes an EmailLog first, a short look-back here stops a
    duplicate cold email from going out. The window is far shorter than any
    legitimate follow-up interval (which is >= 24h), so real follow-ups are unaffected.
    """
    if window_seconds <= 0:
        return False
    cutoff = datetime.now(timezone.utc) - timedelta(seconds=window_seconds)
    return db.query(EmailLog.id).filter(
        EmailLog.lead_id == lead_id,
        EmailLog.direction == "outbound",
        EmailLog.sent_at >= cutoff,
    ).first() is not None


async def send_lead_email(lead: Lead, wf: Workflow, db, *, charge_credits: bool = True, raise_on_credit_error: bool = False):
    credit_charged = False
    sent_successfully = False
    try:
        bounce_pause_reason = _workflow_bounce_pause_reason(wf.id, db)
        if bounce_pause_reason:
            logger.warning(f"Email sending paused for workflow '{wf.name}': {bounce_pause_reason}")
            return

        preflight_reason = validate_lead_before_send(lead, db)
        if preflight_reason:
            _mark_lead_suppressed(lead, preflight_reason, db)
            logger.warning(f"Suppressed email to {lead.email or lead.domain}: {preflight_reason}")
            return

        temporary_reason = temporary_send_block_reason(lead, db)
        if temporary_reason:
            logger.info(f"Skipping {lead.email}: {temporary_reason}")
            return

        # Idempotency guard against duplicate sends (overlapping ticks / retries /
        # multiple instances). Keep the window well under the follow-up interval.
        dedupe_window = _int_env("EMAIL_SEND_DEDUPE_WINDOW_SECONDS", 600, 0, 86400)
        if _has_recent_outbound(lead.id, db, dedupe_window):
            logger.warning(
                f"Skipping duplicate send to {lead.email}: an outbound email to lead "
                f"#{lead.id} was already logged within {dedupe_window}s"
            )
            return

        per_account_daily_cap = _int_env("EMAIL_MAX_DAILY_PER_ACCOUNT", 15, 1, 500)
        sender_selection = select_sender_account(
            db,
            wf,
            per_account_daily_cap=per_account_daily_cap,
        )
        for email, sent_count in sender_selection.capped_accounts:
            logger.info(f"Daily cap reached for {email}: {sent_count}/{sender_selection.daily_cap}, trying next account")
        account_to_use = sender_selection.account
        if not account_to_use:
            logger.info(f"All active sender email accounts for workflow '{wf.name}' have reached their daily caps.")
            return
                    
        sender_name = account_to_use.display_name or "there"
        prepared_email = prepare_email_content(
            lead.ai_draft,
            company_name=lead.company_name,
            first_name=lead.first_name,
            sender_name=sender_name,
        )
        subject = prepared_email.subject
        body = prepared_email.body

        # Wrap in branded HTML template
        custom_sig = wf.email_signature if hasattr(wf, 'email_signature') and wf.email_signature else None
        unsubscribe_url = _unsubscribe_url_for_lead(lead)
        body_html = build_email_html(body, account_to_use.display_name or account_to_use.email, custom_sig, unsubscribe_url)

        in_reply_to = None
        if lead.followup_count > 0:
            last_outbound = db.query(EmailLog).filter(
                EmailLog.lead_id == lead.id,
                EmailLog.direction == "outbound"
            ).order_by(EmailLog.sent_at.desc()).first()
            if last_outbound:
                in_reply_to = last_outbound.message_id

        if charge_credits:
            consume_credits(
                db,
                wf.user_id,
                "email_send",
                description=f"Outbound email send to {lead.email}",
                reference_type="lead",
                reference_id=lead.id,
                metadata={"workflow_id": wf.id, "workflow_name": wf.name},
            )
            credit_charged = True

        res = await asyncio.to_thread(
            send_email,
            smtp_host=account_to_use.smtp_host,
            smtp_port=account_to_use.smtp_port,
            smtp_user=account_to_use.smtp_user,
            smtp_pass=decrypt_smtp_pass(account_to_use.smtp_pass),
            use_ssl=account_to_use.use_ssl,
            use_tls=account_to_use.use_tls,
            from_email=account_to_use.email,
            to_email=lead.email,
            subject=subject,
            body_html=body_html,
            body_text=body,
            sender_name=account_to_use.display_name or account_to_use.email.split('@')[0],
            reply_to=account_to_use.email,
            in_reply_to=in_reply_to,
            references=in_reply_to,
            list_unsubscribe_url=unsubscribe_url
        )
        
        if res.get("success"):
            sent_successfully = True
            record_send_success(
                db,
                lead,
                from_email=account_to_use.email,
                subject=subject,
                body=body,
                message_id=res.get("message_id")
            )
            logger.info(f"✉ Sent to {lead.email} via {account_to_use.email}")
        else:
            if credit_charged:
                refund_credits(
                    db,
                    wf.user_id,
                    "email_send",
                    description=f"Refund failed outbound email send to {lead.email}",
                    reference_type="lead",
                    reference_id=lead.id,
                )
                credit_charged = False
            failure_update = record_send_failure(db, lead)
            if failure_update.permanently_failed:
                logger.error(f"✘ Permanently failed to send to {lead.email} after {failure_update.fail_count} attempts: {res.get('message')}")
                try:
                    from services.notifications import notify
                    notify(
                        db,
                        wf.user_id,
                        "send_failed",
                        f"Email send failed: {lead.company_name or lead.email}",
                        body=f"Could not deliver to {lead.email} after {failure_update.fail_count} attempts. {res.get('message') or ''}".strip(),
                        link="/dashboard/review",
                        reference_type="lead",
                        reference_id=lead.id,
                    )
                except Exception:
                    pass
            else:
                logger.warning(f"Send failed to {lead.email} (attempt {failure_update.fail_count}/3): {res.get('message')}")
    except InsufficientCreditsError as e:
        logger.warning(
            f"Insufficient credits for workflow '{wf.name}' user #{wf.user_id}: "
            f"required={e.required}, balance={e.balance}"
        )
        try:
            lead.reply_snippet = f"Insufficient credits for outbound send: required {e.required}, balance {e.balance}"
            db.commit()
        except Exception:
            db.rollback()
        if raise_on_credit_error:
            raise
    except Exception as e:
        if credit_charged and not sent_successfully:
            try:
                refund_credits(
                    db,
                    wf.user_id,
                    "email_send",
                    description=f"Refund failed outbound email send to {lead.email}",
                    reference_type="lead",
                    reference_id=lead.id,
                )
            except Exception as refund_err:
                logger.error(f"Credit refund failed for lead {lead.id}: {refund_err}")
        logger.error(f"Send stage error for {lead.email}: {e}")

async def _send_linkedin_invites(wf_id: int, wf_name: str, linkedin_template: str, daily_limit: int, ai_prompt: str):
    """Stage 2.5: Auto-send LinkedIn connection invitations for leads that have linkedin_url."""
    global _linkedin_cooldown_until
    from models import ChannelAccount, MessageLog, LeadBrief
    from services.unipile_client import UnipileClient
    from services.ai_writer import generate_linkedin_invite

    if time.time() < _linkedin_cooldown_until:
        return

    cooldown = _int_env("LINKEDIN_PROVIDER_LIMIT_COOLDOWN_SECONDS", 3600, 300, 86400)
    db = SessionLocal()
    try:
        recent_provider_limit = db.query(MessageLog).filter(
            MessageLog.channel == "linkedin",
            MessageLog.status == "provider_limited",
            MessageLog.sent_at >= datetime.now(timezone.utc) - timedelta(seconds=cooldown),
        ).first()
        if recent_provider_limit:
            return
    finally:
        db.close()

    # 1. Find a connected LinkedIn account owned by the workflow owner
    db = SessionLocal()
    try:
        workflow = db.query(Workflow).filter(Workflow.id == wf_id).first()
        if not workflow:
            return
        linkedin_account = db.query(ChannelAccount).filter(
            ChannelAccount.account_type == "LINKEDIN",
            ChannelAccount.status == "OK",
            ChannelAccount.user_id == workflow.user_id,
        ).first()
        if not linkedin_account:
            logger.warning(f"LinkedIn is enabled for workflow '{wf_name}', but no connected LinkedIn account is available")
            return
        account_id = linkedin_account.unipile_account_id
        wf_user_id = workflow.user_id
    finally:
        db.close()

    # 2. Check daily limit
    db = SessionLocal()
    try:
        today = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
        sent_today = db.query(MessageLog).join(Lead).filter(
            Lead.workflow_id == wf_id,
            MessageLog.channel == "linkedin",
            MessageLog.direction == "outbound",
            MessageLog.status == "sent",
            MessageLog.sent_at >= today
        ).count()
        if sent_today >= daily_limit:
            logger.info(f"LinkedIn daily limit reached for workflow '{wf_name}' ({sent_today}/{daily_limit})")
            return
    finally:
        db.close()

    # 3. Find leads that have been emailed but not yet LinkedIn-invited
    db = SessionLocal()
    try:
        candidates = db.query(Lead).filter(
            Lead.workflow_id == wf_id,
            Lead.status.in_(["sent", "drafted"]),
            Lead.linkedin_url.isnot(None),
            Lead.linkedin_url != "",
            Lead.linkedin_sent == False,
            Lead.linkedin_status.in_(["unconnected", None])
        ).limit(3).all()
        lead_data = [(l.id, l.linkedin_url, l.first_name, l.last_name, l.company_name, l.job_title, l.domain, l.email) for l in candidates]
    finally:
        db.close()

    if not lead_data:
        return

    client = UnipileClient()

    for lead_id, linkedin_url, first_name, last_name, company_name, job_title, domain, email in lead_data:
        credit_charged = False
        try:
            db = SessionLocal()
            try:
                reason = suppression_reason(db, email=email, domain=domain, user_id=wf_user_id)
                if reason:
                    lead_obj = db.query(Lead).filter(Lead.id == lead_id).first()
                    if lead_obj:
                        lead_obj.linkedin_status = "suppressed"
                        lead_obj.reply_snippet = f"Suppressed before LinkedIn send: {reason}"
                        db.commit()
                    logger.info(f"Suppressed LinkedIn invite for lead {lead_id}: {reason}")
                    continue
            finally:
                db.close()

            # Get brief for personalization
            brief_summary = "No detailed brief available."
            db = SessionLocal()
            try:
                brief = db.query(LeadBrief).filter(LeadBrief.lead_id == lead_id).first()
                if brief:
                    brief_summary = f"{brief.company_overview or ''} {brief.pain_points or ''}"
            finally:
                db.close()

            # Generate personalized invite
            invite_msg = await asyncio.to_thread(
                generate_linkedin_invite,
                first_name=first_name or "",
                company_name=company_name or domain,
                job_title=job_title or "",
                brief_summary=brief_summary,
                template=linkedin_template or ai_prompt
            )

            if not invite_msg:
                invite_msg = (
                    f"Hi {first_name or 'there'}, I'd like to connect and explore "
                    f"potential collaboration with {company_name or domain}."
                )
                invite_msg = invite_msg[:180]
                logger.warning(f"AI failed to generate LinkedIn invite for lead {lead_id}; using fallback invite")

            # Resolve provider_id from LinkedIn URL
            provider_id = await client.get_linkedin_provider_id(account_id, linkedin_url)
            if not provider_id:
                logger.warning(f"Could not resolve LinkedIn provider_id for: {linkedin_url}")
                db = SessionLocal()
                try:
                    lead_obj = db.query(Lead).filter(Lead.id == lead_id).first()
                    if lead_obj:
                        lead_obj.linkedin_status = "invalid_profile"
                        db.commit()
                finally:
                    db.close()
                continue

            # Reserve credits before the provider call; refund below when provider does not send.
            db = SessionLocal()
            try:
                consume_credits(
                    db,
                    wf_user_id,
                    "linkedin_invite",
                    description=f"Auto LinkedIn invite for lead #{lead_id}",
                    reference_type="lead",
                    reference_id=lead_id,
                    metadata={"workflow_id": wf_id, "workflow_name": wf_name},
                )
                credit_charged = True
            except InsufficientCreditsError as credit_err:
                logger.warning(
                    f"LinkedIn invite skipped for workflow '{wf_name}': "
                    f"insufficient credits (required={credit_err.required}, balance={credit_err.balance})"
                )
                break
            finally:
                db.close()

            # Send invitation
            success = await client.send_linkedin_invitation(account_id, provider_id, invite_msg)
            provider_limited = (
                client.last_error_status == 422
                and (
                    "cannot_resend_yet" in client.last_error_body
                    or "temporary provider limit" in client.last_error_body.lower()
                )
            )

            if provider_limited:
                if credit_charged:
                    db = SessionLocal()
                    try:
                        refund_credits(
                            db,
                            wf_user_id,
                            "linkedin_invite",
                            description=f"Refund provider-limited LinkedIn invite for lead #{lead_id}",
                            reference_type="lead",
                            reference_id=lead_id,
                        )
                        credit_charged = False
                    finally:
                        db.close()
                _linkedin_cooldown_until = time.time() + cooldown
                db = SessionLocal()
                try:
                    # Mark the lead so it won't be retried after cooldown expires
                    lead_obj = db.query(Lead).filter(Lead.id == lead_id).first()
                    if lead_obj:
                        lead_obj.linkedin_status = "provider_limited"
                    db.add(MessageLog(
                        lead_id=lead_id,
                        channel="linkedin",
                        direction="outbound",
                        content=invite_msg,
                        status="provider_limited",
                    ))
                    db.commit()
                finally:
                    db.close()
                logger.warning(
                    f"LinkedIn provider limit reached for workflow '{wf_name}'. "
                    f"Lead {lead_id} marked provider_limited. "
                    f"Pausing LinkedIn invites for {cooldown} seconds."
                )
                break

            # Record in DB
            db = SessionLocal()
            try:
                lead_obj = db.query(Lead).filter(Lead.id == lead_id).first()
                if lead_obj:
                    if success:
                        lead_obj.linkedin_sent = True
                        lead_obj.linkedin_status = "requested"
                    else:
                        lead_obj.linkedin_sent = False
                        lead_obj.linkedin_status = "failed"

                msg_log = MessageLog(
                    lead_id=lead_id,
                    channel="linkedin",
                    direction="outbound",
                    content=invite_msg,
                    status="sent" if success else "failed"
                )
                db.add(msg_log)
                db.commit()

                if success:
                    logger.info(f"💼 LinkedIn invite sent to {first_name} {last_name} ({linkedin_url}) [workflow: {wf_name}]")
                else:
                    if credit_charged:
                        refund_credits(
                            db,
                            wf_user_id,
                            "linkedin_invite",
                            description=f"Refund failed LinkedIn invite for lead #{lead_id}",
                            reference_type="lead",
                            reference_id=lead_id,
                        )
                        credit_charged = False
                    logger.warning(f"💼 LinkedIn invite FAILED for {linkedin_url} [workflow: {wf_name}]")
            finally:
                db.close()

            # Rate limit: wait between sends
            await asyncio.sleep(random.randint(30, 90))

        except Exception as e:
            if credit_charged:
                db = SessionLocal()
                try:
                    refund_credits(
                        db,
                        wf_user_id,
                        "linkedin_invite",
                        description=f"Refund LinkedIn invite error for lead #{lead_id}",
                        reference_type="lead",
                        reference_id=lead_id,
                    )
                finally:
                    db.close()
            logger.error(f"LinkedIn send error for lead {lead_id}: {e}")


async def _send_whatsapp_messages(wf_id: int, wf_name: str, whatsapp_template: str, ai_prompt: str):
    """Stage 2.6: Auto-send WhatsApp messages for leads that have whatsapp_number."""
    from models import ChannelAccount, MessageLog, LeadBrief
    from services.unipile_client import UnipileClient
    from services.ai_writer import generate_whatsapp_message

    # 1. Find a connected WhatsApp account owned by the workflow owner
    db = SessionLocal()
    try:
        workflow = db.query(Workflow).filter(Workflow.id == wf_id).first()
        if not workflow:
            return
        wa_account = db.query(ChannelAccount).filter(
            ChannelAccount.account_type == "WHATSAPP",
            ChannelAccount.status == "OK",
            ChannelAccount.user_id == workflow.user_id,
        ).first()
        if not wa_account:
            return
        account_id = wa_account.unipile_account_id
        wf_user_id = workflow.user_id
    finally:
        db.close()

    # 2. Find leads with WhatsApp numbers not yet messaged
    db = SessionLocal()
    try:
        candidates = db.query(Lead).filter(
            Lead.workflow_id == wf_id,
            Lead.status.in_(["sent", "drafted"]),
            Lead.whatsapp_number.isnot(None),
            Lead.whatsapp_number != "",
            Lead.whatsapp_sent == False
        ).limit(3).all()
        lead_data = [(l.id, l.whatsapp_number, l.first_name, l.company_name, l.domain, l.email) for l in candidates]
    finally:
        db.close()

    if not lead_data:
        return

    client = UnipileClient()

    for lead_id, phone, first_name, company_name, domain, email in lead_data:
        credit_charged = False
        try:
            db = SessionLocal()
            try:
                reason = suppression_reason(db, email=email, domain=domain, user_id=wf_user_id)
                if reason:
                    lead_obj = db.query(Lead).filter(Lead.id == lead_id).first()
                    if lead_obj:
                        lead_obj.reply_snippet = f"Suppressed before WhatsApp send: {reason}"
                        db.commit()
                    logger.info(f"Suppressed WhatsApp message for lead {lead_id}: {reason}")
                    continue
            finally:
                db.close()

            # Get brief
            brief_summary = "No detailed brief available."
            db = SessionLocal()
            try:
                brief = db.query(LeadBrief).filter(LeadBrief.lead_id == lead_id).first()
                if brief:
                    brief_summary = f"{brief.company_overview or ''}"
            finally:
                db.close()

            # Generate message
            wa_msg = await asyncio.to_thread(
                generate_whatsapp_message,
                first_name=first_name or "",
                company_name=company_name or domain,
                brief_summary=brief_summary,
                template=whatsapp_template or ai_prompt
            )

            if not wa_msg:
                logger.warning(f"AI failed to generate WhatsApp message for lead {lead_id}")
                continue

            # Reserve credits before the provider call; refund below when provider does not send.
            db = SessionLocal()
            try:
                consume_credits(
                    db,
                    wf_user_id,
                    "whatsapp_message",
                    description=f"Auto WhatsApp message for lead #{lead_id}",
                    reference_type="lead",
                    reference_id=lead_id,
                    metadata={"workflow_id": wf_id, "workflow_name": wf_name},
                )
                credit_charged = True
            except InsufficientCreditsError as credit_err:
                logger.warning(
                    f"WhatsApp message skipped for workflow '{wf_name}': "
                    f"insufficient credits (required={credit_err.required}, balance={credit_err.balance})"
                )
                break
            finally:
                db.close()

            # Send
            success = await client.send_whatsapp_message(account_id, phone, wa_msg)

            # Record
            db = SessionLocal()
            try:
                lead_obj = db.query(Lead).filter(Lead.id == lead_id).first()
                if lead_obj:
                    lead_obj.whatsapp_sent = True

                msg_log = MessageLog(
                    lead_id=lead_id,
                    channel="whatsapp",
                    direction="outbound",
                    content=wa_msg,
                    status="sent" if success else "failed"
                )
                db.add(msg_log)
                db.commit()

                if success:
                    logger.info(f"💬 WhatsApp sent to {first_name} ({phone}) [workflow: {wf_name}]")
                else:
                    if credit_charged:
                        refund_credits(
                            db,
                            wf_user_id,
                            "whatsapp_message",
                            description=f"Refund failed WhatsApp message for lead #{lead_id}",
                            reference_type="lead",
                            reference_id=lead_id,
                        )
                        credit_charged = False
                    logger.warning(f"💬 WhatsApp FAILED for {phone} [workflow: {wf_name}]")
            finally:
                db.close()

            await asyncio.sleep(random.randint(20, 60))

        except Exception as e:
            if credit_charged:
                db = SessionLocal()
                try:
                    refund_credits(
                        db,
                        wf_user_id,
                        "whatsapp_message",
                        description=f"Refund WhatsApp error for lead #{lead_id}",
                        reference_type="lead",
                        reference_id=lead_id,
                    )
                finally:
                    db.close()
            logger.error(f"WhatsApp send error for lead {lead_id}: {e}")
