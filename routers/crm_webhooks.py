import json
from datetime import datetime, timezone

import requests
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import List

import models, schemas
from database import get_db
from services.auth import get_current_user
from services.crm_webhooks import KNOWN_EVENTS, sign

router = APIRouter(prefix="/api/crm_webhooks", tags=["crm_webhooks"])


def _to_read(wh: models.CrmWebhook) -> schemas.CrmWebhook:
    data = schemas.CrmWebhook.model_validate(wh)
    data.has_secret = bool(wh.secret)
    return data


def _owned(webhook_id: int, db: Session, user: models.User) -> models.CrmWebhook:
    query = db.query(models.CrmWebhook).filter(models.CrmWebhook.id == webhook_id)
    if not user.is_admin:
        query = query.filter(models.CrmWebhook.user_id == user.id)
    wh = query.first()
    if not wh:
        raise HTTPException(status_code=404, detail="Webhook not found")
    return wh


@router.get("/events")
def list_events(user: models.User = Depends(get_current_user)):
    return {"events": KNOWN_EVENTS}


@router.get("/", response_model=List[schemas.CrmWebhook])
def list_webhooks(db: Session = Depends(get_db), user: models.User = Depends(get_current_user)):
    query = db.query(models.CrmWebhook)
    if not user.is_admin:
        query = query.filter(models.CrmWebhook.user_id == user.id)
    return [_to_read(w) for w in query.order_by(models.CrmWebhook.id.desc()).all()]


@router.post("/", response_model=schemas.CrmWebhook)
def create_webhook(payload: schemas.CrmWebhookCreate, db: Session = Depends(get_db), user: models.User = Depends(get_current_user)):
    data = payload.model_dump()
    secret = data.pop("secret", None)
    wh = models.CrmWebhook(**data, secret=(secret or None), user_id=user.id)
    db.add(wh)
    db.commit()
    db.refresh(wh)
    return _to_read(wh)


@router.put("/{webhook_id}", response_model=schemas.CrmWebhook)
def update_webhook(webhook_id: int, payload: schemas.CrmWebhookCreate, db: Session = Depends(get_db), user: models.User = Depends(get_current_user)):
    wh = _owned(webhook_id, db, user)
    data = payload.model_dump()
    secret = data.pop("secret", None)
    for key, value in data.items():
        setattr(wh, key, value)
    # Only overwrite the secret when a non-empty value is supplied, so editing other
    # fields doesn't wipe an existing secret the UI never shows.
    if secret:
        wh.secret = secret
    db.commit()
    db.refresh(wh)
    return _to_read(wh)


@router.delete("/{webhook_id}")
def delete_webhook(webhook_id: int, db: Session = Depends(get_db), user: models.User = Depends(get_current_user)):
    wh = _owned(webhook_id, db, user)
    db.delete(wh)
    db.commit()
    return {"ok": True}


@router.post("/{webhook_id}/test")
def test_webhook(webhook_id: int, db: Session = Depends(get_db), user: models.User = Depends(get_current_user)):
    """Send a synchronous sample payload so the user can confirm the endpoint works."""
    wh = _owned(webhook_id, db, user)
    payload = {
        "event": "test.ping",
        "sent_at": datetime.now(timezone.utc).isoformat(),
        "webhook_id": wh.id,
        "lead": {
            "id": 0, "company_name": "Acme Components", "domain": "acme-components.com",
            "first_name": "Alex", "last_name": "Chen", "email": "alex@acme-components.com",
            "job_title": "Purchasing Manager", "status": "replied", "sales_stage": "won",
            "fit_score": 88, "fit_grade": "A",
        },
    }
    body = json.dumps(payload, default=str).encode("utf-8")
    headers = {"Content-Type": "application/json", "User-Agent": "AutoLeadGen-Webhook/1"}
    signature = sign(wh.secret, body)
    if signature:
        headers["X-AutoLeadGen-Signature"] = signature

    status = None
    error = None
    try:
        resp = requests.post(wh.url, data=body, headers=headers, timeout=(5, 10))
        status = resp.status_code
        if status >= 400:
            error = f"HTTP {status}: {resp.text[:200]}"
    except Exception as exc:
        error = str(exc)[:300]

    wh.last_status = status
    wh.last_error = error
    wh.last_delivered_at = datetime.now(timezone.utc)
    db.commit()
    return {"ok": error is None, "status": status, "error": error}
