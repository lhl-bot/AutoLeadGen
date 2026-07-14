from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from typing import List

import models, schemas
from database import get_db
from services.auth import get_current_user

router = APIRouter(prefix="/api/notifications", tags=["notifications"])


def _scoped(db: Session, user: models.User):
    query = db.query(models.Notification)
    if not user.is_admin:
        query = query.filter(models.Notification.user_id == user.id)
    return query


@router.get("/", response_model=schemas.NotificationList)
def list_notifications(
    limit: int = Query(30, ge=1, le=100),
    unread_only: bool = Query(False),
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
):
    base = _scoped(db, user)
    unread_count = base.filter(models.Notification.is_read.is_(False)).count()
    items_q = _scoped(db, user)
    if unread_only:
        items_q = items_q.filter(models.Notification.is_read.is_(False))
    items = items_q.order_by(models.Notification.created_at.desc()).limit(limit).all()
    return {"unread_count": unread_count, "items": items}


@router.post("/{notification_id}/read", response_model=schemas.Notification)
def mark_read(notification_id: int, db: Session = Depends(get_db), user: models.User = Depends(get_current_user)):
    notification = _scoped(db, user).filter(models.Notification.id == notification_id).first()
    if not notification:
        raise HTTPException(status_code=404, detail="Notification not found")
    notification.is_read = True
    db.commit()
    db.refresh(notification)
    return notification


@router.post("/read-all")
def mark_all_read(db: Session = Depends(get_db), user: models.User = Depends(get_current_user)):
    updated = (
        _scoped(db, user)
        .filter(models.Notification.is_read.is_(False))
        .update({models.Notification.is_read: True}, synchronize_session=False)
    )
    db.commit()
    return {"marked_read": int(updated or 0)}
