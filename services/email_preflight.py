import os
import re
import socket
from datetime import datetime, timedelta, timezone
from typing import Optional

from cachetools import TTLCache
from sqlalchemy.orm import Session

from models import EmailLog, Lead
from services.suppression import owner_id_for_lead, suppression_reason


EMAIL_RE = re.compile(r"^[A-Z0-9._%+\-']+@[A-Z0-9.\-]+\.[A-Z]{2,}$", re.IGNORECASE)
MOCK_FIRST_NAMES = {"alex", "sam", "jordan", "taylor", "morgan", "casey"}
MOCK_LAST_NAMES = {"smith", "johnson", "williams", "brown", "jones", "garcia"}
VERIFIED_EMAIL_STATUSES = {"valid", "verified"}

_domain_resolution_cache: TTLCache = TTLCache(maxsize=10000, ttl=3600)
_mx_record_cache: TTLCache = TTLCache(maxsize=10000, ttl=3600)


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


def email_domain(email: str) -> str:
    return (email or "").split("@")[-1].lower().strip()


def looks_like_generated_mock_email(email: str) -> bool:
    local = (email or "").split("@", 1)[0].lower()
    parts = re.split(r"[._+\-]+", local)
    if len(parts) >= 2 and parts[0] in MOCK_FIRST_NAMES and parts[1] in MOCK_LAST_NAMES:
        return True
    if "_" in local:
        underscore_parts = local.split("_")
        if len(underscore_parts) >= 2 and underscore_parts[0] in MOCK_FIRST_NAMES and underscore_parts[1] in MOCK_LAST_NAMES:
            return True
    return False


def domain_resolves(domain: str) -> bool:
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


def domain_has_mx(domain: str) -> bool:
    if not domain:
        return False
    if domain in _mx_record_cache:
        return _mx_record_cache[domain]
    try:
        import dns.resolver
        answers = dns.resolver.resolve(domain, "MX")
        ok = len(answers) > 0
    except Exception:
        ok = False
    _mx_record_cache[domain] = ok
    return ok


def validate_lead_before_send(lead: Lead, db: Optional[Session] = None) -> Optional[str]:
    if lead.status in {"unsubscribed", "rejected"}:
        return f"lead_status_{lead.status}"
    if db is not None:
        owner_id = owner_id_for_lead(db, lead)
        reason = suppression_reason(db, email=lead.email, domain=lead.domain, user_id=owner_id)
        if reason:
            return reason
    if not lead.email:
        return "missing_email"
    email = lead.email.strip().lower()
    if not EMAIL_RE.match(email):
        return "invalid_email_format"
    if _bool_env("EMAIL_SKIP_GENERATED_MOCK_EMAILS", True) and looks_like_generated_mock_email(email):
        return "suspected_generated_mock_email"
    v_status = getattr(lead, "email_validation_status", None)
    if v_status == "invalid":
        return "email_pre_verified_invalid"
    if _bool_env("EMAIL_REQUIRE_VERIFIED", True) and v_status not in VERIFIED_EMAIL_STATUSES:
        return f"email_not_verified({v_status})"
    domain = email_domain(email)
    if _bool_env("EMAIL_CHECK_RECIPIENT_DOMAIN_DNS", True) and not domain_resolves(domain):
        return "recipient_domain_does_not_resolve"
    if _bool_env("EMAIL_CHECK_RECIPIENT_MX", True) and not domain_has_mx(domain):
        return "recipient_domain_has_no_mx_record"
    return None


def quality_gate_reason(lead: Lead) -> Optional[str]:
    min_score = _int_env("EMAIL_MIN_FIT_SCORE", 60, 0, 100)
    if _bool_env("EMAIL_REQUIRE_MIN_FIT_SCORE", False):
        score = getattr(lead, "fit_score", None)
        if score is not None and score < min_score:
            return f"fit_score_too_low({score}<{min_score})"

    if _bool_env("EMAIL_REQUIRE_VERIFIED", True):
        v_status = getattr(lead, "email_validation_status", None)
        if v_status not in VERIFIED_EMAIL_STATUSES:
            return f"email_not_verified({v_status})"
    return None


def temporary_send_block_reason(
    lead: Lead,
    db: Session,
    *,
    now: Optional[datetime] = None,
) -> Optional[str]:
    current_time = now or datetime.now(timezone.utc)
    if current_time.tzinfo is None:
        current_time = current_time.replace(tzinfo=timezone.utc)

    if lead.timezone:
        try:
            import pytz
            local_tz = pytz.timezone(lead.timezone)
            local_now = current_time.astimezone(local_tz)
            if local_now.hour < 9 or local_now.hour >= 17:
                return "outside_working_hours"
        except Exception:
            pass

    domain_cooldown_hours = _int_env("EMAIL_SAME_DOMAIN_COOLDOWN_HOURS", 24, 1, 168)
    if lead.domain:
        recent_to_same_domain = (
            db.query(EmailLog)
            .join(Lead)
            .filter(
                Lead.domain == lead.domain,
                EmailLog.direction == "outbound",
                EmailLog.sent_at >= current_time - timedelta(hours=domain_cooldown_hours),
            )
            .count()
        )
        if recent_to_same_domain > 0:
            return "domain_cooldown"

    return None


def is_lead_sendable_now(lead: Lead, db: Session) -> tuple[bool, Optional[str]]:
    preflight_reason = validate_lead_before_send(lead, db)
    if preflight_reason:
        return False, preflight_reason

    quality_reason = quality_gate_reason(lead)
    if quality_reason:
        return False, quality_reason

    temporary_reason = temporary_send_block_reason(lead, db)
    if temporary_reason:
        return False, temporary_reason

    return True, None


def is_email_good_for_lead(email: str, domain: str) -> bool:
    if not email or not EMAIL_RE.match(email.strip()):
        return False

    normalized = email.strip().lower()
    local = normalized.split("@", 1)[0]
    blocked_locals = {
        "test", "example", "no-reply", "noreply", "donotreply",
        "mailer-daemon", "postmaster", "abuse", "privacy",
    }
    if local in blocked_locals:
        return False

    if _bool_env("EMAIL_SKIP_GENERATED_MOCK_EMAILS", True) and looks_like_generated_mock_email(normalized):
        return False

    if _bool_env("SEARCH_REQUIRE_EMAIL_DOMAIN_MATCH", True):
        candidate_domain = email_domain(normalized)
        candidate = (domain or "").lower().strip()
        if candidate and candidate_domain != candidate and not candidate_domain.endswith("." + candidate):
            return False

    return True
