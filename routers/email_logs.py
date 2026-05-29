from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from sqlalchemy import func, case, desc
from typing import List, Optional
from datetime import datetime, timezone, timedelta
import models
import schemas
from database import get_db
from services.auth import get_current_user

router = APIRouter(prefix="/api", tags=["email_logs"])


@router.get("/email_logs")
def list_email_logs(
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    direction: Optional[str] = None,
    workflow_id: Optional[int] = Query(None),
    status: Optional[str] = Query(None),
    include_body: bool = Query(False),
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
):
    """List email logs with lead info. Supports pagination and filtering."""
    q = (
        db.query(
            models.EmailLog.id,
            models.EmailLog.direction,
            models.EmailLog.from_email,
            models.EmailLog.to_email,
            models.EmailLog.subject,
            models.EmailLog.body,
            models.EmailLog.sent_at,
            models.EmailLog.message_id,
            models.Lead.company_name.label("lead_company"),
            (models.Lead.first_name + " " + models.Lead.last_name).label("lead_name"),
            models.Lead.status.label("lead_status"),
        )
        .join(models.Lead, models.EmailLog.lead_id == models.Lead.id)
    )

    # Filter by user ownership (admin sees all)
    if not user.is_admin:
        q = q.join(models.Workflow, models.Lead.workflow_id == models.Workflow.id).filter(
            models.Workflow.user_id == user.id
        )

    if direction:
        q = q.filter(models.EmailLog.direction == direction)
    if workflow_id:
        q = q.filter(models.Lead.workflow_id == workflow_id)
    if status:
        q = q.filter(models.Lead.status == status)

    logs = q.order_by(desc(models.EmailLog.sent_at)).offset(offset).limit(limit).all()

    return [
        {
            "id": log.id,
            "direction": log.direction,
            "from_email": log.from_email,
            "to_email": log.to_email,
            "subject": log.subject,
            "body": log.body if include_body else None,
            "sent_at": log.sent_at.isoformat() if log.sent_at else None,
            "message_id": log.message_id,
            "lead_company": log.lead_company,
            "lead_name": log.lead_name,
            "lead_status": log.lead_status,
        }
        for log in logs
    ]


@router.get("/deliverability/summary")
def deliverability_summary(
    days: int = Query(30, ge=1, le=365),
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
):
    """Return deliverability metrics for the last N days."""
    since = datetime.now(timezone.utc) - timedelta(days=days)

    # Base query: outbound logs within time range
    base_q = (
        db.query(models.EmailLog, models.Lead)
        .join(models.Lead, models.EmailLog.lead_id == models.Lead.id)
    )
    if not user.is_admin:
        base_q = base_q.join(models.Workflow, models.Lead.workflow_id == models.Workflow.id).filter(
            models.Workflow.user_id == user.id
        )

    outbound_logs = base_q.filter(
        models.EmailLog.direction == "outbound",
        models.EmailLog.sent_at >= since,
    ).all()

    outbound_count = len(outbound_logs)

    # Status counts from leads that received outbound emails
    lead_ids = {log.Lead.id for log in outbound_logs}
    status_counts = {}
    if lead_ids:
        status_rows = (
            db.query(models.Lead.status, func.count(models.Lead.id))
            .filter(models.Lead.id.in_(lead_ids))
            .group_by(models.Lead.status)
            .all()
        )
        status_counts = {row[0]: row[1] for row in status_rows}

    # Risk domains: domains with high failure rates
    domain_stats = {}
    for log in outbound_logs:
        to_email = (log.EmailLog.to_email or "").lower()
        domain = to_email.split("@")[-1] if "@" in to_email else ""
        if not domain:
            continue
        if domain not in domain_stats:
            domain_stats[domain] = {"sent": 0, "failures": 0}
        domain_stats[domain]["sent"] += 1
        if log.Lead.status in ("bounced", "send_failed", "invalid_email"):
            domain_stats[domain]["failures"] += 1

    risk_domains = sorted(
        [
            {"domain": d, "failures": s["failures"], "sent": s["sent"]}
            for d, s in domain_stats.items()
            if s["failures"] > 0 and s["sent"] >= 2
        ],
        key=lambda x: -x["failures"],
    )[:10]

    return {
        "status_counts": status_counts,
        "outbound_count": outbound_count,
        "risk_domains": risk_domains,
    }
