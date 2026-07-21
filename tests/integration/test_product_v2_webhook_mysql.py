"""MySQL concurrency acceptance for replay-safe Provider webhooks."""
from __future__ import annotations

import json
import os
from threading import Barrier, Lock, Thread
import time
from uuid import uuid4

from alembic import command
from alembic.config import Config
from pathlib import Path
import pytest
from sqlalchemy import create_engine
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker

import models as legacy
from product_v2 import models
from product_v2.enums import Channel, ContactPointVerificationStatus, TaskType
from product_v2.runtime.webhook_ingest import ingest_verified_webhook
from product_v2.webhook_security import sign_webhook, verify_webhook


MYSQL_URL = os.environ.get("PRODUCT_V2_MYSQL_TEST_URL")
pytestmark = pytest.mark.mysql


def _mysql_url() -> str:
    if not MYSQL_URL:
        pytest.skip("PRODUCT_V2_MYSQL_TEST_URL is not configured")
    url = make_url(MYSQL_URL)
    if not url.drivername.startswith("mysql"):
        pytest.fail("PRODUCT_V2_MYSQL_TEST_URL must use MySQL")
    if not (url.database or "").endswith("_test"):
        pytest.fail("Refusing to test against a database whose name does not end in _test")
    return MYSQL_URL


def test_mysql_concurrent_identical_webhooks_create_one_event_task_and_audit(monkeypatch):
    # GIVEN: An isolated MySQL owner and one byte-identical authenticated
    # unknown Provider event delivered to two independent database sessions.
    url = _mysql_url()
    monkeypatch.setenv("DATABASE_URL", url)
    monkeypatch.setenv("AUTOLEADGEN_ENV", "test")
    command.upgrade(Config(str(Path(__file__).parents[2] / "alembic.ini")), "head")
    engine = create_engine(url, pool_pre_ping=True)
    Session = sessionmaker(bind=engine, autoflush=False)
    suffix = uuid4().hex[:12]
    seed = Session()
    owner = legacy.User(
        username=f"mysql-webhook-{suffix}",
        hashed_password="x",
        is_active=True,
    )
    seed.add(owner)
    seed.flush()
    company = models.Company(
        owner_id=owner.id,
        name=f"Webhook company {suffix}",
        normalized_domain=f"webhook-{suffix}.example",
    )
    seed.add(company)
    seed.flush()
    contact = models.Contact(
        owner_id=owner.id,
        company_id=company.id,
        full_name="MySQL Webhook Contact",
        timezone="UTC",
    )
    seed.add(contact)
    seed.flush()
    point = models.ContactPoint(
        owner_id=owner.id,
        company_id=company.id,
        contact_id=contact.id,
        channel=Channel.EMAIL,
        value=f"webhook-{suffix}@example.com",
        normalized_value=f"webhook-{suffix}@example.com",
        verification_status=ContactPointVerificationStatus.VALID,
    )
    seed.add(point)
    seed.commit()
    owner_id = owner.id
    company_id = company.id
    contact_id = contact.id
    point_id = point.id
    seed.close()

    event_id = f"mysql-webhook-{suffix}"
    provider = "fake-email"
    raw_body = json.dumps(
        {
            "channel": "email",
            "direction": "outbound",
            "event_type": "provider.experimental.mysql",
            "company_id": company_id,
            "contact_id": contact_id,
            "contact_point_id": point_id,
            "metadata_json": {"source": "mysql-concurrency-gwt"},
        },
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    timestamp = int(time.time())
    secret = "mysql-concurrent-webhook-secret-material"
    signature = sign_webhook(
        secret=secret,
        provider=provider,
        owner_id=owner_id,
        timestamp=timestamp,
        event_id=event_id,
        raw_body=raw_body,
    )
    verification = verify_webhook(
        provider=provider,
        owner_id=owner_id,
        timestamp_header=str(timestamp),
        event_id_header=event_id,
        signature_header=signature,
        raw_body=raw_body,
        secret=secret,
    )
    barrier = Barrier(3)
    result_lock = Lock()
    results: list[tuple[int, bool]] = []
    errors: list[BaseException] = []

    def deliver() -> None:
        session = Session()
        try:
            barrier.wait(timeout=10)
            event, was_replay = ingest_verified_webhook(
                session,
                verification=verification,
                idempotency_key=event_id,
                raw_body=raw_body,
            )
            with result_lock:
                results.append((event.id, was_replay))
        except BaseException as exc:  # surfaced in the parent assertion
            with result_lock:
                errors.append(exc)
        finally:
            session.close()

    first = Thread(target=deliver, name="mysql-webhook-a")
    second = Thread(target=deliver, name="mysql-webhook-b")
    first.start()
    second.start()

    # WHEN: Both transactions are released concurrently.
    barrier.wait(timeout=10)
    first.join(timeout=20)
    second.join(timeout=20)

    verify = Session()
    try:
        # THEN: One delivery owns the write, the other is an exact replay, and
        # no duplicate event, reconciliation task, or audit side effect exists.
        assert not first.is_alive()
        assert not second.is_alive()
        assert errors == []
        assert len(results) == 2
        assert results[0][0] == results[1][0]
        assert sorted(was_replay for _, was_replay in results) == [False, True]
        assert verify.query(models.MessageEvent).filter_by(owner_id=owner_id).count() == 1
        assert verify.query(models.Task).filter_by(
            owner_id=owner_id,
            task_type=TaskType.RECONCILIATION,
        ).count() == 1
        assert verify.query(models.AuditEvent).filter_by(
            owner_id=owner_id,
            action="message_event.unknown_reconciliation_requested",
        ).count() == 1
        assert verify.query(models.AuditEvent).filter_by(
            owner_id=owner_id,
            action="message_event.ingested",
        ).count() == 1
    finally:
        # Keep the shared isolated test database reusable by removing only this
        # test's owner-scoped graph in reverse FK order.
        verify.query(models.AuditEvent).filter_by(owner_id=owner_id).delete(synchronize_session=False)
        verify.query(models.Task).filter_by(owner_id=owner_id).delete(synchronize_session=False)
        verify.query(models.MessageEvent).filter_by(owner_id=owner_id).delete(synchronize_session=False)
        verify.query(models.ContactPoint).filter_by(owner_id=owner_id).delete(synchronize_session=False)
        verify.query(models.Contact).filter_by(owner_id=owner_id).delete(synchronize_session=False)
        verify.query(models.Company).filter_by(owner_id=owner_id).delete(synchronize_session=False)
        verify.query(models.ChannelAccount).filter_by(owner_id=owner_id).delete(synchronize_session=False)
        verify.query(legacy.User).filter_by(id=owner_id).delete(synchronize_session=False)
        verify.commit()
        verify.close()
        engine.dispose()
