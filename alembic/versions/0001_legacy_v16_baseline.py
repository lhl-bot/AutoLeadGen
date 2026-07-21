"""Create the frozen legacy v16 baseline on an empty database.

Existing verified v16 databases are stamped at this revision and never run
this upgrade. Empty isolated databases receive this exact, reviewable schema.
Existing tables are preserved so interrupted local/MySQL creation can resume.
"""
from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import inspect


revision: str = "0001_legacy_v16_baseline"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# This DDL is a checked-in historical snapshot. Do not regenerate it from
# application metadata; schema changes require a new Alembic revision.
FROZEN_TABLES: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    (
        'snovio_usage_events',
        """
CREATE TABLE snovio_usage_events (
	id INTEGER NOT NULL,
	endpoint VARCHAR(120) NOT NULL,
	domain VARCHAR(255),
	email VARCHAR(255),
	status VARCHAR(50),
	result_count INTEGER NOT NULL,
	estimated_credits INTEGER,
	metadata_json JSON,
	created_at DATETIME,
	PRIMARY KEY (id)
)
        """.strip(),
        (
            'CREATE INDEX ix_snovio_usage_events_created_endpoint ON snovio_usage_events (created_at, endpoint)',
            'CREATE INDEX ix_snovio_usage_events_domain ON snovio_usage_events (domain)',
            'CREATE INDEX ix_snovio_usage_events_domain_created ON snovio_usage_events (domain, created_at)',
            'CREATE INDEX ix_snovio_usage_events_email ON snovio_usage_events (email)',
            'CREATE INDEX ix_snovio_usage_events_endpoint ON snovio_usage_events (endpoint)',
            'CREATE INDEX ix_snovio_usage_events_id ON snovio_usage_events (id)',
        ),
    ),
    (
        'users',
        """
CREATE TABLE users (
	id INTEGER NOT NULL,
	username VARCHAR(100) NOT NULL,
	hashed_password VARCHAR(255) NOT NULL,
	display_name VARCHAR(255),
	is_admin BOOLEAN,
	is_active BOOLEAN,
	created_at DATETIME,
	PRIMARY KEY (id)
)
        """.strip(),
        (
            'CREATE INDEX ix_users_id ON users (id)',
            'CREATE UNIQUE INDEX ix_users_username ON users (username)',
        ),
    ),
    (
        'channel_accounts',
        """
CREATE TABLE channel_accounts (
	id INTEGER NOT NULL,
	user_id INTEGER NOT NULL,
	account_type VARCHAR(50) NOT NULL,
	unipile_account_id VARCHAR(255) NOT NULL,
	name VARCHAR(255),
	status VARCHAR(50),
	created_at DATETIME,
	updated_at DATETIME,
	PRIMARY KEY (id),
	FOREIGN KEY(user_id) REFERENCES users (id),
	UNIQUE (unipile_account_id)
)
        """.strip(),
        (
            'CREATE INDEX ix_channel_accounts_id ON channel_accounts (id)',
            'CREATE INDEX ix_channel_accounts_status ON channel_accounts (status)',
            'CREATE INDEX ix_channel_accounts_user_id ON channel_accounts (user_id)',
        ),
    ),
    (
        'chat_sessions',
        """
CREATE TABLE chat_sessions (
	id INTEGER NOT NULL,
	user_id INTEGER NOT NULL,
	title VARCHAR(255),
	created_at DATETIME,
	updated_at DATETIME,
	PRIMARY KEY (id),
	FOREIGN KEY(user_id) REFERENCES users (id)
)
        """.strip(),
        (
            'CREATE INDEX ix_chat_sessions_id ON chat_sessions (id)',
            'CREATE INDEX ix_chat_sessions_user_id ON chat_sessions (user_id)',
        ),
    ),
    (
        'client_pools',
        """
CREATE TABLE client_pools (
	id INTEGER NOT NULL,
	user_id INTEGER NOT NULL,
	name VARCHAR(255) NOT NULL,
	description TEXT,
	excluded_domains TEXT,
	created_at DATETIME,
	PRIMARY KEY (id),
	FOREIGN KEY(user_id) REFERENCES users (id)
)
        """.strip(),
        (
            'CREATE INDEX ix_client_pools_id ON client_pools (id)',
            'CREATE INDEX ix_client_pools_user_id ON client_pools (user_id)',
        ),
    ),
    (
        'credit_wallets',
        """
CREATE TABLE credit_wallets (
	id INTEGER NOT NULL,
	user_id INTEGER NOT NULL,
	balance INTEGER NOT NULL,
	lifetime_granted INTEGER NOT NULL,
	lifetime_used INTEGER NOT NULL,
	created_at DATETIME,
	updated_at DATETIME,
	PRIMARY KEY (id),
	CONSTRAINT uq_credit_wallets_user_id UNIQUE (user_id),
	FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE
)
        """.strip(),
        (
            'CREATE INDEX ix_credit_wallets_id ON credit_wallets (id)',
            'CREATE INDEX ix_credit_wallets_user_id ON credit_wallets (user_id)',
        ),
    ),
    (
        'crm_webhooks',
        """
CREATE TABLE crm_webhooks (
	id INTEGER NOT NULL,
	user_id INTEGER NOT NULL,
	name VARCHAR(255) NOT NULL,
	url VARCHAR(1000) NOT NULL,
	secret VARCHAR(255),
	events VARCHAR(500),
	is_active BOOLEAN,
	last_status INTEGER,
	last_error TEXT,
	last_delivered_at DATETIME,
	created_at DATETIME,
	PRIMARY KEY (id),
	FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE
)
        """.strip(),
        (
            'CREATE INDEX ix_crm_webhooks_id ON crm_webhooks (id)',
            'CREATE INDEX ix_crm_webhooks_user_active ON crm_webhooks (user_id, is_active)',
            'CREATE INDEX ix_crm_webhooks_user_id ON crm_webhooks (user_id)',
        ),
    ),
    (
        'customer_personas',
        """
CREATE TABLE customer_personas (
	id INTEGER NOT NULL,
	user_id INTEGER NOT NULL,
	name VARCHAR(255) NOT NULL,
	target_industry VARCHAR(255),
	target_countries VARCHAR(255),
	target_keywords TEXT,
	negative_keywords TEXT,
	target_roles TEXT,
	ai_prompt_template TEXT,
	customer_types TEXT,
	product_categories TEXT,
	company_size TEXT,
	evidence_sources TEXT,
	qualification_rules TEXT,
	disqualification_rules TEXT,
	cultural_notes TEXT,
	positive_examples TEXT,
	negative_examples TEXT,
	created_at DATETIME,
	PRIMARY KEY (id),
	FOREIGN KEY(user_id) REFERENCES users (id)
)
        """.strip(),
        (
            'CREATE INDEX ix_customer_personas_id ON customer_personas (id)',
            'CREATE INDEX ix_customer_personas_user_id ON customer_personas (user_id)',
        ),
    ),
    (
        'email_accounts',
        """
CREATE TABLE email_accounts (
	id INTEGER NOT NULL,
	user_id INTEGER NOT NULL,
	email VARCHAR(255) NOT NULL,
	display_name VARCHAR(255),
	smtp_host VARCHAR(255) NOT NULL,
	smtp_port INTEGER,
	smtp_user VARCHAR(255) NOT NULL,
	smtp_pass VARCHAR(255) NOT NULL,
	use_tls BOOLEAN,
	use_ssl BOOLEAN,
	imap_host VARCHAR(255),
	imap_port INTEGER,
	created_at DATETIME,
	PRIMARY KEY (id),
	FOREIGN KEY(user_id) REFERENCES users (id),
	UNIQUE (email)
)
        """.strip(),
        (
            'CREATE INDEX ix_email_accounts_id ON email_accounts (id)',
            'CREATE INDEX ix_email_accounts_user_id ON email_accounts (user_id)',
        ),
    ),
    (
        'email_templates',
        """
CREATE TABLE email_templates (
	id INTEGER NOT NULL,
	user_id INTEGER NOT NULL,
	name VARCHAR(255) NOT NULL,
	category VARCHAR(50),
	ab_group VARCHAR(100),
	variant_label VARCHAR(20),
	subject VARCHAR(500),
	body TEXT NOT NULL,
	weight INTEGER,
	is_active BOOLEAN,
	created_at DATETIME,
	updated_at DATETIME,
	PRIMARY KEY (id),
	FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE
)
        """.strip(),
        (
            'CREATE INDEX ix_email_templates_ab_group ON email_templates (ab_group)',
            'CREATE INDEX ix_email_templates_id ON email_templates (id)',
            'CREATE INDEX ix_email_templates_user_group ON email_templates (user_id, ab_group)',
            'CREATE INDEX ix_email_templates_user_id ON email_templates (user_id)',
        ),
    ),
    (
        'notifications',
        """
CREATE TABLE notifications (
	id INTEGER NOT NULL,
	user_id INTEGER NOT NULL,
	type VARCHAR(50) NOT NULL,
	title VARCHAR(255) NOT NULL,
	body TEXT,
	link VARCHAR(255),
	reference_type VARCHAR(50),
	reference_id INTEGER,
	is_read BOOLEAN NOT NULL,
	created_at DATETIME,
	PRIMARY KEY (id),
	FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE
)
        """.strip(),
        (
            'CREATE INDEX ix_notifications_id ON notifications (id)',
            'CREATE INDEX ix_notifications_user_id ON notifications (user_id)',
            'CREATE INDEX ix_notifications_user_read_created ON notifications (user_id, is_read, created_at)',
        ),
    ),
    (
        'chat_messages',
        """
CREATE TABLE chat_messages (
	id INTEGER NOT NULL,
	session_id INTEGER NOT NULL,
	role VARCHAR(50) NOT NULL,
	content TEXT NOT NULL,
	created_at DATETIME,
	PRIMARY KEY (id),
	FOREIGN KEY(session_id) REFERENCES chat_sessions (id)
)
        """.strip(),
        (
            'CREATE INDEX ix_chat_messages_id ON chat_messages (id)',
            'CREATE INDEX ix_chat_messages_role_created_at ON chat_messages (role, created_at)',
        ),
    ),
    (
        'credit_transactions',
        """
CREATE TABLE credit_transactions (
	id INTEGER NOT NULL,
	wallet_id INTEGER NOT NULL,
	user_id INTEGER NOT NULL,
	amount INTEGER NOT NULL,
	balance_after INTEGER NOT NULL,
	transaction_type VARCHAR(50) NOT NULL,
	action VARCHAR(100) NOT NULL,
	description VARCHAR(500),
	reference_type VARCHAR(100),
	reference_id VARCHAR(100),
	metadata_json JSON,
	created_by_user_id INTEGER,
	created_at DATETIME,
	PRIMARY KEY (id),
	FOREIGN KEY(wallet_id) REFERENCES credit_wallets (id) ON DELETE CASCADE,
	FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE,
	FOREIGN KEY(created_by_user_id) REFERENCES users (id) ON DELETE SET NULL
)
        """.strip(),
        (
            'CREATE INDEX ix_credit_transactions_action_created ON credit_transactions (action, created_at)',
            'CREATE INDEX ix_credit_transactions_id ON credit_transactions (id)',
            'CREATE INDEX ix_credit_transactions_user_created ON credit_transactions (user_id, created_at)',
            'CREATE INDEX ix_credit_transactions_user_id ON credit_transactions (user_id)',
            'CREATE INDEX ix_credit_transactions_wallet_id ON credit_transactions (wallet_id)',
        ),
    ),
    (
        'workflows',
        """
CREATE TABLE workflows (
	id INTEGER NOT NULL,
	user_id INTEGER NOT NULL,
	name VARCHAR(255) NOT NULL,
	status VARCHAR(50),
	search_keywords TEXT NOT NULL,
	target_positions TEXT NOT NULL,
	ai_prompt TEXT,
	email_signature TEXT,
	client_pool_id INTEGER,
	persona_id INTEGER,
	daily_limit INTEGER,
	send_interval_min INTEGER,
	send_interval_max INTEGER,
	auto_followup BOOLEAN,
	max_followups INTEGER,
	followup_steps JSON,
	email_sending_paused BOOLEAN NOT NULL,
	email_pause_reason VARCHAR(255),
	template_id INTEGER,
	search_offset INTEGER,
	playbook_type VARCHAR(50),
	domain_warmup_enabled BOOLEAN,
	pilot_goal TEXT,
	target_customer_type VARCHAR(255),
	target_region VARCHAR(255),
	product_focus VARCHAR(255),
	manual_handoff_triggers TEXT,
	search_sources TEXT,
	competitor_names TEXT,
	trade_show_names TEXT,
	enable_linkedin BOOLEAN,
	enable_whatsapp BOOLEAN,
	linkedin_invite_message TEXT,
	whatsapp_message_template TEXT,
	linkedin_daily_limit INTEGER,
	created_at DATETIME,
	PRIMARY KEY (id),
	FOREIGN KEY(user_id) REFERENCES users (id),
	FOREIGN KEY(client_pool_id) REFERENCES client_pools (id),
	FOREIGN KEY(persona_id) REFERENCES customer_personas (id),
	FOREIGN KEY(template_id) REFERENCES email_templates (id) ON DELETE SET NULL
)
        """.strip(),
        (
            'CREATE INDEX ix_workflows_id ON workflows (id)',
            'CREATE INDEX ix_workflows_user_id ON workflows (user_id)',
        ),
    ),
    (
        'leads',
        """
CREATE TABLE leads (
	id INTEGER NOT NULL,
	workflow_id INTEGER,
	client_pool_id INTEGER,
	domain VARCHAR(255),
	company_name VARCHAR(255),
	email VARCHAR(255),
	first_name VARCHAR(255),
	last_name VARCHAR(255),
	job_title VARCHAR(255),
	linkedin_url VARCHAR(500),
	status VARCHAR(50),
	ai_draft TEXT,
	send_fail_count INTEGER,
	followup_count INTEGER,
	last_reply_at DATETIME,
	reply_snippet TEXT,
	automation_block_reason VARCHAR(255),
	automation_blocked_at DATETIME,
	has_replied BOOLEAN NOT NULL,
	reply_intent VARCHAR(50),
	user_rating VARCHAR(20),
	email_verified BOOLEAN,
	email_validation_status VARCHAR(50),
	timezone VARCHAR(50),
	fit_score INTEGER,
	fit_grade VARCHAR(5),
	qualification_notes TEXT,
	handoff_recommended BOOLEAN,
	source_channel VARCHAR(50),
	data_sources TEXT,
	whatsapp_number VARCHAR(50),
	linkedin_status VARCHAR(50),
	linkedin_sent BOOLEAN,
	whatsapp_sent BOOLEAN,
	template_id INTEGER,
	template_variant VARCHAR(20),
	sales_stage VARCHAR(30),
	created_at DATETIME,
	updated_at DATETIME,
	PRIMARY KEY (id),
	FOREIGN KEY(workflow_id) REFERENCES workflows (id),
	FOREIGN KEY(client_pool_id) REFERENCES client_pools (id),
	FOREIGN KEY(template_id) REFERENCES email_templates (id) ON DELETE SET NULL
)
        """.strip(),
        (
            'CREATE INDEX ix_leads_automation_block_reason ON leads (automation_block_reason)',
            'CREATE INDEX ix_leads_created_source_channel ON leads (created_at, source_channel)',
            'CREATE INDEX ix_leads_domain ON leads (domain)',
            'CREATE INDEX ix_leads_email ON leads (email)',
            'CREATE INDEX ix_leads_has_replied ON leads (has_replied)',
            'CREATE INDEX ix_leads_id ON leads (id)',
            'CREATE INDEX ix_leads_sales_stage ON leads (sales_stage)',
            'CREATE INDEX ix_leads_template_id ON leads (template_id)',
            'CREATE INDEX ix_leads_updated_at ON leads (updated_at)',
        ),
    ),
    (
        'processed_domains',
        """
CREATE TABLE processed_domains (
	id INTEGER NOT NULL,
	workflow_id INTEGER NOT NULL,
	domain VARCHAR(255) NOT NULL,
	created_at DATETIME,
	PRIMARY KEY (id),
	FOREIGN KEY(workflow_id) REFERENCES workflows (id)
)
        """.strip(),
        (
            'CREATE INDEX ix_processed_domains_created_at ON processed_domains (created_at)',
            'CREATE INDEX ix_processed_domains_domain ON processed_domains (domain)',
            'CREATE INDEX ix_processed_domains_id ON processed_domains (id)',
        ),
    ),
    (
        'workflow_emails',
        """
CREATE TABLE workflow_emails (
	id INTEGER NOT NULL,
	workflow_id INTEGER NOT NULL,
	email_account_id INTEGER NOT NULL,
	PRIMARY KEY (id),
	FOREIGN KEY(workflow_id) REFERENCES workflows (id),
	FOREIGN KEY(email_account_id) REFERENCES email_accounts (id)
)
        """.strip(),
        (
            'CREATE INDEX ix_workflow_emails_id ON workflow_emails (id)',
        ),
    ),
    (
        'email_logs',
        """
CREATE TABLE email_logs (
	id INTEGER NOT NULL,
	lead_id INTEGER NOT NULL,
	direction VARCHAR(50),
	from_email VARCHAR(255) NOT NULL,
	to_email VARCHAR(255) NOT NULL,
	subject VARCHAR(500),
	body TEXT,
	sent_at DATETIME,
	message_id VARCHAR(255),
	PRIMARY KEY (id),
	FOREIGN KEY(lead_id) REFERENCES leads (id)
)
        """.strip(),
        (
            'CREATE INDEX ix_email_logs_direction_sent_at ON email_logs (direction, sent_at)',
            'CREATE INDEX ix_email_logs_id ON email_logs (id)',
        ),
    ),
    (
        'email_suppressions',
        """
CREATE TABLE email_suppressions (
	id INTEGER NOT NULL,
	user_id INTEGER,
	lead_id INTEGER,
	email VARCHAR(255),
	domain VARCHAR(255),
	reason VARCHAR(100) NOT NULL,
	source VARCHAR(100) NOT NULL,
	created_at DATETIME,
	PRIMARY KEY (id),
	CONSTRAINT uq_email_suppression_user_email UNIQUE (user_id, email),
	CONSTRAINT uq_email_suppression_user_domain UNIQUE (user_id, domain),
	FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE SET NULL,
	FOREIGN KEY(lead_id) REFERENCES leads (id) ON DELETE SET NULL
)
        """.strip(),
        (
            'CREATE INDEX ix_email_suppressions_domain ON email_suppressions (domain)',
            'CREATE INDEX ix_email_suppressions_email ON email_suppressions (email)',
            'CREATE INDEX ix_email_suppressions_id ON email_suppressions (id)',
            'CREATE INDEX ix_email_suppressions_lead_id ON email_suppressions (lead_id)',
            'CREATE INDEX ix_email_suppressions_user_id ON email_suppressions (user_id)',
        ),
    ),
    (
        'lead_briefs',
        """
CREATE TABLE lead_briefs (
	id INTEGER NOT NULL,
	lead_id INTEGER NOT NULL,
	company_overview TEXT,
	recent_news TEXT,
	pain_points TEXT,
	value_proposition_alignment TEXT,
	specific_products TEXT,
	recent_activity TEXT,
	personalization_hook TEXT,
	research_status VARCHAR(30) NOT NULL,
	quality_flags JSON,
	evidence_sources JSON,
	researched_at DATETIME,
	created_at DATETIME,
	updated_at DATETIME,
	PRIMARY KEY (id),
	UNIQUE (lead_id),
	FOREIGN KEY(lead_id) REFERENCES leads (id)
)
        """.strip(),
        (
            'CREATE INDEX ix_lead_briefs_created_at ON lead_briefs (created_at)',
            'CREATE INDEX ix_lead_briefs_id ON lead_briefs (id)',
            'CREATE INDEX ix_lead_briefs_research_status ON lead_briefs (research_status)',
        ),
    ),
    (
        'lead_feedbacks',
        """
CREATE TABLE lead_feedbacks (
	id INTEGER NOT NULL,
	user_id INTEGER NOT NULL,
	lead_id INTEGER NOT NULL,
	workflow_id INTEGER,
	rating VARCHAR(20) NOT NULL,
	reason TEXT,
	lead_snapshot JSON,
	created_at DATETIME,
	PRIMARY KEY (id),
	FOREIGN KEY(user_id) REFERENCES users (id),
	FOREIGN KEY(lead_id) REFERENCES leads (id)
)
        """.strip(),
        (
            'CREATE INDEX ix_lead_feedbacks_id ON lead_feedbacks (id)',
            'CREATE INDEX ix_lead_feedbacks_user_id ON lead_feedbacks (user_id)',
        ),
    ),
    (
        'message_logs',
        """
CREATE TABLE message_logs (
	id INTEGER NOT NULL,
	lead_id INTEGER NOT NULL,
	channel VARCHAR(50) NOT NULL,
	direction VARCHAR(50),
	content TEXT,
	status VARCHAR(50),
	sent_at DATETIME,
	PRIMARY KEY (id),
	FOREIGN KEY(lead_id) REFERENCES leads (id)
)
        """.strip(),
        (
            'CREATE INDEX ix_message_logs_direction_sent_channel ON message_logs (direction, sent_at, channel)',
            'CREATE INDEX ix_message_logs_id ON message_logs (id)',
        ),
    ),
    (
        'provider_usage_events',
        """
CREATE TABLE provider_usage_events (
	id INTEGER NOT NULL,
	provider VARCHAR(50) NOT NULL,
	operation VARCHAR(100) NOT NULL,
	workflow_id INTEGER,
	lead_id INTEGER,
	status VARCHAR(50) NOT NULL,
	units INTEGER NOT NULL,
	estimated_credits INTEGER,
	result_count INTEGER NOT NULL,
	metadata_json JSON,
	created_at DATETIME,
	PRIMARY KEY (id),
	FOREIGN KEY(workflow_id) REFERENCES workflows (id) ON DELETE SET NULL,
	FOREIGN KEY(lead_id) REFERENCES leads (id) ON DELETE SET NULL
)
        """.strip(),
        (
            'CREATE INDEX ix_provider_usage_events_id ON provider_usage_events (id)',
            'CREATE INDEX ix_provider_usage_events_lead_id ON provider_usage_events (lead_id)',
            'CREATE INDEX ix_provider_usage_events_operation ON provider_usage_events (operation)',
            'CREATE INDEX ix_provider_usage_events_provider ON provider_usage_events (provider)',
            'CREATE INDEX ix_provider_usage_events_workflow_id ON provider_usage_events (workflow_id)',
            'CREATE INDEX ix_provider_usage_operation_status ON provider_usage_events (operation, status)',
            'CREATE INDEX ix_provider_usage_provider_created ON provider_usage_events (provider, created_at)',
            'CREATE INDEX ix_provider_usage_workflow_created ON provider_usage_events (workflow_id, created_at)',
        ),
    ),
)


def _dialect_sql(statement: str, dialect_name: str) -> str:
    """Adapt the frozen portable SQL only where SQLite and MySQL differ."""

    if dialect_name == "sqlite":
        return statement
    if dialect_name != "mysql":
        raise RuntimeError(
            f"{revision} supports only SQLite and MySQL, not {dialect_name!r}"
        )
    return (
        statement.replace(
            "\tid INTEGER NOT NULL,", "\tid INTEGER NOT NULL AUTO_INCREMENT,"
        )
        .replace("BOOLEAN", "BOOL")
        .replace("\trole VARCHAR(50)", "\t`role` VARCHAR(50)")
        .replace("(role, created_at)", "(`role`, created_at)")
    )


def upgrade() -> None:
    from alembic import op

    bind = op.get_bind()
    dialect_name = bind.dialect.name
    inspector = inspect(bind)
    existing_tables = set(inspector.get_table_names())

    for table_name, create_table_sql, index_statements in FROZEN_TABLES:
        if table_name not in existing_tables:
            bind.exec_driver_sql(_dialect_sql(create_table_sql, dialect_name))
            existing_tables.add(table_name)

        # A table may survive a non-transactional MySQL interruption while one
        # or more standalone indexes do not. Re-inspect and resume safely.
        inspector = inspect(bind)
        existing_indexes = {
            item["name"]
            for item in inspector.get_indexes(table_name)
            if item.get("name")
        }
        for index_sql in index_statements:
            index_name = index_sql.split(" ON ", 1)[0].rsplit(" ", 1)[-1]
            if index_name not in existing_indexes:
                bind.exec_driver_sql(_dialect_sql(index_sql, dialect_name))
                existing_indexes.add(index_name)


def downgrade() -> None:
    raise RuntimeError('The legacy baseline is non-destructive and cannot be downgraded')
