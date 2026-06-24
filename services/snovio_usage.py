import logging
import os
from typing import Any, Dict, Optional

from database import SessionLocal
from models import SnovioUsageEvent

logger = logging.getLogger(__name__)

FALSE_ENV_VALUES = {"0", "false", "no", "off"}


def _audit_enabled() -> bool:
    raw = os.environ.get("SNOVIO_USAGE_AUDIT_ENABLED", "true")
    return raw.strip().lower() not in FALSE_ENV_VALUES


def record_snovio_usage(
    *,
    endpoint: str,
    domain: Optional[str] = None,
    email: Optional[str] = None,
    status: Optional[str] = None,
    result_count: int = 0,
    estimated_credits: Optional[int] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> None:
    """Persist a best-effort audit event for Snov.io API usage.

    Snov.io's exact billing is provider-side, so estimated_credits is a local
    approximation used for cost visibility rather than accounting.
    """
    if not _audit_enabled():
        return

    db = SessionLocal()
    try:
        db.add(SnovioUsageEvent(
            endpoint=endpoint[:120],
            domain=(domain or None),
            email=(email or None),
            status=(status or None),
            result_count=max(0, int(result_count or 0)),
            estimated_credits=estimated_credits,
            metadata_json=metadata or {},
        ))
        db.commit()
    except Exception as e:
        db.rollback()
        logger.warning(f"Failed to record Snov.io usage event: {e}")
    finally:
        db.close()
