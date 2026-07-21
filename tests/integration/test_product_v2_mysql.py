"""MySQL 8-only migration and claim checks.

Set PRODUCT_V2_MYSQL_TEST_URL to an isolated database whose name ends in
``_test``.  The local Compose environment can provide this URL; the suite skips
cleanly when Docker/MySQL is unavailable.
"""
import os
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from threading import Event, Thread
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker

import models as legacy
from product_v2 import models
from product_v2.connectors import build_local_registry
from product_v2.enums import (
    AttemptStatus,
    CampaignLifecycle,
    CampaignRevisionStatus,
    Channel,
    ContactPointVerificationStatus,
    EnrollmentStatus,
    JobStatus,
    ProviderCostStatus,
)
from product_v2.runtime.outbound import execute_attempt
from product_v2.runtime.queue import (
    LeaseFenceLost,
    claim_attempt,
    claim_job,
    complete_job,
    lease_fence,
)
from product_v2.services.domain import enqueue_job
from product_v2.settings_policy import SETTINGS_ACTION, SETTINGS_ENTITY, global_budget_snapshot


MYSQL_URL = os.environ.get("PRODUCT_V2_MYSQL_TEST_URL")
pytestmark = pytest.mark.mysql


def _mysql_url():
    if not MYSQL_URL:
        pytest.skip("PRODUCT_V2_MYSQL_TEST_URL is not configured")
    url = make_url(MYSQL_URL)
    if not url.drivername.startswith("mysql"):
        pytest.fail("PRODUCT_V2_MYSQL_TEST_URL must use MySQL")
    if not (url.database or "").endswith("_test"):
        pytest.fail("Refusing to test against a database whose name does not end in _test")
    return MYSQL_URL


def test_mysql_empty_upgrade_repeat_and_skip_locked_claim(monkeypatch):
    url = _mysql_url()
    monkeypatch.setenv("DATABASE_URL", url)
    monkeypatch.setenv("AUTOLEADGEN_ENV", "test")
    config = Config(str(Path(__file__).parents[2] / "alembic.ini"))

    command.upgrade(config, "head")
    command.upgrade(config, "head")
    engine = create_engine(url, pool_pre_ping=True)
    assert set(models.V2_TABLE_NAMES).issubset(set(inspect(engine).get_table_names()))
    with engine.connect() as connection:
        assert connection.execute(text("SELECT version_num FROM alembic_version")).scalar() == (
            ScriptDirectory.from_config(config).get_current_head()
        )

    Session = sessionmaker(bind=engine, autoflush=False)
    seed = Session()
    user = legacy.User(username="mysql-v2-worker", hashed_password="x", is_active=True)
    seed.add(user)
    seed.flush()
    first = enqueue_job(seed, owner_id=user.id, job_type="first", idempotency_key="mysql-job-first", queue="campaign", priority=200)
    second = enqueue_job(seed, owner_id=user.id, job_type="second", idempotency_key="mysql-job-second", queue="campaign", priority=100)
    seed.commit()
    user_id = user.id
    first_id = first.id
    second_id = second.id
    seed.close()

    worker_a = Session()
    worker_b = Session()
    try:
        claimed_a = claim_job(worker_a, worker_name="mysql-a", queues=("campaign",))
        claimed_b = claim_job(worker_b, worker_name="mysql-b", queues=("campaign",))
        assert claimed_a.id == first_id
        assert claimed_b.id == second_id
        assert claimed_a.id != claimed_b.id
        assert claimed_a.status == JobStatus.CLAIMED
        assert claimed_b.status == JobStatus.CLAIMED
    finally:
        worker_a.rollback()
        worker_b.rollback()
        cleanup = Session()
        cleanup.query(models.AutomationJob).filter(models.AutomationJob.owner_id == user_id).delete(synchronize_session=False)
        cleanup.query(models.ChannelAccount).filter_by(owner_id=user_id).delete(synchronize_session=False)
        cleanup.query(legacy.User).filter(legacy.User.id == user_id).delete(synchronize_session=False)
        cleanup.commit()
        cleanup.close()


def test_mysql_attempt_skip_locked_claims_distinct_rows(monkeypatch):
    # GIVEN: Two eligible Attempts in the same running Campaign and two fresh
    # MySQL worker sessions whose claim transactions remain uncommitted.
    url = _mysql_url()
    monkeypatch.setenv("DATABASE_URL", url)
    monkeypatch.setenv("AUTOLEADGEN_ENV", "test")
    command.upgrade(Config(str(Path(__file__).parents[2] / "alembic.ini")), "head")
    engine = create_engine(url, pool_pre_ping=True)
    Session = sessionmaker(bind=engine, autoflush=False)
    suffix = uuid4().hex[:12]
    claim_time = datetime.now(timezone.utc).replace(microsecond=0)
    seed = Session()
    user = legacy.User(username=f"mysql-attempt-claim-{suffix}", hashed_password="x", is_active=True)
    seed.add(user)
    seed.flush()
    campaign = models.Campaign(
        owner_id=user.id,
        name=f"Attempt claim {suffix}",
        lifecycle=CampaignLifecycle.RUNNING,
        priority=200,
        published_revision_number=1,
    )
    seed.add(campaign)
    seed.flush()
    revision = models.CampaignRevision(
        owner_id=user.id,
        campaign_id=campaign.id,
        revision_number=1,
        status=CampaignRevisionStatus.PUBLISHED,
    )
    seed.add(revision)
    seed.flush()
    step = models.SequenceStep(
        owner_id=user.id,
        campaign_revision_id=revision.id,
        position=1,
        channel=Channel.EMAIL,
    )
    seed.add(step)
    seed.flush()
    attempt_ids = []
    for index in range(2):
        company = models.Company(
            owner_id=user.id,
            name=f"Claim company {suffix} {index}",
            normalized_domain=f"claim-{suffix}-{index}.example",
        )
        seed.add(company)
        seed.flush()
        contact = models.Contact(
            owner_id=user.id,
            company_id=company.id,
            full_name=f"Claim buyer {index}",
            timezone="UTC",
        )
        seed.add(contact)
        seed.flush()
        point = models.ContactPoint(
            owner_id=user.id,
            company_id=company.id,
            contact_id=contact.id,
            channel=Channel.EMAIL,
            value=f"claim-{suffix}-{index}@example.com",
            normalized_value=f"claim-{suffix}-{index}@example.com",
            verification_status=ContactPointVerificationStatus.VALID,
        )
        enrollment = models.Enrollment(
            owner_id=user.id,
            campaign_id=campaign.id,
            campaign_revision_id=revision.id,
            company_id=company.id,
            contact_id=contact.id,
            status=EnrollmentStatus.ACTIVE,
            scheduled_at=claim_time - timedelta(seconds=1),
        )
        seed.add_all([point, enrollment])
        seed.flush()
        attempt = models.OutreachAttempt(
            owner_id=user.id,
            campaign_id=campaign.id,
            enrollment_id=enrollment.id,
            sequence_step_id=step.id,
            contact_point_id=point.id,
            channel=Channel.EMAIL,
            idempotency_key=f"mysql-attempt-claim-{suffix}-{index}",
            scheduled_at=claim_time - timedelta(seconds=1),
        )
        seed.add(attempt)
        seed.flush()
        attempt_ids.append(attempt.id)
    seed.commit()
    owner_id = user.id
    seed.close()

    worker_a = Session()
    worker_b = Session()
    try:
        # WHEN: Both workers claim without committing the first worker's row.
        claimed_a = claim_attempt(
            worker_a,
            worker_name="mysql-attempt-a",
            now=claim_time,
        )
        claimed_b = claim_attempt(
            worker_b,
            worker_name="mysql-attempt-b",
            now=claim_time,
        )

        # THEN: PK-level SKIP LOCKED lets the second worker claim the other
        # eligible Attempt despite the shared Campaign row.
        assert claimed_a is not None
        assert claimed_b is not None
        assert [claimed_a.id, claimed_b.id] == attempt_ids
        assert claimed_a.id != claimed_b.id
        assert claimed_a.status == AttemptStatus.CLAIMED
        assert claimed_b.status == AttemptStatus.CLAIMED
        assert claimed_a.claimed_by == "mysql-attempt-a"
        assert claimed_b.claimed_by == "mysql-attempt-b"
    finally:
        worker_a.rollback()
        worker_b.rollback()
        worker_a.close()
        worker_b.close()
        cleanup = Session()
        cleanup.query(models.OutreachAttempt).filter_by(owner_id=owner_id).delete(synchronize_session=False)
        cleanup.query(models.Enrollment).filter_by(owner_id=owner_id).delete(synchronize_session=False)
        cleanup.query(models.SequenceStep).filter_by(owner_id=owner_id).delete(synchronize_session=False)
        cleanup.query(models.CampaignRevision).filter_by(owner_id=owner_id).delete(synchronize_session=False)
        cleanup.query(models.Campaign).filter_by(owner_id=owner_id).delete(synchronize_session=False)
        cleanup.query(models.ContactPoint).filter_by(owner_id=owner_id).delete(synchronize_session=False)
        cleanup.query(models.Contact).filter_by(owner_id=owner_id).delete(synchronize_session=False)
        cleanup.query(models.Company).filter_by(owner_id=owner_id).delete(synchronize_session=False)
        cleanup.query(models.ChannelAccount).filter_by(owner_id=owner_id).delete(synchronize_session=False)
        cleanup.query(legacy.User).filter_by(id=owner_id).delete(synchronize_session=False)
        cleanup.commit()
        cleanup.close()
        engine.dispose()


def test_mysql_expired_job_generation_fences_stale_worker(monkeypatch):
    # GIVEN: One MySQL-backed Job whose first worker lease expires.
    url = _mysql_url()
    monkeypatch.setenv("DATABASE_URL", url)
    monkeypatch.setenv("AUTOLEADGEN_ENV", "test")
    command.upgrade(Config(str(Path(__file__).parents[2] / "alembic.ini")), "head")
    engine = create_engine(url, pool_pre_ping=True)
    Session = sessionmaker(bind=engine, autoflush=False)
    suffix = uuid4().hex[:12]
    seed = Session()
    user = legacy.User(username=f"mysql-fence-{suffix}", hashed_password="x", is_active=True)
    seed.add(user)
    seed.flush()
    job = enqueue_job(
        seed,
        owner_id=user.id,
        job_type="fenced-command",
        idempotency_key=f"mysql-fence-{suffix}",
        queue="campaign",
    )
    seed.commit()
    owner_id = user.id
    job_id = job.id
    seed.close()

    claimed_at = datetime.now(timezone.utc) + timedelta(seconds=1)
    worker_a = Session()
    worker_b = Session()
    try:
        first = claim_job(
            worker_a,
            worker_name="mysql-fence-a",
            queues=("campaign",),
            lease_seconds=1,
            now=claimed_at,
        )
        first_fence = lease_fence(first)
        worker_a.commit()

        second = claim_job(
            worker_b,
            worker_name="mysql-fence-b",
            queues=("campaign",),
            lease_seconds=90,
            now=claimed_at + timedelta(seconds=2),
        )
        second_fence = lease_fence(second)
        worker_b.commit()

        # WHEN: Worker A tries to persist success with its stale generation.
        with pytest.raises(LeaseFenceLost):
            complete_job(
                worker_a,
                first,
                {"stale": True},
                fence=first_fence,
                now=claimed_at + timedelta(seconds=2),
            )
        worker_a.rollback()

        # THEN: Worker B's generation remains authoritative.
        verify = Session()
        try:
            current = verify.get(models.AutomationJob, job_id)
            assert first_fence.generation == 1
            assert second_fence.generation == 2
            assert current.status == JobStatus.CLAIMED
            assert current.lease_owner == "mysql-fence-b"
            assert current.attempts == 2
            assert current.result is None
        finally:
            verify.close()
    finally:
        worker_a.rollback()
        worker_b.rollback()
        worker_a.close()
        worker_b.close()
        cleanup = Session()
        cleanup.query(models.AutomationJob).filter_by(id=job_id).delete(synchronize_session=False)
        cleanup.query(models.ChannelAccount).filter_by(owner_id=owner_id).delete(synchronize_session=False)
        cleanup.query(legacy.User).filter_by(id=owner_id).delete(synchronize_session=False)
        cleanup.commit()
        cleanup.close()
        engine.dispose()


def test_mysql_campaign_lock_prevents_concurrent_budget_overspend(monkeypatch):
    # GIVEN: An isolated MySQL campaign with one native unit and two ready attempts.
    url = _mysql_url()
    monkeypatch.setenv("DATABASE_URL", url)
    monkeypatch.setenv("AUTOLEADGEN_ENV", "test")
    monkeypatch.setenv("AUTOLEADGEN_CONNECTOR_MODE", "fake")
    monkeypatch.setenv("ALLOW_REAL_EXTERNAL_CALLS", "false")
    command.upgrade(Config(str(Path(__file__).parents[2] / "alembic.ini")), "head")
    engine = create_engine(url, pool_pre_ping=True)
    Session = sessionmaker(bind=engine, autoflush=False)
    seed = Session()
    suffix = uuid4().hex[:12]
    user = legacy.User(username=f"mysql-budget-{suffix}", hashed_password="x", is_active=True)
    seed.add(user)
    seed.flush()
    campaign = models.Campaign(owner_id=user.id, name=f"Budget {suffix}", lifecycle=CampaignLifecycle.RUNNING, published_revision_number=1)
    seed.add(campaign)
    seed.flush()
    revision = models.CampaignRevision(
        owner_id=user.id,
        campaign_id=campaign.id,
        revision_number=1,
        status=CampaignRevisionStatus.PUBLISHED,
        quality_gates={"require_evidence": False, "require_timezone": False},
        budget_definition={"native_limit": 1, "native_unit": "fake_calls"},
    )
    seed.add(revision)
    seed.flush()
    step = models.SequenceStep(owner_id=user.id, campaign_revision_id=revision.id, position=1, channel=Channel.EMAIL)
    seed.add(step)
    seed.flush()
    attempt_ids = []
    for index in range(2):
        company = models.Company(owner_id=user.id, name=f"Concurrent {index}", normalized_domain=f"mysql-{suffix}-{index}.example")
        seed.add(company)
        seed.flush()
        contact = models.Contact(owner_id=user.id, company_id=company.id, full_name=f"Buyer {index}", timezone="UTC")
        seed.add(contact)
        seed.flush()
        point = models.ContactPoint(
            owner_id=user.id,
            company_id=company.id,
            contact_id=contact.id,
            channel=Channel.EMAIL,
            value=f"buyer-{suffix}-{index}@example.com",
            normalized_value=f"buyer-{suffix}-{index}@example.com",
            verification_status=ContactPointVerificationStatus.VALID,
        )
        enrollment = models.Enrollment(
            owner_id=user.id,
            campaign_id=campaign.id,
            campaign_revision_id=revision.id,
            company_id=company.id,
            contact_id=contact.id,
            status=EnrollmentStatus.ACTIVE,
        )
        seed.add_all([point, enrollment])
        seed.flush()
        attempt = models.OutreachAttempt(
            owner_id=user.id,
            campaign_id=campaign.id,
            enrollment_id=enrollment.id,
            sequence_step_id=step.id,
            contact_point_id=point.id,
            channel=Channel.EMAIL,
            idempotency_key=f"mysql-budget-{suffix}-{index}",
        )
        seed.add(attempt)
        seed.flush()
        attempt_ids.append(attempt.id)
    seed.commit()
    owner_id = user.id
    campaign_id = campaign.id
    seed.close()

    first_reserved = Event()
    release_first = Event()
    second_started = Event()
    errors = []

    def run_first():
        session = Session()
        try:
            execute_attempt(session, attempt=session.get(models.OutreachAttempt, attempt_ids[0]), registry=build_local_registry())
            first_reserved.set()
            assert release_first.wait(10)
            session.commit()
        except Exception as exc:  # pragma: no cover - asserted in parent thread
            errors.append(exc)
            session.rollback()
        finally:
            session.close()

    def run_second():
        session = Session()
        try:
            assert first_reserved.wait(10)
            second_started.set()
            execute_attempt(session, attempt=session.get(models.OutreachAttempt, attempt_ids[1]), registry=build_local_registry())
            session.commit()
        except Exception as exc:  # pragma: no cover - asserted in parent thread
            errors.append(exc)
            session.rollback()
        finally:
            session.close()

    # WHEN: Two workers execute concurrently while the first reservation is uncommitted.
    thread_a = Thread(target=run_first)
    thread_b = Thread(target=run_second)
    thread_a.start()
    thread_b.start()
    assert first_reserved.wait(10)
    assert second_started.wait(10)
    release_first.set()
    thread_a.join(15)
    thread_b.join(15)

    # THEN: MySQL row locking exposes the committed reservation before worker B evaluates budget.
    verify = Session()
    try:
        assert errors == []
        statuses = [verify.get(models.OutreachAttempt, attempt_id).status for attempt_id in attempt_ids]
        assert sorted(status.value for status in statuses) == sorted([AttemptStatus.SUCCEEDED.value, AttemptStatus.BLOCKED.value])
        assert verify.query(models.ProviderCostEvent).filter_by(campaign_id=campaign_id).count() == 1
    finally:
        verify.query(models.AuditEvent).filter_by(owner_id=owner_id).delete(synchronize_session=False)
        verify.query(models.Task).filter_by(owner_id=owner_id).delete(synchronize_session=False)
        verify.query(models.SafetyLock).filter_by(owner_id=owner_id).delete(synchronize_session=False)
        verify.query(models.ProviderCostEvent).filter_by(owner_id=owner_id).delete(synchronize_session=False)
        verify.query(models.MessageEvent).filter_by(owner_id=owner_id).delete(synchronize_session=False)
        verify.query(models.OutreachAttempt).filter_by(owner_id=owner_id).delete(synchronize_session=False)
        verify.query(models.Enrollment).filter_by(owner_id=owner_id).delete(synchronize_session=False)
        verify.query(models.SequenceStep).filter_by(owner_id=owner_id).delete(synchronize_session=False)
        verify.query(models.CampaignRevision).filter_by(owner_id=owner_id).delete(synchronize_session=False)
        verify.query(models.StageRuntime).filter_by(owner_id=owner_id).delete(synchronize_session=False)
        verify.query(models.Campaign).filter_by(owner_id=owner_id).delete(synchronize_session=False)
        verify.query(models.ContactPoint).filter_by(owner_id=owner_id).delete(synchronize_session=False)
        verify.query(models.Contact).filter_by(owner_id=owner_id).delete(synchronize_session=False)
        verify.query(models.Company).filter_by(owner_id=owner_id).delete(synchronize_session=False)
        verify.query(models.ChannelAccount).filter_by(owner_id=owner_id).delete(synchronize_session=False)
        verify.query(legacy.User).filter_by(id=owner_id).delete(synchronize_session=False)
        verify.commit()
        verify.close()
        engine.dispose()


def test_mysql_global_budget_lock_reads_cost_committed_after_transaction_snapshot(monkeypatch):
    # GIVEN: Worker B has established a REPEATABLE READ snapshot before Worker
    # A commits the final dollar of an owner-wide paid Provider budget.
    url = _mysql_url()
    monkeypatch.setenv("DATABASE_URL", url)
    monkeypatch.setenv("AUTOLEADGEN_ENV", "test")
    command.upgrade(Config(str(Path(__file__).parents[2] / "alembic.ini")), "head")
    engine = create_engine(url, pool_pre_ping=True)
    Session = sessionmaker(bind=engine, autoflush=False)
    suffix = uuid4().hex[:12]
    seed = Session()
    user = legacy.User(username=f"mysql-global-budget-{suffix}", hashed_password="x", is_active=True)
    seed.add(user)
    seed.flush()
    seed.add(
        models.AuditEvent(
            owner_id=user.id,
            actor_user_id=user.id,
            action=SETTINGS_ACTION,
            entity_type=SETTINGS_ENTITY,
            entity_id="providers",
            after_data={
                "version": 1,
                "values": {
                    "global_budget_limit": 1,
                    "currency": "USD",
                    "price_version": "mysql-price-v1",
                    "paid_miss_requires_review": True,
                    "provider_policy_notes": "",
                },
            },
        )
    )
    seed.commit()
    owner_id = user.id
    seed.close()

    first_holds_owner = Event()
    second_has_snapshot = Event()
    release_first = Event()
    errors = []
    observed = []

    def reserve_last_dollar():
        session = Session()
        try:
            initial = global_budget_snapshot(session, owner_id=owner_id, lock=True)
            assert initial.remaining == Decimal("1.000000")
            session.add(
                models.ProviderCostEvent(
                    owner_id=owner_id,
                    provider="mysql-paid-provider",
                    operation="lookup",
                    status=ProviderCostStatus.RESERVED,
                    units=1,
                    native_unit="call",
                    unit_price=Decimal("1"),
                    normalized_amount=Decimal("1"),
                    normalized_currency="USD",
                    billable=True,
                    price_version="mysql-price-v1",
                    idempotency_key=f"mysql-global-budget-reservation-{suffix}",
                )
            )
            session.flush()
            first_holds_owner.set()
            assert second_has_snapshot.wait(10)
            assert release_first.wait(10)
            session.commit()
        except Exception as exc:  # pragma: no cover - asserted in parent thread
            errors.append(exc)
            session.rollback()
        finally:
            session.close()

    def read_after_waiting_for_owner():
        session = Session()
        try:
            assert first_holds_owner.wait(10)
            # A non-locking read establishes a snapshot that cannot see Worker
            # A's still-uncommitted ProviderCostEvent.
            assert session.query(legacy.User.username).filter_by(id=owner_id).scalar()
            second_has_snapshot.set()
            observed.append(global_budget_snapshot(session, owner_id=owner_id, lock=True))
            session.rollback()
        except Exception as exc:  # pragma: no cover - asserted in parent thread
            errors.append(exc)
            session.rollback()
        finally:
            session.close()

    thread_a = Thread(target=reserve_last_dollar)
    thread_b = Thread(target=read_after_waiting_for_owner)
    thread_a.start()
    thread_b.start()
    assert first_holds_owner.wait(10)
    assert second_has_snapshot.wait(10)
    release_first.set()
    thread_a.join(15)
    thread_b.join(15)

    # THEN: The owner lock plus locking current reads expose the committed
    # reservation instead of reusing Worker B's stale snapshot.
    cleanup = Session()
    try:
        assert errors == []
        assert len(observed) == 1
        assert observed[0].used == Decimal("1.000000")
        assert observed[0].remaining == Decimal("0")
    finally:
        cleanup.query(models.ProviderCostEvent).filter_by(owner_id=owner_id).delete(synchronize_session=False)
        cleanup.query(models.AuditEvent).filter_by(owner_id=owner_id).delete(synchronize_session=False)
        cleanup.query(models.ChannelAccount).filter_by(owner_id=owner_id).delete(synchronize_session=False)
        cleanup.query(legacy.User).filter_by(id=owner_id).delete(synchronize_session=False)
        cleanup.commit()
        cleanup.close()
        engine.dispose()
