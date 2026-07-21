from __future__ import annotations

from datetime import datetime, timezone

import models as legacy
from database import SessionLocal
from product_v2 import models
from product_v2.connectors.base import ConnectorRequest, ConnectorResult
from product_v2.connectors.registry import ConnectorRegistry
from product_v2.enums import (
    AttemptStatus,
    CampaignLifecycle,
    CampaignRevisionStatus,
    Channel,
    ContactPointVerificationStatus,
    EnrollmentStatus,
    MessageDirection,
    MessageEventType,
    ProviderCostStatus,
    SafetyLockScope,
    TaskStatus,
    TaskType,
)
from product_v2.runtime.events import ingest_provider_event
from product_v2.runtime.outbound import execute_attempt
from product_v2.runtime.queue import claim_attempt, lease_fence
from product_v2.schemas import WebhookEventCreate


def _two_campaign_graph(db):
    user = legacy.User(username="unknown-race-owner", hashed_password="x", is_active=True)
    db.add(user)
    db.flush()
    company = models.Company(
        owner_id=user.id,
        name="Shared Buyer",
        normalized_domain="shared-buyer.example",
    )
    db.add(company)
    db.flush()
    contact = models.Contact(
        owner_id=user.id,
        company_id=company.id,
        full_name="Shared Contact",
        timezone="UTC",
    )
    db.add(contact)
    db.flush()
    point = models.ContactPoint(
        owner_id=user.id,
        company_id=company.id,
        contact_id=contact.id,
        channel=Channel.EMAIL,
        value="shared@shared-buyer.example",
        normalized_value="shared@shared-buyer.example",
        verification_status=ContactPointVerificationStatus.VALID,
    )
    db.add(point)
    db.flush()

    campaigns = []
    enrollments = []
    attempts = []
    for index in (1, 2):
        campaign = models.Campaign(
            owner_id=user.id,
            name=f"Cross-campaign {index}",
            lifecycle=CampaignLifecycle.RUNNING,
        )
        db.add(campaign)
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
            idempotency_key=f"cross-campaign-attempt-{index}",
        )
        db.add(attempt)
        campaigns.append(campaign)
        enrollments.append(enrollment)
        attempts.append(attempt)
    db.commit()
    return user, company, contact, point, campaigns, enrollments, attempts


class _TimeoutConnector:
    channel = Channel.EMAIL
    provider = "fake-timeout-email"
    is_fake = True

    def __init__(self):
        self.calls: list[int] = []

    def send(self, request: ConnectorRequest) -> ConnectorResult:
        self.calls.append(int(request.metadata["attempt_id"]))
        raise TimeoutError("ambiguous fake timeout")


def test_authoritative_sent_webhook_wins_race_with_sending_worker(db_session):
    # GIVEN: A fenced worker has committed SENDING, RESERVED cost and in-flight
    # locks, then receives an authoritative webhook before send() returns.
    user, company, _, point, _, _, attempts = _two_campaign_graph(db_session)
    attempt = attempts[0]
    claimed = claim_attempt(db_session, worker_name="webhook-race-worker")
    assert claimed.id == attempt.id
    fence = lease_fence(claimed)
    db_session.commit()

    class WebhookDuringSendConnector:
        channel = Channel.EMAIL
        provider = "fake-webhook-race"
        is_fake = True

        def __init__(self):
            self.calls = 0

        def send(self, request: ConnectorRequest) -> ConnectorResult:
            self.calls += 1
            webhook_db = SessionLocal()
            try:
                ingest_provider_event(
                    webhook_db,
                    owner_id=user.id,
                    provider=self.provider,
                    idempotency_key="sending-race-delivered-1",
                    payload=WebhookEventCreate(
                        channel=Channel.EMAIL,
                        direction=MessageDirection.OUTBOUND,
                        event_type=MessageEventType.DELIVERED,
                        attempt_id=attempt.id,
                        provider_message_id="sending-race-message",
                    ),
                )
                webhook_db.commit()
            finally:
                webhook_db.close()
            return ConnectorResult(
                accepted=True,
                provider=self.provider,
                provider_message_id="sending-race-message",
                raw={"fake": True, "network_calls": 0},
            )

    connector = WebhookDuringSendConnector()
    registry = ConnectorRegistry()
    registry.register(connector)

    # WHEN: The original worker returns after the webhook transaction committed.
    result = execute_attempt(
        db_session,
        attempt=claimed,
        registry=registry,
        lease_fence=fence,
    )
    db_session.commit()
    db_session.expire_all()

    # THEN: The authoritative event remains the single terminal result. The
    # stale worker neither duplicates SENT nor creates false reconciliation.
    current = db_session.get(models.OutreachAttempt, result.id)
    assert connector.calls == 1
    assert current.status == AttemptStatus.SUCCEEDED
    assert current.sent_at is not None
    assert current.provider_message_id == "sending-race-message"
    assert db_session.get(models.ContactPoint, point.id).last_cold_outreach_at is not None
    assert db_session.get(models.Company, company.id).last_cold_outreach_at is not None
    cost = db_session.query(models.ProviderCostEvent).filter_by(outreach_attempt_id=current.id).one()
    assert cost.status == ProviderCostStatus.CHARGED
    assert db_session.query(models.MessageEvent).filter_by(outreach_attempt_id=current.id).count() == 1
    assert db_session.query(models.Task).filter_by(
        attempt_id=current.id,
        task_type=TaskType.RECONCILIATION,
    ).count() == 0
    assert db_session.query(models.SafetyLock).filter(
        models.SafetyLock.code.like(f"provider_in_flight:{current.id}:%"),
        models.SafetyLock.active.is_(True),
    ).count() == 0
    assert db_session.query(models.AuditEvent).filter_by(
        action="outreach_attempt.provider_boundary_reconciled_succeeded",
        entity_type="outreach_attempt",
        entity_id=str(current.id),
    ).count() == 1


def test_in_flight_lock_prevents_second_campaign_send_and_unknown_pauses_both(db_session):
    # GIVEN: Two Campaigns target the same email/contact/company, and A crosses
    # the committed Provider boundary before its outcome is known.
    _, _, _, _, _, enrollments, attempts = _two_campaign_graph(db_session)
    attempt_a, attempt_b = attempts

    class RaceConnector(_TimeoutConnector):
        def __init__(self):
            super().__init__()
            self.registry = None
            self.observed_b_status = None

        def send(self, request: ConnectorRequest) -> ConnectorResult:
            attempt_id = int(request.metadata["attempt_id"])
            self.calls.append(attempt_id)
            if attempt_id != attempt_a.id:
                raise AssertionError("Campaign B crossed the Provider boundary")

            # A has committed SENDING + RESERVED + its attempt-bound locks.
            # A separate worker/session now tries to send B while A is in flight.
            other = SessionLocal()
            try:
                candidate = other.get(models.OutreachAttempt, attempt_b.id)
                execute_attempt(other, attempt=candidate, registry=self.registry)
                other.commit()
                self.observed_b_status = candidate.status
            finally:
                other.close()
            raise TimeoutError("A's Provider outcome is ambiguous")

    connector = RaceConnector()
    registry = ConnectorRegistry()
    registry.register(connector)
    connector.registry = registry

    # WHEN: A times out after B has attempted to race it.
    execute_attempt(db_session, attempt=attempt_a, registry=registry)
    db_session.commit()
    db_session.expire_all()

    # THEN: Only A crossed the Provider boundary. B stayed retryable during the
    # call, then UNKNOWN paused every active Enrollment at that contact/company.
    current_a = db_session.get(models.OutreachAttempt, attempt_a.id)
    current_b = db_session.get(models.OutreachAttempt, attempt_b.id)
    assert connector.calls == [attempt_a.id]
    assert connector.observed_b_status == AttemptStatus.QUEUED
    assert current_a.status == AttemptStatus.UNKNOWN
    assert current_b.status == AttemptStatus.QUEUED
    assert [db_session.get(models.Enrollment, row.id).status for row in enrollments] == [
        EnrollmentStatus.PAUSED,
        EnrollmentStatus.PAUSED,
    ]
    active_locks = db_session.query(models.SafetyLock).filter_by(active=True).all()
    assert {lock.scope for lock in active_locks} == {
        SafetyLockScope.CONTACT,
        SafetyLockScope.COMPANY,
    }
    assert {lock.metadata_json["outreach_attempt_id"] for lock in active_locks} == {attempt_a.id}
    assert claim_attempt(db_session, worker_name="must-not-send-b") is None
    assert connector.calls == [attempt_a.id]


def test_authoritative_sent_event_fully_reconciles_unknown_and_only_its_locks(db_session):
    user, company, contact, point, _, _, attempts = _two_campaign_graph(db_session)
    attempt = attempts[0]
    connector = _TimeoutConnector()
    registry = ConnectorRegistry()
    registry.register(connector)
    execute_attempt(db_session, attempt=attempt, registry=registry)
    db_session.commit()

    # An unrelated manual lock must survive automatic reconciliation.
    unrelated = models.SafetyLock(
        owner_id=user.id,
        scope=SafetyLockScope.CONTACT,
        contact_id=contact.id,
        code="manual-risk-stop",
        reason="Human review remains required",
    )
    db_session.add(unrelated)
    db_session.commit()
    occurred_at = datetime.now(timezone.utc)

    event = ingest_provider_event(
        db_session,
        owner_id=user.id,
        provider="fake-timeout-email",
        idempotency_key="unknown-authoritative-sent-1",
        payload=WebhookEventCreate(
            channel=Channel.EMAIL,
            direction=MessageDirection.OUTBOUND,
            event_type=MessageEventType.DELIVERED,
            attempt_id=attempt.id,
            provider_message_id="authoritative-provider-message",
            occurred_at=occurred_at,
        ),
    )
    db_session.commit()
    db_session.refresh(attempt)
    db_session.refresh(point)
    db_session.refresh(company)
    db_session.refresh(unrelated)

    assert attempt.status == AttemptStatus.SUCCEEDED
    assert attempt.sent_at is not None
    assert attempt.provider_message_id == "authoritative-provider-message"
    assert point.last_cold_outreach_at is not None
    assert company.last_cold_outreach_at is not None
    cost = db_session.query(models.ProviderCostEvent).filter_by(outreach_attempt_id=attempt.id).one()
    assert cost.status == ProviderCostStatus.CHARGED
    assert cost.result_count == 1
    assert cost.metadata_json["requires_reconciliation"] is False
    assert cost.metadata_json["reconciled_by_message_event_id"] == event.id
    task = db_session.query(models.Task).filter_by(
        attempt_id=attempt.id,
        task_type=TaskType.RECONCILIATION,
    ).one()
    assert task.status == TaskStatus.COMPLETED
    assert task.completed_at is not None
    assert task.metadata_json["resolution"] == "sent"
    assert db_session.query(models.SafetyLock).filter(
        models.SafetyLock.code.like(f"provider_in_flight:{attempt.id}:%"),
        models.SafetyLock.active.is_(True),
    ).count() == 0
    assert unrelated.active is True
    assert db_session.query(models.AuditEvent).filter_by(
        action="outreach_attempt.unknown_reconciled_succeeded",
        entity_type="outreach_attempt",
        entity_id=str(attempt.id),
    ).count() == 1


def test_failed_event_requires_explicit_confirmed_not_sent_before_unlock(db_session):
    user, _, _, _, _, enrollments, attempts = _two_campaign_graph(db_session)
    attempt = attempts[0]
    connector = _TimeoutConnector()
    registry = ConnectorRegistry()
    registry.register(connector)
    execute_attempt(db_session, attempt=attempt, registry=registry)
    db_session.commit()

    # A generic failure is not authoritative enough to make a timeout safe.
    ingest_provider_event(
        db_session,
        owner_id=user.id,
        provider="fake-timeout-email",
        idempotency_key="unknown-ambiguous-failed-1",
        payload=WebhookEventCreate(
            channel=Channel.EMAIL,
            direction=MessageDirection.OUTBOUND,
            event_type=MessageEventType.FAILED,
            attempt_id=attempt.id,
        ),
    )
    db_session.commit()
    db_session.refresh(attempt)
    assert attempt.status == AttemptStatus.UNKNOWN
    assert db_session.query(models.SafetyLock).filter(
        models.SafetyLock.code.like(f"provider_in_flight:{attempt.id}:%"),
        models.SafetyLock.active.is_(True),
    ).count() == 2
    task = db_session.query(models.Task).filter_by(
        attempt_id=attempt.id,
        task_type=TaskType.RECONCILIATION,
    ).one()
    assert task.status == TaskStatus.OPEN

    # Only explicit normalized Provider evidence that nothing was sent resolves
    # FAILED/BOUNCED, releases this attempt's locks, and closes reconciliation.
    ingest_provider_event(
        db_session,
        owner_id=user.id,
        provider="fake-timeout-email",
        idempotency_key="unknown-confirmed-not-sent-1",
        payload=WebhookEventCreate(
            channel=Channel.EMAIL,
            direction=MessageDirection.OUTBOUND,
            event_type=MessageEventType.FAILED,
            attempt_id=attempt.id,
            metadata_json={"confirmed_not_sent": True},
        ),
    )
    db_session.commit()
    db_session.refresh(attempt)
    db_session.refresh(task)
    assert attempt.status == AttemptStatus.FAILED
    assert task.status == TaskStatus.COMPLETED
    assert db_session.query(models.SafetyLock).filter(
        models.SafetyLock.code.like(f"provider_in_flight:{attempt.id}:%"),
        models.SafetyLock.active.is_(True),
    ).count() == 0
    cost = db_session.query(models.ProviderCostEvent).filter_by(outreach_attempt_id=attempt.id).one()
    assert cost.status == ProviderCostStatus.FAILED
    assert all(db_session.get(models.Enrollment, row.id).status == EnrollmentStatus.PAUSED for row in enrollments)
