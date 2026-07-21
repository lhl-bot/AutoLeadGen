"""Owner-scoped single-writer cutover policy.

No row means ``legacy``.  A V2 writer is enabled only by an explicit,
preview-confirmed transition persisted in ``v2_owner_migration_states``.
Legacy HTTP writers and transitions share an owner-scoped MySQL advisory
fence; V2 writers and workers retain a same-session ``users`` row lock.  A
successful switch commits before releasing the advisory fence, preventing an
in-flight V1 or V2 write from crossing the path change.
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
from contextlib import contextmanager
from typing import Any

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError, PendingRollbackError
from sqlalchemy.orm import Session

import models as legacy
from product_v2 import models
from product_v2.enums import (
    AttemptStatus,
    CampaignLifecycle,
    EnrollmentStatus,
    JobStatus,
    OwnerWritePath,
    ProviderCostStatus,
    TaskStatus,
    TaskType,
)
from product_v2.migration_schemas import OwnerMigrationPreview, OwnerMigrationStateRead
from product_v2.services.domain import add_audit, utcnow


MIGRATION_JOB_TYPE = "owner_migration_state.switch"
MIGRATION_AUDIT_ACTION = "owner_migration_state.switched"
MIGRATION_ENTITY_TYPE = "owner_migration_state"


class OwnerMigrationConflict(ValueError):
    def __init__(self, code: str, message: str, **context: Any):
        super().__init__(message)
        self.code = code
        self.message = message
        self.context = context


_OWNER_LOCKS_GUARD = threading.Lock()
_OWNER_LOCKS: dict[int, threading.RLock] = {}


@contextmanager
def _owner_process_lock(owner_id: int):
    # SQLite ignores SELECT ... FOR UPDATE.  The process lock gives local/test
    # requests the same compare-and-switch semantics; MySQL additionally uses
    # the durable users-row lock for cross-process serialization.
    with _OWNER_LOCKS_GUARD:
        lock = _OWNER_LOCKS.setdefault(owner_id, threading.RLock())
    with lock:
        yield


def _owner_advisory_timeout_seconds() -> int:
    try:
        configured = int(os.environ.get("PRODUCT_V2_OWNER_FENCE_TIMEOUT_SECONDS", "10"))
    except ValueError as exc:
        raise OwnerMigrationConflict(
            "OWNER_PATH_FENCE_CONFIGURATION_INVALID",
            "Owner path fence timeout must be an integer",
        ) from exc
    return max(0, min(configured, 30))


@contextmanager
def serialize_owner_write_path(
    db: Session,
    owner_id: int,
    *,
    commit_on_success: bool = False,
):
    """Serialize legacy writers and path switches without cross-session locks.

    MySQL advisory locks are connection-scoped and therefore can safely span a
    legacy request whose route owns a different ORM session.  SQLite uses the
    process mutex because it has no equivalent cross-connection primitive.
    """

    def guarded_operation():
        try:
            yield
            if commit_on_success:
                db.commit()
        except Exception:
            if commit_on_success:
                db.rollback()
            raise

    if db.get_bind().dialect.name != "mysql":
        with _owner_process_lock(owner_id):
            yield from guarded_operation()
        return

    lock_name = f"autoleadgen:owner-path:{owner_id}"
    acquired = db.execute(
        text("SELECT GET_LOCK(:lock_name, :timeout_seconds)"),
        {
            "lock_name": lock_name,
            "timeout_seconds": _owner_advisory_timeout_seconds(),
        },
    ).scalar()
    if acquired != 1:
        raise OwnerMigrationConflict(
            "OWNER_PATH_FENCE_TIMEOUT",
            "Timed out waiting for the owner's write-path fence",
        )
    try:
        yield from guarded_operation()
    finally:
        try:
            db.execute(
                text("SELECT RELEASE_LOCK(:lock_name)"),
                {"lock_name": lock_name},
            )
        except PendingRollbackError:
            # A failed flush invalidates SQLAlchemy's transaction but MySQL's
            # named lock remains connection-scoped. Roll back the failed unit
            # before releasing that lock on the same checked-out connection.
            db.rollback()
            db.execute(
                text("SELECT RELEASE_LOCK(:lock_name)"),
                {"lock_name": lock_name},
            )


def owner_path_test_bypass_enabled() -> bool:
    """Compatibility switch that is deliberately impossible outside tests."""

    if os.environ.get("AUTOLEADGEN_ENV", "").strip().lower() != "test":
        return False
    return os.environ.get("PRODUCT_V2_OWNER_PATH_TEST_BYPASS", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def owner_path_enforcement_enabled() -> bool:
    """Return whether owner state fences application writers.

    Local/test keeps the existing isolated V2 preview usable by default.  Any
    production-like environment fails closed unless explicitly configured
    otherwise; setting PRODUCT_V2_OWNER_PATH_ENFORCEMENT always wins.
    """

    configured = os.environ.get("PRODUCT_V2_OWNER_PATH_ENFORCEMENT")
    if configured is not None:
        return configured.strip().lower() in {"1", "true", "yes", "on"}
    environment = os.environ.get("AUTOLEADGEN_ENV", "local").strip().lower()
    return environment not in {"local", "test"}


def _enabled(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "on"}


def _production_like_cutover() -> bool:
    environment = os.environ.get("AUTOLEADGEN_ENV", "local").strip().lower()
    return environment not in {"local", "test"} or owner_path_enforcement_enabled()


def lock_owner_row(db: Session, owner_id: int) -> None:
    owner = (
        db.query(legacy.User.id)
        .filter(legacy.User.id == owner_id)
        .with_for_update()
        .one_or_none()
    )
    if owner is None:
        raise OwnerMigrationConflict("OWNER_NOT_FOUND", "Owner does not exist")


def _state_row(db: Session, owner_id: int, *, for_update: bool = False):
    query = db.query(models.OwnerMigrationState).filter(
        models.OwnerMigrationState.owner_id == owner_id
    )
    if for_update:
        query = query.with_for_update()
    return query.one_or_none()


def read_owner_migration_state(db: Session, owner_id: int) -> OwnerMigrationStateRead:
    row = _state_row(db, owner_id)
    if row is None:
        return OwnerMigrationStateRead(
            owner_id=owner_id,
            current_path=OwnerWritePath.LEGACY,
            version=0,
            explicit=False,
        )
    return OwnerMigrationStateRead(
        owner_id=owner_id,
        current_path=row.current_path,
        version=row.version,
        explicit=True,
        switched_at=row.switched_at,
        switched_by_user_id=row.switched_by_user_id,
    )


def owner_v2_write_enabled(
    db: Session,
    owner_id: int,
    *,
    lock: bool = False,
) -> bool:
    if not owner_path_enforcement_enabled() or owner_path_test_bypass_enabled():
        return True
    if lock:
        lock_owner_row(db, owner_id)
    row = _state_row(db, owner_id, for_update=lock)
    return bool(row and row.current_path == OwnerWritePath.V2)


def _active_execution_counts(db: Session, owner_id: int) -> dict[str, int]:
    return {
        "running_campaigns": db.query(models.Campaign.id).filter(
            models.Campaign.owner_id == owner_id,
            models.Campaign.lifecycle == CampaignLifecycle.RUNNING,
            models.Campaign.archived_at.is_(None),
        ).count(),
        "active_enrollments": db.query(models.Enrollment.id).filter(
            models.Enrollment.owner_id == owner_id,
            models.Enrollment.status.in_((EnrollmentStatus.SCHEDULED, EnrollmentStatus.ACTIVE)),
            models.Enrollment.archived_at.is_(None),
        ).count(),
        "executable_attempts": db.query(models.OutreachAttempt.id).filter(
            models.OutreachAttempt.owner_id == owner_id,
            models.OutreachAttempt.status.in_(
                (AttemptStatus.QUEUED, AttemptStatus.CLAIMED, AttemptStatus.SENDING)
            ),
        ).count(),
        "executable_jobs": db.query(models.AutomationJob.id).filter(
            models.AutomationJob.owner_id == owner_id,
            models.AutomationJob.status.in_(
                (JobStatus.PENDING, JobStatus.CLAIMED, JobStatus.RUNNING, JobStatus.RETRY)
            ),
            models.AutomationJob.job_type != MIGRATION_JOB_TYPE,
        ).count(),
    }


def _legacy_recovery_risk_counts(db: Session, owner_id: int) -> dict[str, int]:
    """Facts V1 cannot safely enforce until an approved projection exists."""

    return {
        "active_consent_restrictions": db.query(models.ConsentRestriction.id).filter(
            models.ConsentRestriction.owner_id == owner_id,
            models.ConsentRestriction.active.is_(True),
        ).count(),
        "active_safety_locks": db.query(models.SafetyLock.id).filter(
            models.SafetyLock.owner_id == owner_id,
            models.SafetyLock.active.is_(True),
        ).count(),
        "uncertain_or_sending_attempts": db.query(models.OutreachAttempt.id).filter(
            models.OutreachAttempt.owner_id == owner_id,
            models.OutreachAttempt.status.in_((AttemptStatus.UNKNOWN, AttemptStatus.SENDING)),
        ).count(),
        "reserved_or_unknown_costs": db.query(models.ProviderCostEvent.id).filter(
            models.ProviderCostEvent.owner_id == owner_id,
            models.ProviderCostEvent.status.in_(
                (ProviderCostStatus.RESERVED, ProviderCostStatus.UNKNOWN)
            ),
        ).count(),
        "open_reconciliation_tasks": db.query(models.Task.id).filter(
            models.Task.owner_id == owner_id,
            models.Task.task_type == TaskType.RECONCILIATION,
            models.Task.status.in_((TaskStatus.OPEN, TaskStatus.IN_PROGRESS)),
            models.Task.archived_at.is_(None),
        ).count(),
        "contact_point_cooldowns": db.query(models.ContactPoint.id).filter(
            models.ContactPoint.owner_id == owner_id,
            models.ContactPoint.last_cold_outreach_at.isnot(None),
            models.ContactPoint.archived_at.is_(None),
        ).count(),
        "company_cooldowns": db.query(models.Company.id).filter(
            models.Company.owner_id == owner_id,
            models.Company.last_cold_outreach_at.isnot(None),
            models.Company.archived_at.is_(None),
        ).count(),
    }


def _preview_document(
    db: Session,
    *,
    owner_id: int,
    target_path: OwnerWritePath,
) -> dict[str, Any]:
    current = read_owner_migration_state(db, owner_id)
    active = _active_execution_counts(db, owner_id)
    legacy_writers_frozen = _enabled("PRODUCT_V2_LEGACY_WRITERS_FROZEN")
    legacy_recovery_approved = _enabled("PRODUCT_V2_LEGACY_WRITE_RECOVERY_APPROVED")
    recovery_risks = _legacy_recovery_risk_counts(db, owner_id)
    blockers: list[dict[str, Any]] = []
    if target_path == current.current_path:
        blockers.append(
            {
                "code": "OWNER_PATH_UNCHANGED",
                "message": f"Owner is already on the {target_path.value} path",
            }
        )
    if current.current_path == OwnerWritePath.LEGACY and target_path == OwnerWritePath.V2:
        if _production_like_cutover() and not legacy_writers_frozen:
            blockers.append(
                {
                    "code": "LEGACY_WRITERS_NOT_FROZEN",
                    "message": (
                        "Production-like cutover requires explicit evidence that all legacy "
                        "API and background writers are frozen"
                    ),
                }
            )
    if current.current_path == OwnerWritePath.V2 and target_path == OwnerWritePath.LEGACY:
        if not legacy_recovery_approved:
            blockers.append(
                {
                    "code": "LEGACY_WRITE_RECOVERY_NOT_APPROVED",
                    "message": (
                        "Read fallback does not reopen legacy writes; write recovery requires "
                        "a separate explicit approval"
                    ),
                }
            )
        if any(active.values()):
            blockers.append(
                {
                    "code": "V2_EXECUTION_STILL_ACTIVE",
                    "message": "Pause active V2 campaigns, attempts, and jobs before legacy write recovery",
                    "counts": active,
                }
            )
        risk_groups = (
            (
                "V2_CONSENT_NOT_PROJECTED",
                "Active V2 consent restrictions are not proven enforceable by legacy writers",
                ("active_consent_restrictions",),
            ),
            (
                "V2_SAFETY_LOCKS_ACTIVE",
                "Active V2 hard safety locks prevent legacy write recovery",
                ("active_safety_locks",),
            ),
            (
                "V2_UNCERTAINTY_UNRESOLVED",
                "Provider sending/cost uncertainty must be reconciled before legacy write recovery",
                ("uncertain_or_sending_attempts", "reserved_or_unknown_costs"),
            ),
            (
                "V2_RECONCILIATION_OPEN",
                "Open V2 reconciliation tasks prevent legacy write recovery",
                ("open_reconciliation_tasks",),
            ),
            (
                "V2_COOLDOWNS_NOT_PROJECTED",
                "V2 contact and company cooldowns are not projected into the legacy scheduler",
                ("contact_point_cooldowns", "company_cooldowns"),
            ),
        )
        for code, message, keys in risk_groups:
            counts = {key: recovery_risks[key] for key in keys}
            if any(counts.values()):
                blockers.append({"code": code, "message": message, "counts": counts})
    return {
        "owner_id": owner_id,
        "current_path": current.current_path.value,
        "target_path": target_path.value,
        "expected_version": current.version,
        "effects": {
            "write_path": target_path.value,
            "read_fallback_changed": False,
            "legacy_writes_allowed": target_path == OwnerWritePath.LEGACY,
            "v2_writes_allowed": target_path == OwnerWritePath.V2,
            "active_v2_execution": active,
            "legacy_recovery_risks": recovery_risks,
            "legacy_writers_frozen_evidence": legacy_writers_frozen,
            "legacy_write_recovery_approved": legacy_recovery_approved,
            "switch_serialization": "owner_advisory_lock_then_users_row_lock",
        },
        "blockers": blockers,
    }


def _checksum(document: dict[str, Any]) -> str:
    encoded = json.dumps(document, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def preview_owner_path(
    db: Session,
    *,
    owner_id: int,
    target_path: OwnerWritePath,
) -> OwnerMigrationPreview:
    document = _preview_document(db, owner_id=owner_id, target_path=target_path)
    return OwnerMigrationPreview(
        **document,
        preview_checksum=_checksum(document),
    )


def _replay_receipt(
    db: Session,
    *,
    owner_id: int,
    idempotency_key: str,
    command_payload: dict[str, Any],
) -> OwnerMigrationStateRead | None:
    receipt = db.query(models.AutomationJob).filter_by(idempotency_key=idempotency_key).first()
    if receipt is None:
        return None
    if (
        receipt.owner_id != owner_id
        or receipt.job_type != MIGRATION_JOB_TYPE
        or (receipt.payload or {}) != command_payload
        or not receipt.result
    ):
        raise OwnerMigrationConflict(
            "IDEMPOTENCY_KEY_REUSED",
            "Idempotency-Key is already associated with a different command",
        )
    return OwnerMigrationStateRead.model_validate(receipt.result)


def switch_owner_path(
    db: Session,
    *,
    owner_id: int,
    actor_user_id: int,
    target_path: OwnerWritePath,
    expected_version: int,
    preview_checksum: str,
    idempotency_key: str,
) -> OwnerMigrationStateRead:
    command_payload = {
        "owner_id": owner_id,
        "target_path": target_path.value,
        "expected_version": expected_version,
        "preview_checksum": preview_checksum,
        "impact_preview_confirmed": True,
    }
    with serialize_owner_write_path(db, owner_id, commit_on_success=True):
        lock_owner_row(db, owner_id)
        replay = _replay_receipt(
            db,
            owner_id=owner_id,
            idempotency_key=idempotency_key,
            command_payload=command_payload,
        )
        if replay is not None:
            return replay

        current = read_owner_migration_state(db, owner_id)
        if current.version != expected_version:
            raise OwnerMigrationConflict(
                "OWNER_PATH_VERSION_CONFLICT",
                "Owner migration state changed since preview",
                expected_version=expected_version,
                current_version=current.version,
            )
        preview = preview_owner_path(db, owner_id=owner_id, target_path=target_path)
        if preview.preview_checksum != preview_checksum:
            raise OwnerMigrationConflict(
                "OWNER_PATH_PREVIEW_STALE",
                "Owner migration impact changed since preview",
                expected_checksum=preview.preview_checksum,
            )
        if preview.blockers:
            raise OwnerMigrationConflict(
                "OWNER_PATH_SWITCH_BLOCKED",
                "Owner migration path cannot be switched while blockers remain",
                blockers=preview.blockers,
            )

        observed_at = utcnow()
        row = _state_row(db, owner_id, for_update=True)
        before = current.model_dump(mode="json")
        if row is None:
            row = models.OwnerMigrationState(
                owner_id=owner_id,
                current_path=target_path,
                version=1,
                switched_by_user_id=actor_user_id,
                switched_at=observed_at,
            )
            db.add(row)
        else:
            row.current_path = target_path
            row.version += 1
            row.switched_by_user_id = actor_user_id
            row.switched_at = observed_at
        db.flush()
        result = OwnerMigrationStateRead(
            owner_id=owner_id,
            current_path=target_path,
            version=row.version,
            explicit=True,
            switched_at=observed_at,
            switched_by_user_id=actor_user_id,
        )
        persisted_result = result.model_dump(mode="json")
        db.add(
            models.AutomationJob(
                owner_id=owner_id,
                status=JobStatus.SUCCEEDED,
                job_type=MIGRATION_JOB_TYPE,
                queue="migration_control",
                payload=command_payload,
                idempotency_key=idempotency_key,
                priority=1000,
                scheduled_at=observed_at,
                attempts=1,
                max_attempts=1,
                result=persisted_result,
                completed_at=observed_at,
            )
        )
        add_audit(
            db,
            owner_id=owner_id,
            actor_user_id=actor_user_id,
            action=MIGRATION_AUDIT_ACTION,
            entity_type=MIGRATION_ENTITY_TYPE,
            entity_id=owner_id,
            correlation_id=idempotency_key,
            before=before,
            after=persisted_result,
            metadata={
                "impact_preview_confirmed": True,
                "preview_checksum": preview_checksum,
                "effects": preview.effects,
            },
        )
        try:
            db.flush()
        except IntegrityError as exc:
            raise OwnerMigrationConflict(
                "OWNER_PATH_WRITE_CONFLICT",
                "Concurrent owner path switch conflicted",
            ) from exc
        return result
