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

    base_filters = [
        models.EmailLog.direction == "outbound",
        models.EmailLog.sent_at >= since,
    ]

    outbound_q = (
        db.query(models.EmailLog)
        .join(models.Lead, models.EmailLog.lead_id == models.Lead.id)
    )
    if not user.is_admin:
        outbound_q = outbound_q.join(
            models.Workflow,
            models.Lead.workflow_id == models.Workflow.id,
        ).filter(models.Workflow.user_id == user.id)

    outbound_q = outbound_q.filter(*base_filters)
    outbound_count = outbound_q.with_entities(func.count(models.EmailLog.id)).scalar() or 0

    contacted_leads = outbound_q.with_entities(
        models.Lead.id.label("lead_id"),
        models.Lead.status.label("status"),
    ).distinct().subquery()
    status_rows = (
        db.query(contacted_leads.c.status, func.count(contacted_leads.c.lead_id))
        .group_by(contacted_leads.c.status)
        .all()
    )
    status_counts = {status: count for status, count in status_rows}

    domain_expr = func.lower(
        func.substr(
            models.EmailLog.to_email,
            func.instr(models.EmailLog.to_email, "@") + 1,
        )
    )
    risk_rows = (
        outbound_q.with_entities(
            domain_expr.label("domain"),
            func.count(models.EmailLog.id).label("sent"),
            func.sum(case(
                (models.Lead.status.in_(("bounced", "send_failed", "invalid_email")), 1),
                else_=0,
            )).label("failures"),
        )
        .filter(func.instr(models.EmailLog.to_email, "@") > 0)
        .group_by(domain_expr)
        .having(
            func.count(models.EmailLog.id) >= 2,
            func.sum(case(
                (models.Lead.status.in_(("bounced", "send_failed", "invalid_email")), 1),
                else_=0,
            )) > 0,
        )
        .order_by(desc("failures"), desc("sent"))
        .limit(10)
        .all()
    )
    risk_domains = [
        {
            "domain": row.domain,
            "failures": int(row.failures or 0),
            "sent": int(row.sent or 0),
        }
        for row in risk_rows
    ]

    return {
        "status_counts": status_counts,
        "outbound_count": outbound_count,
        "risk_domains": risk_domains,
    }
