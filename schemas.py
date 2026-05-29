from pydantic import BaseModel
from typing import Optional, List
from datetime import datetime

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

    class Config:
        from_attributes = True

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

    class Config:
        from_attributes = True

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

    class Config:
        from_attributes = True

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
    search_offset: int = 0
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

    class Config:
        from_attributes = True

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

class LeadCreate(LeadBase):
    pass

class Lead(LeadBase):
    id: int
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True

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

    class Config:
        from_attributes = True

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

    class Config:
        from_attributes = True

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

    class Config:
        from_attributes = True

