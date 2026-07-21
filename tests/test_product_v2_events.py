import asyncio
import json
import time

import httpx
import pytest

import models as legacy
from product_v2 import models
from product_v2.enums import Channel, ContactPointVerificationStatus, RestrictionScope, TaskType
from product_v2.webhook_security import sign_webhook


_WEBHOOK_SECRET = "local-test-webhook-secret-material-0001"


def _signed_headers(*, owner_id, provider, event_id, raw_body, timestamp=None):
    timestamp = timestamp or int(time.time())
    return {
        "Content-Type": "application/json",
        "Idempotency-Key": event_id,
        "X-AutoLeadGen-Webhook-Timestamp": str(timestamp),
        "X-AutoLeadGen-Webhook-Event-Id": event_id,
        "X-AutoLeadGen-Webhook-Signature": sign_webhook(
            secret=_WEBHOOK_SECRET,
            provider=provider,
            owner_id=owner_id,
            timestamp=timestamp,
            event_id=event_id,
            raw_body=raw_body,
        ),
    }


def _contact(db_session):
    user = legacy.User(username="event-owner", hashed_password="x", is_active=True)
    db_session.add(user)
    db_session.flush()
    company = models.Company(owner_id=user.id, name="Event Buyer", normalized_domain="event.example")
    db_session.add(company)
    db_session.flush()
    contact = models.Contact(owner_id=user.id, company_id=company.id, full_name="Event Contact", timezone="UTC")
    db_session.add(contact)
    db_session.flush()
    point = models.ContactPoint(
        owner_id=user.id,
        company_id=company.id,
        contact_id=contact.id,
        channel=Channel.EMAIL,
        value="buyer@event.example",
        normalized_value="buyer@event.example",
        verification_status=ContactPointVerificationStatus.VALID,
    )
    db_session.add(point)
    db_session.commit()
    return user, company, contact, point


def test_v2_webhook_requires_idempotency_and_creates_email_scoped_unsubscribe(db_session, monkeypatch):
    # GIVEN: An authenticated V2 contact point and an inbound unsubscribe event.
    user, company, contact, point = _contact(db_session)
    import main

    monkeypatch.setenv("PRODUCT_V2_WEBHOOK_SECRET_FAKE_EMAIL", _WEBHOOK_SECRET)
    payload = {
        "channel": "email",
        "direction": "inbound",
        "event_type": "replied",
        "company_id": company.id,
        "contact_id": contact.id,
        "contact_point_id": point.id,
        "provider_message_id": "provider-message-1",
        "body": "unsubscribe",
        "metadata_json": {"thread_id": "thread-1"},
    }

    raw_body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    headers = _signed_headers(
        owner_id=user.id,
        provider="fake-email",
        event_id="webhook-event-0001",
        raw_body=raw_body,
    )

    async def flow():
        transport = httpx.ASGITransport(app=main.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            # WHEN: The webhook is sent without and then twice with the required key.
            missing_headers = dict(headers)
            missing_headers.pop("Idempotency-Key")
            missing = await client.post(
                f"/api/v2/webhooks/{user.id}/fake-email/events",
                headers=missing_headers,
                content=raw_body,
            )
            first = await client.post(
                f"/api/v2/webhooks/{user.id}/fake-email/events",
                headers=headers,
                content=raw_body,
            )
            duplicate = await client.post(
                f"/api/v2/webhooks/{user.id}/fake-email/events",
                headers=headers,
                content=raw_body,
            )

            # THEN: Missing keys fail and duplicate delivery returns one durable event.
            assert missing.status_code == 422
            assert first.status_code == 201, first.text
            assert duplicate.status_code == 201, duplicate.text
            assert first.json()["id"] == duplicate.json()["id"]

    asyncio.run(flow())

    assert db_session.query(models.MessageEvent).count() == 1
    assert db_session.query(models.ReplyAssessment).count() == 1
    assert db_session.query(models.Task).filter_by(task_type=TaskType.REPLY_TRIAGE).count() == 1
    restriction = db_session.query(models.ConsentRestriction).one()
    assert restriction.scope == RestrictionScope.CONTACT_POINT
    assert restriction.contact_point_id == point.id
    assert restriction.contact_id is None
    assert restriction.company_id is None


def test_company_wide_unsubscribe_is_only_a_human_confirmation_task(db_session):
    # GIVEN: A provider event explicitly asking to block an entire company.
    user, company, contact, point = _contact(db_session)
    from product_v2.runtime.events import ingest_provider_event
    from product_v2.schemas import WebhookEventCreate

    payload = WebhookEventCreate(
        channel="email",
        direction="inbound",
        event_type="replied",
        company_id=company.id,
        contact_id=contact.id,
        contact_point_id=point.id,
        body="Do not contact anyone at our company",
    )

    # WHEN: The event is ingested in the local transaction.
    ingest_provider_event(
        db_session,
        owner_id=user.id,
        provider="fake-email",
        idempotency_key="webhook-company-0001",
        payload=payload,
    )
    db_session.commit()

    # THEN: No company restriction is written until a human confirms the scope.
    assert db_session.query(models.ConsentRestriction).count() == 0
    task = next(
        task
        for task in db_session.query(models.Task).all()
        if (task.metadata_json or {}).get("requires_human_confirmation") is True
    )
    assert task.task_type == TaskType.REPLY_TRIAGE
    assert task.metadata_json["requires_human_confirmation"] is True
    assert task.metadata_json["restriction_scope"] == "company"


def test_native_unsubscribe_event_without_body_creates_contact_point_restriction(db_session):
    # GIVEN: A provider-native unsubscribe event with no natural-language body.
    user, company, contact, point = _contact(db_session)
    from product_v2.runtime.events import ingest_provider_event
    from product_v2.schemas import WebhookEventCreate

    payload = WebhookEventCreate(
        channel=Channel.EMAIL,
        direction="inbound",
        event_type="unsubscribed",
        company_id=company.id,
        contact_id=contact.id,
        contact_point_id=point.id,
        body=None,
    )

    # WHEN: The durable provider event is ingested twice with one idempotency key.
    first = ingest_provider_event(
        db_session,
        owner_id=user.id,
        provider="fake-email",
        idempotency_key="native-unsubscribe-0001",
        payload=payload,
    )
    duplicate = ingest_provider_event(
        db_session,
        owner_id=user.id,
        provider="fake-email",
        idempotency_key="native-unsubscribe-0001",
        payload=payload,
    )
    db_session.commit()

    # THEN: Body parsing is irrelevant and exactly one email-scoped hard restriction exists.
    assert duplicate.id == first.id
    restriction = db_session.query(models.ConsentRestriction).one()
    assert restriction.scope == RestrictionScope.CONTACT_POINT
    assert restriction.channel == Channel.EMAIL
    assert restriction.contact_point_id == point.id


def test_webhook_rejects_channel_that_does_not_match_contact_point(db_session):
    # GIVEN: An email contact point mislabeled as a LinkedIn webhook event.
    user, company, contact, point = _contact(db_session)
    from product_v2.runtime.events import ingest_provider_event
    from product_v2.schemas import WebhookEventCreate

    payload = WebhookEventCreate(
        channel=Channel.LINKEDIN,
        direction="inbound",
        event_type="unsubscribed",
        company_id=company.id,
        contact_id=contact.id,
        contact_point_id=point.id,
    )

    # WHEN/THEN: The mismatched event fails closed before an event or restriction is stored.
    with pytest.raises(ValueError, match="channel does not match contact point"):
        ingest_provider_event(
            db_session,
            owner_id=user.id,
            provider="fake-linkedin",
            idempotency_key="mismatched-channel-0001",
            payload=payload,
        )
    assert db_session.query(models.MessageEvent).count() == 0
    assert db_session.query(models.ConsentRestriction).count() == 0


def test_bounce_event_invalidates_contact_point_and_creates_alert(db_session):
    # GIVEN: A verified email contact point receives a Provider bounce event.
    user, company, contact, point = _contact(db_session)
    from product_v2.runtime.events import ingest_provider_event
    from product_v2.schemas import WebhookEventCreate

    payload = WebhookEventCreate(
        channel=Channel.EMAIL,
        direction="outbound",
        event_type="bounced",
        company_id=company.id,
        contact_id=contact.id,
        contact_point_id=point.id,
        provider_message_id="bounced-provider-message",
    )

    # WHEN: The event is ingested.
    ingest_provider_event(
        db_session,
        owner_id=user.id,
        provider="fake-email",
        idempotency_key="bounce-event-0001",
        payload=payload,
    )
    db_session.commit()

    # THEN: Future sends fail the non-overridable validity gate and sales gets an alert.
    assert point.verification_status.value == "invalid"
    assert point.availability_status.value == "unavailable"
    task = db_session.query(models.Task).filter_by(task_type=TaskType.DELIVERABILITY_ALERT).one()
    assert task.contact_id == contact.id
    assert task.metadata_json["contact_point_id"] == point.id


def test_webhook_idempotency_key_is_bound_to_one_provider_event(db_session):
    # GIVEN: One idempotency key has already been used for a delivered Provider event.
    user, company, contact, point = _contact(db_session)
    from product_v2.runtime.events import ingest_provider_event
    from product_v2.schemas import WebhookEventCreate

    first = WebhookEventCreate(
        channel=Channel.EMAIL,
        direction="outbound",
        event_type="delivered",
        company_id=company.id,
        contact_id=contact.id,
        contact_point_id=point.id,
        provider_event_id="provider-event-a",
        provider_message_id="provider-message-a",
    )
    ingest_provider_event(
        db_session,
        owner_id=user.id,
        provider="fake-email",
        idempotency_key="provider-header-key-0001",
        payload=first,
    )
    db_session.flush()

    # WHEN/THEN: Reusing the header key for another Provider event fails closed.
    conflicting = first.model_copy(
        update={"provider_event_id": "provider-event-b", "provider_message_id": "provider-message-b"}
    )
    with pytest.raises(ValueError, match="different Provider event"):
        ingest_provider_event(
            db_session,
            owner_id=user.id,
            provider="fake-email",
            idempotency_key="provider-header-key-0001",
            payload=conflicting,
        )
    assert db_session.query(models.MessageEvent).count() == 1
