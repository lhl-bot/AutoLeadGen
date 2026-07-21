import base64
import hashlib
import hmac
import os
from typing import Optional
from urllib.parse import urlparse

from sqlalchemy import func, or_
from sqlalchemy.orm import Session

import models
from runtime_config import is_production_like, read_secret


def normalize_email(email: Optional[str]) -> Optional[str]:
    value = (email or "").strip().lower()
    return value or None


def normalize_domain(value: Optional[str]) -> Optional[str]:
    raw = (value or "").strip().lower()
    if not raw:
        return None
    if "://" in raw:
        raw = urlparse(raw).netloc
    if "@" in raw:
        raw = raw.rsplit("@", 1)[-1]
    raw = raw.removeprefix("www.").split("/")[0].strip(".")
    return raw or None


def owner_id_for_lead(db: Session, lead: models.Lead) -> Optional[int]:
    if lead.workflow_id:
        workflow = db.query(models.Workflow.user_id).filter(models.Workflow.id == lead.workflow_id).first()
        if workflow:
            return workflow.user_id
    if lead.client_pool_id:
        pool = db.query(models.ClientPool.user_id).filter(models.ClientPool.id == lead.client_pool_id).first()
        if pool:
            return pool.user_id
    return None


def find_suppression(
    db: Session,
    *,
    email: Optional[str] = None,
    domain: Optional[str] = None,
    user_id: Optional[int] = None,
) -> Optional[models.EmailSuppression]:
    email = normalize_email(email)
    domain = normalize_domain(domain or email)
    filters = []
    if email:
        filters.append(models.EmailSuppression.email == email)
    if domain:
        filters.append(models.EmailSuppression.domain == domain)
    if not filters:
        return None

    query = db.query(models.EmailSuppression).filter(or_(*filters))
    if user_id is not None:
        query = query.filter(or_(models.EmailSuppression.user_id == user_id, models.EmailSuppression.user_id.is_(None)))
    else:
        query = query.filter(models.EmailSuppression.user_id.is_(None))
    return query.order_by(models.EmailSuppression.id.desc()).first()


def find_exact_email_suppression(
    db: Session,
    *,
    email: Optional[str],
    user_id: Optional[int] = None,
) -> Optional[models.EmailSuppression]:
    """Return an exact legacy email suppression without widening to a domain.

    Product V2 keeps the legacy suppression table read-only during migration.
    This lookup is the compatibility safety net for suppressions whose legacy
    Lead no longer maps to a V2 ContactPoint.  Normalizing in SQL also covers
    historical rows written before canonical normalization was enforced.
    """

    normalized = normalize_email(email)
    if not normalized:
        return None
    query = db.query(models.EmailSuppression).filter(
        models.EmailSuppression.email.isnot(None),
        func.lower(func.trim(models.EmailSuppression.email)) == normalized,
    )
    if user_id is not None:
        query = query.filter(
            or_(
                models.EmailSuppression.user_id == user_id,
                models.EmailSuppression.user_id.is_(None),
            )
        )
    else:
        query = query.filter(models.EmailSuppression.user_id.is_(None))
    return query.order_by(models.EmailSuppression.id.desc()).first()


def suppression_reason(
    db: Session,
    *,
    email: Optional[str] = None,
    domain: Optional[str] = None,
    user_id: Optional[int] = None,
) -> Optional[str]:
    suppression = find_suppression(db, email=email, domain=domain, user_id=user_id)
    if not suppression:
        return None
    target = suppression.email or suppression.domain or "recipient"
    return f"suppressed:{suppression.reason}:{target}"


def ensure_suppression(
    db: Session,
    *,
    email: Optional[str] = None,
    domain: Optional[str] = None,
    user_id: Optional[int] = None,
    lead_id: Optional[int] = None,
    reason: str = "manual",
    source: str = "system",
) -> models.EmailSuppression:
    email = normalize_email(email)
    # Scope is explicit. An email-only unsubscribe must never be silently
    # widened to every address at the same company domain.
    domain = normalize_domain(domain)
    existing = find_suppression(db, email=email, domain=domain, user_id=user_id)
    if existing:
        if lead_id and not existing.lead_id:
            existing.lead_id = lead_id
        if reason and existing.reason == "manual":
            existing.reason = reason
        if source:
            existing.source = source
        return existing

    suppression = models.EmailSuppression(
        user_id=user_id,
        lead_id=lead_id,
        email=email,
        domain=domain,
        reason=reason,
        source=source,
    )
    db.add(suppression)
    return suppression


def suppress_lead(
    db: Session,
    lead: models.Lead,
    *,
    reason: str = "unsubscribe",
    source: str = "reply",
    status: str = "unsubscribed",
) -> models.EmailSuppression:
    user_id = owner_id_for_lead(db, lead)
    suppression = ensure_suppression(
        db,
        email=lead.email,
        user_id=user_id,
        lead_id=lead.id,
        reason=reason,
        source=source,
    )
    lead.status = status
    return suppression


def _secret() -> bytes:
    secret = read_secret("UNSUBSCRIBE_TOKEN_SECRET")
    jwt_secret = read_secret("JWT_SECRET_KEY")
    if not secret:
        secret = jwt_secret
    if not secret:
        raise RuntimeError("JWT_SECRET_KEY or UNSUBSCRIBE_TOKEN_SECRET must be set")
    if is_production_like() and secret == jwt_secret:
        raise RuntimeError(
            "UNSUBSCRIBE_TOKEN_SECRET must be independent from JWT_SECRET_KEY"
        )
    if is_production_like() and len(secret.encode("utf-8")) < 32:
        raise RuntimeError("UNSUBSCRIBE_TOKEN_SECRET must contain at least 32 bytes")
    return secret.encode("utf-8")


def generate_unsubscribe_token(lead_id: int, email: str) -> str:
    normalized_email = normalize_email(email) or ""
    payload = f"{lead_id}:{normalized_email}"
    signature = hmac.new(_secret(), payload.encode("utf-8"), hashlib.sha256).hexdigest()
    raw = f"{payload}:{signature}".encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("utf-8").rstrip("=")


def verify_unsubscribe_token(token: str) -> tuple[int, str]:
    if not 80 <= len(token) <= 1024 or any(
        char not in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"
        for char in token
    ):
        raise ValueError("Invalid unsubscribe token")
    padded = token + "=" * (-len(token) % 4)
    try:
        raw = base64.b64decode(
            padded.encode("ascii"), altchars=b"-_", validate=True
        ).decode("utf-8")
        lead_id_text, email, signature = raw.split(":", 2)
        lead_id = int(lead_id_text)
    except Exception as exc:
        raise ValueError("Invalid unsubscribe token") from exc

    normalized_email = normalize_email(email) or ""
    if lead_id < 1 or not normalized_email or len(normalized_email) > 255:
        raise ValueError("Invalid unsubscribe token")
    payload = f"{lead_id}:{normalized_email}"
    expected = hmac.new(_secret(), payload.encode("utf-8"), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(signature, expected):
        raise ValueError("Invalid unsubscribe token")
    return lead_id, normalized_email


def generate_v2_unsubscribe_token(
    *,
    owner_id: int,
    contact_point_id: int,
    identity_hash: str,
) -> str:
    """Create a stable opt-out token bound to one V2 email identity."""

    digest = (identity_hash or "").strip().lower()
    if len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
        raise ValueError("A valid contact-point identity hash is required")
    payload = f"v2:{owner_id}:{contact_point_id}:{digest}"
    signature = hmac.new(_secret(), payload.encode("utf-8"), hashlib.sha256).hexdigest()
    return base64.urlsafe_b64encode(f"{payload}:{signature}".encode("utf-8")).decode("ascii").rstrip("=")


def verify_v2_unsubscribe_token(token: str) -> tuple[int, int, str]:
    if not 100 <= len(token) <= 512 or any(
        char not in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"
        for char in token
    ):
        raise ValueError("Invalid unsubscribe token")
    padded = token + "=" * (-len(token) % 4)
    try:
        raw = base64.b64decode(
            padded.encode("ascii"), altchars=b"-_", validate=True
        ).decode("utf-8")
        version, owner_text, point_text, digest, signature = raw.split(":", 4)
        owner_id = int(owner_text)
        contact_point_id = int(point_text)
    except Exception as exc:
        raise ValueError("Invalid unsubscribe token") from exc
    if (
        version != "v2"
        or owner_id < 1
        or contact_point_id < 1
        or len(digest) != 64
        or any(char not in "0123456789abcdef" for char in digest)
    ):
        raise ValueError("Invalid unsubscribe token")
    payload = f"v2:{owner_id}:{contact_point_id}:{digest}"
    expected = hmac.new(_secret(), payload.encode("utf-8"), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(signature, expected):
        raise ValueError("Invalid unsubscribe token")
    return owner_id, contact_point_id, digest
