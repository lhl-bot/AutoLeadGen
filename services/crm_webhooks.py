"""Outbound CRM webhooks: push lead events to user-configured endpoints.

Delivery is best-effort and never blocks or breaks the caller — events are sent on a
background thread with a short timeout, and the latest delivery status is recorded on
the webhook row for debugging.
"""
import hashlib
import hmac
import json
import logging
import threading
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import requests

import models

logger = logging.getLogger(__name__)

# Canonical event names. The frontend offers these as subscription options.
KNOWN_EVENTS = [
    "lead.contacted",
    "lead.interested",
    "lead.quoting",
    "lead.won",
    "lead.lost",
    "lead.replied",
]

_TIMEOUT = (5, 10)  # (connect, read) seconds


def parse_events(raw: Optional[str]) -> List[str]:
    if not raw:
        return []
    return [e.strip() for e in raw.split(",") if e.strip()]


def lead_payload(lead: models.Lead) -> Dict[str, Any]:
    return {
        "id": lead.id,
        "company_name": lead.company_name,
        "domain": lead.domain,
        "first_name": lead.first_name,
        "last_name": lead.last_name,
        "email": lead.email,
        "job_title": lead.job_title,
        "linkedin_url": lead.linkedin_url,
        "whatsapp_number": lead.whatsapp_number,
        "status": lead.status,
        "sales_stage": lead.sales_stage,
        "fit_score": lead.fit_score,
        "fit_grade": lead.fit_grade,
    }


def sign(secret: Optional[str], body: bytes) -> Optional[str]:
    if not secret:
        return None
    return hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def _deliver(webhook_id: int, url: str, secret: Optional[str], payload: Dict[str, Any]) -> None:
    """Send one webhook in its own session and record the result. Never raises."""
    from database import SessionLocal

    body = json.dumps(payload, default=str).encode("utf-8")
    headers = {"Content-Type": "application/json", "User-Agent": "AutoLeadGen-Webhook/1"}
    signature = sign(secret, body)
    if signature:
        headers["X-AutoLeadGen-Signature"] = signature

    status: Optional[int] = None
    error: Optional[str] = None
    try:
        resp = requests.post(url, data=body, headers=headers, timeout=_TIMEOUT)
        status = resp.status_code
        if status >= 400:
            error = f"HTTP {status}: {resp.text[:200]}"
    except Exception as exc:
        error = str(exc)[:300]

    db = SessionLocal()
    try:
        wh = db.query(models.CrmWebhook).filter(models.CrmWebhook.id == webhook_id).first()
        if wh:
            wh.last_status = status
            wh.last_error = error
            wh.last_delivered_at = datetime.now(timezone.utc)
            db.commit()
    except Exception:
        db.rollback()
    finally:
        db.close()

    if error:
        logger.warning(f"CRM webhook {webhook_id} delivery issue: {error}")


def dispatch_event(db, user_id: Optional[int], event: str, lead: models.Lead) -> int:
    """Fan out an event to the user's active webhooks subscribed to it.

    Returns the number of webhooks scheduled for delivery. Delivery runs on
    background threads, so this returns quickly and never blocks the request.
    """
    if not user_id:
        return 0
    try:
        webhooks = (
            db.query(models.CrmWebhook)
            .filter(
                models.CrmWebhook.user_id == user_id,
                models.CrmWebhook.is_active.is_(True),
            )
            .all()
        )
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning(f"Failed to load CRM webhooks for user {user_id}: {exc}")
        return 0

    targets = [w for w in webhooks if event in parse_events(w.events)]
    if not targets:
        return 0

    payload_lead = lead_payload(lead)
    scheduled = 0
    for wh in targets:
        payload = {
            "event": event,
            "sent_at": datetime.now(timezone.utc).isoformat(),
            "webhook_id": wh.id,
            "lead": payload_lead,
        }
        thread = threading.Thread(
            target=_deliver, args=(wh.id, wh.url, wh.secret, payload), daemon=True
        )
        thread.start()
        scheduled += 1
    return scheduled
