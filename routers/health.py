from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from sqlalchemy import text
import os

import models
from database import get_db
from services.auth import get_current_user

router = APIRouter(prefix="/api/health", tags=["health"])


@router.get("/status")
def system_health(db: Session = Depends(get_db), user: models.User = Depends(get_current_user)):
    """Return real health status of system components."""
    # DB connectivity
    try:
        db.execute(text("SELECT 1"))
        db_ok = True
    except Exception:
        db_ok = False

    # Outbound engine: check if there are active workflows with recent activity
    from datetime import datetime, timedelta, timezone
    active_workflows = db.query(models.Workflow).filter(
        models.Workflow.status == "active"
    ).count()

    # Check if there was any engine activity in the last 30 minutes
    recent_cutoff = datetime.now(timezone.utc) - timedelta(minutes=30)
    recent_emails = db.query(models.EmailLog).filter(
        models.EmailLog.sent_at >= recent_cutoff
    ).count()

    # Check Unipile connectivity
    unipile_connected = db.query(models.ChannelAccount).filter(
        models.ChannelAccount.status == "OK"
    ).count()

    # Check email accounts
    total_email_accounts = db.query(models.EmailAccount).count()
    has_active_email = total_email_accounts > 0

    # Check LLM API key
    llm_key_set = bool(os.environ.get("LLM_API_KEY"))

    return {
        "database": "online" if db_ok else "error",
        "outbound_engine": "active" if active_workflows > 0 and recent_emails > 0 else ("idle" if active_workflows > 0 else "inactive"),
        "active_workflows": active_workflows,
        "recent_emails_30m": recent_emails,
        "unipile_accounts": unipile_connected,
        "email_accounts": total_email_accounts,
        "has_active_email": has_active_email,
        "llm_api_configured": llm_key_set,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
