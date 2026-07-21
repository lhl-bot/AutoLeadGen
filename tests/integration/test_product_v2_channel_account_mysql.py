"""MySQL current-read serialization for sender-account daily capacity."""

import os
from datetime import datetime, timezone
from pathlib import Path
from queue import Queue
from threading import Barrier, Thread
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker

import models as legacy
from product_v2 import models
from product_v2.enums import (
    CampaignLifecycle,
    CampaignRevisionStatus,
    Channel,
    ChannelAccountHealth,
    ContactPointVerificationStatus,
    EnrollmentStatus,
)
from product_v2.services.channel_accounts import lock_and_reserve_attempt_account


MYSQL_URL = os.environ.get("PRODUCT_V2_MYSQL_TEST_URL")
pytestmark = pytest.mark.mysql


def _session_factory(monkeypatch):
    if not MYSQL_URL:
        pytest.skip("PRODUCT_V2_MYSQL_TEST_URL is not configured")
    parsed = make_url(MYSQL_URL)
    if not parsed.drivername.startswith("mysql"):
        pytest.fail("PRODUCT_V2_MYSQL_TEST_URL must use MySQL")
    if not (parsed.database or "").endswith("_test"):
        pytest.fail("Refusing to test against a database whose name does not end in _test")
    monkeypatch.setenv("DATABASE_URL", MYSQL_URL)
    monkeypatch.setenv("AUTOLEADGEN_ENV", "test")
    command.upgrade(Config(str(Path(__file__).parents[2] / "alembic.ini")), "head")
    engine = create_engine(MYSQL_URL, pool_pre_ping=True)
    return engine, sessionmaker(bind=engine, autoflush=False)


def _seed(Session):
    suffix = uuid4().hex[:12]
    db = Session()
    owner = legacy.User(
        username=f"account-capacity-{suffix}",
        hashed_password="x",
        is_active=True,
    )
    db.add(owner)
    db.flush()
    account = models.ChannelAccount(
        owner_id=owner.id,
        channel=Channel.EMAIL,
        provider="fake-email",
        provider_account_id=f"local-fake:{owner.id}:email",
        enabled=True,
        health_status=ChannelAccountHealth.HEALTHY,
        health_checked_at=datetime.now(timezone.utc),
        daily_limit=1,
        timezone="UTC",
    )
    campaign = models.Campaign(
        owner_id=owner.id,
        name=f"Capacity {suffix}",
        lifecycle=CampaignLifecycle.RUNNING,
        published_revision_number=1,
    )
    db.add_all([account, campaign])
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
    )
    db.add(step)
    db.flush()
    attempt_ids = []
    for index in range(2):
        company = models.Company(
            owner_id=owner.id,
            name=f"Capacity company {index} {suffix}",
            normalized_domain=f"capacity-{index}-{suffix}.example",
        )
        db.add(company)
        db.flush()
        contact = models.Contact(
            owner_id=owner.id,
            company_id=company.id,
            full_name=f"Buyer {index}",
            timezone="UTC",
        )
        db.add(contact)
        db.flush()
        point = models.ContactPoint(
            owner_id=owner.id,
            company_id=company.id,
            contact_id=contact.id,
            channel=Channel.EMAIL,
            value=f"capacity-{index}-{suffix}@example.com",
            normalized_value=f"capacity-{index}-{suffix}@example.com",
            verification_status=ContactPointVerificationStatus.VALID,
        )
        enrollment = models.Enrollment(
            owner_id=owner.id,
            campaign_id=campaign.id,
            campaign_revision_id=revision.id,
            company_id=company.id,
            contact_id=contact.id,
            status=EnrollmentStatus.ACTIVE,
        )
        db.add_all([point, enrollment])
        db.flush()
        attempt = models.OutreachAttempt(
            owner_id=owner.id,
            campaign_id=campaign.id,
            enrollment_id=enrollment.id,
            sequence_step_id=step.id,
            contact_point_id=point.id,
            channel_account_id=account.id,
            channel=Channel.EMAIL,
            idempotency_key=f"account-capacity-{suffix}-{index}",
        )
        db.add(attempt)
        db.flush()
        attempt_ids.append(attempt.id)
    db.commit()
    owner_id = owner.id
    db.close()
    return owner_id, attempt_ids


def _cleanup(Session, owner_id):
    db = Session()
    try:
        for model in (
            models.AuditEvent,
            models.Task,
            models.SafetyLock,
            models.ProviderCostEvent,
            models.MessageEvent,
            models.OutreachAttempt,
            models.Enrollment,
            models.SequenceStep,
            models.CampaignRevision,
            models.Campaign,
            models.ContactPoint,
            models.Contact,
            models.Company,
            models.ChannelAccount,
            models.OwnerMigrationState,
        ):
            db.query(model).filter_by(owner_id=owner_id).delete(synchronize_session=False)
        db.query(legacy.User).filter_by(id=owner_id).delete(synchronize_session=False)
        db.commit()
    finally:
        db.close()


def test_mysql_two_attempts_cannot_oversubscribe_one_account_daily_slot(monkeypatch):
    # GIVEN: Two worker transactions target one healthy account with one slot.
    engine, Session = _session_factory(monkeypatch)
    owner_id, attempt_ids = _seed(Session)
    barrier = Barrier(2)
    results = Queue()

    def reserve(attempt_id):
        db = Session()
        try:
            attempt = db.get(models.OutreachAttempt, attempt_id)
            barrier.wait(timeout=10)
            decision = lock_and_reserve_attempt_account(
                db,
                attempt=attempt,
                connector_provider="fake-email",
            )
            db.commit()
            results.put((attempt_id, decision.allowed, tuple(decision.blockers)))
        except Exception as exc:
            db.rollback()
            results.put((attempt_id, False, (f"{type(exc).__name__}:{exc}",)))
        finally:
            db.close()

    threads = [Thread(target=reserve, args=(attempt_id,), daemon=True) for attempt_id in attempt_ids]
    try:
        # WHEN: both transactions pass their old snapshots and contend on the
        # same account row-lock/current-read capacity fence.
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=15)
        assert all(not thread.is_alive() for thread in threads), "capacity workers deadlocked"
        outcomes = [results.get_nowait() for _ in threads]

        # THEN: exactly one durable reservation wins; the waiter sees exhaustion.
        assert sum(1 for _, allowed, _ in outcomes if allowed) == 1, outcomes
        assert any(
            "channel_account_capacity_exhausted" in blockers
            for _, allowed, blockers in outcomes
            if not allowed
        ), outcomes
        verify = Session()
        try:
            reserved = verify.query(models.OutreachAttempt).filter(
                models.OutreachAttempt.id.in_(attempt_ids),
                models.OutreachAttempt.capacity_reserved_at.isnot(None),
            ).count()
            assert reserved == 1
        finally:
            verify.close()
    finally:
        _cleanup(Session, owner_id)
        engine.dispose()
