"""GWT acceptance tests for authenticated Product V2 Provider webhooks."""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import json
import time

import httpx
import pytest

import models as legacy
from product_v2 import models
from product_v2.enums import Channel, ContactPointVerificationStatus, MessageEventType, TaskType
from product_v2.webhook_security import WebhookSecurityError, sign_webhook, verify_webhook


SECRET = "gwt-webhook-secret-material-with-32-bytes"
PROVIDER = "fake-email"


def _subject(db):
    owner = legacy.User(username=f"webhook-owner-{time.time_ns()}", hashed_password="x", is_active=True)
    db.add(owner)
    db.flush()
    company = models.Company(
        owner_id=owner.id,
        name="Webhook Buyer",
        normalized_domain=f"webhook-{owner.id}.example",
    )
    db.add(company)
    db.flush()
    contact = models.Contact(
        owner_id=owner.id,
        company_id=company.id,
        full_name="Webhook Contact",
        timezone="UTC",
    )
    db.add(contact)
    db.flush()
    point = models.ContactPoint(
        owner_id=owner.id,
        company_id=company.id,
        contact_id=contact.id,
        channel=Channel.EMAIL,
        value=f"webhook-{owner.id}@example.com",
        normalized_value=f"webhook-{owner.id}@example.com",
        verification_status=ContactPointVerificationStatus.VALID,
    )
    db.add(point)
    db.commit()
    return owner, company, contact, point


def _body(company, contact, point, **updates):
    payload = {
        "channel": "email",
        "direction": "outbound",
        "event_type": "delivered",
        "company_id": company.id,
        "contact_id": contact.id,
        "contact_point_id": point.id,
        "provider_message_id": "provider-message-gwt",
        "metadata_json": {"provider_region": "local-fake"},
    }
    payload.update(updates)
    return json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")


def _headers(*, owner_id, event_id, raw_body, timestamp=None, secret=SECRET):
    timestamp = int(time.time()) if timestamp is None else timestamp
    return {
        "Content-Type": "application/json",
        "Idempotency-Key": event_id,
        "X-AutoLeadGen-Webhook-Timestamp": str(timestamp),
        "X-AutoLeadGen-Webhook-Event-Id": event_id,
        "X-AutoLeadGen-Webhook-Signature": sign_webhook(
            secret=secret,
            provider=PROVIDER,
            owner_id=owner_id,
            timestamp=timestamp,
            event_id=event_id,
            raw_body=raw_body,
        ),
    }


async def _post(client, *, owner_id, raw_body, headers):
    return await client.post(
        f"/api/v2/webhooks/{owner_id}/{PROVIDER}/events",
        content=raw_body,
        headers=headers,
    )


def test_webhook_openapi_documents_raw_contract_without_user_jwt():
    # GIVEN/WHEN: The generated V2 OpenAPI contract is inspected.
    import main

    operation = main.app.openapi()["paths"][
        "/api/v2/webhooks/{owner_id}/{provider}/events"
    ]["post"]

    # THEN: Provider JSON remains documented despite manual byte-exact parsing,
    # and the Provider ingress does not advertise a user JWT requirement.
    request_schema = operation["requestBody"]["content"]["application/json"]["schema"]
    assert request_schema["title"] == "WebhookEventCreate"
    assert "event_type" in request_schema["properties"]
    assert operation.get("security") in (None, [])
    assert "413" in operation["responses"]
    owner_parameter = next(
        parameter
        for parameter in operation["parameters"]
        if parameter["in"] == "path" and parameter["name"] == "owner_id"
    )
    assert owner_parameter["schema"]["exclusiveMinimum"] == 0


def test_webhook_hmac_is_over_exact_raw_body_and_enforces_timestamp(db_session, monkeypatch):
    # GIVEN: A Provider secret configured outside the database and a valid raw
    # JSON delivery signed for one owner, Provider, timestamp, and event id.
    owner, company, contact, point = _subject(db_session)
    monkeypatch.setenv("PRODUCT_V2_WEBHOOK_SECRET_FAKE_EMAIL", SECRET)
    import main

    valid_body = _body(company, contact, point)
    valid_headers = _headers(
        owner_id=owner.id,
        event_id="gwt-exact-body-0001",
        raw_body=valid_body,
    )
    tampered_body = valid_body.replace(b"delivered", b"failed")
    stale_timestamp = int(datetime.now(timezone.utc).timestamp()) - 301
    stale_body = _body(company, contact, point, provider_message_id="stale-message")
    stale_headers = _headers(
        owner_id=owner.id,
        event_id="gwt-stale-time-0001",
        raw_body=stale_body,
        timestamp=stale_timestamp,
    )

    async def flow():
        transport = httpx.ASGITransport(app=main.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            # WHEN: The exact bytes, mutated bytes, and an expired signed request
            # are submitted without any user JWT.
            valid = await _post(
                client,
                owner_id=owner.id,
                raw_body=valid_body,
                headers=valid_headers,
            )
            tampered = await _post(
                client,
                owner_id=owner.id,
                raw_body=tampered_body,
                headers=valid_headers,
            )
            stale = await _post(
                client,
                owner_id=owner.id,
                raw_body=stale_body,
                headers=stale_headers,
            )

            # THEN: Only byte-identical, timely evidence crosses the durable
            # boundary and authentication errors expose no secret or owner data.
            assert valid.status_code == 201, valid.text
            assert tampered.status_code == 401, tampered.text
            assert tampered.json()["detail"]["code"] == "WEBHOOK_AUTHENTICATION_FAILED"
            assert stale.status_code == 401, stale.text
            assert stale.json()["detail"]["code"] == "WEBHOOK_TIMESTAMP_OUTSIDE_TOLERANCE"
            assert SECRET not in tampered.text
            assert str(owner.id) not in tampered.text

    asyncio.run(flow())
    assert db_session.query(models.MessageEvent).count() == 1


def test_webhook_hmac_canonical_input_binds_every_routing_and_replay_field():
    # GIVEN: One signature over the documented canonical Provider, owner,
    # timestamp, event id, and exact raw bytes.
    timestamp = int(time.time())
    raw_body = b'{"event_type":"delivered"}'
    signature = sign_webhook(
        secret=SECRET,
        provider=PROVIDER,
        owner_id=42,
        timestamp=timestamp,
        event_id="gwt-canonical-0001",
        raw_body=raw_body,
    )
    base = {
        "provider": PROVIDER,
        "owner_id": 42,
        "timestamp_header": str(timestamp),
        "event_id_header": "gwt-canonical-0001",
        "signature_header": signature,
        "raw_body": raw_body,
        "secret": SECRET,
    }

    # WHEN/THEN: Reusing the signature with any independently meaningful field
    # changed fails authentication.
    mutations = (
        {"provider": "fake-linkedin"},
        {"owner_id": 43},
        {"timestamp_header": str(timestamp + 1)},
        {"event_id_header": "gwt-canonical-0002"},
        {"raw_body": b'{"event_type":"failed"}'},
    )
    for mutation in mutations:
        with pytest.raises(WebhookSecurityError) as conflict:
            verify_webhook(**{**base, **mutation})
        assert conflict.value.code == "WEBHOOK_AUTHENTICATION_FAILED"


def test_webhook_owner_secret_configuration_is_not_enumerable(db_session, monkeypatch):
    # GIVEN: Only one owner's Provider secret exists. A caller has a
    # syntactically valid signature header but does not know that secret.
    owner, company, contact, point = _subject(db_session)
    monkeypatch.delenv("PRODUCT_V2_WEBHOOK_SECRET", raising=False)
    monkeypatch.delenv("PRODUCT_V2_WEBHOOK_SECRET_FAKE_EMAIL", raising=False)
    monkeypatch.setenv(
        f"PRODUCT_V2_WEBHOOK_SECRET_OWNER_{owner.id}_FAKE_EMAIL",
        SECRET,
    )
    import main

    raw_body = _body(company, contact, point)
    unknown_owner_id = owner.id + 100_000
    known_headers = _headers(
        owner_id=owner.id,
        event_id="gwt-owner-probe-0001",
        raw_body=raw_body,
        secret="wrong-webhook-secret-material-with-32-bytes",
    )
    unknown_headers = _headers(
        owner_id=unknown_owner_id,
        event_id="gwt-owner-probe-0001",
        raw_body=raw_body,
        secret="wrong-webhook-secret-material-with-32-bytes",
    )

    async def flow():
        transport = httpx.ASGITransport(app=main.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            # WHEN: The unauthenticated probe targets configured and
            # unconfigured owner ids.
            known = await _post(
                client,
                owner_id=owner.id,
                raw_body=raw_body,
                headers=known_headers,
            )
            unknown = await _post(
                client,
                owner_id=unknown_owner_id,
                raw_body=raw_body,
                headers=unknown_headers,
            )

            # THEN: Status and public body are identical and disclose neither
            # owner existence nor owner-specific secret configuration.
            assert known.status_code == 401
            assert unknown.status_code == 401
            assert known.json() == unknown.json() == {
                "detail": {
                    "code": "WEBHOOK_AUTHENTICATION_FAILED",
                    "message": "Webhook authentication failed",
                }
            }

    asyncio.run(flow())
    assert db_session.query(models.MessageEvent).count() == 0


def test_webhook_rejects_declared_and_streamed_oversize_bodies_before_auth_or_db(
    db_session,
    monkeypatch,
):
    # GIVEN: A deliberately tiny local byte limit and both a declared-size and
    # chunked request that exceed it.
    owner, _company, _contact, _point = _subject(db_session)
    monkeypatch.setenv("PRODUCT_V2_WEBHOOK_MAX_BODY_BYTES", "128")
    monkeypatch.setenv("PRODUCT_V2_WEBHOOK_SECRET_FAKE_EMAIL", SECRET)
    import main

    oversized = b'{' + b'"padding":"' + (b"x" * 256) + b'"}'
    headers = _headers(
        owner_id=owner.id,
        event_id="gwt-body-limit-0001",
        raw_body=oversized,
    )

    async def chunks():
        yield oversized[:80]
        yield oversized[80:]

    async def flow():
        transport = httpx.ASGITransport(app=main.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            # WHEN: Content-Length announces the oversized body and then an
            # unbounded/chunked sender omits Content-Length.
            declared = await _post(
                client,
                owner_id=owner.id,
                raw_body=oversized,
                headers=headers,
            )
            chunked_headers = dict(headers)
            chunked_headers.pop("Content-Length", None)
            chunked = await client.post(
                f"/api/v2/webhooks/{owner.id}/{PROVIDER}/events",
                content=chunks(),
                headers=chunked_headers,
            )

            # THEN: Both paths fail before authentication, JSON parsing, or any
            # durable business side effect.
            assert declared.status_code == 413, declared.text
            assert chunked.status_code == 413, chunked.text
            assert declared.json()["detail"]["code"] == "WEBHOOK_PAYLOAD_TOO_LARGE"
            assert chunked.json()["detail"]["code"] == "WEBHOOK_PAYLOAD_TOO_LARGE"

    asyncio.run(flow())
    assert db_session.query(models.MessageEvent).count() == 0
    assert db_session.query(models.Task).count() == 0
    assert db_session.query(models.AuditEvent).count() == 0


def test_webhook_event_id_is_idempotent_but_conflicting_replay_fails(db_session, monkeypatch):
    # GIVEN: One authenticated Provider event and an independently signed body
    # trying to reuse the same durable event/idempotency identity.
    owner, company, contact, point = _subject(db_session)
    monkeypatch.setenv("PRODUCT_V2_WEBHOOK_SECRET_FAKE_EMAIL", SECRET)
    import main

    event_id = "gwt-replay-event-0001"
    first_body = _body(company, contact, point)
    first_headers = _headers(owner_id=owner.id, event_id=event_id, raw_body=first_body)
    mismatched_idempotency_headers = dict(first_headers)
    mismatched_idempotency_headers["Idempotency-Key"] = "different-idempotency-0001"
    conflict_body = _body(company, contact, point, provider_message_id="different-message")
    conflict_headers = _headers(owner_id=owner.id, event_id=event_id, raw_body=conflict_body)

    async def flow():
        transport = httpx.ASGITransport(app=main.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            # WHEN: The same bytes are retried and then different bytes reuse the
            # signed event id.
            mismatch = await _post(
                client,
                owner_id=owner.id,
                raw_body=first_body,
                headers=mismatched_idempotency_headers,
            )
            first = await _post(client, owner_id=owner.id, raw_body=first_body, headers=first_headers)
            duplicate = await _post(client, owner_id=owner.id, raw_body=first_body, headers=first_headers)
            conflict = await _post(client, owner_id=owner.id, raw_body=conflict_body, headers=conflict_headers)

            # THEN: The exact replay returns the original event while the
            # conflicting replay fails closed.
            assert mismatch.status_code == 409, mismatch.text
            assert mismatch.json()["detail"]["code"] == "WEBHOOK_IDEMPOTENCY_MISMATCH"
            assert first.status_code == 201, first.text
            assert duplicate.status_code == 201, duplicate.text
            assert duplicate.json()["id"] == first.json()["id"]
            assert conflict.status_code == 409, conflict.text
            assert conflict.json()["detail"]["code"] == "WEBHOOK_REPLAY_CONFLICT"

    asyncio.run(flow())
    assert db_session.query(models.MessageEvent).count() == 1
    assert db_session.query(models.AuditEvent).filter_by(action="message_event.ingested").count() == 1


def test_unknown_webhook_creates_one_safe_reconciliation_record(db_session, monkeypatch, caplog):
    # GIVEN: A correctly signed but Provider-specific event type containing
    # credential-shaped metadata that is unsafe to persist.
    owner, company, contact, point = _subject(db_session)
    monkeypatch.setenv("PRODUCT_V2_WEBHOOK_SECRET_FAKE_EMAIL", SECRET)
    import main

    event_id = "gwt-unknown-event-0001"
    raw_body = _body(
        company,
        contact,
        point,
        event_type="provider.experimental.delivery_state",
        provider_message_id=SECRET,
        subject=SECRET,
        body=SECRET,
        metadata_json={
            "provider_region": "local-fake",
            "webhook_secret": SECRET,
            "nested": {"Authorization": f"Bearer {SECRET}"},
            "neutral_exact_copy": SECRET,
            "neutral_bearer_copy": f"Bearer {SECRET}",
            "neutral_signature_copy": f"v1={'a' * 64}",
            "neutral_openai_copy": "sk-standaloneexample123",
            "neutral_github_copy": "ghp_abcdefghijklmnop",
            "neutral_slack_copy": "xoxb-abcdefghij1234",
            "neutral_aws_copy": "AKIA" + "ABCDEFGHIJKLMNOP",
        },
    )
    headers = _headers(owner_id=owner.id, event_id=event_id, raw_body=raw_body)

    async def flow():
        transport = httpx.ASGITransport(app=main.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            # WHEN: The unknown event is delivered twice.
            first = await _post(client, owner_id=owner.id, raw_body=raw_body, headers=headers)
            duplicate = await _post(client, owner_id=owner.id, raw_body=raw_body, headers=headers)

            # THEN: It is immutable UNKNOWN evidence and both deliveries resolve
            # to one event rather than applying guessed business side effects.
            assert first.status_code == 201, first.text
            assert duplicate.status_code == 201, duplicate.text
            assert first.json()["event_type"] == MessageEventType.UNKNOWN.value
            assert duplicate.json()["id"] == first.json()["id"]

    asyncio.run(flow())

    event = db_session.query(models.MessageEvent).one()
    task = db_session.query(models.Task).filter_by(task_type=TaskType.RECONCILIATION).one()
    audit = db_session.query(models.AuditEvent).filter_by(
        action="message_event.unknown_reconciliation_requested"
    ).one()
    assert event.metadata_json["provider_event_type"] == "provider.experimental.delivery_state"
    assert event.metadata_json["webhook_verification"]["verified"] is True
    assert event.metadata_json["webhook_secret"] == "[REDACTED]"
    assert event.metadata_json["nested"]["Authorization"] == "[REDACTED]"
    assert event.metadata_json["neutral_exact_copy"] == "[REDACTED]"
    assert event.metadata_json["neutral_bearer_copy"] == "[REDACTED]"
    assert event.metadata_json["neutral_signature_copy"] == "[REDACTED]"
    assert event.metadata_json["neutral_openai_copy"] == "[REDACTED]"
    assert event.metadata_json["neutral_github_copy"] == "[REDACTED]"
    assert event.metadata_json["neutral_slack_copy"] == "[REDACTED]"
    assert event.metadata_json["neutral_aws_copy"] == "[REDACTED]"
    assert event.provider_message_id == "[REDACTED]"
    assert event.subject == "[REDACTED]"
    assert event.body == "[REDACTED]"
    assert task.metadata_json["message_event_id"] == event.id
    assert audit.after_data["reconciliation_task_id"] == task.id
    assert db_session.query(models.ReplyAssessment).count() == 0
    assert db_session.query(models.ConsentRestriction).count() == 0

    durable_projection = json.dumps(
        {
            "event": event.metadata_json,
            "task": task.metadata_json,
            "audit": audit.after_data,
        },
        sort_keys=True,
    )
    assert SECRET not in durable_projection
    assert SECRET not in caplog.text
    assert "X-AutoLeadGen-Webhook-Signature" not in durable_projection
