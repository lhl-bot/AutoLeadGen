"""Deterministic 30-company fake-only Product V2 acceptance replay."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Optional
from uuid import uuid4

from sqlalchemy import func
from sqlalchemy.orm import Session

import models as legacy
from product_v2 import models
from product_v2.connectors import build_local_registry
from product_v2.enums import (
    AttemptStatus,
    CampaignLifecycle,
    CampaignRevisionStatus,
    CampaignRunMode,
    Channel,
    ContactPointAvailabilityStatus,
    ContactPointVerificationStatus,
    EnrollmentStatus,
    ProviderCostStatus,
    RestrictionScope,
    SafetyLockScope,
    StageStatus,
    WorkerType,
)
from product_v2.runtime.outbound import create_first_attempt, execute_attempt
from product_v2.runtime.queue import heartbeat
from product_v2.services.channel_accounts import ensure_fake_channel_account


@dataclass(frozen=True)
class ShadowReplayReport:
    run_id: str
    companies: int
    campaigns: int
    attempts: int
    succeeded: int
    blocked: int
    external_calls: int
    duplicate_attempts: int
    duplicate_provider_messages: int
    hard_gate_bypasses: int
    traceable_attempts: int
    account_traceable_attempts: int
    message_events: int
    cost_events: int
    tasks: int
    orphan_messages: int
    orphan_costs: int
    orphan_tasks: int
    attempt_audits: int
    heartbeat_stage_mismatches: int
    real_provider_events: int
    billable_events: int
    passed: bool

    def to_dict(self) -> dict:
        return asdict(self)


def _contact_value(channel: Channel, index: int) -> str:
    if channel == Channel.EMAIL:
        return f"buyer{index}@shadow-{index}.example"
    if channel == Channel.LINKEDIN:
        return f"https://linkedin.example/in/shadow-{index}"
    return f"+1555{index:07d}"


def run_shadow_replay(
    db: Session,
    *,
    company_count: int = 30,
    run_id: Optional[str] = None,
) -> ShadowReplayReport:
    if company_count < 3:
        raise ValueError("Shadow replay requires at least three companies")
    replay_id = run_id or f"{datetime.now(timezone.utc):%Y%m%d%H%M%S}-{uuid4().hex[:8]}"
    user = legacy.User(
        username=f"product-v2-shadow-{replay_id}",
        hashed_password="local-shadow-no-login",
        display_name="Product V2 Shadow Replay",
        is_active=True,
    )
    db.add(user)
    db.flush()

    heartbeat(
        db,
        worker_name=f"shadow-{replay_id}-outbound",
        worker_type=WorkerType.OUTBOUND,
        status=StageStatus.IDLE,
        lease_seconds=3600,
        details={"connector_mode": "fake", "external_calls": 0, "run_id": replay_id},
    )
    heartbeat(
        db,
        worker_name=f"shadow-{replay_id}-inbox",
        worker_type=WorkerType.INBOX,
        status=StageStatus.IDLE,
        lease_seconds=3600,
        details={"connector_mode": "fake", "external_calls": 0, "run_id": replay_id},
    )

    channels = (Channel.EMAIL, Channel.LINKEDIN, Channel.WHATSAPP)
    campaigns: list[models.Campaign] = []
    revisions: dict[Channel, models.CampaignRevision] = {}
    for offset, channel in enumerate(channels):
        channel_account = ensure_fake_channel_account(
            db,
            owner_id=user.id,
            channel=channel,
        )
        campaign = models.Campaign(
            owner_id=user.id,
            name=f"Shadow {channel.value} {replay_id}",
            lifecycle=CampaignLifecycle.RUNNING,
            run_mode=CampaignRunMode.SHADOW,
            priority=300 - offset,
            published_revision_number=1,
        )
        db.add(campaign)
        db.flush()
        revision = models.CampaignRevision(
            owner_id=user.id,
            campaign_id=campaign.id,
            revision_number=1,
            status=CampaignRevisionStatus.PUBLISHED,
            icp_definition={"shadow": True},
            audience_definition={"synthetic": True},
            quality_gates={"require_evidence": False, "require_timezone": False},
            budget_definition={"native_limit": company_count, "native_unit": "fake_calls", "price_version": "fake-v1"},
            stop_conditions={"stop_on_reply": True, "public_unsubscribe_url": "http://127.0.0.1:3000/api/unsubscribe"},
            published_at=datetime.now(timezone.utc),
            published_by_user_id=user.id,
        )
        db.add(revision)
        db.flush()
        db.add(
            models.SequenceStep(
                owner_id=user.id,
                campaign_revision_id=revision.id,
                channel_account_id=channel_account.id,
                position=1,
                channel=channel,
                template_version=f"shadow-{channel.value}-v1",
                condition_definition={"fake_only": True},
                stop_condition_definition={"stop_on_reply": True},
            )
        )
        for stage_name, worker_type in (("outbound", WorkerType.OUTBOUND), ("inbox", WorkerType.INBOX)):
            db.add(
                models.StageRuntime(
                    owner_id=user.id,
                    campaign_id=campaign.id,
                    stage_name=stage_name,
                    status=StageStatus.IDLE,
                    reason="shadow replay",
                    details={"worker_type": worker_type.value, "run_id": replay_id},
                )
            )
        campaigns.append(campaign)
        revisions[channel] = revision
    db.flush()

    attempts: list[models.OutreachAttempt] = []
    protected_point_ids: set[int] = set()
    for index in range(company_count):
        channel = channels[index % len(channels)]
        campaign = campaigns[index % len(campaigns)]
        revision = revisions[channel]
        company = models.Company(
            owner_id=user.id,
            name=f"Synthetic Buyer {index + 1}",
            normalized_domain=f"shadow-{replay_id}-{index + 1}.example",
            industry="synthetic",
            region="shadow",
        )
        db.add(company)
        db.flush()
        contact = models.Contact(
            owner_id=user.id,
            company_id=company.id,
            full_name=f"Buyer {index + 1}",
            job_title="Purchasing Manager",
            timezone="UTC",
        )
        db.add(contact)
        db.flush()
        value = _contact_value(channel, index + 1)
        point = models.ContactPoint(
            owner_id=user.id,
            company_id=company.id,
            contact_id=contact.id,
            channel=channel,
            value=value,
            normalized_value=value.lower(),
            verification_status=ContactPointVerificationStatus.VALID,
            availability_status=ContactPointAvailabilityStatus.AVAILABLE,
            is_primary=True,
        )
        db.add(point)
        db.flush()
        enrollment = models.Enrollment(
            owner_id=user.id,
            campaign_id=campaign.id,
            campaign_revision_id=revision.id,
            company_id=company.id,
            contact_id=contact.id,
            status=EnrollmentStatus.ACTIVE,
            priority_snapshot=campaign.priority,
        )
        db.add(enrollment)
        db.flush()
        attempt = create_first_attempt(db, enrollment)
        duplicate = create_first_attempt(db, enrollment)
        if not attempt or not duplicate or attempt.id != duplicate.id:
            raise AssertionError("Sequence-step idempotency failed during shadow replay")
        attempts.append(attempt)
        if index == 0:
            protected_point_ids.add(point.id)
            db.add(
                models.ConsentRestriction(
                    owner_id=user.id,
                    idempotency_key=f"shadow-consent-{replay_id}",
                    scope=RestrictionScope.CONTACT_POINT,
                    channel=channel,
                    contact_point_id=point.id,
                    reason="shadow consent gate",
                    source="shadow_replay",
                )
            )
        elif index == 1:
            protected_point_ids.add(point.id)
            db.add(
                models.SafetyLock(
                    owner_id=user.id,
                    scope=SafetyLockScope.CONTACT,
                    contact_id=contact.id,
                    code="shadow_hard_pause",
                    reason="shadow safety gate",
                    active=True,
                )
            )
    db.commit()

    registry = build_local_registry()
    for attempt in attempts:
        execute_attempt(db, attempt=attempt, registry=registry)
        db.commit()

    attempt_ids = [attempt.id for attempt in attempts]
    statuses = dict(
        db.query(models.OutreachAttempt.id, models.OutreachAttempt.status)
        .filter(models.OutreachAttempt.id.in_(attempt_ids))
        .all()
    )
    succeeded_ids = {attempt_id for attempt_id, status in statuses.items() if status == AttemptStatus.SUCCEEDED}
    blocked_ids = {attempt_id for attempt_id, status in statuses.items() if status == AttemptStatus.BLOCKED}
    messages = db.query(models.MessageEvent).filter(models.MessageEvent.outreach_attempt_id.in_(attempt_ids)).all()
    costs = db.query(models.ProviderCostEvent).filter(models.ProviderCostEvent.outreach_attempt_id.in_(attempt_ids)).all()
    tasks = db.query(models.Task).filter(models.Task.attempt_id.in_(attempt_ids)).all()
    messages_by_attempt = {event.outreach_attempt_id for event in messages}
    costs_by_attempt = {event.outreach_attempt_id for event in costs}
    tasks_by_attempt = {task.attempt_id for task in tasks}
    traceable = sum(
        1
        for attempt_id in attempt_ids
        if (attempt_id in succeeded_ids and attempt_id in messages_by_attempt and attempt_id in costs_by_attempt)
        or (attempt_id in blocked_ids and attempt_id in tasks_by_attempt)
    )
    replay_attempts = db.query(models.OutreachAttempt).filter(
        models.OutreachAttempt.id.in_(attempt_ids)
    ).all()
    accounts_by_id = {
        account.id: account
        for account in db.query(models.ChannelAccount).filter(
            models.ChannelAccount.owner_id == user.id
        ).all()
    }
    costs_by_attempt_row = {event.outreach_attempt_id: event for event in costs}
    account_traceable = sum(
        1
        for replay_attempt in replay_attempts
        if replay_attempt.channel_account_id in accounts_by_id
        and accounts_by_id[replay_attempt.channel_account_id].owner_id == replay_attempt.owner_id
        and accounts_by_id[replay_attempt.channel_account_id].channel == replay_attempt.channel
        and (
            replay_attempt.id not in costs_by_attempt_row
            or (costs_by_attempt_row[replay_attempt.id].metadata_json or {}).get(
                "channel_account_id"
            )
            == replay_attempt.channel_account_id
        )
    )
    provider_ids = [
        attempt.provider_message_id
        for attempt in db.query(models.OutreachAttempt).filter(models.OutreachAttempt.id.in_(succeeded_ids)).all()
        if attempt.provider_message_id
    ]
    distinct_attempt_keys = db.query(func.count(func.distinct(models.OutreachAttempt.idempotency_key))).filter(
        models.OutreachAttempt.id.in_(attempt_ids)
    ).scalar()
    hard_gate_bypasses = db.query(models.OutreachAttempt.id).filter(
        models.OutreachAttempt.id.in_(succeeded_ids),
        models.OutreachAttempt.contact_point_id.in_(protected_point_ids),
    ).count()
    external_calls = sum(int((attempt.provider_response or {}).get("network_calls") or 0) for attempt in attempts)
    heartbeat_status = {
        row.worker_type.value: row.status
        for row in db.query(models.WorkerHeartbeat).filter(models.WorkerHeartbeat.worker_name.like(f"shadow-{replay_id}-%")).all()
    }
    stage_mismatches = 0
    for stage in db.query(models.StageRuntime).filter(models.StageRuntime.campaign_id.in_([item.id for item in campaigns])).all():
        worker_type = str((stage.details or {}).get("worker_type") or stage.stage_name)
        if heartbeat_status.get(worker_type) != stage.status:
            stage_mismatches += 1
    attempt_audits = db.query(models.AuditEvent).filter(
        models.AuditEvent.entity_type == "outreach_attempt",
        models.AuditEvent.entity_id.in_([str(item) for item in attempt_ids]),
    ).count()
    orphan_messages = sum(1 for event in messages if event.outreach_attempt_id not in attempt_ids)
    orphan_costs = sum(1 for event in costs if event.outreach_attempt_id not in attempt_ids or not all((event.campaign_id, event.enrollment_id, event.company_id, event.contact_id)))
    orphan_tasks = sum(1 for task in tasks if task.attempt_id not in attempt_ids or not task.campaign_id or not task.enrollment_id)
    real_provider_events = sum(1 for attempt in attempts if attempt.provider and not attempt.provider.startswith("fake-"))
    billable_events = sum(1 for event in costs if event.billable or event.status != ProviderCostStatus.CHARGED)

    report_values = {
        "run_id": replay_id,
        "companies": company_count,
        "campaigns": len(campaigns),
        "attempts": len(attempt_ids),
        "succeeded": len(succeeded_ids),
        "blocked": len(blocked_ids),
        "external_calls": external_calls,
        "duplicate_attempts": len(attempt_ids) - int(distinct_attempt_keys or 0),
        "duplicate_provider_messages": len(provider_ids) - len(set(provider_ids)),
        "hard_gate_bypasses": hard_gate_bypasses,
        "traceable_attempts": traceable,
        "account_traceable_attempts": account_traceable,
        "message_events": len(messages),
        "cost_events": len(costs),
        "tasks": len(tasks),
        "orphan_messages": orphan_messages,
        "orphan_costs": orphan_costs,
        "orphan_tasks": orphan_tasks,
        "attempt_audits": attempt_audits,
        "heartbeat_stage_mismatches": stage_mismatches,
        "real_provider_events": real_provider_events,
        "billable_events": billable_events,
    }
    passed = all(
        (
            report_values["companies"] == company_count,
            report_values["attempts"] == company_count,
            report_values["succeeded"] + report_values["blocked"] == company_count,
            report_values["blocked"] == len(protected_point_ids),
            report_values["external_calls"] == 0,
            report_values["duplicate_attempts"] == 0,
            report_values["duplicate_provider_messages"] == 0,
            report_values["hard_gate_bypasses"] == 0,
            report_values["traceable_attempts"] == company_count,
            report_values["account_traceable_attempts"] == company_count,
            report_values["orphan_messages"] == 0,
            report_values["orphan_costs"] == 0,
            report_values["orphan_tasks"] == 0,
            report_values["attempt_audits"] == company_count,
            report_values["heartbeat_stage_mismatches"] == 0,
            report_values["real_provider_events"] == 0,
            report_values["billable_events"] == 0,
        )
    )
    return ShadowReplayReport(**report_values, passed=passed)
