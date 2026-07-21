from datetime import datetime, timezone
import asyncio
from email.message import EmailMessage
from email.utils import format_datetime
import imaplib
import smtplib

import pytest
import httpx

import models as legacy
from database import SessionLocal
from main import app
from product_v2 import models
from product_v2.connectors.base import ConnectorRequest
from product_v2.connectors.smtp import SMTPConnector, SMTPDeliveryUncertain
from product_v2.enums import (
    AttemptStatus,
    CampaignLifecycle,
    CampaignRunMode,
    CampaignRevisionStatus,
    Channel,
    ChannelAccountHealth,
    ContactPointAvailabilityStatus,
    ContactPointVerificationStatus,
    EnrollmentStatus,
    MessageEventType,
    OwnerWritePath,
    RestrictionScope,
    TaskStatus,
    TaskType,
)
from product_v2.message_rendering import MessageRenderError, render_sequence_message
from product_v2.production import Check, domain_preflight_checks
from product_v2.runtime.imap_inbox import poll_imap_inbox
from product_v2.runtime.email_account_probe import (
    EmailAccountProbeError,
    EmailProbeCredentials,
    probe_email_credentials,
)
from product_v2.services.domain import utcnow
from product_v2.services.channel_accounts import create_account_safety_lock
from product_v2.settings_policy import SETTINGS_ACTION, SETTINGS_ENTITY
from services.auth import encrypt_smtp_pass


def _request(method: str, path: str):
    async def run():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.request(method, path)

    return asyncio.run(run())


def _seed_email_attempt(db, *, suffix: str = "production"):
    owner = legacy.User(
        username=f"{suffix}-owner",
        hashed_password="x",
        is_active=True,
        is_admin=True,
    )
    db.add(owner)
    db.flush()
    source = legacy.EmailAccount(
        user_id=owner.id,
        email=f"sender-{suffix}@example.com",
        display_name="Canary Sender",
        smtp_host="smtp.example.com",
        smtp_port=465,
        smtp_user=f"sender-{suffix}@example.com",
        smtp_pass=encrypt_smtp_pass("smtp-password"),
        use_ssl=True,
        use_tls=False,
        imap_host="imap.example.com",
        imap_port=993,
    )
    db.add(source)
    db.flush()
    account = models.ChannelAccount(
        owner_id=owner.id,
        channel=Channel.EMAIL,
        provider="smtp",
        provider_account_id=source.email,
        legacy_email_account_id=source.id,
        enabled=True,
        health_status=ChannelAccountHealth.HEALTHY,
        health_checked_at=utcnow(),
        daily_limit=5,
        timezone="UTC",
    )
    db.add(account)
    db.flush()
    db.add(
        models.OwnerMigrationState(
            owner_id=owner.id,
            current_path=OwnerWritePath.V2,
            version=1,
            switched_by_user_id=owner.id,
        )
    )
    company = models.Company(
        owner_id=owner.id,
        name="Buyer Co",
        normalized_domain=f"buyer-{suffix}.example",
    )
    db.add(company)
    db.flush()
    contact = models.Contact(
        owner_id=owner.id,
        company_id=company.id,
        full_name="Ada Buyer",
        job_title="Purchasing Manager",
        timezone="UTC",
    )
    db.add(contact)
    db.flush()
    point = models.ContactPoint(
        owner_id=owner.id,
        company_id=company.id,
        contact_id=contact.id,
        channel=Channel.EMAIL,
        value=f"buyer-{suffix}@example.net",
        normalized_value=f"buyer-{suffix}@example.net",
        verification_status=ContactPointVerificationStatus.VALID,
        availability_status=ContactPointAvailabilityStatus.AVAILABLE,
        is_primary=True,
    )
    db.add(point)
    campaign = models.Campaign(
        owner_id=owner.id,
        name=f"Canary {suffix}",
        lifecycle=CampaignLifecycle.RUNNING,
        run_mode=CampaignRunMode.REVIEW,
        published_revision_number=1,
    )
    db.add(campaign)
    db.flush()
    revision = models.CampaignRevision(
        owner_id=owner.id,
        campaign_id=campaign.id,
        revision_number=1,
        status=CampaignRevisionStatus.PUBLISHED,
    )
    db.add(revision)
    db.flush()
    step = models.SequenceStep(
        owner_id=owner.id,
        campaign_revision_id=revision.id,
        channel_account_id=account.id,
        position=1,
        channel=Channel.EMAIL,
        subject_template="Hello {{first_name}}",
        body_template="A note for {{company_name}}. {{unsubscribe_url}}",
    )
    db.add(step)
    db.flush()
    enrollment = models.Enrollment(
        owner_id=owner.id,
        campaign_id=campaign.id,
        campaign_revision_id=revision.id,
        company_id=company.id,
        contact_id=contact.id,
        status=EnrollmentStatus.ACTIVE,
    )
    db.add(enrollment)
    db.flush()
    attempt = models.OutreachAttempt(
        owner_id=owner.id,
        campaign_id=campaign.id,
        enrollment_id=enrollment.id,
        sequence_step_id=step.id,
        contact_point_id=point.id,
        channel_account_id=account.id,
        channel=Channel.EMAIL,
        idempotency_key=f"attempt-{suffix}",
        status=AttemptStatus.SUCCEEDED,
        provider="smtp",
        provider_message_id=f"<outbound-{suffix}@example.com>",
    )
    db.add(attempt)
    db.commit()
    return owner, source, account, company, contact, point, enrollment, attempt


def test_strict_message_rendering_generates_recipient_bound_unsubscribe_url(db_session):
    owner, _source, _account, company, contact, point, _enrollment, _attempt = _seed_email_attempt(
        db_session,
        suffix="render",
    )
    rendered = render_sequence_message(
        channel=Channel.EMAIL,
        subject_template="For {{company_name}} / {{first_name}}",
        body_template="Hi {{contact_name}}",
        company_name=company.name,
        company_domain=company.normalized_domain,
        contact_name=contact.full_name,
        job_title=contact.job_title,
        owner_id=owner.id,
        contact_point_id=point.id,
        contact_point_identity_hash=point.normalized_value_hash,
        public_unsubscribe_base_url="https://app.example.com/api/unsubscribe/v2",
    )
    assert rendered.subject == "For Buyer Co / Ada"
    assert rendered.unsubscribe_url.startswith(
        "https://app.example.com/api/unsubscribe/v2/"
    )
    assert rendered.unsubscribe_url in rendered.body

    with pytest.raises(MessageRenderError, match="unknown_placeholder"):
        render_sequence_message(
            channel=Channel.EMAIL,
            subject_template="{{unknown_value}}",
            body_template="Body",
            company_name=company.name,
            company_domain=company.normalized_domain,
            contact_name=contact.full_name,
            job_title=contact.job_title,
            owner_id=owner.id,
            contact_point_id=point.id,
            contact_point_identity_hash=point.normalized_value_hash,
            public_unsubscribe_base_url="https://app.example.com/api/unsubscribe/v2",
        )

    with pytest.raises(MessageRenderError, match="public_unsubscribe_https_url_missing"):
        render_sequence_message(
            channel=Channel.EMAIL,
            subject_template="Hello",
            body_template="Body",
            company_name=company.name,
            company_domain=company.normalized_domain,
            contact_name=contact.full_name,
            job_title=contact.job_title,
            owner_id=owner.id,
            contact_point_id=point.id,
            contact_point_identity_hash=point.normalized_value_hash,
            public_unsubscribe_base_url=(
                "https://user:password@app.example.com/api/unsubscribe/v2"
            ),
        )


def test_v2_unsubscribe_get_is_safe_and_post_is_idempotent(db_session):
    owner, _source, _account, company, contact, point, _enrollment, _attempt = _seed_email_attempt(
        db_session,
        suffix="unsubscribe",
    )
    rendered = render_sequence_message(
        channel=Channel.EMAIL,
        subject_template="Hello",
        body_template="Body",
        company_name=company.name,
        company_domain=company.normalized_domain,
        contact_name=contact.full_name,
        job_title=contact.job_title,
        owner_id=owner.id,
        contact_point_id=point.id,
        contact_point_identity_hash=point.normalized_value_hash,
        public_unsubscribe_base_url="https://app.example.com/api/unsubscribe/v2",
    )
    path = rendered.unsubscribe_url.removeprefix("https://app.example.com")
    assert _request("GET", path).status_code == 200
    assert db_session.query(models.ConsentRestriction).count() == 0
    assert _request("POST", path).json() == {"ok": True}
    assert _request("POST", path).json() == {"ok": True}
    restrictions = db_session.query(models.ConsentRestriction).all()
    assert len(restrictions) == 1
    assert restrictions[0].scope == RestrictionScope.CONTACT_POINT
    assert restrictions[0].contact_point_id == point.id

    malicious = _request(
        "GET",
        "/api/unsubscribe/v2/bad%22%3E%3Cimg%20src%3Dx%20onerror%3Dalert(1)%3E",
    )
    assert malicious.status_code == 400
    assert "<img" not in malicious.text


def test_smtp_connector_emits_plaintext_and_one_click_headers(db_session, monkeypatch):
    owner, _source, account, _company, _contact, point, _enrollment, _attempt = _seed_email_attempt(
        db_session,
        suffix="smtp",
    )
    captured = {}

    class FakeSMTP:
        def __init__(self, host, port, timeout, context):
            captured.update(host=host, port=port, timeout=timeout, context=context)

        def login(self, username, password):
            captured.update(username=username, password=password)

        def send_message(self, message, from_addr, to_addrs):
            captured.update(message=message, from_addr=from_addr, to_addrs=to_addrs)
            return {}

        def quit(self):
            return None

    monkeypatch.setattr(smtplib, "SMTP_SSL", FakeSMTP)
    connector = SMTPConnector(session_factory=SessionLocal)
    result = connector.send(
        ConnectorRequest(
            channel=Channel.EMAIL,
            idempotency_key="smtp-real-test",
            recipient=point.normalized_value,
            subject="Canary subject",
            body="Canary body",
            metadata={
                "owner_id": owner.id,
                "channel_account_id": account.id,
                "unsubscribe_url": "https://app.example.com/api/unsubscribe/v2/token",
            },
        )
    )
    assert result.accepted is True
    assert result.provider_message_id.startswith("<")
    assert captured["password"] == "smtp-password"
    assert captured["message"].get_content_type() == "text/plain"
    assert "List-Unsubscribe=One-Click" == captured["message"]["List-Unsubscribe-Post"]


def test_smtp_disconnect_after_data_boundary_is_unknown(db_session, monkeypatch):
    owner, _source, account, _company, _contact, point, _enrollment, _attempt = _seed_email_attempt(
        db_session,
        suffix="smtp-uncertain",
    )

    class DisconnectingSMTP:
        def __init__(self, *args, **kwargs):
            pass

        def login(self, username, password):
            pass

        def send_message(self, *args, **kwargs):
            raise smtplib.SMTPServerDisconnected("lost after DATA")

        def quit(self):
            pass

    monkeypatch.setattr(smtplib, "SMTP_SSL", DisconnectingSMTP)
    with pytest.raises(SMTPDeliveryUncertain):
        SMTPConnector(session_factory=SessionLocal).send(
            ConnectorRequest(
                channel=Channel.EMAIL,
                idempotency_key="smtp-uncertain-test",
                recipient=point.normalized_value,
                subject="Subject",
                body="Body",
                metadata={"owner_id": owner.id, "channel_account_id": account.id},
            )
        )


def test_smtp_final_boundary_requires_exact_auto_owner_approval_and_daily_cap(
    db_session,
    monkeypatch,
):
    owner, _source, account, _company, _contact, point, _enrollment, _attempt = (
        _seed_email_attempt(db_session, suffix="smtp-auto-approval")
    )
    campaign = db_session.query(models.Campaign).filter_by(owner_id=owner.id).one()
    campaign.run_mode = CampaignRunMode.AUTO
    db_session.commit()
    calls = []

    class FakeSMTP:
        def __init__(self, *args, **kwargs):
            calls.append("connected")

        def login(self, username, password):
            pass

        def send_message(self, *args, **kwargs):
            return {}

        def quit(self):
            pass

    monkeypatch.setenv("AUTOLEADGEN_ENV", "production")
    monkeypatch.setattr(smtplib, "SMTP_SSL", FakeSMTP)

    def send():
        return SMTPConnector(session_factory=SessionLocal).send(
            ConnectorRequest(
                channel=Channel.EMAIL,
                idempotency_key="smtp-auto-approval-test",
                recipient=point.normalized_value,
                subject="Approved automatic subject",
                body="Approved automatic body",
                metadata={
                    "owner_id": owner.id,
                    "channel_account_id": account.id,
                    "run_mode": "auto",
                    "review_approved": False,
                },
            )
        )

    blocked = send()
    assert blocked.accepted is False
    assert blocked.raw == {"reason": "smtp_production_auto_send_not_approved"}
    assert calls == []

    monkeypatch.setenv("PRODUCT_V2_PRODUCTION_AUTO_SEND_APPROVED", "true")
    monkeypatch.setenv("PRODUCT_V2_AUTO_SEND_APPROVAL_ID", "AUTO-APPROVAL-1234")
    monkeypatch.setenv("PRODUCT_V2_AUTO_SEND_OWNER_IDS", str(owner.id))
    monkeypatch.setenv("PRODUCT_V2_AUTO_SEND_MAX_DAILY_PER_ACCOUNT", "4")
    over_cap = send()
    assert over_cap.accepted is False
    assert over_cap.raw == {"reason": "smtp_production_auto_daily_limit_invalid"}
    assert calls == []

    monkeypatch.setenv("PRODUCT_V2_AUTO_SEND_MAX_DAILY_PER_ACCOUNT", "5")
    accepted = send()
    assert accepted.accepted is True
    assert calls == ["connected"]


def test_smtp_final_boundary_blocks_orphaned_legacy_suppression(
    db_session,
    monkeypatch,
):
    owner, _source, account, _company, _contact, point, _enrollment, _attempt = (
        _seed_email_attempt(db_session, suffix="smtp-legacy-suppression")
    )
    db_session.add(
        legacy.EmailSuppression(
            user_id=owner.id,
            email=f" {point.normalized_value.upper()} ",
            reason="unsubscribe",
            source="legacy",
        )
    )
    db_session.commit()
    calls = []

    class FakeSMTP:
        def __init__(self, *args, **kwargs):
            calls.append("connected")

    monkeypatch.setenv("AUTOLEADGEN_ENV", "production")
    monkeypatch.setattr(smtplib, "SMTP_SSL", FakeSMTP)
    result = SMTPConnector(session_factory=SessionLocal).send(
        ConnectorRequest(
            channel=Channel.EMAIL,
            idempotency_key="smtp-legacy-suppression-test",
            recipient=point.normalized_value,
            subject="Reviewed subject",
            body="Reviewed body",
            metadata={
                "owner_id": owner.id,
                "channel_account_id": account.id,
                "run_mode": "review",
                "review_approved": True,
            },
        )
    )

    assert result.accepted is False
    assert result.raw == {"reason": "smtp_recipient_suppressed"}
    assert calls == []


def test_smtp_rechecks_account_safety_lock_after_login_before_data(
    db_session,
    monkeypatch,
):
    owner, _source, account, _company, _contact, point, _enrollment, _attempt = (
        _seed_email_attempt(db_session, suffix="smtp-final-lock")
    )
    calls = []

    class LockingSMTP:
        def __init__(self, *args, **kwargs):
            calls.append("connected")

        def login(self, username, password):
            lock_db = SessionLocal()
            try:
                locked_account = lock_db.get(models.ChannelAccount, account.id)
                create_account_safety_lock(
                    lock_db,
                    account=locked_account,
                    reason="Complaint arrived during the SMTP session",
                    code="provider_complaint:after-login",
                )
                lock_db.commit()
            finally:
                lock_db.close()
            calls.append("login")

        def send_message(self, *args, **kwargs):
            calls.append("data")
            return {}

        def quit(self):
            calls.append("quit")

    monkeypatch.setenv("AUTOLEADGEN_ENV", "production")
    monkeypatch.setattr(smtplib, "SMTP_SSL", LockingSMTP)
    result = SMTPConnector(session_factory=SessionLocal).send(
        ConnectorRequest(
            channel=Channel.EMAIL,
            idempotency_key="smtp-final-lock-test",
            recipient=point.normalized_value,
            subject="Reviewed subject",
            body="Reviewed body",
            metadata={
                "owner_id": owner.id,
                "channel_account_id": account.id,
                "run_mode": "review",
                "review_approved": True,
            },
        )
    )

    assert result.accepted is False
    assert result.raw == {"reason": "smtp_account_safety_lock"}
    assert calls == ["connected", "login", "quit"]


def test_production_email_probe_authenticates_without_sending(monkeypatch):
    actions = []

    class FakeSMTP:
        def __init__(self, host, port, timeout, context):
            actions.append(("smtp_connect", host, port, timeout))

        def login(self, username, password):
            actions.append(("smtp_login", username, password))
            return 235, b"ok"

        def noop(self):
            actions.append(("smtp_noop",))
            return 250, b"ok"

        def quit(self):
            actions.append(("smtp_quit",))

    class FakeIMAP:
        def __init__(self, host, port, timeout, ssl_context):
            actions.append(("imap_connect", host, port, timeout))

        def login(self, username, password):
            actions.append(("imap_login", username, password))
            return "OK", [b"authenticated"]

        def select(self, mailbox, readonly):
            actions.append(("imap_select", mailbox, readonly))
            return "OK", [b"0"]

        def logout(self):
            actions.append(("imap_logout",))

    monkeypatch.setattr(
        "product_v2.runtime.email_account_probe.smtplib.SMTP_SSL",
        FakeSMTP,
    )
    monkeypatch.setattr(
        "product_v2.runtime.email_account_probe.imaplib.IMAP4_SSL",
        FakeIMAP,
    )
    probe_email_credentials(
        EmailProbeCredentials(
            email="sender@example.com",
            smtp_host="smtp.example.com",
            smtp_port=465,
            username="sender@example.com",
            password="not-logged",
            use_ssl=True,
            use_tls=False,
            imap_host="imap.example.com",
            imap_port=993,
        ),
        timeout=5,
    )
    assert ("smtp_noop",) in actions
    assert ("imap_select", "INBOX", True) in actions
    assert all(action[0] not in {"send", "sendmail", "send_message"} for action in actions)


def test_production_email_probe_returns_stable_imap_auth_failure(monkeypatch):
    class FakeSMTP:
        def __init__(self, *args, **kwargs):
            pass

        def login(self, *args):
            return 235, b"ok"

        def noop(self):
            return 250, b"ok"

        def quit(self):
            pass

    class RejectingIMAP:
        def __init__(self, *args, **kwargs):
            pass

        def login(self, *args):
            raise imaplib.IMAP4.error("must not leak")

        def logout(self):
            pass

    monkeypatch.setattr(
        "product_v2.runtime.email_account_probe.smtplib.SMTP_SSL",
        FakeSMTP,
    )
    monkeypatch.setattr(
        "product_v2.runtime.email_account_probe.imaplib.IMAP4_SSL",
        RejectingIMAP,
    )
    credentials = EmailProbeCredentials(
        email="sender@example.com",
        smtp_host="smtp.example.com",
        smtp_port=465,
        username="sender@example.com",
        password="not-logged",
        use_ssl=True,
        use_tls=False,
        imap_host="imap.example.com",
        imap_port=993,
    )
    with pytest.raises(EmailAccountProbeError) as captured:
        probe_email_credentials(credentials, timeout=5)
    assert captured.value.code == "imap_auth_failed"


def test_imap_reply_is_thread_bound_ingested_and_idempotent(db_session, monkeypatch):
    owner, _source, _account, _company, _contact, point, enrollment, attempt = _seed_email_attempt(
        db_session,
        suffix="imap",
    )
    message = EmailMessage()
    message["From"] = point.normalized_value
    message["To"] = "sender-imap@example.com"
    message["Subject"] = "Re: Canary"
    message["Message-ID"] = "<reply-imap@example.net>"
    message["In-Reply-To"] = attempt.provider_message_id
    message["Date"] = format_datetime(datetime.now(timezone.utc))
    message.set_content("Please unsubscribe me")
    raw = message.as_bytes()

    class FakeIMAP:
        def __init__(self, *args, **kwargs):
            pass

        def login(self, username, password):
            assert username == "sender-imap@example.com"
            assert password == "smtp-password"

        def select(self, mailbox, readonly):
            return "OK", [b"1"]

        def search(self, *args):
            return "OK", [b"1"]

        def fetch(self, identifier, query):
            return "OK", [(b"1 BODY[]", raw)]

        def logout(self):
            pass

    monkeypatch.setattr("product_v2.runtime.imap_inbox.imaplib.IMAP4_SSL", FakeIMAP)
    first = poll_imap_inbox(db_session)
    second = poll_imap_inbox(db_session)
    assert first.ingested == 1
    assert second.duplicates == 1
    assert db_session.query(models.MessageEvent).count() == 1
    assert db_session.query(models.ConsentRestriction).filter_by(
        contact_point_id=point.id,
        active=True,
    ).count() == 1
    db_session.refresh(enrollment)
    assert enrollment.status == EnrollmentStatus.PAUSED


def test_imap_abuse_report_suppresses_contact_and_hard_stops_sender(
    db_session,
    monkeypatch,
):
    owner, _source, account, _company, _contact, point, enrollment, attempt = (
        _seed_email_attempt(db_session, suffix="imap-complaint")
    )
    raw = (
        "From: complaints@feedback.example.net\r\n"
        "To: sender-imap-complaint@example.com\r\n"
        "Subject: abuse report\r\n"
        "Message-ID: <arf-imap-complaint@example.net>\r\n"
        f"Date: {format_datetime(datetime.now(timezone.utc))}\r\n"
        "MIME-Version: 1.0\r\n"
        "Content-Type: multipart/report; report-type=feedback-report; boundary=arf-boundary\r\n"
        "\r\n"
        "--arf-boundary\r\n"
        "Content-Type: text/plain; charset=utf-8\r\n"
        "\r\n"
        "An abuse complaint was received.\r\n"
        "--arf-boundary\r\n"
        "Content-Type: message/feedback-report\r\n"
        "\r\n"
        "Feedback-Type: abuse\r\n"
        f"Original-Message-ID: {attempt.provider_message_id}\r\n"
        "\r\n"
        "--arf-boundary--\r\n"
    ).encode("utf-8")

    class FakeIMAP:
        def __init__(self, *args, **kwargs):
            pass

        def login(self, username, password):
            pass

        def select(self, mailbox, readonly):
            return "OK", [b"1"]

        def search(self, *args):
            return "OK", [b"1"]

        def fetch(self, identifier, query):
            return "OK", [(b"1 BODY[]", raw)]

        def logout(self):
            pass

    monkeypatch.setattr("product_v2.runtime.imap_inbox.imaplib.IMAP4_SSL", FakeIMAP)
    first = poll_imap_inbox(db_session)
    second = poll_imap_inbox(db_session)

    assert first.ingested == 1
    assert second.duplicates == 1
    event = db_session.query(models.MessageEvent).one()
    assert event.event_type == MessageEventType.COMPLAINED
    assert event.metadata_json["feedback_type"] == "abuse"
    assert db_session.query(models.ConsentRestriction).filter_by(
        contact_point_id=point.id,
        active=True,
        reason="provider_complaint",
    ).count() == 1
    db_session.refresh(point)
    db_session.refresh(account)
    db_session.refresh(enrollment)
    assert point.availability_status == ContactPointAvailabilityStatus.UNAVAILABLE
    assert account.health_status == ChannelAccountHealth.UNHEALTHY
    assert enrollment.status == EnrollmentStatus.BLOCKED
    safety_lock = db_session.query(models.SafetyLock).filter_by(
        owner_id=owner.id,
        channel_account_id=account.id,
        active=True,
    ).one()
    task = db_session.query(models.Task).filter_by(
        owner_id=owner.id,
        task_type=TaskType.DELIVERABILITY_ALERT,
        status=TaskStatus.OPEN,
    ).one()
    assert task.metadata_json["safety_lock_id"] == safety_lock.id
    assert task.metadata_json["auto_send_allowed"] is False


def test_imap_rejects_oversized_message_before_fetching_body(db_session, monkeypatch):
    _seed_email_attempt(db_session, suffix="imap-oversize")
    queries = []

    class FakeIMAP:
        def __init__(self, *args, **kwargs):
            pass

        def login(self, username, password):
            pass

        def select(self, mailbox, readonly):
            return "OK", [b"1"]

        def search(self, *args):
            return "OK", [b"1"]

        def fetch(self, identifier, query):
            queries.append(query)
            assert query == "(RFC822.SIZE BODY.PEEK[HEADER])"
            return "OK", [
                (
                    b"1 (RFC822.SIZE 9999999 BODY[HEADER] {64}",
                    b"Message-ID: <oversized@example.net>\r\n\r\n",
                )
            ]

        def logout(self):
            pass

    monkeypatch.setenv("IMAP_MAX_MESSAGE_BYTES", "1024")
    monkeypatch.setattr("product_v2.runtime.imap_inbox.imaplib.IMAP4_SSL", FakeIMAP)
    result = poll_imap_inbox(db_session)
    assert result.failures == 1
    assert result.ingested == 0
    assert queries == ["(RFC822.SIZE BODY.PEEK[HEADER])"]


def test_enable_real_preflight_requires_review_policy_and_exact_unsubscribe_endpoint(
    db_session,
    monkeypatch,
):
    owner, *_ = _seed_email_attempt(db_session, suffix="preflight-policy")
    monkeypatch.setattr(
        "product_v2.production.database_readiness_checks",
        lambda db, require_head=True: [Check("database", True, "ready")],
    )

    missing = domain_preflight_checks(db_session, phase="enable-real")
    assert not next(item for item in missing if item.name == "email_review_policy").passed
    assert not next(item for item in missing if item.name == "public_unsubscribe_url").passed

    db_session.add(
        models.AuditEvent(
            owner_id=owner.id,
            actor_user_id=owner.id,
            action=SETTINGS_ACTION,
            entity_type=SETTINGS_ENTITY,
            entity_id="channels_integrations",
            after_data={
                "version": 1,
                "values": {
                    "email_enabled": True,
                    "linkedin_enabled": False,
                    "whatsapp_enabled": False,
                    "public_unsubscribe_url": (
                        "https://app.example.com/api/unsubscribe/v2"
                    ),
                    "review_before_send": True,
                    "integration_notes": "Email canary only",
                },
            },
        )
    )
    db_session.commit()

    ready = domain_preflight_checks(db_session, phase="enable-real")
    assert next(item for item in ready if item.name == "email_review_policy").passed
    assert next(item for item in ready if item.name == "public_unsubscribe_url").passed
    assert next(item for item in ready if item.name == "production_campaign_modes").passed
    assert next(item for item in ready if item.name == "production_auto_send").passed


def test_enable_real_preflight_allows_auto_only_for_exact_capped_owner_cohort(
    db_session,
    monkeypatch,
):
    owner, *_ = _seed_email_attempt(db_session, suffix="preflight-auto")
    campaign = db_session.query(models.Campaign).filter_by(owner_id=owner.id).one()
    campaign.run_mode = CampaignRunMode.AUTO
    db_session.add(
        models.AuditEvent(
            owner_id=owner.id,
            actor_user_id=owner.id,
            action=SETTINGS_ACTION,
            entity_type=SETTINGS_ENTITY,
            entity_id="channels_integrations",
            after_data={
                "version": 1,
                "values": {
                    "email_enabled": True,
                    "linkedin_enabled": False,
                    "whatsapp_enabled": False,
                    "public_unsubscribe_url": "https://app.example.com/api/unsubscribe/v2",
                    "review_before_send": False,
                    "integration_notes": "Approved automatic cohort",
                },
            },
        )
    )
    db_session.commit()
    monkeypatch.setattr(
        "product_v2.production.database_readiness_checks",
        lambda db, require_head=True: [Check("database", True, "ready")],
    )

    unapproved = domain_preflight_checks(db_session, phase="enable-real")
    assert not next(
        item for item in unapproved if item.name == "production_auto_send"
    ).passed

    monkeypatch.setenv("PRODUCT_V2_PRODUCTION_AUTO_SEND_APPROVED", "true")
    monkeypatch.setenv("PRODUCT_V2_AUTO_SEND_APPROVAL_ID", "AUTO-APPROVAL-1234")
    monkeypatch.setenv("PRODUCT_V2_AUTO_SEND_OWNER_IDS", f"{owner.id},{owner.id + 1}")
    wrong_cohort = domain_preflight_checks(db_session, phase="enable-real")
    assert not next(
        item for item in wrong_cohort if item.name == "production_auto_send"
    ).passed

    monkeypatch.setenv("PRODUCT_V2_AUTO_SEND_OWNER_IDS", str(owner.id))
    monkeypatch.setenv("PRODUCT_V2_AUTO_SEND_MAX_DAILY_PER_ACCOUNT", "4")
    over_cap = domain_preflight_checks(db_session, phase="enable-real")
    assert next(item for item in over_cap if item.name == "production_auto_send").passed
    assert not next(
        item for item in over_cap if item.name == "production_auto_send_capacity"
    ).passed

    monkeypatch.setenv("PRODUCT_V2_AUTO_SEND_MAX_DAILY_PER_ACCOUNT", "5")
    approved = domain_preflight_checks(db_session, phase="enable-real")
    assert next(item for item in approved if item.name == "production_auto_send").passed
    assert next(
        item for item in approved if item.name == "production_auto_send_capacity"
    ).passed
