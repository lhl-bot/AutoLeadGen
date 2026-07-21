"""Job handlers shared by the dedicated worker processes."""
from __future__ import annotations

from sqlalchemy.orm import Session

from product_v2 import models
from product_v2.enums import (
    CampaignLifecycle,
    EnrollmentStatus,
    JobStatus,
    TaskPriority,
    TaskQueueScope,
    TaskStatus,
    TaskType,
)
from product_v2.migration_state import OwnerMigrationConflict, owner_v2_write_enabled
from product_v2.runtime.outbound import create_first_attempt
from product_v2.runtime.queue import (
    LeaseFence,
    LeaseFenceLost,
    complete_job,
    fail_job,
    lease_fence as captured_lease_fence,
    start_job,
)
from product_v2.services.domain import add_audit, campaign_readiness, utcnow, validate_campaign_command
from product_v2.services.acquisition import (
    execute_activation_launch,
    execute_search_run,
    execute_verify_run,
)


def _cancel_for_inactive_owner_path(
    db: Session,
    job: models.AutomationJob,
) -> dict:
    """Terminally cancel claimed automation without applying domain writes."""

    completed_at = utcnow()
    job.status = JobStatus.CANCELLED
    job.last_error = "owner_v2_write_path_inactive"
    job.completed_at = completed_at
    job.lease_owner = None
    job.lease_expires_at = None
    result = {
        "cancelled": True,
        "reason": "owner_v2_write_path_inactive",
        "provider_call_allowed": False,
    }
    job.result = result
    add_audit(
        db,
        owner_id=job.owner_id,
        actor_user_id=None,
        action="automation_job.owner_path_cancelled",
        entity_type="automation_job",
        entity_id=job.id,
        after={**result, "job_type": job.job_type},
        correlation_id=job.idempotency_key,
    )
    return result


def execute_job(
    db: Session,
    job: models.AutomationJob,
    *,
    lease_fence: LeaseFence | None = None,
) -> dict:
    fence = lease_fence or captured_lease_fence(job)
    try:
        if fence:
            # This conditional transition happens before any Campaign or
            # Enrollment mutation.  A reclaimed generation therefore makes the
            # stale worker roll back without touching the replacement claim.
            job = start_job(db, job, fence)
        try:
            owner_path_active = owner_v2_write_enabled(db, job.owner_id, lock=True)
        except OwnerMigrationConflict:
            owner_path_active = False
        if not owner_path_active:
            return _cancel_for_inactive_owner_path(db, job)
        if job.job_type == "campaign.start":
            campaign = db.get(models.Campaign, job.campaign_id)
            if not campaign:
                raise ValueError("campaign_not_found")
            validate_campaign_command(campaign, "start")
            readiness = campaign_readiness(db, campaign)
            if not readiness.ready:
                raise ValueError("campaign_not_ready")
            if readiness.warnings and not bool(job.payload.get("confirm_warnings")):
                raise ValueError("campaign_warnings_not_confirmed")
            campaign.lifecycle = CampaignLifecycle.RUNNING
            campaign.started_at = utcnow()
            campaign.paused_at = None
            db.query(models.Enrollment).filter(
                models.Enrollment.campaign_id == campaign.id,
                models.Enrollment.status == EnrollmentStatus.PAUSED,
                models.Enrollment.paused_reason == "campaign_paused",
            ).update(
                {"status": EnrollmentStatus.ACTIVE, "paused_reason": None, "paused_at": None},
                synchronize_session="fetch",
            )
            result = {"campaign_id": campaign.id, "lifecycle": campaign.lifecycle.value}
        elif job.job_type == "campaign.pause":
            campaign = db.get(models.Campaign, job.campaign_id)
            if not campaign:
                raise ValueError("campaign_not_found")
            validate_campaign_command(campaign, "pause")
            campaign.lifecycle = CampaignLifecycle.PAUSED
            campaign.paused_at = utcnow()
            db.query(models.Enrollment).filter(
                models.Enrollment.campaign_id == campaign.id,
                models.Enrollment.status.in_((EnrollmentStatus.SCHEDULED, EnrollmentStatus.ACTIVE)),
            ).update(
                {"status": EnrollmentStatus.PAUSED, "paused_reason": "campaign_paused", "paused_at": utcnow()},
                synchronize_session=False,
            )
            result = {"campaign_id": campaign.id, "lifecycle": campaign.lifecycle.value}
        elif job.job_type == "campaign.complete":
            campaign = db.get(models.Campaign, job.campaign_id)
            if not campaign:
                raise ValueError("campaign_not_found")
            validate_campaign_command(campaign, "complete")
            campaign.lifecycle = CampaignLifecycle.COMPLETED
            campaign.completed_at = utcnow()
            db.query(models.Enrollment).filter(
                models.Enrollment.campaign_id == campaign.id,
                models.Enrollment.status.notin_((EnrollmentStatus.COMPLETED, EnrollmentStatus.CANCELLED)),
            ).update(
                {"status": EnrollmentStatus.COMPLETED, "completed_at": utcnow()},
                synchronize_session=False,
            )
            result = {"campaign_id": campaign.id, "lifecycle": campaign.lifecycle.value}
        elif job.job_type == "enrollment.created":
            enrollment = db.get(models.Enrollment, job.enrollment_id)
            if not enrollment:
                raise ValueError("enrollment_not_found")
            attempt = create_first_attempt(db, enrollment)
            result = {"enrollment_id": enrollment.id, "attempt_id": attempt.id if attempt else None}
        elif job.job_type == "acquisition.search":
            run = db.get(models.AcquisitionRun, int((job.payload or {}).get("run_id") or 0))
            if not run or run.owner_id != job.owner_id:
                raise ValueError("acquisition_run_not_found")
            result = execute_search_run(db, run)
        elif job.job_type == "acquisition.verify":
            run = db.get(models.AcquisitionRun, int((job.payload or {}).get("run_id") or 0))
            if not run or run.owner_id != job.owner_id:
                raise ValueError("acquisition_run_not_found")
            result = execute_verify_run(
                db,
                run,
                (job.payload or {}).get("candidate_ids") or [],
                (job.payload or {}).get("approval_id"),
            )
        elif job.job_type == "activation.launch":
            result = execute_activation_launch(db, job)
        else:
            raise ValueError(f"unsupported_job_type:{job.job_type}")
        complete_job(db, job, result, fence=fence)
        return result
    except LeaseFenceLost:
        raise
    except Exception as exc:
        if job.job_type in {"acquisition.search", "acquisition.verify"}:
            run = db.get(models.AcquisitionRun, int((job.payload or {}).get("run_id") or 0))
            if run is not None and run.owner_id == job.owner_id:
                run.status = "failed"
                run.last_error = str(exc)[:4000]
            if "_unknown:" in str(exc):
                job.status = JobStatus.UNKNOWN
                job.last_error = str(exc)[:4000]
                job.lease_owner = None
                job.lease_expires_at = None
                job.completed_at = utcnow()
                existing_task = db.query(models.Task.id).filter(
                    models.Task.owner_id == job.owner_id,
                    models.Task.automation_job_id == job.id,
                    models.Task.task_type == TaskType.RECONCILIATION,
                    models.Task.status.in_((TaskStatus.OPEN, TaskStatus.IN_PROGRESS)),
                ).first()
                if existing_task is None:
                    db.add(
                        models.Task(
                            owner_id=job.owner_id,
                            task_type=TaskType.RECONCILIATION,
                            queue_scope=TaskQueueScope.ADMIN,
                            status=TaskStatus.OPEN,
                            priority=TaskPriority.URGENT,
                            automation_job_id=job.id,
                            title="找客费用结果待人工对账",
                            description="Provider 结果不确定，禁止自动重试；请核对账单后人工处置。",
                            metadata_json={
                                "acquisition_run_id": run.id if run else None,
                                "provider_result": "unknown",
                                "automatic_retry_allowed": False,
                            },
                        )
                    )
                raise
        fail_job(db, job, str(exc), fence=fence)
        raise
