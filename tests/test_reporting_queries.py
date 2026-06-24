from datetime import datetime, timezone

import models
from routers.client_pools import read_pool_leads
from routers.email_logs import deliverability_summary
from routers.workflows import get_workflow_pilot_report


def _seed_user_workflow_pool(db):
    user = models.User(username="owner", hashed_password="x", is_active=True)
    db.add(user)
    db.flush()
    pool = models.ClientPool(user_id=user.id, name="Pool")
    workflow = models.Workflow(
        user_id=user.id,
        client_pool=pool,
        name="Workflow",
        search_keywords="distributor",
        target_positions="buyer",
    )
    db.add_all([pool, workflow])
    db.flush()
    return user, workflow, pool


def test_deliverability_summary_aggregates_status_and_risk_domains(db_session):
    user, workflow, pool = _seed_user_workflow_pool(db_session)
    leads = [
        models.Lead(
            workflow_id=workflow.id,
            client_pool_id=pool.id,
            domain="good.example",
            email="a@good.example",
            status="sent",
        ),
        models.Lead(
            workflow_id=workflow.id,
            client_pool_id=pool.id,
            domain="risk.example",
            email="b@risk.example",
            status="bounced",
        ),
    ]
    db_session.add_all(leads)
    db_session.flush()
    db_session.add_all([
        models.EmailLog(
            lead_id=leads[0].id,
            direction="outbound",
            from_email="sender@example.com",
            to_email="a@good.example",
            sent_at=datetime.now(timezone.utc),
        ),
        models.EmailLog(
            lead_id=leads[1].id,
            direction="outbound",
            from_email="sender@example.com",
            to_email="b@risk.example",
            sent_at=datetime.now(timezone.utc),
        ),
        models.EmailLog(
            lead_id=leads[1].id,
            direction="outbound",
            from_email="sender@example.com",
            to_email="c@risk.example",
            sent_at=datetime.now(timezone.utc),
        ),
    ])
    db_session.commit()

    result = deliverability_summary(30, db_session, user)

    assert result["outbound_count"] == 3
    assert result["status_counts"] == {"bounced": 1, "sent": 1}
    assert result["risk_domains"] == [
        {"domain": "risk.example", "failures": 2, "sent": 2}
    ]


def test_pilot_report_uses_aggregated_metrics(db_session):
    user, workflow, pool = _seed_user_workflow_pool(db_session)
    db_session.add_all([
        models.Lead(
            workflow_id=workflow.id,
            client_pool_id=pool.id,
            domain="one.example",
            email="one@example.com",
            email_verified=True,
            fit_score=80,
            status="replied",
            handoff_recommended=True,
            source_channel="apollo",
        ),
        models.Lead(
            workflow_id=workflow.id,
            client_pool_id=pool.id,
            domain="two.example",
            email="two@example.com",
            email_validation_status="invalid",
            fit_score=40,
            user_rating="positive",
            status="sent",
            source_channel="apollo",
        ),
        models.Lead(
            workflow_id=workflow.id,
            client_pool_id=pool.id,
            domain="three.example",
            status="found",
            handoff_recommended=True,
            source_channel=None,
        ),
    ])
    db_session.commit()

    report = get_workflow_pilot_report(workflow.id, db_session, user)

    assert report.leads_total == 3
    assert report.matched_leads == 2
    assert report.match_rate == 2 / 3
    assert report.email_valid_rate == 0.5
    assert report.reply_rate == 0.5
    assert report.handoff_count == 2
    assert report.high_intent_count == 2
    assert report.avg_fit_score == 60
    assert report.top_channels == ["apollo: 2", "unknown: 1"]


def test_pool_leads_supports_server_side_search_and_pagination(db_session):
    user, workflow, pool = _seed_user_workflow_pool(db_session)
    for index in range(3):
        db_session.add(models.Lead(
            workflow_id=workflow.id,
            client_pool_id=pool.id,
            domain=f"company-{index}.example",
            company_name=f"Company {index}",
            status="found" if index < 2 else "sent",
        ))
    db_session.commit()

    first_page = read_pool_leads(
        pool.id,
        status=None,
        search="Company",
        skip=0,
        limit=2,
        db=db_session,
        user=user,
    )
    sent_only = read_pool_leads(
        pool.id,
        status="sent",
        search=None,
        skip=0,
        limit=50,
        db=db_session,
        user=user,
    )

    assert len(first_page) == 2
    assert len(sent_only) == 1
    assert sent_only[0].company_name == "Company 2"
