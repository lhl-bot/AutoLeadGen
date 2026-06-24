from sqlalchemy import Column, Integer, String, Text, ForeignKey, DateTime, Boolean, JSON, UniqueConstraint, Index
from sqlalchemy.orm import relationship
from datetime import datetime, timezone
from database import Base

class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    username = Column(String(100), unique=True, nullable=False, index=True)
    hashed_password = Column(String(255), nullable=False)
    display_name = Column(String(255), nullable=True)
    is_admin = Column(Boolean, default=False)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    credit_wallet = relationship("CreditWallet", back_populates="user", uselist=False, cascade="all, delete-orphan")


class ClientPool(Base):
    __tablename__ = "client_pools"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    name = Column(String(255), nullable=False)
    description = Column(Text, nullable=True)
    excluded_domains = Column(Text, nullable=True, doc="Comma separated domains to exclude from search")
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    workflows = relationship("Workflow", back_populates="client_pool")
    leads = relationship("Lead", back_populates="client_pool", cascade="all, delete-orphan")

class CustomerPersona(Base):
    __tablename__ = "customer_personas"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    name = Column(String(255), nullable=False)
    target_industry = Column(String(255), nullable=True)
    target_countries = Column(String(255), nullable=True)
    target_keywords = Column(Text, doc="Comma separated keywords for search")
    negative_keywords = Column(Text, doc="Comma separated keywords to exclude")
    target_roles = Column(Text, doc="Comma separated roles, e.g., Purchasing Manager, Buyer")
    ai_prompt_template = Column(Text, doc="Template for the AI to generate the email")
    customer_types = Column(Text, nullable=True, doc="Comma separated buyer types, e.g., distributor, agent, brand")
    product_categories = Column(Text, nullable=True, doc="Comma separated product categories to match")
    company_size = Column(Text, nullable=True, doc="Comma separated target company-size buckets (LeadContact enum: 1_10,11_50,51_200,201_500,501_1000,1001_5000,5001_10000,10001)")
    evidence_sources = Column(Text, nullable=True, doc="Preferred evidence sources: website, customs, social, trade show")
    qualification_rules = Column(Text, nullable=True, doc="Positive fit rules used by lead scoring")
    disqualification_rules = Column(Text, nullable=True, doc="Negative fit rules used by lead scoring")
    cultural_notes = Column(Text, nullable=True, doc="Regional communication preferences and localization notes")
    positive_examples = Column(Text, nullable=True, doc="Examples of good-fit customers")
    negative_examples = Column(Text, nullable=True, doc="Examples of poor-fit customers")
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

class Workflow(Base):
    __tablename__ = "workflows"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    name = Column(String(255), nullable=False)
    status = Column(String(50), default="paused") # active, paused, completed
    search_keywords = Column(Text, nullable=False)
    target_positions = Column(Text, nullable=False)
    ai_prompt = Column(Text, nullable=True)
    email_signature = Column(Text, nullable=True, doc="Custom email signature appended to every outbound email")
    client_pool_id = Column(Integer, ForeignKey("client_pools.id"), nullable=True)
    persona_id = Column(Integer, ForeignKey("customer_personas.id"), nullable=True)
    
    daily_limit = Column(Integer, default=50)
    send_interval_min = Column(Integer, default=60)
    send_interval_max = Column(Integer, default=300)
    auto_followup = Column(Boolean, default=False)
    max_followups = Column(Integer, default=3)
    followup_steps = Column(JSON, nullable=True, doc="Ordered cold follow-up sequence: [{day_offset, instruction?}]. Overrides max_followups + global interval when set.")
    template_id = Column(Integer, ForeignKey("email_templates.id", ondelete="SET NULL"), nullable=True, doc="When set, cold drafts use this template (A/B group) instead of pure AI generation")
    search_offset = Column(Integer, default=0)
    
    # Playbook & warmup settings
    playbook_type = Column(String(50), default="standard", doc="standard, trade_show, reactivation, competitor_mining")
    domain_warmup_enabled = Column(Boolean, default=False)
    pilot_goal = Column(Text, nullable=True, doc="Pilot validation goal and success criteria")
    target_customer_type = Column(String(255), nullable=True, doc="Distributor, agent, brand, competitor buyer, old customer, etc.")
    target_region = Column(String(255), nullable=True, doc="Target market or region for this workflow")
    product_focus = Column(String(255), nullable=True, doc="Product or category to validate")
    manual_handoff_triggers = Column(Text, nullable=True, doc="Signals that should move a lead to human sales")
    search_sources = Column(Text, nullable=True, doc="Comma separated discovery sources: web, customs, competitors, trade_shows, directories, retail, social")
    competitor_names = Column(Text, nullable=True, doc="Competitor brands or companies used for buyer/dealer mining")
    trade_show_names = Column(Text, nullable=True, doc="Trade shows used for exhibitor/buyer discovery")
    
    # Omnichannel settings
    enable_linkedin = Column(Boolean, default=False)
    enable_whatsapp = Column(Boolean, default=False)
    linkedin_invite_message = Column(Text, nullable=True, doc="AI prompt template for LinkedIn invite")
    whatsapp_message_template = Column(Text, nullable=True, doc="AI prompt template for WhatsApp message")
    linkedin_daily_limit = Column(Integer, default=20)

    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    client_pool = relationship("ClientPool", back_populates="workflows")
    workflow_emails = relationship("WorkflowEmail", back_populates="workflow", cascade="all, delete-orphan")
    leads = relationship("Lead", back_populates="workflow", cascade="all, delete-orphan")

class EmailAccount(Base):
    __tablename__ = "email_accounts"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    email = Column(String(255), nullable=False, unique=True)
    display_name = Column(String(255), nullable=True)
    
    smtp_host = Column(String(255), nullable=False)
    smtp_port = Column(Integer, default=465)
    smtp_user = Column(String(255), nullable=False)
    smtp_pass = Column(String(255), nullable=False)
    use_tls = Column(Boolean, default=False)
    use_ssl = Column(Boolean, default=True)
    
    imap_host = Column(String(255), nullable=True)
    imap_port = Column(Integer, default=993)
    
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    workflow_emails = relationship("WorkflowEmail", back_populates="email_account", cascade="all, delete-orphan")

class ChannelAccount(Base):
    __tablename__ = "channel_accounts"
    __table_args__ = (
        Index("ix_channel_accounts_status", "status"),
    )

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    account_type = Column(String(50), nullable=False) # LINKEDIN, WHATSAPP, INSTAGRAM
    unipile_account_id = Column(String(255), nullable=False, unique=True)
    name = Column(String(255), nullable=True) # Display name of the account
    status = Column(String(50), default="OK") # OK, CREDENTIALS, DISCONNECTED
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))

class WorkflowEmail(Base):
    __tablename__ = "workflow_emails"
    
    id = Column(Integer, primary_key=True, index=True)
    workflow_id = Column(Integer, ForeignKey("workflows.id"), nullable=False)
    email_account_id = Column(Integer, ForeignKey("email_accounts.id"), nullable=False)
    workflow = relationship("Workflow", back_populates="workflow_emails")
    email_account = relationship("EmailAccount", back_populates="workflow_emails")

class ProcessedDomain(Base):
    __tablename__ = "processed_domains"
    __table_args__ = (
        Index("ix_processed_domains_created_at", "created_at"),
    )

    id = Column(Integer, primary_key=True, index=True)
    workflow_id = Column(Integer, ForeignKey("workflows.id"), nullable=False)
    domain = Column(String(255), index=True, nullable=False)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    workflow = relationship("Workflow")

class Lead(Base):
    __tablename__ = "leads"
    __table_args__ = (
        Index("ix_leads_created_source_channel", "created_at", "source_channel"),
        Index("ix_leads_updated_at", "updated_at"),
    )

    id = Column(Integer, primary_key=True, index=True)
    workflow_id = Column(Integer, ForeignKey("workflows.id"), nullable=True)
    client_pool_id = Column(Integer, ForeignKey("client_pools.id"), nullable=True)
    domain = Column(String(255), index=True)
    company_name = Column(String(255))
    email = Column(String(255), index=True)
    first_name = Column(String(255))
    last_name = Column(String(255))
    job_title = Column(String(255))
    linkedin_url = Column(String(500))
    status = Column(String(50), default="found") # found, drafted, sent, replied, bounced, rejected, unsubscribed, send_failed, invalid_email, needs_email, low_score
    ai_draft = Column(Text)
    send_fail_count = Column(Integer, default=0)
    
    followup_count = Column(Integer, default=0)
    last_reply_at = Column(DateTime, nullable=True)
    reply_snippet = Column(Text, nullable=True)
    
    # Feedback & verification fields
    user_rating = Column(String(20), nullable=True, doc="User feedback: positive, negative")
    email_verified = Column(Boolean, default=False)
    email_validation_status = Column(String(50), nullable=True, doc="valid, invalid, catch-all, unknown")
    timezone = Column(String(50), nullable=True, doc="IANA timezone e.g. Europe/Berlin")
    fit_score = Column(Integer, nullable=True, doc="0-100 AI lead fit score")
    fit_grade = Column(String(5), nullable=True, doc="A/B/C/D lead quality grade")
    qualification_notes = Column(Text, nullable=True, doc="Signals and risks behind the fit score")
    handoff_recommended = Column(Boolean, default=False, doc="True when sales should manually follow up")
    source_channel = Column(String(50), nullable=True, doc="search, apollo, snovio, customs, trade_show, import")
    data_sources = Column(Text, nullable=True, doc="Comma separated data/evidence sources used for this lead")
    
    whatsapp_number = Column(String(50))
    linkedin_status = Column(String(50), default="unconnected") # unconnected, requested, connected, invalid_profile, provider_limited, failed
    linkedin_sent = Column(Boolean, default=False)
    whatsapp_sent = Column(Boolean, default=False)

    # A/B template attribution for the most recent template-based draft/send.
    template_id = Column(Integer, ForeignKey("email_templates.id", ondelete="SET NULL"), nullable=True, index=True)
    template_variant = Column(String(20), nullable=True, doc="Variant label of the template used, e.g. A/B")

    # Manual CRM-style sales stage, independent of the automation `status`.
    sales_stage = Column(String(30), default="new", index=True, doc="new, contacted, interested, quoting, won, lost")
    
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))

    workflow = relationship("Workflow", back_populates="leads")
    client_pool = relationship("ClientPool", back_populates="leads")
    email_logs = relationship("EmailLog", back_populates="lead", cascade="all, delete-orphan")
    brief = relationship("LeadBrief", back_populates="lead", uselist=False, cascade="all, delete-orphan")

class EmailLog(Base):
    __tablename__ = "email_logs"
    __table_args__ = (
        Index("ix_email_logs_direction_sent_at", "direction", "sent_at"),
    )

    id = Column(Integer, primary_key=True, index=True)
    lead_id = Column(Integer, ForeignKey("leads.id"), nullable=False)
    direction = Column(String(50), default="outbound") # outbound, inbound
    
    from_email = Column(String(255), nullable=False)
    to_email = Column(String(255), nullable=False)
    subject = Column(String(500))
    body = Column(Text)
    
    sent_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    message_id = Column(String(255), nullable=True)
    
    lead = relationship("Lead", back_populates="email_logs")


class EmailSuppression(Base):
    """Recipients or domains that must not be contacted again."""
    __tablename__ = "email_suppressions"
    __table_args__ = (
        UniqueConstraint("user_id", "email", name="uq_email_suppression_user_email"),
        UniqueConstraint("user_id", "domain", name="uq_email_suppression_user_domain"),
    )

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    lead_id = Column(Integer, ForeignKey("leads.id", ondelete="SET NULL"), nullable=True, index=True)
    email = Column(String(255), nullable=True, index=True)
    domain = Column(String(255), nullable=True, index=True)
    reason = Column(String(100), default="manual", nullable=False)
    source = Column(String(100), default="system", nullable=False)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    lead = relationship("Lead")


class CreditWallet(Base):
    """Per-user credit balance for commercial usage limits."""
    __tablename__ = "credit_wallets"
    __table_args__ = (
        UniqueConstraint("user_id", name="uq_credit_wallets_user_id"),
    )

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    balance = Column(Integer, default=0, nullable=False)
    lifetime_granted = Column(Integer, default=0, nullable=False)
    lifetime_used = Column(Integer, default=0, nullable=False)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))

    user = relationship("User", back_populates="credit_wallet")
    transactions = relationship("CreditTransaction", back_populates="wallet", cascade="all, delete-orphan")


class CreditTransaction(Base):
    """Immutable-ish ledger row for every credit grant, debit, refund, or adjustment."""
    __tablename__ = "credit_transactions"
    __table_args__ = (
        Index("ix_credit_transactions_user_created", "user_id", "created_at"),
        Index("ix_credit_transactions_action_created", "action", "created_at"),
    )

    id = Column(Integer, primary_key=True, index=True)
    wallet_id = Column(Integer, ForeignKey("credit_wallets.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    amount = Column(Integer, nullable=False, doc="Positive for grant/refund, negative for usage")
    balance_after = Column(Integer, nullable=False)
    transaction_type = Column(String(50), nullable=False, doc="grant, debit, refund, adjustment")
    action = Column(String(100), nullable=False, doc="email_send, ai_reply_draft, linkedin_invite, whatsapp_message, etc.")
    description = Column(String(500), nullable=True)
    reference_type = Column(String(100), nullable=True)
    reference_id = Column(String(100), nullable=True)
    metadata_json = Column(JSON, nullable=True)
    created_by_user_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    wallet = relationship("CreditWallet", back_populates="transactions")
    user = relationship("User", foreign_keys=[user_id])
    created_by = relationship("User", foreign_keys=[created_by_user_id])


class EmailTemplate(Base):
    """Reusable outreach template with {{variable}} placeholders and A/B variants.

    Variants are grouped by ``ab_group``; when a workflow points at any template in
    a group, the engine picks an active variant by ``weight`` and records which one
    was used on the lead for per-variant reply-rate reporting.
    """
    __tablename__ = "email_templates"
    __table_args__ = (
        Index("ix_email_templates_user_group", "user_id", "ab_group"),
    )

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    name = Column(String(255), nullable=False)
    category = Column(String(50), default="cold", doc="cold, followup")
    ab_group = Column(String(100), nullable=True, index=True, doc="Groups A/B variants; null = standalone")
    variant_label = Column(String(20), default="A", doc="A, B, C ...")
    subject = Column(String(500), nullable=True)
    body = Column(Text, nullable=False)
    weight = Column(Integer, default=1, doc="Relative A/B split weight")
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))


class Notification(Base):
    """In-app alert for the owning user (high-intent reply, send failure, low balance...)."""
    __tablename__ = "notifications"
    __table_args__ = (
        Index("ix_notifications_user_read_created", "user_id", "is_read", "created_at"),
    )

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    type = Column(String(50), nullable=False, doc="high_intent_reply, send_failed, low_balance, email_account_error")
    title = Column(String(255), nullable=False)
    body = Column(Text, nullable=True)
    link = Column(String(255), nullable=True, doc="In-app deep link, e.g. /dashboard/replies")
    reference_type = Column(String(50), nullable=True)
    reference_id = Column(Integer, nullable=True)
    is_read = Column(Boolean, default=False, nullable=False)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


class SnovioUsageEvent(Base):
    """Local audit trail for Snov.io API calls and estimated credit impact."""
    __tablename__ = "snovio_usage_events"
    __table_args__ = (
        Index("ix_snovio_usage_events_created_endpoint", "created_at", "endpoint"),
        Index("ix_snovio_usage_events_domain_created", "domain", "created_at"),
    )

    id = Column(Integer, primary_key=True, index=True)
    endpoint = Column(String(120), nullable=False, index=True)
    domain = Column(String(255), nullable=True, index=True)
    email = Column(String(255), nullable=True, index=True)
    status = Column(String(50), nullable=True)
    result_count = Column(Integer, default=0, nullable=False)
    estimated_credits = Column(Integer, nullable=True)
    metadata_json = Column(JSON, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


class LeadBrief(Base):
    __tablename__ = "lead_briefs"
    __table_args__ = (
        Index("ix_lead_briefs_created_at", "created_at"),
    )

    id = Column(Integer, primary_key=True, index=True)
    lead_id = Column(Integer, ForeignKey("leads.id"), nullable=False, unique=True)
    company_overview = Column(Text, nullable=True)
    recent_news = Column(Text, nullable=True)
    pain_points = Column(Text, nullable=True)
    value_proposition_alignment = Column(Text, nullable=True)
    specific_products = Column(Text, nullable=True, doc="Specific product names extracted from company website")
    recent_activity = Column(Text, nullable=True, doc="Recent launches, events, or partnerships")
    personalization_hook = Column(Text, nullable=True, doc="Concrete detail for email personalization")
    
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))

    lead = relationship("Lead", back_populates="brief")

class ChatSession(Base):
    __tablename__ = "chat_sessions"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    title = Column(String(255), default="New Chat")
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))

    messages = relationship("ChatMessage", back_populates="session", cascade="all, delete-orphan")

class ChatMessage(Base):
    __tablename__ = "chat_messages"
    __table_args__ = (
        Index("ix_chat_messages_role_created_at", "role", "created_at"),
    )

    id = Column(Integer, primary_key=True, index=True)
    session_id = Column(Integer, ForeignKey("chat_sessions.id"), nullable=False)
    role = Column(String(50), nullable=False) # 'user' or 'assistant'
    content = Column(Text, nullable=False)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    session = relationship("ChatSession", back_populates="messages")


class MessageLog(Base):
    """Unified log for all omnichannel messages (LinkedIn, WhatsApp, etc.)"""
    __tablename__ = "message_logs"
    __table_args__ = (
        Index("ix_message_logs_direction_sent_channel", "direction", "sent_at", "channel"),
    )

    id = Column(Integer, primary_key=True, index=True)
    lead_id = Column(Integer, ForeignKey("leads.id"), nullable=False)
    channel = Column(String(50), nullable=False)  # "email", "linkedin", "whatsapp"
    direction = Column(String(50), default="outbound")  # "outbound", "inbound"
    content = Column(Text)
    status = Column(String(50), default="sent")  # "sent", "delivered", "failed"
    sent_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    lead = relationship("Lead")


class LeadFeedback(Base):
    """Stores user feedback (positive/negative) on lead quality for RLHF optimization."""
    __tablename__ = "lead_feedbacks"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    lead_id = Column(Integer, ForeignKey("leads.id"), nullable=False)
    workflow_id = Column(Integer, nullable=True)
    rating = Column(String(20), nullable=False, doc="positive or negative")
    reason = Column(Text, nullable=True)
    lead_snapshot = Column(JSON, nullable=True, doc="Snapshot of lead data at time of rating")
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    lead = relationship("Lead")
