import asyncio
import time
import random
import logging
import threading
import re
import socket
from datetime import datetime, timezone, timedelta
from typing import Optional, Dict, Any
from database import SessionLocal, db_retry, db_retry_async
from models import Workflow, Lead, EmailLog, WorkflowEmail, ProcessedDomain
from services.search_engine import is_domain_quality_candidate, search_domain_results
from services.snovio_client import SnovioClient
from services.ai_writer import generate_email
from services.research_agent import build_and_save_lead_brief
from services.email_sender import send_email
from services.auth import decrypt_smtp_pass
from services.lead_scoring import apply_lead_score, build_outreach_context

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


_EMAIL_RE = re.compile(r"^[A-Z0-9._%+\-']+@[A-Z0-9.\-]+\.[A-Z]{2,}$", re.IGNORECASE)
_MOCK_FIRST_NAMES = {"alex", "sam", "jordan", "taylor", "morgan", "casey"}
_MOCK_LAST_NAMES = {"smith", "johnson", "williams", "brown", "jones", "garcia"}
from cachetools import TTLCache

_domain_resolution_cache: TTLCache = TTLCache(maxsize=10000, ttl=3600)
_mx_record_cache: TTLCache = TTLCache(maxsize=10000, ttl=3600)


def _email_domain(email: str) -> str:
    return (email or "").split("@")[-1].lower().strip()


def _looks_like_generated_mock_email(email: str) -> bool:
    local = (email or "").split("@", 1)[0].lower()
    parts = re.split(r"[._+\-]+", local)
    if len(parts) >= 2 and parts[0] in _MOCK_FIRST_NAMES and parts[1] in _MOCK_LAST_NAMES:
        return True
    # Also catch underscore-joined mock patterns like "casey_brown"
    if "_" in local:
        underscore_parts = local.split("_")
        if len(underscore_parts) >= 2 and underscore_parts[0] in _MOCK_FIRST_NAMES and underscore_parts[1] in _MOCK_LAST_NAMES:
            return True
    return False


def _domain_resolves(domain: str) -> bool:
    if not domain:
        return False
    if domain in _domain_resolution_cache:
        return _domain_resolution_cache[domain]
    try:
        socket.getaddrinfo(domain, None)
        ok = True
    except OSError:
        ok = False
    _domain_resolution_cache[domain] = ok
    return ok


def _domain_has_mx(domain: str) -> bool:
    """Check if domain has valid MX records (can receive email)."""
    if not domain:
        return False
    if domain in _mx_record_cache:
        return _mx_record_cache[domain]
    try:
        import dns.resolver
        answers = dns.resolver.resolve(domain, 'MX')
        ok = len(answers) > 0
    except Exception:
        ok = False
    _mx_record_cache[domain] = ok
    return ok


def _validate_lead_before_send(lead: Lead) -> Optional[str]:
    if not lead.email:
        return "missing_email"
    email = lead.email.strip().lower()
    if not _EMAIL_RE.match(email):
        return "invalid_email_format"
    if _bool_env("EMAIL_SKIP_GENERATED_MOCK_EMAILS", True) and _looks_like_generated_mock_email(email):
        return "suspected_generated_mock_email"
    # Check pre-verified status (from email_verifier pipeline)
    if getattr(lead, 'email_validation_status', None) == "invalid":
        return "email_pre_verified_invalid"
    domain = _email_domain(email)
    if _bool_env("EMAIL_CHECK_RECIPIENT_DOMAIN_DNS", True) and not _domain_resolves(domain):
        return "recipient_domain_does_not_resolve"
    # MX record check: domain may resolve (has A record) but cannot receive email
    if _bool_env("EMAIL_CHECK_RECIPIENT_MX", True) and not _domain_has_mx(domain):
        return "recipient_domain_has_no_mx_record"
    return None


def _is_email_good_for_lead(email: str, domain: str) -> bool:
    if not email or not _EMAIL_RE.match(email.strip()):
        return False

    normalized = email.strip().lower()
    local = normalized.split("@", 1)[0]
    blocked_locals = {
        "test", "example", "no-reply", "noreply", "donotreply",
        "mailer-daemon", "postmaster", "abuse", "privacy",
    }
    if local in blocked_locals:
        return False

    if _bool_env("EMAIL_SKIP_GENERATED_MOCK_EMAILS", True) and _looks_like_generated_mock_email(normalized):
        return False

    if _bool_env("SEARCH_REQUIRE_EMAIL_DOMAIN_MATCH", True):
        email_domain = _email_domain(normalized)
        candidate = (domain or "").lower().strip()
        if candidate and email_domain != candidate and not email_domain.endswith("." + candidate):
            return False

    return True


def _mark_lead_suppressed(lead: Lead, reason: str, db) -> None:
    if reason == "missing_email":
        lead.status = "needs_email"
    elif reason and reason.startswith("fit_score_too_low"):
        lead.status = "low_score"
    elif reason and reason.startswith("email_not_verified"):
        lead.status = "needs_email"  # Can re-verify later
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
            wf_max_followups = wf.max_followups or 3
            wf_name = wf.name
            wf_ai_prompt = wf.ai_prompt or "Introduce our company and ask for a quick meeting."
        finally:
            db.close()

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
            if not email_limit_reached:
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
                        is_sendable, reason = _is_lead_sendable_now(candidate, db)
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
                try:
                    # 0. Try LeadContact email enrichment if no email or unverified
                    has_usable_email = bool(email and email.strip())
                    if (not has_usable_email) and linkedin_url:
                        _lc_key = os.environ.get("LEADCONTACT_API_KEY", "").strip()
                        if _lc_key:
                            from services.leadcontact_client import LeadContactClient
                            lc = LeadContactClient(_lc_key)
                            lc_result = lc.query_email_with_validation(linkedin_url)
                            lc_email = lc_result.get("email")
                            if lc_email:
                                email = lc_email
                                has_usable_email = True
                                db_lc = SessionLocal()
                                try:
                                    lead_lc = db_lc.query(Lead).filter(Lead.id == lead_id).first()
                                    if lead_lc:
                                        lead_lc.email = lc_email
                                        lead_lc.email_validation_status = "valid" if lc_result.get("valid") else "catch-all"
                                        lead_lc.email_verified = bool(lc_result.get("valid"))
                                        lead_lc.data_sources = (lead_lc.data_sources or "") + ",leadcontact"
                                        db_lc.commit()
                                        logger.info(f"[LeadContact] Email found for {linkedin_url}: {lc_email}")
                                finally:
                                    db_lc.close()
                            else:
                                logger.info(f"[LeadContact] No email found for {linkedin_url}: {lc_result.get('error', 'unknown')}")

                    # 0.1. Try LeadContact phone enrichment if WhatsApp channel is enabled
                    if linkedin_url and not whatsapp_number and enable_whatsapp:
                        _lc_key = os.environ.get("LEADCONTACT_API_KEY", "").strip()
                        if _lc_key:
                            from services.leadcontact_client import LeadContactClient
                            lc = LeadContactClient(_lc_key)
                            phone_result = lc.query_phone(linkedin_url)
                            phones = phone_result.get("phones", [])
                            if phones:
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
                    
                    db_shot = SessionLocal()
                    try:
                        draft = await asyncio.to_thread(
                            generate_email,
                            first_name=first_name or "",
                            last_name=last_name or "",
                            company_name=company_name, 
                            target_role=job_title, 
                            website_summary=brief_summary, 
                            template=enriched_prompt,
                            db=db_shot,
                            persona_id=persona_id
                        )
                    finally:
                        db_shot.close()
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
                except Exception as e:
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
                    finally:
                        db2.close()
                except Exception as e:
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

                        # Get interval from env var or default to 72 hours
                        interval_hours = float(os.environ.get("EMAIL_FOLLOWUP_INTERVAL_HOURS", 72))
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

                            from services.followup_engine import draft_cold_followup_email
                            followup_draft = await asyncio.to_thread(
                                draft_cold_followup_email,
                                lead_data={"first_name": first_name, "company_name": company_name},
                                conversation_history=conversation_history,
                                followup_round=followup_round,
                                ai_prompt=wf_ai_prompt or ""
                            )

                            if followup_draft:
                                lead_obj.ai_draft = followup_draft
                                lead_obj.status = "drafted"
                                lead_obj.followup_count += 1
                                db2.commit()
                                logger.info(f"Cold follow-up #{followup_round} drafted for {email} (workflow: {wf_name})")
                    finally:
                        db2.close()
                except Exception as e:
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
                email = snovio.get_prospect_email(search_url) if search_url else None
                if email and _is_email_good_for_lead(email, domain):
                    prospect_emails.append((email, p))
                elif email:
                    logger.info(f"[QUALITY] Skipping low-quality or mismatched email {email} for {domain}")

        if not prospect_emails and _bool_env("SNOVIO_ALLOW_VERIFIED_DOMAIN_EMAIL_FALLBACK", True):
            for email in snovio.get_verified_domain_emails(domain):
                if _is_email_good_for_lead(email, domain):
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

    try:
        db = SessionLocal()
        try:
            wf = db.query(Workflow).filter(Workflow.id == wf_id).first()
            if not wf:
                return {"status": "error", "error": "workflow_not_found", "source": "leadcontact"}
            keywords = wf.search_keywords or ""
            target_role = wf.target_role or ""
            target_region = wf.target_region or ""
            product_focus = wf.product_focus or ""
        finally:
            db.close()

        # Map product_focus to LeadContact industry list (best-effort match)
        industry_map = {
            "padel": "Sporting Goods",
            "sports": "Sporting Goods",
            "pickleball": "Sporting Goods",
            "tennis": "Sporting Goods",
            "medical": "Medical Device",
            "pharma": "Pharmaceuticals",
            "software": "Computer Software",
            "saas": "Computer Software",
            "hardware": "Computer Hardware",
            "fintech": "Financial Services",
            "logistics": "Logistics & Supply Chain",
            "construction": "Construction",
            "education": "Education Management",
        }
        industries = []
        for k, v in industry_map.items():
            if k in (keywords + product_focus).lower():
                industries.append(v)

        # Parse job titles from target_role (comma or semicolon separated)
        job_titles = [t.strip() for t in re.split(r"[,;]", target_role) if t.strip()]

        # Use keywords to build a search query
        search_keyword = " ".join(keywords.split(",")[:2]).strip()

        logger.info(
            f"[LeadContact] Searching — titles={job_titles}, keyword={search_keyword}, "
            f"locations={target_region[:5] if target_region else 'any'}, industries={industries}"
        )

        result = lc.search_employees(
            job_titles=job_titles if job_titles else None,
            locations=[target_region] if target_region else None,
            industries=industries if industries else None,
            keyword=search_keyword if search_keyword else None,
            current_titles_only=True,
            per_page=min(max_lc_leads, batch_lead_limit),
        )

        employees = result.get("employees", [])
        if not employees:
            logger.info(f"[LeadContact] No employees found for keywords: {search_keyword}")
            return {"status": "ok", "source": "leadcontact", "new_leads": 0}

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
                existing = db_check.query(Lead).filter(
                    Lead.workflow_id == wf_id,
                    Lead.email == emp_email if emp_email else None,
                    Lead.linkedin_url == linkedin_url if linkedin_url else None,
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
        logger.info(f"[LeadContact] Added {new_leads} leads from LeadContact search")
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

def _is_lead_sendable_now(lead: Lead, db) -> tuple:
    from typing import Tuple, Optional
    suppression_reason = _validate_lead_before_send(lead)
    if suppression_reason:
        return False, suppression_reason

    # ── Fit-score gate: skip low-quality leads ──
    min_score = _int_env("EMAIL_MIN_FIT_SCORE", 60, 0, 100)
    if _bool_env("EMAIL_REQUIRE_MIN_FIT_SCORE", False):
        score = getattr(lead, 'fit_score', None)
        if score is not None and score < min_score:
            return False, f"fit_score_too_low({score}<{min_score})"

    # ── Email verification gate: only send to verified/catch-all addresses ──
    if _bool_env("EMAIL_REQUIRE_VERIFIED", True):
        v_status = getattr(lead, 'email_validation_status', None)
        if v_status is not None and v_status not in ("valid", "catch-all"):
            return False, f"email_not_verified({v_status})"

    if lead.timezone:
        try:
            import pytz
            local_tz = pytz.timezone(lead.timezone)
            local_now = datetime.now(local_tz)
            if local_now.hour < 9 or local_now.hour >= 17:
                return False, "outside_working_hours"
        except Exception:
            pass

    domain_cooldown_hours = _int_env("EMAIL_SAME_DOMAIN_COOLDOWN_HOURS", 24, 1, 168)
    if lead.domain:
        recent_to_same_domain = db.query(EmailLog).join(Lead).filter(
            Lead.domain == lead.domain,
            EmailLog.direction == "outbound",
            EmailLog.sent_at >= datetime.now(timezone.utc) - timedelta(hours=domain_cooldown_hours)
        ).count()
        if recent_to_same_domain > 0:
            return False, "domain_cooldown"

    return True, None

async def send_lead_email(lead: Lead, wf: Workflow, db):
    try:
        bounce_pause_reason = _workflow_bounce_pause_reason(wf.id, db)
        if bounce_pause_reason:
            logger.warning(f"Email sending paused for workflow '{wf.name}': {bounce_pause_reason}")
            return

        suppression_reason = _validate_lead_before_send(lead)
        if suppression_reason:
            _mark_lead_suppressed(lead, suppression_reason, db)
            logger.warning(f"Suppressed email to {lead.email or lead.domain}: {suppression_reason}")
            return

        # Timezone-aware sending: only send during recipient's working hours (9-17)
        if lead.timezone:
            try:
                import pytz
                local_tz = pytz.timezone(lead.timezone)
                local_now = datetime.now(local_tz)
                if local_now.hour < 9 or local_now.hour >= 17:
                    logger.info(
                        f"Skipping {lead.email}: outside working hours in {lead.timezone} "
                        f"(local time: {local_now.strftime('%H:%M')})"
                    )
                    return
            except Exception as tz_err:
                logger.warning(f"Timezone check failed for {lead.email} ({lead.timezone}): {tz_err}")

        # Same-domain cooldown: avoid sending multiple emails to the same company in a short window
        domain_cooldown_hours = _int_env("EMAIL_SAME_DOMAIN_COOLDOWN_HOURS", 24, 1, 168)
        if lead.domain:
            recent_to_same_domain = db.query(EmailLog).join(Lead).filter(
                Lead.domain == lead.domain,
                EmailLog.direction == "outbound",
                EmailLog.sent_at >= datetime.now(timezone.utc) - timedelta(hours=domain_cooldown_hours)
            ).count()
            if recent_to_same_domain > 0:
                logger.info(f"Skipping {lead.email}: domain {lead.domain} already contacted in last {domain_cooldown_hours}h")
                return

        workflow_emails = db.query(WorkflowEmail).filter(WorkflowEmail.workflow_id == wf.id).all()
        if not workflow_emails:
            return
            
        accounts = [we.email_account for we in workflow_emails]
        
        # Round Robin
        last_log = db.query(EmailLog).join(Lead).filter(
            Lead.workflow_id == wf.id,
            EmailLog.direction == "outbound"
        ).order_by(EmailLog.sent_at.desc()).first()
        
        account_to_use = accounts[0]
        if last_log:
            last_email = last_log.from_email
            for i, acc in enumerate(accounts):
                if acc.email == last_email:
                    account_to_use = accounts[(i + 1) % len(accounts)]
                    break

        per_account_daily_cap = _int_env("EMAIL_MAX_DAILY_PER_ACCOUNT", 15, 1, 500)
        today = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
        sent_by_account_today = db.query(EmailLog).filter(
            EmailLog.direction == "outbound",
            EmailLog.from_email == account_to_use.email,
            EmailLog.sent_at >= today,
        ).count()
        if sent_by_account_today >= per_account_daily_cap:
            logger.info(f"Daily cap reached for {account_to_use.email}: {sent_by_account_today}/{per_account_daily_cap}")
            return
                    
        # Parse subject and body from AI draft
        import re
        lines = lead.ai_draft.split('\n')
        subject = ""
        body = lead.ai_draft
        
        for i, line in enumerate(lines):
            line_stripped = line.strip()
            # Match "Subject: xxx" or "SUBJECT: xxx" or "主题: xxx"
            m = re.match(r'^(?:\*{0,2})(?:subject|SUBJECT|主题)\s*[：:]\s*(.+)', line_stripped, re.IGNORECASE)
            if m:
                subject = m.group(1).strip().strip('*').strip()
                raw_body = "\n".join(lines[i+1:]).strip()
                # Remove "BODY:" line if present
                raw_body = re.sub(r'^(?:\*{0,2})(?:body|BODY)\s*[：:]\s*\n?', '', raw_body).strip()
                body = raw_body
                break
        
        # If no subject was extracted, generate a simple one
        if not subject:
            subject = f"Quick question for {lead.company_name or 'you'}"
        
        # === Aggressive placeholder cleanup ===
        sender_name = account_to_use.display_name or "there"
        first_n = lead.first_name or "there"
        comp_n = lead.company_name or "your company"
        
        # Replace known recipient placeholders
        body = re.sub(r'\[First Name\]|\[first name\]', first_n, body, flags=re.IGNORECASE)
        body = re.sub(r'\[Company\]|\[Company Name\]|\[Target Company\]', comp_n, body, flags=re.IGNORECASE)
        
        # Replace known sender placeholders (use display_name, not hardcoded company)
        body = re.sub(r'\[Your Name\]|\[Name\]|\[Sender Name\]', sender_name, body, flags=re.IGNORECASE)
        body = re.sub(r'\[Our Company\]|\[Your Company\]', '', body, flags=re.IGNORECASE)
        
        # Nuke ALL remaining bracket placeholders (e.g. [Title], [Phone], [Email], [LinkedIn], etc.)
        body = re.sub(r'\[.*?\]', '', body)
        
        # === Thorough signature cleanup ===
        # Remove sign-off lines + optional name/company lines that follow
        # This catches: "Best regards,\nHuilong\nPeter Patter" and similar multi-line patterns
        sign_off_words = r'(?:Best regards|Kind regards|Warm regards|Regards|Cheers|Thanks|Thank you|Best|Sincerely|Yours truly|Looking forward)'
        # Pattern: sign-off word, optional comma, then up to 3 trailing lines (name, title, company)
        body = re.sub(
            r'\n\s*' + sign_off_words + r',?\s*(?:\n.{0,60}){0,3}\s*$',
            '', body, flags=re.IGNORECASE
        )
        
        # Remove pipe separators and orphaned whitespace lines
        body = "\n".join([line for line in body.split("\n") if line.strip() not in ('|', '', '  |  ', '|  ')])

        # Wrap in branded HTML template
        custom_sig = wf.email_signature if hasattr(wf, 'email_signature') and wf.email_signature else None
        body_html = _build_email_html(body, account_to_use.display_name or account_to_use.email, custom_sig)

        in_reply_to = None
        if lead.followup_count > 0:
            last_outbound = db.query(EmailLog).filter(
                EmailLog.lead_id == lead.id,
                EmailLog.direction == "outbound"
            ).order_by(EmailLog.sent_at.desc()).first()
            if last_outbound:
                in_reply_to = last_outbound.message_id

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
            references=in_reply_to
        )
        
        if res.get("success"):
            lead.status = "sent"
            lead.send_fail_count = 0
            db_log = EmailLog(
                lead_id=lead.id,
                direction="outbound",
                from_email=account_to_use.email,
                to_email=lead.email,
                subject=subject,
                body=body,
                message_id=res.get("message_id")
            )
            db.add(db_log)
            db.commit()
            logger.info(f"✉ Sent to {lead.email} via {account_to_use.email}")
        else:
            lead.send_fail_count = (lead.send_fail_count or 0) + 1
            if lead.send_fail_count >= 3:
                lead.status = "send_failed"
                logger.error(f"✘ Permanently failed to send to {lead.email} after {lead.send_fail_count} attempts: {res.get('message')}")
            else:
                logger.warning(f"Send failed to {lead.email} (attempt {lead.send_fail_count}/3): {res.get('message')}")
            db.commit()
            
    except Exception as e:
        logger.error(f"Send stage error for {lead.email}: {e}")


def _build_email_html(body_text: str, sender_name: str, custom_signature: str = None) -> str:
    """Wrap plain text body in a clean, professional HTML email template."""
    body_paragraphs = ""
    for para in body_text.strip().split("\n\n"):
        cleaned = para.strip().replace("\n", "<br>")
        if cleaned:
            body_paragraphs += f"<p style='margin:0 0 12px 0;line-height:1.6;color:#333333;'>{cleaned}</p>\n"
    
    if not body_paragraphs:
        body_paragraphs = f"<p style='margin:0 0 12px 0;line-height:1.6;color:#333333;'>{body_text.replace(chr(10), '<br>')}</p>"

    # Build signature block
    if custom_signature and custom_signature.strip():
        sig_lines = custom_signature.strip().split("\n")
        sig_html = "<br>".join(line.strip() for line in sig_lines if line.strip())
        signature_block = f"<p style='margin:0;color:#555;line-height:1.5;'>{sig_html}</p>"
    else:
        signature_block = f"<p style='margin:0;'>Best regards,<br><strong style='color:#555;'>{sender_name}</strong></p>"

    return f"""<!DOCTYPE html>
<html>
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1.0"></head>
<body style="margin:0;padding:0;background-color:#f4f4f7;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;">
<table width="100%" cellpadding="0" cellspacing="0" style="background-color:#f4f4f7;padding:24px 0;">
<tr><td align="center">
<table width="600" cellpadding="0" cellspacing="0" style="background-color:#ffffff;border-radius:8px;overflow:hidden;box-shadow:0 2px 8px rgba(0,0,0,0.06);">
<tr><td style="padding:32px 40px;">
{body_paragraphs}
</td></tr>
<tr><td style="padding:16px 40px 24px;border-top:1px solid #eee;font-size:13px;color:#999;">
{signature_block}
<p style="margin:8px 0 0;font-size:11px;color:#bbb;">If you no longer wish to receive these emails, please reply with "unsubscribe".</p>
</td></tr>
</table>
</td></tr>
</table>
</body>
</html>"""


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

    # 1. Find a connected LinkedIn account
    db = SessionLocal()
    try:
        linkedin_account = db.query(ChannelAccount).filter(
            ChannelAccount.account_type == "LINKEDIN",
            ChannelAccount.status == "OK"
        ).first()
        if not linkedin_account:
            logger.warning(f"LinkedIn is enabled for workflow '{wf_name}', but no connected LinkedIn account is available")
            return
        account_id = linkedin_account.unipile_account_id
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
        lead_data = [(l.id, l.linkedin_url, l.first_name, l.last_name, l.company_name, l.job_title, l.domain) for l in candidates]
    finally:
        db.close()

    if not lead_data:
        return

    client = UnipileClient()

    for lead_id, linkedin_url, first_name, last_name, company_name, job_title, domain in lead_data:
        try:
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
                    logger.warning(f"💼 LinkedIn invite FAILED for {linkedin_url} [workflow: {wf_name}]")
            finally:
                db.close()

            # Rate limit: wait between sends
            await asyncio.sleep(random.randint(30, 90))

        except Exception as e:
            logger.error(f"LinkedIn send error for lead {lead_id}: {e}")


async def _send_whatsapp_messages(wf_id: int, wf_name: str, whatsapp_template: str, ai_prompt: str):
    """Stage 2.6: Auto-send WhatsApp messages for leads that have whatsapp_number."""
    from models import ChannelAccount, MessageLog, LeadBrief
    from services.unipile_client import UnipileClient
    from services.ai_writer import generate_whatsapp_message

    # 1. Find a connected WhatsApp account
    db = SessionLocal()
    try:
        wa_account = db.query(ChannelAccount).filter(
            ChannelAccount.account_type == "WHATSAPP",
            ChannelAccount.status == "OK"
        ).first()
        if not wa_account:
            return
        account_id = wa_account.unipile_account_id
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
        lead_data = [(l.id, l.whatsapp_number, l.first_name, l.company_name, l.domain) for l in candidates]
    finally:
        db.close()

    if not lead_data:
        return

    client = UnipileClient()

    for lead_id, phone, first_name, company_name, domain in lead_data:
        try:
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
                    logger.warning(f"💬 WhatsApp FAILED for {phone} [workflow: {wf_name}]")
            finally:
                db.close()

            await asyncio.sleep(random.randint(20, 60))

        except Exception as e:
            logger.error(f"WhatsApp send error for lead {lead_id}: {e}")
