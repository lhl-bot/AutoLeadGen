import base64
import hashlib
import hmac
import os
from typing import Optional
from urllib.parse import urlparse

from sqlalchemy import or_
from sqlalchemy.orm import Session

import models


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
    domain = normalize_domain(domain or email)
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
        domain=lead.domain,
        user_id=user_id,
        lead_id=lead.id,
        reason=reason,
        source=source,
    )
    lead.status = status
    return suppression


def _secret() -> bytes:
    secret = os.environ.get("UNSUBSCRIBE_TOKEN_SECRET") or os.environ.get("JWT_SECRET_KEY")
    if not secret:
        raise RuntimeError("JWT_SECRET_KEY or UNSUBSCRIBE_TOKEN_SECRET must be set")
    return secret.encode("utf-8")


def generate_unsubscribe_token(lead_id: int, email: str) -> str:
    normalized_email = normalize_email(email) or ""
    payload = f"{lead_id}:{normalized_email}"
    signature = hmac.new(_secret(), payload.encode("utf-8"), hashlib.sha256).hexdigest()
    raw = f"{payload}:{signature}".encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("utf-8").rstrip("=")


def verify_unsubscribe_token(token: str) -> tuple[int, str]:
    padded = token + "=" * (-len(token) % 4)
    try:
        raw = base64.urlsafe_b64decode(padded.encode("utf-8")).decode("utf-8")
        lead_id_text, email, signature = raw.split(":", 2)
        lead_id = int(lead_id_text)
    except Exception as exc:
        raise ValueError("Invalid unsubscribe token") from exc

    payload = f"{lead_id}:{normalize_email(email) or ''}"
    expected = hmac.new(_secret(), payload.encode("utf-8"), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(signature, expected):
        raise ValueError("Invalid unsubscribe token")
    return lead_id, normalize_email(email) or ""
