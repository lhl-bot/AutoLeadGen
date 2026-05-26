from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from sqlalchemy import func, or_
from datetime import datetime, timedelta, timezone

import models
from database import get_db
from services.auth import get_current_user

router = APIRouter(prefix="/api/analytics", tags=["analytics"])

@router.get("/dashboard")
def get_dashboard_stats(db: Session = Depends(get_db), user: models.User = Depends(get_current_user)):
    """
    Returns aggregated KPIs and a 14-day timeseries for the dashboard charts.
    """
    # 1. Total Active Workflows
    wf_query = db.query(models.Workflow)
    if not user.is_admin:
        wf_query = wf_query.filter(models.Workflow.user_id == user.id)
    active_workflows = wf_query.filter(models.Workflow.status == "active").count()

    # Base query filters for isolation
    if not user.is_admin:
        user_wf_ids = [w.id for w in wf_query.all()]
        if not user_wf_ids:
            return {
                "kpis": {
                    "active_workflows": 0,
                    "total_leads": 0,
                    "emails_sent": 0,
                    "total_replies": 0,
                },
                "trends": []
            }
        lead_filter = models.Lead.workflow_id.in_(user_wf_ids)
        # Email logs filtered by user's leads
        user_lead_ids = [l.id for l in db.query(models.Lead.id).filter(lead_filter).all()]
        email_filter = models.EmailLog.lead_id.in_(user_lead_ids) if user_lead_ids else False
    else:
        lead_filter = True
        email_filter = True

    # 2. Total Leads Sourced
    total_leads = db.query(models.Lead).filter(lead_filter).count()

    # 3. Total Messages Sent
    emails_sent = 0
    if isinstance(email_filter, bool) and email_filter is False:
        pass
    else:
        emails_sent = db.query(models.EmailLog).filter(
            models.EmailLog.direction == "outbound",
            email_filter
        ).count()

    # 4. Total Replies
    total_replies = db.query(models.Lead).filter(
        models.Lead.status == "replied",
        lead_filter
    ).count()

    # 5. Timeseries (Last 14 days)
    # Generate dates list
    now = datetime.now(timezone.utc)
    dates = [(now - timedelta(days=i)).date() for i in range(13, -1, -1)]
    
    # We will compute counts directly in memory for simplicity (sqlite/mysql cross-compat)
    # Get all leads in last 14 days
    fourteen_days_ago = now - timedelta(days=14)
    
    recent_leads = db.query(models.Lead.created_at).filter(
        models.Lead.created_at >= fourteen_days_ago,
        lead_filter
    ).all()
    
    recent_emails = []
    if not (isinstance(email_filter, bool) and email_filter is False):
        recent_emails = db.query(models.EmailLog.sent_at).filter(
            models.EmailLog.sent_at >= fourteen_days_ago,
            models.EmailLog.direction == "outbound",
            email_filter
        ).all()
    
    # Aggregate into dictionaries by date string
    lead_counts_by_date = {}
    for (created_at,) in recent_leads:
        if created_at:
            d_str = created_at.date().isoformat()
            lead_counts_by_date[d_str] = lead_counts_by_date.get(d_str, 0) + 1
            
    email_counts_by_date = {}
    for (sent_at,) in recent_emails:
        if sent_at:
            d_str = sent_at.date().isoformat()
            email_counts_by_date[d_str] = email_counts_by_date.get(d_str, 0) + 1

    trends = []
    for d in dates:
        d_str = d.isoformat()
        trends.append({
            "date": d.strftime("%m/%d"),
            "leads_found": lead_counts_by_date.get(d_str, 0),
            "emails_sent": email_counts_by_date.get(d_str, 0)
        })

    # ── Today's AI Work Report ──
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)

    leads_found_today = db.query(models.Lead).filter(
        models.Lead.created_at >= today_start,
        lead_filter
    ).count()

    emails_sent_today = 0
    if not (isinstance(email_filter, bool) and email_filter is False):
        emails_sent_today = db.query(models.EmailLog).filter(
            models.EmailLog.sent_at >= today_start,
            models.EmailLog.direction == "outbound",
            email_filter
        ).count()

    high_intent_replies = db.query(models.Lead).filter(
        lead_filter,
        models.Lead.status == "replied",
        models.Lead.last_reply_at >= today_start
    ).count()

    # Top 3 most recently replied leads for quick action
    top_leads_raw = db.query(models.Lead).filter(
        models.Lead.status == "replied",
        lead_filter
    ).order_by(models.Lead.last_reply_at.desc()).limit(3).all()

    top_leads = [
        {
            "id": lead.id,
            "company_name": lead.company_name or lead.domain,
            "email": lead.email,
            "reply_snippet": (lead.reply_snippet or "")[:100],
        }
        for lead in top_leads_raw
    ]

    # Active workflow names
    active_wf_names = [
        wf.name for wf in db.query(models.Workflow.name).filter(
            models.Workflow.status == "active",
            models.Workflow.user_id == user.id if not user.is_admin else True
        ).all()
    ]

    return {
        "kpis": {
            "active_workflows": active_workflows,
            "total_leads": total_leads,
            "emails_sent": emails_sent,
            "total_replies": total_replies,
        },
        "trends": trends,
        "today_report": {
            "leads_found_today": leads_found_today,
            "emails_sent_today": emails_sent_today,
            "high_intent_replies": high_intent_replies,
            "top_leads": top_leads,
            "active_workflow_names": active_wf_names,
        }
    }
