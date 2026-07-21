import asyncio
from datetime import datetime, timedelta, timezone

import models
from services import outbound_engine


def test_automated_reply_threads_to_customer_message_not_later_bounce(db_session, monkeypatch):
    monkeypatch.setenv("CREDITS_ENABLED", "false")
    monkeypatch.setenv("EMAIL_MAX_DAILY_PER_ACCOUNT", "50")
    monkeypatch.setenv("EMAIL_CHECK_RECIPIENT_DOMAIN_DNS", "false")
    monkeypatch.setenv("EMAIL_CHECK_RECIPIENT_MX", "false")
    captured = {}

    def fake_send_email(**kwargs):
        captured.update(kwargs)
        return {"success": True, "message_id": "<response@example.com>"}

    monkeypatch.setattr(outbound_engine, "send_email", fake_send_email)
    monkeypatch.setattr(outbound_engine, "decrypt_smtp_pass", lambda value: value)

    user = models.User(username="owner", hashed_password="x", is_active=True)
    db_session.add(user)
    db_session.flush()
    workflow = models.Workflow(
        user_id=user.id,
        name="Owner",
        search_keywords="textiles",
        target_positions="buyer",
    )
    db_session.add(workflow)
    db_session.flush()
    account = models.EmailAccount(
        user_id=user.id,
        email="owner@example.com",
        smtp_host="smtp.example.com",
        smtp_user="owner@example.com",
        smtp_pass="secret",
        imap_host="imap.example.com",
    )
    db_session.add(account)
    db_session.flush()
    db_session.add(models.WorkflowEmail(workflow_id=workflow.id, email_account_id=account.id))
    lead = models.Lead(
        workflow_id=workflow.id,
        domain="client.example",
        company_name="Client",
        email="buyer@client.example",
        status="sent",
        has_replied=True,
        ai_draft="Subject: Re: details\n\nHere are the details.",
        email_validation_status="valid",
    )
    db_session.add(lead)
    db_session.flush()
    db_session.add(models.LeadBrief(
        lead_id=lead.id,
        research_status="valid",
        company_overview="Client sells home textiles.",
        specific_products="Bedding and duvet covers",
        personalization_hook="Client recently expanded its duvet cover range.",
    ))
    db_session.add_all([
        models.EmailLog(
            lead_id=lead.id,
            direction="outbound",
            from_email=account.email,
            to_email=lead.email,
            message_id="<outbound@example.com>",
            sent_at=datetime.now(timezone.utc) - timedelta(hours=72),
        ),
        models.EmailLog(
            lead_id=lead.id,
            direction="inbound",
            from_email=lead.email,
            to_email=account.email,
            message_id="<customer-reply@example.com>",
        ),
        models.EmailLog(
            lead_id=lead.id,
            direction="inbound",
            from_email="mailer-daemon@example.com",
            to_email=account.email,
            message_id="<later-bounce@example.com>",
        ),
    ])
    db_session.commit()

    result = asyncio.run(outbound_engine.send_lead_email(
        lead,
        workflow,
        db_session,
        manual_reviewed=True,
    ))

    assert result["success"] is True
    assert captured["in_reply_to"] == "<customer-reply@example.com>"
    assert captured["references"] == "<outbound@example.com> <customer-reply@example.com>"
