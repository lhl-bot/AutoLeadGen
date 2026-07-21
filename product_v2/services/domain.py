"""Transactional Product V2 business rules.

This module contains policy, not network I/O.  HTTP routes and workers both use
the same functions so manual actions cannot bypass automation safety gates.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
import hashlib
import ipaddress
import json
import re
from typing import Any, Optional
from urllib.parse import urlparse

from sqlalchemy import and_, func, or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

import models as legacy
from product_v2 import models
from product_v2.enums import (
    AttemptStatus,
    CampaignLifecycle,
    CampaignRevisionStatus,
    Channel,
    ContactPointAvailabilityStatus,
    ContactPointVerificationStatus,
    EnrollmentStatus,
    JobStatus,
    OpportunityStage,
    OverrideGate,
    POSITIVE_REPLY_INTENTS,
    ProviderCostStatus,
    ReplyAssessmentStatus,
    ReplyIntent,
    RestrictionScope,
    StageStatus,
    TaskPriority,
    TaskStatus,
    TaskType,
    WorkerType,
)
from product_v2.schemas import CampaignReadiness, ReadinessCheck
from product_v2.settings_policy import (
    channel_policy_allows,
    configured_public_unsubscribe_url,
    global_budget_snapshot,
    review_policy_required,
    revision_unit_price,
)
from product_v2.services.channel_accounts import (
    ensure_fake_channel_account,
    evaluate_channel_account,
    fake_account_fallback_allowed,
    validate_sequence_account_reference,
)
from services.suppression import find_exact_email_suppression
from services.research_quality import PERSONAL_EMAIL_DOMAINS


UTC = timezone.utc
ACTIVE_ENROLLMENT_STATUSES = (EnrollmentStatus.SCHEDULED, EnrollmentStatus.ACTIVE)
CAMPAIGN_COMMAND_LIFECYCLES = {
    "start": frozenset((CampaignLifecycle.READY, CampaignLifecycle.PAUSED)),
    "pause": frozenset((CampaignLifecycle.RUNNING,)),
    "complete": frozenset((CampaignLifecycle.RUNNING, CampaignLifecycle.PAUSED)),
}
_DOMAIN_LABEL = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")
_PLACEHOLDER_DOMAIN_INPUTS = {
    "-",
    "--",
    "example",
    "example.com",
    "example.net",
    "example.org",
    "invalid",
    "localhost",
    "n/a",
    "na",
    "none",
    "null",
    "placeholder",
    "test",
    "test.com",
    "unknown",
}


def utcnow() -> datetime:
    return datetime.now(UTC)


def as_utc(value: Optional[datetime]) -> Optional[datetime]:
    if value is None:
        return None
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def validate_campaign_command(campaign: models.Campaign, action: str) -> None:
    allowed = CAMPAIGN_COMMAND_LIFECYCLES.get(action)
    if allowed is None:
        raise ValueError(f"Unsupported Campaign command: {action}")
    if campaign.archived_at is not None or campaign.lifecycle == CampaignLifecycle.ARCHIVED:
        raise ValueError("Archived Campaigns cannot accept lifecycle commands")
    if campaign.lifecycle not in allowed:
        expected = ", ".join(sorted(item.value for item in allowed))
        raise ValueError(
            f"Campaign cannot {action} from {campaign.lifecycle.value}; expected one of: {expected}"
        )


def normalize_domain(value: Optional[str]) -> Optional[str]:
    raw = (value or "").strip().lower()
    if not raw:
        return None
    if "://" in raw:
        raw = urlparse(raw).netloc
    if "@" in raw:
        raw = raw.rsplit("@", 1)[-1]
    raw = raw.split("/", 1)[0].split(":", 1)[0].strip(".")
    if raw.startswith("www."):
        raw = raw[4:]
    return raw or None


def is_usable_company_domain(value: Optional[str]) -> bool:
    """Return whether a normalized value can safely identify a Company.

    This intentionally accepts reserved ``*.example`` domains because they are
    used by the isolated Product V2 fixtures, while rejecting explicit
    placeholders, malformed hostnames, IP addresses and the reserved
    ``.invalid`` namespace.
    """

    raw = (value or "").strip().lower()
    if raw in _PLACEHOLDER_DOMAIN_INPUTS:
        return False
    domain = normalize_domain(raw)
    if not domain or domain in _PLACEHOLDER_DOMAIN_INPUTS:
        return False
    if domain in PERSONAL_EMAIL_DOMAINS:
        return False
    if len(domain) > 253 or "." not in domain or domain.endswith(".invalid"):
        return False
    try:
        ipaddress.ip_address(domain)
        return False
    except ValueError:
        pass
    try:
        ascii_domain = domain.encode("idna").decode("ascii")
    except UnicodeError:
        return False
    labels = ascii_domain.split(".")
    return all(_DOMAIN_LABEL.fullmatch(label) is not None for label in labels)


def normalize_contact_point(channel: Channel, value: str) -> str:
    normalized = value.strip()
    if channel == Channel.EMAIL:
        return normalized.lower()
    if channel == Channel.WHATSAPP:
        return "".join(char for char in normalized if char.isdigit() or char == "+")
    if channel == Channel.LINKEDIN:
        return normalized.rstrip("/").lower()
    return normalized


def owner_filter(query, model, owner_id: int):
    return query.filter(model.owner_id == owner_id)


def add_audit(
    db: Session,
    *,
    owner_id: int,
    actor_user_id: Optional[int],
    action: str,
    entity_type: str,
    entity_id: Any,
    before: Optional[dict[str, Any]] = None,
    after: Optional[dict[str, Any]] = None,
    metadata: Optional[dict[str, Any]] = None,
    correlation_id: Optional[str] = None,
) -> models.AuditEvent:
    event = models.AuditEvent(
        owner_id=owner_id,
        actor_user_id=actor_user_id,
        action=action,
        entity_type=entity_type,
        entity_id=str(entity_id),
        correlation_id=correlation_id,
        before_data=before,
        after_data=after,
        metadata_json=metadata,
    )
    db.add(event)
    return event


def enqueue_job(
    db: Session,
    *,
    owner_id: int,
    job_type: str,
    idempotency_key: str,
    queue: str = "default",
    payload: Optional[dict[str, Any]] = None,
    priority: int = 100,
    campaign_id: Optional[int] = None,
    enrollment_id: Optional[int] = None,
    attempt_id: Optional[int] = None,
    scheduled_at: Optional[datetime] = None,
) -> models.AutomationJob:
    def validate_replay(existing: models.AutomationJob) -> models.AutomationJob:
        same_request = all(
            (
                existing.owner_id == owner_id,
                existing.job_type == job_type,
                existing.queue == queue,
                existing.campaign_id == campaign_id,
                existing.enrollment_id == enrollment_id,
                existing.attempt_id == attempt_id,
                (existing.payload or {}) == (payload or {}),
            )
        )
        if not same_request:
            raise ValueError("Idempotency-Key is already used by a different command payload")
        return existing

    existing = db.query(models.AutomationJob).filter_by(idempotency_key=idempotency_key).first()
    if existing:
        return validate_replay(existing)
    job = models.AutomationJob(
        owner_id=owner_id,
        campaign_id=campaign_id,
        enrollment_id=enrollment_id,
        attempt_id=attempt_id,
        status=JobStatus.PENDING,
        job_type=job_type,
        queue=queue,
        payload=payload or {},
        idempotency_key=idempotency_key,
        priority=priority,
        # MySQL DATETIME without fractional precision rounds microseconds.
        # Floor immediate jobs so a just-committed row cannot appear up to one
        # second in the future and be missed by the first worker poll.
        scheduled_at=scheduled_at if scheduled_at is not None else utcnow().replace(microsecond=0),
    )
    try:
        with db.begin_nested():
            db.add(job)
            db.flush()
        return job
    except IntegrityError:
        existing = db.query(models.AutomationJob).filter_by(idempotency_key=idempotency_key).first()
        if not existing:
            raise
        return validate_replay(existing)


def create_campaign_revision(
    db: Session,
    *,
    campaign: models.Campaign,
    actor_user_id: int,
    data,
) -> models.CampaignRevision:
    revision_number = (
        db.query(func.coalesce(func.max(models.CampaignRevision.revision_number), 0))
        .filter(models.CampaignRevision.campaign_id == campaign.id)
        .scalar()
        + 1
    )
    revision = models.CampaignRevision(
        owner_id=campaign.owner_id,
        campaign_id=campaign.id,
        revision_number=revision_number,
        status=CampaignRevisionStatus.DRAFT,
        icp_definition=data.icp_definition,
        audience_definition=data.audience_definition,
        quality_gates=data.quality_gates.model_dump(),
        budget_definition=data.budget_definition,
        stop_conditions=data.stop_conditions,
    )
    db.add(revision)
    db.flush()
    for step in data.sequence_steps:
        channel_account_id = step.channel_account_id
        if channel_account_id is None and fake_account_fallback_allowed():
            channel_account_id = ensure_fake_channel_account(
                db,
                owner_id=campaign.owner_id,
                channel=step.channel,
            ).id
        if channel_account_id is not None:
            validate_sequence_account_reference(
                db,
                owner_id=campaign.owner_id,
                channel=step.channel,
                channel_account_id=channel_account_id,
            )
        db.add(
            models.SequenceStep(
                owner_id=campaign.owner_id,
                campaign_revision_id=revision.id,
                position=step.position,
                channel=step.channel,
                channel_account_id=channel_account_id,
                wait_minutes=step.wait_minutes,
                template_version=step.template_version,
                subject_template=step.subject_template,
                body_template=step.body_template,
                condition_definition=step.conditions,
                stop_condition_definition=step.stop_conditions,
            )
        )
    add_audit(
        db,
        owner_id=campaign.owner_id,
        actor_user_id=actor_user_id,
        action="campaign_revision.created",
        entity_type="campaign_revision",
        entity_id=revision.id,
        after={"revision_number": revision_number, "status": CampaignRevisionStatus.DRAFT.value},
    )
    return revision


def published_revision(db: Session, campaign: models.Campaign) -> Optional[models.CampaignRevision]:
    if campaign.published_revision_number is None:
        return None
    return db.query(models.CampaignRevision).filter_by(
        campaign_id=campaign.id,
        revision_number=campaign.published_revision_number,
        status=CampaignRevisionStatus.PUBLISHED,
    ).first()


def _json_diff(before: Any, after: Any, path: str = "") -> dict[str, list]:
    added: list[dict[str, Any]] = []
    removed: list[dict[str, Any]] = []
    changed: list[dict[str, Any]] = []
    if isinstance(before, dict) and isinstance(after, dict):
        for key in sorted(set(before) | set(after)):
            child_path = f"{path}.{key}" if path else key
            if key not in before:
                added.append({"path": child_path, "value": after[key]})
            elif key not in after:
                removed.append({"path": child_path, "value": before[key]})
            else:
                child = _json_diff(before[key], after[key], child_path)
                added.extend(child["added"])
                removed.extend(child["removed"])
                changed.extend(child["changed"])
    elif before != after:
        changed.append({"path": path or "$", "before": before, "after": after})
    return {"added": added, "removed": removed, "changed": changed}


def campaign_revision_diff(
    db: Session,
    *,
    campaign: models.Campaign,
    proposed: models.CampaignRevision,
) -> tuple[Optional[models.CampaignRevision], dict[str, Any]]:
    base = published_revision(db, campaign)

    def sequence_snapshot(revision: Optional[models.CampaignRevision]) -> list[dict[str, Any]]:
        if revision is None:
            return []
        steps = db.query(models.SequenceStep).filter(
            models.SequenceStep.campaign_revision_id == revision.id,
            models.SequenceStep.archived_at.is_(None),
        ).order_by(models.SequenceStep.position.asc(), models.SequenceStep.id.asc()).all()
        return [
            {
                "position": step.position,
                "channel": step.channel.value,
                "channel_account_id": step.channel_account_id,
                "wait_minutes": step.wait_minutes,
                "template_version": step.template_version,
                "subject_template": step.subject_template,
                "body_template": step.body_template,
                "conditions": step.condition_definition or {},
                "stop_conditions": step.stop_condition_definition or {},
            }
            for step in steps
        ]

    before = {
        "icp_definition": base.icp_definition if base else {},
        "audience_definition": base.audience_definition if base else {},
        "quality_gates": base.quality_gates if base else {},
        "budget_definition": base.budget_definition if base else {},
        "stop_conditions": base.stop_conditions if base else {},
        "sequence_steps": sequence_snapshot(base),
    }
    after = {
        "icp_definition": proposed.icp_definition,
        "audience_definition": proposed.audience_definition,
        "quality_gates": proposed.quality_gates,
        "budget_definition": proposed.budget_definition,
        "stop_conditions": proposed.stop_conditions,
        "sequence_steps": sequence_snapshot(proposed),
    }
    return base, _json_diff(before, after)


def campaign_revision_diff_checksum(
    *,
    campaign_id: int,
    base_revision_id: Optional[int],
    proposed_revision_id: int,
    diff: dict[str, Any],
) -> str:
    """Return the stable review token for one exact revision diff."""

    canonical = json.dumps(
        {
            "campaign_id": campaign_id,
            "base_revision_id": base_revision_id,
            "proposed_revision_id": proposed_revision_id,
            "diff": diff,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def publish_campaign_revision(
    db: Session,
    *,
    campaign: models.Campaign,
    revision: models.CampaignRevision,
    actor_user_id: int,
    idempotency_key: str,
    base_revision_id: Optional[int],
    reviewed_diff_checksum: str,
    human_confirmed: bool,
) -> models.CampaignRevision:
    if revision.campaign_id != campaign.id:
        raise ValueError("Revision does not belong to campaign")

    # Do not let ``populate_existing`` below overwrite unflushed draft content
    # (or the result of an idempotent same-session replay).  The flush remains
    # inside the caller's transaction and does not make external state visible.
    db.flush()

    # Publishing changes the single current-revision pointer on Campaign.  The
    # Campaign row is therefore the serialization fence for every publication
    # in that Campaign.  ``populate_existing`` is important: callers normally
    # load these objects before entering this function, so a waiter must refresh
    # state committed by the transaction which held the lock first.
    locked_campaign = (
        db.query(models.Campaign)
        .filter(
            models.Campaign.id == campaign.id,
            models.Campaign.owner_id == campaign.owner_id,
        )
        .populate_existing()
        .with_for_update()
        .one_or_none()
    )
    if locked_campaign is None:
        raise ValueError("Campaign no longer exists or is not owned by the publisher")
    locked_revision = (
        db.query(models.CampaignRevision)
        .filter(
            models.CampaignRevision.id == revision.id,
            models.CampaignRevision.campaign_id == locked_campaign.id,
            models.CampaignRevision.owner_id == locked_campaign.owner_id,
        )
        .populate_existing()
        .with_for_update()
        .one_or_none()
    )
    if locked_revision is None:
        raise ValueError("Revision does not belong to campaign")
    campaign = locked_campaign
    revision = locked_revision

    # Lock the reviewed baseline as well.  The Campaign fence serializes Product
    # V2 writers; the explicit baseline lock also protects the immutable review
    # input from an out-of-band database writer while its checksum is verified.
    if campaign.published_revision_number is not None:
        (
            db.query(models.CampaignRevision)
            .filter(
                models.CampaignRevision.campaign_id == campaign.id,
                models.CampaignRevision.revision_number
                == campaign.published_revision_number,
            )
            .populate_existing()
            .with_for_update()
            .one_or_none()
        )

    receipt_payload = {
        "campaign_id": campaign.id,
        "revision_id": revision.id,
        "base_revision_id": base_revision_id,
        "reviewed_diff_checksum": reviewed_diff_checksum,
        "human_confirmed": human_confirmed,
    }

    def publish_receipt() -> models.AutomationJob:
        return enqueue_job(
            db,
            owner_id=campaign.owner_id,
            campaign_id=campaign.id,
            job_type="campaign_revision.publish",
            queue="commands",
            idempotency_key=idempotency_key,
            payload=receipt_payload,
        )

    def validate_receipt_request(receipt: models.AutomationJob) -> None:
        if not all(
            (
                receipt.owner_id == campaign.owner_id,
                receipt.campaign_id == campaign.id,
                receipt.enrollment_id is None,
                receipt.attempt_id is None,
                receipt.job_type == "campaign_revision.publish",
                receipt.queue == "commands",
                (receipt.payload or {}) == receipt_payload,
            )
        ):
            raise ValueError(
                "Idempotency-Key is already used by a different command payload"
            )

    def validate_completed_receipt(receipt: models.AutomationJob) -> None:
        validate_receipt_request(receipt)
        result = receipt.result or {}
        if (
            receipt.status != JobStatus.SUCCEEDED
            or result.get("campaign_id") != campaign.id
            or result.get("revision_id") != revision.id
        ):
            raise ValueError(
                "Idempotency-Key is already used by an incomplete publish command"
            )

    # AutomationJob has a database UNIQUE constraint on ``idempotency_key`` and
    # acts as the durable command receipt.  It protects idempotency even when two
    # requests target different Campaign rows and therefore do not share a lock.
    existing_receipt = (
        db.query(models.AutomationJob)
        .filter_by(idempotency_key=idempotency_key)
        .with_for_update()
        .first()
    )
    if existing_receipt is not None:
        validate_completed_receipt(existing_receipt)
        return revision

    prior_command = next(
        (
            event
            for event in db.new
            if isinstance(event, models.AuditEvent)
            and event.owner_id == campaign.owner_id
            and event.correlation_id == idempotency_key
            and event.action == "campaign_revision.published"
        ),
        None,
    )
    if prior_command is None:
        prior_command = (
            db.query(models.AuditEvent)
            .filter_by(
                owner_id=campaign.owner_id,
                correlation_id=idempotency_key,
                action="campaign_revision.published",
            )
            .with_for_update()
            .first()
        )
    if prior_command:
        if prior_command.entity_id != str(revision.id):
            raise ValueError("Idempotency-Key is already used by another revision")
        reviewed = prior_command.metadata_json or {}
        if (
            reviewed.get("base_revision_id") != base_revision_id
            or reviewed.get("reviewed_diff_checksum") != reviewed_diff_checksum
            or reviewed.get("human_confirmed") is not True
            or human_confirmed is not True
        ):
            raise ValueError("Idempotency-Key is already used by a different publish review")
        # Backfill a durable receipt for commands produced before receipts were
        # introduced.  This does not create a second audit event.
        receipt = publish_receipt()
        receipt.status = JobStatus.SUCCEEDED
        receipt.completed_at = utcnow()
        receipt.result = {
            "campaign_id": campaign.id,
            "revision_id": revision.id,
            "published_revision_number": revision.revision_number,
        }
        return revision
    if human_confirmed is not True:
        raise ValueError("Human confirmation of the reviewed diff is required")
    if revision.status != CampaignRevisionStatus.DRAFT:
        raise ValueError("Only a draft revision can be published")

    base, diff = campaign_revision_diff(db, campaign=campaign, proposed=revision)
    current_base_revision_id = base.id if base and base.id != revision.id else None
    if current_base_revision_id != base_revision_id:
        raise ValueError("The reviewed base revision is stale")
    current_checksum = campaign_revision_diff_checksum(
        campaign_id=campaign.id,
        base_revision_id=current_base_revision_id,
        proposed_revision_id=revision.id,
        diff=diff,
    )
    if current_checksum != reviewed_diff_checksum:
        raise ValueError("The reviewed revision diff checksum does not match current content")

    # Create the unique receipt only after all rejection-only checks.  A stale or
    # unconfirmed request must not leave a command artifact if its caller catches
    # the domain error and continues the surrounding transaction.
    receipt = publish_receipt()
    db.query(models.CampaignRevision).filter(
        models.CampaignRevision.campaign_id == campaign.id,
        models.CampaignRevision.id != revision.id,
        models.CampaignRevision.status == CampaignRevisionStatus.PUBLISHED,
    ).update({"status": CampaignRevisionStatus.SUPERSEDED}, synchronize_session="fetch")
    revision.status = CampaignRevisionStatus.PUBLISHED
    revision.published_at = utcnow()
    revision.published_by_user_id = actor_user_id
    campaign.published_revision_number = revision.revision_number
    if campaign.lifecycle == CampaignLifecycle.DRAFT:
        campaign.lifecycle = CampaignLifecycle.READY
    add_audit(
        db,
        owner_id=campaign.owner_id,
        actor_user_id=actor_user_id,
        action="campaign_revision.published",
        entity_type="campaign_revision",
        entity_id=revision.id,
        correlation_id=idempotency_key,
        after={"revision_number": revision.revision_number},
        metadata={
            "base_revision_id": current_base_revision_id,
            "reviewed_diff_checksum": current_checksum,
            "human_confirmed": True,
        },
    )
    receipt.status = JobStatus.SUCCEEDED
    receipt.completed_at = utcnow()
    receipt.result = {
        "campaign_id": campaign.id,
        "revision_id": revision.id,
        "published_revision_number": revision.revision_number,
    }
    return revision


def _heartbeat_is_live(heartbeat: Optional[models.WorkerHeartbeat], now: datetime) -> bool:
    lease_expires_at = as_utc(heartbeat.lease_expires_at) if heartbeat else None
    return bool(
        heartbeat
        and heartbeat.status in {StageStatus.RUNNING, StageStatus.IDLE, StageStatus.BACKOFF}
        and lease_expires_at
        and lease_expires_at >= as_utc(now)
    )


def campaign_readiness(db: Session, campaign: models.Campaign, *, now: Optional[datetime] = None) -> CampaignReadiness:
    checked_at = now or utcnow()
    blockers: list[ReadinessCheck] = []
    warnings: list[ReadinessCheck] = []

    def check(code: str, passed: bool, message: str, *, warning: bool = False, **details):
        item = ReadinessCheck(
            code=code,
            severity="warning" if warning else "blocker",
            passed=passed,
            message=message,
            details=details,
        )
        if not passed:
            (warnings if warning else blockers).append(item)

    revision = published_revision(db, campaign)
    check("published_revision", revision is not None, "A published campaign revision is required")

    steps: list[models.SequenceStep] = []
    if revision:
        steps = db.query(models.SequenceStep).filter_by(campaign_revision_id=revision.id).all()
    check("sequence", bool(steps), "At least one sequence step is required")
    channels = {step.channel for step in steps}
    first_step = min(steps, key=lambda step: step.position) if steps else None
    for channel in sorted(channels, key=lambda item: item.value):
        check(
            f"channel_policy_{channel.value}",
            channel_policy_allows(db, owner_id=campaign.owner_id, channel=channel),
            f"The published global policy disables {channel.value}",
            channel=channel.value,
        )

    for step in sorted(steps, key=lambda item: (item.position, item.id)):
        if step.channel == Channel.OFFLINE:
            check(
                f"sequence_channel_step_{step.position}",
                False,
                "Offline evidence cannot be executed as an outreach Sequence Step",
                step_id=step.id,
                position=step.position,
                channel=step.channel.value,
            )
            continue
        account = (
            db.get(models.ChannelAccount, step.channel_account_id)
            if step.channel_account_id is not None
            else None
        )
        virtual_fake = (
            account is None
            and step.channel_account_id is None
            and fake_account_fallback_allowed()
        )
        if virtual_fake:
            blockers_for_step = []
            account_id = None
            remaining = None
            used = 0
            healthy = "virtual_fake_compatibility"
        elif account is None:
            blockers_for_step = [
                "channel_account_missing"
                if step.channel_account_id is not None
                else "channel_account_binding_missing"
            ]
            account_id = step.channel_account_id
            remaining = None
            used = 0
            healthy = False
        else:
            decision = evaluate_channel_account(
                db,
                account=account,
                owner_id=campaign.owner_id,
                channel=step.channel,
                now=checked_at,
            )
            blockers_for_step = decision.blockers
            account_id = account.id
            remaining = decision.remaining_capacity
            used = decision.used_capacity
            healthy = (
                decision.observed_health.value
                if decision.observed_health is not None
                else account.health_status.value
            )
        check(
            f"channel_account_step_{step.position}",
            not blockers_for_step,
            f"Sequence step {step.position} requires an enabled, healthy sender account with capacity",
            step_id=step.id,
            position=step.position,
            channel=step.channel.value,
            channel_account_id=account_id,
            account_health=healthy,
            used_capacity=used,
            remaining_capacity=remaining,
            blockers=blockers_for_step,
        )

    enrollment_count = db.query(models.Enrollment).filter(
        models.Enrollment.campaign_id == campaign.id,
        models.Enrollment.status.in_(ACTIVE_ENROLLMENT_STATUSES),
        models.Enrollment.archived_at.is_(None),
    ).count()
    valid_contact_count = db.query(models.Enrollment.id).join(
        models.ContactPoint,
        models.ContactPoint.contact_id == models.Enrollment.contact_id,
    ).filter(
        models.Enrollment.campaign_id == campaign.id,
        models.Enrollment.status.in_(ACTIVE_ENROLLMENT_STATUSES),
        models.ContactPoint.channel == first_step.channel if first_step else False,
        models.ContactPoint.verification_status == ContactPointVerificationStatus.VALID,
        models.ContactPoint.availability_status == ContactPointAvailabilityStatus.AVAILABLE,
        models.ContactPoint.archived_at.is_(None),
    ).distinct().count()
    check(
        "valid_audience",
        enrollment_count > 0 and valid_contact_count > 0,
        "At least one enrolled contact with a verified contact point is required",
        enrollments=enrollment_count,
        valid_contacts=valid_contact_count,
        first_step_channel=first_step.channel.value if first_step else None,
    )

    active_lock = db.query(models.SafetyLock.id).filter(
        models.SafetyLock.owner_id == campaign.owner_id,
        models.SafetyLock.active.is_(True),
        or_(models.SafetyLock.campaign_id == campaign.id, models.SafetyLock.scope == "global"),
    ).first()
    check("safety_lock", active_lock is None, "An active hard safety lock blocks this campaign")

    if Channel.EMAIL in channels:
        policy_public_url = configured_public_unsubscribe_url(
            db,
            owner_id=campaign.owner_id,
        )
        public_url = policy_public_url or (
            "" if not revision else str(revision.stop_conditions.get("public_unsubscribe_url") or "")
        )
        check(
            "public_unsubscribe_url",
            public_url.startswith("https://") or public_url.startswith("http://127.0.0.1"),
            "A public HTTPS unsubscribe URL is required for email",
        )

    required_workers = {WorkerType.OUTBOUND}
    if Channel.EMAIL in channels:
        required_workers.add(WorkerType.INBOX)
    if channels & {Channel.LINKEDIN, Channel.WHATSAPP}:
        required_workers.add(WorkerType.OMNICHANNEL)
    for worker_type in sorted(required_workers, key=lambda item: item.value):
        heartbeat = db.query(models.WorkerHeartbeat).filter_by(worker_type=worker_type).order_by(
            models.WorkerHeartbeat.last_seen_at.desc()
        ).first()
        check(
            f"worker_{worker_type.value}",
            _heartbeat_is_live(heartbeat, checked_at),
            f"A live {worker_type.value} worker heartbeat is required",
            last_seen_at=heartbeat.last_seen_at.isoformat() if heartbeat else None,
        )

    budget = campaign_budget_snapshot(db, revision, campaign.id)
    check(
        "budget",
        budget.limit is not None and budget.remaining > 0,
        "A campaign budget with remaining capacity is required",
        limit=str(budget.limit) if budget.limit is not None else None,
        used=str(budget.used),
        remaining=str(budget.remaining),
        native_unit=budget.native_unit,
    )
    global_budget = global_budget_snapshot(db, owner_id=campaign.owner_id)
    if global_budget.configured and global_budget.limit is not None:
        unit_price, pricing_error = revision_unit_price(revision, global_budget)
        check(
            "global_budget_accounting",
            global_budget.unpriced_billable_events == 0,
            "Unpriced billable Provider events require reconciliation before more paid work",
            unpriced_billable_events=global_budget.unpriced_billable_events,
            currency=global_budget.currency,
            price_version=global_budget.price_version,
        )
        check(
            "global_budget_pricing",
            pricing_error is None and unit_price is not None,
            "The Campaign Revision must use the reviewed global price and currency version",
            reason=pricing_error,
            currency=global_budget.currency,
            price_version=global_budget.price_version,
        )
        check(
            "global_budget",
            global_budget.remaining is not None
            and unit_price is not None
            and global_budget.remaining >= unit_price,
            "The global Provider budget cannot fund the next billable Attempt",
            limit=str(global_budget.limit),
            used=str(global_budget.used),
            remaining=str(global_budget.remaining),
            next_attempt_cost=str(unit_price) if unit_price is not None else None,
            currency=global_budget.currency,
        )
    if revision and not revision.quality_gates:
        check("quality_gates", False, "No quality gates are configured", warning=True)
    if campaign.run_mode.value == "auto":
        check("auto_mode", False, "Auto mode remains fake-only in the local phase", warning=True)
        if review_policy_required(db, owner_id=campaign.owner_id):
            check(
                "global_review_policy",
                False,
                "The global channel policy will route every Auto Attempt to human review",
                warning=True,
            )

    return CampaignReadiness(
        campaign_id=campaign.id,
        ready=not blockers,
        blockers=blockers,
        warnings=warnings,
        checked_at=checked_at,
    )


def create_enrollment(
    db: Session,
    *,
    campaign: models.Campaign,
    contact: models.Contact,
    idempotency_key: str,
    scheduled_at: Optional[datetime],
    actor_user_id: int,
) -> tuple[models.Enrollment, models.AutomationJob]:
    company_query = db.query(models.Company).filter(
        models.Company.id == contact.company_id,
        models.Company.owner_id == campaign.owner_id,
        models.Company.archived_at.is_(None),
    )
    if db.bind and db.bind.dialect.name == "mysql":
        # Serializes the per-Campaign/company two-contact invariant across API workers.
        company_query = company_query.with_for_update()
    company = company_query.first()
    if not company or contact.owner_id != campaign.owner_id or contact.archived_at is not None:
        raise ValueError("Contact and Company must be active and owned by the Campaign owner")
    revision = published_revision(db, campaign)
    if not revision:
        raise ValueError("Campaign has no published revision")
    existing = db.query(models.Enrollment).filter_by(campaign_id=campaign.id, contact_id=contact.id).first()
    if existing:
        job = enqueue_job(
            db,
            owner_id=campaign.owner_id,
            job_type="enrollment.created",
            idempotency_key=idempotency_key,
            queue="campaign",
            campaign_id=campaign.id,
            enrollment_id=existing.id,
            payload={"enrollment_id": existing.id},
        )
        return existing, job
    active_company_contacts = db.query(models.Enrollment.contact_id).filter(
        models.Enrollment.campaign_id == campaign.id,
        models.Enrollment.company_id == contact.company_id,
        models.Enrollment.status.in_(ACTIVE_ENROLLMENT_STATUSES),
        models.Enrollment.archived_at.is_(None),
    ).distinct().count()
    if active_company_contacts >= 2:
        raise ValueError("A campaign may contact at most two people at one company")
    if db.query(models.Opportunity.id).filter_by(company_id=contact.company_id).first():
        raise ValueError("A qualified opportunity already blocks new cold outreach to this company")
    enrollment = models.Enrollment(
        owner_id=campaign.owner_id,
        campaign_id=campaign.id,
        campaign_revision_id=revision.id,
        company_id=contact.company_id,
        contact_id=contact.id,
        status=EnrollmentStatus.SCHEDULED,
        scheduled_at=scheduled_at if scheduled_at is not None else utcnow().replace(microsecond=0),
        priority_snapshot=campaign.priority,
    )
    db.add(enrollment)
    db.flush()
    job = enqueue_job(
        db,
        owner_id=campaign.owner_id,
        job_type="enrollment.created",
        idempotency_key=idempotency_key,
        queue="campaign",
        campaign_id=campaign.id,
        enrollment_id=enrollment.id,
        payload={"enrollment_id": enrollment.id},
        priority=campaign.priority,
    )
    add_audit(
        db,
        owner_id=campaign.owner_id,
        actor_user_id=actor_user_id,
        action="enrollment.created",
        entity_type="enrollment",
        entity_id=enrollment.id,
        correlation_id=idempotency_key,
    )
    return enrollment, job


def _active_override(
    db: Session,
    enrollment_id: int,
    gate: OverrideGate,
    now: datetime,
    attempt_id: Optional[int] = None,
):
    attempt_scope = (
        models.ManualOverride.attempt_id.is_(None)
        if attempt_id is None
        else or_(models.ManualOverride.attempt_id.is_(None), models.ManualOverride.attempt_id == attempt_id)
    )
    return db.query(models.ManualOverride).filter(
        models.ManualOverride.enrollment_id == enrollment_id,
        models.ManualOverride.gate == gate,
        attempt_scope,
        models.ManualOverride.expires_at > now,
        models.ManualOverride.revoked_at.is_(None),
        models.ManualOverride.consumed_at.is_(None),
    ).order_by(models.ManualOverride.created_at.desc()).first()


@dataclass
class GateDecision:
    allowed: bool
    hard_blockers: list[str] = field(default_factory=list)
    soft_blockers: list[str] = field(default_factory=list)
    overrides: list[int] = field(default_factory=list)


@dataclass(frozen=True)
class BudgetSnapshot:
    limit: Optional[Decimal]
    used: Decimal
    remaining: Decimal
    native_unit: str


def campaign_budget_snapshot(
    db: Session,
    revision: Optional[models.CampaignRevision],
    campaign_id: int,
    *,
    lock: bool = False,
) -> BudgetSnapshot:
    definition = revision.budget_definition if revision else {}
    raw_limit = definition.get("native_limit", definition.get("limit")) if definition else None
    try:
        limit = Decimal(str(raw_limit)) if raw_limit is not None else None
    except Exception:
        limit = None
    native_unit = str(definition.get("native_unit") or "fake_calls") if definition else "fake_calls"
    budget_consuming_status = or_(
        models.ProviderCostEvent.status.in_(
            (
                ProviderCostStatus.RESERVED,
                ProviderCostStatus.CHARGED,
                ProviderCostStatus.UNKNOWN,
            )
        ),
        and_(
            models.ProviderCostEvent.status == ProviderCostStatus.FAILED,
            models.ProviderCostEvent.billable.is_(True),
        ),
    )
    cost_filter = (
        models.ProviderCostEvent.campaign_id == campaign_id,
        models.ProviderCostEvent.native_unit == native_unit,
        budget_consuming_status,
    )
    if lock and db.bind and db.bind.dialect.name == "mysql":
        rows = db.query(models.ProviderCostEvent.units).filter(*cost_filter).with_for_update().all()
        used = sum((Decimal(str(row.units)) for row in rows), Decimal("0"))
    else:
        used = db.query(func.coalesce(func.sum(models.ProviderCostEvent.units), 0)).filter(*cost_filter).scalar()
    used_decimal = Decimal(str(used or 0))
    remaining = max(Decimal("0"), (limit or Decimal("0")) - used_decimal)
    return BudgetSnapshot(limit=limit, used=used_decimal, remaining=remaining, native_unit=native_unit)


def evaluate_outreach_gates(
    db: Session,
    *,
    enrollment: models.Enrollment,
    contact_point: models.ContactPoint,
    now: Optional[datetime] = None,
    lock_budget: bool = False,
    cold_start: bool = True,
    expected_channel: Optional[Channel] = None,
    sequence_step_id: Optional[int] = None,
    attempt_id: Optional[int] = None,
    channel_account_id: Optional[int] = None,
    provider_billable: bool = False,
) -> GateDecision:
    current = now or utcnow()
    hard: list[str] = []
    soft: list[str] = []
    used_overrides: list[int] = []

    campaign = db.get(models.Campaign, enrollment.campaign_id)
    if not campaign or campaign.lifecycle != CampaignLifecycle.RUNNING:
        hard.append("campaign_not_running")
    if campaign and campaign.archived_at is not None:
        hard.append("campaign_archived")
    if enrollment.status != EnrollmentStatus.ACTIVE:
        hard.append("enrollment_not_active")
    if enrollment.archived_at is not None:
        hard.append("enrollment_archived")
    if contact_point.archived_at is not None:
        hard.append("contact_point_archived")
    if (
        contact_point.owner_id != enrollment.owner_id
        or contact_point.company_id != enrollment.company_id
        or contact_point.contact_id != enrollment.contact_id
    ):
        hard.append("contact_point_identity_mismatch")
    if expected_channel is not None and contact_point.channel != expected_channel:
        hard.append("contact_point_channel_mismatch")
    policy_channel = expected_channel or contact_point.channel
    if not channel_policy_allows(
        db,
        owner_id=enrollment.owner_id,
        channel=policy_channel,
        lock=lock_budget,
    ):
        hard.append("channel_policy_disabled")

    restrictions = db.query(models.ConsentRestriction).filter(
        models.ConsentRestriction.owner_id == enrollment.owner_id,
        models.ConsentRestriction.active.is_(True),
        or_(models.ConsentRestriction.channel.is_(None), models.ConsentRestriction.channel == contact_point.channel),
        or_(
            models.ConsentRestriction.scope == RestrictionScope.GLOBAL,
            models.ConsentRestriction.contact_point_id == contact_point.id,
            models.ConsentRestriction.contact_id == enrollment.contact_id,
            models.ConsentRestriction.company_id == enrollment.company_id,
        ),
    ).count()
    if restrictions:
        hard.append("consent_restriction")
    if (
        contact_point.channel == Channel.EMAIL
        and find_exact_email_suppression(
            db,
            email=contact_point.normalized_value or contact_point.value,
            user_id=enrollment.owner_id,
        )
        is not None
    ):
        # Migration compatibility gate: an orphaned legacy unsubscribe must
        # remain authoritative until it is explicitly reconciled into V2.
        hard.append("legacy_email_suppression")
    locks = db.query(models.SafetyLock).filter(
        models.SafetyLock.owner_id == enrollment.owner_id,
        models.SafetyLock.active.is_(True),
        or_(
            models.SafetyLock.scope == "global",
            models.SafetyLock.campaign_id == enrollment.campaign_id,
            models.SafetyLock.company_id == enrollment.company_id,
            models.SafetyLock.contact_id == enrollment.contact_id,
            models.SafetyLock.channel == contact_point.channel,
            models.SafetyLock.channel_account_id == channel_account_id
            if channel_account_id is not None
            else False,
        ),
    ).count()
    if locks:
        hard.append("safety_lock")
    if contact_point.verification_status != ContactPointVerificationStatus.VALID:
        hard.append("contact_point_not_verified")
    if contact_point.availability_status != ContactPointAvailabilityStatus.AVAILABLE:
        hard.append("contact_point_unavailable")
    point_last_outreach = as_utc(contact_point.last_cold_outreach_at)
    if cold_start and point_last_outreach and point_last_outreach >= current - timedelta(days=14):
        hard.append("contact_point_cooldown_14d")
    company = db.get(models.Company, enrollment.company_id)
    if not company or company.owner_id != enrollment.owner_id:
        hard.append("company_identity_mismatch")
    elif company.archived_at is not None:
        hard.append("company_archived")
    company_last_outreach = as_utc(company.last_cold_outreach_at) if company else None
    if cold_start and company_last_outreach and company_last_outreach >= current - timedelta(hours=24):
        hard.append("company_cooldown_24h")
    if db.query(models.Opportunity.id).filter_by(company_id=enrollment.company_id).first():
        hard.append("qualified_opportunity")
    eligible_contact_ids = [
        row[0]
        for row in db.query(models.Enrollment.contact_id).filter(
            models.Enrollment.campaign_id == enrollment.campaign_id,
            models.Enrollment.company_id == enrollment.company_id,
            models.Enrollment.status.in_(ACTIVE_ENROLLMENT_STATUSES),
            models.Enrollment.archived_at.is_(None),
        ).distinct().order_by(models.Enrollment.contact_id.asc()).limit(2).all()
    ]
    if enrollment.contact_id not in eligible_contact_ids:
        hard.append("campaign_company_contact_cap")

    revision = db.get(models.CampaignRevision, enrollment.campaign_revision_id)
    contact = db.get(models.Contact, enrollment.contact_id)
    if not contact or contact.owner_id != enrollment.owner_id or contact.company_id != enrollment.company_id:
        hard.append("contact_identity_mismatch")
    elif contact.archived_at is not None:
        hard.append("contact_archived")
    if not revision or revision.campaign_id != enrollment.campaign_id or revision.owner_id != enrollment.owner_id:
        hard.append("campaign_revision_mismatch")
    elif revision.archived_at is not None:
        hard.append("campaign_revision_archived")
    if sequence_step_id is not None:
        sequence_step = db.get(models.SequenceStep, sequence_step_id)
        if (
            not sequence_step
            or not revision
            or sequence_step.campaign_revision_id != revision.id
            or sequence_step.archived_at is not None
            or (expected_channel is not None and sequence_step.channel != expected_channel)
        ):
            hard.append("sequence_step_mismatch")
    gates = revision.quality_gates if revision else {}
    budget = campaign_budget_snapshot(db, revision, enrollment.campaign_id, lock=lock_budget)
    if budget.limit is None:
        hard.append("campaign_budget_missing")
    elif budget.remaining < Decimal("1"):
        hard.append("campaign_budget_exhausted")
    global_budget = global_budget_snapshot(
        db,
        owner_id=enrollment.owner_id,
        lock=lock_budget and provider_billable,
    )
    if provider_billable and global_budget.configured and global_budget.limit is not None:
        if global_budget.unpriced_billable_events:
            hard.append("global_budget_accounting_uncertain")
        if global_budget.remaining is None or global_budget.remaining <= 0:
            hard.append("global_budget_exhausted")
        if provider_billable:
            unit_price, pricing_error = revision_unit_price(revision, global_budget)
            if pricing_error:
                hard.append(pricing_error)
            elif (
                unit_price is not None
                and global_budget.remaining is not None
                and global_budget.remaining < unit_price
            ):
                hard.append("global_budget_insufficient_for_attempt")
    latest_evidence = db.query(models.EvidenceSnapshot).filter(
        or_(
            models.EvidenceSnapshot.company_id == enrollment.company_id,
            models.EvidenceSnapshot.contact_id == enrollment.contact_id,
        ),
        models.EvidenceSnapshot.archived_at.is_(None),
    ).order_by(models.EvidenceSnapshot.captured_at.desc()).first()
    score = None
    if latest_evidence and isinstance(latest_evidence.evidence, dict):
        score = latest_evidence.evidence.get("fit_score")
    if gates.get("min_fit_score") is not None and (score is None or score < gates["min_fit_score"]):
        soft.append("fit")
    if gates.get("require_evidence", True) and latest_evidence is None:
        soft.append("research_evidence")
    if gates.get("require_timezone", True) and not (contact and contact.timezone):
        soft.append("timezone")

    gate_by_code = {
        "fit": OverrideGate.FIT,
        "research_evidence": OverrideGate.RESEARCH_EVIDENCE,
        "timezone": OverrideGate.TIMEZONE,
    }
    remaining_soft = []
    for code in soft:
        override = _active_override(db, enrollment.id, gate_by_code[code], current, attempt_id)
        if override:
            used_overrides.append(override.id)
        else:
            remaining_soft.append(code)
    return GateDecision(
        allowed=not hard and not remaining_soft,
        hard_blockers=hard,
        soft_blockers=remaining_soft,
        overrides=used_overrides,
    )


def _apply_confirmed_unsubscribe(
    db: Session,
    *,
    assessment: models.ReplyAssessment,
    conversation: models.Conversation,
    actor_user_id: int,
) -> models.ConsentRestriction:
    """Persist the human decision and stop every Enrollment it can affect."""

    contact_point = None
    if conversation.contact_point_id is not None:
        contact_point = db.query(models.ContactPoint).filter(
            models.ContactPoint.id == conversation.contact_point_id,
            models.ContactPoint.owner_id == assessment.owner_id,
        ).first()
        if not contact_point:
            raise ValueError("Conversation contact point not found")
        if (
            contact_point.contact_id != conversation.contact_id
            or contact_point.company_id != conversation.company_id
            or contact_point.channel != conversation.channel
        ):
            raise ValueError("Conversation contact point does not match the conversation")

    idempotency_key = f"reply-assessment:{assessment.owner_id}:{assessment.id}:unsubscribe"
    expected = {
        "scope": RestrictionScope.CONTACT_POINT if contact_point else RestrictionScope.CONTACT,
        "channel": contact_point.channel if contact_point else None,
        "contact_point_id": contact_point.id if contact_point else None,
        "contact_id": None if contact_point else conversation.contact_id,
        "company_id": None,
    }
    restriction = db.query(models.ConsentRestriction).filter_by(
        idempotency_key=idempotency_key
    ).first()
    created = restriction is None
    if restriction is None:
        candidate = models.ConsentRestriction(
            owner_id=assessment.owner_id,
            idempotency_key=idempotency_key,
            reason="Human-confirmed unsubscribe reply",
            source="human_confirmed_reply",
            created_by_user_id=actor_user_id,
            metadata_json={
                "reply_assessment_id": assessment.id,
                "conversation_id": conversation.id,
            },
            **expected,
        )
        try:
            with db.begin_nested():
                db.add(candidate)
                db.flush()
            restriction = candidate
        except IntegrityError:
            created = False
            restriction = db.query(models.ConsentRestriction).filter_by(
                idempotency_key=idempotency_key
            ).first()
            if restriction is None:
                raise
    if restriction.owner_id != assessment.owner_id or any(
        getattr(restriction, key) != value for key, value in expected.items()
    ):
        raise ValueError("Unsubscribe idempotency key conflicts with another restriction")

    enrollments = db.query(models.Enrollment).filter(
        models.Enrollment.owner_id == assessment.owner_id,
        models.Enrollment.contact_id == conversation.contact_id,
        models.Enrollment.status.in_(ACTIVE_ENROLLMENT_STATUSES),
    )
    if contact_point is not None:
        matching_revision_ids = [
            row[0]
            for row in db.query(models.SequenceStep.campaign_revision_id).filter(
                models.SequenceStep.owner_id == assessment.owner_id,
                models.SequenceStep.channel == contact_point.channel,
                models.SequenceStep.archived_at.is_(None),
            ).distinct().all()
        ]
        filters = []
        if matching_revision_ids:
            filters.append(models.Enrollment.campaign_revision_id.in_(matching_revision_ids))
        if assessment.enrollment_id is not None:
            filters.append(models.Enrollment.id == assessment.enrollment_id)
        enrollments = enrollments.filter(or_(*filters)) if filters else enrollments.filter(False)

    paused_at = utcnow()
    paused_ids: list[int] = []
    for enrollment in enrollments.all():
        enrollment.status = EnrollmentStatus.PAUSED
        enrollment.paused_reason = f"consent_restriction:{restriction.id}"
        enrollment.paused_at = paused_at
        paused_ids.append(enrollment.id)

    if created:
        add_audit(
            db,
            owner_id=assessment.owner_id,
            actor_user_id=actor_user_id,
            action="consent_restriction.created",
            entity_type="consent_restriction",
            entity_id=restriction.id,
            correlation_id=idempotency_key,
            after={
                "scope": expected["scope"].value,
                "channel": expected["channel"].value if expected["channel"] else None,
                "contact_point_id": expected["contact_point_id"],
                "contact_id": expected["contact_id"],
            },
            metadata={
                "reply_assessment_id": assessment.id,
                "conversation_id": conversation.id,
                "paused_enrollment_ids": paused_ids,
            },
        )
    return restriction


def confirm_reply_assessment(
    db: Session,
    *,
    assessment: models.ReplyAssessment,
    actor_user_id: int,
    intent,
    is_positive: bool,
) -> models.Task:
    expected_positive = intent in POSITIVE_REPLY_INTENTS
    if is_positive != expected_positive:
        raise ValueError("Reply positivity must match the confirmed intent")
    already_confirmed = assessment.status == ReplyAssessmentStatus.CONFIRMED
    if already_confirmed and (assessment.intent != intent or assessment.is_positive != is_positive):
        raise ValueError("A confirmed reply assessment cannot be changed")
    if not already_confirmed:
        assessment.intent = intent
        assessment.is_positive = is_positive
        assessment.status = ReplyAssessmentStatus.CONFIRMED
        assessment.confirmed_by_user_id = actor_user_id
        assessment.confirmed_at = utcnow()
    conversation = db.get(models.Conversation, assessment.conversation_id)
    if not conversation:
        raise ValueError("Conversation not found")
    if intent == ReplyIntent.UNSUBSCRIBE:
        _apply_confirmed_unsubscribe(
            db,
            assessment=assessment,
            conversation=conversation,
            actor_user_id=actor_user_id,
        )
    if is_positive:
        positive_at = utcnow()
        if assessment.enrollment_id:
            current_enrollment = db.query(models.Enrollment).filter(
                models.Enrollment.id == assessment.enrollment_id,
                models.Enrollment.owner_id == assessment.owner_id,
            ).first()
            if current_enrollment:
                current_enrollment.status = EnrollmentStatus.PAUSED
                current_enrollment.paused_reason = "positive_reply_current_campaign"
                current_enrollment.paused_at = positive_at
                current_enrollment.positive_signal_at = positive_at
        other_enrollments = db.query(models.Enrollment).filter(
            models.Enrollment.contact_id == conversation.contact_id,
            models.Enrollment.status.in_(ACTIVE_ENROLLMENT_STATUSES),
            models.Enrollment.id != (assessment.enrollment_id or -1),
        ).all()
        for other_enrollment in other_enrollments:
            other_enrollment.status = EnrollmentStatus.PAUSED
            other_enrollment.paused_reason = "positive_reply_other_campaign"
            other_enrollment.paused_at = positive_at
            other_enrollment.positive_signal_at = positive_at
    task_type = TaskType.SALES_HANDOFF if is_positive else TaskType.REPLY_TRIAGE
    task = db.query(models.Task).filter(
        models.Task.owner_id == assessment.owner_id,
        models.Task.task_type == task_type,
        models.Task.conversation_id == assessment.conversation_id,
        models.Task.archived_at.is_(None),
    ).first()
    if not task:
        task = models.Task(
            owner_id=assessment.owner_id,
            task_type=task_type,
            status=TaskStatus.OPEN if is_positive else TaskStatus.COMPLETED,
            priority=TaskPriority.URGENT if is_positive else TaskPriority.NORMAL,
            company_id=conversation.company_id,
            contact_id=conversation.contact_id,
            enrollment_id=assessment.enrollment_id,
            conversation_id=conversation.id,
            title="Confirm qualified sales opportunity" if is_positive else "Reply classified as non-positive",
            description=assessment.latest_reply_body,
            metadata_json={"reply_assessment_id": assessment.id, "positive": is_positive},
            completed_at=None if is_positive else utcnow(),
        )
        db.add(task)
        db.flush()
    if not already_confirmed:
        add_audit(
            db,
            owner_id=assessment.owner_id,
            actor_user_id=actor_user_id,
            action="reply_assessment.confirmed",
            entity_type="reply_assessment",
            entity_id=assessment.id,
            after={"intent": str(intent), "is_positive": is_positive},
        )
    return task


def confirm_opportunity(db: Session, *, owner_id: int, actor_user_id: int, data) -> models.Opportunity:
    existing = db.query(models.Opportunity).filter_by(
        source_task_id=data.source_task_id,
        owner_id=owner_id,
    ).first()
    if existing:
        return existing
    assessment = db.query(models.ReplyAssessment).filter_by(id=data.reply_assessment_id, owner_id=owner_id).first()
    task = db.query(models.Task).filter_by(id=data.source_task_id, owner_id=owner_id).first()
    if not assessment or assessment.status != ReplyAssessmentStatus.CONFIRMED or not assessment.is_positive:
        raise ValueError("A human-confirmed positive reply is required")
    if not task or task.task_type != TaskType.SALES_HANDOFF:
        raise ValueError("A sales_handoff task is required")
    if task.status not in {TaskStatus.OPEN, TaskStatus.IN_PROGRESS}:
        raise ValueError("The sales_handoff task is no longer actionable")
    if (
        task.conversation_id != assessment.conversation_id
        or task.enrollment_id != assessment.enrollment_id
        or int((task.metadata_json or {}).get("reply_assessment_id") or 0) != assessment.id
    ):
        raise ValueError("The sales_handoff task does not match the reply assessment")
    fit_ok = data.fit_confirmed
    if not fit_ok and data.fit_override_id:
        override = db.query(models.ManualOverride).filter(
            models.ManualOverride.id == data.fit_override_id,
            models.ManualOverride.owner_id == owner_id,
            models.ManualOverride.gate == OverrideGate.FIT,
            models.ManualOverride.enrollment_id == assessment.enrollment_id,
            models.ManualOverride.revoked_at.is_(None),
            models.ManualOverride.consumed_at.is_(None),
            models.ManualOverride.expires_at > utcnow(),
        ).first()
        fit_ok = override is not None
    if not fit_ok:
        raise ValueError("Published ICP fit or a valid fit override is required")
    conversation = db.get(models.Conversation, assessment.conversation_id)
    if not conversation:
        raise ValueError("Conversation not found")
    campaign_id = None
    if assessment.enrollment_id:
        enrollment = db.get(models.Enrollment, assessment.enrollment_id)
        campaign_id = enrollment.campaign_id if enrollment else None
    opportunity = models.Opportunity(
        owner_id=owner_id,
        assignee_user_id=data.assignee_user_id,
        company_id=conversation.company_id,
        contact_id=conversation.contact_id,
        campaign_id=campaign_id,
        conversation_id=conversation.id,
        reply_assessment_id=assessment.id,
        source_task_id=task.id,
        stage=OpportunityStage.QUALIFIED_REPLY,
        fit_confirmed=True,
        fit_override_id=data.fit_override_id,
        value_amount=data.value_amount,
        currency=data.currency.upper() if data.currency else None,
        expected_close_date=data.expected_close_date,
        next_action=data.next_action,
        next_action_due_at=data.next_action_due_at,
    )
    db.add(opportunity)
    db.flush()
    if data.fit_override_id:
        fit_override = db.get(models.ManualOverride, data.fit_override_id)
        if fit_override:
            fit_override.consumed_at = utcnow()
    task.status = TaskStatus.COMPLETED
    task.completed_at = utcnow()
    task.opportunity_id = opportunity.id
    db.query(models.Enrollment).filter(
        models.Enrollment.company_id == conversation.company_id,
        models.Enrollment.status.in_(ACTIVE_ENROLLMENT_STATUSES),
    ).update(
        {
            "status": EnrollmentStatus.PAUSED,
            "paused_reason": "qualified_opportunity",
            "paused_at": utcnow(),
        },
        synchronize_session="fetch",
    )
    add_audit(
        db,
        owner_id=owner_id,
        actor_user_id=actor_user_id,
        action="opportunity.qualified",
        entity_type="opportunity",
        entity_id=opportunity.id,
        after={"stage": OpportunityStage.QUALIFIED_REPLY.value},
    )
    return opportunity


def update_opportunity_stage(db: Session, *, opportunity: models.Opportunity, actor_user_id: int, data):
    terminal_stages = {OpportunityStage.WON, OpportunityStage.LOST}
    if opportunity.stage in terminal_stages and data.stage != opportunity.stage:
        raise ValueError(
            f"Terminal Opportunity stage {opportunity.stage.value} cannot transition to {data.stage.value}"
        )
    before = {"stage": opportunity.stage.value}
    opportunity.stage = data.stage
    if data.next_action:
        opportunity.next_action = data.next_action
    if data.next_action_due_at:
        opportunity.next_action_due_at = data.next_action_due_at
    if data.stage == OpportunityStage.WON:
        opportunity.value_amount = data.value_amount
        opportunity.currency = data.currency.upper()
        opportunity.won_at = datetime.combine(data.deal_date, datetime.min.time(), tzinfo=UTC)
        opportunity.lost_at = None
        opportunity.lost_reason = None
    elif data.stage == OpportunityStage.LOST:
        opportunity.lost_reason = data.lost_reason
        opportunity.lost_at = utcnow()
    add_audit(
        db,
        owner_id=opportunity.owner_id,
        actor_user_id=actor_user_id,
        action="opportunity.stage_changed",
        entity_type="opportunity",
        entity_id=opportunity.id,
        before=before,
        after={"stage": data.stage.value},
    )
    return opportunity
