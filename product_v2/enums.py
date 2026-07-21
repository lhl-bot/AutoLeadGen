"""Product V2 domain enums.

The enum values are the public API contract.  SQLAlchemy models persist the
lower-case values (rather than Python enum member names), which keeps the
schema portable between SQLite tests and MySQL 8.
"""

from enum import Enum


class ValueEnum(str, Enum):
    """String enum with a useful ``str()`` representation for logs and APIs."""

    def __str__(self) -> str:
        return self.value


class OwnerWritePath(ValueEnum):
    """The one authoritative application write path for an owner."""

    LEGACY = "legacy"
    V2 = "v2"


class Channel(ValueEnum):
    EMAIL = "email"
    LINKEDIN = "linkedin"
    WHATSAPP = "whatsapp"
    OFFLINE = "offline"


class ContactPointVerificationStatus(ValueEnum):
    UNVERIFIED = "unverified"
    VALID = "valid"
    INVALID = "invalid"
    CATCH_ALL = "catch_all"
    UNKNOWN = "unknown"


class ContactPointAvailabilityStatus(ValueEnum):
    AVAILABLE = "available"
    RESTRICTED = "restricted"
    UNAVAILABLE = "unavailable"


class ChannelAccountHealth(ValueEnum):
    """Last observed ability of a sender account to accept provider work."""

    UNKNOWN = "unknown"
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNHEALTHY = "unhealthy"


class CampaignLifecycle(ValueEnum):
    DRAFT = "draft"
    READY = "ready"
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"
    ARCHIVED = "archived"


class CampaignRunMode(ValueEnum):
    SHADOW = "shadow"
    REVIEW = "review"
    AUTO = "auto"


class CampaignRevisionStatus(ValueEnum):
    DRAFT = "draft"
    PUBLISHED = "published"
    SUPERSEDED = "superseded"


class StageStatus(ValueEnum):
    IDLE = "idle"
    RUNNING = "running"
    BACKOFF = "backoff"
    BLOCKED = "blocked"
    FAILED = "failed"
    DISABLED = "disabled"


class EnrollmentStatus(ValueEnum):
    SCHEDULED = "scheduled"
    ACTIVE = "active"
    PAUSED = "paused"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    BLOCKED = "blocked"


class AttemptKind(ValueEnum):
    COLD = "cold"
    FOLLOW_UP = "follow_up"
    REPLY = "reply"


class AttemptStatus(ValueEnum):
    QUEUED = "queued"
    CLAIMED = "claimed"
    SENDING = "sending"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    UNKNOWN = "unknown"
    BLOCKED = "blocked"
    CANCELLED = "cancelled"


class MessageDirection(ValueEnum):
    INBOUND = "inbound"
    OUTBOUND = "outbound"


class MessageEventType(ValueEnum):
    QUEUED = "queued"
    SENT = "sent"
    DELIVERED = "delivered"
    OPENED = "opened"
    REPLIED = "replied"
    BOUNCED = "bounced"
    COMPLAINED = "complained"
    FAILED = "failed"
    UNSUBSCRIBED = "unsubscribed"
    UNKNOWN = "unknown"


class ConversationStatus(ValueEnum):
    OPEN = "open"
    WAITING_ON_US = "waiting_on_us"
    WAITING_ON_CONTACT = "waiting_on_contact"
    CLOSED = "closed"


class ReplyIntent(ValueEnum):
    INTERESTED = "interested"
    MORE_INFO = "more_info"
    REFERRAL = "referral"
    MEETING = "meeting"
    NOT_INTERESTED = "not_interested"
    UNSUBSCRIBE = "unsubscribe"
    OUT_OF_OFFICE = "out_of_office"
    BOUNCE = "bounce"
    OTHER = "other"


POSITIVE_REPLY_INTENTS = frozenset(
    {
        ReplyIntent.INTERESTED,
        ReplyIntent.MORE_INFO,
        ReplyIntent.REFERRAL,
        ReplyIntent.MEETING,
    }
)


class ReplyAssessmentStatus(ValueEnum):
    PROPOSED = "proposed"
    CONFIRMED = "confirmed"
    REJECTED = "rejected"


class RestrictionScope(ValueEnum):
    CONTACT_POINT = "contact_point"
    CONTACT = "contact"
    COMPANY = "company"
    GLOBAL = "global"


class OpportunityStage(ValueEnum):
    QUALIFIED_REPLY = "qualified_reply"
    DISCOVERY = "discovery"
    SAMPLE_OR_QUOTE = "sample_or_quote"
    NEGOTIATION = "negotiation"
    WON = "won"
    LOST = "lost"


class TaskType(ValueEnum):
    CAMPAIGN_READINESS = "campaign_readiness"
    RESEARCH_REQUIRED = "research_required"
    CONTACT_ENRICHMENT_REQUIRED = "contact_enrichment_required"
    DRAFT_REVIEW = "draft_review"
    REPLY_TRIAGE = "reply_triage"
    SEND_FAILURE = "send_failure"
    DELIVERABILITY_ALERT = "deliverability_alert"
    PROVIDER_BUDGET_ALERT = "provider_budget_alert"
    SALES_HANDOFF = "sales_handoff"
    RECONCILIATION = "reconciliation"
    DATA_GOVERNANCE = "data_governance"


class TaskQueueScope(ValueEnum):
    """Controls which product surface may expose a task."""

    SALES = "sales"
    ADMIN = "admin"
    DATA_GOVERNANCE = "data_governance"


class TaskStatus(ValueEnum):
    OPEN = "open"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    DISMISSED = "dismissed"


class TaskPriority(ValueEnum):
    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"
    URGENT = "urgent"


class OverrideGate(ValueEnum):
    FIT = "fit"
    RESEARCH_EVIDENCE = "research_evidence"
    TIMEZONE = "timezone"


class SafetyLockScope(ValueEnum):
    GLOBAL = "global"
    CAMPAIGN = "campaign"
    COMPANY = "company"
    CONTACT = "contact"
    CHANNEL = "channel"
    ACCOUNT = "account"


class ProviderCostStatus(ValueEnum):
    RESERVED = "reserved"
    CHARGED = "charged"
    REFUNDED = "refunded"
    FAILED = "failed"
    UNKNOWN = "unknown"


class RouteProposalStatus(ValueEnum):
    DRAFT = "draft"
    HUMAN_REVIEW_REQUIRED = "human_review_required"
    PREVIEWED = "previewed"
    APPROVED = "approved"
    REJECTED = "rejected"
    SUPERSEDED = "superseded"


class ReviewBatchStatus(ValueEnum):
    DRAFT = "draft"
    PREVIEWED = "previewed"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXPIRED = "expired"


class JobStatus(ValueEnum):
    PENDING = "pending"
    CLAIMED = "claimed"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    RETRY = "retry"
    CANCELLED = "cancelled"
    UNKNOWN = "unknown"


class WorkerType(ValueEnum):
    PROSPECTING = "prospecting"
    RESEARCH = "research"
    OUTBOUND = "outbound"
    INBOX = "inbox"
    OMNICHANNEL = "omnichannel"
