"""FastAPI V2 resource and command endpoints."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
import hashlib
import hmac
import json
import os
from typing import Annotated, Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, Path, Query, Request, UploadFile, status
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

import models as legacy
from database import get_db
from product_v2 import models
from product_v2.enums import (
    AttemptStatus,
    CampaignLifecycle,
    Channel,
    ChannelAccountHealth,
    EnrollmentStatus,
    JobStatus,
    OpportunityStage,
    ProviderCostStatus,
    ReviewBatchStatus,
    RestrictionScope,
    SafetyLockScope,
    RouteProposalStatus,
    TaskQueueScope,
    TaskStatus,
    TaskType,
)
from product_v2.schemas import (
    AcquisitionCandidateRead,
    AcquisitionCommitRequest,
    AcquisitionRunRead,
    AcquisitionSearchCreate,
    AcquisitionVerifyRequest,
    ActivationLaunchDraft,
    ActivationLaunchPreview,
    ActivationLaunchRequest,
    ActivationRead,
    ArchiveResult,
    AudienceListCreate,
    AudienceListRead,
    CampaignCreate,
    CampaignRead,
    CampaignReadiness,
    CampaignRevisionCreate,
    CampaignRevisionDiff,
    CampaignRevisionPublish,
    CampaignRevisionRead,
    ChannelAccountSummary,
    CommandOptions,
    CompanyCreate,
    CompanyOutreachSummary,
    CompanyRead,
    CompanyUpdate,
    CompanyWorkspaceRead,
    ConsentRestrictionCreate,
    ConsentRestrictionRead,
    ContactCreate,
    ContactPointRead,
    ContactRead,
    ContactUpdate,
    ConversationRead,
    EnrollmentCreate,
    EnrollmentRead,
    EmailAccountBindingApply,
    EmailAccountBindingDraft,
    EmailAccountBindingPreview,
    ErrorResponse,
    EvidenceSnapshotRead,
    JobAccepted,
    JobRead,
    ManualOverrideCreate,
    ManualOverrideRead,
    MessageEventRead,
    MembershipCreate,
    MembershipRead,
    OpportunityConfirm,
    OutcomeAnalyticsRead,
    OpportunityRead,
    OpportunityStageUpdate,
    ReplyAssessmentRead,
    ReplyConfirmation,
    ReviewBatchApprove,
    ReviewBatchItemRead,
    ReviewBatchItemUpdate,
    ReviewBatchPreviewRequest,
    ReviewBatchRead,
    ReviewBatchReject,
    RouteProposalCreate,
    RouteProposalRead,
    SequenceStepRead,
    SafetyLockRead,
    SafetyLockRelease,
    StageRuntimeRead,
    TaskRead,
    TaskUpdate,
    UnipileAccountBindingApply,
    UnipileAccountBindingDraft,
    UnipileAccountBindingPreview,
    WhatsAppConsentCreate,
    WhatsAppConsentRead,
    WhatsAppConsentRevoke,
    ProviderUsageRead,
    WorkerHeartbeatRead,
    WebhookEventCreate,
)
from product_v2.runtime.webhook_ingest import (
    VerifiedWebhookError,
    ingest_verified_webhook,
)
from product_v2.services.domain import (
    add_audit,
    campaign_readiness,
    campaign_revision_diff,
    campaign_revision_diff_checksum,
    confirm_opportunity,
    confirm_reply_assessment,
    create_campaign_revision,
    create_enrollment,
    enqueue_job,
    is_usable_company_domain,
    normalize_contact_point,
    normalize_domain,
    publish_campaign_revision,
    update_opportunity_stage,
    utcnow,
    validate_campaign_command,
)
from product_v2.services.channel_accounts import bind_legacy_email_account
from product_v2.services.acquisition import (
    acquisition_search_daily_limit,
    acquisition_verification_daily_limit,
    activation_launch_preview,
    activation_snapshot,
    commit_candidates,
    email_matches_company_domain,
    is_fake_acquisition_runtime,
    validate_real_acquisition_approval,
)
from product_v2.services.route_reviews import (
    RouteReviewError,
    approve_batch,
    batch_read,
    create_route_proposal,
    preview_batch,
    proposal_read,
    reject_batch,
    update_batch_item,
)
from services.csv_lead_import import (
    MAX_CSV_BYTES,
    normalize_import_row,
    parse_csv,
    suggest_mapping,
    validate_mapping,
)
from services.auth import get_current_user
from runtime_config import environment, read_int
from product_v2.webhook_security import (
    EVENT_ID_HEADER,
    SIGNATURE_HEADER,
    TIMESTAMP_HEADER,
    WebhookSecurityError,
    configured_max_body_bytes,
    verify_webhook,
    webhook_ingress_rejected,
)


_ERROR_DESCRIPTIONS = {
    401: "Authentication is missing, invalid, or expired",
    403: "The authenticated user is not allowed to perform this action",
    404: "The requested owned resource was not found",
    409: "The command conflicts with domain state, safety rules, or idempotency",
}


def _error_responses(*status_codes: int) -> dict[int, dict]:
    return {
        code: {"model": ErrorResponse, "description": _ERROR_DESCRIPTIONS[code]}
        for code in status_codes
    }


router = APIRouter(
    prefix="/api/v2",
    tags=["product-v2"],
    responses=_error_responses(status.HTTP_401_UNAUTHORIZED),
)

# The webhook body is parsed manually so its exact bytes can be authenticated.
# Keep the schema documented with normal OpenAPI component references (Pydantic
# otherwise emits document-root ``#/$defs`` references that OpenAPI tooling
# cannot resolve when the schema is nested below a path operation).
_WEBHOOK_EVENT_REQUEST_SCHEMA = WebhookEventCreate.model_json_schema(
    ref_template="#/components/schemas/{model}",
)
_WEBHOOK_EVENT_REQUEST_SCHEMA.pop("$defs", None)


async def _read_bounded_webhook_body(request: Request) -> bytes:
    """Read exact request bytes without buffering an unbounded payload."""

    limit = configured_max_body_bytes()
    content_length = request.headers.get("content-length")
    if content_length is not None:
        try:
            declared_length = int(content_length)
        except ValueError as exc:
            raise WebhookSecurityError(
                "WEBHOOK_CONTENT_LENGTH_INVALID",
                "Webhook Content-Length is invalid",
                status_code=422,
            ) from exc
        if declared_length < 0:
            raise WebhookSecurityError(
                "WEBHOOK_CONTENT_LENGTH_INVALID",
                "Webhook Content-Length is invalid",
                status_code=422,
            )
        if declared_length > limit:
            raise WebhookSecurityError(
                "WEBHOOK_PAYLOAD_TOO_LARGE",
                "Webhook payload exceeds the configured byte limit",
                status_code=413,
            )

    body = bytearray()
    async for chunk in request.stream():
        if len(body) + len(chunk) > limit:
            raise WebhookSecurityError(
                "WEBHOOK_PAYLOAD_TOO_LARGE",
                "Webhook payload exceeds the configured byte limit",
                status_code=413,
            )
        body.extend(chunk)
    return bytes(body)


def _owned(db: Session, model, entity_id: int, user: legacy.User, *, include_archived: bool = False):
    query = db.query(model).filter(model.id == entity_id, model.owner_id == user.id)
    if hasattr(model, "archived_at") and not include_archived:
        query = query.filter(model.archived_at.is_(None))
    entity = query.first()
    if not entity:
        raise HTTPException(status_code=404, detail=f"{model.__name__} not found")
    return entity


def _contact_read(db: Session, contact: models.Contact) -> ContactRead:
    points = db.query(models.ContactPoint).filter(
        models.ContactPoint.contact_id == contact.id,
        models.ContactPoint.archived_at.is_(None),
    ).order_by(models.ContactPoint.is_primary.desc(), models.ContactPoint.id.asc()).all()
    payload = {column.name: getattr(contact, column.name) for column in contact.__table__.columns}
    payload["contact_points"] = [ContactPointRead.model_validate(point) for point in points]
    return ContactRead.model_validate(payload)


def _revision_read(db: Session, revision: models.CampaignRevision) -> CampaignRevisionRead:
    payload = {column.name: getattr(revision, column.name) for column in revision.__table__.columns}
    steps = db.query(models.SequenceStep).filter(
        models.SequenceStep.campaign_revision_id == revision.id,
        models.SequenceStep.archived_at.is_(None),
    ).order_by(models.SequenceStep.position.asc(), models.SequenceStep.id.asc()).all()
    payload["sequence_steps"] = [
        SequenceStepRead(
            id=step.id,
            position=step.position,
            channel=step.channel,
            channel_account_id=step.channel_account_id,
            wait_minutes=step.wait_minutes,
            template_version=step.template_version,
            subject_template=step.subject_template,
            body_template=step.body_template,
            conditions=step.condition_definition or {},
            stop_conditions=step.stop_condition_definition or {},
        )
        for step in steps
    ]
    return CampaignRevisionRead.model_validate(payload)


def _acquisition_run_read(
    db: Session,
    run: models.AcquisitionRun,
    *,
    job_id: int | None = None,
) -> AcquisitionRunRead:
    payload = {column.name: getattr(run, column.name) for column in run.__table__.columns}
    payload["candidates"] = [
        AcquisitionCandidateRead.model_validate(candidate)
        for candidate in db.query(models.AcquisitionCandidate).filter_by(run_id=run.id).order_by(
            models.AcquisitionCandidate.row_number.asc(),
            models.AcquisitionCandidate.id.asc(),
        ).all()
    ]
    payload["job_id"] = job_id
    return AcquisitionRunRead.model_validate(payload)


def _raise_integrity_conflict(db: Session, exc: IntegrityError) -> None:
    """Map constraint failures consistently, including failures during flush."""

    db.rollback()
    raise HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail={
            "code": "CONFLICT",
            "message": "The request conflicts with an existing resource or relational constraint",
        },
    ) from exc


def _flush(db: Session) -> None:
    try:
        db.flush()
    except IntegrityError as exc:
        _raise_integrity_conflict(db, exc)


def _commit(db: Session) -> None:
    try:
        db.commit()
    except IntegrityError as exc:
        _raise_integrity_conflict(db, exc)


@router.get("/companies", response_model=list[CompanyRead])
def list_companies(
    include_archived: bool = False,
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
    user: legacy.User = Depends(get_current_user),
):
    query = db.query(models.Company).filter(models.Company.owner_id == user.id)
    if not include_archived:
        query = query.filter(models.Company.archived_at.is_(None))
    return query.order_by(models.Company.updated_at.desc()).offset(offset).limit(limit).all()


def _channel_account_summary(
    db: Session,
    account: models.ChannelAccount,
) -> ChannelAccountSummary:
    source = None
    if account.legacy_email_account_id is not None:
        source = db.query(legacy.EmailAccount).filter_by(
            id=account.legacy_email_account_id,
            user_id=account.owner_id,
        ).first()
    omni_source = None
    if account.legacy_channel_account_id is not None:
        omni_source = db.query(legacy.ChannelAccount).filter_by(
            id=account.legacy_channel_account_id,
            user_id=account.owner_id,
        ).first()
    use_ssl = bool(source.use_ssl) if source is not None else False
    use_tls = bool(source.use_tls) if source is not None else False
    if use_ssl and not use_tls:
        transport = "smtps"
    elif use_tls and not use_ssl:
        transport = "starttls"
    else:
        transport = "invalid"
    return ChannelAccountSummary(
        id=account.id,
        owner_id=account.owner_id,
        channel=account.channel,
        provider=account.provider,
        address=account.provider_account_id,
        display_name=(
            source.display_name
            if source is not None
            else (omni_source.name if omni_source is not None else None)
        ),
        enabled=account.enabled,
        health_status=account.health_status,
        health_checked_at=account.health_checked_at,
        daily_limit=account.daily_limit,
        timezone=account.timezone,
        smtp_host=source.smtp_host if source is not None else None,
        smtp_port=source.smtp_port if source is not None else None,
        imap_host=source.imap_host if source is not None else None,
        imap_port=source.imap_port if source is not None else None,
        transport=transport,
        credentials_configured=bool(
            source is not None
            and source.smtp_host
            and source.smtp_user
            and source.smtp_pass
        ),
        legacy_email_account_id=account.legacy_email_account_id,
        legacy_channel_account_id=account.legacy_channel_account_id,
        last_error=account.last_error,
    )


def _email_binding_preview(
    db: Session,
    *,
    owner_id: int,
    draft: EmailAccountBindingDraft,
) -> EmailAccountBindingPreview:
    source = db.query(legacy.EmailAccount).filter_by(
        id=draft.legacy_email_account_id,
        user_id=owner_id,
    ).first()
    if source is None:
        raise HTTPException(status_code=404, detail="Email account not found")
    try:
        ZoneInfo(draft.timezone)
    except ZoneInfoNotFoundError as exc:
        raise HTTPException(
            status_code=422,
            detail={"code": "INVALID_ACCOUNT_TIMEZONE", "message": "Unknown account timezone"},
        ) from exc
    current = db.query(models.ChannelAccount).filter_by(
        legacy_email_account_id=source.id,
    ).first()
    warnings: list[dict[str, str]] = []
    if not source.imap_host:
        warnings.append(
            {
                "code": "IMAP_NOT_CONFIGURED",
                "message": "IMAP is missing; reply, bounce and complaint ingestion cannot be enabled.",
            }
        )
    if bool(source.use_ssl) == bool(source.use_tls):
        warnings.append(
            {
                "code": "SMTP_TRANSPORT_INVALID",
                "message": "Exactly one of SMTPS or STARTTLS must be configured.",
            }
        )
    if not all((source.smtp_host, source.smtp_user, source.smtp_pass)):
        warnings.append(
            {
                "code": "SMTP_CREDENTIALS_INCOMPLETE",
                "message": "SMTP host, username or encrypted credential is missing.",
            }
        )
    identity_drift = bool(
        current is not None
        and (
            current.owner_id != owner_id
            or current.provider != "smtp"
            or current.provider_account_id != source.email
        )
    )
    if identity_drift:
        warnings.append(
            {
                "code": "CHANNEL_ACCOUNT_IDENTITY_DRIFT",
                "message": "The existing immutable V2 binding does not match the legacy mailbox identity.",
            }
        )
    reviewed_state = {
        "owner_id": owner_id,
        "legacy_email_account_id": source.id,
        "address": source.email,
        "smtp_host": source.smtp_host,
        "smtp_port": source.smtp_port,
        "imap_host": source.imap_host,
        "imap_port": source.imap_port,
        "use_ssl": bool(source.use_ssl),
        "use_tls": bool(source.use_tls),
        "credentials_configured": bool(source.smtp_user and source.smtp_pass),
        "current_channel_account_id": current.id if current is not None else None,
        "daily_limit": draft.daily_limit,
        "timezone": draft.timezone,
    }
    preview_checksum = hashlib.sha256(
        json.dumps(reviewed_state, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return EmailAccountBindingPreview(
        legacy_email_account_id=source.id,
        current_channel_account_id=current.id if current is not None else None,
        address=source.email,
        daily_limit=draft.daily_limit,
        timezone=draft.timezone,
        preview_checksum=preview_checksum,
        effects={
            "credential_copy_count": 0,
            "message_send_count": 0,
            "external_provider_call_count": 0,
            "outbound_hard_pause_unchanged": True,
            "health_after_binding": "unknown_until_no_send_probe",
        },
        warnings=warnings,
    )


@router.get("/channel-accounts", response_model=list[ChannelAccountSummary])
def list_channel_accounts(
    db: Session = Depends(get_db),
    user: legacy.User = Depends(get_current_user),
):
    accounts = db.query(models.ChannelAccount).filter(
        models.ChannelAccount.owner_id == user.id,
        models.ChannelAccount.archived_at.is_(None),
    ).order_by(models.ChannelAccount.channel, models.ChannelAccount.id).all()
    return [_channel_account_summary(db, account) for account in accounts]


@router.post(
    "/channel-accounts/email-bindings/preview",
    response_model=EmailAccountBindingPreview,
    responses=_error_responses(status.HTTP_404_NOT_FOUND),
)
def preview_email_account_binding(
    payload: EmailAccountBindingDraft,
    db: Session = Depends(get_db),
    user: legacy.User = Depends(get_current_user),
):
    return _email_binding_preview(db, owner_id=user.id, draft=payload)


@router.post(
    "/channel-accounts/email-bindings",
    response_model=ChannelAccountSummary,
    responses=_error_responses(status.HTTP_404_NOT_FOUND, status.HTTP_409_CONFLICT),
)
def apply_email_account_binding(
    payload: EmailAccountBindingApply,
    idempotency_key: str = Header(
        ...,
        alias="Idempotency-Key",
        min_length=8,
        max_length=255,
    ),
    db: Session = Depends(get_db),
    user: legacy.User = Depends(get_current_user),
):
    command = {
        "legacy_email_account_id": payload.legacy_email_account_id,
        "daily_limit": payload.daily_limit,
        "timezone": payload.timezone,
        "preview_checksum": payload.preview_checksum,
        "human_confirmed": True,
    }
    replay = db.query(models.AuditEvent).filter_by(
        owner_id=user.id,
        action="channel_account.email_binding_applied",
        correlation_id=idempotency_key,
    ).first()
    if replay is not None:
        if (replay.metadata_json or {}).get("command") != command:
            raise HTTPException(
                status_code=409,
                detail={"code": "IDEMPOTENCY_KEY_CONFLICT"},
            )
        account = _owned(
            db,
            models.ChannelAccount,
            int(replay.entity_id),
            user,
            include_archived=True,
        )
        return _channel_account_summary(db, account)

    source_query = db.query(legacy.EmailAccount).filter_by(
        id=payload.legacy_email_account_id,
        user_id=user.id,
    )
    if db.get_bind().dialect.name == "mysql":
        source_query = source_query.with_for_update()
    if source_query.first() is None:
        raise HTTPException(status_code=404, detail="Email account not found")
    preview = _email_binding_preview(db, owner_id=user.id, draft=payload)
    if not hmac.compare_digest(preview.preview_checksum, payload.preview_checksum):
        raise HTTPException(
            status_code=409,
            detail={
                "code": "EMAIL_BINDING_PREVIEW_STALE",
                "message": "Mailbox configuration changed after preview; review it again.",
            },
        )
    if any(item["code"] == "CHANNEL_ACCOUNT_IDENTITY_DRIFT" for item in preview.warnings):
        raise HTTPException(
            status_code=409,
            detail={"code": "CHANNEL_ACCOUNT_IDENTITY_DRIFT"},
        )
    try:
        account = bind_legacy_email_account(
            db,
            owner_id=user.id,
            legacy_email_account_id=payload.legacy_email_account_id,
            daily_limit=payload.daily_limit,
            account_timezone=payload.timezone,
            actor_user_id=user.id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    add_audit(
        db,
        owner_id=user.id,
        actor_user_id=user.id,
        action="channel_account.email_binding_applied",
        entity_type="channel_account",
        entity_id=account.id,
        after={
            "legacy_email_account_id": account.legacy_email_account_id,
            "daily_limit": account.daily_limit,
            "timezone": account.timezone,
            "health_status": account.health_status.value,
            "message_send_count": 0,
        },
        metadata={"command": command},
        correlation_id=idempotency_key,
    )
    _commit(db)
    db.refresh(account)
    return _channel_account_summary(db, account)


def _unipile_binding_preview(
    db: Session,
    *,
    owner_id: int,
    draft: UnipileAccountBindingDraft,
) -> UnipileAccountBindingPreview:
    source = db.query(legacy.ChannelAccount).filter_by(
        id=draft.legacy_channel_account_id,
        user_id=owner_id,
    ).first()
    if source is None:
        raise HTTPException(status_code=404, detail="Channel account not found")
    if source.account_type.strip().lower() != draft.channel.value:
        raise HTTPException(status_code=409, detail={"code": "UNIPILE_CHANNEL_MISMATCH"})
    try:
        ZoneInfo(draft.timezone)
    except ZoneInfoNotFoundError as exc:
        raise HTTPException(status_code=422, detail={"code": "INVALID_ACCOUNT_TIMEZONE"}) from exc
    provider_status = str(source.status or "UNKNOWN").upper()
    health = (
        ChannelAccountHealth.HEALTHY
        if provider_status == "OK"
        else ChannelAccountHealth.UNHEALTHY
        if provider_status in {"CREDENTIALS", "DISCONNECTED"}
        else ChannelAccountHealth.UNKNOWN
    )
    current = db.query(models.ChannelAccount).filter_by(
        legacy_channel_account_id=source.id,
    ).first()
    warnings = []
    if health != ChannelAccountHealth.HEALTHY:
        warnings.append({"code": "UNIPILE_ACCOUNT_NOT_HEALTHY", "message": "Provider account is not healthy"})
    state = {
        "owner_id": owner_id,
        "legacy_channel_account_id": source.id,
        "provider_account_id": source.unipile_account_id,
        "channel": draft.channel.value,
        "provider_status": provider_status,
        "daily_limit": draft.daily_limit,
        "timezone": draft.timezone,
        "current_channel_account_id": current.id if current else None,
    }
    return UnipileAccountBindingPreview(
        legacy_channel_account_id=source.id,
        provider_account_id=source.unipile_account_id,
        channel=draft.channel,
        display_name=source.name,
        provider_status=provider_status,
        health_status=health,
        daily_limit=draft.daily_limit,
        timezone=draft.timezone,
        preview_checksum=hashlib.sha256(
            json.dumps(state, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest(),
        warnings=warnings,
    )


@router.post(
    "/channel-accounts/unipile-bindings/preview",
    response_model=UnipileAccountBindingPreview,
    responses=_error_responses(status.HTTP_404_NOT_FOUND, status.HTTP_409_CONFLICT),
)
def preview_unipile_account_binding(
    payload: UnipileAccountBindingDraft,
    db: Session = Depends(get_db),
    user: legacy.User = Depends(get_current_user),
):
    return _unipile_binding_preview(db, owner_id=user.id, draft=payload)


@router.post(
    "/channel-accounts/unipile-bindings",
    response_model=ChannelAccountSummary,
    responses=_error_responses(status.HTTP_404_NOT_FOUND, status.HTTP_409_CONFLICT),
)
def apply_unipile_account_binding(
    payload: UnipileAccountBindingApply,
    idempotency_key: str = Header(..., alias="Idempotency-Key", min_length=8, max_length=255),
    db: Session = Depends(get_db),
    user: legacy.User = Depends(get_current_user),
):
    preview = _unipile_binding_preview(db, owner_id=user.id, draft=payload)
    if not hmac.compare_digest(preview.preview_checksum, payload.preview_checksum):
        raise HTTPException(status_code=409, detail={"code": "UNIPILE_BINDING_PREVIEW_STALE"})
    source_query = db.query(legacy.ChannelAccount).filter_by(
        id=payload.legacy_channel_account_id,
        user_id=user.id,
    )
    if db.get_bind().dialect.name == "mysql":
        source_query = source_query.with_for_update()
    source = source_query.first()
    if source is None:
        raise HTTPException(status_code=404, detail="Channel account not found")
    existing = db.query(models.ChannelAccount).filter_by(
        legacy_channel_account_id=source.id,
    ).first()
    if existing is not None:
        if (
            existing.owner_id != user.id
            or existing.channel != payload.channel
            or existing.provider != "unipile"
            or existing.provider_account_id != source.unipile_account_id
        ):
            raise HTTPException(status_code=409, detail={"code": "CHANNEL_ACCOUNT_IDENTITY_DRIFT"})
        existing.daily_limit = payload.daily_limit
        existing.timezone = payload.timezone
        account = existing
    else:
        account = models.ChannelAccount(
            owner_id=user.id,
            channel=payload.channel,
            provider="unipile",
            provider_account_id=source.unipile_account_id,
            legacy_channel_account_id=source.id,
            daily_limit=payload.daily_limit,
            timezone=payload.timezone,
            health_status=preview.health_status,
            health_checked_at=utcnow(),
            enabled=True,
        )
        db.add(account)
        _flush(db)
    add_audit(
        db,
        owner_id=user.id,
        actor_user_id=user.id,
        action="channel_account.unipile_binding_applied",
        entity_type="channel_account",
        entity_id=account.id,
        correlation_id=idempotency_key,
        after={
            "channel": payload.channel.value,
            "daily_limit": payload.daily_limit,
            "health_status": account.health_status.value,
            "message_send_count": 0,
        },
    )
    _commit(db)
    db.refresh(account)
    return _channel_account_summary(db, account)


@router.post(
    "/channel-accounts/{account_id}/health-check",
    response_model=ChannelAccountSummary,
    responses=_error_responses(status.HTTP_404_NOT_FOUND, status.HTTP_409_CONFLICT),
)
async def check_unipile_account_health(
    account_id: int,
    db: Session = Depends(get_db),
    user: legacy.User = Depends(get_current_user),
):
    account = _owned(db, models.ChannelAccount, account_id, user)
    if account.provider != "unipile" or account.channel not in {Channel.LINKEDIN, Channel.WHATSAPP}:
        raise HTTPException(status_code=409, detail={"code": "UNIPILE_ACCOUNT_REQUIRED"})
    from services.unipile_client import UnipileClient

    provider_status = await UnipileClient().get_account_status(account.provider_account_id)
    account.health_checked_at = utcnow()
    if provider_status == "OK":
        account.health_status = ChannelAccountHealth.HEALTHY
        account.last_error = None
    elif provider_status is None:
        account.health_status = ChannelAccountHealth.UNKNOWN
        account.last_error = "unipile_health_unknown"
    else:
        account.health_status = ChannelAccountHealth.UNHEALTHY
        account.last_error = f"unipile_status:{provider_status}"[:1000]
    _commit(db)
    db.refresh(account)
    return _channel_account_summary(db, account)


@router.post(
    "/acquisition-runs/import/preview",
    response_model=AcquisitionRunRead,
    status_code=status.HTTP_201_CREATED,
    responses=_error_responses(status.HTTP_409_CONFLICT),
)
async def preview_acquisition_import(
    file: UploadFile = File(...),
    name: str = Form("CSV 首批客户"),
    mapping_json: str = Form(""),
    idempotency_key: str = Header(..., alias="Idempotency-Key", min_length=8, max_length=255),
    db: Session = Depends(get_db),
    user: legacy.User = Depends(get_current_user),
):
    """Parse a CSV into a durable, non-authoritative candidate workspace."""

    try:
        content = await file.read(MAX_CSV_BYTES + 1)
        if len(content) > MAX_CSV_BYTES:
            raise HTTPException(
                status_code=413,
                detail={
                    "code": "CSV_IMPORT_TOO_LARGE",
                    "message": "CSV file exceeds the 2 MB limit",
                },
            )
        parsed = parse_csv(content)
        suggested = suggest_mapping(parsed.headers)
        if mapping_json.strip():
            supplied = json.loads(mapping_json)
            if not isinstance(supplied, dict):
                raise ValueError("mapping_json must be a JSON object")
            suggested.update(supplied)
        mapping = validate_mapping(suggested, parsed.headers)
    except (ValueError, json.JSONDecodeError) as exc:
        raise HTTPException(
            status_code=422,
            detail={"code": "CSV_IMPORT_INVALID", "message": str(exc)},
        ) from exc

    normalized_name = (name.strip() or file.filename or "CSV 首批客户")[:255]
    import_command = {
        "source": "csv",
        "name": normalized_name,
        "filename": file.filename,
        "content_sha256": hashlib.sha256(content).hexdigest(),
        "mapping": mapping,
    }
    existing_run = db.query(models.AcquisitionRun).filter_by(
        owner_id=user.id,
        idempotency_key=idempotency_key,
    ).first()
    if existing_run is not None:
        if (
            existing_run.source != "csv"
            or (existing_run.criteria or {}).get("import_command") != import_command
        ):
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "IDEMPOTENCY_KEY_CONFLICT",
                    "message": "Idempotency-Key belongs to another CSV import",
                },
            )
        return _acquisition_run_read(db, existing_run)

    existing_emails = {
        value
        for (value,) in db.query(models.ContactPoint.normalized_value).filter(
            models.ContactPoint.owner_id == user.id,
            models.ContactPoint.channel == Channel.EMAIL,
            models.ContactPoint.archived_at.is_(None),
        ).all()
        if value
    }
    run = models.AcquisitionRun(
        owner_id=user.id,
        source="csv",
        status="ready",
        name=normalized_name,
        idempotency_key=idempotency_key,
        criteria={
            "filename": file.filename,
            "headers": parsed.headers,
            "encoding": parsed.encoding,
            "delimiter": parsed.delimiter,
            "row_count": len(parsed.rows),
            "import_command": import_command,
        },
        column_mapping=mapping,
        provider="csv-import",
        estimated_units=0,
        price_version="free-v1",
    )
    db.add(run)
    _flush(db)
    seen_emails: set[str] = set()
    for row_number, raw in enumerate(parsed.rows, start=2):
        normalized = normalize_import_row(raw, mapping)
        raw_email_source = mapping.get("email")
        raw_email = str(raw.get(raw_email_source, "") if raw_email_source else "").strip()
        raw_domain_source = mapping.get("domain")
        raw_domain = str(raw.get(raw_domain_source, "") if raw_domain_source else "").strip()
        email = normalized.get("email")
        domain = normalized.get("domain")
        usable_domain = is_usable_company_domain(domain) and (
            not raw_domain or is_usable_company_domain(raw_domain)
        )
        candidate_status = "ready"
        rejection_reason = None
        if raw_email and not email:
            candidate_status = "invalid"
            rejection_reason = "邮箱格式无效"
        elif not usable_domain:
            candidate_status = "invalid"
            rejection_reason = "公司域名无效或属于公共邮箱域名"
        elif email and not email_matches_company_domain(email, domain):
            candidate_status = "invalid"
            rejection_reason = "邮箱域名与公司域名不一致"
        elif email and (email in existing_emails or email in seen_emails):
            candidate_status = "duplicate"
            rejection_reason = "客户库或当前文件中已存在相同邮箱"
        if email:
            seen_emails.add(email)
        first_name = normalized.get("first_name")
        last_name = normalized.get("last_name")
        full_name = " ".join(filter(None, [first_name, last_name])).strip() or None
        db.add(
            models.AcquisitionCandidate(
                owner_id=user.id,
                run_id=run.id,
                row_number=row_number,
                status=candidate_status,
                company_name=(normalized.get("company_name") or (domain.split(".")[0].title() if domain else None)),
                normalized_domain=domain if usable_domain else None,
                first_name=first_name,
                last_name=last_name,
                full_name=full_name,
                job_title=normalized.get("job_title"),
                email=email,
                normalized_email=email,
                evidence={
                    "source": "csv",
                    "row_number": row_number,
                    "provided_fields": [field for field, value in normalized.items() if value],
                },
                confidence=Decimal("1.0000"),
                rejection_reason=rejection_reason,
            )
        )
    add_audit(
        db,
        owner_id=user.id,
        actor_user_id=user.id,
        action="activation.source_run_started",
        entity_type="acquisition_run",
        entity_id=run.id,
        after={"source": "csv", "row_count": len(parsed.rows), "filename": file.filename},
        metadata={"command": import_command},
        correlation_id=idempotency_key,
    )
    _commit(db)
    db.refresh(run)
    return _acquisition_run_read(db, run)


@router.post(
    "/acquisition-runs/search",
    response_model=AcquisitionRunRead,
    status_code=status.HTTP_202_ACCEPTED,
    responses=_error_responses(status.HTTP_409_CONFLICT),
)
def create_acquisition_search(
    payload: AcquisitionSearchCreate,
    idempotency_key: str = Header(..., alias="Idempotency-Key", min_length=8, max_length=255),
    db: Session = Depends(get_db),
    user: legacy.User = Depends(get_current_user),
):
    existing_job = db.query(models.AutomationJob).filter_by(idempotency_key=idempotency_key).first()
    if existing_job:
        run_id = int((existing_job.payload or {}).get("run_id") or 0)
        run = db.query(models.AcquisitionRun).filter_by(id=run_id, owner_id=user.id).first()
        if (
            not run
            or existing_job.job_type != "acquisition.search"
            or (run.criteria or {}) != payload.model_dump(mode="json")
        ):
            raise HTTPException(
                status_code=409,
                detail={"code": "IDEMPOTENCY_KEY_CONFLICT", "message": "Idempotency-Key belongs to another command"},
            )
        return _acquisition_run_read(db, run, job_id=existing_job.id)
    if not is_fake_acquisition_runtime():
        try:
            validate_real_acquisition_approval(owner_id=user.id, approval_id=payload.approval_id)
        except ValueError as exc:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": (
                        "REAL_ACQUISITION_NOT_APPROVED"
                        if str(exc) == "real_acquisition_connector_not_approved"
                        else str(exc).upper()
                    ),
                    "message": "Real acquisition is locked by its approval controls",
                },
            ) from exc
        db.query(legacy.User.id).filter(legacy.User.id == user.id).with_for_update().one()
        day_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
        searches_today = db.query(func.count(models.AcquisitionRun.id)).filter(
            models.AcquisitionRun.owner_id == user.id,
            models.AcquisitionRun.source == "ai",
            models.AcquisitionRun.created_at >= day_start,
        ).scalar() or 0
        if searches_today >= acquisition_search_daily_limit():
            raise HTTPException(status_code=409, detail={"code": "ACQUISITION_DAILY_SEARCH_LIMIT"})
    run = models.AcquisitionRun(
        owner_id=user.id,
        source="ai",
        status="processing",
        name=payload.name.strip(),
        idempotency_key=idempotency_key,
        criteria=payload.model_dump(mode="json"),
        provider="pending",
        estimated_units=payload.limit,
        price_version=(
            "fake-v1"
            if environment() in {"local", "test"}
            else os.environ.get("ACQUISITION_PRICE_VERSION", "unapproved-price-version")
        ),
    )
    db.add(run)
    _flush(db)
    if not is_fake_acquisition_runtime():
        db.add(
            models.ProviderCostEvent(
                owner_id=user.id,
                provider="tavily",
                operation="company_search",
                status=ProviderCostStatus.RESERVED,
                units=1,
                native_unit="search_calls",
                result_count=0,
                billable=True,
                price_version=run.price_version,
                idempotency_key=f"cost:acquisition-search:{run.id}",
                metadata_json={
                    "acquisition_run_id": run.id,
                    "approval_id": payload.approval_id,
                    "reservation": True,
                },
            )
        )
    job = enqueue_job(
        db,
        owner_id=user.id,
        job_type="acquisition.search",
        idempotency_key=idempotency_key,
        queue="prospecting",
        payload={"run_id": run.id},
    )
    add_audit(
        db,
        owner_id=user.id,
        actor_user_id=user.id,
        action="activation.source_run_started",
        entity_type="acquisition_run",
        entity_id=run.id,
        correlation_id=idempotency_key,
        after={"source": "ai", "limit": payload.limit, "paid_action_confirmed": True},
    )
    _commit(db)
    db.refresh(run)
    return _acquisition_run_read(db, run, job_id=job.id)


@router.get(
    "/acquisition-runs/{run_id}",
    response_model=AcquisitionRunRead,
    responses=_error_responses(status.HTTP_404_NOT_FOUND),
)
def get_acquisition_run(
    run_id: int,
    db: Session = Depends(get_db),
    user: legacy.User = Depends(get_current_user),
):
    run = _owned(db, models.AcquisitionRun, run_id, user)
    latest_job = db.query(models.AutomationJob).filter(
        models.AutomationJob.owner_id == user.id,
        models.AutomationJob.job_type.in_(("acquisition.search", "acquisition.verify")),
    ).order_by(models.AutomationJob.id.desc()).first()
    job_id = latest_job.id if latest_job and int((latest_job.payload or {}).get("run_id") or 0) == run.id else None
    return _acquisition_run_read(db, run, job_id=job_id)


@router.post(
    "/acquisition-runs/{run_id}/verify",
    response_model=AcquisitionRunRead,
    status_code=status.HTTP_202_ACCEPTED,
    responses=_error_responses(status.HTTP_404_NOT_FOUND, status.HTTP_409_CONFLICT),
)
def verify_acquisition_candidates(
    run_id: int,
    payload: AcquisitionVerifyRequest,
    idempotency_key: str = Header(..., alias="Idempotency-Key", min_length=8, max_length=255),
    db: Session = Depends(get_db),
    user: legacy.User = Depends(get_current_user),
):
    run = _owned(db, models.AcquisitionRun, run_id, user)
    existing_job = db.query(models.AutomationJob).filter_by(idempotency_key=idempotency_key).first()
    if existing_job:
        expected = {
            "run_id": run.id,
            "candidate_ids": payload.candidate_ids,
            "paid_action_confirmed": True,
            "approval_id": payload.approval_id,
        }
        if existing_job.job_type != "acquisition.verify" or (existing_job.payload or {}) != expected:
            raise HTTPException(
                status_code=409,
                detail={"code": "IDEMPOTENCY_KEY_CONFLICT", "message": "Idempotency-Key belongs to another verification"},
            )
        return _acquisition_run_read(db, run, job_id=existing_job.id)
    if not is_fake_acquisition_runtime():
        try:
            validate_real_acquisition_approval(owner_id=user.id, approval_id=payload.approval_id)
        except ValueError as exc:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": (
                        "REAL_ACQUISITION_NOT_APPROVED"
                        if str(exc) == "real_acquisition_connector_not_approved"
                        else str(exc).upper()
                    ),
                    "message": "Paid verification is locked by its approval controls",
                },
            ) from exc
        db.query(legacy.User.id).filter(legacy.User.id == user.id).with_for_update().one()
        day_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
        reserved_today = db.query(func.coalesce(func.sum(models.ProviderCostEvent.units), 0)).filter(
            models.ProviderCostEvent.owner_id == user.id,
            models.ProviderCostEvent.operation == "email_verification",
            models.ProviderCostEvent.status.in_((
                ProviderCostStatus.RESERVED,
                ProviderCostStatus.CHARGED,
                ProviderCostStatus.UNKNOWN,
            )),
            models.ProviderCostEvent.created_at >= day_start,
        ).scalar() or 0
        if int(reserved_today) + len(payload.candidate_ids) > acquisition_verification_daily_limit():
            raise HTTPException(status_code=409, detail={"code": "ACQUISITION_DAILY_VERIFICATION_LIMIT"})
    candidate_count = db.query(models.AcquisitionCandidate.id).filter(
        models.AcquisitionCandidate.owner_id == user.id,
        models.AcquisitionCandidate.run_id == run.id,
        models.AcquisitionCandidate.id.in_(payload.candidate_ids),
    ).count()
    if candidate_count != len(payload.candidate_ids):
        raise HTTPException(status_code=404, detail="AcquisitionCandidate not found")
    run.status = "processing"
    job_payload = {
        "run_id": run.id,
        "candidate_ids": payload.candidate_ids,
        "paid_action_confirmed": True,
        "approval_id": payload.approval_id,
    }
    if not is_fake_acquisition_runtime():
        for candidate_id in payload.candidate_ids:
            db.add(
                models.ProviderCostEvent(
                    owner_id=user.id,
                    provider="snovio",
                    operation="email_verification",
                    status=ProviderCostStatus.RESERVED,
                    units=1,
                    native_unit="verification_calls",
                    result_count=0,
                    billable=True,
                    price_version=run.price_version,
                    idempotency_key=f"cost:acquisition-verify:{run.id}:{candidate_id}",
                    metadata_json={
                        "acquisition_run_id": run.id,
                        "acquisition_candidate_id": candidate_id,
                        "approval_id": payload.approval_id,
                        "reservation": True,
                    },
                )
            )
    job = enqueue_job(
        db,
        owner_id=user.id,
        job_type="acquisition.verify",
        idempotency_key=idempotency_key,
        queue="prospecting",
        payload=job_payload,
    )
    _commit(db)
    db.refresh(run)
    return _acquisition_run_read(db, run, job_id=job.id)


@router.post(
    "/acquisition-runs/{run_id}/commit",
    response_model=AcquisitionRunRead,
    responses=_error_responses(status.HTTP_404_NOT_FOUND, status.HTTP_409_CONFLICT),
)
def commit_acquisition_candidates(
    run_id: int,
    payload: AcquisitionCommitRequest,
    idempotency_key: str = Header(..., alias="Idempotency-Key", min_length=8, max_length=255),
    db: Session = Depends(get_db),
    user: legacy.User = Depends(get_current_user),
):
    run = _owned(db, models.AcquisitionRun, run_id, user)
    command = {
        "run_id": run.id,
        "candidate_ids": payload.candidate_ids,
        "human_confirmed": True,
    }
    replay = db.query(models.AuditEvent).filter_by(
        owner_id=user.id,
        action="activation.customers_committed",
        correlation_id=idempotency_key,
    ).first()
    if replay is not None:
        if (replay.metadata_json or {}).get("command") != command:
            raise HTTPException(
                status_code=409,
                detail={"code": "IDEMPOTENCY_KEY_CONFLICT", "message": "Idempotency-Key belongs to another commit"},
            )
        return _acquisition_run_read(db, run)
    try:
        commit_candidates(
            db,
            run=run,
            candidate_ids=payload.candidate_ids,
            actor_user_id=user.id,
            correlation_id=idempotency_key,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=409,
            detail={"code": "ACQUISITION_COMMIT_BLOCKED", "message": str(exc)},
        ) from exc
    _commit(db)
    db.refresh(run)
    return _acquisition_run_read(db, run)


@router.get("/activation", response_model=ActivationRead)
def get_activation(
    db: Session = Depends(get_db),
    user: legacy.User = Depends(get_current_user),
):
    return activation_snapshot(db, owner_id=user.id)


@router.post(
    "/activation/launch-preview",
    response_model=ActivationLaunchPreview,
    responses=_error_responses(status.HTTP_409_CONFLICT),
)
def preview_activation_launch(
    payload: ActivationLaunchDraft,
    db: Session = Depends(get_db),
    user: legacy.User = Depends(get_current_user),
):
    return activation_launch_preview(db, owner_id=user.id, draft=payload)


@router.post(
    "/activation/launch",
    response_model=JobAccepted,
    status_code=status.HTTP_202_ACCEPTED,
    responses=_error_responses(status.HTTP_409_CONFLICT),
)
def launch_activation(
    payload: ActivationLaunchRequest,
    idempotency_key: str = Header(..., alias="Idempotency-Key", min_length=8, max_length=255),
    db: Session = Depends(get_db),
    user: legacy.User = Depends(get_current_user),
):
    draft = ActivationLaunchDraft.model_validate(payload.model_dump(exclude={"preview_checksum", "human_confirmed"}))
    job_payload = {
        "draft": draft.model_dump(mode="json"),
        "preview_checksum": payload.preview_checksum,
        "human_confirmed": True,
        "actor_user_id": user.id,
    }
    existing_job = db.query(models.AutomationJob).filter_by(
        idempotency_key=idempotency_key
    ).first()
    if existing_job is not None:
        try:
            job = enqueue_job(
                db,
                owner_id=user.id,
                job_type="activation.launch",
                idempotency_key=idempotency_key,
                queue="campaign",
                payload=job_payload,
            )
        except ValueError as exc:
            raise HTTPException(
                status_code=409,
                detail={"code": "IDEMPOTENCY_KEY_CONFLICT", "message": str(exc)},
            ) from exc
        return JobAccepted(job_id=job.id, status=job.status)

    preview = activation_launch_preview(db, owner_id=user.id, draft=draft)
    if preview.checksum != payload.preview_checksum:
        raise HTTPException(
            status_code=409,
            detail={"code": "ACTIVATION_PREVIEW_STALE", "message": "启动预览已过期，请重新检查"},
        )
    if preview.blockers:
        raise HTTPException(
            status_code=409,
            detail={"code": "ACTIVATION_BLOCKED", "message": preview.blockers[0], "blockers": preview.blockers},
        )
    job_payload["preview_checksum"] = preview.checksum
    try:
        job = enqueue_job(
            db,
            owner_id=user.id,
            job_type="activation.launch",
            idempotency_key=idempotency_key,
            queue="campaign",
            payload=job_payload,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=409,
            detail={"code": "IDEMPOTENCY_KEY_CONFLICT", "message": str(exc)},
        ) from exc
    add_audit(
        db,
        owner_id=user.id,
        actor_user_id=user.id,
        action="activation.launch_requested",
        entity_type="acquisition_run",
        entity_id=payload.run_id,
        correlation_id=idempotency_key,
        after={"candidate_count": len(payload.candidate_ids), "review_required": True},
    )
    _commit(db)
    return JobAccepted(job_id=job.id, status=job.status)


@router.post(
    "/companies",
    response_model=CompanyRead,
    status_code=status.HTTP_201_CREATED,
    responses=_error_responses(status.HTTP_409_CONFLICT),
)
def create_company(
    payload: CompanyCreate,
    db: Session = Depends(get_db),
    user: legacy.User = Depends(get_current_user),
):
    domain_source = payload.domain or payload.website
    normalized_domain = normalize_domain(domain_source)
    if domain_source and not is_usable_company_domain(domain_source):
        raise HTTPException(
            status_code=422,
            detail={"code": "INVALID_COMPANY_DOMAIN", "message": "Company domain or website is not a usable hostname"},
        )
    company = models.Company(
        owner_id=user.id,
        name=payload.name.strip(),
        normalized_domain=normalized_domain,
        website=payload.website,
        country=payload.country,
        region=payload.region,
        industry=payload.industry,
        timezone=payload.timezone,
    )
    db.add(company)
    _flush(db)
    add_audit(db, owner_id=user.id, actor_user_id=user.id, action="company.created", entity_type="company", entity_id=company.id)
    _commit(db)
    db.refresh(company)
    return company


@router.patch(
    "/companies/{company_id}",
    response_model=CompanyRead,
    responses=_error_responses(status.HTTP_404_NOT_FOUND, status.HTTP_409_CONFLICT),
)
def update_company(
    company_id: int,
    payload: CompanyUpdate,
    db: Session = Depends(get_db),
    user: legacy.User = Depends(get_current_user),
):
    company = _owned(db, models.Company, company_id, user)
    changes = payload.model_dump(exclude_unset=True)
    before = {
        key: getattr(company, "normalized_domain" if key == "domain" else key)
        for key in changes
    }

    if "name" in changes:
        name = (changes.pop("name") or "").strip()
        if not name:
            raise HTTPException(status_code=422, detail="Company name cannot be empty")
        company.name = name

    domain_was_supplied = "domain" in changes
    supplied_domain = changes.pop("domain", None)
    if domain_was_supplied:
        domain_source = supplied_domain or changes.get("website") or company.website
        if domain_source and not is_usable_company_domain(domain_source):
            raise HTTPException(
                status_code=422,
                detail={
                    "code": "INVALID_COMPANY_DOMAIN",
                    "message": "Company domain or website is not a usable hostname",
                },
            )
        company.normalized_domain = normalize_domain(domain_source)
    elif "website" in changes and changes["website"]:
        if not is_usable_company_domain(changes["website"]):
            raise HTTPException(
                status_code=422,
                detail={
                    "code": "INVALID_COMPANY_DOMAIN",
                    "message": "Company website is not a usable hostname",
                },
            )
        if not company.normalized_domain:
            company.normalized_domain = normalize_domain(changes["website"])

    for field, value in changes.items():
        setattr(company, field, value.strip() if isinstance(value, str) else value)

    after = {
        key: getattr(company, "normalized_domain" if key == "domain" else key)
        for key in payload.model_fields_set
    }
    add_audit(
        db,
        owner_id=user.id,
        actor_user_id=user.id,
        action="company.updated",
        entity_type="company",
        entity_id=company.id,
        before=before,
        after=after,
    )
    _commit(db)
    db.refresh(company)
    return company


@router.get(
    "/companies/{company_id}/workspace",
    response_model=CompanyWorkspaceRead,
    responses=_error_responses(status.HTTP_404_NOT_FOUND),
)
def get_company_workspace(
    company_id: int,
    db: Session = Depends(get_db),
    user: legacy.User = Depends(get_current_user),
):
    company = _owned(db, models.Company, company_id, user, include_archived=True)
    contacts = db.query(models.Contact).filter(
        models.Contact.owner_id == user.id,
        models.Contact.company_id == company.id,
        models.Contact.archived_at.is_(None),
    ).order_by(models.Contact.updated_at.desc(), models.Contact.id.asc()).all()
    evidence = db.query(models.EvidenceSnapshot).filter(
        models.EvidenceSnapshot.owner_id == user.id,
        models.EvidenceSnapshot.company_id == company.id,
        models.EvidenceSnapshot.archived_at.is_(None),
    ).order_by(
        models.EvidenceSnapshot.captured_at.desc(),
        models.EvidenceSnapshot.version.desc(),
        models.EvidenceSnapshot.id.desc(),
    ).limit(100).all()

    enrollment_count = db.query(func.count(models.Enrollment.id)).filter(
        models.Enrollment.owner_id == user.id,
        models.Enrollment.company_id == company.id,
    ).scalar() or 0
    sent_count, last_sent_at = db.query(
        func.count(models.OutreachAttempt.id),
        func.max(models.OutreachAttempt.sent_at),
    ).join(
        models.Enrollment,
        models.Enrollment.id == models.OutreachAttempt.enrollment_id,
    ).filter(
        models.OutreachAttempt.owner_id == user.id,
        models.Enrollment.company_id == company.id,
        models.OutreachAttempt.sent_at.is_not(None),
    ).one()
    reply_count, last_reply_at = db.query(
        func.count(models.Conversation.id),
        func.max(models.Conversation.last_message_at),
    ).filter(
        models.Conversation.owner_id == user.id,
        models.Conversation.company_id == company.id,
        models.Conversation.latest_reply_body.is_not(None),
    ).one()
    last_contact_at = max(
        (value for value in (last_sent_at, last_reply_at) if value is not None),
        default=None,
    )
    return CompanyWorkspaceRead(
        company=CompanyRead.model_validate(company),
        contacts=[_contact_read(db, contact) for contact in contacts],
        evidence_snapshots=[EvidenceSnapshotRead.model_validate(row) for row in evidence],
        outreach=CompanyOutreachSummary(
            enrollment_count=enrollment_count,
            sent_count=sent_count or 0,
            reply_count=reply_count or 0,
            last_contact_at=last_contact_at,
        ),
    )


@router.get(
    "/companies/{company_id}",
    response_model=CompanyRead,
    responses=_error_responses(status.HTTP_404_NOT_FOUND),
)
def get_company(company_id: int, db: Session = Depends(get_db), user: legacy.User = Depends(get_current_user)):
    return _owned(db, models.Company, company_id, user, include_archived=True)


@router.delete(
    "/companies/{company_id}",
    response_model=ArchiveResult,
    responses=_error_responses(status.HTTP_404_NOT_FOUND, status.HTTP_409_CONFLICT),
)
def archive_company(company_id: int, db: Session = Depends(get_db), user: legacy.User = Depends(get_current_user)):
    company = _owned(db, models.Company, company_id, user)
    company.archived_at = utcnow()
    enrollment_ids = [row[0] for row in db.query(models.Enrollment.id).filter(
        models.Enrollment.company_id == company.id,
        models.Enrollment.status.in_((EnrollmentStatus.SCHEDULED, EnrollmentStatus.ACTIVE)),
    ).all()]
    if enrollment_ids:
        db.query(models.Enrollment).filter(models.Enrollment.id.in_(enrollment_ids)).update(
            {"status": EnrollmentStatus.PAUSED, "paused_reason": "company_archived", "paused_at": utcnow()},
            synchronize_session=False,
        )
        db.query(models.OutreachAttempt).filter(
            models.OutreachAttempt.enrollment_id.in_(enrollment_ids),
            models.OutreachAttempt.status == AttemptStatus.QUEUED,
        ).update({"status": AttemptStatus.CANCELLED, "last_error": "company_archived"}, synchronize_session=False)
        db.query(models.AutomationJob).filter(
            models.AutomationJob.enrollment_id.in_(enrollment_ids),
            models.AutomationJob.status.in_((JobStatus.PENDING, JobStatus.RETRY)),
        ).update(
            {"status": JobStatus.CANCELLED, "completed_at": utcnow(), "last_error": "company_archived"},
            synchronize_session=False,
        )
    add_audit(db, owner_id=user.id, actor_user_id=user.id, action="company.archived", entity_type="company", entity_id=company.id)
    _commit(db)
    return {"id": company.id, "archived": True}


@router.get("/contacts", response_model=list[ContactRead])
def list_contacts(
    company_id: Optional[int] = None,
    include_archived: bool = False,
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
    user: legacy.User = Depends(get_current_user),
):
    query = db.query(models.Contact).filter(models.Contact.owner_id == user.id)
    if company_id:
        query = query.filter(models.Contact.company_id == company_id)
    if not include_archived:
        query = query.filter(models.Contact.archived_at.is_(None))
    return [
        _contact_read(db, contact)
        for contact in query.order_by(models.Contact.updated_at.desc(), models.Contact.id.desc()).offset(offset).limit(limit).all()
    ]


@router.post(
    "/contacts",
    response_model=ContactRead,
    status_code=status.HTTP_201_CREATED,
    responses=_error_responses(status.HTTP_404_NOT_FOUND, status.HTTP_409_CONFLICT),
)
def create_contact(
    payload: ContactCreate,
    db: Session = Depends(get_db),
    user: legacy.User = Depends(get_current_user),
):
    company = _owned(db, models.Company, payload.company_id, user)
    full_name = (payload.full_name or f"{payload.first_name or ''} {payload.last_name or ''}").strip()
    contact = models.Contact(
        owner_id=user.id,
        company_id=company.id,
        first_name=payload.first_name,
        last_name=payload.last_name,
        full_name=full_name,
        job_title=payload.job_title,
        department=payload.department,
        timezone=payload.timezone,
        locale=payload.locale,
    )
    db.add(contact)
    _flush(db)
    for point in payload.contact_points:
        db.add(
            models.ContactPoint(
                owner_id=user.id,
                company_id=company.id,
                contact_id=contact.id,
                channel=point.channel,
                value=point.value.strip(),
                normalized_value=normalize_contact_point(point.channel, point.value),
                verification_status=point.verification_status,
                availability_status=point.availability_status,
                is_primary=point.is_primary,
            )
        )
    add_audit(db, owner_id=user.id, actor_user_id=user.id, action="contact.created", entity_type="contact", entity_id=contact.id)
    _commit(db)
    db.refresh(contact)
    return _contact_read(db, contact)


@router.patch(
    "/contacts/{contact_id}",
    response_model=ContactRead,
    responses=_error_responses(status.HTTP_404_NOT_FOUND, status.HTTP_409_CONFLICT),
)
def update_contact(
    contact_id: int,
    payload: ContactUpdate,
    db: Session = Depends(get_db),
    user: legacy.User = Depends(get_current_user),
):
    contact = _owned(db, models.Contact, contact_id, user)
    changes = payload.model_dump(exclude_unset=True)
    before = {key: getattr(contact, key) for key in changes}
    for field, value in changes.items():
        cleaned = value.strip() if isinstance(value, str) else value
        if field == "full_name" and not cleaned:
            raise HTTPException(status_code=422, detail="Contact full_name cannot be empty")
        setattr(contact, field, cleaned)
    add_audit(
        db,
        owner_id=user.id,
        actor_user_id=user.id,
        action="contact.updated",
        entity_type="contact",
        entity_id=contact.id,
        before=before,
        after={key: getattr(contact, key) for key in changes},
    )
    _commit(db)
    db.refresh(contact)
    return _contact_read(db, contact)


@router.get(
    "/contacts/{contact_id}",
    response_model=ContactRead,
    responses=_error_responses(status.HTTP_404_NOT_FOUND),
)
def get_contact(contact_id: int, db: Session = Depends(get_db), user: legacy.User = Depends(get_current_user)):
    return _contact_read(db, _owned(db, models.Contact, contact_id, user, include_archived=True))


@router.delete(
    "/contacts/{contact_id}",
    response_model=ArchiveResult,
    responses=_error_responses(status.HTTP_404_NOT_FOUND, status.HTTP_409_CONFLICT),
)
def archive_contact(contact_id: int, db: Session = Depends(get_db), user: legacy.User = Depends(get_current_user)):
    contact = _owned(db, models.Contact, contact_id, user)
    contact.archived_at = utcnow()
    db.query(models.ContactPoint).filter_by(contact_id=contact.id).update({"archived_at": utcnow()}, synchronize_session=False)
    enrollment_ids = [row[0] for row in db.query(models.Enrollment.id).filter(
        models.Enrollment.contact_id == contact.id,
        models.Enrollment.status.in_((EnrollmentStatus.SCHEDULED, EnrollmentStatus.ACTIVE)),
    ).all()]
    if enrollment_ids:
        db.query(models.Enrollment).filter(models.Enrollment.id.in_(enrollment_ids)).update(
            {"status": EnrollmentStatus.PAUSED, "paused_reason": "contact_archived", "paused_at": utcnow()},
            synchronize_session=False,
        )
        db.query(models.OutreachAttempt).filter(
            models.OutreachAttempt.enrollment_id.in_(enrollment_ids),
            models.OutreachAttempt.status == AttemptStatus.QUEUED,
        ).update({"status": AttemptStatus.CANCELLED, "last_error": "contact_archived"}, synchronize_session=False)
        db.query(models.AutomationJob).filter(
            models.AutomationJob.enrollment_id.in_(enrollment_ids),
            models.AutomationJob.status.in_((JobStatus.PENDING, JobStatus.RETRY)),
        ).update(
            {"status": JobStatus.CANCELLED, "completed_at": utcnow(), "last_error": "contact_archived"},
            synchronize_session=False,
        )
    add_audit(db, owner_id=user.id, actor_user_id=user.id, action="contact.archived", entity_type="contact", entity_id=contact.id)
    _commit(db)
    return {"id": contact.id, "archived": True}


@router.get("/lists", response_model=list[AudienceListRead])
def list_audience_lists(db: Session = Depends(get_db), user: legacy.User = Depends(get_current_user)):
    return db.query(models.AudienceList).filter_by(owner_id=user.id, archived_at=None).order_by(models.AudienceList.name).all()


@router.post(
    "/lists",
    response_model=AudienceListRead,
    status_code=status.HTTP_201_CREATED,
    responses=_error_responses(status.HTTP_409_CONFLICT),
)
def create_audience_list(payload: AudienceListCreate, db: Session = Depends(get_db), user: legacy.User = Depends(get_current_user)):
    row = models.AudienceList(owner_id=user.id, name=payload.name.strip(), description=payload.description)
    db.add(row)
    _commit(db)
    db.refresh(row)
    return row


@router.post(
    "/lists/{list_id}/memberships",
    response_model=MembershipRead,
    status_code=status.HTTP_201_CREATED,
    responses=_error_responses(status.HTTP_404_NOT_FOUND, status.HTTP_409_CONFLICT),
)
def create_membership(
    list_id: int,
    payload: MembershipCreate,
    db: Session = Depends(get_db),
    user: legacy.User = Depends(get_current_user),
):
    audience_list = _owned(db, models.AudienceList, list_id, user)
    if payload.company_id:
        _owned(db, models.Company, payload.company_id, user)
    if payload.contact_id:
        _owned(db, models.Contact, payload.contact_id, user)
    row = models.ListMembership(
        owner_id=user.id,
        audience_list_id=audience_list.id,
        company_id=payload.company_id,
        contact_id=payload.contact_id,
        added_by_user_id=user.id,
    )
    db.add(row)
    _commit(db)
    return {"id": row.id, "list_id": row.audience_list_id, "company_id": row.company_id, "contact_id": row.contact_id}


@router.get("/campaigns", response_model=list[CampaignRead])
def list_campaigns(include_archived: bool = False, db: Session = Depends(get_db), user: legacy.User = Depends(get_current_user)):
    query = db.query(models.Campaign).filter(models.Campaign.owner_id == user.id)
    if not include_archived:
        query = query.filter(models.Campaign.archived_at.is_(None))
    return query.order_by(models.Campaign.updated_at.desc()).all()


@router.post(
    "/campaigns",
    response_model=CampaignRead,
    status_code=status.HTTP_201_CREATED,
    responses=_error_responses(status.HTTP_409_CONFLICT),
)
def create_campaign(payload: CampaignCreate, db: Session = Depends(get_db), user: legacy.User = Depends(get_current_user)):
    row = models.Campaign(
        owner_id=user.id,
        name=payload.name.strip(),
        description=payload.description,
        run_mode=payload.run_mode,
        priority=payload.priority,
    )
    db.add(row)
    _flush(db)
    add_audit(db, owner_id=user.id, actor_user_id=user.id, action="campaign.created", entity_type="campaign", entity_id=row.id)
    _commit(db)
    db.refresh(row)
    return row


@router.delete(
    "/campaigns/{campaign_id}",
    response_model=ArchiveResult,
    responses=_error_responses(status.HTTP_404_NOT_FOUND, status.HTTP_409_CONFLICT),
)
def archive_campaign(campaign_id: int, db: Session = Depends(get_db), user: legacy.User = Depends(get_current_user)):
    campaign = _owned(db, models.Campaign, campaign_id, user)
    campaign.archived_at = utcnow()
    campaign.lifecycle = CampaignLifecycle.ARCHIVED
    enrollment_ids = [row[0] for row in db.query(models.Enrollment.id).filter(
        models.Enrollment.campaign_id == campaign.id,
        models.Enrollment.status.in_((EnrollmentStatus.SCHEDULED, EnrollmentStatus.ACTIVE)),
    ).all()]
    if enrollment_ids:
        db.query(models.Enrollment).filter(models.Enrollment.id.in_(enrollment_ids)).update(
            {"status": EnrollmentStatus.PAUSED, "paused_reason": "campaign_archived", "paused_at": utcnow()},
            synchronize_session=False,
        )
        db.query(models.OutreachAttempt).filter(
            models.OutreachAttempt.enrollment_id.in_(enrollment_ids),
            models.OutreachAttempt.status == AttemptStatus.QUEUED,
        ).update({"status": AttemptStatus.CANCELLED, "last_error": "campaign_archived"}, synchronize_session=False)
    db.query(models.AutomationJob).filter(
        models.AutomationJob.campaign_id == campaign.id,
        models.AutomationJob.status.in_((JobStatus.PENDING, JobStatus.RETRY)),
    ).update(
        {"status": JobStatus.CANCELLED, "completed_at": utcnow(), "last_error": "campaign_archived"},
        synchronize_session=False,
    )
    add_audit(db, owner_id=user.id, actor_user_id=user.id, action="campaign.archived", entity_type="campaign", entity_id=campaign.id)
    _commit(db)
    return {"id": campaign.id, "archived": True}


@router.get(
    "/campaigns/{campaign_id}/revisions",
    response_model=list[CampaignRevisionRead],
    responses=_error_responses(status.HTTP_404_NOT_FOUND),
)
def list_revisions(campaign_id: int, db: Session = Depends(get_db), user: legacy.User = Depends(get_current_user)):
    campaign = _owned(db, models.Campaign, campaign_id, user, include_archived=True)
    revisions = db.query(models.CampaignRevision).filter_by(campaign_id=campaign.id).order_by(models.CampaignRevision.revision_number.desc()).all()
    return [_revision_read(db, revision) for revision in revisions]


@router.post(
    "/campaigns/{campaign_id}/revisions",
    response_model=CampaignRevisionRead,
    status_code=status.HTTP_201_CREATED,
    responses=_error_responses(status.HTTP_404_NOT_FOUND, status.HTTP_409_CONFLICT),
)
def add_revision(
    campaign_id: int,
    payload: CampaignRevisionCreate,
    db: Session = Depends(get_db),
    user: legacy.User = Depends(get_current_user),
):
    campaign = _owned(db, models.Campaign, campaign_id, user)
    unsupported_channels = sorted({
        step.channel.value
        for step in payload.sequence_steps
        if step.channel != Channel.EMAIL
    })
    if environment() in {"staging", "production"} and unsupported_channels:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "CHANNEL_UNAVAILABLE",
                "message": (
                    "This release supports production Email only; unavailable channels: "
                    + ", ".join(unsupported_channels)
                ),
            },
        )
    try:
        revision = create_campaign_revision(
            db, campaign=campaign, actor_user_id=user.id, data=payload
        )
    except IntegrityError as exc:
        _raise_integrity_conflict(db, exc)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "CHANNEL_ACCOUNT_BINDING_INVALID", "message": str(exc)},
        ) from exc
    _commit(db)
    db.refresh(revision)
    return _revision_read(db, revision)


@router.get(
    "/campaigns/{campaign_id}/revisions/{revision_id}/diff",
    response_model=CampaignRevisionDiff,
    responses=_error_responses(status.HTTP_404_NOT_FOUND),
)
def get_revision_diff(
    campaign_id: int,
    revision_id: int,
    db: Session = Depends(get_db),
    user: legacy.User = Depends(get_current_user),
):
    campaign = _owned(db, models.Campaign, campaign_id, user, include_archived=True)
    revision = _owned(db, models.CampaignRevision, revision_id, user, include_archived=True)
    if revision.campaign_id != campaign.id:
        raise HTTPException(status_code=404, detail="CampaignRevision not found")
    base, diff = campaign_revision_diff(db, campaign=campaign, proposed=revision)
    base_revision_id = base.id if base and base.id != revision.id else None
    return CampaignRevisionDiff(
        campaign_id=campaign.id,
        base_revision_id=base_revision_id,
        proposed_revision_id=revision.id,
        diff=diff,
        checksum=campaign_revision_diff_checksum(
            campaign_id=campaign.id,
            base_revision_id=base_revision_id,
            proposed_revision_id=revision.id,
            diff=diff,
        ),
    )


@router.post(
    "/campaigns/{campaign_id}/revisions/{revision_id}/publish",
    response_model=CampaignRevisionRead,
    responses=_error_responses(status.HTTP_404_NOT_FOUND, status.HTTP_409_CONFLICT),
)
def publish_revision(
    campaign_id: int,
    revision_id: int,
    payload: CampaignRevisionPublish,
    idempotency_key: str = Header(..., alias="Idempotency-Key", min_length=8, max_length=255),
    db: Session = Depends(get_db),
    user: legacy.User = Depends(get_current_user),
):
    campaign = _owned(db, models.Campaign, campaign_id, user)
    revision = _owned(db, models.CampaignRevision, revision_id, user)
    try:
        publish_campaign_revision(
            db,
            campaign=campaign,
            revision=revision,
            actor_user_id=user.id,
            idempotency_key=idempotency_key,
            base_revision_id=payload.base_revision_id,
            reviewed_diff_checksum=payload.reviewed_diff_checksum,
            human_confirmed=payload.human_confirmed,
        )
    except IntegrityError as exc:
        _raise_integrity_conflict(db, exc)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail={"code": "REVISION_PUBLISH_REJECTED", "message": str(exc)}) from exc
    _commit(db)
    db.refresh(revision)
    return _revision_read(db, revision)


@router.get(
    "/campaigns/{campaign_id}/readiness",
    response_model=CampaignReadiness,
    responses=_error_responses(status.HTTP_404_NOT_FOUND),
)
def get_readiness(campaign_id: int, db: Session = Depends(get_db), user: legacy.User = Depends(get_current_user)):
    return campaign_readiness(db, _owned(db, models.Campaign, campaign_id, user))


@router.get(
    "/campaigns/{campaign_id}/enrollments",
    response_model=list[EnrollmentRead],
    responses=_error_responses(status.HTTP_404_NOT_FOUND),
)
def list_enrollments(campaign_id: int, db: Session = Depends(get_db), user: legacy.User = Depends(get_current_user)):
    campaign = _owned(db, models.Campaign, campaign_id, user, include_archived=True)
    return db.query(models.Enrollment).filter_by(campaign_id=campaign.id).order_by(models.Enrollment.id.desc()).all()


@router.post(
    "/campaigns/{campaign_id}/enrollments",
    response_model=JobAccepted,
    status_code=status.HTTP_202_ACCEPTED,
    responses=_error_responses(status.HTTP_404_NOT_FOUND, status.HTTP_409_CONFLICT),
)
def enroll_contact(
    campaign_id: int,
    payload: EnrollmentCreate,
    idempotency_key: str = Header(..., alias="Idempotency-Key", min_length=8, max_length=255),
    db: Session = Depends(get_db),
    user: legacy.User = Depends(get_current_user),
):
    campaign = _owned(db, models.Campaign, campaign_id, user)
    contact = _owned(db, models.Contact, payload.contact_id, user)
    try:
        _, job = create_enrollment(
            db,
            campaign=campaign,
            contact=contact,
            idempotency_key=idempotency_key,
            scheduled_at=payload.scheduled_at,
            actor_user_id=user.id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail={"code": "ENROLLMENT_BLOCKED", "message": str(exc)}) from exc
    _commit(db)
    return JobAccepted(job_id=job.id, status=job.status)


def _campaign_command(
    *,
    action: str,
    campaign_id: int,
    payload: CommandOptions,
    idempotency_key: str,
    db: Session,
    user: legacy.User,
) -> JobAccepted:
    campaign = _owned(db, models.Campaign, campaign_id, user)
    job_payload = {"campaign_id": campaign.id, "confirm_warnings": payload.confirm_warnings}
    existing_job = db.query(models.AutomationJob).filter_by(idempotency_key=idempotency_key).first()
    if existing_job:
        try:
            job = enqueue_job(
                db,
                owner_id=user.id,
                job_type=f"campaign.{action}",
                idempotency_key=idempotency_key,
                queue="campaign",
                campaign_id=campaign.id,
                priority=campaign.priority,
                payload=job_payload,
            )
        except ValueError as exc:
            raise HTTPException(status_code=409, detail={"code": "IDEMPOTENCY_KEY_CONFLICT", "message": str(exc)}) from exc
        return JobAccepted(job_id=job.id, status=job.status)
    try:
        validate_campaign_command(campaign, action)
    except ValueError as exc:
        raise HTTPException(
            status_code=409,
            detail={"code": "INVALID_CAMPAIGN_TRANSITION", "message": str(exc)},
        ) from exc
    if action == "start":
        readiness = campaign_readiness(db, campaign)
        if readiness.blockers:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "CAMPAIGN_NOT_READY",
                    "message": "Campaign readiness blockers must be resolved",
                    "blockers": [item.model_dump(mode="json") for item in readiness.blockers],
                },
            )
        if readiness.warnings and not payload.confirm_warnings:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "CAMPAIGN_WARNINGS_REQUIRE_CONFIRMATION",
                    "warnings": [item.model_dump(mode="json") for item in readiness.warnings],
                },
            )
    try:
        job = enqueue_job(
            db,
            owner_id=user.id,
            job_type=f"campaign.{action}",
            idempotency_key=idempotency_key,
            queue="campaign",
            campaign_id=campaign.id,
            priority=campaign.priority,
            payload=job_payload,
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail={"code": "IDEMPOTENCY_KEY_CONFLICT", "message": str(exc)}) from exc
    add_audit(
        db,
        owner_id=user.id,
        actor_user_id=user.id,
        action=f"campaign.{action}_requested",
        entity_type="campaign",
        entity_id=campaign.id,
        correlation_id=idempotency_key,
        metadata={"warnings_confirmed": payload.confirm_warnings},
    )
    _commit(db)
    return JobAccepted(job_id=job.id, status=job.status)


@router.post(
    "/campaigns/{campaign_id}/start",
    response_model=JobAccepted,
    status_code=status.HTTP_202_ACCEPTED,
    responses=_error_responses(status.HTTP_404_NOT_FOUND, status.HTTP_409_CONFLICT),
)
def start_campaign(campaign_id: int, payload: CommandOptions, idempotency_key: str = Header(..., alias="Idempotency-Key", min_length=8, max_length=255), db: Session = Depends(get_db), user: legacy.User = Depends(get_current_user)):
    return _campaign_command(action="start", campaign_id=campaign_id, payload=payload, idempotency_key=idempotency_key, db=db, user=user)


@router.post(
    "/campaigns/{campaign_id}/pause",
    response_model=JobAccepted,
    status_code=status.HTTP_202_ACCEPTED,
    responses=_error_responses(status.HTTP_404_NOT_FOUND, status.HTTP_409_CONFLICT),
)
def pause_campaign(campaign_id: int, payload: CommandOptions, idempotency_key: str = Header(..., alias="Idempotency-Key", min_length=8, max_length=255), db: Session = Depends(get_db), user: legacy.User = Depends(get_current_user)):
    return _campaign_command(action="pause", campaign_id=campaign_id, payload=payload, idempotency_key=idempotency_key, db=db, user=user)


@router.post(
    "/campaigns/{campaign_id}/complete",
    response_model=JobAccepted,
    status_code=status.HTTP_202_ACCEPTED,
    responses=_error_responses(status.HTTP_404_NOT_FOUND, status.HTTP_409_CONFLICT),
)
def complete_campaign(campaign_id: int, payload: CommandOptions, idempotency_key: str = Header(..., alias="Idempotency-Key", min_length=8, max_length=255), db: Session = Depends(get_db), user: legacy.User = Depends(get_current_user)):
    return _campaign_command(action="complete", campaign_id=campaign_id, payload=payload, idempotency_key=idempotency_key, db=db, user=user)


def _route_review_http_error(exc: RouteReviewError) -> HTTPException:
    return HTTPException(
        status_code=exc.status_code,
        detail={"code": exc.code, "message": exc.message},
    )


@router.post(
    "/route-proposals",
    response_model=RouteProposalRead,
    status_code=status.HTTP_201_CREATED,
    responses=_error_responses(status.HTTP_404_NOT_FOUND, status.HTTP_409_CONFLICT),
)
def create_route_proposal_endpoint(
    payload: RouteProposalCreate,
    db: Session = Depends(get_db),
    user: legacy.User = Depends(get_current_user),
):
    try:
        proposal = create_route_proposal(
            db,
            owner_id=user.id,
            actor_user_id=user.id,
            payload=payload,
        )
        return proposal_read(db, proposal)
    except RouteReviewError as exc:
        db.rollback()
        raise _route_review_http_error(exc) from exc


@router.get(
    "/route-proposals/{proposal_id}",
    response_model=RouteProposalRead,
    responses=_error_responses(status.HTTP_404_NOT_FOUND),
)
def get_route_proposal(
    proposal_id: int,
    db: Session = Depends(get_db),
    user: legacy.User = Depends(get_current_user),
):
    proposal = _owned(db, models.RouteProposal, proposal_id, user)
    return proposal_read(db, proposal)


@router.get("/route-proposals", response_model=list[RouteProposalRead])
def list_route_proposals(
    proposal_status: Optional[RouteProposalStatus] = Query(None, alias="status"),
    limit: int = Query(100, ge=1, le=500),
    db: Session = Depends(get_db),
    user: legacy.User = Depends(get_current_user),
):
    query = db.query(models.RouteProposal).filter_by(owner_id=user.id)
    if proposal_status is not None:
        query = query.filter(models.RouteProposal.status == proposal_status)
    proposals = query.order_by(models.RouteProposal.created_at.desc()).limit(limit).all()
    return [proposal_read(db, proposal) for proposal in proposals]


@router.post(
    "/review-batches/preview",
    response_model=ReviewBatchRead,
    responses=_error_responses(status.HTTP_404_NOT_FOUND, status.HTTP_409_CONFLICT),
)
def preview_review_batch(
    payload: ReviewBatchPreviewRequest,
    db: Session = Depends(get_db),
    user: legacy.User = Depends(get_current_user),
):
    try:
        batch = preview_batch(
            db,
            owner_id=user.id,
            actor_user_id=user.id,
            payload=payload,
        )
        return batch_read(db, batch)
    except RouteReviewError as exc:
        db.rollback()
        raise _route_review_http_error(exc) from exc


@router.get(
    "/review-batches/{batch_id}",
    response_model=ReviewBatchRead,
    responses=_error_responses(status.HTTP_404_NOT_FOUND),
)
def get_review_batch(
    batch_id: int,
    db: Session = Depends(get_db),
    user: legacy.User = Depends(get_current_user),
):
    return batch_read(db, _owned(db, models.ReviewBatch, batch_id, user))


@router.get("/review-batches", response_model=list[ReviewBatchRead])
def list_review_batches(
    batch_status: Optional[ReviewBatchStatus] = Query(None, alias="status"),
    limit: int = Query(100, ge=1, le=500),
    db: Session = Depends(get_db),
    user: legacy.User = Depends(get_current_user),
):
    query = db.query(models.ReviewBatch).filter_by(owner_id=user.id)
    if batch_status is not None:
        query = query.filter(models.ReviewBatch.status == batch_status)
    batches = query.order_by(models.ReviewBatch.created_at.desc()).limit(limit).all()
    return [batch_read(db, batch) for batch in batches]


@router.patch(
    "/review-batches/{batch_id}/items/{item_id}",
    response_model=ReviewBatchRead,
    responses=_error_responses(status.HTTP_404_NOT_FOUND, status.HTTP_409_CONFLICT),
)
def edit_review_batch_item(
    batch_id: int,
    item_id: int,
    payload: ReviewBatchItemUpdate,
    db: Session = Depends(get_db),
    user: legacy.User = Depends(get_current_user),
):
    try:
        batch = update_batch_item(
            db,
            owner_id=user.id,
            actor_user_id=user.id,
            batch_id=batch_id,
            item_id=item_id,
            payload=payload,
        )
        return batch_read(db, batch)
    except RouteReviewError as exc:
        db.rollback()
        raise _route_review_http_error(exc) from exc


@router.post(
    "/review-batches/{batch_id}/approve",
    response_model=ReviewBatchRead,
    responses=_error_responses(status.HTTP_404_NOT_FOUND, status.HTTP_409_CONFLICT),
)
def approve_review_batch(
    batch_id: int,
    payload: ReviewBatchApprove,
    db: Session = Depends(get_db),
    user: legacy.User = Depends(get_current_user),
):
    try:
        batch = approve_batch(
            db,
            owner_id=user.id,
            actor_user_id=user.id,
            batch_id=batch_id,
            preview_checksum=payload.preview_checksum,
            approval_id=payload.approval_id,
            human_confirmed=payload.human_confirmed,
        )
        return batch_read(db, batch)
    except RouteReviewError as exc:
        db.rollback()
        raise _route_review_http_error(exc) from exc


@router.post(
    "/review-batches/{batch_id}/reject",
    response_model=ReviewBatchRead,
    responses=_error_responses(status.HTTP_404_NOT_FOUND, status.HTTP_409_CONFLICT),
)
def reject_review_batch(
    batch_id: int,
    payload: ReviewBatchReject,
    db: Session = Depends(get_db),
    user: legacy.User = Depends(get_current_user),
):
    try:
        batch = reject_batch(
            db,
            owner_id=user.id,
            actor_user_id=user.id,
            batch_id=batch_id,
            reason=payload.reason,
        )
        return batch_read(db, batch)
    except RouteReviewError as exc:
        db.rollback()
        raise _route_review_http_error(exc) from exc


@router.post(
    "/whatsapp-consents",
    response_model=WhatsAppConsentRead,
    status_code=status.HTTP_201_CREATED,
    responses=_error_responses(status.HTTP_404_NOT_FOUND, status.HTTP_409_CONFLICT),
)
def create_whatsapp_consent(
    payload: WhatsAppConsentCreate,
    db: Session = Depends(get_db),
    user: legacy.User = Depends(get_current_user),
):
    contact_point = _owned(db, models.ContactPoint, payload.contact_point_id, user)
    if contact_point.channel != Channel.WHATSAPP:
        raise HTTPException(status_code=409, detail={"code": "WHATSAPP_CONTACT_POINT_REQUIRED"})
    existing = db.query(models.WhatsAppConsent).filter_by(
        owner_id=user.id,
        idempotency_key=payload.idempotency_key,
    ).first()
    if existing is not None:
        return existing
    if payload.expires_at is not None and payload.expires_at <= payload.granted_at:
        raise HTTPException(status_code=422, detail={"code": "CONSENT_EXPIRY_INVALID"})
    consent = models.WhatsAppConsent(
        owner_id=user.id,
        contact_id=contact_point.contact_id,
        contact_point_id=contact_point.id,
        captured_by_user_id=user.id,
        **payload.model_dump(exclude={"contact_point_id"}),
    )
    db.add(consent)
    add_audit(
        db,
        owner_id=user.id,
        actor_user_id=user.id,
        action="whatsapp_consent.granted",
        entity_type="whatsapp_consent",
        entity_id=None,
        after={"contact_point_id": contact_point.id, "source": payload.source},
    )
    _commit(db)
    db.refresh(consent)
    return consent


@router.get("/whatsapp-consents", response_model=list[WhatsAppConsentRead])
def list_whatsapp_consents(
    contact_point_id: Optional[int] = Query(None),
    db: Session = Depends(get_db),
    user: legacy.User = Depends(get_current_user),
):
    query = db.query(models.WhatsAppConsent).filter_by(owner_id=user.id)
    if contact_point_id is not None:
        query = query.filter(models.WhatsAppConsent.contact_point_id == contact_point_id)
    return query.order_by(models.WhatsAppConsent.granted_at.desc()).all()


@router.post(
    "/whatsapp-consents/{consent_id}/revoke",
    response_model=WhatsAppConsentRead,
    responses=_error_responses(status.HTTP_404_NOT_FOUND, status.HTTP_409_CONFLICT),
)
def revoke_whatsapp_consent(
    consent_id: int,
    payload: WhatsAppConsentRevoke,
    db: Session = Depends(get_db),
    user: legacy.User = Depends(get_current_user),
):
    consent = _owned(db, models.WhatsAppConsent, consent_id, user)
    if consent.revoked_at is None:
        consent.revoked_at = utcnow()
        consent.revoked_by_user_id = user.id
        consent.revocation_reason = payload.reason
        add_audit(
            db,
            owner_id=user.id,
            actor_user_id=user.id,
            action="whatsapp_consent.revoked",
            entity_type="whatsapp_consent",
            entity_id=consent.id,
            after={"reason": payload.reason},
        )
        _commit(db)
        db.refresh(consent)
    return consent


@router.get("/tasks", response_model=list[TaskRead])
def list_tasks(
    task_status: Optional[TaskStatus] = Query(None, alias="status"),
    queue_scope: TaskQueueScope = Query(TaskQueueScope.SALES),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
    user: legacy.User = Depends(get_current_user),
):
    query = db.query(models.Task).filter_by(owner_id=user.id, archived_at=None)
    query = query.filter(models.Task.queue_scope == queue_scope)
    if task_status:
        query = query.filter(models.Task.status == task_status)
    return query.order_by(models.Task.priority.desc(), models.Task.due_at.asc(), models.Task.id.asc()).offset(offset).limit(limit).all()


@router.patch(
    "/tasks/{task_id}",
    response_model=TaskRead,
    responses=_error_responses(status.HTTP_404_NOT_FOUND, status.HTTP_409_CONFLICT),
)
def update_task(task_id: int, payload: TaskUpdate, db: Session = Depends(get_db), user: legacy.User = Depends(get_current_user)):
    task = _owned(db, models.Task, task_id, user)
    review_content_supplied = bool({"review_subject", "review_body"} & payload.model_fields_set)
    if review_content_supplied:
        if task.task_type != TaskType.DRAFT_REVIEW:
            raise HTTPException(status_code=409, detail={"code": "TASK_IS_NOT_DRAFT_REVIEW"})
        if task.status in {TaskStatus.COMPLETED, TaskStatus.DISMISSED}:
            raise HTTPException(status_code=409, detail={"code": "REVIEW_DECISION_ALREADY_FINAL"})
        if "review_body" in payload.model_fields_set and not (payload.review_body or "").strip():
            raise HTTPException(status_code=422, detail={"code": "REVIEW_BODY_REQUIRED"})
        task.metadata_json = {
            **(task.metadata_json or {}),
            **({"subject": payload.review_subject} if "review_subject" in payload.model_fields_set else {}),
            **({"body": payload.review_body.strip()} if "review_body" in payload.model_fields_set and payload.review_body else {}),
            "edited_by_user_id": user.id,
            "edited_at": utcnow().isoformat(),
        }
    if (
        task.task_type == TaskType.SALES_HANDOFF
        and payload.status == TaskStatus.COMPLETED
        and task.opportunity_id is None
    ):
        raise HTTPException(
            status_code=409,
            detail={
                "code": "TASK_REQUIRES_OPPORTUNITY_CONFIRMATION",
                "message": "sales_handoff must be completed by confirming or dismissing the Opportunity handoff",
            },
        )
    if task.task_type == TaskType.DRAFT_REVIEW and payload.status in {
        TaskStatus.COMPLETED,
        TaskStatus.DISMISSED,
    }:
        if task.status in {TaskStatus.COMPLETED, TaskStatus.DISMISSED}:
            if task.status != payload.status:
                raise HTTPException(
                    status_code=409,
                    detail={"code": "REVIEW_DECISION_ALREADY_FINAL"},
                )
            return task
        if task.attempt_id is None:
            raise HTTPException(status_code=409, detail={"code": "REVIEW_ATTEMPT_MISSING"})
        attempt = _owned(db, models.OutreachAttempt, task.attempt_id, user, include_archived=True)
        enrollment = _owned(db, models.Enrollment, attempt.enrollment_id, user, include_archived=True)
        if task.enrollment_id != enrollment.id or task.campaign_id != attempt.campaign_id:
            raise HTTPException(status_code=409, detail={"code": "REVIEW_TASK_IDENTITY_MISMATCH"})
        if payload.status == TaskStatus.COMPLETED:
            if (
                attempt.status != AttemptStatus.BLOCKED
                or attempt.last_error != "review_approval_required"
                or enrollment.status != EnrollmentStatus.BLOCKED
                or enrollment.paused_reason != "review_approval_required"
            ):
                raise HTTPException(status_code=409, detail={"code": "REVIEW_ATTEMPT_NOT_APPROVABLE"})
            campaign = _owned(db, models.Campaign, attempt.campaign_id, user)
            if campaign.lifecycle != CampaignLifecycle.RUNNING:
                raise HTTPException(status_code=409, detail={"code": "REVIEW_CAMPAIGN_NOT_RUNNING"})
            attempt.status = AttemptStatus.QUEUED
            attempt.scheduled_at = utcnow()
            attempt.last_error = None
            attempt.claimed_by = None
            attempt.lease_expires_at = None
            enrollment.status = EnrollmentStatus.ACTIVE
            enrollment.paused_reason = None
            enrollment.paused_at = None
        else:
            if attempt.status not in {AttemptStatus.BLOCKED, AttemptStatus.QUEUED}:
                raise HTTPException(status_code=409, detail={"code": "REVIEW_ATTEMPT_NOT_DISMISSIBLE"})
            attempt.status = AttemptStatus.CANCELLED
            attempt.last_error = "review_rejected"
            attempt.claimed_by = None
            attempt.lease_expires_at = None
            enrollment.status = EnrollmentStatus.PAUSED
            enrollment.paused_reason = "review_rejected"
            enrollment.paused_at = utcnow()
        task.metadata_json = {
            **(task.metadata_json or {}),
            "review_decision": "approved" if payload.status == TaskStatus.COMPLETED else "dismissed",
            "decided_by_user_id": user.id,
            "decided_at": utcnow().isoformat(),
            "provider_call_allowed": payload.status == TaskStatus.COMPLETED,
        }
    before = {"status": task.status.value, "assignee_user_id": task.assignee_user_id}
    if payload.status is not None:
        task.status = payload.status
        if payload.status in {TaskStatus.COMPLETED, TaskStatus.DISMISSED}:
            task.completed_at = utcnow()
        else:
            task.completed_at = None
    if payload.assignee_user_id is not None:
        task.assignee_user_id = payload.assignee_user_id
    if payload.due_at is not None:
        task.due_at = payload.due_at
    add_audit(
        db,
        owner_id=user.id,
        actor_user_id=user.id,
        action="task.updated",
        entity_type="task",
        entity_id=task.id,
        before=before,
        after={
            "status": task.status.value,
            "assignee_user_id": task.assignee_user_id,
            "attempt_id": task.attempt_id,
            "review_decision": (task.metadata_json or {}).get("review_decision"),
            "review_content_edited": review_content_supplied,
        },
    )
    if task.task_type == TaskType.DRAFT_REVIEW and payload.status == TaskStatus.COMPLETED:
        activation_campaign = db.query(models.AuditEvent.id).filter_by(
            owner_id=user.id,
            action="activation.launch_completed",
            entity_type="campaign",
            entity_id=str(task.campaign_id),
        ).first()
        if activation_campaign:
            add_audit(
                db,
                owner_id=user.id,
                actor_user_id=user.id,
                action="activation.draft_approved",
                entity_type="task",
                entity_id=task.id,
                after={"attempt_id": task.attempt_id, "campaign_id": task.campaign_id},
            )
    _commit(db)
    db.refresh(task)
    return task


@router.get("/conversations", response_model=list[ConversationRead])
def list_conversations(limit: int = Query(100, ge=1, le=500), db: Session = Depends(get_db), user: legacy.User = Depends(get_current_user)):
    return db.query(models.Conversation).filter_by(owner_id=user.id, archived_at=None).order_by(models.Conversation.last_message_at.desc()).limit(limit).all()


@router.post(
    "/webhooks/{owner_id}/{provider}/events",
    response_model=MessageEventRead,
    status_code=status.HTTP_201_CREATED,
    responses={
        401: {"model": ErrorResponse, "description": "Webhook authentication failed"},
        409: {"model": ErrorResponse, "description": "Webhook replay or domain conflict"},
        413: {"model": ErrorResponse, "description": "Webhook payload exceeds the byte limit"},
        422: {"model": ErrorResponse, "description": "Webhook envelope or payload is invalid"},
        503: {"model": ErrorResponse, "description": "Webhook authentication policy is unavailable"},
    },
    openapi_extra={
        "requestBody": {
            "required": True,
            "content": {
                "application/json": {
                    # Manual parsing is intentional: HMAC must be checked over
                    # the exact bytes before Pydantic touches the body.
                    "schema": _WEBHOOK_EVENT_REQUEST_SCHEMA,
                }
            },
        }
    },
)
async def ingest_webhook_event(
    request: Request,
    owner_id: Annotated[int, Path(gt=0)],
    provider: str,
    idempotency_key: str = Header(..., alias="Idempotency-Key", min_length=8, max_length=255),
    webhook_timestamp: str = Header(..., alias=TIMESTAMP_HEADER),
    webhook_event_id: str = Header(..., alias=EVENT_ID_HEADER),
    webhook_signature: str = Header(..., alias=SIGNATURE_HEADER),
    db: Session = Depends(get_db),
):
    try:
        if webhook_ingress_rejected():
            raise WebhookSecurityError(
                "WEBHOOK_INGRESS_DISABLED",
                "Webhook ingress is temporarily disabled",
                status_code=503,
            )
        raw_body = await _read_bounded_webhook_body(request)
        verification = verify_webhook(
            provider=provider,
            owner_id=owner_id,
            timestamp_header=webhook_timestamp,
            event_id_header=webhook_event_id,
            signature_header=webhook_signature,
            raw_body=raw_body,
        )
    except WebhookSecurityError as exc:
        raise HTTPException(
            status_code=exc.status_code,
            detail={"code": exc.code, "message": exc.message},
        ) from exc

    # Owner existence is checked only after successful HMAC verification and
    # deliberately uses the same generic authentication response.  This route
    # is Provider-facing and therefore does not depend on a user's JWT.
    if not db.query(legacy.User.id).filter(legacy.User.id == owner_id).first():
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={
                "code": "WEBHOOK_AUTHENTICATION_FAILED",
                "message": "Webhook authentication failed",
            },
        )

    try:
        event, _was_replay = ingest_verified_webhook(
            db,
            verification=verification,
            idempotency_key=idempotency_key,
            raw_body=raw_body,
        )
    except VerifiedWebhookError as exc:
        raise HTTPException(
            status_code=exc.status_code,
            detail={"code": exc.code, "message": exc.message},
        ) from exc
    except ValueError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "WEBHOOK_EVENT_REJECTED",
                "message": "Webhook event conflicts with Product V2 domain state",
            },
        ) from exc
    return event


@router.get(
    "/conversations/{conversation_id}/assessments",
    response_model=list[ReplyAssessmentRead],
    responses=_error_responses(status.HTTP_404_NOT_FOUND),
)
def list_assessments(conversation_id: int, db: Session = Depends(get_db), user: legacy.User = Depends(get_current_user)):
    conversation = _owned(db, models.Conversation, conversation_id, user, include_archived=True)
    return db.query(models.ReplyAssessment).filter_by(conversation_id=conversation.id, owner_id=user.id).order_by(models.ReplyAssessment.created_at.desc()).all()


@router.post(
    "/reply-assessments/{assessment_id}/confirm",
    response_model=TaskRead,
    responses=_error_responses(status.HTTP_404_NOT_FOUND, status.HTTP_409_CONFLICT),
)
def confirm_assessment(assessment_id: int, payload: ReplyConfirmation, db: Session = Depends(get_db), user: legacy.User = Depends(get_current_user)):
    assessment = _owned(db, models.ReplyAssessment, assessment_id, user, include_archived=True)
    try:
        task = confirm_reply_assessment(
            db,
            assessment=assessment,
            actor_user_id=user.id,
            intent=payload.intent,
            is_positive=payload.is_positive,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=409,
            detail={"code": "REPLY_CONFIRMATION_REJECTED", "message": str(exc)},
        ) from exc
    _commit(db)
    db.refresh(task)
    return task


@router.get("/opportunities", response_model=list[OpportunityRead])
def list_opportunities(include_archived: bool = False, db: Session = Depends(get_db), user: legacy.User = Depends(get_current_user)):
    query = db.query(models.Opportunity).filter(models.Opportunity.owner_id == user.id)
    if not include_archived:
        query = query.filter(models.Opportunity.archived_at.is_(None))
    return query.order_by(models.Opportunity.updated_at.desc()).all()


@router.post(
    "/opportunities",
    response_model=OpportunityRead,
    status_code=status.HTTP_201_CREATED,
    responses=_error_responses(status.HTTP_409_CONFLICT),
)
def create_opportunity(payload: OpportunityConfirm, db: Session = Depends(get_db), user: legacy.User = Depends(get_current_user)):
    try:
        opportunity = confirm_opportunity(db, owner_id=user.id, actor_user_id=user.id, data=payload)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail={"code": "OPPORTUNITY_NOT_QUALIFIED", "message": str(exc)}) from exc
    _commit(db)
    db.refresh(opportunity)
    return opportunity


@router.patch(
    "/opportunities/{opportunity_id}/stage",
    response_model=OpportunityRead,
    responses=_error_responses(status.HTTP_404_NOT_FOUND, status.HTTP_409_CONFLICT),
)
def change_opportunity_stage(opportunity_id: int, payload: OpportunityStageUpdate, db: Session = Depends(get_db), user: legacy.User = Depends(get_current_user)):
    opportunity = _owned(db, models.Opportunity, opportunity_id, user)
    try:
        update_opportunity_stage(db, opportunity=opportunity, actor_user_id=user.id, data=payload)
    except ValueError as exc:
        raise HTTPException(
            status_code=409,
            detail={"code": "INVALID_OPPORTUNITY_TRANSITION", "message": str(exc)},
        ) from exc
    _commit(db)
    db.refresh(opportunity)
    return opportunity


@router.get("/consent-restrictions", response_model=list[ConsentRestrictionRead])
def list_consent_restrictions(db: Session = Depends(get_db), user: legacy.User = Depends(get_current_user)):
    return db.query(models.ConsentRestriction).filter_by(owner_id=user.id).order_by(models.ConsentRestriction.created_at.desc()).all()


@router.post(
    "/consent-restrictions",
    response_model=ConsentRestrictionRead,
    status_code=status.HTTP_201_CREATED,
    responses=_error_responses(
        status.HTTP_403_FORBIDDEN,
        status.HTTP_404_NOT_FOUND,
        status.HTTP_409_CONFLICT,
    ),
)
def create_consent_restriction(
    payload: ConsentRestrictionCreate,
    idempotency_key: str = Header(..., alias="Idempotency-Key", min_length=8, max_length=255),
    db: Session = Depends(get_db),
    user: legacy.User = Depends(get_current_user),
):
    if payload.scope == RestrictionScope.COMPANY:
        if not payload.company_scope_confirmed:
            raise HTTPException(status_code=409, detail={"code": "COMPANY_SCOPE_CONFIRMATION_REQUIRED"})
    if payload.scope == RestrictionScope.GLOBAL and not user.is_admin:
        raise HTTPException(status_code=403, detail="Only admins may create global restrictions")

    normalized = {
        "scope": payload.scope,
        "channel": None,
        "contact_point_id": None,
        "contact_id": None,
        "company_id": None,
        "reason": payload.reason,
        "source": payload.source,
    }
    if payload.scope == RestrictionScope.CONTACT_POINT:
        contact_point = _owned(
            db, models.ContactPoint, payload.contact_point_id, user, include_archived=True
        )
        if payload.channel is not None and payload.channel != contact_point.channel:
            raise HTTPException(
                status_code=422,
                detail={
                    "code": "CONTACT_POINT_CHANNEL_MISMATCH",
                    "message": "channel must match the owned contact point",
                },
            )
        normalized["channel"] = contact_point.channel
        normalized["contact_point_id"] = contact_point.id
    elif payload.scope == RestrictionScope.CONTACT:
        contact = _owned(db, models.Contact, payload.contact_id, user, include_archived=True)
        normalized["contact_id"] = contact.id
    elif payload.scope == RestrictionScope.COMPANY:
        company = _owned(db, models.Company, payload.company_id, user, include_archived=True)
        normalized["company_id"] = company.id

    existing = db.query(models.ConsentRestriction).filter_by(idempotency_key=idempotency_key).first()
    if existing:
        same_request = existing.owner_id == user.id and all(
            getattr(existing, field) == value for field, value in normalized.items()
        )
        if not same_request:
            raise HTTPException(status_code=409, detail={"code": "IDEMPOTENCY_KEY_CONFLICT"})
        return existing
    row = models.ConsentRestriction(
        owner_id=user.id,
        idempotency_key=idempotency_key,
        created_by_user_id=user.id,
        metadata_json={"company_scope_confirmed": payload.company_scope_confirmed},
        **normalized,
    )
    db.add(row)
    _flush(db)
    add_audit(db, owner_id=user.id, actor_user_id=user.id, action="consent_restriction.created", entity_type="consent_restriction", entity_id=row.id, correlation_id=idempotency_key)
    _commit(db)
    db.refresh(row)
    return row


@router.get("/manual-overrides", response_model=list[ManualOverrideRead])
def list_manual_overrides(db: Session = Depends(get_db), user: legacy.User = Depends(get_current_user)):
    return db.query(models.ManualOverride).filter_by(owner_id=user.id).order_by(models.ManualOverride.created_at.desc()).all()


@router.post(
    "/manual-overrides",
    response_model=ManualOverrideRead,
    status_code=status.HTTP_201_CREATED,
    responses=_error_responses(status.HTTP_404_NOT_FOUND, status.HTTP_409_CONFLICT),
)
def create_manual_override(payload: ManualOverrideCreate, db: Session = Depends(get_db), user: legacy.User = Depends(get_current_user)):
    enrollment = _owned(db, models.Enrollment, payload.enrollment_id, user, include_archived=True)
    if payload.expires_at <= utcnow():
        raise HTTPException(status_code=422, detail="expires_at must be in the future")
    if payload.attempt_id:
        attempt = _owned(db, models.OutreachAttempt, payload.attempt_id, user, include_archived=True)
        if attempt.enrollment_id != enrollment.id:
            raise HTTPException(status_code=409, detail="Attempt does not belong to enrollment")
    row = models.ManualOverride(
        owner_id=user.id,
        gate=payload.gate,
        enrollment_id=enrollment.id,
        attempt_id=payload.attempt_id,
        reason=payload.reason,
        expires_at=payload.expires_at,
        created_by_user_id=user.id,
    )
    db.add(row)
    _flush(db)
    add_audit(db, owner_id=user.id, actor_user_id=user.id, action="manual_override.created", entity_type="manual_override", entity_id=row.id, after={"gate": payload.gate.value, "expires_at": payload.expires_at.isoformat()})
    _commit(db)
    db.refresh(row)
    return row


@router.get("/safety-locks", response_model=list[SafetyLockRead])
def list_safety_locks(
    active: Optional[bool] = True,
    limit: int = Query(100, ge=1, le=500),
    db: Session = Depends(get_db),
    user: legacy.User = Depends(get_current_user),
):
    query = db.query(models.SafetyLock).filter_by(owner_id=user.id)
    if active is not None:
        query = query.filter(models.SafetyLock.active.is_(active))
    return query.order_by(models.SafetyLock.locked_at.desc(), models.SafetyLock.id.desc()).limit(
        limit
    ).all()


@router.post(
    "/safety-locks/{safety_lock_id}/release",
    response_model=SafetyLockRead,
    responses=_error_responses(status.HTTP_404_NOT_FOUND, status.HTTP_409_CONFLICT),
)
def release_safety_lock(
    safety_lock_id: int,
    payload: SafetyLockRelease,
    idempotency_key: str = Header(
        ...,
        alias="Idempotency-Key",
        min_length=8,
        max_length=255,
    ),
    db: Session = Depends(get_db),
    user: legacy.User = Depends(get_current_user),
):
    """Human-confirm a remediated durable lock; transient Provider locks stay internal."""

    query = db.query(models.SafetyLock).filter(
        models.SafetyLock.id == safety_lock_id,
        models.SafetyLock.owner_id == user.id,
    )
    if db.get_bind().dialect.name == "mysql":
        query = query.with_for_update()
    safety_lock = query.first()
    if safety_lock is None:
        raise HTTPException(status_code=404, detail="SafetyLock not found")
    if safety_lock.code.startswith("provider_in_flight:") or (
        safety_lock.metadata_json or {}
    ).get("lock_kind") == "provider_in_flight":
        raise HTTPException(
            status_code=409,
            detail={"code": "TRANSIENT_SAFETY_LOCK_NOT_RELEASABLE"},
        )

    existing = db.query(models.AuditEvent).filter_by(
        owner_id=user.id,
        action="safety_lock.released",
        correlation_id=idempotency_key,
    ).first()
    expected = {
        "safety_lock_id": safety_lock.id,
        "reason": payload.reason,
        "evidence_id": payload.evidence_id,
        "human_confirmed": True,
    }
    if existing is not None:
        if (
            existing.entity_type != "safety_lock"
            or existing.entity_id != str(safety_lock.id)
            or (existing.after_data or {}) != expected
        ):
            raise HTTPException(
                status_code=409,
                detail={"code": "IDEMPOTENCY_KEY_CONFLICT"},
            )
        return safety_lock
    if not safety_lock.active:
        raise HTTPException(
            status_code=409,
            detail={"code": "SAFETY_LOCK_ALREADY_RELEASED"},
        )

    if safety_lock.scope == SafetyLockScope.ACCOUNT:
        account = db.query(models.ChannelAccount).filter(
            models.ChannelAccount.id == safety_lock.channel_account_id,
            models.ChannelAccount.owner_id == user.id,
        ).first()
        maximum_age = read_int(
            "PRODUCT_V2_ACCOUNT_HEALTH_TTL_SECONDS",
            default=300,
            minimum=1,
            maximum=86_400,
        )
        checked_at = account.health_checked_at if account else None
        checked_utc = (
            checked_at
            if checked_at is None or checked_at.tzinfo is not None
            else checked_at.replace(tzinfo=timezone.utc)
        )
        if (
            account is None
            or account.health_status.value != "healthy"
            or checked_utc is None
            or utcnow() - checked_utc > timedelta(seconds=maximum_age)
        ):
            raise HTTPException(
                status_code=409,
                detail={"code": "FRESH_HEALTH_PROBE_REQUIRED"},
            )

    observed_at = utcnow()
    safety_lock.active = False
    safety_lock.unlocked_at = observed_at
    safety_lock.unlocked_by_user_id = user.id
    safety_lock.metadata_json = {
        **(safety_lock.metadata_json or {}),
        "release_evidence_id": payload.evidence_id,
        "release_reason": payload.reason,
        "human_confirmed": True,
    }
    for task in db.query(models.Task).filter(
        models.Task.owner_id == user.id,
        models.Task.task_type == TaskType.DELIVERABILITY_ALERT,
        models.Task.status == TaskStatus.OPEN,
    ).all():
        if (task.metadata_json or {}).get("safety_lock_id") != safety_lock.id:
            continue
        task.status = TaskStatus.COMPLETED
        task.completed_at = observed_at
        task.metadata_json = {
            **(task.metadata_json or {}),
            "remediation_evidence_id": payload.evidence_id,
            "resolved_by_user_id": user.id,
            "resolved_at": observed_at.isoformat(),
        }
    db.add(
        models.AuditEvent(
            owner_id=user.id,
            actor_user_id=user.id,
            action="safety_lock.released",
            entity_type="safety_lock",
            entity_id=str(safety_lock.id),
            correlation_id=idempotency_key,
            before_data={"active": True},
            after_data=expected,
        )
    )
    _commit(db)
    db.refresh(safety_lock)
    return safety_lock


@router.get("/providers/usage", response_model=ProviderUsageRead)
def provider_usage(db: Session = Depends(get_db), user: legacy.User = Depends(get_current_user)):
    native_rows = db.query(
        models.ProviderCostEvent.provider,
        models.ProviderCostEvent.native_unit,
        func.sum(models.ProviderCostEvent.units),
        func.sum(models.ProviderCostEvent.result_count),
    ).filter_by(owner_id=user.id).group_by(models.ProviderCostEvent.provider, models.ProviderCostEvent.native_unit).all()
    normalized_rows = db.query(
        models.ProviderCostEvent.normalized_currency,
        func.sum(models.ProviderCostEvent.normalized_amount),
    ).filter(
        models.ProviderCostEvent.owner_id == user.id,
        models.ProviderCostEvent.normalized_amount.isnot(None),
    ).group_by(models.ProviderCostEvent.normalized_currency).all()
    return {
        "native": [
            {"provider": provider, "unit": unit, "units": units or 0, "results": results or 0}
            for provider, unit, units, results in native_rows
        ],
        "normalized": [
            {"currency": currency, "amount": amount}
            for currency, amount in normalized_rows
        ],
    }


@router.get("/analytics/outcomes", response_model=OutcomeAnalyticsRead)
def outcome_analytics(db: Session = Depends(get_db), user: legacy.User = Depends(get_current_user)):
    opportunity_count = db.query(models.Opportunity.id).filter_by(owner_id=user.id).count()
    won_count = db.query(models.Opportunity.id).filter_by(owner_id=user.id, stage=OpportunityStage.WON).count()
    sent_count = db.query(models.OutreachAttempt.id).filter_by(owner_id=user.id, status="succeeded").count()
    positive_replies = db.query(models.ReplyAssessment.id).filter_by(owner_id=user.id, status="confirmed", is_positive=True).count()
    return {
        "north_star": {"qualified_opportunities": opportunity_count},
        "outcomes": {"won": won_count, "positive_replies": positive_replies},
        "diagnostics": {"successful_attempts": sent_count},
    }


@router.get(
    "/jobs/{job_id}",
    response_model=JobRead,
    responses=_error_responses(status.HTTP_404_NOT_FOUND),
)
def get_job(job_id: int, db: Session = Depends(get_db), user: legacy.User = Depends(get_current_user)):
    return _owned(db, models.AutomationJob, job_id, user, include_archived=True)


@router.get("/runtime/heartbeats", response_model=list[WorkerHeartbeatRead])
def list_heartbeats(db: Session = Depends(get_db), user: legacy.User = Depends(get_current_user)):
    return db.query(models.WorkerHeartbeat).order_by(models.WorkerHeartbeat.worker_type, models.WorkerHeartbeat.worker_name).all()


@router.get(
    "/runtime/stages",
    response_model=list[StageRuntimeRead],
    responses=_error_responses(status.HTTP_404_NOT_FOUND),
)
def list_stage_runtimes(
    campaign_id: Optional[int] = None,
    db: Session = Depends(get_db),
    user: legacy.User = Depends(get_current_user),
):
    query = db.query(models.StageRuntime).filter(models.StageRuntime.owner_id == user.id)
    if campaign_id is not None:
        _owned(db, models.Campaign, campaign_id, user, include_archived=True)
        query = query.filter(models.StageRuntime.campaign_id == campaign_id)
    return query.order_by(models.StageRuntime.campaign_id, models.StageRuntime.stage_name).all()
