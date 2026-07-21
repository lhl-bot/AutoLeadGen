"""SQLAlchemy persistence model for AutoLeadGen Product V2.

The tables are additive and deliberately prefixed with ``v2_``.  Nothing in
this module mutates or aliases a legacy table, so the new read/write path can
be exercised against an isolated database while V1 remains available.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from decimal import Decimal
from typing import Type

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    CheckConstraint,
    Column,
    Date,
    DateTime,
    Enum as SAEnum,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    event,
    inspect,
)
from sqlalchemy.orm import mapped_column

from database import Base
from product_v2.enums import (
    AttemptKind,
    AttemptStatus,
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
    OwnerWritePath,
    OverrideGate,
    ProviderCostStatus,
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
    ValueEnum,
    WorkerType,
)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def contact_point_identity_hash(normalized_value: str) -> str:
    """Return the stable digest used by the cross-channel identity key."""

    return hashlib.sha256(normalized_value.encode("utf-8")).hexdigest()


def _contact_point_identity_hash_default(context) -> str:
    return contact_point_identity_hash(context.get_current_parameters()["normalized_value"])


def enum_type(enum_cls: Type[ValueEnum], *, constraint_name: str) -> SAEnum:
    """Persist enum values with a schema-wide unique CHECK constraint name.

    MySQL requires CHECK constraint names to be unique across the schema, not
    merely within a table.  Making the name explicit at every column keeps the
    generated DDL deterministic and prevents two uses of the same enum class
    from colliding during an additive migration.
    """

    if not constraint_name.startswith("ck_v2_"):
        raise ValueError("Product V2 enum constraint names must start with 'ck_v2_'")
    if len(constraint_name) > 64:
        raise ValueError("MySQL constraint names must not exceed 64 characters")

    values = [member.value for member in enum_cls]
    return SAEnum(
        enum_cls,
        values_callable=lambda cls: [member.value for member in cls],
        native_enum=False,
        validate_strings=True,
        create_constraint=True,
        name=constraint_name,
        length=max(len(value) for value in values),
    )


class TimestampMixin:
    created_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)


class ArchiveMixin:
    archived_at = Column(DateTime(timezone=True), nullable=True, index=True)


class LegacySourceMixin:
    legacy_source_table = Column(String(100), nullable=True)
    legacy_id = Column(String(100), nullable=True)


class Company(Base, TimestampMixin, ArchiveMixin, LegacySourceMixin):
    __tablename__ = "v2_companies"
    __table_args__ = (
        UniqueConstraint("owner_id", "normalized_domain", name="uq_v2_company_owner_domain"),
        UniqueConstraint(
            "owner_id", "legacy_source_table", "legacy_id", name="uq_v2_company_legacy_source"
        ),
        Index("ix_v2_company_owner_archived", "owner_id", "archived_at"),
    )

    id = Column(Integer, primary_key=True)
    owner_id = Column(Integer, ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True)
    name = Column(String(255), nullable=False)
    normalized_domain = Column(String(255), nullable=True, index=True)
    website = Column(String(1000), nullable=True)
    country = Column(String(100), nullable=True)
    region = Column(String(100), nullable=True)
    industry = Column(String(255), nullable=True)
    timezone = Column(String(100), nullable=True)
    last_cold_outreach_at = Column(DateTime(timezone=True), nullable=True, index=True)


class Contact(Base, TimestampMixin, ArchiveMixin, LegacySourceMixin):
    __tablename__ = "v2_contacts"
    __table_args__ = (
        UniqueConstraint(
            "owner_id", "legacy_source_table", "legacy_id", name="uq_v2_contact_legacy_source"
        ),
        Index("ix_v2_contact_owner_company", "owner_id", "company_id"),
    )

    id = Column(Integer, primary_key=True)
    owner_id = Column(Integer, ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True)
    company_id = Column(Integer, ForeignKey("v2_companies.id", ondelete="RESTRICT"), nullable=False, index=True)
    first_name = Column(String(120), nullable=True)
    last_name = Column(String(120), nullable=True)
    full_name = Column(String(255), nullable=False)
    job_title = Column(String(255), nullable=True)
    department = Column(String(255), nullable=True)
    timezone = Column(String(100), nullable=True)
    locale = Column(String(50), nullable=True)


class ContactPoint(Base, TimestampMixin, ArchiveMixin, LegacySourceMixin):
    __tablename__ = "v2_contact_points"
    __table_args__ = (
        UniqueConstraint(
            "owner_id",
            "channel",
            "normalized_value_hash",
            name="uq_v2_contact_point_owner_identity_hash",
        ),
        UniqueConstraint(
            "owner_id", "legacy_source_table", "legacy_id", name="uq_v2_contact_point_legacy_source"
        ),
        Index("ix_v2_contact_point_contact_channel", "contact_id", "channel"),
    )

    id = Column(Integer, primary_key=True)
    owner_id = Column(Integer, ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True)
    company_id = Column(Integer, ForeignKey("v2_companies.id", ondelete="RESTRICT"), nullable=False, index=True)
    contact_id = Column(Integer, ForeignKey("v2_contacts.id", ondelete="RESTRICT"), nullable=False, index=True)
    channel = Column(
        enum_type(Channel, constraint_name="ck_v2_contact_point_channel"), nullable=False
    )
    value = Column(String(1000), nullable=False)
    normalized_value = Column(String(1000), nullable=False)
    normalized_value_hash = Column(
        String(64), nullable=False, default=_contact_point_identity_hash_default
    )
    verification_status = Column(
        enum_type(
            ContactPointVerificationStatus,
            constraint_name="ck_v2_contact_point_verification",
        ),
        nullable=False,
        default=ContactPointVerificationStatus.UNVERIFIED,
    )
    availability_status = Column(
        enum_type(
            ContactPointAvailabilityStatus,
            constraint_name="ck_v2_contact_point_availability",
        ),
        nullable=False,
        default=ContactPointAvailabilityStatus.AVAILABLE,
    )
    is_primary = Column(Boolean, default=False, nullable=False)
    verified_at = Column(DateTime(timezone=True), nullable=True)
    last_cold_outreach_at = Column(DateTime(timezone=True), nullable=True, index=True)


class OwnerMigrationState(Base, TimestampMixin):
    """Durable owner-level switch selecting the only writable product path.

    Absence is intentionally meaningful and is interpreted as ``legacy`` by
    the cutover service.  Once a row exists, every transition increments the
    compare-and-switch version while serializing on the stable legacy user
    row.
    """

    __tablename__ = "v2_owner_migration_states"
    __table_args__ = (
        CheckConstraint("version >= 1", name="ck_v2_owner_migration_state_version"),
        Index("ix_v2_owner_migration_state_path", "current_path", "updated_at"),
    )

    owner_id = Column(
        Integer,
        ForeignKey("users.id", ondelete="RESTRICT"),
        primary_key=True,
        nullable=False,
    )
    current_path = Column(
        enum_type(OwnerWritePath, constraint_name="ck_v2_owner_migration_state_path"),
        nullable=False,
        default=OwnerWritePath.LEGACY,
    )
    version = Column(Integer, nullable=False, default=1)
    switched_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)
    switched_by_user_id = Column(
        Integer,
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )


class ChannelAccount(Base, TimestampMixin, ArchiveMixin):
    """V2-safe sender binding without copying Provider credentials.

    Credentials remain in the legacy connector stores during the migration
    window.  This row is the stable, tenant-scoped identity used by immutable
    Sequence revisions, Attempts, capacity accounting and account SafetyLocks.
    Fake accounts intentionally have neither legacy foreign key.
    """

    __tablename__ = "v2_channel_accounts"
    __table_args__ = (
        UniqueConstraint(
            "owner_id",
            "channel",
            "provider",
            "provider_account_id",
            name="uq_v2_channel_account_identity",
        ),
        UniqueConstraint(
            "legacy_email_account_id",
            name="uq_v2_channel_account_legacy_email",
        ),
        UniqueConstraint(
            "legacy_channel_account_id",
            name="uq_v2_channel_account_legacy_channel",
        ),
        CheckConstraint(
            "legacy_email_account_id IS NULL OR legacy_channel_account_id IS NULL",
            name="ck_v2_channel_account_one_legacy_source",
        ),
        CheckConstraint(
            "legacy_email_account_id IS NULL OR channel = 'email'",
            name="ck_v2_channel_account_email_source_channel",
        ),
        CheckConstraint(
            "legacy_channel_account_id IS NULL OR channel IN ('linkedin', 'whatsapp')",
            name="ck_v2_channel_account_omni_source_channel",
        ),
        CheckConstraint(
            "daily_limit IS NULL OR daily_limit >= 0",
            name="ck_v2_channel_account_daily_limit_nonnegative",
        ),
        CheckConstraint(
            "channel <> 'offline'",
            name="ck_v2_channel_account_outbound_channel",
        ),
        Index(
            "ix_v2_channel_account_owner_channel_health",
            "owner_id",
            "channel",
            "enabled",
            "health_status",
            "archived_at",
        ),
    )

    id = Column(Integer, primary_key=True)
    owner_id = Column(Integer, ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True)
    channel = Column(
        enum_type(Channel, constraint_name="ck_v2_channel_account_channel"), nullable=False
    )
    provider = Column(String(100), nullable=False)
    provider_account_id = Column(String(255), nullable=False)
    legacy_email_account_id = Column(
        Integer, ForeignKey("email_accounts.id", ondelete="RESTRICT"), nullable=True, index=True
    )
    legacy_channel_account_id = Column(
        Integer, ForeignKey("channel_accounts.id", ondelete="RESTRICT"), nullable=True, index=True
    )
    enabled = Column(Boolean, nullable=False, default=True)
    health_status = Column(
        enum_type(ChannelAccountHealth, constraint_name="ck_v2_channel_account_health"),
        nullable=False,
        default=ChannelAccountHealth.UNKNOWN,
    )
    health_checked_at = Column(DateTime(timezone=True), nullable=True)
    daily_limit = Column(Integer, nullable=True)
    timezone = Column(String(100), nullable=False, default="UTC")
    last_error = Column(String(1000), nullable=True)


@event.listens_for(ChannelAccount, "before_update", propagate=True)
def _prevent_channel_account_identity_drift(_mapper, _connection, target) -> None:
    """Attempts and immutable revisions must never point at a rewritten identity."""

    state = inspect(target)
    immutable_fields = (
        "owner_id",
        "channel",
        "provider",
        "provider_account_id",
        "legacy_email_account_id",
        "legacy_channel_account_id",
    )
    changed = [name for name in immutable_fields if state.attrs[name].history.has_changes()]
    if changed:
        raise ValueError(
            "Channel account identity is immutable; create a new binding "
            f"instead of changing {','.join(changed)}"
        )


class AudienceList(Base, TimestampMixin, ArchiveMixin, LegacySourceMixin):
    __tablename__ = "v2_audience_lists"
    __table_args__ = (
        UniqueConstraint("owner_id", "name", name="uq_v2_audience_list_owner_name"),
        UniqueConstraint(
            "owner_id", "legacy_source_table", "legacy_id", name="uq_v2_audience_list_legacy_source"
        ),
    )

    id = Column(Integer, primary_key=True)
    owner_id = Column(Integer, ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True)
    name = Column(String(255), nullable=False)
    description = Column(Text, nullable=True)


class ListMembership(Base, TimestampMixin, ArchiveMixin, LegacySourceMixin):
    __tablename__ = "v2_list_memberships"
    __table_args__ = (
        CheckConstraint(
            "(contact_id IS NOT NULL AND company_id IS NULL) OR "
            "(contact_id IS NULL AND company_id IS NOT NULL)",
            name="ck_v2_list_membership_one_subject",
        ),
        UniqueConstraint("audience_list_id", "contact_id", name="uq_v2_list_contact"),
        UniqueConstraint("audience_list_id", "company_id", name="uq_v2_list_company"),
        UniqueConstraint(
            "owner_id", "legacy_source_table", "legacy_id", name="uq_v2_membership_legacy_source"
        ),
    )

    id = Column(Integer, primary_key=True)
    owner_id = Column(Integer, ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True)
    audience_list_id = Column(
        Integer, ForeignKey("v2_audience_lists.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    contact_id = Column(Integer, ForeignKey("v2_contacts.id", ondelete="RESTRICT"), nullable=True, index=True)
    company_id = Column(Integer, ForeignKey("v2_companies.id", ondelete="RESTRICT"), nullable=True, index=True)
    added_by_user_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)


class EvidenceSnapshot(Base, TimestampMixin, ArchiveMixin, LegacySourceMixin):
    __tablename__ = "v2_evidence_snapshots"
    __table_args__ = (
        CheckConstraint(
            "company_id IS NOT NULL OR contact_id IS NOT NULL", name="ck_v2_evidence_has_subject"
        ),
        UniqueConstraint(
            "owner_id", "legacy_source_table", "legacy_id", name="uq_v2_evidence_legacy_source"
        ),
        Index("ix_v2_evidence_subject_version", "company_id", "contact_id", "version"),
    )

    id = Column(Integer, primary_key=True)
    owner_id = Column(Integer, ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True)
    company_id = Column(Integer, ForeignKey("v2_companies.id", ondelete="RESTRICT"), nullable=True, index=True)
    contact_id = Column(Integer, ForeignKey("v2_contacts.id", ondelete="RESTRICT"), nullable=True, index=True)
    source = Column(String(255), nullable=False)
    source_url = Column(String(2000), nullable=True)
    evidence = Column(JSON, nullable=False, default=dict)
    confidence = Column(Numeric(5, 4), nullable=False, default=Decimal("0"))
    version = Column(Integer, nullable=False, default=1)
    captured_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)


class AcquisitionRun(Base, TimestampMixin):
    """Owner-scoped staging run for CSV import or AI-assisted sourcing.

    Candidates stay outside the Company/Contact graph until a human commits
    them.  This lets parsing, deduplication, evidence review, paid verification,
    and retries happen without weakening the authoritative ContactPoint gates.
    """

    __tablename__ = "v2_acquisition_runs"
    __table_args__ = (
        CheckConstraint(
            "source IN ('csv', 'ai')",
            name="ck_v2_acquisition_run_source",
        ),
        CheckConstraint(
            "status IN ('draft', 'ready', 'processing', 'verified', 'committed', 'failed')",
            name="ck_v2_acquisition_run_status",
        ),
        UniqueConstraint(
            "owner_id",
            "idempotency_key",
            name="uq_v2_acquisition_run_owner_idempotency",
        ),
        Index("ix_v2_acquisition_run_owner_updated", "owner_id", "updated_at"),
        Index("ix_v2_acquisition_run_owner_status", "owner_id", "status"),
    )

    id = Column(Integer, primary_key=True)
    owner_id = Column(Integer, ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True)
    source = Column(String(20), nullable=False)
    status = Column(String(20), nullable=False, default="draft")
    name = Column(String(255), nullable=False)
    idempotency_key = Column(String(255), nullable=False)
    criteria = Column(JSON, nullable=False, default=dict)
    column_mapping = Column(JSON, nullable=False, default=dict)
    provider = Column(String(100), nullable=True)
    estimated_units = Column(Numeric(18, 4), nullable=True)
    price_version = Column(String(100), nullable=False, default="local-unpriced")
    last_error = Column(Text, nullable=True)
    committed_at = Column(DateTime(timezone=True), nullable=True)


class AcquisitionCandidate(Base, TimestampMixin):
    """Reviewable candidate that has not yet become trusted customer data."""

    __tablename__ = "v2_acquisition_candidates"
    __table_args__ = (
        CheckConstraint(
            "status IN ('ready', 'duplicate', 'invalid', 'selected', 'committed')",
            name="ck_v2_acquisition_candidate_status",
        ),
        UniqueConstraint("run_id", "row_number", name="uq_v2_acquisition_candidate_run_row"),
        Index("ix_v2_acquisition_candidate_run_status", "run_id", "status"),
    )

    id = Column(Integer, primary_key=True)
    owner_id = Column(Integer, ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True)
    run_id = Column(
        Integer,
        ForeignKey("v2_acquisition_runs.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    row_number = Column(Integer, nullable=False)
    status = Column(String(20), nullable=False, default="ready")
    selected = Column(Boolean, nullable=False, default=False)
    company_name = Column(String(255), nullable=True)
    normalized_domain = Column(String(255), nullable=True)
    first_name = Column(String(120), nullable=True)
    last_name = Column(String(120), nullable=True)
    full_name = Column(String(255), nullable=True)
    job_title = Column(String(255), nullable=True)
    email = Column(String(1000), nullable=True)
    normalized_email = Column(String(1000), nullable=True)
    source_url = Column(String(2000), nullable=True)
    evidence = Column(JSON, nullable=False, default=dict)
    confidence = Column(Numeric(5, 4), nullable=False, default=Decimal("0"))
    verification_status = Column(
        enum_type(
            ContactPointVerificationStatus,
            constraint_name="ck_v2_acquisition_candidate_verification",
        ),
        nullable=False,
        default=ContactPointVerificationStatus.UNVERIFIED,
    )
    verification_source = Column(String(100), nullable=True)
    verification_checked_at = Column(DateTime(timezone=True), nullable=True)
    rejection_reason = Column(String(1000), nullable=True)
    committed_company_id = Column(
        Integer, ForeignKey("v2_companies.id", ondelete="SET NULL"), nullable=True, index=True
    )
    committed_contact_id = Column(
        Integer, ForeignKey("v2_contacts.id", ondelete="SET NULL"), nullable=True, index=True
    )
    committed_contact_point_id = Column(
        Integer, ForeignKey("v2_contact_points.id", ondelete="SET NULL"), nullable=True, index=True
    )


class Campaign(Base, TimestampMixin, ArchiveMixin, LegacySourceMixin):
    __tablename__ = "v2_campaigns"
    __table_args__ = (
        UniqueConstraint("owner_id", "name", name="uq_v2_campaign_owner_name"),
        UniqueConstraint(
            "owner_id", "legacy_source_table", "legacy_id", name="uq_v2_campaign_legacy_source"
        ),
        Index("ix_v2_campaign_owner_lifecycle", "owner_id", "lifecycle"),
    )

    id = Column(Integer, primary_key=True)
    owner_id = Column(Integer, ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True)
    name = Column(String(255), nullable=False)
    description = Column(Text, nullable=True)
    lifecycle = Column(
        enum_type(CampaignLifecycle, constraint_name="ck_v2_campaign_lifecycle"),
        nullable=False,
        default=CampaignLifecycle.DRAFT,
        index=True,
    )
    run_mode = Column(
        enum_type(CampaignRunMode, constraint_name="ck_v2_campaign_run_mode"),
        nullable=False,
        default=CampaignRunMode.SHADOW,
    )
    priority = Column(Integer, nullable=False, default=100)
    published_revision_number = Column(Integer, nullable=True)
    started_at = Column(DateTime(timezone=True), nullable=True)
    paused_at = Column(DateTime(timezone=True), nullable=True)
    completed_at = Column(DateTime(timezone=True), nullable=True)


class CampaignRevision(Base, TimestampMixin, ArchiveMixin, LegacySourceMixin):
    __tablename__ = "v2_campaign_revisions"
    __table_args__ = (
        UniqueConstraint("campaign_id", "revision_number", name="uq_v2_campaign_revision_number"),
        UniqueConstraint(
            "owner_id", "legacy_source_table", "legacy_id", name="uq_v2_revision_legacy_source"
        ),
        Index("ix_v2_campaign_revision_status", "campaign_id", "status"),
    )

    id = Column(Integer, primary_key=True)
    owner_id = Column(Integer, ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True)
    campaign_id = Column(Integer, ForeignKey("v2_campaigns.id", ondelete="RESTRICT"), nullable=False, index=True)
    revision_number = Column(Integer, nullable=False)
    status = Column(
        enum_type(CampaignRevisionStatus, constraint_name="ck_v2_campaign_revision_status"),
        nullable=False,
        default=CampaignRevisionStatus.DRAFT,
    )
    icp_definition = Column(JSON, nullable=False, default=dict)
    audience_definition = Column(JSON, nullable=False, default=dict)
    quality_gates = Column(JSON, nullable=False, default=dict)
    budget_definition = Column(JSON, nullable=False, default=dict)
    stop_conditions = Column(JSON, nullable=False, default=dict)
    published_at = Column(DateTime(timezone=True), nullable=True)
    published_by_user_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)


class SequenceStep(Base, TimestampMixin, ArchiveMixin):
    __tablename__ = "v2_sequence_steps"
    __table_args__ = (
        UniqueConstraint("campaign_revision_id", "position", name="uq_v2_sequence_step_position"),
        Index("ix_v2_sequence_revision_channel", "campaign_revision_id", "channel"),
    )

    id = Column(Integer, primary_key=True)
    owner_id = Column(Integer, ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True)
    campaign_revision_id = Column(
        Integer, ForeignKey("v2_campaign_revisions.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    channel_account_id = Column(
        Integer, ForeignKey("v2_channel_accounts.id", ondelete="RESTRICT"), nullable=True, index=True
    )
    position = Column(Integer, nullable=False)
    channel = Column(
        enum_type(Channel, constraint_name="ck_v2_sequence_step_channel"), nullable=False
    )
    wait_minutes = Column(Integer, nullable=False, default=0)
    template_version = Column(String(100), nullable=True)
    condition_definition = Column(JSON, nullable=False, default=dict)
    stop_condition_definition = Column(JSON, nullable=False, default=dict)
    # Added after the original table existed. Keep these at the physical tail
    # so metadata fingerprints match additive MySQL/SQLite migrations.
    subject_template = mapped_column(Text, nullable=True, sort_order=1000)
    body_template = mapped_column(Text, nullable=True, sort_order=1001)


class Enrollment(Base, TimestampMixin, ArchiveMixin, LegacySourceMixin):
    __tablename__ = "v2_enrollments"
    __table_args__ = (
        UniqueConstraint("campaign_id", "contact_id", name="uq_v2_campaign_contact_enrollment"),
        UniqueConstraint(
            "owner_id", "legacy_source_table", "legacy_id", name="uq_v2_enrollment_legacy_source"
        ),
        Index("ix_v2_enrollment_schedule", "status", "scheduled_at"),
        Index("ix_v2_enrollment_company_campaign", "company_id", "campaign_id", "status"),
    )

    id = Column(Integer, primary_key=True)
    owner_id = Column(Integer, ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True)
    campaign_id = Column(Integer, ForeignKey("v2_campaigns.id", ondelete="RESTRICT"), nullable=False, index=True)
    campaign_revision_id = Column(
        Integer, ForeignKey("v2_campaign_revisions.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    company_id = Column(Integer, ForeignKey("v2_companies.id", ondelete="RESTRICT"), nullable=False, index=True)
    contact_id = Column(Integer, ForeignKey("v2_contacts.id", ondelete="RESTRICT"), nullable=False, index=True)
    status = Column(
        enum_type(EnrollmentStatus, constraint_name="ck_v2_enrollment_status"),
        nullable=False,
        default=EnrollmentStatus.SCHEDULED,
    )
    scheduled_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)
    priority_snapshot = Column(Integer, nullable=False, default=100)
    paused_reason = Column(String(500), nullable=True)
    paused_at = Column(DateTime(timezone=True), nullable=True)
    positive_signal_at = Column(DateTime(timezone=True), nullable=True)
    completed_at = Column(DateTime(timezone=True), nullable=True)


class OutreachAttempt(Base, TimestampMixin, ArchiveMixin):
    __tablename__ = "v2_outreach_attempts"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_v2_attempt_idempotency"),
        Index("ix_v2_attempt_claim", "status", "scheduled_at", "lease_expires_at"),
        Index("ix_v2_attempt_campaign_status", "campaign_id", "status"),
        Index(
            "ix_v2_attempt_account_capacity",
            "channel_account_id",
            "capacity_reserved_at",
        ),
    )

    id = Column(Integer, primary_key=True)
    owner_id = Column(Integer, ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True)
    campaign_id = Column(Integer, ForeignKey("v2_campaigns.id", ondelete="RESTRICT"), nullable=False, index=True)
    enrollment_id = Column(Integer, ForeignKey("v2_enrollments.id", ondelete="RESTRICT"), nullable=False, index=True)
    sequence_step_id = Column(
        Integer, ForeignKey("v2_sequence_steps.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    contact_point_id = Column(
        Integer, ForeignKey("v2_contact_points.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    channel_account_id = Column(
        Integer, ForeignKey("v2_channel_accounts.id", ondelete="RESTRICT"), nullable=True, index=True
    )
    channel = Column(
        enum_type(Channel, constraint_name="ck_v2_outreach_attempt_channel"), nullable=False
    )
    kind = Column(
        enum_type(AttemptKind, constraint_name="ck_v2_outreach_attempt_kind"),
        nullable=False,
        default=AttemptKind.COLD,
    )
    idempotency_key = Column(String(255), nullable=False)
    status = Column(
        enum_type(AttemptStatus, constraint_name="ck_v2_outreach_attempt_status"),
        nullable=False,
        default=AttemptStatus.QUEUED,
    )
    priority = Column(Integer, nullable=False, default=100)
    scheduled_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)
    claimed_by = Column(String(255), nullable=True)
    lease_expires_at = Column(DateTime(timezone=True), nullable=True)
    attempt_count = Column(Integer, nullable=False, default=0)
    provider = Column(String(100), nullable=True)
    provider_message_id = Column(String(500), nullable=True, index=True)
    provider_response = Column(JSON, nullable=True)
    unknown_reason = Column(Text, nullable=True)
    last_error = Column(Text, nullable=True)
    sent_at = Column(DateTime(timezone=True), nullable=True)
    capacity_reserved_at = Column(DateTime(timezone=True), nullable=True, index=True)


class RouteProposal(Base, TimestampMixin):
    """A per-contact route proposed for human review.

    The checksum covers all route content.  Once approved, the proposal is
    frozen and copied into EnrollmentRouteStep; the executor never re-runs AI
    or mutates the approved route at send time.
    """

    __tablename__ = "v2_route_proposals"
    __table_args__ = (
        UniqueConstraint("owner_id", "idempotency_key", name="uq_v2_route_proposal_idempotency"),
        Index("ix_v2_route_proposal_owner_status", "owner_id", "status", "created_at"),
        Index("ix_v2_route_proposal_enrollment", "enrollment_id", "status"),
        CheckConstraint("confidence >= 0 AND confidence <= 1", name="ck_v2_route_proposal_confidence"),
    )

    id = Column(Integer, primary_key=True)
    owner_id = Column(Integer, ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True)
    enrollment_id = Column(Integer, ForeignKey("v2_enrollments.id", ondelete="RESTRICT"), nullable=False, index=True)
    contact_id = Column(Integer, ForeignKey("v2_contacts.id", ondelete="RESTRICT"), nullable=False, index=True)
    status = Column(
        enum_type(RouteProposalStatus, constraint_name="ck_v2_route_proposal_status"),
        nullable=False,
        default=RouteProposalStatus.DRAFT,
    )
    idempotency_key = Column(String(255), nullable=False)
    ai_model = Column(String(100), nullable=False)
    ai_reason = Column(Text, nullable=False)
    confidence = Column(Numeric(5, 4), nullable=False)
    evidence_snapshot_ids = Column(JSON, nullable=False, default=list)
    checksum = Column(String(64), nullable=False)
    proposed_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)
    approved_at = Column(DateTime(timezone=True), nullable=True)
    rejected_at = Column(DateTime(timezone=True), nullable=True)


class RouteProposalStep(Base, TimestampMixin):
    __tablename__ = "v2_route_proposal_steps"
    __table_args__ = (
        UniqueConstraint("route_proposal_id", "position", name="uq_v2_route_proposal_step_position"),
        UniqueConstraint("route_proposal_id", "channel", name="uq_v2_route_proposal_step_channel"),
        CheckConstraint("position >= 1 AND position <= 3", name="ck_v2_route_proposal_step_position"),
        CheckConstraint("confidence >= 0 AND confidence <= 1", name="ck_v2_route_proposal_step_confidence"),
        Index("ix_v2_route_proposal_step_account", "channel_account_id", "scheduled_at"),
    )

    id = Column(Integer, primary_key=True)
    owner_id = Column(Integer, ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True)
    route_proposal_id = Column(Integer, ForeignKey("v2_route_proposals.id", ondelete="RESTRICT"), nullable=False, index=True)
    sequence_step_id = Column(Integer, ForeignKey("v2_sequence_steps.id", ondelete="RESTRICT"), nullable=False, index=True)
    attempt_id = Column(Integer, ForeignKey("v2_outreach_attempts.id", ondelete="SET NULL"), nullable=True, index=True)
    contact_point_id = Column(Integer, ForeignKey("v2_contact_points.id", ondelete="RESTRICT"), nullable=False, index=True)
    channel_account_id = Column(Integer, ForeignKey("v2_channel_accounts.id", ondelete="RESTRICT"), nullable=False, index=True)
    position = Column(Integer, nullable=False)
    channel = Column(enum_type(Channel, constraint_name="ck_v2_route_proposal_step_channel"), nullable=False)
    scheduled_at = Column(DateTime(timezone=True), nullable=False)
    subject = Column(Text, nullable=True)
    body = Column(Text, nullable=False)
    ai_reason = Column(Text, nullable=False)
    confidence = Column(Numeric(5, 4), nullable=False)
    evidence_snapshot_ids = Column(JSON, nullable=False, default=list)


class ReviewBatch(Base, TimestampMixin):
    __tablename__ = "v2_review_batches"
    __table_args__ = (
        UniqueConstraint("owner_id", "idempotency_key", name="uq_v2_review_batch_idempotency"),
        Index("ix_v2_review_batch_owner_status", "owner_id", "status", "created_at"),
        CheckConstraint("item_count >= 1 AND item_count <= 20", name="ck_v2_review_batch_item_count"),
    )

    id = Column(Integer, primary_key=True)
    owner_id = Column(Integer, ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True)
    status = Column(
        enum_type(ReviewBatchStatus, constraint_name="ck_v2_review_batch_status"),
        nullable=False,
        default=ReviewBatchStatus.DRAFT,
    )
    idempotency_key = Column(String(255), nullable=False)
    approval_id = Column(String(255), nullable=False)
    item_count = Column(Integer, nullable=False)
    preview_checksum = Column(String(64), nullable=True)
    estimated_cost = Column(Numeric(18, 6), nullable=False, default=Decimal("0"))
    price_version = Column(String(100), nullable=False)
    previewed_at = Column(DateTime(timezone=True), nullable=True)
    approved_at = Column(DateTime(timezone=True), nullable=True)
    rejected_at = Column(DateTime(timezone=True), nullable=True)
    decided_by_user_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)


class ReviewBatchItem(Base, TimestampMixin):
    __tablename__ = "v2_review_batch_items"
    __table_args__ = (
        UniqueConstraint("review_batch_id", "route_proposal_id", name="uq_v2_review_batch_route"),
        Index("ix_v2_review_batch_item_batch_position", "review_batch_id", "position"),
    )

    id = Column(Integer, primary_key=True)
    owner_id = Column(Integer, ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True)
    review_batch_id = Column(Integer, ForeignKey("v2_review_batches.id", ondelete="RESTRICT"), nullable=False, index=True)
    route_proposal_id = Column(Integer, ForeignKey("v2_route_proposals.id", ondelete="RESTRICT"), nullable=False, index=True)
    position = Column(Integer, nullable=False)
    proposal_checksum = Column(String(64), nullable=False)
    preview_payload = Column(JSON, nullable=False)
    edited = Column(Boolean, nullable=False, default=False)


class EnrollmentRouteStep(Base, TimestampMixin):
    """Immutable execution instruction materialized by atomic batch approval."""

    __tablename__ = "v2_enrollment_route_steps"
    __table_args__ = (
        UniqueConstraint("enrollment_id", "position", name="uq_v2_enrollment_route_position"),
        UniqueConstraint("enrollment_id", "channel", name="uq_v2_enrollment_route_channel"),
        UniqueConstraint("attempt_id", name="uq_v2_enrollment_route_attempt"),
        UniqueConstraint(
            "route_proposal_step_id",
            name="uq_v2_enrollment_route_steps_route_proposal_step_id",
        ),
        Index("ix_v2_enrollment_route_execution", "status", "scheduled_at"),
        CheckConstraint("position >= 1 AND position <= 3", name="ck_v2_enrollment_route_position"),
    )

    id = Column(Integer, primary_key=True)
    owner_id = Column(Integer, ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True)
    enrollment_id = Column(Integer, ForeignKey("v2_enrollments.id", ondelete="RESTRICT"), nullable=False, index=True)
    route_proposal_id = Column(Integer, ForeignKey("v2_route_proposals.id", ondelete="RESTRICT"), nullable=False, index=True)
    route_proposal_step_id = Column(Integer, ForeignKey("v2_route_proposal_steps.id", ondelete="RESTRICT"), nullable=False)
    review_batch_id = Column(Integer, ForeignKey("v2_review_batches.id", ondelete="RESTRICT"), nullable=False, index=True)
    attempt_id = Column(Integer, ForeignKey("v2_outreach_attempts.id", ondelete="RESTRICT"), nullable=True)
    contact_point_id = Column(Integer, ForeignKey("v2_contact_points.id", ondelete="RESTRICT"), nullable=False)
    channel_account_id = Column(Integer, ForeignKey("v2_channel_accounts.id", ondelete="RESTRICT"), nullable=False)
    position = Column(Integer, nullable=False)
    channel = Column(enum_type(Channel, constraint_name="ck_v2_enrollment_route_channel"), nullable=False)
    scheduled_at = Column(DateTime(timezone=True), nullable=False)
    subject = Column(Text, nullable=True)
    body = Column(Text, nullable=False)
    status = Column(String(20), nullable=False, default="approved")
    approval_checksum = Column(String(64), nullable=False)
    approved_at = Column(DateTime(timezone=True), nullable=False)


@event.listens_for(EnrollmentRouteStep, "before_update", propagate=True)
@event.listens_for(EnrollmentRouteStep, "before_delete", propagate=True)
def _prevent_approved_route_mutation(_mapper, _connection, _target) -> None:
    raise ValueError("Approved enrollment route steps are immutable")


class WhatsAppConsent(Base, TimestampMixin):
    """Auditable proof of affirmative WhatsApp messaging consent."""

    __tablename__ = "v2_whatsapp_consents"
    __table_args__ = (
        UniqueConstraint("owner_id", "idempotency_key", name="uq_v2_whatsapp_consent_idempotency"),
        Index("ix_v2_whatsapp_consent_active", "owner_id", "contact_point_id", "revoked_at", "expires_at"),
    )

    id = Column(Integer, primary_key=True)
    owner_id = Column(Integer, ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True)
    contact_id = Column(Integer, ForeignKey("v2_contacts.id", ondelete="RESTRICT"), nullable=False, index=True)
    contact_point_id = Column(Integer, ForeignKey("v2_contact_points.id", ondelete="RESTRICT"), nullable=False, index=True)
    idempotency_key = Column(String(255), nullable=False)
    source = Column(String(100), nullable=False)
    evidence_url = Column(String(2000), nullable=True)
    evidence_text = Column(Text, nullable=False)
    captured_by_user_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    granted_at = Column(DateTime(timezone=True), nullable=False)
    expires_at = Column(DateTime(timezone=True), nullable=True)
    revoked_at = Column(DateTime(timezone=True), nullable=True)
    revoked_by_user_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    revocation_reason = Column(String(1000), nullable=True)


class Conversation(Base, TimestampMixin, ArchiveMixin):
    __tablename__ = "v2_conversations"
    __table_args__ = (
        Index("ix_v2_conversation_owner_last_message", "owner_id", "last_message_at"),
        Index("ix_v2_conversation_contact_channel", "contact_id", "channel"),
    )

    id = Column(Integer, primary_key=True)
    owner_id = Column(Integer, ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True)
    company_id = Column(Integer, ForeignKey("v2_companies.id", ondelete="RESTRICT"), nullable=False, index=True)
    contact_id = Column(Integer, ForeignKey("v2_contacts.id", ondelete="RESTRICT"), nullable=False, index=True)
    contact_point_id = Column(
        Integer, ForeignKey("v2_contact_points.id", ondelete="RESTRICT"), nullable=True, index=True
    )
    channel = Column(
        enum_type(Channel, constraint_name="ck_v2_conversation_channel"), nullable=False
    )
    status = Column(
        enum_type(ConversationStatus, constraint_name="ck_v2_conversation_status"),
        nullable=False,
        default=ConversationStatus.OPEN,
    )
    provider_thread_id = Column(String(500), nullable=True, index=True)
    subject = Column(String(1000), nullable=True)
    latest_reply_body = Column(Text, nullable=True)
    last_message_at = Column(DateTime(timezone=True), nullable=True, index=True)


class MessageEvent(Base, TimestampMixin):
    __tablename__ = "v2_message_events"
    __table_args__ = (
        UniqueConstraint(
            "owner_id", "provider", "provider_event_id", name="uq_v2_message_provider_event"
        ),
        UniqueConstraint(
            "owner_id", "ingest_idempotency_key", name="uq_v2_message_ingest_idempotency"
        ),
        Index("ix_v2_message_conversation_occurred", "conversation_id", "occurred_at"),
        Index("ix_v2_message_attempt_event", "outreach_attempt_id", "event_type"),
    )

    id = Column(Integer, primary_key=True)
    owner_id = Column(Integer, ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True)
    conversation_id = Column(
        Integer, ForeignKey("v2_conversations.id", ondelete="RESTRICT"), nullable=True, index=True
    )
    outreach_attempt_id = Column(
        Integer, ForeignKey("v2_outreach_attempts.id", ondelete="RESTRICT"), nullable=True, index=True
    )
    channel = Column(
        enum_type(Channel, constraint_name="ck_v2_message_event_channel"), nullable=False
    )
    direction = Column(
        enum_type(MessageDirection, constraint_name="ck_v2_message_event_direction"),
        nullable=False,
    )
    event_type = Column(
        enum_type(MessageEventType, constraint_name="ck_v2_message_event_type"),
        nullable=False,
    )
    provider = Column(String(100), nullable=True)
    ingest_idempotency_key = Column(String(255), nullable=True)
    provider_event_id = Column(String(500), nullable=True)
    provider_message_id = Column(String(500), nullable=True, index=True)
    subject = Column(String(1000), nullable=True)
    body = Column(Text, nullable=True)
    latest_body = Column(Text, nullable=True, doc="Reply body after quoted history/signature removal")
    occurred_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)
    metadata_json = Column(JSON, nullable=True)


class ReplyAssessment(Base, TimestampMixin):
    __tablename__ = "v2_reply_assessments"
    __table_args__ = (
        Index("ix_v2_reply_assessment_conversation", "conversation_id", "created_at"),
    )

    id = Column(Integer, primary_key=True)
    owner_id = Column(Integer, ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True)
    conversation_id = Column(
        Integer, ForeignKey("v2_conversations.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    message_event_id = Column(
        Integer, ForeignKey("v2_message_events.id", ondelete="RESTRICT"), nullable=True, index=True
    )
    enrollment_id = Column(
        Integer, ForeignKey("v2_enrollments.id", ondelete="SET NULL"), nullable=True, index=True
    )
    intent = Column(
        enum_type(ReplyIntent, constraint_name="ck_v2_reply_assessment_intent"), nullable=False
    )
    is_positive = Column(Boolean, nullable=False, default=False)
    confidence = Column(Numeric(5, 4), nullable=True)
    status = Column(
        enum_type(ReplyAssessmentStatus, constraint_name="ck_v2_reply_assessment_status"),
        nullable=False,
        default=ReplyAssessmentStatus.PROPOSED,
    )
    latest_reply_body = Column(Text, nullable=False)
    rationale = Column(Text, nullable=True)
    assessed_by = Column(String(50), nullable=False, default="ai")
    confirmed_by_user_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    confirmed_at = Column(DateTime(timezone=True), nullable=True)


class ConsentRestriction(Base, TimestampMixin):
    __tablename__ = "v2_consent_restrictions"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_v2_consent_idempotency"),
        Index("ix_v2_consent_owner_active_scope", "owner_id", "active", "scope"),
        Index("ix_v2_consent_contact_targets", "contact_point_id", "contact_id", "company_id"),
    )

    id = Column(Integer, primary_key=True)
    idempotency_key = Column(String(255), nullable=False)
    owner_id = Column(Integer, ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True)
    scope = Column(
        enum_type(RestrictionScope, constraint_name="ck_v2_consent_restriction_scope"),
        nullable=False,
    )
    channel = Column(
        enum_type(Channel, constraint_name="ck_v2_consent_restriction_channel"), nullable=True
    )
    contact_point_id = Column(
        Integer, ForeignKey("v2_contact_points.id", ondelete="RESTRICT"), nullable=True, index=True
    )
    contact_id = Column(Integer, ForeignKey("v2_contacts.id", ondelete="RESTRICT"), nullable=True, index=True)
    company_id = Column(Integer, ForeignKey("v2_companies.id", ondelete="RESTRICT"), nullable=True, index=True)
    reason = Column(String(500), nullable=False)
    source = Column(String(100), nullable=False, default="manual")
    active = Column(Boolean, nullable=False, default=True)
    created_by_user_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    expires_at = Column(DateTime(timezone=True), nullable=True)
    revoked_at = Column(DateTime(timezone=True), nullable=True)
    metadata_json = Column(JSON, nullable=True)


class Opportunity(Base, TimestampMixin, ArchiveMixin):
    __tablename__ = "v2_opportunities"
    __table_args__ = (
        UniqueConstraint("source_task_id", name="uq_v2_opportunity_source_task"),
        Index("ix_v2_opportunity_owner_stage", "owner_id", "stage"),
        Index("ix_v2_opportunity_company_stage", "company_id", "stage"),
    )

    id = Column(Integer, primary_key=True)
    owner_id = Column(Integer, ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True)
    assignee_user_id = Column(Integer, ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True)
    company_id = Column(Integer, ForeignKey("v2_companies.id", ondelete="RESTRICT"), nullable=False, index=True)
    contact_id = Column(Integer, ForeignKey("v2_contacts.id", ondelete="RESTRICT"), nullable=False, index=True)
    campaign_id = Column(Integer, ForeignKey("v2_campaigns.id", ondelete="SET NULL"), nullable=True, index=True)
    conversation_id = Column(
        Integer, ForeignKey("v2_conversations.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    reply_assessment_id = Column(
        Integer, ForeignKey("v2_reply_assessments.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    source_task_id = Column(Integer, nullable=True, index=True)
    stage = Column(
        enum_type(OpportunityStage, constraint_name="ck_v2_opportunity_stage"),
        nullable=False,
        default=OpportunityStage.QUALIFIED_REPLY,
    )
    fit_confirmed = Column(Boolean, nullable=False, default=False)
    fit_override_id = Column(Integer, nullable=True)
    value_amount = Column(Numeric(18, 2), nullable=True)
    currency = Column(String(3), nullable=True)
    expected_close_date = Column(Date, nullable=True)
    next_action = Column(String(1000), nullable=False)
    next_action_due_at = Column(DateTime(timezone=True), nullable=False)
    qualified_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)
    won_at = Column(DateTime(timezone=True), nullable=True)
    lost_at = Column(DateTime(timezone=True), nullable=True)
    lost_reason = Column(String(1000), nullable=True)


class Task(Base, TimestampMixin, ArchiveMixin):
    __tablename__ = "v2_tasks"
    __table_args__ = (
        Index("ix_v2_task_work_queue", "owner_id", "status", "priority", "due_at"),
        Index("ix_v2_task_subject", "company_id", "contact_id", "campaign_id"),
    )

    id = Column(Integer, primary_key=True)
    owner_id = Column(Integer, ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True)
    task_type = Column(
        enum_type(TaskType, constraint_name="ck_v2_task_type"), nullable=False
    )
    queue_scope = mapped_column(
        enum_type(TaskQueueScope, constraint_name="ck_v2_task_queue_scope"),
        nullable=False,
        default=TaskQueueScope.SALES,
        sort_order=1000,
    )
    status = Column(
        enum_type(TaskStatus, constraint_name="ck_v2_task_status"),
        nullable=False,
        default=TaskStatus.OPEN,
    )
    priority = Column(
        enum_type(TaskPriority, constraint_name="ck_v2_task_priority"),
        nullable=False,
        default=TaskPriority.NORMAL,
    )
    company_id = Column(Integer, ForeignKey("v2_companies.id", ondelete="SET NULL"), nullable=True, index=True)
    contact_id = Column(Integer, ForeignKey("v2_contacts.id", ondelete="SET NULL"), nullable=True, index=True)
    campaign_id = Column(Integer, ForeignKey("v2_campaigns.id", ondelete="SET NULL"), nullable=True, index=True)
    enrollment_id = Column(
        Integer, ForeignKey("v2_enrollments.id", ondelete="SET NULL"), nullable=True, index=True
    )
    conversation_id = Column(
        Integer, ForeignKey("v2_conversations.id", ondelete="SET NULL"), nullable=True, index=True
    )
    opportunity_id = Column(Integer, ForeignKey("v2_opportunities.id", ondelete="SET NULL"), nullable=True)
    attempt_id = Column(Integer, ForeignKey("v2_outreach_attempts.id", ondelete="SET NULL"), nullable=True)
    automation_job_id = Column(Integer, nullable=True, index=True)
    title = Column(String(500), nullable=False)
    description = Column(Text, nullable=True)
    assignee_user_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    due_at = Column(DateTime(timezone=True), nullable=True, index=True)
    completed_at = Column(DateTime(timezone=True), nullable=True)
    metadata_json = Column(JSON, nullable=True)


class ProviderCostEvent(Base, TimestampMixin):
    __tablename__ = "v2_provider_cost_events"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_v2_provider_cost_idempotency"),
        Index("ix_v2_provider_cost_owner_created", "owner_id", "created_at"),
        Index("ix_v2_provider_cost_campaign_provider", "campaign_id", "provider"),
    )

    id = Column(Integer, primary_key=True)
    owner_id = Column(Integer, ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True)
    provider = Column(String(100), nullable=False, index=True)
    operation = Column(String(100), nullable=False)
    status = Column(
        enum_type(ProviderCostStatus, constraint_name="ck_v2_provider_cost_event_status"),
        nullable=False,
        default=ProviderCostStatus.CHARGED,
    )
    units = Column(Numeric(18, 4), nullable=False, default=Decimal("1"))
    native_unit = Column(String(50), nullable=False, default="credits")
    unit_price = Column(Numeric(18, 6), nullable=True)
    normalized_amount = Column(Numeric(18, 6), nullable=True)
    normalized_currency = Column(String(3), nullable=True)
    result_count = Column(Integer, nullable=False, default=0)
    billable = Column(Boolean, nullable=False, default=True)
    price_version = Column(String(100), nullable=False)
    campaign_id = Column(Integer, ForeignKey("v2_campaigns.id", ondelete="RESTRICT"), nullable=True, index=True)
    enrollment_id = Column(
        Integer, ForeignKey("v2_enrollments.id", ondelete="RESTRICT"), nullable=True, index=True
    )
    company_id = Column(Integer, ForeignKey("v2_companies.id", ondelete="RESTRICT"), nullable=True, index=True)
    contact_id = Column(Integer, ForeignKey("v2_contacts.id", ondelete="RESTRICT"), nullable=True, index=True)
    outreach_attempt_id = Column(
        Integer, ForeignKey("v2_outreach_attempts.id", ondelete="RESTRICT"), nullable=True, index=True
    )
    idempotency_key = Column(String(255), nullable=False)
    metadata_json = Column(JSON, nullable=True)


class SafetyLock(Base, TimestampMixin):
    __tablename__ = "v2_safety_locks"
    __table_args__ = (
        Index("ix_v2_safety_lock_owner_active", "owner_id", "active", "scope"),
        Index(
            "ix_v2_safety_lock_account_active",
            "channel_account_id",
            "active",
        ),
        CheckConstraint(
            "(scope = 'account' AND channel_account_id IS NOT NULL) OR "
            "(scope <> 'account' AND channel_account_id IS NULL)",
            name="ck_v2_safety_lock_account_target",
        ),
    )

    id = Column(Integer, primary_key=True)
    owner_id = Column(Integer, ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True)
    scope = Column(
        enum_type(SafetyLockScope, constraint_name="ck_v2_safety_lock_scope"), nullable=False
    )
    campaign_id = Column(Integer, ForeignKey("v2_campaigns.id", ondelete="RESTRICT"), nullable=True, index=True)
    company_id = Column(Integer, ForeignKey("v2_companies.id", ondelete="RESTRICT"), nullable=True, index=True)
    contact_id = Column(Integer, ForeignKey("v2_contacts.id", ondelete="RESTRICT"), nullable=True, index=True)
    channel_account_id = Column(
        Integer, ForeignKey("v2_channel_accounts.id", ondelete="RESTRICT"), nullable=True, index=True
    )
    channel = Column(
        enum_type(Channel, constraint_name="ck_v2_safety_lock_channel"), nullable=True
    )
    code = Column(String(100), nullable=False)
    reason = Column(String(1000), nullable=False)
    active = Column(Boolean, nullable=False, default=True)
    locked_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)
    unlocked_at = Column(DateTime(timezone=True), nullable=True)
    unlocked_by_user_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    metadata_json = Column(JSON, nullable=True)


class ManualOverride(Base):
    __tablename__ = "v2_manual_overrides"
    __table_args__ = (
        Index("ix_v2_override_enrollment_gate", "enrollment_id", "gate", "expires_at"),
    )

    id = Column(Integer, primary_key=True)
    owner_id = Column(Integer, ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True)
    gate = Column(
        enum_type(OverrideGate, constraint_name="ck_v2_manual_override_gate"), nullable=False
    )
    enrollment_id = Column(
        Integer, ForeignKey("v2_enrollments.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    attempt_id = Column(Integer, ForeignKey("v2_outreach_attempts.id", ondelete="RESTRICT"), nullable=True)
    reason = Column(String(1000), nullable=False)
    expires_at = Column(DateTime(timezone=True), nullable=False)
    created_by_user_id = Column(Integer, ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    consumed_at = Column(DateTime(timezone=True), nullable=True)
    revoked_at = Column(DateTime(timezone=True), nullable=True)
    metadata_json = Column(JSON, nullable=True)
    created_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)


class AuditEvent(Base):
    __tablename__ = "v2_audit_events"
    __table_args__ = (
        Index("ix_v2_audit_entity_created", "entity_type", "entity_id", "created_at"),
        Index("ix_v2_audit_owner_created", "owner_id", "created_at"),
    )

    id = Column(Integer, primary_key=True)
    owner_id = Column(Integer, ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True)
    actor_user_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    action = Column(String(100), nullable=False)
    entity_type = Column(String(100), nullable=False)
    entity_id = Column(String(100), nullable=False)
    correlation_id = Column(String(255), nullable=True, index=True)
    before_data = Column(JSON, nullable=True)
    after_data = Column(JSON, nullable=True)
    metadata_json = Column(JSON, nullable=True)
    created_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)


class StageRuntime(Base, TimestampMixin):
    __tablename__ = "v2_stage_runtimes"
    __table_args__ = (
        UniqueConstraint("campaign_id", "stage_name", name="uq_v2_campaign_stage_runtime"),
        Index("ix_v2_stage_runtime_status", "status", "updated_at"),
    )

    id = Column(Integer, primary_key=True)
    owner_id = Column(Integer, ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True)
    campaign_id = Column(Integer, ForeignKey("v2_campaigns.id", ondelete="RESTRICT"), nullable=False, index=True)
    stage_name = Column(String(100), nullable=False)
    status = Column(
        enum_type(StageStatus, constraint_name="ck_v2_stage_runtime_status"),
        nullable=False,
        default=StageStatus.IDLE,
    )
    reason = Column(String(1000), nullable=True)
    last_started_at = Column(DateTime(timezone=True), nullable=True)
    last_succeeded_at = Column(DateTime(timezone=True), nullable=True)
    last_failed_at = Column(DateTime(timezone=True), nullable=True)
    details = Column(JSON, nullable=True)


class WorkerHeartbeat(Base, TimestampMixin):
    __tablename__ = "v2_worker_heartbeats"
    __table_args__ = (
        UniqueConstraint("worker_name", name="uq_v2_worker_heartbeat_name"),
        Index("ix_v2_worker_heartbeat_type_seen", "worker_type", "last_seen_at"),
    )

    id = Column(Integer, primary_key=True)
    worker_name = Column(String(255), nullable=False)
    worker_type = Column(
        enum_type(WorkerType, constraint_name="ck_v2_worker_heartbeat_type"), nullable=False
    )
    status = Column(
        enum_type(StageStatus, constraint_name="ck_v2_worker_heartbeat_status"),
        nullable=False,
        default=StageStatus.IDLE,
    )
    last_seen_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)
    lease_expires_at = Column(DateTime(timezone=True), nullable=True)
    details = Column(JSON, nullable=True)


class AutomationJob(Base, TimestampMixin):
    __tablename__ = "v2_automation_jobs"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_v2_job_idempotency"),
        Index("ix_v2_job_claim", "queue", "status", "scheduled_at", "priority"),
        Index("ix_v2_job_lease", "status", "lease_expires_at"),
    )

    id = Column(Integer, primary_key=True)
    owner_id = Column(Integer, ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True)
    campaign_id = Column(Integer, ForeignKey("v2_campaigns.id", ondelete="SET NULL"), nullable=True, index=True)
    enrollment_id = Column(
        Integer, ForeignKey("v2_enrollments.id", ondelete="SET NULL"), nullable=True, index=True
    )
    attempt_id = Column(Integer, ForeignKey("v2_outreach_attempts.id", ondelete="SET NULL"), nullable=True)
    status = Column(
        enum_type(JobStatus, constraint_name="ck_v2_automation_job_status"),
        nullable=False,
        default=JobStatus.PENDING,
    )
    job_type = Column(String(100), nullable=False)
    queue = Column(String(100), nullable=False, default="default")
    payload = Column(JSON, nullable=False, default=dict)
    idempotency_key = Column(String(255), nullable=False)
    priority = Column(Integer, nullable=False, default=100)
    scheduled_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)
    attempts = Column(Integer, nullable=False, default=0)
    max_attempts = Column(Integer, nullable=False, default=3)
    lease_owner = Column(String(255), nullable=True)
    lease_expires_at = Column(DateTime(timezone=True), nullable=True)
    last_error = Column(Text, nullable=True)
    result = Column(JSON, nullable=True)
    completed_at = Column(DateTime(timezone=True), nullable=True)


class InboxCursor(Base, TimestampMixin):
    __tablename__ = "v2_inbox_cursors"
    __table_args__ = (
        UniqueConstraint("owner_id", "channel_account_id", name="uq_v2_inbox_cursor_owner_account"),
        Index("ix_v2_inbox_cursor_owner_channel", "owner_id", "channel"),
    )

    id = Column(Integer, primary_key=True)
    owner_id = Column(Integer, ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True)
    channel = Column(
        enum_type(Channel, constraint_name="ck_v2_inbox_cursor_channel"),
        nullable=False,
        default=Channel.EMAIL,
    )
    channel_account_id = Column(String(255), nullable=False)
    uid_validity = Column(String(255), nullable=True)
    last_uid = Column(BigInteger, nullable=False, default=0)
    last_success_at = Column(DateTime(timezone=True), nullable=True)
    last_error = Column(Text, nullable=True)


V2_TABLE_NAMES = tuple(
    model.__tablename__
    for model in (
        Company,
        Contact,
        ContactPoint,
        OwnerMigrationState,
        ChannelAccount,
        AudienceList,
        ListMembership,
        EvidenceSnapshot,
        AcquisitionRun,
        AcquisitionCandidate,
        Campaign,
        CampaignRevision,
        SequenceStep,
        Enrollment,
        OutreachAttempt,
        RouteProposal,
        RouteProposalStep,
        ReviewBatch,
        ReviewBatchItem,
        EnrollmentRouteStep,
        WhatsAppConsent,
        Conversation,
        MessageEvent,
        ReplyAssessment,
        ConsentRestriction,
        Opportunity,
        Task,
        ProviderCostEvent,
        SafetyLock,
        ManualOverride,
        AuditEvent,
        StageRuntime,
        WorkerHeartbeat,
        AutomationJob,
        InboxCursor,
    )
)
