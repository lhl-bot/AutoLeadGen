"""Create and resolve in-app notifications.

``notify`` is dedup-aware: it will not create a second *unread* notification with the
same (user, type, reference) inside a cooldown window, so a flurry of send failures
or repeated low-balance checks won't spam the bell.
"""
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy.orm import Session

import models

logger = logging.getLogger(__name__)

DEFAULT_DEDUP_MINUTES = 360  # 6 hours


def owner_id_for_lead(db: Session, lead: models.Lead) -> Optional[int]:
    """Resolve the user who owns a lead via its workflow or client pool."""
    if lead.workflow_id:
        wf = db.query(models.Workflow.user_id).filter(models.Workflow.id == lead.workflow_id).first()
        if wf:
            return wf[0]
    if lead.client_pool_id:
        pool = db.query(models.ClientPool.user_id).filter(models.ClientPool.id == lead.client_pool_id).first()
        if pool:
            return pool[0]
    return None


def notify(
    db: Session,
    user_id: Optional[int],
    type: str,
    title: str,
    *,
    body: Optional[str] = None,
    link: Optional[str] = None,
    reference_type: Optional[str] = None,
    reference_id: Optional[int] = None,
    dedup_minutes: int = DEFAULT_DEDUP_MINUTES,
    commit: bool = True,
) -> Optional[models.Notification]:
    """Create a notification unless an equivalent unread one exists in the window.

    Never raises — notification delivery must not break the calling business logic.
    """
    if not user_id:
        return None
    try:
        if dedup_minutes > 0:
            cutoff = datetime.now(timezone.utc) - timedelta(minutes=dedup_minutes)
            existing = (
                db.query(models.Notification)
                .filter(
                    models.Notification.user_id == user_id,
                    models.Notification.type == type,
                    models.Notification.is_read.is_(False),
                    models.Notification.reference_type == reference_type,
                    models.Notification.reference_id == reference_id,
                    models.Notification.created_at >= cutoff,
                )
                .first()
            )
            if existing:
                return existing

        notification = models.Notification(
            user_id=user_id,
            type=type,
            title=title,
            body=body,
            link=link,
            reference_type=reference_type,
            reference_id=reference_id,
        )
        db.add(notification)
        if commit:
            db.commit()
            db.refresh(notification)
        return notification
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning(f"Failed to create notification ({type}) for user {user_id}: {exc}")
        try:
            db.rollback()
        except Exception:
            pass
        return None
