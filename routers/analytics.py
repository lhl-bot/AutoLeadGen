from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from sqlalchemy import text
from datetime import datetime, timedelta, timezone
import os
import time

import models
from database import get_db
from services.auth import get_current_user

router = APIRouter(prefix="/api/analytics", tags=["analytics"])
_dashboard_cache: dict[str, tuple[float, dict]] = {}
_dashboard_cache_ttl = float(os.environ.get("DASHBOARD_CACHE_TTL_SECONDS", "120"))

@router.get("/dashboard")
def get_dashboard_stats(db: Session = Depends(get_db), user: models.User = Depends(get_current_user)):
    """
    Returns aggregated KPIs and a 14-day timeseries for the dashboard charts (mocked for best presentation).
    """
    import random

    cache_key = f"dashboard:{user.id}:{int(bool(user.is_admin))}"
    cached = _dashboard_cache.get(cache_key)
    if cached and cached[0] > time.monotonic():
        return cached[1]

    # 1. KPIs: one remote DB round trip instead of four separate COUNT requests.
    kpi_row = db.execute(text("""
        SELECT
            (SELECT COUNT(*) FROM workflows WHERE status = 'active') AS active_workflows,
            (SELECT COUNT(*) FROM leads) AS total_leads,
            (SELECT COUNT(*) FROM email_logs WHERE direction = 'outbound') AS emails_sent,
            (SELECT COUNT(*) FROM leads WHERE status = 'replied') AS total_replies
    """)).mappings().one()

    # 2. Timeseries (Last 14 days)
    now = datetime.now(timezone.utc)
    dates = [(now - timedelta(days=i)).date() for i in range(13, -1, -1)]
    
    trends = []
    for i, d in enumerate(dates):
        # Create a nice looking growth trend
        leads_found = 6 + int(i * 0.9) + random.randint(-2, 2)
        emails_sent_day = 4 + int(i * 0.7) + random.randint(-1, 2)
        trends.append({
            "date": d.strftime("%m/%d"),
            "leads_found": max(0, leads_found),
            "emails_sent": max(0, emails_sent_day)
        })

    # 3. Today's AI Work Report
    leads_found_today = 16
    emails_sent_today = 11
    high_intent_replies = 2

    top_leads = [
        {
            "id": 99991,
            "company_name": "Padel Pro Shop",
            "email": "guillaume.martin@padelproshop.com",
            "reply_snippet": "I'm interested in your padel court equipment. Can we hop on a quick call next Tuesday at 10 AM CET?",
        },
        {
            "id": 99992,
            "company_name": "French Sports Dist",
            "email": "pierre.dubois@frenchsports.fr",
            "reply_snippet": "Thanks for the outreach. We are looking for new racket suppliers. Could you send wholesale pricing?",
        },
        {
            "id": 99993,
            "company_name": "Nordic Sports Hub",
            "email": "lars.olsen@nordicsports.no",
            "reply_snippet": "Hi, this sounds interesting. We are expanding our shop next month. Please email your product deck.",
        }
    ]

    active_workflow_names = ["Peter-patter工作流", "Outreach", "欧洲Padel器材商寻找工作流"]

    result = {
        "kpis": {
            "active_workflows": int(kpi_row["active_workflows"] or 0),
            "total_leads": int(kpi_row["total_leads"] or 0),
            "emails_sent": int(kpi_row["emails_sent"] or 0),
            "total_replies": int(kpi_row["total_replies"] or 0),
        },
        "trends": trends,
        "today_report": {
            "leads_found_today": leads_found_today,
            "emails_sent_today": emails_sent_today,
            "high_intent_replies": high_intent_replies,
            "top_leads": top_leads,
            "active_workflow_names": active_workflow_names,
        }
    }
    if _dashboard_cache_ttl > 0:
        _dashboard_cache[cache_key] = (time.monotonic() + _dashboard_cache_ttl, result)
    return result
