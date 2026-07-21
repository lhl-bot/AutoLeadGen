"""Create and execute outreach attempts through the fake-only connector registry."""
from __future__ import annotations

from datetime import timedelta
from typing import Callable, Optional

from sqlalchemy.orm import Session

from product_v2 import models
from product_v2.connectors import ConnectorRegistry, ConnectorRequest, ConnectorResult
from product_v2.enums import (
    AttemptStatus,
    AttemptKind,
    CampaignRunMode,
    ContactPointAvailabilityStatus,
    EnrollmentStatus,
    MessageDirection,
    MessageEventType,
    ProviderCostStatus,
    StageStatus,
    TaskPriority,
    TaskStatus,
    TaskType,
)
from product_v2.migration_state import OwnerMigrationConflict, owner_v2_write_enabled
from product_v2.message_rendering import MessageRenderError, render_sequence_message
from product_v2.runtime.queue import (
    LeaseFence,
    active_attempt_safety_locks,
    claim_attempt,
    ensure_attempt_safety_locks,
    entity_id,
    lease_fence as captured_lease_fence,
    lock_attempt_for_fence,
    mark_attempt_unknown,
    reconcile_attempt_uncertainty,
    release_attempt_safety_locks,
    renew_attempt_lease,
    set_stage_runtime,
)
from product_v2.runtime.sequence_conditions import (
    evaluate_execution_conditions,
    evaluate_stop_conditions,
)
from product_v2.services.domain import add_audit, as_utc, campaign_budget_snapshot, evaluate_outreach_gates, utcnow
from product_v2.services.channel_accounts import (
    lock_and_reserve_attempt_account,
    resolve_attempt_account,
)
from product_v2.services.route_reviews import ensure_attempt_route_proposal
from product_v2.settings_policy import (
    configured_public_unsubscribe_url,
    global_budget_snapshot,
    review_policy_required,
    revision_unit_price,
)


def _approved_review_message(
    db: Session,
    *,
    attempt: models.OutreachAttempt,
) -> Optional[tuple[Optional[str], str, Optional[str]]]:
    """Return the exact immutable message snapshot a human approved."""

    route = db.query(models.EnrollmentRouteStep).filter_by(
        owner_id=attempt.owner_id,
        enrollment_id=attempt.enrollment_id,
        attempt_id=attempt.id,
    ).first()
    if route is not None:
        if (
            route.status != "approved"
            or route.channel != attempt.channel
            or route.contact_point_id != attempt.contact_point_id
            or route.channel_account_id != attempt.channel_account_id
            or not route.body.strip()
        ):
            raise MessageRenderError("approved_route_snapshot_invalid")
        return route.subject, route.body, None
    approved_route = db.query(models.RouteProposal.id).filter_by(
        owner_id=attempt.owner_id,
        enrollment_id=attempt.enrollment_id,
        status="approved",
    ).first()
    if approved_route is not None:
        raise MessageRenderError("approved_route_attempt_step_missing")

    task = db.query(models.Task).filter(
        models.Task.owner_id == attempt.owner_id,
        models.Task.attempt_id == attempt.id,
        models.Task.task_type == TaskType.DRAFT_REVIEW,
        models.Task.status == TaskStatus.COMPLETED,
        models.Task.archived_at.is_(None),
    ).order_by(models.Task.id.asc()).first()
    if task is None:
        return None
    metadata = task.metadata_json or {}
    body = metadata.get("body")
    subject = metadata.get("subject")
    unsubscribe_url = metadata.get("unsubscribe_url")
    if not isinstance(body, str) or not body.strip():
        raise MessageRenderError("approved_review_snapshot_missing")
    if subject is not None and not isinstance(subject, str):
        raise MessageRenderError("approved_review_snapshot_invalid")
    if unsubscribe_url is not None and not isinstance(unsubscribe_url, str):
        raise MessageRenderError("approved_review_snapshot_invalid")
    return subject, body, unsubscribe_url


def _masked_recipient(value: str, channel) -> str:
    """Keep Task previews useful without copying a full contact point."""

    text = (value or "").strip()
    if channel.value == "email" and "@" in text:
        local, domain = text.rsplit("@", 1)
        return f"{local[:1] or '*'}***@{domain}"
    if channel.value == "whatsapp":
        return f"***{text[-4:]}" if len(text) > 4 else "***"
    return f"{text[:24]}{'...' if len(text) > 24 else ''}"


def _apply_campaign_run_mode(
    db: Session,
    *,
    campaign: models.Campaign,
    enrollment: models.Enrollment,
    attempt: models.OutreachAttempt,
    point: models.ContactPoint,
    connector,
    sequence_step: Optional[models.SequenceStep],
    subject: Optional[str],
    body: str,
    unsubscribe_url: Optional[str] = None,
) -> bool:
    """Return whether this exact Attempt may cross the Provider boundary."""

    if campaign.run_mode == CampaignRunMode.SHADOW and not connector.is_fake:
        attempt.status = AttemptStatus.BLOCKED
        attempt.last_error = "shadow_mode_requires_fake_connector"
        attempt.claimed_by = None
        attempt.lease_expires_at = None
        enrollment.status = EnrollmentStatus.BLOCKED
        enrollment.paused_reason = attempt.last_error
        enrollment.paused_at = utcnow()
        if not db.query(models.Task).filter_by(
            owner_id=attempt.owner_id,
            attempt_id=attempt.id,
            task_type=TaskType.CAMPAIGN_READINESS,
            status=TaskStatus.OPEN,
        ).first():
            db.add(
                models.Task(
                    owner_id=attempt.owner_id,
                    task_type=TaskType.CAMPAIGN_READINESS,
                    status=TaskStatus.OPEN,
                    priority=TaskPriority.URGENT,
                    company_id=enrollment.company_id,
                    contact_id=enrollment.contact_id,
                    campaign_id=enrollment.campaign_id,
                    enrollment_id=enrollment.id,
                    attempt_id=attempt.id,
                    title="Shadow Campaign requires a fake connector",
                    description="The Attempt was stopped before any Provider call.",
                    metadata_json={"run_mode": "shadow", "provider_call_allowed": False},
                )
            )
        add_audit(
            db,
            owner_id=attempt.owner_id,
            actor_user_id=None,
            action="outreach_attempt.shadow_real_connector_blocked",
            entity_type="outreach_attempt",
            entity_id=attempt.id,
            after={"connector": connector.provider, "provider_call_allowed": False},
            correlation_id=attempt.idempotency_key,
        )
        set_stage_runtime(
            db,
            owner_id=attempt.owner_id,
            campaign_id=attempt.campaign_id,
            stage_name="outbound",
            status=StageStatus.BLOCKED,
            reason=attempt.last_error,
            details={"attempt_id": attempt.id, "run_mode": "shadow"},
        )
        return False

    policy_requires_review = (
        campaign.run_mode != CampaignRunMode.SHADOW
        and review_policy_required(db, owner_id=campaign.owner_id, lock=True)
    )
    if campaign.run_mode != CampaignRunMode.REVIEW and not policy_requires_review:
        return True

    task = db.query(models.Task).filter(
        models.Task.owner_id == attempt.owner_id,
        models.Task.attempt_id == attempt.id,
        models.Task.task_type == TaskType.DRAFT_REVIEW,
        models.Task.archived_at.is_(None),
    ).order_by(models.Task.id.asc()).first()
    if task and task.status == TaskStatus.COMPLETED:
        return True
    if task and task.status == TaskStatus.DISMISSED:
        attempt.status = AttemptStatus.CANCELLED
        attempt.last_error = "review_rejected"
        attempt.claimed_by = None
        attempt.lease_expires_at = None
        enrollment.status = EnrollmentStatus.PAUSED
        enrollment.paused_reason = "review_rejected"
        enrollment.paused_at = utcnow()
        return False
    if not task:
        task = models.Task(
            owner_id=attempt.owner_id,
            task_type=TaskType.DRAFT_REVIEW,
            status=TaskStatus.OPEN,
            priority=TaskPriority.HIGH,
            company_id=enrollment.company_id,
            contact_id=enrollment.contact_id,
            campaign_id=enrollment.campaign_id,
            enrollment_id=enrollment.id,
            attempt_id=attempt.id,
            title="Review outreach before sending",
            description="Approve or dismiss this exact Attempt from Today's Work.",
            metadata_json={
                "attempt_id": attempt.id,
                "channel": attempt.channel.value,
                "recipient": _masked_recipient(point.value, attempt.channel),
                "subject": subject,
                "body": body,
                "unsubscribe_url": unsubscribe_url,
                "template_version": sequence_step.template_version if sequence_step else None,
                "run_mode": campaign.run_mode.value,
                "global_review_policy": policy_requires_review,
                "provider_call_allowed": False,
            },
        )
        db.add(task)
        db.flush()
        add_audit(
            db,
            owner_id=attempt.owner_id,
            actor_user_id=None,
            action="outreach_attempt.review_requested",
            entity_type="outreach_attempt",
            entity_id=attempt.id,
            after={"task_id": task.id, "provider_call_allowed": False},
            correlation_id=attempt.idempotency_key,
        )
    if sequence_step is not None and attempt.channel_account_id is not None:
        proposal = ensure_attempt_route_proposal(
            db,
            attempt=attempt,
            sequence_step=sequence_step,
            subject=subject,
            body=body,
        )
        task.metadata_json = {
            **(task.metadata_json or {}),
            "route_proposal_id": proposal.id,
            "batch_review_available": proposal.status.value == "draft",
        }
    attempt.status = AttemptStatus.BLOCKED
    attempt.last_error = "review_approval_required"
    attempt.claimed_by = None
    attempt.lease_expires_at = None
    enrollment.status = EnrollmentStatus.BLOCKED
    enrollment.paused_reason = "review_approval_required"
    enrollment.paused_at = utcnow()
    set_stage_runtime(
        db,
        owner_id=attempt.owner_id,
        campaign_id=attempt.campaign_id,
        stage_name="outbound",
        status=StageStatus.BLOCKED,
        reason="review_approval_required",
        details={"attempt_id": attempt.id, "task_id": task.id, "run_mode": "review"},
    )
    return False


def _block_sequence_definition(
    db: Session,
    *,
    enrollment: models.Enrollment,
    reason: str,
    attempt: Optional[models.OutreachAttempt] = None,
) -> None:
    code = f"sequence_definition_invalid:{reason}"[:500]
    enrollment.status = EnrollmentStatus.BLOCKED
    enrollment.paused_reason = code
    enrollment.paused_at = utcnow()
    if attempt is not None:
        attempt.status = AttemptStatus.BLOCKED
        attempt.last_error = code
        attempt.claimed_by = None
        attempt.lease_expires_at = None
    if not db.query(models.Task).filter_by(
        owner_id=enrollment.owner_id,
        enrollment_id=enrollment.id,
        task_type=TaskType.CAMPAIGN_READINESS,
        status=TaskStatus.OPEN,
    ).first():
        db.add(
            models.Task(
                owner_id=enrollment.owner_id,
                task_type=TaskType.CAMPAIGN_READINESS,
                status=TaskStatus.OPEN,
                priority=TaskPriority.URGENT,
                company_id=enrollment.company_id,
                contact_id=enrollment.contact_id,
                campaign_id=enrollment.campaign_id,
                enrollment_id=enrollment.id,
                attempt_id=attempt.id if attempt else None,
                title="Sequence definition blocked outreach",
                description=code,
                metadata_json={"reason": reason, "auto_send_allowed": False},
            )
        )
    add_audit(
        db,
        owner_id=enrollment.owner_id,
        actor_user_id=None,
        action="enrollment.sequence_definition_blocked",
        entity_type="enrollment",
        entity_id=enrollment.id,
        after={"reason": reason, "attempt_id": attempt.id if attempt else None},
    )


def _halt_sequence(
    db: Session,
    *,
    enrollment: models.Enrollment,
    now,
    reason: str,
    attempt: Optional[models.OutreachAttempt] = None,
) -> None:
    enrollment.status = EnrollmentStatus.COMPLETED
    enrollment.completed_at = now
    enrollment.paused_reason = reason[:500]
    if attempt is not None:
        attempt.status = AttemptStatus.CANCELLED
        attempt.last_error = reason[:500]
        attempt.claimed_by = None
        attempt.lease_expires_at = None
    add_audit(
        db,
        owner_id=enrollment.owner_id,
        actor_user_id=None,
        action="enrollment.sequence_halted",
        entity_type="enrollment",
        entity_id=enrollment.id,
        after={"reason": reason, "attempt_id": attempt.id if attempt else None},
    )


def create_first_attempt(db: Session, enrollment: models.Enrollment) -> Optional[models.OutreachAttempt]:
    if enrollment.archived_at is not None or enrollment.status not in {
        EnrollmentStatus.SCHEDULED,
        EnrollmentStatus.ACTIVE,
    }:
        return None
    campaign = db.get(models.Campaign, enrollment.campaign_id)
    company = db.get(models.Company, enrollment.company_id)
    contact = db.get(models.Contact, enrollment.contact_id)
    if (
        not campaign
        or campaign.archived_at is not None
        or not company
        or company.archived_at is not None
        or not contact
        or contact.archived_at is not None
    ):
        return None
    step = db.query(models.SequenceStep).filter_by(
        campaign_revision_id=enrollment.campaign_revision_id
    ).order_by(models.SequenceStep.position.asc()).first()
    if not step:
        enrollment.status = EnrollmentStatus.BLOCKED
        enrollment.paused_reason = "sequence_missing"
        return None
    condition = evaluate_execution_conditions(db, enrollment=enrollment, step=step)
    if not condition.valid:
        _block_sequence_definition(
            db,
            enrollment=enrollment,
            reason=condition.invalid_reason or "invalid_first_step_condition",
        )
        return None
    if not condition.matched:
        _halt_sequence(
            db,
            enrollment=enrollment,
            now=utcnow(),
            reason="sequence_condition_not_met",
        )
        return None
    point = db.query(models.ContactPoint).filter(
        models.ContactPoint.contact_id == enrollment.contact_id,
        models.ContactPoint.channel == step.channel,
        models.ContactPoint.archived_at.is_(None),
    ).order_by(models.ContactPoint.is_primary.desc(), models.ContactPoint.id.asc()).first()
    if not point:
        enrollment.status = EnrollmentStatus.BLOCKED
        enrollment.paused_reason = f"missing_{step.channel.value}_contact_point"
        db.add(
            models.Task(
                owner_id=enrollment.owner_id,
                task_type=TaskType.CONTACT_ENRICHMENT_REQUIRED,
                status=TaskStatus.OPEN,
                priority=TaskPriority.HIGH,
                company_id=enrollment.company_id,
                contact_id=enrollment.contact_id,
                campaign_id=enrollment.campaign_id,
                enrollment_id=enrollment.id,
                title=f"Add a {step.channel.value} contact point",
                description="The first sequence step has no matching contact point.",
            )
        )
        return None
    key = f"enrollment:{enrollment.id}:step:{step.id}"
    existing = db.query(models.OutreachAttempt).filter_by(idempotency_key=key).first()
    if existing:
        return existing
    attempt = models.OutreachAttempt(
        owner_id=enrollment.owner_id,
        campaign_id=enrollment.campaign_id,
        enrollment_id=enrollment.id,
        sequence_step_id=step.id,
        contact_point_id=point.id,
        channel_account_id=step.channel_account_id,
        channel=step.channel,
        idempotency_key=key,
        priority=enrollment.priority_snapshot,
        scheduled_at=enrollment.scheduled_at,
    )
    enrollment.status = EnrollmentStatus.ACTIVE
    db.add(attempt)
    db.flush()
    return attempt


def schedule_next_attempt(
    db: Session,
    *,
    enrollment: models.Enrollment,
    current_step: models.SequenceStep,
    now,
) -> Optional[models.OutreachAttempt]:
    stop = evaluate_stop_conditions(db, enrollment=enrollment, step=current_step)
    if not stop.valid:
        _block_sequence_definition(
            db,
            enrollment=enrollment,
            reason=stop.invalid_reason or "invalid_stop_condition",
        )
        return None
    if stop.matched:
        _halt_sequence(
            db,
            enrollment=enrollment,
            now=now,
            reason=f"sequence_stop_condition:{','.join(stop.matched_rules)}",
        )
        return None
    next_step = db.query(models.SequenceStep).filter(
        models.SequenceStep.campaign_revision_id == enrollment.campaign_revision_id,
        models.SequenceStep.position > current_step.position,
        models.SequenceStep.archived_at.is_(None),
    ).order_by(models.SequenceStep.position.asc()).first()
    if not next_step:
        enrollment.status = EnrollmentStatus.COMPLETED
        enrollment.completed_at = now
        return None
    condition = evaluate_execution_conditions(db, enrollment=enrollment, step=next_step)
    if not condition.valid:
        _block_sequence_definition(
            db,
            enrollment=enrollment,
            reason=condition.invalid_reason or "invalid_next_step_condition",
        )
        return None
    if not condition.matched:
        _halt_sequence(
            db,
            enrollment=enrollment,
            now=now,
            reason="sequence_condition_not_met",
        )
        return None
    point = db.query(models.ContactPoint).filter(
        models.ContactPoint.contact_id == enrollment.contact_id,
        models.ContactPoint.channel == next_step.channel,
        models.ContactPoint.archived_at.is_(None),
    ).order_by(models.ContactPoint.is_primary.desc(), models.ContactPoint.id.asc()).first()
    if not point:
        enrollment.status = EnrollmentStatus.BLOCKED
        enrollment.paused_reason = f"missing_{next_step.channel.value}_contact_point"
        db.add(
            models.Task(
                owner_id=enrollment.owner_id,
                task_type=TaskType.CONTACT_ENRICHMENT_REQUIRED,
                status=TaskStatus.OPEN,
                priority=TaskPriority.HIGH,
                company_id=enrollment.company_id,
                contact_id=enrollment.contact_id,
                campaign_id=enrollment.campaign_id,
                enrollment_id=enrollment.id,
                title=f"Add a {next_step.channel.value} contact point",
                description="The next sequence step has no matching contact point.",
            )
        )
        return None
    key = f"enrollment:{enrollment.id}:step:{next_step.id}"
    existing = db.query(models.OutreachAttempt).filter_by(idempotency_key=key).first()
    if existing:
        return existing
    next_attempt = models.OutreachAttempt(
        owner_id=enrollment.owner_id,
        campaign_id=enrollment.campaign_id,
        enrollment_id=enrollment.id,
        sequence_step_id=next_step.id,
        contact_point_id=point.id,
        channel_account_id=next_step.channel_account_id,
        channel=next_step.channel,
        kind=AttemptKind.FOLLOW_UP,
        idempotency_key=key,
        priority=enrollment.priority_snapshot,
        scheduled_at=now + timedelta(minutes=next_step.wait_minutes),
    )
    db.add(next_attempt)
    db.flush()
    return next_attempt


def _blocked_task_type(hard: list[str], soft: list[str]) -> TaskType:
    if "contact_point_not_verified" in hard or "contact_point_unavailable" in hard:
        return TaskType.CONTACT_ENRICHMENT_REQUIRED
    if "research_evidence" in soft:
        return TaskType.RESEARCH_REQUIRED
    return TaskType.CAMPAIGN_READINESS


def _block_channel_account_gate(
    db: Session,
    *,
    attempt: models.OutreachAttempt,
    enrollment: models.Enrollment,
    blockers: list[str],
    phase: str,
) -> models.OutreachAttempt:
    """Account gates are hard: review approval and soft overrides cannot win."""

    unique_blockers = list(dict.fromkeys(blockers or ["channel_account_not_ready"]))
    attempt.status = AttemptStatus.BLOCKED
    attempt.last_error = ",".join(unique_blockers)
    attempt.claimed_by = None
    attempt.lease_expires_at = None
    enrollment.status = EnrollmentStatus.BLOCKED
    enrollment.paused_reason = attempt.last_error[:500]
    enrollment.paused_at = utcnow()
    if not db.query(models.Task).filter_by(
        owner_id=attempt.owner_id,
        attempt_id=attempt.id,
        task_type=TaskType.CAMPAIGN_READINESS,
        status=TaskStatus.OPEN,
    ).first():
        db.add(
            models.Task(
                owner_id=attempt.owner_id,
                task_type=TaskType.CAMPAIGN_READINESS,
                status=TaskStatus.OPEN,
                priority=TaskPriority.URGENT,
                company_id=enrollment.company_id,
                contact_id=enrollment.contact_id,
                campaign_id=enrollment.campaign_id,
                enrollment_id=enrollment.id,
                attempt_id=attempt.id,
                title="Sender account blocked outreach",
                description=attempt.last_error,
                metadata_json={
                    "hard": unique_blockers,
                    "phase": phase,
                    "channel_account_id": attempt.channel_account_id,
                    "soft_override_allowed": False,
                    "provider_call_allowed": False,
                },
            )
        )
    add_audit(
        db,
        owner_id=attempt.owner_id,
        actor_user_id=None,
        action="outreach_attempt.channel_account_blocked",
        entity_type="outreach_attempt",
        entity_id=attempt.id,
        after={
            "hard": unique_blockers,
            "phase": phase,
            "channel_account_id": attempt.channel_account_id,
            "provider_call_allowed": False,
        },
        correlation_id=attempt.idempotency_key,
    )
    set_stage_runtime(
        db,
        owner_id=attempt.owner_id,
        campaign_id=attempt.campaign_id,
        stage_name="outbound",
        status=StageStatus.BLOCKED,
        reason=attempt.last_error,
        details={
            "attempt_id": attempt.id,
            "channel_account_id": attempt.channel_account_id,
            "phase": phase,
        },
    )
    return attempt


def _cancel_for_inactive_owner_path(
    db: Session,
    *,
    attempt: models.OutreachAttempt,
    phase: str,
) -> models.OutreachAttempt:
    """Stop V2 automation without crossing the Provider boundary."""

    attempt.status = AttemptStatus.CANCELLED
    attempt.last_error = "owner_v2_write_path_inactive"
    attempt.claimed_by = None
    attempt.lease_expires_at = None
    attempt.capacity_reserved_at = None
    enrollment = db.get(models.Enrollment, attempt.enrollment_id)
    if enrollment and enrollment.status in {
        EnrollmentStatus.SCHEDULED,
        EnrollmentStatus.ACTIVE,
        EnrollmentStatus.BLOCKED,
    }:
        enrollment.status = EnrollmentStatus.PAUSED
        enrollment.paused_reason = attempt.last_error
        enrollment.paused_at = utcnow()
    for cost in db.query(models.ProviderCostEvent).filter(
        models.ProviderCostEvent.outreach_attempt_id == attempt.id,
        models.ProviderCostEvent.status == ProviderCostStatus.RESERVED,
    ).all():
        cost.status = ProviderCostStatus.FAILED
        cost.metadata_json = {
            **(cost.metadata_json or {}),
            "reason": attempt.last_error,
            "phase": phase,
            "provider_call_allowed": False,
        }
    release_attempt_safety_locks(
        db,
        attempt,
        reason="owner_v2_write_path_inactive_before_provider",
    )
    add_audit(
        db,
        owner_id=attempt.owner_id,
        actor_user_id=None,
        action="outreach_attempt.owner_path_cancelled",
        entity_type="outreach_attempt",
        entity_id=attempt.id,
        after={
            "reason": attempt.last_error,
            "phase": phase,
            "provider_call_allowed": False,
        },
        correlation_id=attempt.idempotency_key,
    )
    set_stage_runtime(
        db,
        owner_id=attempt.owner_id,
        campaign_id=attempt.campaign_id,
        stage_name="outbound",
        status=StageStatus.BLOCKED,
        reason=attempt.last_error,
        details={
            "attempt_id": attempt.id,
            "phase": phase,
            "provider_call_allowed": False,
        },
    )
    return attempt


def _owner_path_allows_attempt(db: Session, attempt: models.OutreachAttempt) -> bool:
    try:
        return owner_v2_write_enabled(db, attempt.owner_id, lock=True)
    except OwnerMigrationConflict:
        return False


def execute_attempt(
    db: Session,
    *,
    attempt: models.OutreachAttempt,
    registry: ConnectorRegistry,
    after_provider_success: Optional[Callable[[], None]] = None,
    lease_fence: Optional[LeaseFence] = None,
) -> models.OutreachAttempt:
    attempt_id = entity_id(attempt)
    fence = lease_fence or captured_lease_fence(attempt)
    if fence:
        fenced_attempt = lock_attempt_for_fence(
            db,
            attempt_id=attempt_id,
            fence=fence,
        )
        if not fenced_attempt:
            db.rollback()
            return reconcile_attempt_uncertainty(
                db,
                attempt_id=attempt_id,
                fence=fence,
                reason="lease_fence_lost_before_provider",
            )
        attempt = fenced_attempt
    if attempt.status in {
        AttemptStatus.SUCCEEDED,
        AttemptStatus.FAILED,
        AttemptStatus.UNKNOWN,
        AttemptStatus.BLOCKED,
        AttemptStatus.CANCELLED,
    }:
        return attempt
    if attempt.status == AttemptStatus.SENDING:
        mark_attempt_unknown(db, attempt, "sending_state_reentered")
        return attempt

    if not _owner_path_allows_attempt(db, attempt):
        return _cancel_for_inactive_owner_path(
            db,
            attempt=attempt,
            phase="claimed_before_domain_writes",
        )

    campaign_query = db.query(models.Campaign).filter(models.Campaign.id == attempt.campaign_id)
    if db.bind and db.bind.dialect.name == "mysql":
        campaign_query = campaign_query.with_for_update()
    campaign = campaign_query.first()
    if not campaign:
        attempt.status = AttemptStatus.FAILED
        attempt.last_error = "missing_campaign"
        return attempt

    set_stage_runtime(
        db,
        owner_id=attempt.owner_id,
        campaign_id=attempt.campaign_id,
        stage_name="outbound",
        status=StageStatus.RUNNING,
        details={"attempt_id": attempt.id},
    )

    enrollment_query = db.query(models.Enrollment).filter(models.Enrollment.id == attempt.enrollment_id)
    point_query = db.query(models.ContactPoint).filter(models.ContactPoint.id == attempt.contact_point_id)
    if db.bind and db.bind.dialect.name == "mysql":
        enrollment_query = enrollment_query.with_for_update()
        point_query = point_query.with_for_update()
    enrollment = enrollment_query.first()
    point = point_query.first()
    if not enrollment or not point:
        attempt.status = AttemptStatus.FAILED
        attempt.last_error = "missing_enrollment_or_contact_point"
        set_stage_runtime(
            db,
            owner_id=attempt.owner_id,
            campaign_id=attempt.campaign_id,
            stage_name="outbound",
            status=StageStatus.FAILED,
            reason=attempt.last_error,
            details={"attempt_id": attempt.id},
        )
        return attempt
    current_step = db.get(models.SequenceStep, attempt.sequence_step_id)
    if attempt.channel.value == "offline":
        _block_sequence_definition(
            db,
            enrollment=enrollment,
            attempt=attempt,
            reason="offline_channel_cannot_execute_outreach",
        )
        return attempt
    try:
        connector = registry.get(attempt.channel)
    except LookupError:
        _block_sequence_definition(
            db,
            enrollment=enrollment,
            attempt=attempt,
            reason=f"connector_unavailable:{attempt.channel.value}",
        )
        return attempt
    message_subject = "Product V2 fake outreach" if attempt.channel.value == "email" else None
    message_body = "Fake connector event: no external message was sent."
    if campaign.run_mode == CampaignRunMode.SHADOW and not connector.is_fake:
        _apply_campaign_run_mode(
            db,
            campaign=campaign,
            enrollment=enrollment,
            attempt=attempt,
            point=point,
            connector=connector,
            sequence_step=current_step,
            subject=message_subject,
            body=message_body,
            unsubscribe_url=None,
        )
        return attempt
    account_resolution = resolve_attempt_account(
        db,
        attempt=attempt,
        step=current_step,
        connector=connector,
    )
    if not account_resolution.allowed:
        return _block_channel_account_gate(
            db,
            attempt=attempt,
            enrollment=enrollment,
            blockers=account_resolution.blockers,
            phase="binding",
        )
    if current_step:
        prior_steps = db.query(models.SequenceStep).filter(
            models.SequenceStep.campaign_revision_id == current_step.campaign_revision_id,
            models.SequenceStep.position < current_step.position,
            models.SequenceStep.archived_at.is_(None),
        ).order_by(models.SequenceStep.position.asc()).all()
        for prior_step in prior_steps:
            stop = evaluate_stop_conditions(db, enrollment=enrollment, step=prior_step)
            if not stop.valid:
                _block_sequence_definition(
                    db,
                    enrollment=enrollment,
                    attempt=attempt,
                    reason=stop.invalid_reason or "invalid_stop_condition",
                )
                return attempt
            if stop.matched:
                _halt_sequence(
                    db,
                    enrollment=enrollment,
                    attempt=attempt,
                    now=utcnow(),
                    reason=f"sequence_stop_condition:{','.join(stop.matched_rules)}",
                )
                return attempt
        condition = evaluate_execution_conditions(
            db,
            enrollment=enrollment,
            step=current_step,
            connector_is_fake=connector.is_fake,
        )
        if not condition.valid:
            _block_sequence_definition(
                db,
                enrollment=enrollment,
                attempt=attempt,
                reason=condition.invalid_reason or "invalid_execution_condition",
            )
            return attempt
        if not condition.matched:
            _halt_sequence(
                db,
                enrollment=enrollment,
                attempt=attempt,
                now=utcnow(),
                reason="sequence_condition_not_met",
            )
            return attempt
    company_query = db.query(models.Company).filter(models.Company.id == enrollment.company_id)
    if db.bind and db.bind.dialect.name == "mysql":
        company_query = company_query.with_for_update()
    company = company_query.first()
    if not company:
        _block_sequence_definition(
            db,
            enrollment=enrollment,
            attempt=attempt,
            reason="missing_message_company",
        )
        return attempt
    message_unsubscribe_url = None
    message_review_approved = False
    try:
        # REVIEW approval is an exact message snapshot, regardless of whether
        # the transport is the isolated fake connector or an approved real
        # connector.  This keeps local E2E faithful to what a human reviewed.
        approved = _approved_review_message(db, attempt=attempt)
        if approved is not None:
            message_review_approved = True
            message_subject, message_body, message_unsubscribe_url = approved
        elif not connector.is_fake:
            contact = db.get(models.Contact, enrollment.contact_id)
            revision_for_message = db.get(models.CampaignRevision, enrollment.campaign_revision_id)
            policy_url = configured_public_unsubscribe_url(db, owner_id=attempt.owner_id)
            revision_url = str(
                (revision_for_message.stop_conditions if revision_for_message else {}).get(
                    "public_unsubscribe_url"
                )
                or ""
            )
            rendered = render_sequence_message(
                channel=attempt.channel,
                subject_template=current_step.subject_template if current_step else None,
                body_template=current_step.body_template if current_step else None,
                company_name=company.name,
                company_domain=company.normalized_domain,
                contact_name=contact.full_name if contact else "",
                job_title=contact.job_title if contact else "",
                owner_id=attempt.owner_id,
                contact_point_id=point.id,
                contact_point_identity_hash=point.normalized_value_hash,
                public_unsubscribe_base_url=policy_url or revision_url,
            )
            message_subject = rendered.subject
            message_body = rendered.body
            message_unsubscribe_url = rendered.unsubscribe_url
    except MessageRenderError as exc:
        _block_sequence_definition(
            db,
            enrollment=enrollment,
            attempt=attempt,
            reason=f"message_render:{exc}",
        )
        return attempt
    decision = evaluate_outreach_gates(
        db,
        enrollment=enrollment,
        contact_point=point,
        lock_budget=True,
        cold_start=attempt.kind == AttemptKind.COLD,
        expected_channel=attempt.channel,
        sequence_step_id=attempt.sequence_step_id,
        attempt_id=attempt.id,
        channel_account_id=attempt.channel_account_id,
        provider_billable=not connector.is_fake,
    )
    if not decision.allowed:
        cooldown_codes = {"contact_point_cooldown_14d", "company_cooldown_24h"}
        in_flight_locks = active_attempt_safety_locks(
            db,
            enrollment=enrollment,
            contact_point=point,
        )
        if (
            decision.hard_blockers == ["safety_lock"]
            and not decision.soft_blockers
            and in_flight_locks
        ):
            # An attempt-bound lock is transient while the other worker waits
            # for its Provider. Keep this attempt retryable. If that result
            # becomes UNKNOWN, mark_attempt_unknown will pause this Enrollment
            # before the retry can be claimed.
            retry_at = utcnow() + timedelta(seconds=30)
            attempt.status = AttemptStatus.QUEUED
            attempt.scheduled_at = retry_at
            attempt.claimed_by = None
            attempt.lease_expires_at = None
            attempt.last_error = "deferred:provider_in_flight_safety_lock"
            add_audit(
                db,
                owner_id=attempt.owner_id,
                actor_user_id=None,
                action="outreach_attempt.deferred",
                entity_type="outreach_attempt",
                entity_id=attempt.id,
                after={
                    "reason": "provider_in_flight_safety_lock",
                    "safety_lock_ids": [lock.id for lock in in_flight_locks],
                    "scheduled_at": retry_at.isoformat(),
                },
                correlation_id=attempt.idempotency_key,
            )
            set_stage_runtime(
                db,
                owner_id=attempt.owner_id,
                campaign_id=attempt.campaign_id,
                stage_name="outbound",
                status=StageStatus.BACKOFF,
                reason=attempt.last_error,
                details={"attempt_id": attempt.id, "scheduled_at": retry_at.isoformat()},
            )
            return attempt
        if (
            decision.hard_blockers
            and set(decision.hard_blockers).issubset(cooldown_codes)
            and not decision.soft_blockers
        ):
            eligible_times = []
            if "contact_point_cooldown_14d" in decision.hard_blockers and point.last_cold_outreach_at:
                eligible_times.append(as_utc(point.last_cold_outreach_at) + timedelta(days=14))
            company = db.get(models.Company, enrollment.company_id)
            if "company_cooldown_24h" in decision.hard_blockers and company and company.last_cold_outreach_at:
                eligible_times.append(as_utc(company.last_cold_outreach_at) + timedelta(hours=24))
            attempt.status = AttemptStatus.QUEUED
            attempt.scheduled_at = max(eligible_times)
            attempt.claimed_by = None
            attempt.lease_expires_at = None
            attempt.last_error = "deferred:" + ",".join(decision.hard_blockers)
            add_audit(
                db,
                owner_id=attempt.owner_id,
                actor_user_id=None,
                action="outreach_attempt.deferred",
                entity_type="outreach_attempt",
                entity_id=attempt.id,
                after={"reason": decision.hard_blockers, "scheduled_at": attempt.scheduled_at.isoformat()},
                correlation_id=attempt.idempotency_key,
            )
            set_stage_runtime(
                db,
                owner_id=attempt.owner_id,
                campaign_id=attempt.campaign_id,
                stage_name="outbound",
                status=StageStatus.BACKOFF,
                reason=attempt.last_error,
                details={"attempt_id": attempt.id, "scheduled_at": attempt.scheduled_at.isoformat()},
            )
            return attempt
        attempt.status = AttemptStatus.BLOCKED
        attempt.last_error = ",".join(decision.hard_blockers + decision.soft_blockers)
        enrollment.status = EnrollmentStatus.BLOCKED
        enrollment.paused_reason = attempt.last_error[:500]
        db.add(
            models.Task(
                owner_id=attempt.owner_id,
                task_type=_blocked_task_type(decision.hard_blockers, decision.soft_blockers),
                status=TaskStatus.OPEN,
                priority=TaskPriority.HIGH,
                company_id=enrollment.company_id,
                contact_id=enrollment.contact_id,
                campaign_id=enrollment.campaign_id,
                enrollment_id=enrollment.id,
                attempt_id=attempt.id,
                title="Outreach attempt is blocked",
                description=attempt.last_error,
                metadata_json={"hard": decision.hard_blockers, "soft": decision.soft_blockers},
            )
        )
        add_audit(
            db,
            owner_id=attempt.owner_id,
            actor_user_id=None,
            action="outreach_attempt.blocked",
            entity_type="outreach_attempt",
            entity_id=attempt.id,
            after={"hard": decision.hard_blockers, "soft": decision.soft_blockers},
            correlation_id=attempt.idempotency_key,
        )
        set_stage_runtime(
            db,
            owner_id=attempt.owner_id,
            campaign_id=attempt.campaign_id,
            stage_name="outbound",
            status=StageStatus.BLOCKED,
            reason=attempt.last_error,
            details={"attempt_id": attempt.id},
        )
        return attempt

    if not _apply_campaign_run_mode(
        db,
        campaign=campaign,
        enrollment=enrollment,
        attempt=attempt,
        point=point,
        connector=connector,
        sequence_step=current_step,
        subject=message_subject,
        body=message_body,
        unsubscribe_url=message_unsubscribe_url,
    ):
        return attempt

    account_gate = lock_and_reserve_attempt_account(
        db,
        attempt=attempt,
        connector_provider=connector.provider,
    )
    if not account_gate.allowed:
        return _block_channel_account_gate(
            db,
            attempt=attempt,
            enrollment=enrollment,
            blockers=account_gate.blockers,
            phase="pre_provider_locked_gate",
        )
    channel_account = account_gate.account
    attempt.status = AttemptStatus.SENDING
    ensure_attempt_safety_locks(db, attempt)
    revision = db.get(models.CampaignRevision, enrollment.campaign_revision_id)
    budget = campaign_budget_snapshot(db, revision, attempt.campaign_id)
    global_budget = global_budget_snapshot(
        db,
        owner_id=attempt.owner_id,
        lock=not connector.is_fake,
    )
    normalized_unit_price = None
    normalized_currency = None
    price_version = str(
        (revision.budget_definition if revision else {}).get("price_version")
        or ("fake-v1" if connector.is_fake else "unconfigured")
    )
    if not connector.is_fake and global_budget.configured and global_budget.limit is not None:
        normalized_unit_price, pricing_error = revision_unit_price(revision, global_budget)
        if pricing_error or normalized_unit_price is None:
            # The execution gate above should already have rejected this. Keep
            # the Provider boundary independently fail-closed if state changes
            # inside this transaction or a future caller omits that gate.
            attempt.status = AttemptStatus.BLOCKED
            attempt.last_error = pricing_error or "global_budget_pricing_missing"
            enrollment.status = EnrollmentStatus.BLOCKED
            enrollment.paused_reason = attempt.last_error
            return attempt
        normalized_currency = global_budget.currency
        price_version = global_budget.price_version
    cost_key = f"cost:{attempt.idempotency_key}"
    existing_cost = db.query(models.ProviderCostEvent).filter_by(idempotency_key=cost_key).first()
    if existing_cost:
        mark_attempt_unknown(db, attempt, "provider_cost_reservation_already_exists")
        set_stage_runtime(
            db,
            owner_id=attempt.owner_id,
            campaign_id=attempt.campaign_id,
            stage_name="outbound",
            status=StageStatus.FAILED,
            reason="provider_cost_reservation_already_exists",
            details={"attempt_id": attempt.id, "requires_reconciliation": True},
        )
        return attempt
    cost = models.ProviderCostEvent(
        owner_id=attempt.owner_id,
        provider=connector.provider,
        operation=f"{attempt.channel.value}_send",
        status=ProviderCostStatus.RESERVED,
        units=1,
        native_unit=budget.native_unit,
        unit_price=normalized_unit_price,
        normalized_amount=normalized_unit_price,
        normalized_currency=normalized_currency,
        result_count=0,
        billable=not connector.is_fake,
        price_version=price_version,
        campaign_id=attempt.campaign_id,
        enrollment_id=attempt.enrollment_id,
        company_id=enrollment.company_id,
        contact_id=enrollment.contact_id,
        outreach_attempt_id=attempt.id,
        idempotency_key=cost_key,
        metadata_json={
            "fake": connector.is_fake,
            "network_calls": 0 if connector.is_fake else None,
            "channel_account_id": channel_account.id,
        },
    )
    db.add(cost)
    db.flush()
    request = ConnectorRequest(
        channel=attempt.channel,
        idempotency_key=attempt.idempotency_key,
        recipient=point.value,
        subject=message_subject,
        body=message_body,
        metadata={
            "attempt_id": attempt.id,
            "campaign_id": attempt.campaign_id,
            "channel_account_id": channel_account.id,
            "provider_account_id": channel_account.provider_account_id,
            "owner_id": attempt.owner_id,
            "contact_point_id": point.id,
            "unsubscribe_url": message_unsubscribe_url,
            "run_mode": campaign.run_mode.value,
            "review_approved": message_review_approved,
            "approval_id": (
                db.query(models.ReviewBatch.approval_id)
                .join(
                    models.EnrollmentRouteStep,
                    models.EnrollmentRouteStep.review_batch_id == models.ReviewBatch.id,
                )
                .filter(models.EnrollmentRouteStep.attempt_id == attempt.id)
                .scalar()
            ),
            "price_version": (
                db.query(models.ReviewBatch.price_version)
                .join(
                    models.EnrollmentRouteStep,
                    models.EnrollmentRouteStep.review_batch_id == models.ReviewBatch.id,
                )
                .filter(models.EnrollmentRouteStep.attempt_id == attempt.id)
                .scalar()
                or price_version
            ),
        },
    )
    # Persist SENDING + RESERVED before crossing the Provider boundary. If the
    # process is killed at any later instruction, lease recovery can prove that
    # a call may have happened and will force manual reconciliation.
    cost_id = cost.id
    db.commit()
    if fence:
        # Validate the immutable owner+generation and renew immediately before
        # Provider I/O.  The final response still has to pass the same fence;
        # renewal only reduces the window in which a healthy call outlives its
        # claim.
        if not renew_attempt_lease(
            db,
            attempt_id=attempt_id,
            fence=fence,
        ):
            db.rollback()
            return reconcile_attempt_uncertainty(
                db,
                attempt_id=attempt_id,
                fence=fence,
                reason="lease_fence_lost_before_provider_call",
            )
        db.commit()
    # The preflight transaction above intentionally commits SENDING and its
    # cost reservation before external I/O.  Re-acquire the owner-row fence in
    # this new transaction at the last possible instruction before Provider
    # invocation, and retain it until the result is durably handled.
    attempt = db.get(models.OutreachAttempt, attempt_id)
    if not _owner_path_allows_attempt(db, attempt):
        return _cancel_for_inactive_owner_path(
            db,
            attempt=attempt,
            phase="provider_boundary",
        )
    try:
        try:
            registry.assert_connector_allowed(connector)
        except RuntimeError:
            result = ConnectorResult(
                accepted=False,
                provider=connector.provider,
                provider_message_id=None,
                raw={"reason": "runtime_outbound_gate_closed", "provider_called": False},
            )
        else:
            result = connector.send(request)
    except Exception as exc:
        db.rollback()
        if fence:
            return reconcile_attempt_uncertainty(
                db,
                attempt_id=attempt_id,
                fence=fence,
                reason=f"provider_call_uncertain:{type(exc).__name__}",
                provider_trace={"provider_exception": type(exc).__name__},
                fence_lost=False,
            )
        attempt = db.get(models.OutreachAttempt, attempt_id)
        cost = db.get(models.ProviderCostEvent, cost_id)
        if cost:
            cost.status = ProviderCostStatus.UNKNOWN
            cost.metadata_json = {
                **(cost.metadata_json or {}),
                "provider_exception": type(exc).__name__,
                "requires_reconciliation": True,
            }
        mark_attempt_unknown(db, attempt, f"provider_call_uncertain:{type(exc).__name__}")
        set_stage_runtime(
            db,
            owner_id=attempt.owner_id,
            campaign_id=attempt.campaign_id,
            stage_name="outbound",
            status=StageStatus.FAILED,
            reason="provider_call_uncertain",
            details={"attempt_id": attempt.id, "requires_reconciliation": True},
        )
        return attempt
    try:
        if after_provider_success:
            after_provider_success()
    except Exception as exc:
        db.rollback()
        if fence:
            return reconcile_attempt_uncertainty(
                db,
                attempt_id=attempt_id,
                fence=fence,
                reason=f"provider_success_local_uncertain:{exc}",
                provider_trace={
                    "provider": result.provider,
                    "provider_message_id": result.provider_message_id,
                    "provider_response": result.raw,
                },
                fence_lost=False,
            )
        attempt = db.get(models.OutreachAttempt, attempt_id)
        cost = db.get(models.ProviderCostEvent, cost_id)
        cost.status = ProviderCostStatus.UNKNOWN
        cost.provider = result.provider
        cost.metadata_json = {**(cost.metadata_json or {}), **result.raw}
        mark_attempt_unknown(db, attempt, f"provider_success_local_uncertain:{exc}")
        set_stage_runtime(
            db,
            owner_id=attempt.owner_id,
            campaign_id=attempt.campaign_id,
            stage_name="outbound",
            status=StageStatus.FAILED,
            reason="provider_success_local_uncertain",
            details={"attempt_id": attempt.id, "requires_reconciliation": True},
        )
        return attempt

    if fence:
        attempt = lock_attempt_for_fence(
            db,
            attempt_id=attempt_id,
            fence=fence,
            statuses=(AttemptStatus.SENDING,),
        )
        if not attempt:
            db.rollback()
            return reconcile_attempt_uncertainty(
                db,
                attempt_id=attempt_id,
                fence=fence,
                reason="provider_result_after_lease_fence_lost",
                provider_trace={
                    "provider": result.provider,
                    "provider_message_id": result.provider_message_id,
                    "provider_response": result.raw,
                    "accepted": result.accepted,
                },
            )
        cost_query = db.query(models.ProviderCostEvent).filter(
            models.ProviderCostEvent.id == cost_id,
            models.ProviderCostEvent.outreach_attempt_id == attempt_id,
            models.ProviderCostEvent.status == ProviderCostStatus.RESERVED,
        )
        if db.bind and db.bind.dialect.name == "mysql":
            cost_query = cost_query.with_for_update()
        cost = cost_query.populate_existing().first()
        if not cost:
            db.rollback()
            return reconcile_attempt_uncertainty(
                db,
                attempt_id=attempt_id,
                fence=fence,
                reason="provider_result_cost_reservation_lost",
                provider_trace={
                    "provider": result.provider,
                    "provider_message_id": result.provider_message_id,
                    "provider_response": result.raw,
                    "accepted": result.accepted,
                },
                fence_lost=False,
            )
    else:
        attempt = db.query(models.OutreachAttempt).filter(
            models.OutreachAttempt.id == attempt_id,
        ).populate_existing().one()
        cost = db.query(models.ProviderCostEvent).filter(
            models.ProviderCostEvent.id == cost_id,
        ).populate_existing().one()
        if attempt.status != AttemptStatus.SENDING:
            # A concurrent authoritative webhook may have settled the Provider
            # boundary while this non-fenced local caller was in send(). Never
            # duplicate its event/cost/cooldown mutations.
            return attempt

    # Only the worker that still owns the original generation can persist the
    # Provider response or turn RESERVED into CHARGED/FAILED.
    attempt.provider = result.provider
    attempt.provider_message_id = result.provider_message_id
    attempt.provider_response = result.raw
    enrollment = db.get(models.Enrollment, attempt.enrollment_id)
    point = db.get(models.ContactPoint, attempt.contact_point_id)
    now = utcnow()
    if not result.accepted:
        rejection_reason = str((result.raw or {}).get("reason") or "provider_rejected")
        attempt.status = AttemptStatus.FAILED
        attempt.last_error = rejection_reason[:500]
        enrollment.status = EnrollmentStatus.BLOCKED
        enrollment.paused_reason = attempt.last_error
        cost.status = ProviderCostStatus.FAILED
        cost.provider = result.provider
        cost.metadata_json = {**(cost.metadata_json or {}), **result.raw}
        released_locks = release_attempt_safety_locks(
            db,
            attempt,
            reason=attempt.last_error,
        )
        add_audit(
            db,
            owner_id=attempt.owner_id,
            actor_user_id=None,
            action="outreach_attempt.failed",
            entity_type="outreach_attempt",
            entity_id=attempt.id,
            after={"reason": attempt.last_error, "released_safety_locks": released_locks},
            correlation_id=attempt.idempotency_key,
        )
        db.add(
            models.Task(
                owner_id=attempt.owner_id,
                task_type=TaskType.SEND_FAILURE,
                status=TaskStatus.OPEN,
                priority=TaskPriority.URGENT,
                company_id=enrollment.company_id,
                contact_id=enrollment.contact_id,
                campaign_id=enrollment.campaign_id,
                enrollment_id=enrollment.id,
                attempt_id=attempt.id,
                title=(
                    "Outbound runtime gate stopped outreach"
                    if rejection_reason == "runtime_outbound_gate_closed"
                    else "Provider rejected outreach attempt"
                ),
                description=attempt.last_error,
            )
        )
        set_stage_runtime(
            db,
            owner_id=attempt.owner_id,
            campaign_id=attempt.campaign_id,
            stage_name="outbound",
            status=StageStatus.FAILED,
            reason=attempt.last_error,
            details={"attempt_id": attempt.id},
        )
        return attempt
    db.add(
        models.MessageEvent(
            owner_id=attempt.owner_id,
            outreach_attempt_id=attempt.id,
            channel=attempt.channel,
            direction=MessageDirection.OUTBOUND,
            event_type=MessageEventType.SENT,
            provider=result.provider,
            provider_event_id=f"{result.provider_message_id}:sent",
            provider_message_id=result.provider_message_id,
            subject=request.subject,
            body=request.body,
            occurred_at=now,
            metadata_json={
                "fake": connector.is_fake,
                "channel_account_id": attempt.channel_account_id,
            },
        )
    )
    cost.status = ProviderCostStatus.CHARGED
    cost.provider = result.provider
    cost.result_count = 1
    cost.metadata_json = {**(cost.metadata_json or {}), **result.raw}
    attempt.status = AttemptStatus.SUCCEEDED
    attempt.sent_at = now
    attempt.claimed_by = None
    attempt.lease_expires_at = None
    if attempt.kind == AttemptKind.COLD:
        point.last_cold_outreach_at = now
        company = db.get(models.Company, enrollment.company_id)
        if company:
            company.last_cold_outreach_at = now
    released_locks = release_attempt_safety_locks(
        db,
        attempt,
        reason="provider_accepted",
    )
    for override_id in decision.overrides:
        override = db.get(models.ManualOverride, override_id)
        if override:
            override.consumed_at = now
            override.attempt_id = attempt.id
    current_step = db.get(models.SequenceStep, attempt.sequence_step_id)
    next_attempt = schedule_next_attempt(db, enrollment=enrollment, current_step=current_step, now=now) if current_step else None
    add_audit(
        db,
        owner_id=attempt.owner_id,
        actor_user_id=None,
        action="outreach_attempt.succeeded",
        entity_type="outreach_attempt",
        entity_id=attempt.id,
        after={
            "provider": result.provider,
            "provider_message_id": result.provider_message_id,
            "channel_account_id": attempt.channel_account_id,
            "next_attempt_id": next_attempt.id if next_attempt else None,
            "released_safety_locks": released_locks,
        },
        correlation_id=attempt.idempotency_key,
    )
    activation_campaign = db.query(models.AuditEvent.id).filter_by(
        owner_id=attempt.owner_id,
        action="activation.launch_completed",
        entity_type="campaign",
        entity_id=str(attempt.campaign_id),
    ).first()
    first_activation_send = db.query(models.AuditEvent.id).filter_by(
        owner_id=attempt.owner_id,
        action="activation.first_send_succeeded",
        entity_type="campaign",
        entity_id=str(attempt.campaign_id),
    ).first()
    if activation_campaign and not first_activation_send:
        add_audit(
            db,
            owner_id=attempt.owner_id,
            actor_user_id=None,
            action="activation.first_send_succeeded",
            entity_type="campaign",
            entity_id=attempt.campaign_id,
            after={"attempt_id": attempt.id, "sent_at": now.isoformat()},
            correlation_id=attempt.idempotency_key,
        )
    set_stage_runtime(
        db,
        owner_id=attempt.owner_id,
        campaign_id=attempt.campaign_id,
        stage_name="outbound",
        status=StageStatus.IDLE,
        details={"attempt_id": attempt.id, "provider": result.provider, "next_attempt_id": next_attempt.id if next_attempt else None},
    )
    return attempt


def run_one_attempt(
    db: Session,
    *,
    worker_name: str,
    registry: ConnectorRegistry,
) -> Optional[models.OutreachAttempt]:
    attempt = claim_attempt(db, worker_name=worker_name)
    if not attempt:
        return None
    return execute_attempt(
        db,
        attempt=attempt,
        registry=registry,
        lease_fence=captured_lease_fence(attempt),
    )
