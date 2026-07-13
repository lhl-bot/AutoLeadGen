from datetime import datetime, timezone

import models
from routers import analytics


def test_dashboard_reply_stats_follow_durable_reply_milestone(db_session):
    analytics._dashboard_cache.clear()

    user = models.User(username="owner", hashed_password="x", is_active=True, is_admin=False)
    db_session.add(user)
    db_session.flush()
    workflow = models.Workflow(
        user_id=user.id,
        name="WF",
        status="active",
        search_keywords="textiles",
        target_positions="buyer",
    )
    db_session.add(workflow)
    db_session.flush()

    replied = models.Lead(
        workflow_id=workflow.id,
        company_name="Replied Co",
        email="replied@example.com",
        status="sent",
        has_replied=True,
        reply_intent="interested",
        last_reply_at=datetime.now(timezone.utc),
        reply_snippet="Interested.",
    )
    sent_with_inbound_log = models.Lead(
        workflow_id=workflow.id,
        company_name="Logged Co",
        email="logged@example.com",
        status="sent",
    )
    found_with_inbound_log = models.Lead(
        workflow_id=workflow.id,
        company_name="Noise Co",
        email="noise@example.com",
        status="found",
    )
    db_session.add_all([replied, sent_with_inbound_log, found_with_inbound_log])
    db_session.flush()
    db_session.add_all([
        models.EmailLog(
            lead_id=sent_with_inbound_log.id,
            direction="inbound",
            from_email="logged@example.com",
            to_email="owner@example.com",
            sent_at=datetime.now(timezone.utc),
        ),
        models.EmailLog(
            lead_id=found_with_inbound_log.id,
            direction="inbound",
            from_email="noise@example.com",
            to_email="owner@example.com",
            sent_at=datetime.now(timezone.utc),
        ),
    ])
    db_session.commit()

    result = analytics.get_dashboard_stats(db_session, user)

    assert result["kpis"]["total_replies"] == 1
    assert result["today_report"]["high_intent_replies"] == 1
    assert result["today_report"]["top_leads"] == [
        {
            "id": replied.id,
            "company_name": "Replied Co",
            "email": "replied@example.com",
            "reply_snippet": "Interested.",
        }
    ]
