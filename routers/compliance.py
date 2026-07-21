from datetime import datetime, timezone
from html import escape
import hmac
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from sqlalchemy import or_
from sqlalchemy.orm import Session

import models
from product_v2 import models as v2_models
from product_v2.enums import Channel, RestrictionScope
from database import get_db
from services.auth import get_current_user
from services.suppression import (
    ensure_suppression,
    normalize_domain,
    normalize_email,
    owner_id_for_lead,
    suppress_lead,
    verify_unsubscribe_token,
    verify_v2_unsubscribe_token,
)

router = APIRouter(tags=["compliance"])


class SuppressionCreate(BaseModel):
    email: Optional[str] = None
    domain: Optional[str] = None
    reason: str = "manual"


@router.get("/api/suppressions")
def list_suppressions(
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
):
    query = db.query(models.EmailSuppression)
    if not user.is_admin:
        query = query.filter(or_(models.EmailSuppression.user_id == user.id, models.EmailSuppression.user_id.is_(None)))

    rows = query.order_by(models.EmailSuppression.id.desc()).offset(offset).limit(limit).all()
    return [
        {
            "id": row.id,
            "email": row.email,
            "domain": row.domain,
            "reason": row.reason,
            "source": row.source,
            "lead_id": row.lead_id,
            "created_at": row.created_at.isoformat() if row.created_at else None,
        }
        for row in rows
    ]


@router.post("/api/suppressions")
def create_suppression(
    payload: SuppressionCreate,
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
):
    email = normalize_email(payload.email)
    domain = normalize_domain(payload.domain)
    if not email and not domain:
        raise HTTPException(status_code=400, detail="Provide an email or domain to suppress")

    suppression = ensure_suppression(
        db,
        email=email,
        domain=domain,
        user_id=user.id,
        reason=payload.reason or "manual",
        source="manual",
    )
    db.commit()
    db.refresh(suppression)
    return {
        "id": suppression.id,
        "email": suppression.email,
        "domain": suppression.domain,
        "reason": suppression.reason,
        "source": suppression.source,
    }


@router.delete("/api/suppressions/{suppression_id}")
def delete_suppression(
    suppression_id: int,
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
):
    query = db.query(models.EmailSuppression).filter(models.EmailSuppression.id == suppression_id)
    if not user.is_admin:
        query = query.filter(models.EmailSuppression.user_id == user.id)
    suppression = query.first()
    if not suppression:
        raise HTTPException(status_code=404, detail="Suppression not found")

    db.delete(suppression)
    db.commit()
    return {"ok": True}


def _apply_unsubscribe_token(token: str, db: Session) -> None:
    try:
        lead_id, email = verify_unsubscribe_token(token)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid unsubscribe link")

    lead = db.query(models.Lead).filter(models.Lead.id == lead_id).first()
    if lead:
        if normalize_email(lead.email) != email:
            raise HTTPException(status_code=400, detail="Invalid unsubscribe link")
        suppress_lead(db, lead, reason="unsubscribe", source="unsubscribe_link")
        owner_id = owner_id_for_lead(db, lead)
        point = None
        if owner_id:
            point = db.query(v2_models.ContactPoint).filter_by(
                owner_id=owner_id,
                channel=Channel.EMAIL,
                normalized_value=email,
            ).first()
        if point:
            idempotency_key = f"unsubscribe-link:lead:{lead.id}:email"
            if not db.query(v2_models.ConsentRestriction).filter_by(idempotency_key=idempotency_key).first():
                db.add(
                    v2_models.ConsentRestriction(
                        owner_id=owner_id,
                        idempotency_key=idempotency_key,
                        scope=RestrictionScope.CONTACT_POINT,
                        channel=Channel.EMAIL,
                        contact_point_id=point.id,
                        reason="unsubscribe",
                        source="unsubscribe_link",
                        metadata_json={"legacy_lead_id": lead.id},
                    )
                )
        lead.last_reply_at = datetime.now(timezone.utc)
        lead.reply_snippet = "Unsubscribed via one-click link."
    else:
        ensure_suppression(db, email=email, reason="unsubscribe", source="unsubscribe_link")

    db.commit()


@router.get("/api/unsubscribe/{token}", response_class=HTMLResponse)
def unsubscribe(token: str):
    # GET is intentionally side-effect free. Link scanners and email security
    # gateways routinely prefetch links; only an explicit POST records consent.
    try:
        verify_unsubscribe_token(token)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid unsubscribe link")
    safe_token = escape(token, quote=True)
    return f"""
    <!doctype html>
    <html lang="en">
      <head>
        <meta charset="utf-8">
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <title>Confirm unsubscribe</title>
      </head>
      <body style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;padding:40px;line-height:1.5;max-width:640px;margin:auto;">
        <main>
          <h1>Confirm unsubscribe</h1>
          <p>This will stop email to this address. It will not block unrelated addresses at the same company.</p>
          <form method="post" action="/api/unsubscribe/{safe_token}">
            <button type="submit" style="min-height:44px;padding:10px 18px;">Unsubscribe this email</button>
          </form>
        </main>
      </body>
    </html>
    """


@router.post("/api/unsubscribe/{token}")
def unsubscribe_one_click(token: str, db: Session = Depends(get_db)):
    _apply_unsubscribe_token(token, db)
    return {"ok": True}


def _apply_v2_unsubscribe_token(token: str, db: Session) -> None:
    try:
        owner_id, point_id, identity_hash = verify_v2_unsubscribe_token(token)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid unsubscribe link")
    point = db.query(v2_models.ContactPoint).filter_by(
        id=point_id,
        owner_id=owner_id,
        channel=Channel.EMAIL,
    ).first()
    if point is None or not hmac.compare_digest(point.normalized_value_hash, identity_hash):
        raise HTTPException(status_code=400, detail="Invalid unsubscribe link")

    idempotency_key = f"unsubscribe-link:v2:contact-point:{point.id}"
    restriction = db.query(v2_models.ConsentRestriction).filter_by(
        idempotency_key=idempotency_key
    ).first()
    if restriction is None:
        db.add(
            v2_models.ConsentRestriction(
                owner_id=owner_id,
                idempotency_key=idempotency_key,
                scope=RestrictionScope.CONTACT_POINT,
                channel=Channel.EMAIL,
                contact_point_id=point.id,
                reason="unsubscribe",
                source="v2_unsubscribe_link",
                metadata_json={"contact_point_id": point.id},
            )
        )
    # Keep the legacy read fallback safe during cutover without widening the
    # recipient's request to every address at the same domain.
    ensure_suppression(
        db,
        email=point.normalized_value,
        user_id=owner_id,
        reason="unsubscribe",
        source="v2_unsubscribe_link",
    )
    db.commit()


@router.get("/api/unsubscribe/v2/{token}", response_class=HTMLResponse)
def v2_unsubscribe(token: str):
    try:
        verify_v2_unsubscribe_token(token)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid unsubscribe link")
    safe_token = escape(token, quote=True)
    return f"""
    <!doctype html>
    <html lang="en">
      <head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>Confirm unsubscribe</title></head>
      <body style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;padding:40px;line-height:1.5;max-width:640px;margin:auto;">
        <main>
          <h1>Confirm unsubscribe</h1>
          <p>This stops email to this address only.</p>
          <form method="post" action="/api/unsubscribe/v2/{safe_token}">
            <button type="submit" style="min-height:44px;padding:10px 18px;">Unsubscribe this email</button>
          </form>
        </main>
      </body>
    </html>
    """


@router.post("/api/unsubscribe/v2/{token}")
def v2_unsubscribe_one_click(token: str, db: Session = Depends(get_db)):
    _apply_v2_unsubscribe_token(token, db)
    return {"ok": True}
