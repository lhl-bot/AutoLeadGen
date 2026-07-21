from datetime import datetime, timedelta, timezone

import pytest

import models as legacy
from database import SessionLocal
from product_v2 import models
from product_v2.connectors.base import ConnectorRequest, ConnectorResult
from product_v2.connectors.registry import ConnectorRegistry, build_local_registry
from product_v2.enums import (
    AttemptKind,
    AttemptStatus,
    CampaignLifecycle,
    CampaignRunMode,
    CampaignRevisionStatus,
    Channel,
    ContactPointVerificationStatus,
    EnrollmentStatus,
    JobStatus,
    MessageDirection,
    MessageEventType,
    OverrideGate,
    ProviderCostStatus,
    RestrictionScope,
    TaskStatus,
    TaskType,
)
from product_v2.api import update_task
from product_v2.runtime.inbox_cursor import prepare_cursor, record_success
from product_v2.runtime import outbound as outbound_runtime
from product_v2.runtime.outbound import execute_attempt
from product_v2.runtime.queue import (
    LeaseFenceLost,
    claim_attempt,
    claim_job,
    complete_job,
    fail_job,
    lease_fence,
)
from product_v2.runtime.reply_parser import detect_unsubscribe_intent, extract_latest_reply
from product_v2.runtime.sequence_conditions import evaluate_stop_conditions
from product_v2.schemas import TaskUpdate
from product_v2.services.domain import as_utc, enqueue_job, evaluate_outreach_gates


def _runtime_graph(db):
    user = legacy.User(username="runtime-owner", hashed_password="x", is_active=True)
    db.add(user)
    db.flush()
    company = models.Company(owner_id=user.id, name="Acme", normalized_domain="acme.example")
    db.add(company)
    db.flush()
    contact = models.Contact(owner_id=user.id, company_id=company.id, full_name="Ada Buyer", timezone="UTC")
    db.add(contact)
    db.flush()
    point = models.ContactPoint(
        owner_id=user.id,
        company_id=company.id,
        contact_id=contact.id,
        channel=Channel.EMAIL,
        value="ada@acme.example",
        normalized_value="ada@acme.example",
        verification_status=ContactPointVerificationStatus.VALID,
    )
    campaign = models.Campaign(owner_id=user.id, name="Runtime campaign", lifecycle=CampaignLifecycle.RUNNING)
    db.add_all([point, campaign])
    db.flush()
    revision = models.CampaignRevision(
        owner_id=user.id,
        campaign_id=campaign.id,
        revision_number=1,
        status=CampaignRevisionStatus.PUBLISHED,
        quality_gates={"require_evidence": False, "require_timezone": False},
        budget_definition={"native_limit": 10},
    )
    db.add(revision)
    db.flush()
    step = models.SequenceStep(
        owner_id=user.id,
        campaign_revision_id=revision.id,
        position=1,
        channel=Channel.EMAIL,
    )
    db.add(step)
    db.flush()
    enrollment = models.Enrollment(
        owner_id=user.id,
        campaign_id=campaign.id,
        campaign_revision_id=revision.id,
        company_id=company.id,
        contact_id=contact.id,
        status=EnrollmentStatus.ACTIVE,
    )
    db.add(enrollment)
    db.flush()
    attempt = models.OutreachAttempt(
        owner_id=user.id,
        campaign_id=campaign.id,
        enrollment_id=enrollment.id,
        sequence_step_id=step.id,
        contact_point_id=point.id,
        channel=Channel.EMAIL,
        idempotency_key="runtime-attempt-1",
    )
    db.add(attempt)
    db.commit()
    return user, company, contact, point, campaign, revision, enrollment, attempt


def test_attempt_claim_is_cancelled_when_owner_v2_path_is_inactive(
    db_session,
    monkeypatch,
):
    # GIVEN: A claimed/queued Attempt for an owner whose durable path remains
    # legacy under production-like enforcement.
    *_, enrollment, attempt = _runtime_graph(db_session)
    monkeypatch.setenv("PRODUCT_V2_OWNER_PATH_TEST_BYPASS", "false")
    monkeypatch.setenv("PRODUCT_V2_OWNER_PATH_ENFORCEMENT", "true")
    registry = build_local_registry()
    connector = registry.get(Channel.EMAIL)

    # WHEN: The worker re-checks immediately after claim.
    execute_attempt(db_session, attempt=attempt, registry=registry)
    db_session.commit()

    # THEN: No connector or cost boundary is reached and the Enrollment is
    # paused instead of being silently retried on the wrong writer path.
    assert attempt.status == AttemptStatus.CANCELLED
    assert attempt.last_error == "owner_v2_write_path_inactive"
    assert enrollment.status == EnrollmentStatus.PAUSED
    assert connector.requests == []
    assert db_session.query(models.ProviderCostEvent).filter_by(
        outreach_attempt_id=attempt.id
    ).count() == 0
    assert db_session.query(models.AuditEvent).filter_by(
        action="outreach_attempt.owner_path_cancelled",
        entity_id=str(attempt.id),
    ).count() == 1


def test_owner_path_is_rechecked_at_provider_boundary(
    db_session,
    monkeypatch,
):
    # GIVEN: Owner-path preflight passes, but the authoritative state changes
    # after SENDING/cost reservation is committed and before connector.send.
    *_, enrollment, attempt = _runtime_graph(db_session)
    registry = build_local_registry()
    connector = registry.get(Channel.EMAIL)
    guard_calls = []

    def changing_owner_path(_db, _attempt):
        guard_calls.append(len(guard_calls) + 1)
        return len(guard_calls) == 1

    monkeypatch.setattr(
        outbound_runtime,
        "_owner_path_allows_attempt",
        changing_owner_path,
    )

    # WHEN: Execution reaches its second, last-instruction Provider guard.
    execute_attempt(db_session, attempt=attempt, registry=registry)
    db_session.commit()

    # THEN: The reserved cost is marked not sent, account capacity is released,
    # all in-flight locks close, and no Provider call is made.
    assert guard_calls == [1, 2]
    assert connector.requests == []
    assert attempt.status == AttemptStatus.CANCELLED
    assert attempt.capacity_reserved_at is None
    assert enrollment.status == EnrollmentStatus.PAUSED
    cost = db_session.query(models.ProviderCostEvent).filter_by(
        outreach_attempt_id=attempt.id
    ).one()
    assert cost.status == ProviderCostStatus.FAILED
    assert cost.metadata_json["provider_call_allowed"] is False
    assert db_session.query(models.SafetyLock).filter_by(
        owner_id=attempt.owner_id,
        active=True,
    ).count() == 0


def test_latest_reply_excludes_quoted_unsubscribe_footer():
    # GIVEN: A positive reply quoting an original email with an unsubscribe footer.
    raw = "Thanks, please send the catalogue.\n\nOn Tue, Sales wrote:\n> If you prefer, unsubscribe here."

    # WHEN: Extracting and classifying only the newest reply.
    latest = extract_latest_reply(raw)
    intent = detect_unsubscribe_intent(latest)

    # THEN: The quoted footer cannot create a false unsubscribe.
    assert latest == "Thanks, please send the catalogue."
    assert intent.is_unsubscribe is False


def test_unsubscribe_scope_distinguishes_email_contact_and_company():
    assert detect_unsubscribe_intent("unsubscribe").scope == RestrictionScope.CONTACT_POINT
    assert detect_unsubscribe_intent("Please do not contact me again").scope == RestrictionScope.CONTACT
    company = detect_unsubscribe_intent("Do not contact anyone at our company")
    assert company.scope == RestrictionScope.COMPANY
    assert company.requires_company_confirmation is True


def test_local_registry_rejects_real_connector(monkeypatch):
    class RealConnector:
        channel = Channel.EMAIL
        provider = "smtp"
        is_fake = False

        def send(self, request: ConnectorRequest) -> ConnectorResult:
            raise AssertionError("must never be called")

    monkeypatch.setenv("AUTOLEADGEN_ENV", "local")
    monkeypatch.setenv("ALLOW_REAL_EXTERNAL_CALLS", "false")
    registry = ConnectorRegistry()

    with pytest.raises(RuntimeError, match="Real connectors are disabled"):
        registry.register(RealConnector())


def test_outbound_hard_pause_rejects_real_connector_even_with_all_real_flags(monkeypatch):
    # GIVEN: Production and every real-connector opt-in are enabled, but the
    # global outbound hard pause is still engaged.
    class RealConnector:
        channel = Channel.EMAIL
        provider = "real-hard-paused"
        is_fake = False

        def send(self, request: ConnectorRequest) -> ConnectorResult:
            raise AssertionError("hard pause must prevent Provider I/O")

    monkeypatch.setenv("AUTOLEADGEN_ENV", "production")
    monkeypatch.setenv("AUTOLEADGEN_CONNECTOR_MODE", "real")
    monkeypatch.setenv("ALLOW_REAL_EXTERNAL_CALLS", "true")
    monkeypatch.setenv("OUTBOUND_HARD_PAUSE", "true")

    # WHEN: The process tries to register a real connector.
    registry = ConnectorRegistry()

    # THEN: The independent hard pause wins over every soft/config opt-in.
    with pytest.raises(RuntimeError, match="OUTBOUND_HARD_PAUSE"):
        registry.register(RealConnector())


def test_outbound_hard_pause_engaged_after_registration_blocks_execution(
    db_session,
    monkeypatch,
):
    # GIVEN: A real connector was registered while production sending was
    # permitted, then the operator engages the hard pause before execution.
    *_, campaign, _, _, attempt = _runtime_graph(db_session)
    campaign.run_mode = CampaignRunMode.AUTO
    db_session.commit()

    class RealConnector:
        channel = Channel.EMAIL
        provider = "real-dynamic-hard-pause"
        is_fake = False

        def __init__(self):
            self.calls = 0

        def send(self, request: ConnectorRequest) -> ConnectorResult:
            self.calls += 1
            return ConnectorResult(True, self.provider, "must-not-be-created")

    monkeypatch.setenv("AUTOLEADGEN_ENV", "production")
    # This case isolates the dynamic transport hard pause. Owner-path fencing
    # has dedicated production-like tests below.
    monkeypatch.setenv("PRODUCT_V2_OWNER_PATH_ENFORCEMENT", "false")
    monkeypatch.setenv("AUTOLEADGEN_CONNECTOR_MODE", "real")
    monkeypatch.setenv("ALLOW_REAL_EXTERNAL_CALLS", "true")
    monkeypatch.setenv("OUTBOUND_HARD_PAUSE", "false")
    connector = RealConnector()
    registry = ConnectorRegistry()
    registry.register(connector)

    # WHEN: The hard pause flips after registration but before the Provider
    # boundary.
    monkeypatch.setenv("OUTBOUND_HARD_PAUSE", "true")

    # THEN: Registry retrieval re-checks the live hard pause and no call or
    # cost event can be produced.
    with pytest.raises(RuntimeError, match="OUTBOUND_HARD_PAUSE"):
        execute_attempt(db_session, attempt=attempt, registry=registry)
    assert connector.calls == 0
    assert (
        db_session.query(models.ProviderCostEvent)
        .filter_by(outreach_attempt_id=attempt.id)
        .count()
        == 0
    )


def test_shadow_mode_blocks_real_connector_before_provider_boundary(db_session, monkeypatch):
    # GIVEN: A registry that is valid only in a production-like real-connector
    # configuration, while the Campaign itself is explicitly SHADOW.
    *_, campaign, _, enrollment, attempt = _runtime_graph(db_session)
    campaign.run_mode = CampaignRunMode.SHADOW
    db_session.commit()

    class RealConnector:
        channel = Channel.EMAIL
        provider = "real-test-provider"
        is_fake = False

        def __init__(self):
            self.calls = 0

        def send(self, request: ConnectorRequest) -> ConnectorResult:
            self.calls += 1
            return ConnectorResult(True, self.provider, "must-not-exist")

    monkeypatch.setenv("AUTOLEADGEN_ENV", "production")
    monkeypatch.setenv("PRODUCT_V2_OWNER_PATH_ENFORCEMENT", "false")
    monkeypatch.setenv("AUTOLEADGEN_CONNECTOR_MODE", "real")
    monkeypatch.setenv("ALLOW_REAL_EXTERNAL_CALLS", "true")
    connector = RealConnector()
    registry = ConnectorRegistry()
    registry.register(connector)

    # WHEN: The worker evaluates the Attempt.
    execute_attempt(db_session, attempt=attempt, registry=registry)
    db_session.commit()

    # THEN: run_mode, independently of registry configuration, prevents I/O.
    assert connector.calls == 0
    assert attempt.status == AttemptStatus.BLOCKED
    assert attempt.last_error == "shadow_mode_requires_fake_connector"
    assert enrollment.status == EnrollmentStatus.BLOCKED
    assert db_session.query(models.ProviderCostEvent).filter_by(outreach_attempt_id=attempt.id).count() == 0


def test_review_mode_requires_explicit_task_approval_before_one_fake_send(db_session):
    # GIVEN: A REVIEW Campaign with an otherwise sendable Attempt.
    user, _, _, _, campaign, _, enrollment, attempt = _runtime_graph(db_session)
    campaign.run_mode = CampaignRunMode.REVIEW
    db_session.commit()
    registry = build_local_registry()
    connector = registry.get(Channel.EMAIL)

    # WHEN: The worker first evaluates it.
    execute_attempt(db_session, attempt=attempt, registry=registry)
    db_session.commit()

    # THEN: it creates an exact Attempt review and performs no Provider call.
    task = db_session.query(models.Task).filter_by(
        attempt_id=attempt.id,
        task_type=TaskType.DRAFT_REVIEW,
    ).one()
    assert task.status == TaskStatus.OPEN
    assert task.metadata_json["attempt_id"] == attempt.id
    assert task.metadata_json["channel"] == "email"
    assert task.metadata_json["provider_call_allowed"] is False
    assert "body" in task.metadata_json
    assert connector.requests == []
    assert attempt.status == AttemptStatus.BLOCKED

    # WHEN: the owner explicitly approves that Task and the worker retries.
    update_task(
        task.id,
        TaskUpdate(status=TaskStatus.COMPLETED),
        db=db_session,
        user=user,
    )
    db_session.expire_all()
    attempt = db_session.get(models.OutreachAttempt, attempt.id)
    enrollment = db_session.get(models.Enrollment, enrollment.id)
    assert attempt.status == AttemptStatus.QUEUED
    assert enrollment.status == EnrollmentStatus.ACTIVE
    execute_attempt(db_session, attempt=attempt, registry=registry)
    db_session.commit()

    # THEN: exactly one fake call succeeds; approval never bypasses the gates.
    assert len(connector.requests) == 1
    assert attempt.status == AttemptStatus.SUCCEEDED
    assert db_session.query(models.ProviderCostEvent).filter_by(outreach_attempt_id=attempt.id).count() == 1


def test_dismissing_review_task_cancels_attempt_and_keeps_enrollment_paused(db_session):
    user, _, _, _, campaign, _, enrollment, attempt = _runtime_graph(db_session)
    campaign.run_mode = CampaignRunMode.REVIEW
    db_session.commit()
    registry = build_local_registry()
    execute_attempt(db_session, attempt=attempt, registry=registry)
    db_session.commit()
    task = db_session.query(models.Task).filter_by(
        attempt_id=attempt.id,
        task_type=TaskType.DRAFT_REVIEW,
    ).one()

    update_task(
        task.id,
        TaskUpdate(status=TaskStatus.DISMISSED),
        db=db_session,
        user=user,
    )
    db_session.expire_all()

    assert db_session.get(models.OutreachAttempt, attempt.id).status == AttemptStatus.CANCELLED
    current_enrollment = db_session.get(models.Enrollment, enrollment.id)
    assert current_enrollment.status == EnrollmentStatus.PAUSED
    assert current_enrollment.paused_reason == "review_rejected"
    assert registry.get(Channel.EMAIL).requests == []


def test_job_claim_is_idempotent_and_priority_ordered(db_session):
    user = legacy.User(username="queue-owner", hashed_password="x", is_active=True)
    db_session.add(user)
    db_session.flush()
    low = enqueue_job(db_session, owner_id=user.id, job_type="low", idempotency_key="job-low-0001", queue="campaign", priority=10)
    high = enqueue_job(db_session, owner_id=user.id, job_type="high", idempotency_key="job-high-0001", queue="campaign", priority=200)
    duplicate = enqueue_job(db_session, owner_id=user.id, job_type="high", idempotency_key="job-high-0001", queue="campaign", priority=200)
    db_session.commit()

    claimed = claim_job(db_session, worker_name="worker-a", queues=("campaign",))

    assert duplicate.id == high.id
    assert claimed.id == high.id
    assert claimed.status == JobStatus.CLAIMED
    assert low.status == JobStatus.PENDING


def test_job_idempotency_key_cannot_be_reused_for_different_payload(db_session):
    # GIVEN: A durable command already bound to one canonical payload.
    user = legacy.User(username="job-idempotency-owner", hashed_password="x", is_active=True)
    db_session.add(user)
    db_session.flush()
    campaign_a = models.Campaign(owner_id=user.id, name="Idempotency A")
    campaign_b = models.Campaign(owner_id=user.id, name="Idempotency B")
    db_session.add_all([campaign_a, campaign_b])
    db_session.flush()
    first = enqueue_job(
        db_session,
        owner_id=user.id,
        job_type="campaign.start",
        idempotency_key="job-payload-0001",
        queue="campaign",
        campaign_id=campaign_a.id,
        payload={"campaign_id": campaign_a.id, "confirm_warnings": False},
    )

    # WHEN: The same key is retried with a different target and payload.
    with pytest.raises(ValueError, match="different command payload"):
        enqueue_job(
            db_session,
            owner_id=user.id,
            job_type="campaign.start",
            idempotency_key="job-payload-0001",
            queue="campaign",
            campaign_id=campaign_b.id,
            payload={"campaign_id": campaign_b.id, "confirm_warnings": False},
        )

    # THEN: The original command remains the sole meaning of the key.
    assert first.campaign_id == campaign_a.id
    assert db_session.query(models.AutomationJob).filter_by(idempotency_key="job-payload-0001").count() == 1


def test_expired_job_at_max_attempts_becomes_failed(db_session):
    # GIVEN: A worker lease expired on the final permitted execution attempt.
    user = legacy.User(username="expired-job-owner", hashed_password="x", is_active=True)
    db_session.add(user)
    db_session.flush()
    job = enqueue_job(
        db_session,
        owner_id=user.id,
        job_type="exhausted",
        idempotency_key="job-expired-max-attempts",
        queue="campaign",
    )
    current = datetime.now(timezone.utc)
    job.status = JobStatus.RUNNING
    job.attempts = job.max_attempts
    job.lease_owner = "crashed-worker"
    job.lease_expires_at = current - timedelta(seconds=1)
    db_session.commit()

    # WHEN: Lease recovery runs while looking for another claimable job.
    claimed = claim_job(
        db_session,
        worker_name="recovery-worker",
        queues=("campaign",),
        now=current,
    )
    db_session.refresh(job)

    # THEN: The exhausted job is terminal and cannot be reclaimed.
    assert claimed is None
    assert job.status == JobStatus.FAILED
    assert job.completed_at is not None
    assert job.lease_owner is None
    assert job.lease_expires_at is None
    assert job.last_error == "lease_expired"


def test_stale_job_worker_cannot_complete_or_fail_reclaimed_generation(db_session):
    # GIVEN: Worker A's committed claim expires and worker B reclaims the same Job.
    user = legacy.User(username="job-fence-owner", hashed_password="x", is_active=True)
    db_session.add(user)
    db_session.flush()
    job = enqueue_job(
        db_session,
        owner_id=user.id,
        job_type="fenced-command",
        idempotency_key="job-fence-reclaim-0001",
        queue="campaign",
    )
    db_session.commit()
    claimed_at = datetime.now(timezone.utc) + timedelta(seconds=1)
    worker_a_job = claim_job(
        db_session,
        worker_name="job-worker-a",
        queues=("campaign",),
        lease_seconds=1,
        now=claimed_at,
    )
    worker_a_fence = lease_fence(worker_a_job)
    db_session.commit()

    worker_b = SessionLocal()
    try:
        worker_b_job = claim_job(
            worker_b,
            worker_name="job-worker-b",
            queues=("campaign",),
            lease_seconds=90,
            now=claimed_at + timedelta(seconds=2),
        )
        worker_b_fence = lease_fence(worker_b_job)
        worker_b.commit()
    finally:
        worker_b.close()

    # WHEN: The stale worker tries either terminal write with its old fence.
    with pytest.raises(LeaseFenceLost):
        complete_job(
            db_session,
            worker_a_job,
            {"stale": True},
            fence=worker_a_fence,
            now=claimed_at + timedelta(seconds=2),
        )
    db_session.rollback()
    with pytest.raises(LeaseFenceLost):
        fail_job(
            db_session,
            worker_a_job,
            "stale failure",
            fence=worker_a_fence,
            now=claimed_at + timedelta(seconds=2),
        )
    db_session.rollback()

    # THEN: Generation 2 remains exclusively owned by worker B and has no stale result/error.
    db_session.expire_all()
    current = db_session.get(models.AutomationJob, job.id)
    assert worker_a_fence.owner == "job-worker-a"
    assert worker_a_fence.generation == 1
    assert worker_b_fence.owner == "job-worker-b"
    assert worker_b_fence.generation == 2
    assert current.status == JobStatus.CLAIMED
    assert current.lease_owner == "job-worker-b"
    assert current.attempts == 2
    assert current.result is None
    assert current.last_error == "lease_expired"


def test_provider_success_with_local_uncertainty_never_requeues(db_session):
    *_, attempt = _runtime_graph(db_session)
    registry = build_local_registry()

    def fail_after_provider():
        raise RuntimeError("simulated crash after provider acknowledgement")

    execute_attempt(
        db_session,
        attempt=attempt,
        registry=registry,
        after_provider_success=fail_after_provider,
    )
    db_session.commit()

    assert attempt.status == AttemptStatus.UNKNOWN
    assert "provider_success_local_uncertain" in attempt.unknown_reason
    task = db_session.query(models.Task).filter_by(attempt_id=attempt.id).one()
    assert task.metadata_json["auto_resend_allowed"] is False
    cost = db_session.query(models.ProviderCostEvent).filter_by(outreach_attempt_id=attempt.id).one()
    assert cost.status == ProviderCostStatus.UNKNOWN


def test_provider_exception_keeps_durable_reservation_and_never_requeues(db_session):
    # GIVEN: A connector whose network exception cannot prove whether the Provider accepted.
    *_, attempt = _runtime_graph(db_session)

    class UncertainConnector:
        channel = Channel.EMAIL
        provider = "fake-uncertain-email"
        is_fake = True

        def send(self, request: ConnectorRequest) -> ConnectorResult:
            raise TimeoutError("simulated ambiguous provider timeout")

    registry = ConnectorRegistry()
    registry.register(UncertainConnector())

    # WHEN: Execution crosses the committed reservation boundary and the Provider call errors.
    execute_attempt(db_session, attempt=attempt, registry=registry)
    db_session.commit()

    # THEN: The attempt and cost are UNKNOWN with a mandatory, non-resend reconciliation task.
    assert attempt.status == AttemptStatus.UNKNOWN
    assert "provider_call_uncertain" in attempt.unknown_reason
    cost = db_session.query(models.ProviderCostEvent).filter_by(outreach_attempt_id=attempt.id).one()
    assert cost.status == ProviderCostStatus.UNKNOWN
    assert cost.metadata_json["requires_reconciliation"] is True
    task = db_session.query(models.Task).filter_by(
        attempt_id=attempt.id,
        task_type="reconciliation",
    ).one()
    assert task.metadata_json["auto_resend_allowed"] is False


def test_provider_result_from_expired_worker_cannot_overwrite_recovery(db_session):
    # GIVEN: Worker A owns generation 1 and crosses the durable SENDING boundary.
    *_, attempt = _runtime_graph(db_session)
    claimed_at = datetime.now(timezone.utc) + timedelta(seconds=1)
    attempt.scheduled_at = claimed_at - timedelta(seconds=1)
    db_session.commit()
    claimed = claim_attempt(
        db_session,
        worker_name="outbound-worker-a",
        lease_seconds=1,
        now=claimed_at,
    )
    fence = lease_fence(claimed)
    db_session.commit()

    class RecoverDuringFakeSend:
        channel = Channel.EMAIL
        provider = "fake-fence-race"
        is_fake = True

        def __init__(self):
            self.calls = 0

        def send(self, request: ConnectorRequest) -> ConnectorResult:
            self.calls += 1
            # Worker B observes A's renewed lease as expired at a deterministic
            # future instant. Attempt recovery is fail-closed: UNKNOWN, never a
            # second automatic Provider call.
            worker_b = SessionLocal()
            try:
                assert claim_attempt(
                    worker_b,
                    worker_name="outbound-worker-b",
                    now=claimed_at + timedelta(minutes=5),
                ) is None
                worker_b.commit()
            finally:
                worker_b.close()
            return ConnectorResult(
                accepted=True,
                provider=self.provider,
                provider_message_id="fake-provider-accepted-after-expiry",
                raw={"fake": True, "network_calls": 0, "race": "lease_expired"},
            )

    connector = RecoverDuringFakeSend()
    registry = ConnectorRegistry()
    registry.register(connector)

    # WHEN: Provider acceptance returns to the now-stale generation 1 worker.
    result = execute_attempt(
        db_session,
        attempt=claimed,
        registry=registry,
        lease_fence=fence,
    )
    db_session.commit()

    # THEN: Recovery remains authoritative; no SENT event or success overwrite is possible.
    db_session.refresh(result)
    assert connector.calls == 1
    assert result.status == AttemptStatus.UNKNOWN
    assert result.provider_message_id is None
    assert result.sent_at is None
    assert db_session.query(models.MessageEvent).filter_by(outreach_attempt_id=result.id).count() == 0
    cost = db_session.query(models.ProviderCostEvent).filter_by(outreach_attempt_id=result.id).one()
    assert cost.status == ProviderCostStatus.UNKNOWN
    task = db_session.query(models.Task).filter_by(
        attempt_id=result.id,
        task_type="reconciliation",
    ).one()
    loss = task.metadata_json["lease_fence_losses"][0]
    assert loss["lease_owner"] == "outbound-worker-a"
    assert loss["claim_generation"] == 1
    assert loss["provider_trace"]["provider_message_id"] == "fake-provider-accepted-after-expiry"
    assert task.metadata_json["auto_resend_allowed"] is False
    assert db_session.query(models.AuditEvent).filter_by(
        entity_type="outreach_attempt",
        entity_id=str(result.id),
        action="outreach_attempt.lease_fence_lost",
    ).count() == 1


def test_successful_fake_attempt_is_fully_traceable_and_non_billable(db_session):
    _, company, _, point, _, _, _, attempt = _runtime_graph(db_session)
    registry = build_local_registry()

    execute_attempt(db_session, attempt=attempt, registry=registry)
    db_session.commit()

    assert attempt.status == AttemptStatus.SUCCEEDED
    assert attempt.provider.startswith("fake-")
    assert point.last_cold_outreach_at is not None
    assert company.last_cold_outreach_at is not None
    event = db_session.query(models.MessageEvent).filter_by(outreach_attempt_id=attempt.id).one()
    cost = db_session.query(models.ProviderCostEvent).filter_by(outreach_attempt_id=attempt.id).one()
    assert event.metadata_json == {
        "fake": True,
        "channel_account_id": attempt.channel_account_id,
    }
    assert cost.billable is False
    assert cost.normalized_amount is None
    assert float(cost.units) == 1
    assert cost.metadata_json["network_calls"] == 0
    stage = db_session.query(models.StageRuntime).filter_by(campaign_id=attempt.campaign_id, stage_name="outbound").one()
    assert stage.status.value == "idle"
    assert stage.last_started_at is not None
    assert stage.last_succeeded_at is not None

    # Re-entering a successful attempt is a no-op at both DB and connector layers.
    execute_attempt(db_session, attempt=attempt, registry=registry)
    db_session.commit()
    assert db_session.query(models.MessageEvent).filter_by(outreach_attempt_id=attempt.id).count() == 1
    assert db_session.query(models.ProviderCostEvent).filter_by(outreach_attempt_id=attempt.id).count() == 1


def test_successful_sequence_schedules_follow_up_then_completes_enrollment(db_session):
    # GIVEN: An active enrollment with a two-step email sequence.
    _, company, _, point, _, revision, enrollment, first_attempt = _runtime_graph(db_session)
    db_session.add(
        models.SequenceStep(
            owner_id=enrollment.owner_id,
            campaign_revision_id=revision.id,
            position=2,
            channel=Channel.EMAIL,
            wait_minutes=0,
            template_version="follow-up-v1",
        )
    )
    db_session.commit()
    registry = build_local_registry()

    # WHEN: The cold step succeeds and the scheduler claims the generated follow-up.
    execute_attempt(db_session, attempt=first_attempt, registry=registry)
    db_session.commit()
    cold_contact_at = point.last_cold_outreach_at
    cold_company_at = company.last_cold_outreach_at
    follow_up = db_session.query(models.OutreachAttempt).filter(
        models.OutreachAttempt.enrollment_id == enrollment.id,
        models.OutreachAttempt.id != first_attempt.id,
    ).one()
    claimed = claim_attempt(db_session, worker_name="follow-up-worker")
    assert claimed.id == follow_up.id
    execute_attempt(db_session, attempt=claimed, registry=registry)
    db_session.commit()

    # THEN: The follow-up bypasses cold-start cooldowns, remains traceable, and closes the sequence.
    assert follow_up.kind == AttemptKind.FOLLOW_UP
    assert first_attempt.status == AttemptStatus.SUCCEEDED
    assert follow_up.status == AttemptStatus.SUCCEEDED
    assert enrollment.status == EnrollmentStatus.COMPLETED
    assert enrollment.completed_at is not None
    assert point.last_cold_outreach_at == cold_contact_at
    assert company.last_cold_outreach_at == cold_company_at
    assert db_session.query(models.MessageEvent).filter(
        models.MessageEvent.outreach_attempt_id.in_((first_attempt.id, follow_up.id))
    ).count() == 2
    assert db_session.query(models.ProviderCostEvent).filter(
        models.ProviderCostEvent.outreach_attempt_id.in_((first_attempt.id, follow_up.id))
    ).count() == 2


def test_false_sequence_condition_prevents_follow_up_outreach(db_session):
    # GIVEN: A valid first step followed by a deterministically false step.
    *_, revision, enrollment, first_attempt = _runtime_graph(db_session)
    db_session.add(
        models.SequenceStep(
            owner_id=enrollment.owner_id,
            campaign_revision_id=revision.id,
            position=2,
            channel=Channel.EMAIL,
            condition_definition={"always": False},
        )
    )
    db_session.commit()
    registry = build_local_registry()

    # WHEN: The first step succeeds and evaluates the next step.
    execute_attempt(db_session, attempt=first_attempt, registry=registry)
    db_session.commit()

    # THEN: No follow-up attempt or Provider side effect is created.
    assert first_attempt.status == AttemptStatus.SUCCEEDED
    assert enrollment.status == EnrollmentStatus.COMPLETED
    assert enrollment.paused_reason == "sequence_condition_not_met"
    assert db_session.query(models.OutreachAttempt).filter_by(enrollment_id=enrollment.id).count() == 1
    assert sum(len(connector.requests) for connector in registry._connectors.values()) == 1


def test_unknown_sequence_condition_fails_closed(db_session):
    # GIVEN: A published next step containing an unsupported condition key.
    *_, revision, enrollment, first_attempt = _runtime_graph(db_session)
    db_session.add(
        models.SequenceStep(
            owner_id=enrollment.owner_id,
            campaign_revision_id=revision.id,
            position=2,
            channel=Channel.EMAIL,
            condition_definition={"run_arbitrary_rule": True},
        )
    )
    db_session.commit()

    # WHEN: The scheduler reaches that immutable definition.
    execute_attempt(db_session, attempt=first_attempt, registry=build_local_registry())
    db_session.commit()

    # THEN: It blocks for human repair and never assumes the unknown rule true.
    assert first_attempt.status == AttemptStatus.SUCCEEDED
    assert enrollment.status == EnrollmentStatus.BLOCKED
    assert "unknown_execution_conditions" in enrollment.paused_reason
    assert db_session.query(models.OutreachAttempt).filter_by(enrollment_id=enrollment.id).count() == 1
    task = db_session.query(models.Task).filter_by(
        enrollment_id=enrollment.id,
        task_type="campaign_readiness",
    ).one()
    assert task.metadata_json["auto_send_allowed"] is False


def test_step_stop_condition_halts_queued_follow_up_before_send(db_session):
    # GIVEN: A sent first step configured to stop when the contact replies.
    _, _, contact, _, _, revision, enrollment, first_attempt = _runtime_graph(db_session)
    first_step = db_session.get(models.SequenceStep, first_attempt.sequence_step_id)
    first_step.stop_condition_definition = {"stop_on_reply": True}
    db_session.add(
        models.SequenceStep(
            owner_id=enrollment.owner_id,
            campaign_revision_id=revision.id,
            position=2,
            channel=Channel.EMAIL,
            wait_minutes=0,
        )
    )
    db_session.commit()
    registry = build_local_registry()
    execute_attempt(db_session, attempt=first_attempt, registry=registry)
    db_session.commit()
    follow_up = db_session.query(models.OutreachAttempt).filter(
        models.OutreachAttempt.enrollment_id == enrollment.id,
        models.OutreachAttempt.id != first_attempt.id,
    ).one()
    conversation = models.Conversation(
        owner_id=enrollment.owner_id,
        company_id=enrollment.company_id,
        contact_id=contact.id,
        channel=Channel.EMAIL,
    )
    db_session.add(conversation)
    db_session.flush()
    reply_event = models.MessageEvent(
        owner_id=enrollment.owner_id,
        conversation_id=conversation.id,
        outreach_attempt_id=first_attempt.id,
        channel=Channel.EMAIL,
        direction=MessageDirection.OUTBOUND,
        event_type=MessageEventType.REPLIED,
        body="Provider echo that must not count as a contact reply",
        occurred_at=datetime.now(timezone.utc),
    )
    db_session.add(reply_event)
    db_session.flush()
    assert evaluate_stop_conditions(
        db_session,
        enrollment=enrollment,
        step=first_step,
    ).matched is False
    reply_event.direction = MessageDirection.INBOUND
    reply_event.body = "Please send details"
    db_session.commit()

    # WHEN: A worker revalidates the already queued follow-up before Provider I/O.
    execute_attempt(db_session, attempt=follow_up, registry=registry)
    db_session.commit()

    # THEN: The prior step's stop rule cancels it without a second send or cost.
    assert follow_up.status == AttemptStatus.CANCELLED
    assert enrollment.status == EnrollmentStatus.COMPLETED
    assert enrollment.paused_reason == "sequence_stop_condition:stop_on_reply"
    assert sum(len(connector.requests) for connector in registry._connectors.values()) == 1
    assert db_session.query(models.ProviderCostEvent).filter_by(enrollment_id=enrollment.id).count() == 1


def test_campaign_budget_is_reserved_transactionally_before_provider_call(db_session):
    # GIVEN: A campaign with one fake-call unit of capacity and two eligible contacts.
    user, _, _, _, campaign, revision, _, first_attempt = _runtime_graph(db_session)
    revision.budget_definition = {"native_limit": 1, "native_unit": "fake_calls"}
    step = db_session.get(models.SequenceStep, first_attempt.sequence_step_id)
    second_company = models.Company(owner_id=user.id, name="Second Co", normalized_domain="second.example")
    db_session.add(second_company)
    db_session.flush()
    second_contact = models.Contact(owner_id=user.id, company_id=second_company.id, full_name="Second Buyer", timezone="UTC")
    db_session.add(second_contact)
    db_session.flush()
    second_point = models.ContactPoint(
        owner_id=user.id,
        company_id=second_company.id,
        contact_id=second_contact.id,
        channel=Channel.EMAIL,
        value="buyer@second.example",
        normalized_value="buyer@second.example",
        verification_status=ContactPointVerificationStatus.VALID,
    )
    second_enrollment = models.Enrollment(
        owner_id=user.id,
        campaign_id=campaign.id,
        campaign_revision_id=revision.id,
        company_id=second_company.id,
        contact_id=second_contact.id,
        status=EnrollmentStatus.ACTIVE,
    )
    db_session.add_all([second_point, second_enrollment])
    db_session.flush()
    second_attempt = models.OutreachAttempt(
        owner_id=user.id,
        campaign_id=campaign.id,
        enrollment_id=second_enrollment.id,
        sequence_step_id=step.id,
        contact_point_id=second_point.id,
        channel=Channel.EMAIL,
        idempotency_key="runtime-attempt-budget-2",
    )
    db_session.add(second_attempt)
    db_session.commit()
    registry = build_local_registry()

    # WHEN: Both attempts are evaluated in order.
    execute_attempt(db_session, attempt=first_attempt, registry=registry)
    db_session.commit()
    execute_attempt(db_session, attempt=second_attempt, registry=registry)
    db_session.commit()

    # THEN: Only the reserved first unit reaches the connector; the second is hard-blocked.
    assert first_attempt.status == AttemptStatus.SUCCEEDED
    assert second_attempt.status == AttemptStatus.BLOCKED
    assert "campaign_budget_exhausted" in second_attempt.last_error
    assert sum(len(connector.requests) for connector in registry._connectors.values()) == 1
    assert db_session.query(models.ProviderCostEvent).filter_by(campaign_id=campaign.id).count() == 1


def test_expired_attempt_claim_becomes_unknown_with_reconciliation_task(db_session):
    # GIVEN: A durable attempt claim whose worker lease expired after a possible send.
    *_, attempt = _runtime_graph(db_session)
    attempt.status = AttemptStatus.CLAIMED
    attempt.claimed_by = "crashed-worker"
    attempt.lease_expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    db_session.commit()

    # WHEN: Another worker looks for claimable work.
    claimed = claim_attempt(db_session, worker_name="recovery-worker")
    db_session.commit()

    # THEN: The uncertain attempt is never re-claimed and receives a reconciliation task.
    assert claimed is None
    assert attempt.status == AttemptStatus.UNKNOWN
    task = db_session.query(models.Task).filter_by(attempt_id=attempt.id, task_type="reconciliation").one()
    assert task.metadata_json["auto_resend_allowed"] is False


def test_attempt_scheduler_ignores_paused_campaigns_and_enrollments(db_session):
    # GIVEN: A queued attempt whose campaign is paused.
    *_, campaign, _, enrollment, attempt = _runtime_graph(db_session)
    campaign.lifecycle = CampaignLifecycle.PAUSED
    db_session.commit()

    # WHEN: The outbound worker asks for work.
    paused_claim = claim_attempt(db_session, worker_name="scheduler-worker")

    # THEN: It does not consume or block the attempt before the campaign starts.
    assert paused_claim is None
    assert attempt.status == AttemptStatus.QUEUED

    # WHEN: The campaign runs but the enrollment itself is paused.
    campaign.lifecycle = CampaignLifecycle.RUNNING
    enrollment.status = EnrollmentStatus.PAUSED
    db_session.commit()
    enrollment_claim = claim_attempt(db_session, worker_name="scheduler-worker")

    # THEN: The attempt remains queued until both lifecycle gates are active.
    assert enrollment_claim is None
    assert attempt.status == AttemptStatus.QUEUED


def test_archiving_business_entities_preserves_immutable_execution_history(db_session):
    # GIVEN: A successful traceable attempt for an active Company, Contact, and Campaign.
    _, company, contact, _, campaign, _, _, attempt = _runtime_graph(db_session)
    execute_attempt(db_session, attempt=attempt, registry=build_local_registry())
    db_session.commit()

    # WHEN: The business entities are archived instead of hard-deleted.
    archived_at = datetime.now(timezone.utc)
    company.archived_at = archived_at
    contact.archived_at = archived_at
    campaign.archived_at = archived_at
    db_session.commit()

    # THEN: Message, cost, attempt, task/audit history remains queryable and linked.
    assert db_session.get(models.Company, company.id) is not None
    assert db_session.get(models.Contact, contact.id) is not None
    assert db_session.get(models.Campaign, campaign.id) is not None
    assert db_session.query(models.OutreachAttempt).filter_by(id=attempt.id).count() == 1
    assert db_session.query(models.MessageEvent).filter_by(outreach_attempt_id=attempt.id).count() == 1
    assert db_session.query(models.ProviderCostEvent).filter_by(outreach_attempt_id=attempt.id).count() == 1
    assert db_session.query(models.AuditEvent).filter_by(entity_type="outreach_attempt", entity_id=str(attempt.id)).count() == 1


def test_archived_contact_and_company_fail_closed_before_connector_call(db_session):
    # GIVEN: A queued attempt whose Contact and Company were archived after scheduling.
    _, company, contact, _, _, _, enrollment, attempt = _runtime_graph(db_session)
    contact.archived_at = datetime.now(timezone.utc)
    company.archived_at = datetime.now(timezone.utc)
    db_session.commit()
    registry = build_local_registry()

    # WHEN: A previously claimed worker reaches the execution-time safety gate.
    execute_attempt(db_session, attempt=attempt, registry=registry)
    db_session.commit()

    # THEN: Archival is a hard blocker and no provider/cost/message side effect occurs.
    assert attempt.status == AttemptStatus.BLOCKED
    assert "contact_archived" in attempt.last_error
    assert "company_archived" in attempt.last_error
    assert enrollment.status == EnrollmentStatus.BLOCKED
    assert sum(len(connector.requests) for connector in registry._connectors.values()) == 0
    assert db_session.query(models.MessageEvent).count() == 0
    assert db_session.query(models.ProviderCostEvent).count() == 0


def test_hard_consent_gate_cannot_be_covered_by_soft_override(db_session):
    user, _, _, point, _, _, enrollment, _ = _runtime_graph(db_session)
    db_session.add(
        models.ManualOverride(
            owner_id=user.id,
            gate=OverrideGate.FIT,
            enrollment_id=enrollment.id,
            reason="Sales knows this account",
            expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
            created_by_user_id=user.id,
        )
    )
    db_session.add(
        models.ConsentRestriction(
            owner_id=user.id,
            idempotency_key="consent-runtime-1",
            scope=RestrictionScope.CONTACT_POINT,
            channel=Channel.EMAIL,
            contact_point_id=point.id,
            reason="unsubscribe",
        )
    )
    db_session.commit()

    decision = evaluate_outreach_gates(db_session, enrollment=enrollment, contact_point=point)

    assert decision.allowed is False
    assert "consent_restriction" in decision.hard_blockers


def test_orphaned_legacy_email_suppression_remains_a_hard_v2_gate(db_session):
    user, _, _, point, _, _, enrollment, _ = _runtime_graph(db_session)
    db_session.add(
        legacy.EmailSuppression(
            user_id=user.id,
            email=f"  {point.normalized_value.upper()}  ",
            reason="unsubscribe",
            source="legacy",
        )
    )
    db_session.commit()

    decision = evaluate_outreach_gates(
        db_session,
        enrollment=enrollment,
        contact_point=point,
    )

    assert decision.allowed is False
    assert "legacy_email_suppression" in decision.hard_blockers


def test_attempt_scoped_override_cannot_be_consumed_by_another_step(db_session):
    # GIVEN: A fit override restricted to the first attempt of one Enrollment.
    user, _, _, point, _, revision, enrollment, first_attempt = _runtime_graph(db_session)
    revision.quality_gates = {"min_fit_score": 80, "require_evidence": False, "require_timezone": False}
    second_attempt = models.OutreachAttempt(
        owner_id=user.id,
        campaign_id=enrollment.campaign_id,
        enrollment_id=enrollment.id,
        sequence_step_id=first_attempt.sequence_step_id,
        contact_point_id=point.id,
        channel=Channel.EMAIL,
        idempotency_key="runtime-attempt-override-2",
    )
    db_session.add(second_attempt)
    db_session.flush()
    db_session.add(
        models.ManualOverride(
            owner_id=user.id,
            gate=OverrideGate.FIT,
            enrollment_id=enrollment.id,
            attempt_id=first_attempt.id,
            reason="Only the reviewed first draft",
            expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
            created_by_user_id=user.id,
        )
    )
    db_session.commit()

    # WHEN: The gate is evaluated for the other attempt.
    decision = evaluate_outreach_gates(
        db_session,
        enrollment=enrollment,
        contact_point=point,
        attempt_id=second_attempt.id,
    )

    # THEN: The unrelated override does not cover the fit blocker.
    assert decision.allowed is False
    assert decision.soft_blockers == ["fit"]
    assert decision.overrides == []


def test_global_contact_point_and_company_cold_start_cooldowns(db_session):
    # GIVEN: An eligible enrollment with deterministic recent outreach timestamps.
    _, company, _, point, _, _, enrollment, _ = _runtime_graph(db_session)
    current = datetime(2026, 7, 15, 8, 0, tzinfo=timezone.utc)

    # WHEN: The same contact point was cold-contacted within 14 days.
    point.last_cold_outreach_at = current - timedelta(days=13)
    company.last_cold_outreach_at = None
    contact_decision = evaluate_outreach_gates(db_session, enrollment=enrollment, contact_point=point, now=current)

    # THEN: The contact-point hard gate blocks every campaign.
    assert contact_decision.allowed is False
    assert "contact_point_cooldown_14d" in contact_decision.hard_blockers

    # WHEN: A different contact is eligible but the company was contacted within 24 hours.
    point.last_cold_outreach_at = None
    company.last_cold_outreach_at = current - timedelta(hours=23)
    company_decision = evaluate_outreach_gates(db_session, enrollment=enrollment, contact_point=point, now=current)

    # THEN: The company-level hard gate blocks the cold start.
    assert company_decision.allowed is False
    assert "company_cooldown_24h" in company_decision.hard_blockers

    follow_up = evaluate_outreach_gates(
        db_session,
        enrollment=enrollment,
        contact_point=point,
        now=current,
        cold_start=False,
    )
    assert follow_up.allowed is True

    # WHEN: Both global cooldown windows have elapsed.
    point.last_cold_outreach_at = current - timedelta(days=15)
    company.last_cold_outreach_at = current - timedelta(hours=25)
    elapsed = evaluate_outreach_gates(db_session, enrollment=enrollment, contact_point=point, now=current)

    # THEN: No cooldown blocker remains.
    assert elapsed.allowed is True
    assert not {"contact_point_cooldown_14d", "company_cooldown_24h"}.intersection(elapsed.hard_blockers)


def test_temporary_company_cooldown_defers_instead_of_permanently_blocking(db_session):
    # GIVEN: A claimed cold attempt whose company was contacted less than 24 hours ago.
    _, company, _, _, _, _, enrollment, attempt = _runtime_graph(db_session)
    last_cold = datetime.now(timezone.utc) - timedelta(hours=1)
    company.last_cold_outreach_at = last_cold
    attempt.status = AttemptStatus.CLAIMED
    attempt.claimed_by = "cooldown-worker"
    attempt.lease_expires_at = datetime.now(timezone.utc) + timedelta(seconds=90)
    db_session.commit()
    registry = build_local_registry()

    # WHEN: The execution-time hard gate evaluates the temporary conflict.
    execute_attempt(db_session, attempt=attempt, registry=registry)
    db_session.commit()

    # THEN: The immutable attempt is re-scheduled at the exact safe boundary without a call.
    assert attempt.status == AttemptStatus.QUEUED
    assert attempt.claimed_by is None
    assert attempt.lease_expires_at is None
    assert as_utc(attempt.scheduled_at) == last_cold + timedelta(hours=24)
    assert enrollment.status == EnrollmentStatus.ACTIVE
    assert sum(len(connector.requests) for connector in registry._connectors.values()) == 0
    assert db_session.query(models.Task).filter_by(attempt_id=attempt.id).count() == 0
    stage = db_session.query(models.StageRuntime).filter_by(
        campaign_id=attempt.campaign_id,
        stage_name="outbound",
    ).one()
    assert stage.status == "backoff"


def test_inbox_cursor_resets_only_when_uidvalidity_changes(db_session):
    user = legacy.User(username="cursor-owner", hashed_password="x", is_active=True)
    db_session.add(user)
    db_session.flush()
    cursor, start = prepare_cursor(
        db_session,
        owner_id=user.id,
        channel_account_id="email-account-1",
        uid_validity="100",
    )
    assert start == 1
    record_success(db_session, cursor, last_uid=42)
    db_session.commit()

    same, next_uid = prepare_cursor(
        db_session,
        owner_id=user.id,
        channel_account_id="email-account-1",
        uid_validity="100",
    )
    with pytest.raises(ValueError, match="cannot move backwards"):
        record_success(db_session, same, last_uid=41)
    assert same.last_uid == 42

    changed, reset_uid = prepare_cursor(
        db_session,
        owner_id=user.id,
        channel_account_id="email-account-1",
        uid_validity="101",
    )

    assert same.id == cursor.id
    assert next_uid == 43
    assert changed.id == cursor.id
    assert reset_uid == 1


def test_inbox_cursor_account_identifier_is_tenant_scoped(db_session):
    # GIVEN: Two owners use the same provider-local account identifier.
    first_owner = legacy.User(username="cursor-tenant-a", hashed_password="x", is_active=True)
    second_owner = legacy.User(username="cursor-tenant-b", hashed_password="x", is_active=True)
    db_session.add_all([first_owner, second_owner])
    db_session.flush()

    # WHEN: Each owner initializes an independent cursor.
    first, _ = prepare_cursor(
        db_session,
        owner_id=first_owner.id,
        channel_account_id="shared-provider-id",
        uid_validity="100",
    )
    second, _ = prepare_cursor(
        db_session,
        owner_id=second_owner.id,
        channel_account_id="shared-provider-id",
        uid_validity="200",
    )
    db_session.commit()

    # THEN: The global-looking Provider ID cannot cross tenant state.
    assert first.id != second.id
    assert first.owner_id == first_owner.id
    assert second.owner_id == second_owner.id


def test_mysql_inbox_cursor_query_is_owner_scoped_and_row_locked():
    # GIVEN: An existing cursor queried through a MySQL-bound session.
    class FakeQuery:
        def __init__(self):
            self.filters = {}
            self.did_populate_existing = False
            self.did_lock = False
            self.cursor = type("Cursor", (), {"uid_validity": "100", "last_uid": 42})()

        def filter_by(self, **filters):
            self.filters = filters
            return self

        def populate_existing(self):
            self.did_populate_existing = True
            return self

        def with_for_update(self):
            self.did_lock = True
            return self

        def first(self):
            return self.cursor

    class FakeSession:
        bind = type("Bind", (), {"dialect": type("Dialect", (), {"name": "mysql"})()})()

        def __init__(self):
            self.cursor_query = FakeQuery()

        def query(self, model):
            assert model is models.InboxCursor
            return self.cursor_query

    db = FakeSession()

    # WHEN: The consumer prepares its next UID.
    cursor, next_uid = prepare_cursor(
        db,
        owner_id=27,
        channel_account_id="owner-27-email",
        uid_validity="100",
    )

    # THEN: Tenant identity is part of the lookup and the row is refreshed and locked.
    assert cursor is db.cursor_query.cursor
    assert next_uid == 43
    assert db.cursor_query.filters == {
        "owner_id": 27,
        "channel_account_id": "owner-27-email",
    }
    assert db.cursor_query.did_populate_existing is True
    assert db.cursor_query.did_lock is True
