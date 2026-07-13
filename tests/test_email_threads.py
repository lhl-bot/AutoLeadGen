import asyncio
from datetime import datetime, timezone
from email.message import EmailMessage

import models
from routers import replies
from services import inbox_monitor
from services.email_threads import (
    canonical_inbound_message_id,
    find_lead_for_inbound,
    inbound_message_exists,
    reply_thread_context,
)


def _user(db, username):
    user = models.User(username=username, hashed_password="x", is_active=True)
    db.add(user)
    db.flush()
    return user


def _workflow(db, user, name):
    workflow = models.Workflow(
        user_id=user.id,
        name=name,
        search_keywords="textiles",
        target_positions="buyer",
    )
    db.add(workflow)
    db.flush()
    return workflow


def _account(db, user, email):
    account = models.EmailAccount(
        user_id=user.id,
        email=email,
        smtp_host="smtp.example.com",
        smtp_user=email,
        smtp_pass="secret",
        imap_host="imap.example.com",
    )
    db.add(account)
    db.flush()
    return account


def _bind(db, workflow, account):
    db.add(models.WorkflowEmail(
        workflow_id=workflow.id,
        email_account_id=account.id,
    ))


def _lead(db, workflow, email, *, status="sent", has_replied=False):
    lead = models.Lead(
        workflow_id=workflow.id,
        domain=email.split("@")[-1],
        company_name=workflow.name,
        email=email,
        status=status,
        has_replied=has_replied,
    )
    db.add(lead)
    db.flush()
    return lead


def _log(db, lead, *, direction, from_email, to_email, message_id):
    log = models.EmailLog(
        lead_id=lead.id,
        direction=direction,
        from_email=from_email,
        to_email=to_email,
        subject="Subject",
        body="Body",
        message_id=message_id,
        sent_at=datetime.now(timezone.utc),
    )
    db.add(log)
    db.flush()
    return log


def test_inbound_thread_match_never_crosses_user_or_mailbox(db_session):
    other = _user(db_session, "other")
    owner = _user(db_session, "owner")
    other_workflow = _workflow(db_session, other, "Other")
    owner_workflow = _workflow(db_session, owner, "Owner")
    other_account = _account(db_session, other, "other@example.com")
    owner_account = _account(db_session, owner, "owner@example.com")
    _bind(db_session, other_workflow, other_account)
    _bind(db_session, owner_workflow, owner_account)

    # Insert the wrong user's lead first to reproduce the old `.first()` bug.
    _lead(db_session, other_workflow, "buyer@client.example")
    owner_lead = _lead(db_session, owner_workflow, "buyer@client.example")
    _log(
        db_session,
        owner_lead,
        direction="outbound",
        from_email=owner_account.email,
        to_email=owner_lead.email,
        message_id="<owner-thread@example.com>",
    )
    db_session.commit()

    matched = find_lead_for_inbound(
        db_session,
        account=owner_account,
        sender_email="BUYER@client.example",
        in_reply_to="<owner-thread@example.com>",
    )

    assert matched.id == owner_lead.id
    assert matched.workflow.user_id == owner.id


def test_inbound_fallback_stays_with_workflows_bound_to_mailbox(db_session):
    user = _user(db_session, "owner")
    first_workflow = _workflow(db_session, user, "First")
    second_workflow = _workflow(db_session, user, "Second")
    first_account = _account(db_session, user, "one@example.com")
    second_account = _account(db_session, user, "two@example.com")
    _bind(db_session, first_workflow, first_account)
    _bind(db_session, second_workflow, second_account)
    _lead(db_session, first_workflow, "shared@client.example")
    second_lead = _lead(db_session, second_workflow, "shared@client.example")
    db_session.commit()

    matched = find_lead_for_inbound(
        db_session,
        account=second_account,
        sender_email="shared@client.example",
    )

    assert matched.id == second_lead.id


def test_missing_message_id_gets_stable_mailbox_scoped_dedupe_key(db_session):
    key = canonical_inbound_message_id(
        None,
        account_email="owner@example.com",
        sender_email="buyer@client.example",
        subject="Re: hello",
        date_header="Fri, 10 Jul 2026 10:00:00 +0800",
        body="Interested",
    )
    assert key == canonical_inbound_message_id(
        None,
        account_email="owner@example.com",
        sender_email="buyer@client.example",
        subject="Re: hello",
        date_header="Fri, 10 Jul 2026 10:00:00 +0800",
        body="Interested",
    )

    user = _user(db_session, "owner")
    workflow = _workflow(db_session, user, "Owner")
    lead = _lead(db_session, workflow, "buyer@client.example")
    _log(
        db_session,
        lead,
        direction="inbound",
        from_email=lead.email,
        to_email="owner@example.com",
        message_id=key,
    )
    db_session.commit()

    assert inbound_message_exists(
        db_session,
        account_email="owner@example.com",
        message_id=key,
    ) is True
    assert inbound_message_exists(
        db_session,
        account_email="another@example.com",
        message_id=key,
    ) is False


def test_reply_context_uses_original_sender_and_latest_inbound_message(db_session):
    user = _user(db_session, "owner")
    workflow = _workflow(db_session, user, "Owner")
    first_account = _account(db_session, user, "one@example.com")
    original_account = _account(db_session, user, "two@example.com")
    _bind(db_session, workflow, first_account)
    _bind(db_session, workflow, original_account)
    lead = _lead(db_session, workflow, "buyer@client.example", status="replied", has_replied=True)
    _log(
        db_session,
        lead,
        direction="outbound",
        from_email=original_account.email,
        to_email=lead.email,
        message_id="<outbound@example.com>",
    )
    _log(
        db_session,
        lead,
        direction="inbound",
        from_email=lead.email,
        to_email=original_account.email,
        message_id="<inbound@example.com>",
    )
    _log(
        db_session,
        lead,
        direction="inbound",
        from_email="mailer-daemon@example.com",
        to_email=original_account.email,
        message_id="<later-bounce@example.com>",
    )
    db_session.commit()

    context = reply_thread_context(db_session, lead)

    assert context.account.id == original_account.id
    assert context.in_reply_to == "<inbound@example.com>"
    assert context.references == "<outbound@example.com> <inbound@example.com>"
    assert context.original_sender_missing is False


def test_send_reply_keeps_reply_history_sender_and_thread(db_session, monkeypatch):
    monkeypatch.setenv("CREDITS_ENABLED", "false")
    captured = {}

    def fake_send_email(**kwargs):
        captured.update(kwargs)
        return {"success": True, "message_id": "<response@example.com>"}

    monkeypatch.setattr(replies, "send_email", fake_send_email)
    monkeypatch.setattr(replies, "decrypt_smtp_pass", lambda value: value)

    user = _user(db_session, "owner")
    workflow = _workflow(db_session, user, "Owner")
    _bind(db_session, workflow, _account(db_session, user, "one@example.com"))
    original_account = _account(db_session, user, "two@example.com")
    _bind(db_session, workflow, original_account)
    lead = _lead(db_session, workflow, "buyer@client.example", status="replied", has_replied=True)
    lead.reply_snippet = "Please send details"
    lead.reply_intent = "more_info"
    _log(
        db_session,
        lead,
        direction="outbound",
        from_email=original_account.email,
        to_email=lead.email,
        message_id="<outbound@example.com>",
    )
    _log(
        db_session,
        lead,
        direction="inbound",
        from_email=lead.email,
        to_email=original_account.email,
        message_id="<inbound@example.com>",
    )
    db_session.commit()

    result = asyncio.run(replies.send_ai_reply(
        lead.id,
        replies.SendReplyRequest(draft="Subject: Re: details\n\nHere are the details."),
        db_session,
        user,
    ))

    db_session.refresh(lead)
    assert result["from_email"] == original_account.email
    assert captured["from_email"] == original_account.email
    assert captured["in_reply_to"] == "<inbound@example.com>"
    assert captured["references"] == "<outbound@example.com> <inbound@example.com>"
    assert lead.status == "sent"
    assert lead.has_replied is True
    assert lead.reply_intent == "more_info"

    reply_rows = replies.read_replies(limit=100, db=db_session, user=user)
    assert [row["id"] for row in reply_rows] == [lead.id]


def test_inbox_persists_scoped_reply_even_when_ai_analysis_fails(db_session, monkeypatch):
    other = _user(db_session, "other")
    owner = _user(db_session, "owner")
    other_workflow = _workflow(db_session, other, "Other")
    owner_workflow = _workflow(db_session, owner, "Owner")
    _lead(db_session, other_workflow, "buyer@client.example")
    owner_account = _account(db_session, owner, "owner@example.com")
    _bind(db_session, owner_workflow, owner_account)
    owner_lead = _lead(db_session, owner_workflow, "buyer@client.example")
    _log(
        db_session,
        owner_lead,
        direction="outbound",
        from_email=owner_account.email,
        to_email=owner_lead.email,
        message_id="<outbound@example.com>",
    )
    db_session.commit()

    message = EmailMessage()
    message["From"] = "Buyer <buyer@client.example>"
    message["To"] = owner_account.email
    message["Subject"] = "Re: Subject"
    message["Message-ID"] = "<inbound@example.com>"
    message["In-Reply-To"] = "<outbound@example.com>"
    message.set_content("Interested in more details.")
    raw_message = message.as_bytes()

    class FakeImap:
        def __init__(self, *args, **kwargs):
            pass

        def login(self, *args, **kwargs):
            return "OK", []

        def select(self, *args, **kwargs):
            return "OK", []

        def search(self, *args, **kwargs):
            return "OK", [b"1"]

        def fetch(self, *args, **kwargs):
            return "OK", [(b"1", raw_message)]

        def logout(self):
            return "BYE", []

    monkeypatch.setattr(inbox_monitor.imaplib, "IMAP4_SSL", FakeImap)
    monkeypatch.setattr(inbox_monitor, "decrypt_smtp_pass", lambda value: value)
    monkeypatch.setattr(
        inbox_monitor,
        "analyze_reply_intent",
        lambda text: (_ for _ in ()).throw(RuntimeError("AI unavailable")),
    )

    inbox_monitor.check_inbox_for_replies()

    db_session.refresh(owner_lead)
    assert owner_lead.has_replied is True
    assert owner_lead.reply_intent == "other"
    assert owner_lead.status == "replied"
    assert owner_lead.reply_snippet.startswith("Interested in more details")
    inbound = db_session.query(models.EmailLog).filter_by(
        lead_id=owner_lead.id,
        direction="inbound",
        message_id="<inbound@example.com>",
    ).one()
    assert inbound.to_email == owner_account.email
