"""Idempotent inbound/outbound Provider event ingestion for every channel."""
from __future__ import annotations

from sqlalchemy.orm import Session

from product_v2 import models
from product_v2.enums import (
    AttemptKind,
    AttemptStatus,
    ChannelAccountHealth,
    ContactPointAvailabilityStatus,
    ContactPointVerificationStatus,
    ConversationStatus,
    EnrollmentStatus,
    MessageDirection,
    MessageEventType,
    ProviderCostStatus,
    ReplyAssessmentStatus,
    ReplyIntent,
    RestrictionScope,
    TaskPriority,
    TaskStatus,
    TaskType,
    StageStatus,
)
from product_v2.runtime.reply_parser import UnsubscribeIntent, detect_unsubscribe_intent, extract_latest_reply
from product_v2.runtime.queue import (
    attempt_has_active_safety_locks,
    mark_attempt_unknown,
    release_attempt_safety_locks,
    set_stage_runtime,
)
from product_v2.services.domain import add_audit, utcnow
from product_v2.services.channel_accounts import create_account_safety_lock


def _owned(db: Session, model, entity_id: int, owner_id: int):
    row = db.query(model).filter(model.id == entity_id, model.owner_id == owner_id).first()
    if not row:
        raise ValueError(f"{model.__name__} not found for owner")
    return row


def _owned_attempt_for_update(db: Session, attempt_id: int, owner_id: int) -> models.OutreachAttempt:
    query = db.query(models.OutreachAttempt).filter(
        models.OutreachAttempt.id == attempt_id,
        models.OutreachAttempt.owner_id == owner_id,
    )
    if db.bind and db.bind.dialect.name == "mysql":
        query = query.with_for_update()
    row = query.populate_existing().first()
    if not row:
        raise ValueError("OutreachAttempt not found for owner")
    return row


def _has_unresolved_provider_boundary(
    db: Session,
    attempt: models.OutreachAttempt,
) -> bool:
    if attempt.status not in {
        AttemptStatus.CLAIMED,
        AttemptStatus.SENDING,
        AttemptStatus.UNKNOWN,
    }:
        return False
    unresolved_cost = db.query(models.ProviderCostEvent.id).filter(
        models.ProviderCostEvent.outreach_attempt_id == attempt.id,
        models.ProviderCostEvent.status.in_((ProviderCostStatus.RESERVED, ProviderCostStatus.UNKNOWN)),
    ).first()
    return bool(unresolved_cost) or attempt_has_active_safety_locks(db, attempt)


def _conversation_for_event(
    db: Session,
    *,
    owner_id: int,
    payload,
    company_id: int | None,
    contact_id: int | None,
    contact_point_id: int | None,
) -> models.Conversation | None:
    if payload.conversation_id:
        return _owned(db, models.Conversation, payload.conversation_id, owner_id)
    if (
        payload.direction != MessageDirection.INBOUND
        and payload.event_type
        not in {MessageEventType.UNSUBSCRIBED, MessageEventType.COMPLAINED}
    ):
        return None
    if not company_id or not contact_id:
        raise ValueError("Inbound events require a resolvable company and contact")
    thread_id = str(payload.metadata_json.get("thread_id") or "").strip() or None
    query = db.query(models.Conversation).filter(
        models.Conversation.owner_id == owner_id,
        models.Conversation.contact_id == contact_id,
        models.Conversation.channel == payload.channel,
        models.Conversation.archived_at.is_(None),
    )
    conversation = query.filter(models.Conversation.provider_thread_id == thread_id).first() if thread_id else query.order_by(models.Conversation.last_message_at.desc()).first()
    if conversation:
        return conversation
    conversation = models.Conversation(
        owner_id=owner_id,
        company_id=company_id,
        contact_id=contact_id,
        contact_point_id=contact_point_id,
        channel=payload.channel,
        status=ConversationStatus.OPEN,
        provider_thread_id=thread_id,
        subject=payload.subject,
    )
    db.add(conversation)
    db.flush()
    return conversation


def _apply_unsubscribe(
    db: Session,
    *,
    owner_id: int,
    event: models.MessageEvent,
    conversation: models.Conversation,
    intent,
) -> None:
    if not intent.is_unsubscribe:
        return
    if intent.scope == RestrictionScope.COMPANY:
        existing = db.query(models.Task).filter_by(
            owner_id=owner_id,
            conversation_id=conversation.id,
            task_type=TaskType.REPLY_TRIAGE,
            status=TaskStatus.OPEN,
        ).first()
        if not existing:
            db.add(
                models.Task(
                    owner_id=owner_id,
                    task_type=TaskType.REPLY_TRIAGE,
                    status=TaskStatus.OPEN,
                    priority=TaskPriority.URGENT,
                    company_id=conversation.company_id,
                    contact_id=conversation.contact_id,
                    conversation_id=conversation.id,
                    title="Confirm company-wide contact restriction",
                    description=event.latest_body,
                    metadata_json={"restriction_scope": "company", "requires_human_confirmation": True},
                )
            )
        return
    contact_point_id = conversation.contact_point_id if intent.scope == RestrictionScope.CONTACT_POINT else None
    contact_id = conversation.contact_id if intent.scope == RestrictionScope.CONTACT else None
    if intent.scope == RestrictionScope.CONTACT_POINT and not contact_point_id:
        return
    key = f"message-event:{event.id}:consent"
    if db.query(models.ConsentRestriction).filter_by(idempotency_key=key).first():
        return
    db.add(
        models.ConsentRestriction(
            owner_id=owner_id,
            idempotency_key=key,
            scope=intent.scope,
            channel=event.channel if intent.scope == RestrictionScope.CONTACT_POINT else None,
            contact_point_id=contact_point_id,
            contact_id=contact_id,
            reason=(
                "provider_complaint"
                if event.event_type == MessageEventType.COMPLAINED
                else "unsubscribe_request"
            ),
            source="provider_message_event",
            metadata_json={
                "message_event_id": event.id,
                "matched_phrase": intent.matched_phrase,
                "complaint": event.event_type == MessageEventType.COMPLAINED,
            },
        )
    )


def _apply_complaint_hard_stop(
    db: Session,
    *,
    owner_id: int,
    event: models.MessageEvent,
    attempt: models.OutreachAttempt | None,
    enrollment: models.Enrollment | None,
    point: models.ContactPoint,
    company_id: int | None,
    contact_id: int | None,
    provider: str,
    idempotency_key: str,
) -> None:
    """Disable the reported recipient and sender account until human review."""

    point.availability_status = ContactPointAvailabilityStatus.UNAVAILABLE
    if enrollment and enrollment.status in {
        EnrollmentStatus.SCHEDULED,
        EnrollmentStatus.ACTIVE,
    }:
        enrollment.status = EnrollmentStatus.BLOCKED
        enrollment.paused_reason = "provider_complaint_received"
        enrollment.paused_at = utcnow()

    account = None
    safety_lock = None
    account_id = attempt.channel_account_id if attempt else None
    if account_id is not None:
        account = db.query(models.ChannelAccount).filter(
            models.ChannelAccount.id == account_id,
            models.ChannelAccount.owner_id == owner_id,
        ).first()
    if account is not None:
        account.health_status = ChannelAccountHealth.UNHEALTHY
        account.health_checked_at = utcnow()
        account.last_error = "provider_complaint_requires_review"
        safety_lock = create_account_safety_lock(
            db,
            account=account,
            reason="Provider abuse complaint requires deliverability review",
            code=f"provider_complaint:{event.id}",
        )

    task = models.Task(
        owner_id=owner_id,
        task_type=TaskType.DELIVERABILITY_ALERT,
        status=TaskStatus.OPEN,
        priority=TaskPriority.URGENT,
        company_id=company_id,
        contact_id=contact_id,
        campaign_id=attempt.campaign_id if attempt else None,
        enrollment_id=enrollment.id if enrollment else None,
        attempt_id=attempt.id if attempt else None,
        title="Provider complaint stopped the sender account",
        description=(
            "Investigate the abuse complaint, verify suppression and sender reputation, "
            "then release the safety lock with a remediation evidence ID."
        ),
        metadata_json={
            "message_event_id": event.id,
            "contact_point_id": point.id,
            "channel_account_id": account.id if account else None,
            "safety_lock_id": safety_lock.id if safety_lock else None,
            "feedback_type": (event.metadata_json or {}).get("feedback_type"),
            "auto_send_allowed": False,
        },
    )
    db.add(task)
    add_audit(
        db,
        owner_id=owner_id,
        actor_user_id=None,
        action="contact_point.complained",
        entity_type="contact_point",
        entity_id=point.id,
        after={
            "availability_status": ContactPointAvailabilityStatus.UNAVAILABLE.value,
            "channel_account_id": account.id if account else None,
            "safety_lock_id": safety_lock.id if safety_lock else None,
            "auto_send_allowed": False,
            "provider": provider,
        },
        correlation_id=idempotency_key,
    )


def _complete_reconciliation_tasks(
    db: Session,
    *,
    attempt: models.OutreachAttempt,
    event: models.MessageEvent,
    resolution: str,
) -> int:
    current = utcnow()
    completed = 0
    for task in db.query(models.Task).filter(
        models.Task.owner_id == attempt.owner_id,
        models.Task.attempt_id == attempt.id,
        models.Task.task_type == TaskType.RECONCILIATION,
        models.Task.status != TaskStatus.COMPLETED,
    ).all():
        task.status = TaskStatus.COMPLETED
        task.completed_at = current
        task.metadata_json = {
            **(task.metadata_json or {}),
            "requires_reconciliation": False,
            "resolution": resolution,
            "resolution_message_event_id": event.id,
        }
        completed += 1
    return completed


def _resolve_unknown_as_sent(
    db: Session,
    *,
    attempt: models.OutreachAttempt,
    enrollment: models.Enrollment,
    point: models.ContactPoint,
    event: models.MessageEvent,
    provider: str,
    idempotency_key: str,
) -> None:
    """Apply an authoritative SENT/DELIVERED event to an UNKNOWN attempt."""

    previous_status = attempt.status
    sent_at = event.occurred_at or utcnow()
    attempt.status = AttemptStatus.SUCCEEDED
    attempt.sent_at = sent_at
    attempt.provider = provider
    attempt.provider_message_id = event.provider_message_id or attempt.provider_message_id
    attempt.last_error = None
    attempt.claimed_by = None
    attempt.lease_expires_at = None
    if attempt.kind == AttemptKind.COLD:
        point.last_cold_outreach_at = sent_at
        company = db.get(models.Company, enrollment.company_id)
        if company:
            company.last_cold_outreach_at = sent_at

    reconciled_costs = 0
    for cost in db.query(models.ProviderCostEvent).filter(
        models.ProviderCostEvent.outreach_attempt_id == attempt.id,
        models.ProviderCostEvent.status.in_((ProviderCostStatus.UNKNOWN, ProviderCostStatus.RESERVED)),
    ).all():
        cost.status = ProviderCostStatus.CHARGED
        cost.provider = provider
        cost.result_count = max(cost.result_count or 0, 1)
        cost.metadata_json = {
            **(cost.metadata_json or {}),
            "requires_reconciliation": False,
            "reconciled_by_message_event_id": event.id,
            "reconciliation_result": "sent",
        }
        reconciled_costs += 1
    completed_tasks = _complete_reconciliation_tasks(
        db,
        attempt=attempt,
        event=event,
        resolution="sent",
    )
    released_locks = release_attempt_safety_locks(
        db,
        attempt,
        reason=f"authoritative_{event.event_type.value}_event",
    )
    set_stage_runtime(
        db,
        owner_id=attempt.owner_id,
        campaign_id=attempt.campaign_id,
        stage_name="outbound",
        status=StageStatus.IDLE,
        details={"attempt_id": attempt.id, "reconciled_by_message_event_id": event.id},
    )
    add_audit(
        db,
        owner_id=attempt.owner_id,
        actor_user_id=None,
        action=(
            "outreach_attempt.unknown_reconciled_succeeded"
            if previous_status == AttemptStatus.UNKNOWN
            else "outreach_attempt.provider_boundary_reconciled_succeeded"
        ),
        entity_type="outreach_attempt",
        entity_id=attempt.id,
        after={
            "message_event_id": event.id,
            "event_type": event.event_type.value,
            "previous_status": previous_status.value,
            "sent_at": sent_at.isoformat(),
            "reconciled_costs": reconciled_costs,
            "completed_reconciliation_tasks": completed_tasks,
            "released_safety_locks": released_locks,
        },
        correlation_id=idempotency_key,
    )


def _resolve_unknown_as_not_sent(
    db: Session,
    *,
    attempt: models.OutreachAttempt,
    event: models.MessageEvent,
    provider: str,
    idempotency_key: str,
) -> None:
    """Resolve UNKNOWN only from explicit Provider proof that no send occurred."""

    previous_status = attempt.status
    attempt.status = AttemptStatus.FAILED
    attempt.last_error = f"provider_confirmed_not_sent:{event.event_type.value}"
    attempt.claimed_by = None
    attempt.lease_expires_at = None
    reconciled_costs = 0
    for cost in db.query(models.ProviderCostEvent).filter(
        models.ProviderCostEvent.outreach_attempt_id == attempt.id,
        models.ProviderCostEvent.status.in_((ProviderCostStatus.UNKNOWN, ProviderCostStatus.RESERVED)),
    ).all():
        cost.status = ProviderCostStatus.FAILED
        cost.provider = provider
        cost.metadata_json = {
            **(cost.metadata_json or {}),
            "requires_reconciliation": False,
            "reconciled_by_message_event_id": event.id,
            "reconciliation_result": "confirmed_not_sent",
        }
        reconciled_costs += 1
    completed_tasks = _complete_reconciliation_tasks(
        db,
        attempt=attempt,
        event=event,
        resolution="confirmed_not_sent",
    )
    released_locks = release_attempt_safety_locks(
        db,
        attempt,
        reason="provider_confirmed_not_sent",
    )
    set_stage_runtime(
        db,
        owner_id=attempt.owner_id,
        campaign_id=attempt.campaign_id,
        stage_name="outbound",
        status=StageStatus.FAILED,
        reason=attempt.last_error,
        details={"attempt_id": attempt.id, "reconciled_by_message_event_id": event.id},
    )
    add_audit(
        db,
        owner_id=attempt.owner_id,
        actor_user_id=None,
        action=(
            "outreach_attempt.unknown_reconciled_not_sent"
            if previous_status == AttemptStatus.UNKNOWN
            else "outreach_attempt.provider_boundary_reconciled_not_sent"
        ),
        entity_type="outreach_attempt",
        entity_id=attempt.id,
        after={
            "message_event_id": event.id,
            "event_type": event.event_type.value,
            "previous_status": previous_status.value,
            "reconciled_costs": reconciled_costs,
            "completed_reconciliation_tasks": completed_tasks,
            "released_safety_locks": released_locks,
        },
        correlation_id=idempotency_key,
    )


def ingest_provider_event(
    db: Session,
    *,
    owner_id: int,
    provider: str,
    idempotency_key: str,
    payload,
) -> models.MessageEvent:
    provider_event_id = payload.provider_event_id or idempotency_key
    existing_by_key = db.query(models.MessageEvent).filter_by(
        owner_id=owner_id,
        ingest_idempotency_key=idempotency_key,
    ).first()
    if existing_by_key:
        same_request = all(
            (
                existing_by_key.provider == provider,
                existing_by_key.provider_event_id == provider_event_id,
                existing_by_key.channel == payload.channel,
                existing_by_key.direction == payload.direction,
                existing_by_key.event_type == payload.event_type,
                existing_by_key.provider_message_id == payload.provider_message_id,
            )
        )
        if not same_request:
            raise ValueError("Idempotency-Key is already bound to a different Provider event")
        return existing_by_key
    existing = db.query(models.MessageEvent).filter_by(
        owner_id=owner_id,
        provider=provider,
        provider_event_id=provider_event_id,
    ).first()
    if existing:
        return existing

    attempt = None
    if payload.attempt_id:
        attempt = _owned_attempt_for_update(db, payload.attempt_id, owner_id)
    elif payload.provider_message_id:
        attempt_query = db.query(models.OutreachAttempt).filter_by(
            owner_id=owner_id,
            provider_message_id=payload.provider_message_id,
        )
        if db.bind and db.bind.dialect.name == "mysql":
            attempt_query = attempt_query.with_for_update()
        attempt = attempt_query.populate_existing().first()
    enrollment = db.get(models.Enrollment, attempt.enrollment_id) if attempt else None
    if attempt and (not enrollment or enrollment.owner_id != owner_id):
        raise ValueError("Attempt enrollment does not belong to owner")
    company_id = enrollment.company_id if enrollment else payload.company_id
    contact_id = enrollment.contact_id if enrollment else payload.contact_id
    contact_point_id = attempt.contact_point_id if attempt else payload.contact_point_id
    if company_id:
        _owned(db, models.Company, company_id, owner_id)
    if contact_id:
        _owned(db, models.Contact, contact_id, owner_id)
    point = _owned(db, models.ContactPoint, contact_point_id, owner_id) if contact_point_id else None
    if attempt and payload.channel != attempt.channel:
        raise ValueError("Webhook channel does not match outreach attempt")
    if point:
        if point.channel != payload.channel:
            raise ValueError("Webhook channel does not match contact point")
        if contact_id and point.contact_id != contact_id:
            raise ValueError("Webhook contact point does not match contact")
        if company_id and point.company_id != company_id:
            raise ValueError("Webhook contact point does not match company")
    if payload.event_type in {
        MessageEventType.UNSUBSCRIBED,
        MessageEventType.COMPLAINED,
    } and not point:
        raise ValueError("Suppression event requires a resolvable contact point")
    # Provider-specific event types that we cannot interpret are retained as
    # immutable UNKNOWN evidence, but they must not mutate conversations,
    # consent, deliverability, or Attempt state without human reconciliation.
    is_unknown_event = payload.event_type == MessageEventType.UNKNOWN
    conversation = None if is_unknown_event else _conversation_for_event(
        db,
        owner_id=owner_id,
        payload=payload,
        company_id=company_id,
        contact_id=contact_id,
        contact_point_id=contact_point_id,
    )
    is_inbound_signal = not is_unknown_event and (
        payload.direction == MessageDirection.INBOUND
        or payload.event_type
        in {MessageEventType.UNSUBSCRIBED, MessageEventType.COMPLAINED}
    )
    latest_body = extract_latest_reply(payload.body or "") if is_inbound_signal else None
    event = models.MessageEvent(
        owner_id=owner_id,
        conversation_id=conversation.id if conversation else None,
        outreach_attempt_id=attempt.id if attempt else None,
        channel=payload.channel,
        direction=payload.direction,
        event_type=payload.event_type,
        provider=provider,
        ingest_idempotency_key=idempotency_key,
        provider_event_id=provider_event_id,
        provider_message_id=payload.provider_message_id,
        subject=payload.subject,
        body=payload.body,
        latest_body=latest_body,
        occurred_at=payload.occurred_at or utcnow(),
        metadata_json=payload.metadata_json,
    )
    db.add(event)
    db.flush()

    if attempt:
        was_unknown = attempt.status == AttemptStatus.UNKNOWN
        unresolved_provider_boundary = _has_unresolved_provider_boundary(db, attempt)
        if payload.event_type in {MessageEventType.SENT, MessageEventType.DELIVERED}:
            if was_unknown or unresolved_provider_boundary:
                _resolve_unknown_as_sent(
                    db,
                    attempt=attempt,
                    enrollment=enrollment,
                    point=point,
                    event=event,
                    provider=provider,
                    idempotency_key=idempotency_key,
                )
            else:
                attempt.status = AttemptStatus.SUCCEEDED
        elif payload.event_type in {MessageEventType.FAILED, MessageEventType.BOUNCED}:
            if was_unknown or unresolved_provider_boundary:
                # FAILED/BOUNCED alone does not prove a timeout was not accepted.
                # The Provider adapter must explicitly normalize authoritative
                # no-send evidence to the boolean flag below.
                if payload.metadata_json.get("confirmed_not_sent") is True:
                    _resolve_unknown_as_not_sent(
                        db,
                        attempt=attempt,
                        event=event,
                        provider=provider,
                        idempotency_key=idempotency_key,
                    )
                else:
                    mark_attempt_unknown(
                        db,
                        attempt,
                        attempt.unknown_reason or f"provider_event_ambiguous:{payload.event_type.value}",
                    )
                    add_audit(
                        db,
                        owner_id=attempt.owner_id,
                        actor_user_id=None,
                        action="outreach_attempt.unknown_resolution_deferred",
                        entity_type="outreach_attempt",
                        entity_id=attempt.id,
                        after={
                            "message_event_id": event.id,
                            "event_type": payload.event_type.value,
                            "confirmed_not_sent": False,
                        },
                        correlation_id=idempotency_key,
                    )
            else:
                attempt.status = AttemptStatus.FAILED
                attempt.last_error = f"provider_event:{payload.event_type.value}"

    if payload.event_type == MessageEventType.BOUNCED and point:
        point.verification_status = ContactPointVerificationStatus.INVALID
        point.availability_status = ContactPointAvailabilityStatus.UNAVAILABLE
        if enrollment and enrollment.status in {EnrollmentStatus.SCHEDULED, EnrollmentStatus.ACTIVE}:
            enrollment.status = EnrollmentStatus.BLOCKED
            enrollment.paused_reason = "contact_point_bounced"
            enrollment.paused_at = utcnow()
        if not db.query(models.Task).filter_by(
            owner_id=owner_id,
            contact_id=contact_id,
            attempt_id=attempt.id if attempt else None,
            task_type=TaskType.DELIVERABILITY_ALERT,
            status=TaskStatus.OPEN,
        ).first():
            db.add(
                models.Task(
                    owner_id=owner_id,
                    task_type=TaskType.DELIVERABILITY_ALERT,
                    status=TaskStatus.OPEN,
                    priority=TaskPriority.URGENT,
                    company_id=company_id,
                    contact_id=contact_id,
                    campaign_id=attempt.campaign_id if attempt else None,
                    enrollment_id=enrollment.id if enrollment else None,
                    attempt_id=attempt.id if attempt else None,
                    title="Contact point bounced and was disabled",
                    description=f"Provider {provider} reported a bounce for {point.value}",
                    metadata_json={"message_event_id": event.id, "contact_point_id": point.id},
                )
            )
        add_audit(
            db,
            owner_id=owner_id,
            actor_user_id=None,
            action="contact_point.bounced",
            entity_type="contact_point",
            entity_id=point.id,
            after={"verification_status": "invalid", "availability_status": "unavailable"},
            correlation_id=idempotency_key,
        )

    if payload.event_type == MessageEventType.COMPLAINED and point:
        _apply_complaint_hard_stop(
            db,
            owner_id=owner_id,
            event=event,
            attempt=attempt,
            enrollment=enrollment,
            point=point,
            company_id=company_id,
            contact_id=contact_id,
            provider=provider,
            idempotency_key=idempotency_key,
        )

    if conversation and is_inbound_signal:
        conversation.latest_reply_body = latest_body
        conversation.last_message_at = event.occurred_at
        if enrollment and enrollment.status in {EnrollmentStatus.SCHEDULED, EnrollmentStatus.ACTIVE}:
            enrollment.status = EnrollmentStatus.PAUSED
            enrollment.paused_reason = "reply_received"
            enrollment.paused_at = utcnow()
        unsubscribe = detect_unsubscribe_intent(latest_body or "", payload.subject or "")
        if payload.event_type in {
            MessageEventType.UNSUBSCRIBED,
            MessageEventType.COMPLAINED,
        } and not unsubscribe.is_unsubscribe:
            unsubscribe = UnsubscribeIntent(
                is_unsubscribe=True,
                scope=RestrictionScope.CONTACT_POINT,
                matched_phrase=f"provider_event:{payload.event_type.value}",
            )
        conversation.status = ConversationStatus.CLOSED if unsubscribe.is_unsubscribe else ConversationStatus.WAITING_ON_US
        assessment = models.ReplyAssessment(
            owner_id=owner_id,
            conversation_id=conversation.id,
            message_event_id=event.id,
            enrollment_id=attempt.enrollment_id if attempt else None,
            intent=ReplyIntent.UNSUBSCRIBE if unsubscribe.is_unsubscribe else ReplyIntent.OTHER,
            is_positive=False,
            status=ReplyAssessmentStatus.PROPOSED,
            latest_reply_body=latest_body or "",
            rationale=(
                f"Provider reported {payload.event_type.value}"
                if payload.event_type
                in {MessageEventType.UNSUBSCRIBED, MessageEventType.COMPLAINED}
                else (
                    "Provider reported unsubscribe"
                    if unsubscribe.is_unsubscribe
                    else "Awaiting AI proposal or human triage"
                )
            ),
            assessed_by=(
                "provider_event"
                if payload.event_type
                in {MessageEventType.UNSUBSCRIBED, MessageEventType.COMPLAINED}
                else "pending"
            ),
        )
        db.add(assessment)
        _apply_unsubscribe(
            db,
            owner_id=owner_id,
            event=event,
            conversation=conversation,
            intent=unsubscribe,
        )
        if payload.event_type != MessageEventType.COMPLAINED and not db.query(
            models.Task
        ).filter_by(
            owner_id=owner_id,
            conversation_id=conversation.id,
            task_type=TaskType.REPLY_TRIAGE,
            status=TaskStatus.OPEN,
        ).first():
            db.add(
                models.Task(
                    owner_id=owner_id,
                    task_type=TaskType.REPLY_TRIAGE,
                    status=TaskStatus.OPEN,
                    priority=TaskPriority.HIGH,
                    company_id=conversation.company_id,
                    contact_id=conversation.contact_id,
                    enrollment_id=attempt.enrollment_id if attempt else None,
                    conversation_id=conversation.id,
                    attempt_id=attempt.id if attempt else None,
                    title="Review latest inbound reply",
                    description=latest_body or "Provider reported an unsubscribe event",
                    metadata_json={"message_event_id": event.id},
                )
            )
        if attempt:
            set_stage_runtime(
                db,
                owner_id=owner_id,
                campaign_id=attempt.campaign_id,
                stage_name="inbox",
                status=StageStatus.IDLE,
                details={"message_event_id": event.id, "conversation_id": conversation.id},
            )
    if is_unknown_event:
        task = models.Task(
            owner_id=owner_id,
            task_type=TaskType.RECONCILIATION,
            status=TaskStatus.OPEN,
            priority=TaskPriority.HIGH,
            company_id=company_id,
            contact_id=contact_id,
            campaign_id=attempt.campaign_id if attempt else None,
            enrollment_id=enrollment.id if enrollment else None,
            attempt_id=attempt.id if attempt else None,
            title="Reconcile unknown Provider webhook event",
            description=(
                f"Provider {provider} delivered an authenticated event type "
                "that Product V2 cannot interpret automatically."
            ),
            metadata_json={
                "message_event_id": event.id,
                "provider": provider,
                "provider_event_id": event.provider_event_id,
                "provider_event_type": (event.metadata_json or {}).get("provider_event_type", "unknown"),
                "webhook_verification": (event.metadata_json or {}).get("webhook_verification", {}),
            },
        )
        db.add(task)
        db.flush()
        add_audit(
            db,
            owner_id=owner_id,
            actor_user_id=None,
            action="message_event.unknown_reconciliation_requested",
            entity_type="message_event",
            entity_id=event.id,
            after={
                "provider": provider,
                "event_type": MessageEventType.UNKNOWN.value,
                "reconciliation_task_id": task.id,
                "body_sha256": ((event.metadata_json or {}).get("webhook_verification") or {}).get("body_sha256"),
            },
            correlation_id=idempotency_key,
        )

    add_audit(
        db,
        owner_id=owner_id,
        actor_user_id=None,
        action="message_event.ingested",
        entity_type="message_event",
        entity_id=event.id,
        after={"provider": provider, "event_type": payload.event_type.value},
        correlation_id=idempotency_key,
    )
    return event
