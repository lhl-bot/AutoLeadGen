"""Owner-scoped AI route proposals and atomic batch approval."""
from __future__ import annotations

import hashlib
import hmac
import json
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import or_
from sqlalchemy.orm import Session

from product_v2 import models
from product_v2.enums import (
    AttemptStatus,
    Channel,
    ChannelAccountHealth,
    ContactPointAvailabilityStatus,
    ContactPointVerificationStatus,
    EnrollmentStatus,
    ReviewBatchStatus,
    RouteProposalStatus,
    TaskStatus,
    TaskType,
)
from product_v2.schemas import (
    ReviewBatchItemUpdate,
    ReviewBatchPreviewRequest,
    RouteProposalCreate,
)
from product_v2.services.domain import add_audit, utcnow


MIN_ROUTE_CONFIDENCE = Decimal("0.7500")


class RouteReviewError(RuntimeError):
    def __init__(self, code: str, message: str, *, status_code: int = 409):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


def _digest(payload: Any) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _iso(value: datetime, *, require_timezone: bool = True) -> str:
    if value.tzinfo is None and require_timezone:
        raise RouteReviewError("TIMEZONE_REQUIRED", "Route send times must include a timezone", status_code=422)
    if value.tzinfo is None:
        normalized = value
    else:
        normalized = value.astimezone(timezone.utc).replace(tzinfo=None)
    return f"{normalized.isoformat()}Z"


def _confidence(value: Decimal) -> str:
    return f"{Decimal(value):.4f}"


def _proposal_state(payload: RouteProposalCreate) -> dict[str, Any]:
    return {
        "enrollment_id": payload.enrollment_id,
        "ai_model": payload.ai_model,
        "ai_reason": payload.ai_reason,
        "confidence": _confidence(payload.confidence),
        "evidence_snapshot_ids": payload.evidence_snapshot_ids,
        "steps": [
            {
                "position": step.position,
                "sequence_step_id": step.sequence_step_id,
                "attempt_id": step.attempt_id,
                "contact_point_id": step.contact_point_id,
                "channel_account_id": step.channel_account_id,
                "channel": step.channel.value,
                "scheduled_at": _iso(step.scheduled_at),
                "subject": step.subject,
                "body": step.body,
                "ai_reason": step.ai_reason,
                "confidence": _confidence(step.confidence),
                "evidence_snapshot_ids": step.evidence_snapshot_ids,
            }
            for step in payload.steps
        ],
    }


def _stored_proposal_state(db: Session, proposal: models.RouteProposal) -> dict[str, Any]:
    steps = (
        db.query(models.RouteProposalStep)
        .filter_by(owner_id=proposal.owner_id, route_proposal_id=proposal.id)
        .order_by(models.RouteProposalStep.position.asc())
        .all()
    )
    return {
        "enrollment_id": proposal.enrollment_id,
        "ai_model": proposal.ai_model,
        "ai_reason": proposal.ai_reason,
        "confidence": _confidence(proposal.confidence),
        "evidence_snapshot_ids": proposal.evidence_snapshot_ids or [],
        "steps": [
            {
                "position": step.position,
                "sequence_step_id": step.sequence_step_id,
                "attempt_id": step.attempt_id,
                "contact_point_id": step.contact_point_id,
                "channel_account_id": step.channel_account_id,
                "channel": step.channel.value,
                "scheduled_at": _iso(step.scheduled_at, require_timezone=False),
                "subject": step.subject,
                "body": step.body,
                "ai_reason": step.ai_reason,
                "confidence": _confidence(step.confidence),
                "evidence_snapshot_ids": step.evidence_snapshot_ids or [],
            }
            for step in steps
        ],
    }


def _owned(db: Session, model, entity_id: int, owner_id: int):
    entity = db.query(model).filter_by(id=entity_id, owner_id=owner_id).first()
    if entity is None:
        raise RouteReviewError("OWNED_RESOURCE_NOT_FOUND", "The requested resource was not found", status_code=404)
    return entity


def _has_active_whatsapp_consent(
    db: Session,
    *,
    owner_id: int,
    contact_point_id: int,
    at: datetime,
) -> bool:
    return (
        db.query(models.WhatsAppConsent.id)
        .filter(
            models.WhatsAppConsent.owner_id == owner_id,
            models.WhatsAppConsent.contact_point_id == contact_point_id,
            models.WhatsAppConsent.granted_at <= at,
            models.WhatsAppConsent.revoked_at.is_(None),
            or_(models.WhatsAppConsent.expires_at.is_(None), models.WhatsAppConsent.expires_at > at),
        )
        .first()
        is not None
    )


def _validate_step(
    db: Session,
    *,
    owner_id: int,
    enrollment: models.Enrollment,
    step: dict[str, Any],
    require_consent: bool,
) -> None:
    contact_point = _owned(db, models.ContactPoint, int(step["contact_point_id"]), owner_id)
    account = _owned(db, models.ChannelAccount, int(step["channel_account_id"]), owner_id)
    sequence_step = _owned(db, models.SequenceStep, int(step["sequence_step_id"]), owner_id)
    channel = Channel(step["channel"])
    if contact_point.contact_id != enrollment.contact_id or contact_point.channel != channel:
        raise RouteReviewError("ROUTE_CONTACT_POINT_MISMATCH", "Route contact point no longer matches the contact and channel")
    if contact_point.verification_status != ContactPointVerificationStatus.VALID:
        raise RouteReviewError("ROUTE_CONTACT_POINT_NOT_VALID", "Route contact point is not verified valid")
    if contact_point.availability_status != ContactPointAvailabilityStatus.AVAILABLE:
        raise RouteReviewError("ROUTE_CONTACT_POINT_RESTRICTED", "Route contact point is restricted")
    if account.channel != channel or not account.enabled or account.archived_at is not None:
        raise RouteReviewError("ROUTE_ACCOUNT_UNAVAILABLE", "The selected channel account is unavailable")
    if account.health_status == ChannelAccountHealth.UNHEALTHY:
        raise RouteReviewError("ROUTE_ACCOUNT_UNHEALTHY", "The selected channel account is unhealthy")
    if sequence_step.campaign_revision_id != enrollment.campaign_revision_id or sequence_step.channel != channel:
        raise RouteReviewError("ROUTE_SEQUENCE_MISMATCH", "Route step no longer belongs to the approved plan")
    if step.get("attempt_id") is not None:
        attempt = _owned(db, models.OutreachAttempt, int(step["attempt_id"]), owner_id)
        if attempt.enrollment_id != enrollment.id or attempt.channel != channel:
            raise RouteReviewError("ROUTE_ATTEMPT_MISMATCH", "Route attempt no longer belongs to this contact route")
    if require_consent and channel == Channel.WHATSAPP and not _has_active_whatsapp_consent(
        db,
        owner_id=owner_id,
        contact_point_id=contact_point.id,
        at=utcnow(),
    ):
        raise RouteReviewError("WHATSAPP_CONSENT_REQUIRED", "Active affirmative WhatsApp consent is required")


def create_route_proposal(
    db: Session,
    *,
    owner_id: int,
    actor_user_id: int,
    payload: RouteProposalCreate,
) -> models.RouteProposal:
    state = _proposal_state(payload)
    checksum = _digest(state)
    existing = (
        db.query(models.RouteProposal)
        .filter_by(owner_id=owner_id, idempotency_key=payload.idempotency_key)
        .first()
    )
    if existing is not None:
        if not hmac.compare_digest(existing.checksum, checksum):
            raise RouteReviewError("IDEMPOTENCY_CONFLICT", "Idempotency key was already used for a different route")
        return existing

    enrollment = _owned(db, models.Enrollment, payload.enrollment_id, owner_id)
    evidence_ids = set(payload.evidence_snapshot_ids)
    evidence_ids.update(item for step in payload.steps for item in step.evidence_snapshot_ids)
    existing_evidence = {
        item[0]
        for item in db.query(models.EvidenceSnapshot.id).filter(
            models.EvidenceSnapshot.owner_id == owner_id,
            models.EvidenceSnapshot.id.in_(evidence_ids or {-1}),
            models.EvidenceSnapshot.archived_at.is_(None),
        )
    }
    evidence_complete = bool(evidence_ids) and existing_evidence == evidence_ids
    confidence_complete = payload.confidence >= MIN_ROUTE_CONFIDENCE and all(
        step.confidence >= MIN_ROUTE_CONFIDENCE for step in payload.steps
    )
    for step in state["steps"]:
        _validate_step(
            db,
            owner_id=owner_id,
            enrollment=enrollment,
            step=step,
            require_consent=True,
        )

    proposal = models.RouteProposal(
        owner_id=owner_id,
        enrollment_id=enrollment.id,
        contact_id=enrollment.contact_id,
        status=(
            RouteProposalStatus.DRAFT
            if evidence_complete and confidence_complete
            else RouteProposalStatus.HUMAN_REVIEW_REQUIRED
        ),
        idempotency_key=payload.idempotency_key,
        ai_model=payload.ai_model,
        ai_reason=payload.ai_reason,
        confidence=payload.confidence,
        evidence_snapshot_ids=payload.evidence_snapshot_ids,
        checksum=checksum,
        proposed_at=utcnow(),
    )
    db.add(proposal)
    db.flush()
    for index, step in enumerate(payload.steps):
        step_values = step.model_dump()
        step_values["scheduled_at"] = datetime.fromisoformat(state["steps"][index]["scheduled_at"])
        db.add(
            models.RouteProposalStep(
                owner_id=owner_id,
                route_proposal_id=proposal.id,
                **step_values,
            )
        )
    if proposal.status == RouteProposalStatus.HUMAN_REVIEW_REQUIRED:
        db.add(
            models.Task(
                owner_id=owner_id,
                task_type=TaskType.RESEARCH_REQUIRED,
                title="补充客户路线证据",
                description="AI 路线置信度低于 0.75 或证据不完整，不能生成发送动作。",
                company_id=enrollment.company_id,
                contact_id=enrollment.contact_id,
                campaign_id=enrollment.campaign_id,
                enrollment_id=enrollment.id,
                assignee_user_id=actor_user_id,
                metadata_json={"route_proposal_id": proposal.id},
            )
        )
    add_audit(
        db,
        owner_id=owner_id,
        actor_user_id=actor_user_id,
        action="route_proposal.created",
        entity_type="route_proposal",
        entity_id=proposal.id,
        after={"checksum": checksum, "status": proposal.status.value, "step_count": len(payload.steps)},
    )
    db.commit()
    db.refresh(proposal)
    return proposal


def ensure_attempt_route_proposal(
    db: Session,
    *,
    attempt: models.OutreachAttempt,
    sequence_step: models.SequenceStep,
    subject: str | None,
    body: str,
) -> models.RouteProposal:
    """Create the single-step route shown by the batch approval workbench.

    This is the deterministic structured fallback for an already-rendered AI
    draft. It never calls a Provider and remains subject to the same 0.75 and
    evidence gates as externally generated multi-step proposals.
    """

    existing = db.query(models.RouteProposal).filter_by(
        owner_id=attempt.owner_id,
        idempotency_key=f"attempt-route:{attempt.idempotency_key}",
    ).first()
    if existing is not None:
        return existing
    enrollment = db.get(models.Enrollment, attempt.enrollment_id)
    if enrollment is None or attempt.channel_account_id is None:
        raise RouteReviewError("ROUTE_SOURCE_INCOMPLETE", "The reviewed send is missing its account or enrollment")
    evidence = db.query(models.EvidenceSnapshot).filter(
        models.EvidenceSnapshot.owner_id == attempt.owner_id,
        models.EvidenceSnapshot.archived_at.is_(None),
        or_(
            models.EvidenceSnapshot.contact_id == enrollment.contact_id,
            models.EvidenceSnapshot.company_id == enrollment.company_id,
        ),
    ).order_by(models.EvidenceSnapshot.confidence.desc(), models.EvidenceSnapshot.id.asc()).limit(20).all()
    evidence_ids = [item.id for item in evidence]
    confidence = max((Decimal(item.confidence) for item in evidence), default=Decimal("0"))
    scheduled_at = attempt.scheduled_at
    scheduled_iso = _iso(scheduled_at, require_timezone=False)
    state = {
        "enrollment_id": enrollment.id,
        "ai_model": "structured-route-v1",
        "ai_reason": "已根据客户证据、可用渠道和已渲染内容生成首个联系步骤。",
        "confidence": _confidence(confidence),
        "evidence_snapshot_ids": evidence_ids,
        "steps": [
            {
                "position": 1,
                "sequence_step_id": sequence_step.id,
                "attempt_id": attempt.id,
                "contact_point_id": attempt.contact_point_id,
                "channel_account_id": attempt.channel_account_id,
                "channel": attempt.channel.value,
                "scheduled_at": scheduled_iso,
                "subject": subject,
                "body": body,
                "ai_reason": "优先使用已验证且账号健康的当前渠道。",
                "confidence": _confidence(confidence),
                "evidence_snapshot_ids": evidence_ids,
            }
        ],
    }
    proposal = models.RouteProposal(
        owner_id=attempt.owner_id,
        enrollment_id=enrollment.id,
        contact_id=enrollment.contact_id,
        status=(
            RouteProposalStatus.DRAFT
            if evidence_ids and confidence >= MIN_ROUTE_CONFIDENCE
            else RouteProposalStatus.HUMAN_REVIEW_REQUIRED
        ),
        idempotency_key=f"attempt-route:{attempt.idempotency_key}",
        ai_model="structured-route-v1",
        ai_reason=state["ai_reason"],
        confidence=confidence,
        evidence_snapshot_ids=evidence_ids,
        checksum=_digest(state),
        proposed_at=utcnow(),
    )
    db.add(proposal)
    db.flush()
    db.add(
        models.RouteProposalStep(
            owner_id=attempt.owner_id,
            route_proposal_id=proposal.id,
            sequence_step_id=sequence_step.id,
            attempt_id=attempt.id,
            contact_point_id=attempt.contact_point_id,
            channel_account_id=attempt.channel_account_id,
            position=1,
            channel=attempt.channel,
            scheduled_at=datetime.fromisoformat(scheduled_iso),
            subject=subject,
            body=body,
            ai_reason=state["steps"][0]["ai_reason"],
            confidence=confidence,
            evidence_snapshot_ids=evidence_ids,
        )
    )
    db.flush()
    return proposal


def proposal_read(db: Session, proposal: models.RouteProposal) -> dict[str, Any]:
    steps = (
        db.query(models.RouteProposalStep)
        .filter_by(owner_id=proposal.owner_id, route_proposal_id=proposal.id)
        .order_by(models.RouteProposalStep.position.asc())
        .all()
    )
    return {**{column.name: getattr(proposal, column.name) for column in proposal.__table__.columns}, "steps": steps}


def _proposal_preview(db: Session, proposal: models.RouteProposal) -> dict[str, Any]:
    enrollment = _owned(db, models.Enrollment, proposal.enrollment_id, proposal.owner_id)
    contact = _owned(db, models.Contact, proposal.contact_id, proposal.owner_id)
    company = _owned(db, models.Company, enrollment.company_id, proposal.owner_id)
    stored_state = _stored_proposal_state(db, proposal)
    stored_steps = (
        db.query(models.RouteProposalStep)
        .filter_by(owner_id=proposal.owner_id, route_proposal_id=proposal.id)
        .order_by(models.RouteProposalStep.position.asc())
        .all()
    )
    steps = [
        {**step_payload, "id": step.id}
        for step_payload, step in zip(stored_state["steps"], stored_steps, strict=True)
    ]
    return {
        "route_proposal_id": proposal.id,
        "enrollment_id": proposal.enrollment_id,
        "company": {"id": company.id, "name": company.name, "domain": company.normalized_domain},
        "contact": {"id": contact.id, "name": contact.full_name, "job_title": contact.job_title},
        "ai_reason": proposal.ai_reason,
        "confidence": _confidence(proposal.confidence),
        "evidence_snapshot_ids": proposal.evidence_snapshot_ids or [],
        "steps": steps,
    }


def _batch_state(batch: models.ReviewBatch, items: list[models.ReviewBatchItem]) -> dict[str, Any]:
    return {
        "approval_id": batch.approval_id,
        "estimated_cost": f"{Decimal(batch.estimated_cost):.6f}",
        "price_version": batch.price_version,
        "items": [
            {
                "id": item.id,
                "route_proposal_id": item.route_proposal_id,
                "proposal_checksum": item.proposal_checksum,
                "preview_payload": item.preview_payload,
            }
            for item in items
        ],
    }


def batch_read(db: Session, batch: models.ReviewBatch) -> dict[str, Any]:
    items = (
        db.query(models.ReviewBatchItem)
        .filter_by(owner_id=batch.owner_id, review_batch_id=batch.id)
        .order_by(models.ReviewBatchItem.position.asc())
        .all()
    )
    return {**{column.name: getattr(batch, column.name) for column in batch.__table__.columns}, "items": items}


def preview_batch(
    db: Session,
    *,
    owner_id: int,
    actor_user_id: int,
    payload: ReviewBatchPreviewRequest,
) -> models.ReviewBatch:
    proposals = (
        db.query(models.RouteProposal)
        .filter(models.RouteProposal.owner_id == owner_id, models.RouteProposal.id.in_(payload.route_proposal_ids))
        .order_by(models.RouteProposal.id.asc())
        .all()
    )
    if len(proposals) != len(payload.route_proposal_ids):
        raise RouteReviewError("ROUTE_PROPOSAL_NOT_FOUND", "One or more route proposals were not found", status_code=404)
    if any(item.status not in {RouteProposalStatus.DRAFT, RouteProposalStatus.PREVIEWED} for item in proposals):
        raise RouteReviewError("ROUTE_PROPOSAL_NOT_PREVIEWABLE", "Every route must pass confidence and evidence gates before preview")

    if payload.batch_id is not None:
        batch = _owned(db, models.ReviewBatch, payload.batch_id, owner_id)
        if batch.status not in {ReviewBatchStatus.DRAFT, ReviewBatchStatus.PREVIEWED}:
            raise RouteReviewError("REVIEW_BATCH_FINAL", "A final batch cannot be previewed again")
        if {item.route_proposal_id for item in db.query(models.ReviewBatchItem).filter_by(review_batch_id=batch.id)} != set(payload.route_proposal_ids):
            raise RouteReviewError("REVIEW_BATCH_MEMBERSHIP_CHANGED", "Create a new batch to change its routes")
        batch.approval_id = payload.approval_id
        batch.estimated_cost = payload.estimated_cost
        batch.price_version = payload.price_version
        items = (
            db.query(models.ReviewBatchItem)
            .filter_by(owner_id=owner_id, review_batch_id=batch.id)
            .order_by(models.ReviewBatchItem.position.asc())
            .all()
        )
    else:
        existing = db.query(models.ReviewBatch).filter_by(owner_id=owner_id, idempotency_key=payload.idempotency_key).first()
        if existing is not None:
            raise RouteReviewError("IDEMPOTENCY_CONFLICT", "This batch idempotency key already exists")
        batch = models.ReviewBatch(
            owner_id=owner_id,
            status=ReviewBatchStatus.DRAFT,
            idempotency_key=payload.idempotency_key,
            approval_id=payload.approval_id,
            item_count=len(proposals),
            estimated_cost=payload.estimated_cost,
            price_version=payload.price_version,
        )
        db.add(batch)
        db.flush()
        items = []
        proposal_by_id = {item.id: item for item in proposals}
        for position, proposal_id in enumerate(payload.route_proposal_ids, start=1):
            proposal = proposal_by_id[proposal_id]
            item = models.ReviewBatchItem(
                owner_id=owner_id,
                review_batch_id=batch.id,
                route_proposal_id=proposal.id,
                position=position,
                proposal_checksum=proposal.checksum,
                preview_payload=_proposal_preview(db, proposal),
            )
            db.add(item)
            items.append(item)
        db.flush()
    for proposal in proposals:
        stored_checksum = _digest(_stored_proposal_state(db, proposal))
        if not hmac.compare_digest(stored_checksum, proposal.checksum):
            raise RouteReviewError("ROUTE_PROPOSAL_STALE", "A route changed after it was proposed")
        proposal.status = RouteProposalStatus.PREVIEWED
    batch.preview_checksum = _digest(_batch_state(batch, items))
    batch.status = ReviewBatchStatus.PREVIEWED
    batch.previewed_at = utcnow()
    add_audit(
        db,
        owner_id=owner_id,
        actor_user_id=actor_user_id,
        action="review_batch.previewed",
        entity_type="review_batch",
        entity_id=batch.id,
        after={"checksum": batch.preview_checksum, "item_count": batch.item_count},
    )
    db.commit()
    db.refresh(batch)
    return batch


def update_batch_item(
    db: Session,
    *,
    owner_id: int,
    actor_user_id: int,
    batch_id: int,
    item_id: int,
    payload: ReviewBatchItemUpdate,
) -> models.ReviewBatch:
    batch = _owned(db, models.ReviewBatch, batch_id, owner_id)
    if batch.status not in {ReviewBatchStatus.DRAFT, ReviewBatchStatus.PREVIEWED}:
        raise RouteReviewError("REVIEW_BATCH_FINAL", "A final batch cannot be edited")
    item = db.query(models.ReviewBatchItem).filter_by(id=item_id, owner_id=owner_id, review_batch_id=batch.id).first()
    if item is None:
        raise RouteReviewError("REVIEW_BATCH_ITEM_NOT_FOUND", "Batch item not found", status_code=404)
    preview = dict(item.preview_payload or {})
    steps = [dict(step) for step in preview.get("steps", [])]
    if len(steps) != 1:
        raise RouteReviewError("BATCH_ITEM_STEP_REQUIRED", "Choose a specific step before editing a multi-step route", status_code=422)
    changes = payload.model_dump(exclude_unset=True)
    if "scheduled_at" in changes:
        changes["scheduled_at"] = _iso(changes["scheduled_at"])
    steps[0].update(changes)
    preview["steps"] = steps
    item.preview_payload = preview
    item.edited = True
    batch.preview_checksum = None
    batch.status = ReviewBatchStatus.DRAFT
    add_audit(
        db,
        owner_id=owner_id,
        actor_user_id=actor_user_id,
        action="review_batch.item_edited",
        entity_type="review_batch_item",
        entity_id=item.id,
        after={"changed_fields": sorted(changes)},
    )
    db.commit()
    db.refresh(batch)
    return batch


def approve_batch(
    db: Session,
    *,
    owner_id: int,
    actor_user_id: int,
    batch_id: int,
    preview_checksum: str,
    approval_id: str,
    human_confirmed: bool,
) -> models.ReviewBatch:
    if not human_confirmed:
        raise RouteReviewError("HUMAN_CONFIRMATION_REQUIRED", "Batch approval requires explicit human confirmation", status_code=422)
    batch = (
        db.query(models.ReviewBatch)
        .filter_by(id=batch_id, owner_id=owner_id)
        .with_for_update()
        .first()
    )
    if batch is None:
        raise RouteReviewError("REVIEW_BATCH_NOT_FOUND", "Review batch not found", status_code=404)
    if batch.status == ReviewBatchStatus.APPROVED:
        return batch
    if batch.status != ReviewBatchStatus.PREVIEWED or not batch.preview_checksum:
        raise RouteReviewError("REVIEW_BATCH_REPREVIEW_REQUIRED", "The batch must be previewed again before approval")
    if approval_id != batch.approval_id:
        raise RouteReviewError("APPROVAL_ID_MISMATCH", "Approval ID does not match the reviewed batch")
    items = (
        db.query(models.ReviewBatchItem)
        .filter_by(owner_id=owner_id, review_batch_id=batch.id)
        .order_by(models.ReviewBatchItem.position.asc())
        .with_for_update()
        .all()
    )
    current_batch_checksum = _digest(_batch_state(batch, items))
    if not hmac.compare_digest(preview_checksum, batch.preview_checksum) or not hmac.compare_digest(current_batch_checksum, batch.preview_checksum):
        raise RouteReviewError("REVIEW_BATCH_STALE", "The batch changed after preview; preview it again")

    proposals = {
        proposal.id: proposal
        for proposal in db.query(models.RouteProposal)
        .filter(models.RouteProposal.owner_id == owner_id, models.RouteProposal.id.in_([item.route_proposal_id for item in items]))
        .with_for_update()
        .all()
    }
    now = utcnow()
    for item in items:
        proposal = proposals.get(item.route_proposal_id)
        if proposal is None or proposal.status != RouteProposalStatus.PREVIEWED:
            raise RouteReviewError("ROUTE_PROPOSAL_STALE", "A route is no longer approvable")
        stored_checksum = _digest(_stored_proposal_state(db, proposal))
        if not hmac.compare_digest(stored_checksum, item.proposal_checksum):
            raise RouteReviewError("ROUTE_PROPOSAL_STALE", "A route changed after preview")
        enrollment = (
            db.query(models.Enrollment)
            .filter_by(id=proposal.enrollment_id, owner_id=owner_id)
            .with_for_update()
            .first()
        )
        if enrollment is None:
            raise RouteReviewError("ROUTE_ENROLLMENT_MISSING", "Route enrollment no longer exists")
        if db.query(models.EnrollmentRouteStep.id).filter_by(enrollment_id=enrollment.id).first():
            raise RouteReviewError("ROUTE_ALREADY_APPROVED", "This contact already has an approved route")
        stored_steps = {
            step.id: step
            for step in db.query(models.RouteProposalStep)
            .filter_by(owner_id=owner_id, route_proposal_id=proposal.id)
            .with_for_update()
            .all()
        }
        for step_payload in item.preview_payload.get("steps", []):
            route_step_id = int(step_payload["id"])
            source_step = stored_steps.get(route_step_id)
            if source_step is None:
                raise RouteReviewError("ROUTE_PROPOSAL_STALE", "A route step disappeared after preview")
            _validate_step(
                db,
                owner_id=owner_id,
                enrollment=enrollment,
                step=step_payload,
                require_consent=True,
            )
            attempt = None
            if step_payload.get("attempt_id") is not None:
                attempt = (
                    db.query(models.OutreachAttempt)
                    .filter_by(id=int(step_payload["attempt_id"]), owner_id=owner_id)
                    .with_for_update()
                    .first()
                )
                if attempt is None or attempt.status not in {AttemptStatus.BLOCKED, AttemptStatus.QUEUED}:
                    raise RouteReviewError("ROUTE_ATTEMPT_STALE", "A send attempt changed after preview")
            db.add(
                models.EnrollmentRouteStep(
                    owner_id=owner_id,
                    enrollment_id=enrollment.id,
                    route_proposal_id=proposal.id,
                    route_proposal_step_id=source_step.id,
                    review_batch_id=batch.id,
                    attempt_id=attempt.id if attempt else None,
                    contact_point_id=int(step_payload["contact_point_id"]),
                    channel_account_id=int(step_payload["channel_account_id"]),
                    position=int(step_payload["position"]),
                    channel=Channel(step_payload["channel"]),
                    scheduled_at=datetime.fromisoformat(step_payload["scheduled_at"]),
                    subject=step_payload.get("subject"),
                    body=step_payload["body"],
                    status="approved",
                    approval_checksum=batch.preview_checksum,
                    approved_at=now,
                )
            )
            if attempt is not None:
                attempt.status = AttemptStatus.QUEUED
                attempt.scheduled_at = datetime.fromisoformat(step_payload["scheduled_at"])
                attempt.channel_account_id = int(step_payload["channel_account_id"])
                attempt.last_error = None
                task = (
                    db.query(models.Task)
                    .filter_by(owner_id=owner_id, attempt_id=attempt.id, task_type=TaskType.DRAFT_REVIEW)
                    .with_for_update()
                    .first()
                )
                if task is not None and task.status not in {TaskStatus.COMPLETED, TaskStatus.DISMISSED}:
                    task.status = TaskStatus.COMPLETED
                    task.completed_at = now
                    task.metadata_json = {
                        **(task.metadata_json or {}),
                        "review_batch_id": batch.id,
                        "review_decision": "approved",
                        "subject": step_payload.get("subject"),
                        "body": step_payload["body"],
                    }
        enrollment.status = EnrollmentStatus.ACTIVE
        enrollment.paused_reason = None
        enrollment.paused_at = None
        proposal.status = RouteProposalStatus.APPROVED
        proposal.approved_at = now

    batch.status = ReviewBatchStatus.APPROVED
    batch.approved_at = now
    batch.decided_by_user_id = actor_user_id
    add_audit(
        db,
        owner_id=owner_id,
        actor_user_id=actor_user_id,
        action="review_batch.approved",
        entity_type="review_batch",
        entity_id=batch.id,
        after={"checksum": batch.preview_checksum, "approval_id": batch.approval_id, "item_count": batch.item_count},
    )
    db.commit()
    db.refresh(batch)
    return batch


def reject_batch(
    db: Session,
    *,
    owner_id: int,
    actor_user_id: int,
    batch_id: int,
    reason: str,
) -> models.ReviewBatch:
    batch = _owned(db, models.ReviewBatch, batch_id, owner_id)
    if batch.status == ReviewBatchStatus.APPROVED:
        raise RouteReviewError("REVIEW_BATCH_ALREADY_APPROVED", "An approved batch cannot be rejected")
    if batch.status == ReviewBatchStatus.REJECTED:
        return batch
    proposal_ids = [item[0] for item in db.query(models.ReviewBatchItem.route_proposal_id).filter_by(review_batch_id=batch.id)]
    db.query(models.RouteProposal).filter(
        models.RouteProposal.owner_id == owner_id,
        models.RouteProposal.id.in_(proposal_ids),
        models.RouteProposal.status.in_((RouteProposalStatus.DRAFT, RouteProposalStatus.PREVIEWED)),
    ).update({"status": RouteProposalStatus.REJECTED, "rejected_at": utcnow()}, synchronize_session=False)
    batch.status = ReviewBatchStatus.REJECTED
    batch.rejected_at = utcnow()
    batch.decided_by_user_id = actor_user_id
    add_audit(
        db,
        owner_id=owner_id,
        actor_user_id=actor_user_id,
        action="review_batch.rejected",
        entity_type="review_batch",
        entity_id=batch.id,
        after={"reason": reason},
    )
    db.commit()
    db.refresh(batch)
    return batch
