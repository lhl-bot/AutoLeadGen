from types import SimpleNamespace

import models as legacy
from product_v2 import models
from product_v2 import worker
from product_v2.enums import (
    AttemptStatus,
    CampaignLifecycle,
    CampaignRevisionStatus,
    CampaignRunMode,
    Channel,
    ContactPointVerificationStatus,
    JobStatus,
    WorkerType,
)
from product_v2.runtime.queue import LeaseFence, LeaseFenceLost
from product_v2.services.domain import create_enrollment, enqueue_job


def test_empty_claims_still_commit_job_and_attempt_lease_recovery(monkeypatch):
    # GIVEN: Both claim functions recover expired leases but find no new work.
    class SessionSpy:
        def __init__(self):
            self.commits = 0
            self.rollbacks = 0
            self.closed = False

        def commit(self):
            self.commits += 1

        def rollback(self):
            self.rollbacks += 1

        def close(self):
            self.closed = True

    db = SessionSpy()
    calls = []
    monkeypatch.setattr(worker, "SessionLocal", lambda: db)
    monkeypatch.setattr(worker, "build_runtime_registry", lambda: object())
    monkeypatch.setattr(worker, "heartbeat", lambda *args, **kwargs: calls.append("heartbeat"))
    monkeypatch.setattr(
        worker,
        "claim_job",
        lambda *args, **kwargs: calls.append("job_recovery") or None,
    )
    monkeypatch.setattr(
        worker,
        "claim_attempt",
        lambda *args, **kwargs: calls.append("attempt_recovery") or None,
    )

    # WHEN: An outbound worker performs one empty polling cycle.
    did_work = worker.run_once("outbound-test", WorkerType.OUTBOUND)

    # THEN: Heartbeat, job recovery, and attempt recovery each cross a commit boundary.
    assert did_work is False
    assert calls == ["heartbeat", "job_recovery", "attempt_recovery"]
    assert db.commits == 3
    assert db.rollbacks == 0
    assert db.closed is True


def test_fake_inbox_worker_claims_its_queue_and_reports_real_capability(monkeypatch):
    # GIVEN: The isolated fake runtime includes a durable Inbox queue consumer.
    class SessionSpy:
        def __init__(self):
            self.commits = 0
            self.rollbacks = 0
            self.closed = False

        def commit(self):
            self.commits += 1

        def rollback(self):
            self.rollbacks += 1

        def close(self):
            self.closed = True

    db = SessionSpy()
    observed = {}
    monkeypatch.setattr(worker, "SessionLocal", lambda: db)
    monkeypatch.setattr(worker, "heartbeat", lambda *args, **kwargs: observed.update(kwargs))
    claimed_queues = []
    monkeypatch.setattr(
        worker,
        "claim_job",
        lambda *args, **kwargs: claimed_queues.append(tuple(kwargs["queues"])) or None,
    )
    monkeypatch.setenv("AUTOLEADGEN_ENV", "test")
    monkeypatch.setenv("AUTOLEADGEN_CONNECTOR_MODE", "fake")

    # WHEN: The fake Inbox worker performs one empty polling cycle.
    did_work = worker.run_once("inbox-test", WorkerType.INBOX)

    # THEN: It advertises the exact fake capability and durably polls only Inbox work.
    assert did_work is False
    assert observed["status"].value == "running"
    assert observed["details"]["implemented"] is True
    assert observed["details"]["capability"] == "fake_queue_consumer"
    assert claimed_queues == [("inbox",)]
    assert db.commits == 2
    assert db.rollbacks == 0
    assert db.closed is True


def test_real_mode_worker_reports_disabled_until_external_calls_are_approved(monkeypatch):
    # GIVEN: A production process is in real mode but external calls have not
    # been explicitly approved.
    class SessionSpy:
        def __init__(self):
            self.commits = 0
            self.closed = False

        def commit(self):
            self.commits += 1

        def rollback(self):
            raise AssertionError("disabled worker should not roll back")

        def close(self):
            self.closed = True

    db = SessionSpy()
    observed = {}
    monkeypatch.setattr(worker, "SessionLocal", lambda: db)
    monkeypatch.setattr(worker, "heartbeat", lambda *args, **kwargs: observed.update(kwargs))
    monkeypatch.setattr(
        worker,
        "claim_job",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("must not claim")),
    )
    monkeypatch.setenv("AUTOLEADGEN_ENV", "production")
    monkeypatch.setenv("AUTOLEADGEN_CONNECTOR_MODE", "real")

    # WHEN: A real-mode Inbox worker starts without an approved handler.
    assert worker.run_once("real-inbox", WorkerType.INBOX) is False

    # THEN: The heartbeat fails closed and no queue work is claimed.
    assert observed["status"].value == "disabled"
    assert observed["details"]["implemented"] is False
    assert observed["details"]["reason"] == "runtime_controls_not_enabled"
    assert db.commits == 1
    assert db.closed is True


def test_outbound_pause_does_not_disable_real_inbox_safety_ingestion(monkeypatch):
    monkeypatch.setenv("AUTOLEADGEN_ENV", "production")
    monkeypatch.setenv("AUTOLEADGEN_CONNECTOR_MODE", "real")
    monkeypatch.setenv("ALLOW_REAL_EXTERNAL_CALLS", "true")
    monkeypatch.setenv("OUTBOUND_HARD_PAUSE", "true")

    assert worker._worker_runtime_enabled(WorkerType.OUTBOUND) is False
    assert worker._worker_runtime_enabled(WorkerType.INBOX) is True


def test_worker_rolls_back_domain_mutations_when_job_fence_is_lost(monkeypatch):
    # GIVEN: A claimed job whose generation is replaced during execution.
    class SessionSpy:
        def __init__(self):
            self.commits = 0
            self.rollbacks = 0
            self.closed = False

        def commit(self):
            self.commits += 1

        def rollback(self):
            self.rollbacks += 1

        def close(self):
            self.closed = True

    db = SessionSpy()
    fence = LeaseFence(owner="stale-worker", generation=1)
    job = SimpleNamespace(_product_v2_lease_fence=fence)
    monkeypatch.setattr(worker, "SessionLocal", lambda: db)
    monkeypatch.setattr(worker, "build_runtime_registry", lambda: object())
    monkeypatch.setattr(worker, "heartbeat", lambda *args, **kwargs: None)
    monkeypatch.setattr(worker, "claim_job", lambda *args, **kwargs: job)
    monkeypatch.setattr(worker, "claim_attempt", lambda *args, **kwargs: None)

    def lose_fence(*args, **kwargs):
        assert kwargs["lease_fence"] == fence
        raise LeaseFenceLost("generation was reclaimed")

    monkeypatch.setattr(worker, "execute_job", lose_fence)

    # WHEN: The stale worker reaches its terminal persistence step.
    assert worker.run_once("stale-worker", WorkerType.OUTBOUND) is True

    # THEN: Its transaction is rolled back, while recovery polling still commits.
    assert db.rollbacks == 1
    assert db.commits == 3
    assert db.closed is True


def test_thirty_company_shadow_runs_through_readiness_jobs_and_worker_claims(
    db_session,
    monkeypatch,
):
    # GIVEN: A 30-company fake-only Campaign that is READY, not pre-forced to
    # RUNNING, with all Enrollment work present only as durable jobs.
    monkeypatch.setenv("AUTOLEADGEN_ENV", "test")
    monkeypatch.setenv("AUTOLEADGEN_CONNECTOR_MODE", "fake")
    monkeypatch.setenv("ALLOW_REAL_EXTERNAL_CALLS", "false")
    monkeypatch.setenv("OUTBOUND_HARD_PAUSE", "1")
    user = legacy.User(
        username="worker-shadow-30",
        hashed_password="local-test-only",
        is_active=True,
    )
    db_session.add(user)
    db_session.flush()
    db_session.add(
        legacy.EmailAccount(
            user_id=user.id,
            email="sales@worker-shadow.example",
            smtp_host="fake.invalid",
            smtp_user="sales@worker-shadow.example",
            smtp_pass="fake-not-used",
        )
    )
    campaign = models.Campaign(
        owner_id=user.id,
        name="Worker-backed shadow 30",
        lifecycle=CampaignLifecycle.READY,
        run_mode=CampaignRunMode.SHADOW,
        priority=300,
        published_revision_number=1,
    )
    db_session.add(campaign)
    db_session.flush()
    revision = models.CampaignRevision(
        owner_id=user.id,
        campaign_id=campaign.id,
        revision_number=1,
        status=CampaignRevisionStatus.PUBLISHED,
        quality_gates={"require_evidence": False, "require_timezone": False},
        budget_definition={
            "native_limit": 30,
            "native_unit": "fake_calls",
            "price_version": "fake-v1",
        },
        stop_conditions={
            "public_unsubscribe_url": "http://127.0.0.1:3000/api/unsubscribe",
        },
    )
    db_session.add(revision)
    db_session.flush()
    db_session.add(
        models.SequenceStep(
            owner_id=user.id,
            campaign_revision_id=revision.id,
            position=1,
            channel=Channel.EMAIL,
            template_version="worker-shadow-v1",
            condition_definition={"fake_only": True},
            stop_condition_definition={"stop_on_reply": True},
        )
    )
    for index in range(30):
        company = models.Company(
            owner_id=user.id,
            name=f"Worker Buyer {index + 1}",
            normalized_domain=f"worker-shadow-{index + 1}.example",
        )
        db_session.add(company)
        db_session.flush()
        contact = models.Contact(
            owner_id=user.id,
            company_id=company.id,
            full_name=f"Buyer {index + 1}",
            timezone="UTC",
        )
        db_session.add(contact)
        db_session.flush()
        address = f"buyer{index + 1}@worker-shadow.example"
        db_session.add(
            models.ContactPoint(
                owner_id=user.id,
                company_id=company.id,
                contact_id=contact.id,
                channel=Channel.EMAIL,
                value=address,
                normalized_value=address,
                verification_status=ContactPointVerificationStatus.VALID,
                is_primary=True,
            )
        )
        db_session.flush()
        create_enrollment(
            db_session,
            campaign=campaign,
            contact=contact,
            idempotency_key=f"worker-shadow-enroll-{index + 1}",
            scheduled_at=None,
            actor_user_id=user.id,
        )
    start_job = enqueue_job(
        db_session,
        owner_id=user.id,
        job_type="campaign.start",
        idempotency_key="worker-shadow-start",
        queue="campaign",
        payload={"campaign_id": campaign.id, "confirm_warnings": False},
        priority=1000,
        campaign_id=campaign.id,
    )
    db_session.commit()
    campaign_id = campaign.id
    start_job_id = start_job.id

    # WHEN: The real fake-only Inbox and Outbound worker loops provide their
    # own heartbeat, claim the start command, create each Attempt, and execute it.
    assert worker.run_once("worker-shadow-inbox", WorkerType.INBOX) is False
    assert worker.run_once("worker-shadow-outbound", WorkerType.OUTBOUND) is True
    for _ in range(30):
        assert worker.run_once("worker-shadow-outbound", WorkerType.OUTBOUND) is True

    # THEN: Readiness and lifecycle were honored and every side effect is a
    # traceable fake event produced through a claimed worker path.
    db_session.expire_all()
    assert db_session.get(models.Campaign, campaign_id).lifecycle == CampaignLifecycle.RUNNING
    assert db_session.get(models.AutomationJob, start_job_id).status == JobStatus.SUCCEEDED
    attempts = db_session.query(models.OutreachAttempt).filter_by(campaign_id=campaign_id).all()
    assert len(attempts) == 30
    assert all(attempt.status == AttemptStatus.SUCCEEDED for attempt in attempts)
    assert all(attempt.provider and attempt.provider.startswith("fake-") for attempt in attempts)
    assert all((attempt.provider_response or {}).get("network_calls") == 0 for attempt in attempts)
    assert db_session.query(models.MessageEvent).join(
        models.OutreachAttempt,
        models.OutreachAttempt.id == models.MessageEvent.outreach_attempt_id,
    ).filter(models.OutreachAttempt.campaign_id == campaign_id).count() == 30
    assert db_session.query(models.ProviderCostEvent).filter_by(campaign_id=campaign_id).count() == 30
    assert db_session.query(models.AuditEvent).filter_by(
        action="outreach_attempt.succeeded",
    ).count() == 30
