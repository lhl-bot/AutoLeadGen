"""Persistent external-provider usage events used for ROI reporting."""
import logging
from typing import Any, Optional

from sqlalchemy.orm import Session

import models


logger = logging.getLogger("provider_usage")


def record_provider_usage(
    db: Session,
    *,
    provider: str,
    operation: str,
    status: str,
    workflow_id: Optional[int] = None,
    lead_id: Optional[int] = None,
    units: int = 1,
    estimated_credits: Optional[int] = None,
    result_count: int = 0,
    metadata: Optional[dict[str, Any]] = None,
    commit: bool = True,
) -> models.ProviderUsageEvent:
    event = models.ProviderUsageEvent(
        provider=provider,
        operation=operation,
        workflow_id=workflow_id,
        lead_id=lead_id,
        status=status,
        units=max(0, int(units or 0)),
        estimated_credits=estimated_credits,
        result_count=max(0, int(result_count or 0)),
        metadata_json=metadata or None,
    )
    db.add(event)
    if commit:
        db.commit()
    return event


def safe_record_provider_usage(**kwargs) -> None:
    """Best-effort event logging that must never break the provider workflow."""
    from database import SessionLocal

    db = SessionLocal()
    try:
        record_provider_usage(db, **kwargs)
    except Exception as exc:
        db.rollback()
        logger.warning("Unable to record provider usage: %s", exc)
    finally:
        db.close()
