"""Verify the new alerting/integration features actually *fire* at their trigger
points (not just that the underlying services work in isolation)."""
from unittest.mock import patch

import pytest

import models
from services.credits import consume_credits, ensure_credit_wallet, InsufficientCreditsError
from routers.leads import set_lead_stage, bulk_lead_action, LeadStageRequest, BulkLeadActionRequest


def _user(db, name="rep"):
    u = models.User(username=name, hashed_password="x", is_active=True)
    db.add(u)
    db.commit()
    db.refresh(u)
    return u


# ---- Notification trigger: low balance on credit exhaustion ----

def test_consume_credits_emits_low_balance_notification(db_session, monkeypatch):
    monkeypatch.setenv("CREDITS_ENABLED", "true")
    user = _user(db_session)
    ensure_credit_wallet(db_session, user.id, initial_balance=0)
    db_session.commit()

    with pytest.raises(InsufficientCreditsError):
        consume_credits(db_session, user.id, "ai_email_draft")

    note = (
        db_session.query(models.Notification)
        .filter_by(user_id=user.id, type="low_balance")
        .first()
    )
    assert note is not None
    assert note.link == "/dashboard/api-usage"


# ---- CRM trigger: stage change dispatches to subscribed webhooks ----

def _pool_lead(db, user):
    pool = models.ClientPool(user_id=user.id, name="P")
    db.add(pool)
    db.flush()
    lead = models.Lead(client_pool_id=pool.id, domain="acme.com", company_name="Acme")
    db.add(lead)
    db.commit()
    db.refresh(lead)
    return lead


def test_single_stage_change_dispatches_webhook(db_session):
    user = _user(db_session)
    lead = _pool_lead(db_session, user)
    db_session.add(models.CrmWebhook(user_id=user.id, name="won", url="https://crm.example/won", events="lead.won", is_active=True))
    db_session.commit()

    with patch("services.crm_webhooks.threading.Thread") as Thread:
        set_lead_stage(lead.id, LeadStageRequest(stage="won"), db=db_session, user=user)
    assert Thread.called  # a delivery thread was scheduled for the won webhook


def test_stage_change_does_not_dispatch_unsubscribed_event(db_session):
    user = _user(db_session)
    lead = _pool_lead(db_session, user)
    db_session.add(models.CrmWebhook(user_id=user.id, name="won-only", url="https://crm.example/won", events="lead.won", is_active=True))
    db_session.commit()

    # Moving to "lost" must not fire the won-only webhook.
    with patch("services.crm_webhooks.threading.Thread") as Thread:
        set_lead_stage(lead.id, LeadStageRequest(stage="lost"), db=db_session, user=user)
    assert not Thread.called


def test_bulk_stage_change_dispatches_per_lead(db_session):
    user = _user(db_session)
    pool = models.ClientPool(user_id=user.id, name="P")
    db_session.add(pool)
    db_session.flush()
    leads = [models.Lead(client_pool_id=pool.id, domain=f"d{i}.example") for i in range(3)]
    db_session.add_all(leads)
    db_session.add(models.CrmWebhook(user_id=user.id, name="won", url="https://crm.example/won", events="lead.won", is_active=True))
    db_session.commit()
    lead_ids = [l.id for l in leads]

    with patch("services.crm_webhooks.threading.Thread") as Thread:
        result = bulk_lead_action(
            BulkLeadActionRequest(lead_ids=lead_ids, action="set_stage", target_stage="won"),
            db=db_session, user=user,
        )
    assert result["succeeded"] == 3
    assert Thread.call_count == 3  # one delivery per moved lead
