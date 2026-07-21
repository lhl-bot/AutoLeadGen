from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session, Query as OrmQuery
from sqlalchemy import text, or_, and_, func
from datetime import datetime, timedelta, timezone
from typing import Optional
import os
import time

import models
from database import get_db
from services.auth import get_current_user

router = APIRouter(prefix="/api/analytics", tags=["analytics"])

# Statuses that mean a lead has *reached or passed* a given funnel stage.
_DRAFTED_OR_BEYOND = ("drafted", "sent", "replied")
_SENT_OR_BEYOND = ("sent", "replied")


def _lead_scope_condition(db: Session, user: models.User):
    """A SQLAlchemy filter restricting Lead rows to those the user owns, or None for all (admin)."""
    if user.is_admin:
        return None
    wf_ids = [w.id for w in db.query(models.Workflow.id).filter(models.Workflow.user_id == user.id).all()]
    pool_ids = [p.id for p in db.query(models.ClientPool.id).filter(models.ClientPool.user_id == user.id).all()]
    conds = []
    if wf_ids:
        conds.append(models.Lead.workflow_id.in_(wf_ids))
    if pool_ids:
        conds.append(models.Lead.client_pool_id.in_(pool_ids))
    if not conds:
        return models.Lead.id == None  # noqa: E711 — match nothing
    return or_(*conds)


def _scoped_lead_query(db: Session, user: models.User, workflow_id: Optional[int] = None) -> OrmQuery:
    """Base Lead query restricted to leads the user may see (via owned workflows/pools)."""
    query = db.query(models.Lead)
    if workflow_id is not None:
        wf = db.query(models.Workflow).filter(models.Workflow.id == workflow_id).first()
        if not wf or (not user.is_admin and wf.user_id != user.id):
            raise HTTPException(status_code=404, detail="Workflow not found")
        return query.filter(models.Lead.workflow_id == workflow_id)
    if user.is_admin:
        return query
    wf_ids = [w.id for w in db.query(models.Workflow.id).filter(models.Workflow.user_id == user.id).all()]
    pool_ids = [p.id for p in db.query(models.ClientPool.id).filter(models.ClientPool.user_id == user.id).all()]
    conditions = []
    if wf_ids:
        conditions.append(models.Lead.workflow_id.in_(wf_ids))
    if pool_ids:
        conditions.append(models.Lead.client_pool_id.in_(pool_ids))
    if not conditions:
        # No owned workflows/pools → match nothing.
        return query.filter(models.Lead.id == None)  # noqa: E711
    return query.filter(or_(*conditions))
_dashboard_cache: dict[str, tuple[float, dict]] = {}
_dashboard_cache_ttl = float(os.environ.get("DASHBOARD_CACHE_TTL_SECONDS", "120"))

@router.get("/dashboard")
def get_dashboard_stats(db: Session = Depends(get_db), user: models.User = Depends(get_current_user)):
    """Real, per-user dashboard: KPIs, a 14-day timeseries, and today's work report.

    Everything is scoped to the workflows/pools the current user owns (admins see all).
    """
    cache_key = f"dashboard:{user.id}:{int(bool(user.is_admin))}"
    cached = _dashboard_cache.get(cache_key)
    if cached and cached[0] > time.monotonic():
        return cached[1]

    lead_cond = _lead_scope_condition(db, user)

    def lead_q():
        q = db.query(models.Lead)
        return q if lead_cond is None else q.filter(lead_cond)

    def email_q():
        q = db.query(models.EmailLog).join(models.Lead, models.EmailLog.lead_id == models.Lead.id)
        return q if lead_cond is None else q.filter(lead_cond)

    def workflow_q():
        q = db.query(models.Workflow)
        return q if user.is_admin else q.filter(models.Workflow.user_id == user.id)

    now = datetime.now(timezone.utc)
    window_start = (now - timedelta(days=13)).replace(hour=0, minute=0, second=0, microsecond=0)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)

    # 1. KPIs (all user-scoped)
    active_workflows = workflow_q().filter(models.Workflow.status == "active").count()
    total_leads = lead_q().count()
    emails_sent = email_q().filter(models.EmailLog.direction == "outbound").count()
    total_replies = lead_q().filter(or_(
        models.Lead.has_replied.is_(True),
        models.Lead.status == "replied",
    )).count()

    # 2. Timeseries (last 14 days), aggregated in SQL — no full-table load.
    dates = [(now - timedelta(days=i)).date() for i in range(13, -1, -1)]

    leads_by_day = {
        str(d)[:10]: int(c)
        for d, c in lead_q()
        .with_entities(func.date(models.Lead.created_at), func.count())
        .filter(models.Lead.created_at >= window_start)
        .group_by(func.date(models.Lead.created_at))
        .all()
    }
    emails_by_day = {
        str(d)[:10]: int(c)
        for d, c in email_q()
        .with_entities(func.date(models.EmailLog.sent_at), func.count())
        .filter(models.EmailLog.direction == "outbound", models.EmailLog.sent_at >= window_start)
        .group_by(func.date(models.EmailLog.sent_at))
        .all()
    }
    trends = [
        {
            "date": d.strftime("%m/%d"),
            "leads_found": leads_by_day.get(d.strftime("%Y-%m-%d"), 0),
            "emails_sent": emails_by_day.get(d.strftime("%Y-%m-%d"), 0),
        }
        for d in dates
    ]

    # 3. Today's work report
    leads_found_today = lead_q().filter(models.Lead.created_at >= today_start).count()
    emails_sent_today = email_q().filter(
        models.EmailLog.direction == "outbound", models.EmailLog.sent_at >= today_start
    ).count()
    high_intent_replies = lead_q().filter(
        or_(
            models.Lead.reply_intent.in_(("interested", "more_info")),
            models.Lead.status == "replied",
        ),
        models.Lead.last_reply_at >= today_start,
    ).count()

    recent_replied = (
        lead_q()
        .filter(
            or_(models.Lead.has_replied.is_(True), models.Lead.status == "replied"),
            models.Lead.last_reply_at.isnot(None),
        )
        .order_by(models.Lead.last_reply_at.desc())
        .limit(3)
        .all()
    )
    top_leads = [
        {
            "id": lead.id,
            "company_name": lead.company_name or lead.domain,
            "email": lead.email,
            "reply_snippet": lead.reply_snippet or "",
        }
        for lead in recent_replied
    ]

    active_workflow_names = [
        w.name for w in workflow_q().filter(models.Workflow.status == "active").limit(10).all()
    ]

    result = {
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
            "active_workflow_names": active_workflow_names,
        },
    }
    if _dashboard_cache_ttl > 0:
        _dashboard_cache[cache_key] = (time.monotonic() + _dashboard_cache_ttl, result)
    return result


@router.get("/funnel")
def get_funnel(
    workflow_id: Optional[int] = Query(None),
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
):
    """Real conversion funnel (cumulative) for the user's leads, optionally one workflow.

    Stages are cumulative: each count includes leads that progressed further, so the
    funnel never widens as it descends.
    """
    base = _scoped_lead_query(db, user, workflow_id)

    found = base.count()
    with_email = base.filter(models.Lead.email.isnot(None), models.Lead.email != "").count()
    drafted = base.filter(models.Lead.status.in_(_DRAFTED_OR_BEYOND)).count()
    sent = base.filter(models.Lead.email_logs.any(models.EmailLog.direction == "outbound")).count()
    replied = base.filter(or_(
        models.Lead.has_replied.is_(True),
        models.Lead.status == "replied",
    )).count()

    return {
        "stages": [
            {"key": "found", "count": found},
            {"key": "with_email", "count": with_email},
            {"key": "drafted", "count": drafted},
            {"key": "sent", "count": sent},
            {"key": "replied", "count": replied},
        ],
        "reply_rate": round(replied / sent, 4) if sent else 0.0,
    }


# Map a lead status to the kind of activity event it represents.
_STATUS_EVENT = {
    "found": "found",
    "needs_email": "found",
    "drafted": "drafted",
    "sent": "sent",
    "replied": "replied",
    "send_failed": "send_failed",
    "rejected": "rejected",
    "unsubscribed": "unsubscribed",
}


@router.get("/activity")
def get_activity(
    limit: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
):
    """Recent lead activity (most recently updated leads) for a live activity feed."""
    base = _scoped_lead_query(db, user)
    leads = (
        base.order_by(models.Lead.updated_at.desc())
        .limit(limit)
        .all()
    )
    items = []
    for lead in leads:
        name = f"{lead.first_name or ''} {lead.last_name or ''}".strip()
        items.append({
            "lead_id": lead.id,
            "event": _STATUS_EVENT.get(lead.status, "updated"),
            "status": lead.status,
            "title": name or lead.company_name or lead.domain or f"#{lead.id}",
            "company": lead.company_name or lead.domain,
            "workflow_id": lead.workflow_id,
            "at": (lead.updated_at or lead.created_at).isoformat() if (lead.updated_at or lead.created_at) else None,
        })
    return {"items": items}


@router.get("/onboarding")
def get_onboarding(
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
):
    """First-run setup progress for the onboarding checklist.

    Each step reports whether the user has completed it and a count, so the
    dashboard can guide a new user from an empty account to their first
    reviewed batch of emails. Admins see global counts.
    """
    def owned_count(model) -> int:
        q = db.query(func.count(model.id))
        if not user.is_admin:
            q = q.filter(model.user_id == user.id)
        return int(q.scalar() or 0)

    personas = owned_count(models.CustomerPersona)
    emails = owned_count(models.EmailAccount)
    pools = owned_count(models.ClientPool)
    workflows = owned_count(models.Workflow)

    lead_scope = _lead_scope_condition(db, user)
    leads_q = db.query(func.count(models.Lead.id))
    reviewed_q = db.query(func.count(models.Lead.id)).filter(
        models.Lead.status.in_(_DRAFTED_OR_BEYOND)
    )
    if lead_scope is not None:
        leads_q = leads_q.filter(lead_scope)
        reviewed_q = reviewed_q.filter(lead_scope)
    leads_found = int(leads_q.scalar() or 0)
    drafted_or_sent = int(reviewed_q.scalar() or 0)

    steps = [
        {"key": "persona", "done": personas > 0, "count": personas},
        {"key": "email", "done": emails > 0, "count": emails},
        {"key": "pool", "done": pools > 0, "count": pools},
        {"key": "workflow", "done": workflows > 0, "count": workflows},
        {"key": "leads", "done": leads_found > 0, "count": leads_found},
        {"key": "review", "done": drafted_or_sent > 0, "count": drafted_or_sent},
    ]
    completed = sum(1 for s in steps if s["done"])
    return {
        "steps": steps,
        "completed": completed,
        "total": len(steps),
        "all_done": completed == len(steps),
    }
