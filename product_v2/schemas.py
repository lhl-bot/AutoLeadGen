"""Pydantic contracts for the Product V2 HTTP API."""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any, Literal, Optional, Union

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from product_v2.enums import (
    CampaignLifecycle,
    CampaignRevisionStatus,
    CampaignRunMode,
    Channel,
    ChannelAccountHealth,
    ContactPointAvailabilityStatus,
    ContactPointVerificationStatus,
    ConversationStatus,
    EnrollmentStatus,
    JobStatus,
    MessageDirection,
    MessageEventType,
    OpportunityStage,
    OverrideGate,
    ReplyAssessmentStatus,
    ReplyIntent,
    ReviewBatchStatus,
    RestrictionScope,
    RouteProposalStatus,
    SafetyLockScope,
    StageStatus,
    TaskPriority,
    TaskQueueScope,
    TaskStatus,
    TaskType,
    WorkerType,
)


class OrmModel(BaseModel):
    model_config = ConfigDict(from_attributes=True, use_enum_values=True)


class ErrorDetail(BaseModel):
    """Structured business-error payload carried in FastAPI's ``detail`` field.

    Authentication and not-found failures intentionally remain compatible with
    FastAPI's string details, while V2 domain conflicts expose a stable code and
    optional machine-readable readiness context.
    """

    model_config = ConfigDict(extra="allow")

    code: str
    message: Optional[str] = None
    blockers: Optional[list[dict[str, Any]]] = None
    warnings: Optional[list[dict[str, Any]]] = None


class ErrorResponse(BaseModel):
    detail: Union[str, ErrorDetail]


class JobAccepted(BaseModel):
    job_id: int
    status: JobStatus


class CompanyCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    domain: Optional[str] = Field(default=None, max_length=255)
    website: Optional[str] = Field(default=None, max_length=1000)
    country: Optional[str] = Field(default=None, max_length=100)
    region: Optional[str] = Field(default=None, max_length=100)
    industry: Optional[str] = Field(default=None, max_length=255)
    timezone: Optional[str] = Field(default=None, max_length=100)


class CompanyUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=255)
    domain: Optional[str] = Field(default=None, max_length=255)
    website: Optional[str] = Field(default=None, max_length=1000)
    country: Optional[str] = Field(default=None, max_length=100)
    region: Optional[str] = Field(default=None, max_length=100)
    industry: Optional[str] = Field(default=None, max_length=255)
    timezone: Optional[str] = Field(default=None, max_length=100)

    @model_validator(mode="after")
    def require_change(self):
        if not self.model_fields_set:
            raise ValueError("Provide at least one Company field to update")
        return self


class CompanyRead(OrmModel):
    id: int
    owner_id: int
    name: str
    normalized_domain: Optional[str]
    website: Optional[str]
    country: Optional[str]
    region: Optional[str]
    industry: Optional[str]
    timezone: Optional[str]
    archived_at: Optional[datetime]
    created_at: datetime
    updated_at: datetime


class ChannelAccountSummary(BaseModel):
    id: int
    owner_id: int
    channel: Channel
    provider: str
    address: str
    display_name: Optional[str]
    enabled: bool
    health_status: ChannelAccountHealth
    health_checked_at: Optional[datetime]
    daily_limit: Optional[int]
    timezone: str
    smtp_host: Optional[str]
    smtp_port: Optional[int]
    imap_host: Optional[str]
    imap_port: Optional[int]
    transport: str
    credentials_configured: bool
    legacy_email_account_id: Optional[int]
    legacy_channel_account_id: Optional[int]
    last_error: Optional[str]


class UnipileAccountBindingDraft(BaseModel):
    legacy_channel_account_id: int
    channel: Channel
    daily_limit: int = Field(default=5, ge=1, le=100)
    timezone: str = Field(default="UTC", min_length=1, max_length=100)

    @model_validator(mode="after")
    def supported_channel(self):
        if self.channel not in {Channel.LINKEDIN, Channel.WHATSAPP}:
            raise ValueError("Unipile supports LinkedIn or WhatsApp bindings")
        return self


class UnipileAccountBindingPreview(BaseModel):
    legacy_channel_account_id: int
    provider_account_id: str
    channel: Channel
    display_name: Optional[str]
    provider_status: str
    health_status: ChannelAccountHealth
    daily_limit: int
    timezone: str
    preview_checksum: str = Field(pattern=r"^[0-9a-f]{64}$")
    warnings: list[dict[str, str]] = Field(default_factory=list)


class UnipileAccountBindingApply(UnipileAccountBindingDraft):
    preview_checksum: str = Field(pattern=r"^[0-9a-f]{64}$")
    human_confirmed: Literal[True]


class EmailAccountBindingDraft(BaseModel):
    legacy_email_account_id: int = Field(gt=0)
    daily_limit: int = Field(ge=1, le=100)
    timezone: str = Field(min_length=1, max_length=100)


class EmailAccountBindingWarning(BaseModel):
    code: str
    message: str


class EmailAccountBindingPreview(BaseModel):
    legacy_email_account_id: int
    current_channel_account_id: Optional[int]
    address: str
    daily_limit: int
    timezone: str
    preview_checksum: str = Field(pattern=r"^[0-9a-f]{64}$")
    effects: dict[str, Any]
    warnings: list[EmailAccountBindingWarning]


class EmailAccountBindingApply(EmailAccountBindingDraft):
    preview_checksum: str = Field(pattern=r"^[0-9a-f]{64}$")
    human_confirmed: Literal[True]


class ContactPointCreate(BaseModel):
    channel: Channel
    value: str = Field(min_length=1, max_length=1000)
    # Public callers may describe a new contact point, but they are not a
    # verification authority.  Provider verification and backfill code write
    # the model directly after retaining their own provenance.
    verification_status: Literal[
        ContactPointVerificationStatus.UNVERIFIED
    ] = ContactPointVerificationStatus.UNVERIFIED
    availability_status: ContactPointAvailabilityStatus = ContactPointAvailabilityStatus.AVAILABLE
    is_primary: bool = False


class ContactCreate(BaseModel):
    company_id: int
    first_name: Optional[str] = Field(default=None, max_length=120)
    last_name: Optional[str] = Field(default=None, max_length=120)
    full_name: Optional[str] = Field(default=None, max_length=255)
    job_title: Optional[str] = Field(default=None, max_length=255)
    department: Optional[str] = Field(default=None, max_length=255)
    timezone: Optional[str] = Field(default=None, max_length=100)
    locale: Optional[str] = Field(default=None, max_length=50)
    contact_points: list[ContactPointCreate] = Field(default_factory=list)

    @model_validator(mode="after")
    def require_name(self):
        if not (self.full_name or self.first_name or self.last_name):
            raise ValueError("Provide full_name, first_name, or last_name")
        return self


class ContactUpdate(BaseModel):
    first_name: Optional[str] = Field(default=None, max_length=120)
    last_name: Optional[str] = Field(default=None, max_length=120)
    full_name: Optional[str] = Field(default=None, min_length=1, max_length=255)
    job_title: Optional[str] = Field(default=None, max_length=255)
    department: Optional[str] = Field(default=None, max_length=255)
    timezone: Optional[str] = Field(default=None, max_length=100)
    locale: Optional[str] = Field(default=None, max_length=50)

    @model_validator(mode="after")
    def require_change(self):
        if not self.model_fields_set:
            raise ValueError("Provide at least one Contact field to update")
        if "full_name" in self.model_fields_set and not (self.full_name or "").strip():
            raise ValueError("full_name cannot be empty")
        return self


class ContactPointRead(OrmModel):
    id: int
    company_id: int
    contact_id: int
    channel: Channel
    value: str
    normalized_value: str
    verification_status: ContactPointVerificationStatus
    availability_status: ContactPointAvailabilityStatus
    is_primary: bool
    archived_at: Optional[datetime]


class ContactRead(OrmModel):
    id: int
    owner_id: int
    company_id: int
    first_name: Optional[str]
    last_name: Optional[str]
    full_name: str
    job_title: Optional[str]
    department: Optional[str]
    timezone: Optional[str]
    locale: Optional[str]
    archived_at: Optional[datetime]
    created_at: datetime
    updated_at: datetime
    contact_points: list[ContactPointRead] = Field(default_factory=list)


class EvidenceSnapshotRead(OrmModel):
    id: int
    owner_id: int
    company_id: Optional[int]
    contact_id: Optional[int]
    source: str
    source_url: Optional[str]
    evidence: dict[str, Any]
    confidence: Decimal
    version: int
    captured_at: datetime


class AcquisitionCandidateRead(OrmModel):
    id: int
    run_id: int
    row_number: int
    status: str
    selected: bool
    company_name: Optional[str]
    normalized_domain: Optional[str]
    first_name: Optional[str]
    last_name: Optional[str]
    full_name: Optional[str]
    job_title: Optional[str]
    email: Optional[str]
    source_url: Optional[str]
    evidence: dict[str, Any]
    confidence: Decimal
    verification_status: ContactPointVerificationStatus
    verification_source: Optional[str]
    verification_checked_at: Optional[datetime]
    rejection_reason: Optional[str]
    committed_company_id: Optional[int]
    committed_contact_id: Optional[int]
    committed_contact_point_id: Optional[int]


class AcquisitionRunRead(OrmModel):
    id: int
    source: Literal["csv", "ai"]
    status: str
    name: str
    criteria: dict[str, Any]
    column_mapping: dict[str, Any]
    provider: Optional[str]
    estimated_units: Optional[Decimal]
    price_version: str
    last_error: Optional[str]
    committed_at: Optional[datetime]
    created_at: datetime
    updated_at: datetime
    candidates: list[AcquisitionCandidateRead] = Field(default_factory=list)
    job_id: Optional[int] = None


class AcquisitionSearchCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=255)
    product_summary: str = Field(min_length=3, max_length=4000)
    target_industries: list[str] = Field(default_factory=list, max_length=20)
    target_roles: list[str] = Field(default_factory=list, max_length=20)
    target_regions: list[str] = Field(default_factory=list, max_length=20)
    limit: int = Field(default=10, ge=5, le=20)
    paid_action_confirmed: Literal[True]
    approval_id: Optional[str] = Field(default=None, max_length=255)


class AcquisitionVerifyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    candidate_ids: list[int] = Field(min_length=1, max_length=20)
    paid_action_confirmed: Literal[True]
    approval_id: Optional[str] = Field(default=None, max_length=255)

    @field_validator("candidate_ids")
    @classmethod
    def unique_candidate_ids(cls, values: list[int]) -> list[int]:
        if any(value <= 0 for value in values):
            raise ValueError("candidate_ids must be positive")
        if len(set(values)) != len(values):
            raise ValueError("candidate_ids must be unique")
        return values


class AcquisitionCommitRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    candidate_ids: list[int] = Field(min_length=1, max_length=20)
    human_confirmed: Literal[True]

    @field_validator("candidate_ids")
    @classmethod
    def unique_candidate_ids(cls, values: list[int]) -> list[int]:
        if any(value <= 0 for value in values):
            raise ValueError("candidate_ids must be positive")
        if len(set(values)) != len(values):
            raise ValueError("candidate_ids must be unique")
        return values


class ActivationStepRead(BaseModel):
    key: Literal["icp", "mailbox", "customers", "plan", "send"]
    label: str
    completed: bool
    detail: str
    href: str


class ActivationRead(BaseModel):
    activated: bool
    current_step: int = Field(ge=1, le=5)
    started_at: Optional[datetime]
    first_sent_at: Optional[datetime]
    steps: list[ActivationStepRead]
    blockers: list[str] = Field(default_factory=list)
    latest_run_id: Optional[int] = None
    campaign_id: Optional[int] = None
    review_tasks_open: int = 0


class ActivationLaunchDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: int = Field(gt=0)
    candidate_ids: list[int] = Field(min_length=1, max_length=20)
    channel_account_id: int = Field(gt=0)
    plan_name: str = Field(min_length=1, max_length=255)
    objective: str = Field(min_length=3, max_length=4000)
    tone: str = Field(default="专业、简洁、尊重", min_length=1, max_length=255)
    language: str = Field(default="中文", min_length=1, max_length=50)
    subject_template: str = Field(min_length=1, max_length=1000)
    body_template: str = Field(min_length=1, max_length=20000)
    daily_limit: int = Field(default=10, ge=1, le=20)

    @field_validator("candidate_ids")
    @classmethod
    def unique_launch_candidates(cls, values: list[int]) -> list[int]:
        if any(value <= 0 for value in values):
            raise ValueError("candidate_ids must be positive")
        if len(set(values)) != len(values):
            raise ValueError("candidate_ids must be unique")
        return values


class ActivationLaunchPreview(BaseModel):
    checksum: str = Field(pattern=r"^[0-9a-f]{64}$")
    effects: list[str]
    blockers: list[str]
    candidate_count: int
    estimated_send_count: int


class ActivationLaunchRequest(ActivationLaunchDraft):
    preview_checksum: str = Field(pattern=r"^[0-9a-f]{64}$")
    human_confirmed: Literal[True]


class CompanyOutreachSummary(BaseModel):
    enrollment_count: int
    sent_count: int
    reply_count: int
    last_contact_at: Optional[datetime]


class CompanyWorkspaceRead(BaseModel):
    company: CompanyRead
    contacts: list[ContactRead]
    evidence_snapshots: list[EvidenceSnapshotRead]
    outreach: CompanyOutreachSummary


class AudienceListCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    description: Optional[str] = None


class AudienceListRead(OrmModel):
    id: int
    owner_id: int
    name: str
    description: Optional[str]
    archived_at: Optional[datetime]
    created_at: datetime


class MembershipRead(BaseModel):
    id: int
    list_id: int
    company_id: Optional[int]
    contact_id: Optional[int]


class ArchiveResult(BaseModel):
    id: int
    archived: Literal[True]


class MembershipCreate(BaseModel):
    company_id: Optional[int] = None
    contact_id: Optional[int] = None

    @model_validator(mode="after")
    def exactly_one_subject(self):
        if (self.company_id is None) == (self.contact_id is None):
            raise ValueError("Exactly one of company_id or contact_id is required")
        return self


class SequenceStepCreate(BaseModel):
    position: int = Field(ge=1)
    channel: Channel
    channel_account_id: Optional[int] = Field(default=None, ge=1)
    wait_minutes: int = Field(default=0, ge=0)
    template_version: Optional[str] = Field(default=None, max_length=100)
    subject_template: Optional[str] = Field(default=None, max_length=1000)
    body_template: Optional[str] = Field(default=None, max_length=20000)
    conditions: dict[str, Any] = Field(default_factory=dict)
    stop_conditions: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def outbound_channel_only(self):
        if self.channel == Channel.OFFLINE:
            raise ValueError("Offline evidence cannot be an outreach Sequence Step")
        return self


class CampaignCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    description: Optional[str] = None
    run_mode: CampaignRunMode = CampaignRunMode.SHADOW
    priority: int = Field(default=100, ge=0, le=1000)


class CampaignRead(OrmModel):
    id: int
    owner_id: int
    name: str
    description: Optional[str]
    lifecycle: CampaignLifecycle
    run_mode: CampaignRunMode
    priority: int
    published_revision_number: Optional[int]
    archived_at: Optional[datetime]
    created_at: datetime
    updated_at: datetime


class CampaignQualityGates(BaseModel):
    """The only quality gates accepted by the V2 authoring API.

    Keeping this contract closed prevents a misspelled gate from being stored
    as inert JSON while the Campaign appears to have the intended protection.
    """

    model_config = ConfigDict(extra="forbid")

    min_fit_score: Optional[int] = Field(default=None, ge=0, le=100)
    require_evidence: bool = True
    require_timezone: bool = True
    # Verification is a hard safety gate and therefore cannot be disabled by
    # Campaign authoring input.
    require_verified_contact_point: Literal[True] = True


class CampaignRevisionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    icp_definition: dict[str, Any] = Field(default_factory=dict)
    audience_definition: dict[str, Any] = Field(default_factory=dict)
    quality_gates: CampaignQualityGates = Field(default_factory=CampaignQualityGates)
    budget_definition: dict[str, Any] = Field(default_factory=dict)
    stop_conditions: dict[str, Any] = Field(default_factory=dict)
    sequence_steps: list[SequenceStepCreate] = Field(default_factory=list)


class SequenceStepRead(BaseModel):
    id: int
    position: int
    channel: Channel
    channel_account_id: Optional[int]
    wait_minutes: int
    template_version: Optional[str]
    subject_template: Optional[str]
    body_template: Optional[str]
    conditions: dict[str, Any]
    stop_conditions: dict[str, Any]


class CampaignRevisionRead(OrmModel):
    id: int
    campaign_id: int
    revision_number: int
    status: CampaignRevisionStatus
    icp_definition: dict[str, Any]
    audience_definition: dict[str, Any]
    quality_gates: dict[str, Any]
    budget_definition: dict[str, Any]
    stop_conditions: dict[str, Any]
    sequence_steps: list[SequenceStepRead] = Field(default_factory=list)
    published_at: Optional[datetime]
    created_at: datetime


class CampaignRevisionDiff(BaseModel):
    campaign_id: int
    base_revision_id: Optional[int]
    proposed_revision_id: int
    diff: dict[str, Any]
    checksum: str = Field(pattern=r"^[0-9a-f]{64}$")


class CampaignRevisionPublish(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # Field(...) makes an explicit JSON null mandatory for the first revision.
    base_revision_id: Optional[int] = Field(...)
    reviewed_diff_checksum: str = Field(pattern=r"^[0-9a-f]{64}$")
    human_confirmed: Literal[True]


class EnrollmentCreate(BaseModel):
    contact_id: int
    scheduled_at: Optional[datetime] = None


class EnrollmentRead(OrmModel):
    id: int
    campaign_id: int
    campaign_revision_id: int
    company_id: int
    contact_id: int
    status: EnrollmentStatus
    scheduled_at: datetime
    priority_snapshot: int
    paused_reason: Optional[str]
    positive_signal_at: Optional[datetime]
    archived_at: Optional[datetime]


class ReadinessCheck(BaseModel):
    code: str
    severity: str
    passed: bool
    message: str
    details: dict[str, Any] = Field(default_factory=dict)


class CampaignReadiness(BaseModel):
    campaign_id: int
    ready: bool
    blockers: list[ReadinessCheck]
    warnings: list[ReadinessCheck]
    checked_at: datetime


class CommandOptions(BaseModel):
    confirm_warnings: bool = False


class TaskRead(OrmModel):
    id: int
    task_type: TaskType
    queue_scope: TaskQueueScope
    status: TaskStatus
    priority: TaskPriority
    title: str
    description: Optional[str]
    assignee_user_id: Optional[int]
    due_at: Optional[datetime]
    company_id: Optional[int]
    contact_id: Optional[int]
    campaign_id: Optional[int]
    enrollment_id: Optional[int]
    conversation_id: Optional[int]
    opportunity_id: Optional[int]
    attempt_id: Optional[int]
    metadata_json: Optional[dict[str, Any]]
    created_at: datetime


class TaskUpdate(BaseModel):
    status: Optional[TaskStatus] = None
    assignee_user_id: Optional[int] = None
    due_at: Optional[datetime] = None
    review_subject: Optional[str] = Field(default=None, max_length=1000)
    review_body: Optional[str] = Field(default=None, max_length=20000)


class RouteProposalStepCreate(BaseModel):
    position: int = Field(ge=1, le=3)
    sequence_step_id: int
    attempt_id: Optional[int] = None
    contact_point_id: int
    channel_account_id: int
    channel: Channel
    scheduled_at: datetime
    subject: Optional[str] = Field(default=None, max_length=1000)
    body: str = Field(min_length=1, max_length=20000)
    ai_reason: str = Field(min_length=1, max_length=4000)
    confidence: Decimal = Field(ge=Decimal("0"), le=Decimal("1"))
    evidence_snapshot_ids: list[int] = Field(default_factory=list, max_length=20)


class RouteProposalCreate(BaseModel):
    enrollment_id: int
    idempotency_key: str = Field(min_length=1, max_length=255)
    ai_model: str = Field(min_length=1, max_length=100)
    ai_reason: str = Field(min_length=1, max_length=8000)
    confidence: Decimal = Field(ge=Decimal("0"), le=Decimal("1"))
    evidence_snapshot_ids: list[int] = Field(default_factory=list, max_length=50)
    steps: list[RouteProposalStepCreate] = Field(min_length=1, max_length=3)

    @model_validator(mode="after")
    def validate_route_shape(self):
        positions = [step.position for step in self.steps]
        channels = [step.channel for step in self.steps]
        if positions != list(range(1, len(self.steps) + 1)):
            raise ValueError("route step positions must be contiguous and start at 1")
        if len(channels) != len(set(channels)):
            raise ValueError("each channel may appear at most once in a route")
        return self


class RouteProposalStepRead(OrmModel):
    id: int
    position: int
    sequence_step_id: int
    attempt_id: Optional[int]
    contact_point_id: int
    channel_account_id: int
    channel: Channel
    scheduled_at: datetime
    subject: Optional[str]
    body: str
    ai_reason: str
    confidence: Decimal
    evidence_snapshot_ids: list[int]


class RouteProposalRead(OrmModel):
    id: int
    enrollment_id: int
    contact_id: int
    status: RouteProposalStatus
    idempotency_key: str
    ai_model: str
    ai_reason: str
    confidence: Decimal
    evidence_snapshot_ids: list[int]
    checksum: str
    proposed_at: datetime
    approved_at: Optional[datetime]
    rejected_at: Optional[datetime]
    steps: list[RouteProposalStepRead]


class ReviewBatchPreviewRequest(BaseModel):
    route_proposal_ids: list[int] = Field(min_length=1, max_length=20)
    idempotency_key: str = Field(min_length=1, max_length=255)
    approval_id: str = Field(min_length=1, max_length=255)
    price_version: str = Field(min_length=1, max_length=100)
    estimated_cost: Decimal = Field(default=Decimal("0"), ge=Decimal("0"))
    batch_id: Optional[int] = None

    @field_validator("route_proposal_ids")
    @classmethod
    def route_ids_must_be_unique(cls, value: list[int]) -> list[int]:
        if len(value) != len(set(value)):
            raise ValueError("route_proposal_ids must be unique")
        return value


class ReviewBatchItemUpdate(BaseModel):
    scheduled_at: Optional[datetime] = None
    channel_account_id: Optional[int] = None
    subject: Optional[str] = Field(default=None, max_length=1000)
    body: Optional[str] = Field(default=None, min_length=1, max_length=20000)


class ReviewBatchItemRead(OrmModel):
    id: int
    position: int
    route_proposal_id: int
    proposal_checksum: str
    preview_payload: dict[str, Any]
    edited: bool


class ReviewBatchRead(OrmModel):
    id: int
    status: ReviewBatchStatus
    idempotency_key: str
    approval_id: str
    item_count: int
    preview_checksum: Optional[str]
    estimated_cost: Decimal
    price_version: str
    previewed_at: Optional[datetime]
    approved_at: Optional[datetime]
    rejected_at: Optional[datetime]
    items: list[ReviewBatchItemRead]


class ReviewBatchApprove(BaseModel):
    preview_checksum: str = Field(pattern=r"^[0-9a-f]{64}$")
    approval_id: str = Field(min_length=1, max_length=255)
    human_confirmed: bool


class ReviewBatchReject(BaseModel):
    reason: str = Field(min_length=1, max_length=1000)


class WhatsAppConsentCreate(BaseModel):
    contact_point_id: int
    idempotency_key: str = Field(min_length=1, max_length=255)
    source: str = Field(min_length=1, max_length=100)
    evidence_url: Optional[str] = Field(default=None, max_length=2000)
    evidence_text: str = Field(min_length=1, max_length=20000)
    granted_at: datetime
    expires_at: Optional[datetime] = None


class WhatsAppConsentRevoke(BaseModel):
    reason: str = Field(min_length=1, max_length=1000)


class WhatsAppConsentRead(OrmModel):
    id: int
    contact_id: int
    contact_point_id: int
    idempotency_key: str
    source: str
    evidence_url: Optional[str]
    evidence_text: str
    granted_at: datetime
    expires_at: Optional[datetime]
    revoked_at: Optional[datetime]
    revocation_reason: Optional[str]


class ConversationRead(OrmModel):
    id: int
    company_id: int
    contact_id: int
    contact_point_id: Optional[int]
    channel: Channel
    status: ConversationStatus
    subject: Optional[str]
    latest_reply_body: Optional[str]
    last_message_at: Optional[datetime]


class WebhookEventCreate(BaseModel):
    channel: Channel
    direction: MessageDirection
    event_type: MessageEventType
    attempt_id: Optional[int] = None
    conversation_id: Optional[int] = None
    company_id: Optional[int] = None
    contact_id: Optional[int] = None
    contact_point_id: Optional[int] = None
    provider_event_id: Optional[str] = Field(default=None, max_length=500)
    provider_message_id: Optional[str] = Field(default=None, max_length=500)
    subject: Optional[str] = Field(default=None, max_length=1000)
    body: Optional[str] = None
    occurred_at: Optional[datetime] = None
    metadata_json: dict[str, Any] = Field(default_factory=dict)


class MessageEventRead(OrmModel):
    id: int
    conversation_id: Optional[int]
    outreach_attempt_id: Optional[int]
    channel: Channel
    direction: MessageDirection
    event_type: MessageEventType
    provider: Optional[str]
    ingest_idempotency_key: Optional[str]
    provider_event_id: Optional[str]
    provider_message_id: Optional[str]
    subject: Optional[str]
    body: Optional[str]
    latest_body: Optional[str]
    occurred_at: datetime
    metadata_json: Optional[dict[str, Any]]


class ReplyAssessmentRead(OrmModel):
    id: int
    conversation_id: int
    message_event_id: Optional[int]
    enrollment_id: Optional[int]
    intent: ReplyIntent
    is_positive: bool
    confidence: Optional[Decimal]
    status: ReplyAssessmentStatus
    latest_reply_body: str
    rationale: Optional[str]
    confirmed_at: Optional[datetime]


class ReplyConfirmation(BaseModel):
    intent: ReplyIntent
    is_positive: bool


class OpportunityConfirm(BaseModel):
    reply_assessment_id: int
    source_task_id: int
    assignee_user_id: int
    next_action: str = Field(min_length=1, max_length=1000)
    next_action_due_at: datetime
    fit_confirmed: bool = False
    fit_override_id: Optional[int] = None
    value_amount: Optional[Decimal] = Field(default=None, ge=0)
    currency: Optional[str] = Field(default=None, min_length=3, max_length=3)
    expected_close_date: Optional[date] = None


class OpportunityStageUpdate(BaseModel):
    stage: OpportunityStage
    value_amount: Optional[Decimal] = Field(default=None, ge=0)
    currency: Optional[str] = Field(default=None, min_length=3, max_length=3)
    deal_date: Optional[date] = None
    lost_reason: Optional[str] = Field(default=None, max_length=1000)
    next_action: Optional[str] = Field(default=None, max_length=1000)
    next_action_due_at: Optional[datetime] = None

    @model_validator(mode="after")
    def validate_terminal_stage(self):
        if self.stage == OpportunityStage.WON:
            if self.value_amount is None or not self.currency or self.deal_date is None:
                raise ValueError("Won requires value_amount, currency, and deal_date")
        if self.stage == OpportunityStage.LOST and not self.lost_reason:
            raise ValueError("Lost requires lost_reason")
        return self


class OpportunityRead(OrmModel):
    id: int
    owner_id: int
    assignee_user_id: int
    company_id: int
    contact_id: int
    campaign_id: Optional[int]
    conversation_id: int
    reply_assessment_id: int
    source_task_id: int
    stage: OpportunityStage
    fit_confirmed: bool
    value_amount: Optional[Decimal]
    currency: Optional[str]
    expected_close_date: Optional[date]
    next_action: str
    next_action_due_at: datetime
    qualified_at: datetime
    won_at: Optional[datetime]
    lost_at: Optional[datetime]
    lost_reason: Optional[str]
    archived_at: Optional[datetime]


class ConsentRestrictionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    scope: RestrictionScope
    channel: Optional[Channel] = None
    contact_point_id: Optional[int] = None
    contact_id: Optional[int] = None
    company_id: Optional[int] = None
    reason: str = Field(min_length=1, max_length=500)
    source: str = Field(default="manual", min_length=1, max_length=100)
    company_scope_confirmed: bool = False

    @model_validator(mode="after")
    def validate_scope_targets(self):
        targets = {
            RestrictionScope.CONTACT_POINT: self.contact_point_id,
            RestrictionScope.CONTACT: self.contact_id,
            RestrictionScope.COMPANY: self.company_id,
        }
        expected_target = targets.get(self.scope)
        if self.scope != RestrictionScope.GLOBAL and expected_target is None:
            raise ValueError(f"{self.scope.value}_id is required")

        extraneous = {
            RestrictionScope.CONTACT_POINT: (self.contact_id, self.company_id),
            RestrictionScope.CONTACT: (self.contact_point_id, self.company_id),
            RestrictionScope.COMPANY: (self.contact_point_id, self.contact_id),
            RestrictionScope.GLOBAL: (
                self.contact_point_id,
                self.contact_id,
                self.company_id,
            ),
        }[self.scope]
        if any(target is not None for target in extraneous):
            raise ValueError(f"Only the {self.scope.value} target may be supplied")
        if self.scope != RestrictionScope.CONTACT_POINT and self.channel is not None:
            raise ValueError("channel is only valid for contact_point restrictions")
        return self


class ConsentRestrictionRead(OrmModel):
    id: int
    idempotency_key: str
    scope: RestrictionScope
    channel: Optional[Channel]
    contact_point_id: Optional[int]
    contact_id: Optional[int]
    company_id: Optional[int]
    reason: str
    source: str
    active: bool
    created_at: datetime


class ManualOverrideCreate(BaseModel):
    gate: OverrideGate
    enrollment_id: int
    attempt_id: Optional[int] = None
    reason: str = Field(min_length=3, max_length=1000)
    expires_at: datetime


class ManualOverrideRead(OrmModel):
    id: int
    gate: OverrideGate
    enrollment_id: int
    attempt_id: Optional[int]
    reason: str
    expires_at: datetime
    created_by_user_id: int
    consumed_at: Optional[datetime]
    revoked_at: Optional[datetime]


class SafetyLockRead(OrmModel):
    id: int
    scope: SafetyLockScope
    campaign_id: Optional[int]
    company_id: Optional[int]
    contact_id: Optional[int]
    channel_account_id: Optional[int]
    channel: Optional[Channel]
    code: str
    reason: str
    active: bool
    locked_at: datetime
    unlocked_at: Optional[datetime]
    unlocked_by_user_id: Optional[int]
    metadata_json: Optional[dict[str, Any]]


class SafetyLockRelease(BaseModel):
    reason: str = Field(min_length=10, max_length=1000)
    evidence_id: str = Field(
        min_length=3,
        max_length=255,
        pattern=r"^[a-zA-Z0-9][a-zA-Z0-9_.:/-]*$",
    )
    human_confirmed: Literal[True]


class WorkerHeartbeatUpsert(BaseModel):
    worker_name: str = Field(min_length=1, max_length=255)
    worker_type: WorkerType
    status: StageStatus
    lease_seconds: int = Field(default=90, ge=5, le=3600)
    details: dict[str, Any] = Field(default_factory=dict)


class WorkerHeartbeatRead(OrmModel):
    worker_name: str
    worker_type: WorkerType
    status: StageStatus
    last_seen_at: datetime
    lease_expires_at: Optional[datetime]
    details: Optional[dict[str, Any]]


class StageRuntimeRead(OrmModel):
    id: int
    campaign_id: int
    stage_name: str
    status: StageStatus
    reason: Optional[str]
    last_started_at: Optional[datetime]
    last_succeeded_at: Optional[datetime]
    last_failed_at: Optional[datetime]
    details: Optional[dict[str, Any]]
    updated_at: datetime


class JobRead(OrmModel):
    id: int
    status: JobStatus
    job_type: str
    queue: str
    priority: int
    scheduled_at: datetime
    attempts: int
    max_attempts: int
    last_error: Optional[str]
    result: Optional[dict[str, Any]]
    completed_at: Optional[datetime]


class ProviderNativeUsage(BaseModel):
    provider: str
    unit: str
    units: float
    results: int


class ProviderNormalizedUsage(BaseModel):
    currency: Optional[str]
    amount: float


class ProviderUsageRead(BaseModel):
    native: list[ProviderNativeUsage]
    normalized: list[ProviderNormalizedUsage]


class OutcomeNorthStar(BaseModel):
    qualified_opportunities: int


class OutcomeCounts(BaseModel):
    won: int
    positive_replies: int


class OutcomeDiagnostics(BaseModel):
    successful_attempts: int


class OutcomeAnalyticsRead(BaseModel):
    north_star: OutcomeNorthStar
    outcomes: OutcomeCounts
    diagnostics: OutcomeDiagnostics
