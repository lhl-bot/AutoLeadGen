from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from sqlalchemy import or_
from sqlalchemy.orm import Session

import models
from database import get_db
from services.auth import get_current_user
from services.suppression import (
    ensure_suppression,
    normalize_domain,
    normalize_email,
    suppress_lead,
    verify_unsubscribe_token,
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
    domain = normalize_domain(payload.domain or email)
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


def _unsubscribe_token(token: str, db: Session) -> None:
    try:
        lead_id, email = verify_unsubscribe_token(token)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid unsubscribe link")

    lead = db.query(models.Lead).filter(models.Lead.id == lead_id).first()
    if lead:
        if normalize_email(lead.email) != email:
            raise HTTPException(status_code=400, detail="Invalid unsubscribe link")
        suppress_lead(db, lead, reason="unsubscribe", source="unsubscribe_link")
        lead.last_reply_at = datetime.now(timezone.utc)
        lead.reply_snippet = "Unsubscribed via one-click link."
    else:
        ensure_suppression(db, email=email, reason="unsubscribe", source="unsubscribe_link")

    db.commit()


@router.get("/api/unsubscribe/{token}", response_class=HTMLResponse)
def unsubscribe(token: str, db: Session = Depends(get_db)):
    _unsubscribe_token(token, db)
    return """
    <!doctype html>
    <html lang="en">
      <head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"></head>
      <body style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;padding:40px;line-height:1.5;">
        <h1>You have been unsubscribed</h1>
        <p>This address has been added to the do-not-contact list.</p>
      </body>
    </html>
    """


@router.post("/api/unsubscribe/{token}")
def unsubscribe_one_click(token: str, db: Session = Depends(get_db)):
    _unsubscribe_token(token, db)
    return {"ok": True}
