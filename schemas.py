from pydantic import BaseModel, ConfigDict, Field, field_validator
from typing import Optional, List
from datetime import datetime


class FollowupStep(BaseModel):
    """One step in a workflow's cold follow-up sequence."""
    day_offset: int = Field(ge=1, le=90, description="Days to wait after the previous email before sending this follow-up")
    instruction: Optional[str] = Field(default=None, max_length=500, description="Optional extra guidance for the AI for this step")

    @field_validator("instruction")
    @classmethod
    def _strip_instruction(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        v = v.strip()
        return v or None

# --- Customer Persona ---
class CustomerPersonaBase(BaseModel):
    name: str
    target_industry: Optional[str] = None
    target_countries: Optional[str] = None
    target_keywords: Optional[str] = None
    negative_keywords: Optional[str] = None
    target_roles: Optional[str] = None
    ai_prompt_template: Optional[str] = None
    customer_types: Optional[str] = None
    product_categories: Optional[str] = None
    company_size: Optional[str] = None
    evidence_sources: Optional[str] = None
    qualification_rules: Optional[str] = None
    disqualification_rules: Optional[str] = None
    cultural_notes: Optional[str] = None
    positive_examples: Optional[str] = None
    negative_examples: Optional[str] = None

class CustomerPersonaCreate(CustomerPersonaBase):
    pass

class CustomerPersona(CustomerPersonaBase):
    id: int
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)

# --- Client Pool ---
class ClientPoolBase(BaseModel):
    name: str
    description: Optional[str] = None
    excluded_domains: Optional[str] = None

class ClientPoolCreate(ClientPoolBase):
    pass

class ClientPool(ClientPoolBase):
    id: int
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)

class ClientPoolWithStats(ClientPool):
    total_leads: int = 0
    contacted_leads: int = 0
    replied_leads: int = 0
    workflow_count: int = 0

# --- Email Account ---
class EmailAccountBase(BaseModel):
    email: str
    display_name: Optional[str] = None
    smtp_host: str
    smtp_port: int = 465
    smtp_user: str
    use_tls: bool = False
    use_ssl: bool = True
    imap_host: Optional[str] = None
    imap_port: int = 993

class EmailAccountCreate(EmailAccountBase):
    smtp_pass: str

class EmailAccount(EmailAccountBase):
    id: int
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)

# --- Workflow ---
class WorkflowBase(BaseModel):
    name: str
    status: str = "paused"
    search_keywords: str
    target_positions: str
    ai_prompt: Optional[str] = None
    client_pool_id: Optional[int] = None
    persona_id: Optional[int] = None
    daily_limit: int = 50
    send_interval_min: int = 60
    send_interval_max: int = 300
    auto_followup: bool = False
    max_followups: int = 3
    followup_steps: Optional[List[FollowupStep]] = Field(default=None, max_length=6)
    template_id: Optional[int] = None
    search_offset: int = 0

    @field_validator("followup_steps")
    @classmethod
    def _empty_steps_to_none(cls, v):
        # Treat an empty sequence as "not configured" so the engine falls back to defaults.
        return v or None
    email_signature: Optional[str] = None
    # Playbook
    playbook_type: str = "standard"
    domain_warmup_enabled: bool = False
    pilot_goal: Optional[str] = None
    target_customer_type: Optional[str] = None
    target_region: Optional[str] = None
    product_focus: Optional[str] = None
    manual_handoff_triggers: Optional[str] = None
    search_sources: Optional[str] = None
    competitor_names: Optional[str] = None
    trade_show_names: Optional[str] = None
    # Omnichannel
    enable_linkedin: bool = False
    enable_whatsapp: bool = False
    linkedin_invite_message: Optional[str] = None
    whatsapp_message_template: Optional[str] = None
    linkedin_daily_limit: int = 20

class WorkflowCreate(WorkflowBase):
    email_account_ids: List[int] = []

class Workflow(WorkflowBase):
    id: int
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)

class WorkflowWithDetails(Workflow):
    emails: List[EmailAccount] = []
    leads_count: int = 0
    contactable_count: int = 0
    needs_email_count: int = 0
    replied_count: int = 0
    bounced_count: int = 0
    low_score_count: int = 0
    outbound_count: int = 0
    bounce_rate: float = 0
    email_paused: bool = False
    client_pool_name: Optional[str] = None
    enable_linkedin: bool = False
    enable_whatsapp: bool = False
    linkedin_invite_message: Optional[str] = None
    whatsapp_message_template: Optional[str] = None
    linkedin_daily_limit: int = 20
    avg_fit_score: Optional[float] = None
    handoff_count: int = 0
    persona_name: Optional[str] = None

# --- Lead ---
class LeadBase(BaseModel):
    workflow_id: Optional[int] = None
    client_pool_id: Optional[int] = None
    domain: str
    company_name: Optional[str] = None
    email: Optional[str] = None
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    job_title: Optional[str] = None
    linkedin_url: Optional[str] = None
    status: str = "found"
    ai_draft: Optional[str] = None
    followup_count: int = 0
    last_reply_at: Optional[datetime] = None
    reply_snippet: Optional[str] = None
    # Feedback & verification
    user_rating: Optional[str] = None
    email_verified: bool = False
    email_validation_status: Optional[str] = None
    timezone: Optional[str] = None
    fit_score: Optional[int] = None
    fit_grade: Optional[str] = None
    qualification_notes: Optional[str] = None
    handoff_recommended: bool = False
    source_channel: Optional[str] = None
    data_sources: Optional[str] = None
    sales_stage: Optional[str] = None

class LeadCreate(LeadBase):
    pass

class Lead(LeadBase):
    id: int
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)

# --- Email Log ---
class EmailLogBase(BaseModel):
    lead_id: int
    direction: str = "outbound"
    from_email: str
    to_email: str
    subject: Optional[str] = None
    body: Optional[str] = None
    message_id: Optional[str] = None

class EmailLogCreate(EmailLogBase):
    pass

class EmailLog(EmailLogBase):
    id: int
    sent_at: datetime

    model_config = ConfigDict(from_attributes=True)

# --- Lead Rating / Feedback ---
class LeadRateRequest(BaseModel):
    rating: str  # "positive" or "negative"
    reason: Optional[str] = None

class LeadFeedbackResponse(BaseModel):
    id: int
    lead_id: int
    rating: str
    reason: Optional[str] = None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)

class FeedbackSummary(BaseModel):
    total_positive: int = 0
    total_negative: int = 0
    recent_positive_domains: List[str] = []
    recent_negative_domains: List[str] = []


class LeadScoreResponse(BaseModel):
    lead_id: int
    fit_score: int
    fit_grade: str
    handoff_recommended: bool
    qualification_notes: str


class WorkflowPilotReport(BaseModel):
    workflow_id: int
    leads_total: int = 0
    matched_leads: int = 0
    match_rate: float = 0
    email_valid_rate: float = 0
    reply_rate: float = 0
    handoff_count: int = 0
    high_intent_count: int = 0
    avg_fit_score: float = 0
    top_channels: List[str] = []


class LeadBriefResponse(BaseModel):
    id: int
    lead_id: int
    company_overview: Optional[str] = None
    recent_news: Optional[str] = None
    pain_points: Optional[str] = None
    value_proposition_alignment: Optional[str] = None
    specific_products: Optional[str] = None
    recent_activity: Optional[str] = None
    personalization_hook: Optional[str] = None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


# --- Credits ---
class CreditSummary(BaseModel):
    user_id: int
    balance: int
    lifetime_granted: int
    lifetime_used: int
    pricing: dict[str, int]
    credits_enabled: bool
    updated_at: Optional[datetime] = None


class CreditTransaction(BaseModel):
    id: int
    user_id: int
    amount: int
    balance_after: int
    transaction_type: str
    action: str
    description: Optional[str] = None
    reference_type: Optional[str] = None
    reference_id: Optional[str] = None
    metadata_json: Optional[dict] = None
    created_by_user_id: Optional[int] = None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class CreditGrantRequest(BaseModel):
    amount: int
    description: Optional[str] = None


class CreditLedgerResponse(BaseModel):
    summary: CreditSummary
    transactions: List[CreditTransaction]


# --- Email Templates (A/B testing) ---
class EmailTemplateBase(BaseModel):
    name: str
    category: str = "cold"
    ab_group: Optional[str] = None
    variant_label: str = "A"
    subject: Optional[str] = None
    body: str
    weight: int = Field(default=1, ge=0, le=100)
    is_active: bool = True

    @field_validator("name", "body")
    @classmethod
    def _not_blank(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("must not be blank")
        return v.strip()

    @field_validator("ab_group", "variant_label", "subject")
    @classmethod
    def _trim_optional(cls, v):
        if v is None:
            return None
        v = v.strip()
        return v or None


class EmailTemplateCreate(EmailTemplateBase):
    pass


class EmailTemplate(EmailTemplateBase):
    id: int
    user_id: int
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class EmailTemplateStats(EmailTemplate):
    # Reply-rate attribution derived from leads that used this template.
    sent_count: int = 0
    replied_count: int = 0
    reply_rate: float = 0.0


class TemplatePreviewRequest(BaseModel):
    subject: Optional[str] = None
    body: str


# --- Notifications ---
class Notification(BaseModel):
    id: int
    type: str
    title: str
    body: Optional[str] = None
    link: Optional[str] = None
    reference_type: Optional[str] = None
    reference_id: Optional[int] = None
    is_read: bool
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class NotificationList(BaseModel):
    unread_count: int = 0
    items: List[Notification] = []


# --- CRM Webhooks ---
class CrmWebhookBase(BaseModel):
    name: str
    url: str
    events: str = "lead.won"
    is_active: bool = True

    @field_validator("name", "url")
    @classmethod
    def _required(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("must not be blank")
        return v.strip()

    @field_validator("url")
    @classmethod
    def _http_url(cls, v: str) -> str:
        if not v.startswith(("http://", "https://")):
            raise ValueError("URL must start with http:// or https://")
        return v


class CrmWebhookCreate(CrmWebhookBase):
    secret: Optional[str] = None


class CrmWebhook(CrmWebhookBase):
    id: int
    has_secret: bool = False
    last_status: Optional[int] = None
    last_error: Optional[str] = None
    last_delivered_at: Optional[datetime] = None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)
