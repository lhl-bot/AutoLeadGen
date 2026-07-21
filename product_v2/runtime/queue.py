"""Transactional job/outbox, attempt claim, heartbeat, and retry primitives."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Iterable, Optional

from sqlalchemy import inspect as sa_inspect, or_
from sqlalchemy.orm import Session

from product_v2 import models
from product_v2.enums import (
    AttemptKind,
    AttemptStatus,
    CampaignLifecycle,
    EnrollmentStatus,
    JobStatus,
    ProviderCostStatus,
    SafetyLockScope,
    StageStatus,
    TaskPriority,
    TaskStatus,
    TaskType,
    WorkerType,
)


@dataclass(frozen=True)
class LeaseFence:
    """Immutable proof of the exact claim granted to one worker.

    ORM instances expire after ``Session.commit()``.  Reading ``lease_owner`` or
    ``attempts`` from that expired instance can therefore pick up a *new*
    worker's claim.  Keeping these values outside mapped state prevents a stale
    worker from accidentally impersonating the replacement worker.
    """

    owner: str
    generation: int


class LeaseFenceLost(RuntimeError):
    """Raised when a worker no longer owns the claim it is trying to finish."""


_LEASE_FENCE_ATTRIBUTE = "_product_v2_lease_fence"


def _begin_mysql_claim_transaction(db: Session) -> None:
    """Use READ COMMITTED for MySQL SKIP LOCKED queue consumers.

    MySQL's default REPEATABLE READ takes next-key locks while walking an
    ordered queue. One worker can therefore lock the gap containing the next
    eligible row, causing a second worker to see no work despite SKIP LOCKED.
    Claim functions own a fresh, short transaction and opt it into READ
    COMMITTED before their first statement so only the selected record locks.
    """

    bind = db.get_bind()
    if bind.dialect.name == "mysql" and not db.in_transaction():
        db.connection(execution_options={"isolation_level": "READ COMMITTED"})


def entity_id(instance) -> int:
    """Return a mapped identity without refreshing an expired ORM instance."""

    identity = sa_inspect(instance).identity
    if not identity:
        raise ValueError("lease-fenced entity must be persistent")
    return int(identity[0])


def lease_fence(instance) -> Optional[LeaseFence]:
    """Return the immutable fence captured when ``instance`` was claimed."""

    value = getattr(instance, _LEASE_FENCE_ATTRIBUTE, None)
    return value if isinstance(value, LeaseFence) else None


def _attach_lease_fence(instance, *, owner: str, generation: int) -> LeaseFence:
    fence = LeaseFence(owner=owner, generation=generation)
    setattr(instance, _LEASE_FENCE_ATTRIBUTE, fence)
    return fence


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def heartbeat(
    db: Session,
    *,
    worker_name: str,
    worker_type: WorkerType,
    status: StageStatus,
    lease_seconds: int = 90,
    details: Optional[dict] = None,
) -> models.WorkerHeartbeat:
    row = db.query(models.WorkerHeartbeat).filter_by(worker_name=worker_name).first()
    if not row:
        row = models.WorkerHeartbeat(worker_name=worker_name, worker_type=worker_type)
        db.add(row)
    row.worker_type = worker_type
    row.status = status
    row.last_seen_at = utcnow()
    row.lease_expires_at = row.last_seen_at + timedelta(seconds=lease_seconds)
    row.details = details or {}
    db.flush()
    return row


def set_stage_runtime(
    db: Session,
    *,
    owner_id: int,
    campaign_id: int,
    stage_name: str,
    status: StageStatus,
    reason: Optional[str] = None,
    details: Optional[dict] = None,
) -> models.StageRuntime:
    row = db.query(models.StageRuntime).filter_by(
        campaign_id=campaign_id,
        stage_name=stage_name,
    ).first()
    if not row:
        row = models.StageRuntime(
            owner_id=owner_id,
            campaign_id=campaign_id,
            stage_name=stage_name,
        )
        db.add(row)
    current = utcnow()
    row.status = status
    row.reason = reason
    row.details = details or {}
    if status == StageStatus.RUNNING:
        row.last_started_at = current
    elif status == StageStatus.IDLE:
        row.last_succeeded_at = current
    elif status == StageStatus.FAILED:
        row.last_failed_at = current
    db.flush()
    return row


def claim_job(
    db: Session,
    *,
    worker_name: str,
    queues: Iterable[str],
    lease_seconds: int = 90,
    now: Optional[datetime] = None,
) -> Optional[models.AutomationJob]:
    _begin_mysql_claim_transaction(db)
    current = now or utcnow()
    expired_query = db.query(models.AutomationJob).filter(
        models.AutomationJob.status.in_((JobStatus.CLAIMED, JobStatus.RUNNING)),
        models.AutomationJob.lease_expires_at.isnot(None),
        models.AutomationJob.lease_expires_at < current,
    ).order_by(models.AutomationJob.lease_expires_at.asc(), models.AutomationJob.id.asc())
    if db.bind and db.bind.dialect.name == "mysql":
        # A bulk UPDATE scans locked rows and defeats SKIP LOCKED on the actual
        # claim below. Reap only rows this transaction can lock immediately.
        expired_query = expired_query.with_for_update(skip_locked=True)
    for expired in expired_query.limit(100).all():
        expired.lease_owner = None
        expired.lease_expires_at = None
        expired.last_error = "lease_expired"
        if expired.attempts >= expired.max_attempts:
            expired.status = JobStatus.FAILED
            expired.completed_at = current
        else:
            expired.status = JobStatus.RETRY
    db.flush()
    query = db.query(models.AutomationJob).filter(
        models.AutomationJob.queue.in_(tuple(queues)),
        models.AutomationJob.status.in_((JobStatus.PENDING, JobStatus.RETRY)),
        models.AutomationJob.scheduled_at <= current,
        models.AutomationJob.attempts < models.AutomationJob.max_attempts,
    ).order_by(
        models.AutomationJob.priority.desc(),
        models.AutomationJob.scheduled_at.asc(),
        models.AutomationJob.id.asc(),
    )
    if db.bind and db.bind.dialect.name == "mysql":
        # MySQL can range-lock every ordered candidate before LIMIT is applied,
        # making a second SKIP LOCKED consumer return no work. First read a
        # bounded, non-locking candidate list, then acquire row locks by PK and
        # re-check every eligibility predicate under that lock.
        candidate_ids = [row[0] for row in query.with_entities(models.AutomationJob.id).limit(100).all()]
        job = None
        for candidate_id in candidate_ids:
            job = db.query(models.AutomationJob).filter(
                models.AutomationJob.id == candidate_id,
                models.AutomationJob.queue.in_(tuple(queues)),
                models.AutomationJob.status.in_((JobStatus.PENDING, JobStatus.RETRY)),
                models.AutomationJob.scheduled_at <= current,
                models.AutomationJob.attempts < models.AutomationJob.max_attempts,
            ).with_for_update(skip_locked=True).first()
            if job is not None:
                break
    else:
        job = query.first()
    if not job:
        return None
    job.status = JobStatus.CLAIMED
    job.lease_owner = worker_name
    job.lease_expires_at = current + timedelta(seconds=lease_seconds)
    job.attempts += 1
    db.flush()
    _attach_lease_fence(job, owner=worker_name, generation=job.attempts)
    return job


def _job_fence_query(
    db: Session,
    *,
    job_id: int,
    fence: LeaseFence,
    now: datetime,
):
    return db.query(models.AutomationJob).filter(
        models.AutomationJob.id == job_id,
        models.AutomationJob.lease_owner == fence.owner,
        models.AutomationJob.attempts == fence.generation,
        models.AutomationJob.lease_expires_at.isnot(None),
        models.AutomationJob.lease_expires_at >= now,
        models.AutomationJob.status.in_((JobStatus.CLAIMED, JobStatus.RUNNING)),
    )


def start_job(
    db: Session,
    job: models.AutomationJob,
    fence: LeaseFence,
    *,
    now: Optional[datetime] = None,
) -> models.AutomationJob:
    """Fence and move one claimed job to RUNNING before domain mutations."""

    current = now or utcnow()
    job_id = entity_id(job)
    updated = _job_fence_query(db, job_id=job_id, fence=fence, now=current).update(
        {"status": JobStatus.RUNNING},
        synchronize_session=False,
    )
    if updated != 1:
        raise LeaseFenceLost(
            f"automation_job:{job_id}:lease_fence_lost:{fence.owner}:{fence.generation}"
        )
    db.flush()
    db.expire(job)
    return job


def complete_job(
    db: Session,
    job: models.AutomationJob,
    result: Optional[dict] = None,
    *,
    fence: Optional[LeaseFence] = None,
    now: Optional[datetime] = None,
) -> None:
    current = now or utcnow()
    if fence is None:
        job.status = JobStatus.SUCCEEDED
        job.result = result or {}
        job.completed_at = current
        job.lease_owner = None
        job.lease_expires_at = None
        return
    job_id = entity_id(job)
    updated = _job_fence_query(db, job_id=job_id, fence=fence, now=current).update(
        {
            "status": JobStatus.SUCCEEDED,
            "result": result or {},
            "completed_at": current,
            "lease_owner": None,
            "lease_expires_at": None,
        },
        synchronize_session=False,
    )
    if updated != 1:
        raise LeaseFenceLost(
            f"automation_job:{job_id}:lease_fence_lost:{fence.owner}:{fence.generation}"
        )
    db.expire(job)


def fail_job(
    db: Session,
    job: models.AutomationJob,
    error: str,
    retry_delay_seconds: int = 30,
    *,
    fence: Optional[LeaseFence] = None,
    now: Optional[datetime] = None,
) -> None:
    current = now or utcnow()
    if fence is None:
        job.last_error = error[:4000]
        job.lease_owner = None
        job.lease_expires_at = None
        if job.attempts < job.max_attempts:
            job.status = JobStatus.RETRY
            job.scheduled_at = current + timedelta(seconds=retry_delay_seconds)
        else:
            job.status = JobStatus.FAILED
            job.completed_at = current
        return

    job_id = entity_id(job)
    terminal = fence.generation >= job.max_attempts
    values = {
        "status": JobStatus.FAILED if terminal else JobStatus.RETRY,
        "last_error": error[:4000],
        "lease_owner": None,
        "lease_expires_at": None,
    }
    if terminal:
        values["completed_at"] = current
    else:
        values["scheduled_at"] = current + timedelta(seconds=retry_delay_seconds)
    updated = _job_fence_query(db, job_id=job_id, fence=fence, now=current).update(
        values,
        synchronize_session=False,
    )
    if updated != 1:
        raise LeaseFenceLost(
            f"automation_job:{job_id}:lease_fence_lost:{fence.owner}:{fence.generation}"
        )
    db.expire(job)


def claim_attempt(
    db: Session,
    *,
    worker_name: str,
    lease_seconds: int = 90,
    now: Optional[datetime] = None,
) -> Optional[models.OutreachAttempt]:
    _begin_mysql_claim_transaction(db)
    current = now or utcnow()
    expired_query = db.query(models.OutreachAttempt).filter(
        models.OutreachAttempt.status.in_((AttemptStatus.CLAIMED, AttemptStatus.SENDING)),
        models.OutreachAttempt.lease_expires_at.isnot(None),
        models.OutreachAttempt.lease_expires_at < current,
    ).order_by(
        models.OutreachAttempt.lease_expires_at.asc(),
        models.OutreachAttempt.id.asc(),
    )
    if db.bind and db.bind.dialect.name == "mysql":
        expired_query = expired_query.with_for_update(skip_locked=True)
    for expired in expired_query.limit(100).all():
        mark_attempt_unknown(db, expired, "lease_expired_after_claim")
    query = db.query(models.OutreachAttempt).join(
        models.Campaign, models.Campaign.id == models.OutreachAttempt.campaign_id
    ).join(
        models.Enrollment, models.Enrollment.id == models.OutreachAttempt.enrollment_id
    ).filter(
        models.OutreachAttempt.status == AttemptStatus.QUEUED,
        models.OutreachAttempt.scheduled_at <= current,
        models.Campaign.lifecycle == CampaignLifecycle.RUNNING,
        models.Enrollment.status == EnrollmentStatus.ACTIVE,
        models.Enrollment.archived_at.is_(None),
    ).order_by(
        models.Campaign.priority.desc(),
        models.OutreachAttempt.scheduled_at.asc(),
        models.OutreachAttempt.id.asc(),
    )
    if db.bind and db.bind.dialect.name == "mysql":
        # As with AutomationJob, locking this ordered join directly lets MySQL
        # lock every examined Attempt (and the shared Campaign row) before
        # LIMIT is applied. A second SKIP LOCKED worker can then incorrectly
        # observe an empty queue. Read a small ordered candidate window first,
        # lock only each Attempt by primary key, and re-check every eligibility
        # predicate while that Attempt lock is held.
        candidate_ids = [
            row[0]
            for row in query.with_entities(models.OutreachAttempt.id).limit(100).all()
        ]
        attempt = None
        for candidate_id in candidate_ids:
            candidate = db.query(models.OutreachAttempt).filter(
                models.OutreachAttempt.id == candidate_id,
                models.OutreachAttempt.status == AttemptStatus.QUEUED,
                models.OutreachAttempt.scheduled_at <= current,
            ).with_for_update(skip_locked=True).first()
            if candidate is None:
                continue
            campaign_lifecycle = db.query(models.Campaign.lifecycle).filter(
                models.Campaign.id == candidate.campaign_id,
            ).scalar()
            enrollment_state = db.query(
                models.Enrollment.status,
                models.Enrollment.archived_at,
            ).filter(
                models.Enrollment.id == candidate.enrollment_id,
            ).first()
            if (
                campaign_lifecycle == CampaignLifecycle.RUNNING
                and enrollment_state is not None
                and enrollment_state.status == EnrollmentStatus.ACTIVE
                and enrollment_state.archived_at is None
            ):
                attempt = candidate
                break
    else:
        attempt = query.first()
    if not attempt:
        return None
    attempt.status = AttemptStatus.CLAIMED
    attempt.claimed_by = worker_name
    attempt.lease_expires_at = current + timedelta(seconds=lease_seconds)
    attempt.attempt_count += 1
    db.flush()
    _attach_lease_fence(attempt, owner=worker_name, generation=attempt.attempt_count)
    return attempt


def lock_attempt_for_fence(
    db: Session,
    *,
    attempt_id: int,
    fence: LeaseFence,
    statuses: Iterable[AttemptStatus] = (AttemptStatus.CLAIMED, AttemptStatus.SENDING),
    now: Optional[datetime] = None,
) -> Optional[models.OutreachAttempt]:
    """Lock and return an attempt only while the exact claim is still valid."""

    current = now or utcnow()
    query = db.query(models.OutreachAttempt).filter(
        models.OutreachAttempt.id == attempt_id,
        models.OutreachAttempt.claimed_by == fence.owner,
        models.OutreachAttempt.attempt_count == fence.generation,
        models.OutreachAttempt.status.in_(tuple(statuses)),
        models.OutreachAttempt.lease_expires_at.isnot(None),
        models.OutreachAttempt.lease_expires_at >= current,
    )
    if db.bind and db.bind.dialect.name == "mysql":
        query = query.with_for_update()
    return query.populate_existing().first()


def renew_attempt_lease(
    db: Session,
    *,
    attempt_id: int,
    fence: LeaseFence,
    lease_seconds: int = 90,
    now: Optional[datetime] = None,
) -> bool:
    """Atomically validate and renew a SENDING attempt before Provider I/O."""

    current = now or utcnow()
    updated = db.query(models.OutreachAttempt).filter(
        models.OutreachAttempt.id == attempt_id,
        models.OutreachAttempt.claimed_by == fence.owner,
        models.OutreachAttempt.attempt_count == fence.generation,
        models.OutreachAttempt.status == AttemptStatus.SENDING,
        models.OutreachAttempt.lease_expires_at.isnot(None),
        models.OutreachAttempt.lease_expires_at >= current,
    ).update(
        {"lease_expires_at": current + timedelta(seconds=lease_seconds)},
        synchronize_session=False,
    )
    return updated == 1


def _ensure_reconciliation_task(
    db: Session,
    attempt: models.OutreachAttempt,
    reason: str,
) -> models.Task:
    task = db.query(models.Task).filter_by(
        attempt_id=attempt.id,
        task_type=TaskType.RECONCILIATION,
    ).first()
    if task:
        return task
    task = models.Task(
        owner_id=attempt.owner_id,
        task_type=TaskType.RECONCILIATION,
        status=TaskStatus.OPEN,
        priority=TaskPriority.URGENT,
        campaign_id=attempt.campaign_id,
        enrollment_id=attempt.enrollment_id,
        attempt_id=attempt.id,
        title="Provider result requires reconciliation",
        description=reason,
        metadata_json={"auto_resend_allowed": False},
    )
    db.add(task)
    return task


def _attempt_safety_lock_codes(attempt_id: int) -> tuple[str, str]:
    return (
        f"provider_in_flight:{attempt_id}:contact",
        f"provider_in_flight:{attempt_id}:company",
    )


def ensure_attempt_safety_locks(
    db: Session,
    attempt: models.OutreachAttempt,
) -> list[models.SafetyLock]:
    """Create the attempt-bound locks that close the Provider I/O race window.

    The lock rows are persisted in the same transaction as SENDING and the cost
    reservation.  A second Campaign therefore observes a hard gate while the
    first worker is outside the database waiting for the Provider.
    """

    enrollment = db.get(models.Enrollment, attempt.enrollment_id)
    if not enrollment:
        return []
    contact_code, company_code = _attempt_safety_lock_codes(attempt.id)
    definitions = [
        {
            "scope": SafetyLockScope.CONTACT,
            "code": contact_code,
            "contact_id": enrollment.contact_id,
            "company_id": None,
        }
    ]
    if attempt.kind == AttemptKind.COLD:
        definitions.append(
            {
                "scope": SafetyLockScope.COMPANY,
                "code": company_code,
                "contact_id": None,
                "company_id": enrollment.company_id,
            }
        )

    current = utcnow()
    locks: list[models.SafetyLock] = []
    for definition in definitions:
        lock = db.query(models.SafetyLock).filter_by(
            owner_id=attempt.owner_id,
            scope=definition["scope"],
            code=definition["code"],
        ).first()
        if not lock:
            lock = models.SafetyLock(
                owner_id=attempt.owner_id,
                scope=definition["scope"],
                company_id=definition["company_id"],
                contact_id=definition["contact_id"],
                # CONTACT/COMPANY scope is already precise. Setting ``channel``
                # would also match the gate's channel-wide branch and block
                # unrelated recipients using the same channel.
                channel=None,
                code=definition["code"],
                reason="Provider result is not yet known",
                metadata_json={
                    "outreach_attempt_id": attempt.id,
                    "lock_kind": "provider_in_flight",
                    "cold_start": attempt.kind == AttemptKind.COLD,
                },
            )
            db.add(lock)
        else:
            # Re-entry before a Provider boundary must fail closed even if a
            # prior local transaction prematurely deactivated the same row.
            lock.active = True
            lock.locked_at = current
            lock.unlocked_at = None
            lock.unlocked_by_user_id = None
            lock.reason = "Provider result is not yet known"
            lock.metadata_json = {
                **(lock.metadata_json or {}),
                "outreach_attempt_id": attempt.id,
                "lock_kind": "provider_in_flight",
                "cold_start": attempt.kind == AttemptKind.COLD,
            }
        locks.append(lock)
    db.flush()
    return locks


def active_attempt_safety_locks(
    db: Session,
    *,
    enrollment: models.Enrollment,
    contact_point: models.ContactPoint,
) -> list[models.SafetyLock]:
    """Return only transient Provider locks relevant to this Enrollment."""

    return db.query(models.SafetyLock).filter(
        models.SafetyLock.owner_id == enrollment.owner_id,
        models.SafetyLock.active.is_(True),
        models.SafetyLock.code.like("provider_in_flight:%"),
        or_(
            models.SafetyLock.contact_id == enrollment.contact_id,
            models.SafetyLock.company_id == enrollment.company_id,
        ),
    ).all()


def release_attempt_safety_locks(
    db: Session,
    attempt: models.OutreachAttempt,
    *,
    reason: str,
) -> int:
    """Release only the in-flight locks whose code binds them to ``attempt``."""

    codes = _attempt_safety_lock_codes(attempt.id)
    locks = db.query(models.SafetyLock).filter(
        models.SafetyLock.owner_id == attempt.owner_id,
        models.SafetyLock.active.is_(True),
        models.SafetyLock.code.in_(codes),
    ).all()
    current = utcnow()
    released = 0
    for lock in locks:
        if (lock.metadata_json or {}).get("outreach_attempt_id") != attempt.id:
            continue
        lock.active = False
        lock.unlocked_at = current
        lock.metadata_json = {
            **(lock.metadata_json or {}),
            "released_reason": reason,
        }
        released += 1
    return released


def attempt_has_active_safety_locks(
    db: Session,
    attempt: models.OutreachAttempt,
) -> bool:
    """Whether this exact attempt still owns an in-flight Provider lock."""

    codes = _attempt_safety_lock_codes(attempt.id)
    for lock in db.query(models.SafetyLock).filter(
        models.SafetyLock.owner_id == attempt.owner_id,
        models.SafetyLock.active.is_(True),
        models.SafetyLock.code.in_(codes),
    ).all():
        if (lock.metadata_json or {}).get("outreach_attempt_id") == attempt.id:
            return True
    return False


def _pause_enrollments_affected_by_unknown(
    db: Session,
    attempt: models.OutreachAttempt,
) -> int:
    enrollment = db.get(models.Enrollment, attempt.enrollment_id)
    if not enrollment:
        return 0
    subject_filter = models.Enrollment.contact_id == enrollment.contact_id
    if attempt.kind == AttemptKind.COLD:
        subject_filter = or_(
            subject_filter,
            models.Enrollment.company_id == enrollment.company_id,
        )
    affected = db.query(models.Enrollment).filter(
        models.Enrollment.owner_id == attempt.owner_id,
        models.Enrollment.status.in_((EnrollmentStatus.SCHEDULED, EnrollmentStatus.ACTIVE)),
        models.Enrollment.archived_at.is_(None),
        subject_filter,
    ).all()
    current = utcnow()
    for row in affected:
        row.status = EnrollmentStatus.PAUSED
        row.paused_reason = "provider_result_unknown"
        row.paused_at = current
    return len(affected)


def mark_attempt_unknown(db: Session, attempt: models.OutreachAttempt, reason: str) -> models.Task:
    ensure_attempt_safety_locks(db, attempt)
    attempt.status = AttemptStatus.UNKNOWN
    attempt.unknown_reason = reason[:4000]
    attempt.claimed_by = None
    attempt.lease_expires_at = None
    for cost in db.query(models.ProviderCostEvent).filter(
        models.ProviderCostEvent.outreach_attempt_id == attempt.id,
        models.ProviderCostEvent.status == ProviderCostStatus.RESERVED,
    ).all():
        cost.status = ProviderCostStatus.UNKNOWN
        cost.metadata_json = {
            **(cost.metadata_json or {}),
            "unknown_reason": reason,
            "requires_reconciliation": True,
        }
    paused_enrollments = _pause_enrollments_affected_by_unknown(db, attempt)
    task = _ensure_reconciliation_task(db, attempt, reason)
    if not db.query(models.AuditEvent).filter_by(
        action="outreach_attempt.unknown",
        entity_type="outreach_attempt",
        entity_id=str(attempt.id),
        correlation_id=attempt.idempotency_key,
    ).first():
        db.add(
            models.AuditEvent(
                owner_id=attempt.owner_id,
                actor_user_id=None,
                action="outreach_attempt.unknown",
                entity_type="outreach_attempt",
                entity_id=str(attempt.id),
                correlation_id=attempt.idempotency_key,
                after_data={
                    "reason": reason,
                    "auto_resend_allowed": False,
                    "paused_enrollments": paused_enrollments,
                },
            )
        )
    return task


def reconcile_attempt_uncertainty(
    db: Session,
    *,
    attempt_id: int,
    fence: LeaseFence,
    reason: str,
    provider_trace: Optional[dict] = None,
    fence_lost: bool = True,
) -> models.OutreachAttempt:
    """Fail closed after Provider uncertainty without overwriting a replacement claim.

    If the original generation is still present, it can safely transition to
    UNKNOWN.  If another generation or a terminal resolution now owns the row,
    this function leaves that state untouched. The audit distinguishes an actual
    lease loss from a Provider/local uncertainty while retaining the originating
    owner and generation for reconciliation.
    """

    query = db.query(models.OutreachAttempt).filter(models.OutreachAttempt.id == attempt_id)
    if db.bind and db.bind.dialect.name == "mysql":
        query = query.with_for_update()
    attempt = query.populate_existing().one()
    costs = db.query(models.ProviderCostEvent).filter(
        models.ProviderCostEvent.outreach_attempt_id == attempt.id,
    ).all()
    active_attempt_lock = attempt_has_active_safety_locks(db, attempt)
    authoritative_success = (
        attempt.status == AttemptStatus.SUCCEEDED
        and bool(costs)
        and all(cost.status == ProviderCostStatus.CHARGED for cost in costs)
        and not active_attempt_lock
    )
    authoritative_not_sent = (
        attempt.status == AttemptStatus.FAILED
        and bool(costs)
        and all(cost.status == ProviderCostStatus.FAILED for cost in costs)
        and not active_attempt_lock
        and (
            attempt.last_error == "provider_rejected"
            or any(
                (cost.metadata_json or {}).get("reconciliation_result") == "confirmed_not_sent"
                for cost in costs
            )
        )
    )
    if authoritative_success or authoritative_not_sent:
        # A webhook (or another authoritative generation) completed the durable
        # result while this worker was outside the database. Its terminal state,
        # cost, cooldowns and released locks win; the stale worker must not open
        # a false reconciliation task or downgrade the stage.
        return attempt
    same_generation = (
        attempt.claimed_by == fence.owner
        and attempt.attempt_count == fence.generation
        and attempt.status in {AttemptStatus.CLAIMED, AttemptStatus.SENDING}
    )
    if same_generation or attempt.status == AttemptStatus.UNKNOWN:
        task = mark_attempt_unknown(db, attempt, reason)
    else:
        for cost in db.query(models.ProviderCostEvent).filter(
            models.ProviderCostEvent.outreach_attempt_id == attempt.id,
            models.ProviderCostEvent.status == ProviderCostStatus.RESERVED,
        ).all():
            cost.status = ProviderCostStatus.UNKNOWN
            cost.metadata_json = {
                **(cost.metadata_json or {}),
                "unknown_reason": reason,
                "requires_reconciliation": True,
            }
        task = _ensure_reconciliation_task(db, attempt, reason)

    evidence = {
        "reason": reason,
        "originating_lease_owner": fence.owner,
        "originating_claim_generation": fence.generation,
        "observed_status": attempt.status.value,
        "observed_lease_owner": attempt.claimed_by,
        "observed_claim_generation": attempt.attempt_count,
        "provider_trace": provider_trace or {},
        "auto_resend_allowed": False,
    }
    if fence_lost:
        # Preserve the original keys for operators and existing reconciliation
        # tooling that already consumes lease-fence evidence.
        evidence["lease_owner"] = fence.owner
        evidence["claim_generation"] = fence.generation
    existing_metadata = task.metadata_json or {}
    metadata_key = "lease_fence_losses" if fence_lost else "provider_uncertainty_events"
    events = list(existing_metadata.get(metadata_key) or [])
    if evidence not in events:
        events.append(evidence)
    task.metadata_json = {
        **existing_metadata,
        "auto_resend_allowed": False,
        metadata_key: events,
    }
    action = "outreach_attempt.lease_fence_lost" if fence_lost else "outreach_attempt.reconciliation_required"
    correlation_id = (
        f"{attempt.idempotency_key}:lease:{fence.generation}"
        if fence_lost
        else f"{attempt.idempotency_key}:uncertain:{fence.generation}:{reason}"[:255]
    )
    if not db.query(models.AuditEvent).filter_by(
        action=action,
        entity_type="outreach_attempt",
        entity_id=str(attempt.id),
        correlation_id=correlation_id,
    ).first():
        db.add(
            models.AuditEvent(
                owner_id=attempt.owner_id,
                actor_user_id=None,
                action=action,
                entity_type="outreach_attempt",
                entity_id=str(attempt.id),
                correlation_id=correlation_id,
                after_data=evidence,
            )
        )
    set_stage_runtime(
        db,
        owner_id=attempt.owner_id,
        campaign_id=attempt.campaign_id,
        stage_name="outbound",
        status=StageStatus.FAILED,
        reason="lease_fence_lost" if fence_lost else "provider_result_unknown",
        details={
            "attempt_id": attempt.id,
            "claim_generation": fence.generation,
            "requires_reconciliation": True,
        },
    )
    return attempt
