"""Add the frozen Product V2 expand schema without changing legacy records.

The table, constraint, and index definitions below are immutable migration
artifacts. They deliberately do not import runtime application metadata.
"""
from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import inspect


revision: str = "0002_product_v2_expand"
down_revision: str | None = '0001_legacy_v16_baseline'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# This DDL is a checked-in historical snapshot. Do not regenerate it from
# application metadata; schema changes require a new Alembic revision.
FROZEN_TABLES: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    (
        'v2_worker_heartbeats',
        """
CREATE TABLE v2_worker_heartbeats (
	id INTEGER NOT NULL,
	worker_name VARCHAR(255) NOT NULL,
	worker_type VARCHAR(11) NOT NULL,
	status VARCHAR(8) NOT NULL,
	last_seen_at DATETIME NOT NULL,
	lease_expires_at DATETIME,
	details JSON,
	created_at DATETIME NOT NULL,
	updated_at DATETIME NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT uq_v2_worker_heartbeat_name UNIQUE (worker_name),
	CONSTRAINT ck_v2_worker_heartbeat_type CHECK (worker_type IN ('prospecting', 'research', 'outbound', 'inbox', 'omnichannel')),
	CONSTRAINT ck_v2_worker_heartbeat_status CHECK (status IN ('idle', 'running', 'backoff', 'blocked', 'failed', 'disabled'))
)
        """.strip(),
        (
            'CREATE INDEX ix_v2_worker_heartbeat_type_seen ON v2_worker_heartbeats (worker_type, last_seen_at)',
        ),
    ),
    (
        'v2_audience_lists',
        """
CREATE TABLE v2_audience_lists (
	id INTEGER NOT NULL,
	owner_id INTEGER NOT NULL,
	name VARCHAR(255) NOT NULL,
	description TEXT,
	created_at DATETIME NOT NULL,
	updated_at DATETIME NOT NULL,
	archived_at DATETIME,
	legacy_source_table VARCHAR(100),
	legacy_id VARCHAR(100),
	PRIMARY KEY (id),
	CONSTRAINT uq_v2_audience_list_owner_name UNIQUE (owner_id, name),
	CONSTRAINT uq_v2_audience_list_legacy_source UNIQUE (owner_id, legacy_source_table, legacy_id),
	FOREIGN KEY(owner_id) REFERENCES users (id) ON DELETE RESTRICT
)
        """.strip(),
        (
            'CREATE INDEX ix_v2_audience_lists_archived_at ON v2_audience_lists (archived_at)',
            'CREATE INDEX ix_v2_audience_lists_owner_id ON v2_audience_lists (owner_id)',
        ),
    ),
    (
        'v2_audit_events',
        """
CREATE TABLE v2_audit_events (
	id INTEGER NOT NULL,
	owner_id INTEGER NOT NULL,
	actor_user_id INTEGER,
	action VARCHAR(100) NOT NULL,
	entity_type VARCHAR(100) NOT NULL,
	entity_id VARCHAR(100) NOT NULL,
	correlation_id VARCHAR(255),
	before_data JSON,
	after_data JSON,
	metadata_json JSON,
	created_at DATETIME NOT NULL,
	PRIMARY KEY (id),
	FOREIGN KEY(owner_id) REFERENCES users (id) ON DELETE RESTRICT,
	FOREIGN KEY(actor_user_id) REFERENCES users (id) ON DELETE SET NULL
)
        """.strip(),
        (
            'CREATE INDEX ix_v2_audit_entity_created ON v2_audit_events (entity_type, entity_id, created_at)',
            'CREATE INDEX ix_v2_audit_events_correlation_id ON v2_audit_events (correlation_id)',
            'CREATE INDEX ix_v2_audit_events_owner_id ON v2_audit_events (owner_id)',
            'CREATE INDEX ix_v2_audit_owner_created ON v2_audit_events (owner_id, created_at)',
        ),
    ),
    (
        'v2_campaigns',
        """
CREATE TABLE v2_campaigns (
	id INTEGER NOT NULL,
	owner_id INTEGER NOT NULL,
	name VARCHAR(255) NOT NULL,
	description TEXT,
	lifecycle VARCHAR(9) NOT NULL,
	run_mode VARCHAR(6) NOT NULL,
	priority INTEGER NOT NULL,
	published_revision_number INTEGER,
	started_at DATETIME,
	paused_at DATETIME,
	completed_at DATETIME,
	created_at DATETIME NOT NULL,
	updated_at DATETIME NOT NULL,
	archived_at DATETIME,
	legacy_source_table VARCHAR(100),
	legacy_id VARCHAR(100),
	PRIMARY KEY (id),
	CONSTRAINT uq_v2_campaign_owner_name UNIQUE (owner_id, name),
	CONSTRAINT uq_v2_campaign_legacy_source UNIQUE (owner_id, legacy_source_table, legacy_id),
	FOREIGN KEY(owner_id) REFERENCES users (id) ON DELETE RESTRICT,
	CONSTRAINT ck_v2_campaign_lifecycle CHECK (lifecycle IN ('draft', 'ready', 'running', 'paused', 'completed', 'archived')),
	CONSTRAINT ck_v2_campaign_run_mode CHECK (run_mode IN ('shadow', 'review', 'auto'))
)
        """.strip(),
        (
            'CREATE INDEX ix_v2_campaign_owner_lifecycle ON v2_campaigns (owner_id, lifecycle)',
            'CREATE INDEX ix_v2_campaigns_archived_at ON v2_campaigns (archived_at)',
            'CREATE INDEX ix_v2_campaigns_lifecycle ON v2_campaigns (lifecycle)',
            'CREATE INDEX ix_v2_campaigns_owner_id ON v2_campaigns (owner_id)',
        ),
    ),
    (
        'v2_companies',
        """
CREATE TABLE v2_companies (
	id INTEGER NOT NULL,
	owner_id INTEGER NOT NULL,
	name VARCHAR(255) NOT NULL,
	normalized_domain VARCHAR(255),
	website VARCHAR(1000),
	country VARCHAR(100),
	region VARCHAR(100),
	industry VARCHAR(255),
	timezone VARCHAR(100),
	last_cold_outreach_at DATETIME,
	created_at DATETIME NOT NULL,
	updated_at DATETIME NOT NULL,
	archived_at DATETIME,
	legacy_source_table VARCHAR(100),
	legacy_id VARCHAR(100),
	PRIMARY KEY (id),
	CONSTRAINT uq_v2_company_owner_domain UNIQUE (owner_id, normalized_domain),
	CONSTRAINT uq_v2_company_legacy_source UNIQUE (owner_id, legacy_source_table, legacy_id),
	FOREIGN KEY(owner_id) REFERENCES users (id) ON DELETE RESTRICT
)
        """.strip(),
        (
            'CREATE INDEX ix_v2_companies_archived_at ON v2_companies (archived_at)',
            'CREATE INDEX ix_v2_companies_last_cold_outreach_at ON v2_companies (last_cold_outreach_at)',
            'CREATE INDEX ix_v2_companies_normalized_domain ON v2_companies (normalized_domain)',
            'CREATE INDEX ix_v2_companies_owner_id ON v2_companies (owner_id)',
            'CREATE INDEX ix_v2_company_owner_archived ON v2_companies (owner_id, archived_at)',
        ),
    ),
    (
        'v2_inbox_cursors',
        """
CREATE TABLE v2_inbox_cursors (
	id INTEGER NOT NULL,
	owner_id INTEGER NOT NULL,
	channel VARCHAR(8) NOT NULL,
	channel_account_id VARCHAR(255) NOT NULL,
	uid_validity VARCHAR(255),
	last_uid BIGINT NOT NULL,
	last_success_at DATETIME,
	last_error TEXT,
	created_at DATETIME NOT NULL,
	updated_at DATETIME NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT uq_v2_inbox_cursor_owner_account UNIQUE (owner_id, channel_account_id),
	FOREIGN KEY(owner_id) REFERENCES users (id) ON DELETE RESTRICT,
	CONSTRAINT ck_v2_inbox_cursor_channel CHECK (channel IN ('email', 'linkedin', 'whatsapp', 'offline'))
)
        """.strip(),
        (
            'CREATE INDEX ix_v2_inbox_cursor_owner_channel ON v2_inbox_cursors (owner_id, channel)',
            'CREATE INDEX ix_v2_inbox_cursors_owner_id ON v2_inbox_cursors (owner_id)',
        ),
    ),
    (
        'v2_campaign_revisions',
        """
CREATE TABLE v2_campaign_revisions (
	id INTEGER NOT NULL,
	owner_id INTEGER NOT NULL,
	campaign_id INTEGER NOT NULL,
	revision_number INTEGER NOT NULL,
	status VARCHAR(10) NOT NULL,
	icp_definition JSON NOT NULL,
	audience_definition JSON NOT NULL,
	quality_gates JSON NOT NULL,
	budget_definition JSON NOT NULL,
	stop_conditions JSON NOT NULL,
	published_at DATETIME,
	published_by_user_id INTEGER,
	created_at DATETIME NOT NULL,
	updated_at DATETIME NOT NULL,
	archived_at DATETIME,
	legacy_source_table VARCHAR(100),
	legacy_id VARCHAR(100),
	PRIMARY KEY (id),
	CONSTRAINT uq_v2_campaign_revision_number UNIQUE (campaign_id, revision_number),
	CONSTRAINT uq_v2_revision_legacy_source UNIQUE (owner_id, legacy_source_table, legacy_id),
	FOREIGN KEY(owner_id) REFERENCES users (id) ON DELETE RESTRICT,
	FOREIGN KEY(campaign_id) REFERENCES v2_campaigns (id) ON DELETE RESTRICT,
	CONSTRAINT ck_v2_campaign_revision_status CHECK (status IN ('draft', 'published', 'superseded')),
	FOREIGN KEY(published_by_user_id) REFERENCES users (id) ON DELETE SET NULL
)
        """.strip(),
        (
            'CREATE INDEX ix_v2_campaign_revision_status ON v2_campaign_revisions (campaign_id, status)',
            'CREATE INDEX ix_v2_campaign_revisions_archived_at ON v2_campaign_revisions (archived_at)',
            'CREATE INDEX ix_v2_campaign_revisions_campaign_id ON v2_campaign_revisions (campaign_id)',
            'CREATE INDEX ix_v2_campaign_revisions_owner_id ON v2_campaign_revisions (owner_id)',
        ),
    ),
    (
        'v2_contacts',
        """
CREATE TABLE v2_contacts (
	id INTEGER NOT NULL,
	owner_id INTEGER NOT NULL,
	company_id INTEGER NOT NULL,
	first_name VARCHAR(120),
	last_name VARCHAR(120),
	full_name VARCHAR(255) NOT NULL,
	job_title VARCHAR(255),
	department VARCHAR(255),
	timezone VARCHAR(100),
	locale VARCHAR(50),
	created_at DATETIME NOT NULL,
	updated_at DATETIME NOT NULL,
	archived_at DATETIME,
	legacy_source_table VARCHAR(100),
	legacy_id VARCHAR(100),
	PRIMARY KEY (id),
	CONSTRAINT uq_v2_contact_legacy_source UNIQUE (owner_id, legacy_source_table, legacy_id),
	FOREIGN KEY(owner_id) REFERENCES users (id) ON DELETE RESTRICT,
	FOREIGN KEY(company_id) REFERENCES v2_companies (id) ON DELETE RESTRICT
)
        """.strip(),
        (
            'CREATE INDEX ix_v2_contact_owner_company ON v2_contacts (owner_id, company_id)',
            'CREATE INDEX ix_v2_contacts_archived_at ON v2_contacts (archived_at)',
            'CREATE INDEX ix_v2_contacts_company_id ON v2_contacts (company_id)',
            'CREATE INDEX ix_v2_contacts_owner_id ON v2_contacts (owner_id)',
        ),
    ),
    (
        'v2_stage_runtimes',
        """
CREATE TABLE v2_stage_runtimes (
	id INTEGER NOT NULL,
	owner_id INTEGER NOT NULL,
	campaign_id INTEGER NOT NULL,
	stage_name VARCHAR(100) NOT NULL,
	status VARCHAR(8) NOT NULL,
	reason VARCHAR(1000),
	last_started_at DATETIME,
	last_succeeded_at DATETIME,
	last_failed_at DATETIME,
	details JSON,
	created_at DATETIME NOT NULL,
	updated_at DATETIME NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT uq_v2_campaign_stage_runtime UNIQUE (campaign_id, stage_name),
	FOREIGN KEY(owner_id) REFERENCES users (id) ON DELETE RESTRICT,
	FOREIGN KEY(campaign_id) REFERENCES v2_campaigns (id) ON DELETE RESTRICT,
	CONSTRAINT ck_v2_stage_runtime_status CHECK (status IN ('idle', 'running', 'backoff', 'blocked', 'failed', 'disabled'))
)
        """.strip(),
        (
            'CREATE INDEX ix_v2_stage_runtime_status ON v2_stage_runtimes (status, updated_at)',
            'CREATE INDEX ix_v2_stage_runtimes_campaign_id ON v2_stage_runtimes (campaign_id)',
            'CREATE INDEX ix_v2_stage_runtimes_owner_id ON v2_stage_runtimes (owner_id)',
        ),
    ),
    (
        'v2_contact_points',
        """
CREATE TABLE v2_contact_points (
	id INTEGER NOT NULL,
	owner_id INTEGER NOT NULL,
	company_id INTEGER NOT NULL,
	contact_id INTEGER NOT NULL,
	channel VARCHAR(8) NOT NULL,
	value VARCHAR(1000) NOT NULL,
	normalized_value VARCHAR(1000) NOT NULL,
	normalized_value_hash VARCHAR(64) NOT NULL,
	verification_status VARCHAR(10) NOT NULL,
	availability_status VARCHAR(11) NOT NULL,
	is_primary BOOLEAN NOT NULL,
	verified_at DATETIME,
	last_cold_outreach_at DATETIME,
	created_at DATETIME NOT NULL,
	updated_at DATETIME NOT NULL,
	archived_at DATETIME,
	legacy_source_table VARCHAR(100),
	legacy_id VARCHAR(100),
	PRIMARY KEY (id),
	CONSTRAINT uq_v2_contact_point_owner_identity_hash UNIQUE (owner_id, channel, normalized_value_hash),
	CONSTRAINT uq_v2_contact_point_legacy_source UNIQUE (owner_id, legacy_source_table, legacy_id),
	FOREIGN KEY(owner_id) REFERENCES users (id) ON DELETE RESTRICT,
	FOREIGN KEY(company_id) REFERENCES v2_companies (id) ON DELETE RESTRICT,
	FOREIGN KEY(contact_id) REFERENCES v2_contacts (id) ON DELETE RESTRICT,
	CONSTRAINT ck_v2_contact_point_channel CHECK (channel IN ('email', 'linkedin', 'whatsapp', 'offline')),
	CONSTRAINT ck_v2_contact_point_verification CHECK (verification_status IN ('unverified', 'valid', 'invalid', 'catch_all', 'unknown')),
	CONSTRAINT ck_v2_contact_point_availability CHECK (availability_status IN ('available', 'restricted', 'unavailable'))
)
        """.strip(),
        (
            'CREATE INDEX ix_v2_contact_point_contact_channel ON v2_contact_points (contact_id, channel)',
            'CREATE INDEX ix_v2_contact_points_archived_at ON v2_contact_points (archived_at)',
            'CREATE INDEX ix_v2_contact_points_company_id ON v2_contact_points (company_id)',
            'CREATE INDEX ix_v2_contact_points_contact_id ON v2_contact_points (contact_id)',
            'CREATE INDEX ix_v2_contact_points_last_cold_outreach_at ON v2_contact_points (last_cold_outreach_at)',
            'CREATE INDEX ix_v2_contact_points_owner_id ON v2_contact_points (owner_id)',
        ),
    ),
    (
        'v2_enrollments',
        """
CREATE TABLE v2_enrollments (
	id INTEGER NOT NULL,
	owner_id INTEGER NOT NULL,
	campaign_id INTEGER NOT NULL,
	campaign_revision_id INTEGER NOT NULL,
	company_id INTEGER NOT NULL,
	contact_id INTEGER NOT NULL,
	status VARCHAR(9) NOT NULL,
	scheduled_at DATETIME NOT NULL,
	priority_snapshot INTEGER NOT NULL,
	paused_reason VARCHAR(500),
	paused_at DATETIME,
	positive_signal_at DATETIME,
	completed_at DATETIME,
	created_at DATETIME NOT NULL,
	updated_at DATETIME NOT NULL,
	archived_at DATETIME,
	legacy_source_table VARCHAR(100),
	legacy_id VARCHAR(100),
	PRIMARY KEY (id),
	CONSTRAINT uq_v2_campaign_contact_enrollment UNIQUE (campaign_id, contact_id),
	CONSTRAINT uq_v2_enrollment_legacy_source UNIQUE (owner_id, legacy_source_table, legacy_id),
	FOREIGN KEY(owner_id) REFERENCES users (id) ON DELETE RESTRICT,
	FOREIGN KEY(campaign_id) REFERENCES v2_campaigns (id) ON DELETE RESTRICT,
	FOREIGN KEY(campaign_revision_id) REFERENCES v2_campaign_revisions (id) ON DELETE RESTRICT,
	FOREIGN KEY(company_id) REFERENCES v2_companies (id) ON DELETE RESTRICT,
	FOREIGN KEY(contact_id) REFERENCES v2_contacts (id) ON DELETE RESTRICT,
	CONSTRAINT ck_v2_enrollment_status CHECK (status IN ('scheduled', 'active', 'paused', 'completed', 'cancelled', 'blocked'))
)
        """.strip(),
        (
            'CREATE INDEX ix_v2_enrollment_company_campaign ON v2_enrollments (company_id, campaign_id, status)',
            'CREATE INDEX ix_v2_enrollment_schedule ON v2_enrollments (status, scheduled_at)',
            'CREATE INDEX ix_v2_enrollments_archived_at ON v2_enrollments (archived_at)',
            'CREATE INDEX ix_v2_enrollments_campaign_id ON v2_enrollments (campaign_id)',
            'CREATE INDEX ix_v2_enrollments_campaign_revision_id ON v2_enrollments (campaign_revision_id)',
            'CREATE INDEX ix_v2_enrollments_company_id ON v2_enrollments (company_id)',
            'CREATE INDEX ix_v2_enrollments_contact_id ON v2_enrollments (contact_id)',
            'CREATE INDEX ix_v2_enrollments_owner_id ON v2_enrollments (owner_id)',
        ),
    ),
    (
        'v2_evidence_snapshots',
        """
CREATE TABLE v2_evidence_snapshots (
	id INTEGER NOT NULL,
	owner_id INTEGER NOT NULL,
	company_id INTEGER,
	contact_id INTEGER,
	source VARCHAR(255) NOT NULL,
	source_url VARCHAR(2000),
	evidence JSON NOT NULL,
	confidence NUMERIC(5, 4) NOT NULL,
	version INTEGER NOT NULL,
	captured_at DATETIME NOT NULL,
	created_at DATETIME NOT NULL,
	updated_at DATETIME NOT NULL,
	archived_at DATETIME,
	legacy_source_table VARCHAR(100),
	legacy_id VARCHAR(100),
	PRIMARY KEY (id),
	CONSTRAINT ck_v2_evidence_has_subject CHECK (company_id IS NOT NULL OR contact_id IS NOT NULL),
	CONSTRAINT uq_v2_evidence_legacy_source UNIQUE (owner_id, legacy_source_table, legacy_id),
	FOREIGN KEY(owner_id) REFERENCES users (id) ON DELETE RESTRICT,
	FOREIGN KEY(company_id) REFERENCES v2_companies (id) ON DELETE RESTRICT,
	FOREIGN KEY(contact_id) REFERENCES v2_contacts (id) ON DELETE RESTRICT
)
        """.strip(),
        (
            'CREATE INDEX ix_v2_evidence_snapshots_archived_at ON v2_evidence_snapshots (archived_at)',
            'CREATE INDEX ix_v2_evidence_snapshots_company_id ON v2_evidence_snapshots (company_id)',
            'CREATE INDEX ix_v2_evidence_snapshots_contact_id ON v2_evidence_snapshots (contact_id)',
            'CREATE INDEX ix_v2_evidence_snapshots_owner_id ON v2_evidence_snapshots (owner_id)',
            'CREATE INDEX ix_v2_evidence_subject_version ON v2_evidence_snapshots (company_id, contact_id, version)',
        ),
    ),
    (
        'v2_list_memberships',
        """
CREATE TABLE v2_list_memberships (
	id INTEGER NOT NULL,
	owner_id INTEGER NOT NULL,
	audience_list_id INTEGER NOT NULL,
	contact_id INTEGER,
	company_id INTEGER,
	added_by_user_id INTEGER,
	created_at DATETIME NOT NULL,
	updated_at DATETIME NOT NULL,
	archived_at DATETIME,
	legacy_source_table VARCHAR(100),
	legacy_id VARCHAR(100),
	PRIMARY KEY (id),
	CONSTRAINT ck_v2_list_membership_one_subject CHECK ((contact_id IS NOT NULL AND company_id IS NULL) OR (contact_id IS NULL AND company_id IS NOT NULL)),
	CONSTRAINT uq_v2_list_contact UNIQUE (audience_list_id, contact_id),
	CONSTRAINT uq_v2_list_company UNIQUE (audience_list_id, company_id),
	CONSTRAINT uq_v2_membership_legacy_source UNIQUE (owner_id, legacy_source_table, legacy_id),
	FOREIGN KEY(owner_id) REFERENCES users (id) ON DELETE RESTRICT,
	FOREIGN KEY(audience_list_id) REFERENCES v2_audience_lists (id) ON DELETE RESTRICT,
	FOREIGN KEY(contact_id) REFERENCES v2_contacts (id) ON DELETE RESTRICT,
	FOREIGN KEY(company_id) REFERENCES v2_companies (id) ON DELETE RESTRICT,
	FOREIGN KEY(added_by_user_id) REFERENCES users (id) ON DELETE SET NULL
)
        """.strip(),
        (
            'CREATE INDEX ix_v2_list_memberships_archived_at ON v2_list_memberships (archived_at)',
            'CREATE INDEX ix_v2_list_memberships_audience_list_id ON v2_list_memberships (audience_list_id)',
            'CREATE INDEX ix_v2_list_memberships_company_id ON v2_list_memberships (company_id)',
            'CREATE INDEX ix_v2_list_memberships_contact_id ON v2_list_memberships (contact_id)',
            'CREATE INDEX ix_v2_list_memberships_owner_id ON v2_list_memberships (owner_id)',
        ),
    ),
    (
        'v2_safety_locks',
        """
CREATE TABLE v2_safety_locks (
	id INTEGER NOT NULL,
	owner_id INTEGER NOT NULL,
	scope VARCHAR(8) NOT NULL,
	campaign_id INTEGER,
	company_id INTEGER,
	contact_id INTEGER,
	channel VARCHAR(8),
	code VARCHAR(100) NOT NULL,
	reason VARCHAR(1000) NOT NULL,
	active BOOLEAN NOT NULL,
	locked_at DATETIME NOT NULL,
	unlocked_at DATETIME,
	unlocked_by_user_id INTEGER,
	metadata_json JSON,
	created_at DATETIME NOT NULL,
	updated_at DATETIME NOT NULL,
	PRIMARY KEY (id),
	FOREIGN KEY(owner_id) REFERENCES users (id) ON DELETE RESTRICT,
	CONSTRAINT ck_v2_safety_lock_scope CHECK (scope IN ('global', 'campaign', 'company', 'contact', 'channel', 'account')),
	FOREIGN KEY(campaign_id) REFERENCES v2_campaigns (id) ON DELETE RESTRICT,
	FOREIGN KEY(company_id) REFERENCES v2_companies (id) ON DELETE RESTRICT,
	FOREIGN KEY(contact_id) REFERENCES v2_contacts (id) ON DELETE RESTRICT,
	CONSTRAINT ck_v2_safety_lock_channel CHECK (channel IN ('email', 'linkedin', 'whatsapp', 'offline')),
	FOREIGN KEY(unlocked_by_user_id) REFERENCES users (id) ON DELETE SET NULL
)
        """.strip(),
        (
            'CREATE INDEX ix_v2_safety_lock_owner_active ON v2_safety_locks (owner_id, active, scope)',
            'CREATE INDEX ix_v2_safety_locks_campaign_id ON v2_safety_locks (campaign_id)',
            'CREATE INDEX ix_v2_safety_locks_company_id ON v2_safety_locks (company_id)',
            'CREATE INDEX ix_v2_safety_locks_contact_id ON v2_safety_locks (contact_id)',
            'CREATE INDEX ix_v2_safety_locks_owner_id ON v2_safety_locks (owner_id)',
        ),
    ),
    (
        'v2_sequence_steps',
        """
CREATE TABLE v2_sequence_steps (
	id INTEGER NOT NULL,
	owner_id INTEGER NOT NULL,
	campaign_revision_id INTEGER NOT NULL,
	position INTEGER NOT NULL,
	channel VARCHAR(8) NOT NULL,
	wait_minutes INTEGER NOT NULL,
	template_version VARCHAR(100),
	condition_definition JSON NOT NULL,
	stop_condition_definition JSON NOT NULL,
	created_at DATETIME NOT NULL,
	updated_at DATETIME NOT NULL,
	archived_at DATETIME,
	PRIMARY KEY (id),
	CONSTRAINT uq_v2_sequence_step_position UNIQUE (campaign_revision_id, position),
	FOREIGN KEY(owner_id) REFERENCES users (id) ON DELETE RESTRICT,
	FOREIGN KEY(campaign_revision_id) REFERENCES v2_campaign_revisions (id) ON DELETE RESTRICT,
	CONSTRAINT ck_v2_sequence_step_channel CHECK (channel IN ('email', 'linkedin', 'whatsapp', 'offline'))
)
        """.strip(),
        (
            'CREATE INDEX ix_v2_sequence_revision_channel ON v2_sequence_steps (campaign_revision_id, channel)',
            'CREATE INDEX ix_v2_sequence_steps_archived_at ON v2_sequence_steps (archived_at)',
            'CREATE INDEX ix_v2_sequence_steps_campaign_revision_id ON v2_sequence_steps (campaign_revision_id)',
            'CREATE INDEX ix_v2_sequence_steps_owner_id ON v2_sequence_steps (owner_id)',
        ),
    ),
    (
        'v2_consent_restrictions',
        """
CREATE TABLE v2_consent_restrictions (
	id INTEGER NOT NULL,
	idempotency_key VARCHAR(255) NOT NULL,
	owner_id INTEGER NOT NULL,
	scope VARCHAR(13) NOT NULL,
	channel VARCHAR(8),
	contact_point_id INTEGER,
	contact_id INTEGER,
	company_id INTEGER,
	reason VARCHAR(500) NOT NULL,
	source VARCHAR(100) NOT NULL,
	active BOOLEAN NOT NULL,
	created_by_user_id INTEGER,
	expires_at DATETIME,
	revoked_at DATETIME,
	metadata_json JSON,
	created_at DATETIME NOT NULL,
	updated_at DATETIME NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT uq_v2_consent_idempotency UNIQUE (idempotency_key),
	FOREIGN KEY(owner_id) REFERENCES users (id) ON DELETE RESTRICT,
	CONSTRAINT ck_v2_consent_restriction_scope CHECK (scope IN ('contact_point', 'contact', 'company', 'global')),
	CONSTRAINT ck_v2_consent_restriction_channel CHECK (channel IN ('email', 'linkedin', 'whatsapp', 'offline')),
	FOREIGN KEY(contact_point_id) REFERENCES v2_contact_points (id) ON DELETE RESTRICT,
	FOREIGN KEY(contact_id) REFERENCES v2_contacts (id) ON DELETE RESTRICT,
	FOREIGN KEY(company_id) REFERENCES v2_companies (id) ON DELETE RESTRICT,
	FOREIGN KEY(created_by_user_id) REFERENCES users (id) ON DELETE SET NULL
)
        """.strip(),
        (
            'CREATE INDEX ix_v2_consent_contact_targets ON v2_consent_restrictions (contact_point_id, contact_id, company_id)',
            'CREATE INDEX ix_v2_consent_owner_active_scope ON v2_consent_restrictions (owner_id, active, scope)',
            'CREATE INDEX ix_v2_consent_restrictions_company_id ON v2_consent_restrictions (company_id)',
            'CREATE INDEX ix_v2_consent_restrictions_contact_id ON v2_consent_restrictions (contact_id)',
            'CREATE INDEX ix_v2_consent_restrictions_contact_point_id ON v2_consent_restrictions (contact_point_id)',
            'CREATE INDEX ix_v2_consent_restrictions_owner_id ON v2_consent_restrictions (owner_id)',
        ),
    ),
    (
        'v2_conversations',
        """
CREATE TABLE v2_conversations (
	id INTEGER NOT NULL,
	owner_id INTEGER NOT NULL,
	company_id INTEGER NOT NULL,
	contact_id INTEGER NOT NULL,
	contact_point_id INTEGER,
	channel VARCHAR(8) NOT NULL,
	status VARCHAR(18) NOT NULL,
	provider_thread_id VARCHAR(500),
	subject VARCHAR(1000),
	latest_reply_body TEXT,
	last_message_at DATETIME,
	created_at DATETIME NOT NULL,
	updated_at DATETIME NOT NULL,
	archived_at DATETIME,
	PRIMARY KEY (id),
	FOREIGN KEY(owner_id) REFERENCES users (id) ON DELETE RESTRICT,
	FOREIGN KEY(company_id) REFERENCES v2_companies (id) ON DELETE RESTRICT,
	FOREIGN KEY(contact_id) REFERENCES v2_contacts (id) ON DELETE RESTRICT,
	FOREIGN KEY(contact_point_id) REFERENCES v2_contact_points (id) ON DELETE RESTRICT,
	CONSTRAINT ck_v2_conversation_channel CHECK (channel IN ('email', 'linkedin', 'whatsapp', 'offline')),
	CONSTRAINT ck_v2_conversation_status CHECK (status IN ('open', 'waiting_on_us', 'waiting_on_contact', 'closed'))
)
        """.strip(),
        (
            'CREATE INDEX ix_v2_conversation_contact_channel ON v2_conversations (contact_id, channel)',
            'CREATE INDEX ix_v2_conversation_owner_last_message ON v2_conversations (owner_id, last_message_at)',
            'CREATE INDEX ix_v2_conversations_archived_at ON v2_conversations (archived_at)',
            'CREATE INDEX ix_v2_conversations_company_id ON v2_conversations (company_id)',
            'CREATE INDEX ix_v2_conversations_contact_id ON v2_conversations (contact_id)',
            'CREATE INDEX ix_v2_conversations_contact_point_id ON v2_conversations (contact_point_id)',
            'CREATE INDEX ix_v2_conversations_last_message_at ON v2_conversations (last_message_at)',
            'CREATE INDEX ix_v2_conversations_owner_id ON v2_conversations (owner_id)',
            'CREATE INDEX ix_v2_conversations_provider_thread_id ON v2_conversations (provider_thread_id)',
        ),
    ),
    (
        'v2_outreach_attempts',
        """
CREATE TABLE v2_outreach_attempts (
	id INTEGER NOT NULL,
	owner_id INTEGER NOT NULL,
	campaign_id INTEGER NOT NULL,
	enrollment_id INTEGER NOT NULL,
	sequence_step_id INTEGER NOT NULL,
	contact_point_id INTEGER NOT NULL,
	channel VARCHAR(8) NOT NULL,
	kind VARCHAR(9) NOT NULL,
	idempotency_key VARCHAR(255) NOT NULL,
	status VARCHAR(9) NOT NULL,
	priority INTEGER NOT NULL,
	scheduled_at DATETIME NOT NULL,
	claimed_by VARCHAR(255),
	lease_expires_at DATETIME,
	attempt_count INTEGER NOT NULL,
	provider VARCHAR(100),
	provider_message_id VARCHAR(500),
	provider_response JSON,
	unknown_reason TEXT,
	last_error TEXT,
	sent_at DATETIME,
	created_at DATETIME NOT NULL,
	updated_at DATETIME NOT NULL,
	archived_at DATETIME,
	PRIMARY KEY (id),
	CONSTRAINT uq_v2_attempt_idempotency UNIQUE (idempotency_key),
	FOREIGN KEY(owner_id) REFERENCES users (id) ON DELETE RESTRICT,
	FOREIGN KEY(campaign_id) REFERENCES v2_campaigns (id) ON DELETE RESTRICT,
	FOREIGN KEY(enrollment_id) REFERENCES v2_enrollments (id) ON DELETE RESTRICT,
	FOREIGN KEY(sequence_step_id) REFERENCES v2_sequence_steps (id) ON DELETE RESTRICT,
	FOREIGN KEY(contact_point_id) REFERENCES v2_contact_points (id) ON DELETE RESTRICT,
	CONSTRAINT ck_v2_outreach_attempt_channel CHECK (channel IN ('email', 'linkedin', 'whatsapp', 'offline')),
	CONSTRAINT ck_v2_outreach_attempt_kind CHECK (kind IN ('cold', 'follow_up', 'reply')),
	CONSTRAINT ck_v2_outreach_attempt_status CHECK (status IN ('queued', 'claimed', 'sending', 'succeeded', 'failed', 'unknown', 'blocked', 'cancelled'))
)
        """.strip(),
        (
            'CREATE INDEX ix_v2_attempt_campaign_status ON v2_outreach_attempts (campaign_id, status)',
            'CREATE INDEX ix_v2_attempt_claim ON v2_outreach_attempts (status, scheduled_at, lease_expires_at)',
            'CREATE INDEX ix_v2_outreach_attempts_archived_at ON v2_outreach_attempts (archived_at)',
            'CREATE INDEX ix_v2_outreach_attempts_campaign_id ON v2_outreach_attempts (campaign_id)',
            'CREATE INDEX ix_v2_outreach_attempts_contact_point_id ON v2_outreach_attempts (contact_point_id)',
            'CREATE INDEX ix_v2_outreach_attempts_enrollment_id ON v2_outreach_attempts (enrollment_id)',
            'CREATE INDEX ix_v2_outreach_attempts_owner_id ON v2_outreach_attempts (owner_id)',
            'CREATE INDEX ix_v2_outreach_attempts_provider_message_id ON v2_outreach_attempts (provider_message_id)',
            'CREATE INDEX ix_v2_outreach_attempts_sequence_step_id ON v2_outreach_attempts (sequence_step_id)',
        ),
    ),
    (
        'v2_automation_jobs',
        """
CREATE TABLE v2_automation_jobs (
	id INTEGER NOT NULL,
	owner_id INTEGER NOT NULL,
	campaign_id INTEGER,
	enrollment_id INTEGER,
	attempt_id INTEGER,
	status VARCHAR(9) NOT NULL,
	job_type VARCHAR(100) NOT NULL,
	queue VARCHAR(100) NOT NULL,
	payload JSON NOT NULL,
	idempotency_key VARCHAR(255) NOT NULL,
	priority INTEGER NOT NULL,
	scheduled_at DATETIME NOT NULL,
	attempts INTEGER NOT NULL,
	max_attempts INTEGER NOT NULL,
	lease_owner VARCHAR(255),
	lease_expires_at DATETIME,
	last_error TEXT,
	result JSON,
	completed_at DATETIME,
	created_at DATETIME NOT NULL,
	updated_at DATETIME NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT uq_v2_job_idempotency UNIQUE (idempotency_key),
	FOREIGN KEY(owner_id) REFERENCES users (id) ON DELETE RESTRICT,
	FOREIGN KEY(campaign_id) REFERENCES v2_campaigns (id) ON DELETE SET NULL,
	FOREIGN KEY(enrollment_id) REFERENCES v2_enrollments (id) ON DELETE SET NULL,
	FOREIGN KEY(attempt_id) REFERENCES v2_outreach_attempts (id) ON DELETE SET NULL,
	CONSTRAINT ck_v2_automation_job_status CHECK (status IN ('pending', 'claimed', 'running', 'succeeded', 'failed', 'retry', 'cancelled', 'unknown'))
)
        """.strip(),
        (
            'CREATE INDEX ix_v2_automation_jobs_campaign_id ON v2_automation_jobs (campaign_id)',
            'CREATE INDEX ix_v2_automation_jobs_enrollment_id ON v2_automation_jobs (enrollment_id)',
            'CREATE INDEX ix_v2_automation_jobs_owner_id ON v2_automation_jobs (owner_id)',
            'CREATE INDEX ix_v2_job_claim ON v2_automation_jobs (queue, status, scheduled_at, priority)',
            'CREATE INDEX ix_v2_job_lease ON v2_automation_jobs (status, lease_expires_at)',
        ),
    ),
    (
        'v2_manual_overrides',
        """
CREATE TABLE v2_manual_overrides (
	id INTEGER NOT NULL,
	owner_id INTEGER NOT NULL,
	gate VARCHAR(17) NOT NULL,
	enrollment_id INTEGER NOT NULL,
	attempt_id INTEGER,
	reason VARCHAR(1000) NOT NULL,
	expires_at DATETIME NOT NULL,
	created_by_user_id INTEGER NOT NULL,
	consumed_at DATETIME,
	revoked_at DATETIME,
	metadata_json JSON,
	created_at DATETIME NOT NULL,
	PRIMARY KEY (id),
	FOREIGN KEY(owner_id) REFERENCES users (id) ON DELETE RESTRICT,
	CONSTRAINT ck_v2_manual_override_gate CHECK (gate IN ('fit', 'research_evidence', 'timezone')),
	FOREIGN KEY(enrollment_id) REFERENCES v2_enrollments (id) ON DELETE RESTRICT,
	FOREIGN KEY(attempt_id) REFERENCES v2_outreach_attempts (id) ON DELETE RESTRICT,
	FOREIGN KEY(created_by_user_id) REFERENCES users (id) ON DELETE RESTRICT
)
        """.strip(),
        (
            'CREATE INDEX ix_v2_manual_overrides_enrollment_id ON v2_manual_overrides (enrollment_id)',
            'CREATE INDEX ix_v2_manual_overrides_owner_id ON v2_manual_overrides (owner_id)',
            'CREATE INDEX ix_v2_override_enrollment_gate ON v2_manual_overrides (enrollment_id, gate, expires_at)',
        ),
    ),
    (
        'v2_message_events',
        """
CREATE TABLE v2_message_events (
	id INTEGER NOT NULL,
	owner_id INTEGER NOT NULL,
	conversation_id INTEGER,
	outreach_attempt_id INTEGER,
	channel VARCHAR(8) NOT NULL,
	direction VARCHAR(8) NOT NULL,
	event_type VARCHAR(12) NOT NULL,
	provider VARCHAR(100),
	ingest_idempotency_key VARCHAR(255),
	provider_event_id VARCHAR(500),
	provider_message_id VARCHAR(500),
	subject VARCHAR(1000),
	body TEXT,
	latest_body TEXT,
	occurred_at DATETIME NOT NULL,
	metadata_json JSON,
	created_at DATETIME NOT NULL,
	updated_at DATETIME NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT uq_v2_message_provider_event UNIQUE (owner_id, provider, provider_event_id),
	CONSTRAINT uq_v2_message_ingest_idempotency UNIQUE (owner_id, ingest_idempotency_key),
	FOREIGN KEY(owner_id) REFERENCES users (id) ON DELETE RESTRICT,
	FOREIGN KEY(conversation_id) REFERENCES v2_conversations (id) ON DELETE RESTRICT,
	FOREIGN KEY(outreach_attempt_id) REFERENCES v2_outreach_attempts (id) ON DELETE RESTRICT,
	CONSTRAINT ck_v2_message_event_channel CHECK (channel IN ('email', 'linkedin', 'whatsapp', 'offline')),
	CONSTRAINT ck_v2_message_event_direction CHECK (direction IN ('inbound', 'outbound')),
	CONSTRAINT ck_v2_message_event_type CHECK (event_type IN ('queued', 'sent', 'delivered', 'opened', 'replied', 'bounced', 'failed', 'unsubscribed', 'unknown'))
)
        """.strip(),
        (
            'CREATE INDEX ix_v2_message_attempt_event ON v2_message_events (outreach_attempt_id, event_type)',
            'CREATE INDEX ix_v2_message_conversation_occurred ON v2_message_events (conversation_id, occurred_at)',
            'CREATE INDEX ix_v2_message_events_conversation_id ON v2_message_events (conversation_id)',
            'CREATE INDEX ix_v2_message_events_outreach_attempt_id ON v2_message_events (outreach_attempt_id)',
            'CREATE INDEX ix_v2_message_events_owner_id ON v2_message_events (owner_id)',
            'CREATE INDEX ix_v2_message_events_provider_message_id ON v2_message_events (provider_message_id)',
        ),
    ),
    (
        'v2_provider_cost_events',
        """
CREATE TABLE v2_provider_cost_events (
	id INTEGER NOT NULL,
	owner_id INTEGER NOT NULL,
	provider VARCHAR(100) NOT NULL,
	operation VARCHAR(100) NOT NULL,
	status VARCHAR(8) NOT NULL,
	units NUMERIC(18, 4) NOT NULL,
	native_unit VARCHAR(50) NOT NULL,
	unit_price NUMERIC(18, 6),
	normalized_amount NUMERIC(18, 6),
	normalized_currency VARCHAR(3),
	result_count INTEGER NOT NULL,
	billable BOOLEAN NOT NULL,
	price_version VARCHAR(100) NOT NULL,
	campaign_id INTEGER,
	enrollment_id INTEGER,
	company_id INTEGER,
	contact_id INTEGER,
	outreach_attempt_id INTEGER,
	idempotency_key VARCHAR(255) NOT NULL,
	metadata_json JSON,
	created_at DATETIME NOT NULL,
	updated_at DATETIME NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT uq_v2_provider_cost_idempotency UNIQUE (idempotency_key),
	FOREIGN KEY(owner_id) REFERENCES users (id) ON DELETE RESTRICT,
	CONSTRAINT ck_v2_provider_cost_event_status CHECK (status IN ('reserved', 'charged', 'refunded', 'failed', 'unknown')),
	FOREIGN KEY(campaign_id) REFERENCES v2_campaigns (id) ON DELETE RESTRICT,
	FOREIGN KEY(enrollment_id) REFERENCES v2_enrollments (id) ON DELETE RESTRICT,
	FOREIGN KEY(company_id) REFERENCES v2_companies (id) ON DELETE RESTRICT,
	FOREIGN KEY(contact_id) REFERENCES v2_contacts (id) ON DELETE RESTRICT,
	FOREIGN KEY(outreach_attempt_id) REFERENCES v2_outreach_attempts (id) ON DELETE RESTRICT
)
        """.strip(),
        (
            'CREATE INDEX ix_v2_provider_cost_campaign_provider ON v2_provider_cost_events (campaign_id, provider)',
            'CREATE INDEX ix_v2_provider_cost_events_campaign_id ON v2_provider_cost_events (campaign_id)',
            'CREATE INDEX ix_v2_provider_cost_events_company_id ON v2_provider_cost_events (company_id)',
            'CREATE INDEX ix_v2_provider_cost_events_contact_id ON v2_provider_cost_events (contact_id)',
            'CREATE INDEX ix_v2_provider_cost_events_enrollment_id ON v2_provider_cost_events (enrollment_id)',
            'CREATE INDEX ix_v2_provider_cost_events_outreach_attempt_id ON v2_provider_cost_events (outreach_attempt_id)',
            'CREATE INDEX ix_v2_provider_cost_events_owner_id ON v2_provider_cost_events (owner_id)',
            'CREATE INDEX ix_v2_provider_cost_events_provider ON v2_provider_cost_events (provider)',
            'CREATE INDEX ix_v2_provider_cost_owner_created ON v2_provider_cost_events (owner_id, created_at)',
        ),
    ),
    (
        'v2_reply_assessments',
        """
CREATE TABLE v2_reply_assessments (
	id INTEGER NOT NULL,
	owner_id INTEGER NOT NULL,
	conversation_id INTEGER NOT NULL,
	message_event_id INTEGER,
	enrollment_id INTEGER,
	intent VARCHAR(14) NOT NULL,
	is_positive BOOLEAN NOT NULL,
	confidence NUMERIC(5, 4),
	status VARCHAR(9) NOT NULL,
	latest_reply_body TEXT NOT NULL,
	rationale TEXT,
	assessed_by VARCHAR(50) NOT NULL,
	confirmed_by_user_id INTEGER,
	confirmed_at DATETIME,
	created_at DATETIME NOT NULL,
	updated_at DATETIME NOT NULL,
	PRIMARY KEY (id),
	FOREIGN KEY(owner_id) REFERENCES users (id) ON DELETE RESTRICT,
	FOREIGN KEY(conversation_id) REFERENCES v2_conversations (id) ON DELETE RESTRICT,
	FOREIGN KEY(message_event_id) REFERENCES v2_message_events (id) ON DELETE RESTRICT,
	FOREIGN KEY(enrollment_id) REFERENCES v2_enrollments (id) ON DELETE SET NULL,
	CONSTRAINT ck_v2_reply_assessment_intent CHECK (intent IN ('interested', 'more_info', 'referral', 'meeting', 'not_interested', 'unsubscribe', 'out_of_office', 'bounce', 'other')),
	CONSTRAINT ck_v2_reply_assessment_status CHECK (status IN ('proposed', 'confirmed', 'rejected')),
	FOREIGN KEY(confirmed_by_user_id) REFERENCES users (id) ON DELETE SET NULL
)
        """.strip(),
        (
            'CREATE INDEX ix_v2_reply_assessment_conversation ON v2_reply_assessments (conversation_id, created_at)',
            'CREATE INDEX ix_v2_reply_assessments_conversation_id ON v2_reply_assessments (conversation_id)',
            'CREATE INDEX ix_v2_reply_assessments_enrollment_id ON v2_reply_assessments (enrollment_id)',
            'CREATE INDEX ix_v2_reply_assessments_message_event_id ON v2_reply_assessments (message_event_id)',
            'CREATE INDEX ix_v2_reply_assessments_owner_id ON v2_reply_assessments (owner_id)',
        ),
    ),
    (
        'v2_opportunities',
        """
CREATE TABLE v2_opportunities (
	id INTEGER NOT NULL,
	owner_id INTEGER NOT NULL,
	assignee_user_id INTEGER NOT NULL,
	company_id INTEGER NOT NULL,
	contact_id INTEGER NOT NULL,
	campaign_id INTEGER,
	conversation_id INTEGER NOT NULL,
	reply_assessment_id INTEGER NOT NULL,
	source_task_id INTEGER,
	stage VARCHAR(15) NOT NULL,
	fit_confirmed BOOLEAN NOT NULL,
	fit_override_id INTEGER,
	value_amount NUMERIC(18, 2),
	currency VARCHAR(3),
	expected_close_date DATE,
	next_action VARCHAR(1000) NOT NULL,
	next_action_due_at DATETIME NOT NULL,
	qualified_at DATETIME NOT NULL,
	won_at DATETIME,
	lost_at DATETIME,
	lost_reason VARCHAR(1000),
	created_at DATETIME NOT NULL,
	updated_at DATETIME NOT NULL,
	archived_at DATETIME,
	PRIMARY KEY (id),
	CONSTRAINT uq_v2_opportunity_source_task UNIQUE (source_task_id),
	FOREIGN KEY(owner_id) REFERENCES users (id) ON DELETE RESTRICT,
	FOREIGN KEY(assignee_user_id) REFERENCES users (id) ON DELETE RESTRICT,
	FOREIGN KEY(company_id) REFERENCES v2_companies (id) ON DELETE RESTRICT,
	FOREIGN KEY(contact_id) REFERENCES v2_contacts (id) ON DELETE RESTRICT,
	FOREIGN KEY(campaign_id) REFERENCES v2_campaigns (id) ON DELETE SET NULL,
	FOREIGN KEY(conversation_id) REFERENCES v2_conversations (id) ON DELETE RESTRICT,
	FOREIGN KEY(reply_assessment_id) REFERENCES v2_reply_assessments (id) ON DELETE RESTRICT,
	CONSTRAINT ck_v2_opportunity_stage CHECK (stage IN ('qualified_reply', 'discovery', 'sample_or_quote', 'negotiation', 'won', 'lost'))
)
        """.strip(),
        (
            'CREATE INDEX ix_v2_opportunities_archived_at ON v2_opportunities (archived_at)',
            'CREATE INDEX ix_v2_opportunities_assignee_user_id ON v2_opportunities (assignee_user_id)',
            'CREATE INDEX ix_v2_opportunities_campaign_id ON v2_opportunities (campaign_id)',
            'CREATE INDEX ix_v2_opportunities_company_id ON v2_opportunities (company_id)',
            'CREATE INDEX ix_v2_opportunities_contact_id ON v2_opportunities (contact_id)',
            'CREATE INDEX ix_v2_opportunities_conversation_id ON v2_opportunities (conversation_id)',
            'CREATE INDEX ix_v2_opportunities_owner_id ON v2_opportunities (owner_id)',
            'CREATE INDEX ix_v2_opportunities_reply_assessment_id ON v2_opportunities (reply_assessment_id)',
            'CREATE INDEX ix_v2_opportunities_source_task_id ON v2_opportunities (source_task_id)',
            'CREATE INDEX ix_v2_opportunity_company_stage ON v2_opportunities (company_id, stage)',
            'CREATE INDEX ix_v2_opportunity_owner_stage ON v2_opportunities (owner_id, stage)',
        ),
    ),
    (
        'v2_tasks',
        """
CREATE TABLE v2_tasks (
	id INTEGER NOT NULL,
	owner_id INTEGER NOT NULL,
	task_type VARCHAR(27) NOT NULL,
	status VARCHAR(11) NOT NULL,
	priority VARCHAR(6) NOT NULL,
	company_id INTEGER,
	contact_id INTEGER,
	campaign_id INTEGER,
	enrollment_id INTEGER,
	conversation_id INTEGER,
	opportunity_id INTEGER,
	attempt_id INTEGER,
	automation_job_id INTEGER,
	title VARCHAR(500) NOT NULL,
	description TEXT,
	assignee_user_id INTEGER,
	due_at DATETIME,
	completed_at DATETIME,
	metadata_json JSON,
	created_at DATETIME NOT NULL,
	updated_at DATETIME NOT NULL,
	archived_at DATETIME,
	PRIMARY KEY (id),
	FOREIGN KEY(owner_id) REFERENCES users (id) ON DELETE RESTRICT,
	CONSTRAINT ck_v2_task_type CHECK (task_type IN ('campaign_readiness', 'research_required', 'contact_enrichment_required', 'draft_review', 'reply_triage', 'send_failure', 'deliverability_alert', 'provider_budget_alert', 'sales_handoff', 'reconciliation')),
	CONSTRAINT ck_v2_task_status CHECK (status IN ('open', 'in_progress', 'completed', 'dismissed')),
	CONSTRAINT ck_v2_task_priority CHECK (priority IN ('low', 'normal', 'high', 'urgent')),
	FOREIGN KEY(company_id) REFERENCES v2_companies (id) ON DELETE SET NULL,
	FOREIGN KEY(contact_id) REFERENCES v2_contacts (id) ON DELETE SET NULL,
	FOREIGN KEY(campaign_id) REFERENCES v2_campaigns (id) ON DELETE SET NULL,
	FOREIGN KEY(enrollment_id) REFERENCES v2_enrollments (id) ON DELETE SET NULL,
	FOREIGN KEY(conversation_id) REFERENCES v2_conversations (id) ON DELETE SET NULL,
	FOREIGN KEY(opportunity_id) REFERENCES v2_opportunities (id) ON DELETE SET NULL,
	FOREIGN KEY(attempt_id) REFERENCES v2_outreach_attempts (id) ON DELETE SET NULL,
	FOREIGN KEY(assignee_user_id) REFERENCES users (id) ON DELETE SET NULL
)
        """.strip(),
        (
            'CREATE INDEX ix_v2_task_subject ON v2_tasks (company_id, contact_id, campaign_id)',
            'CREATE INDEX ix_v2_task_work_queue ON v2_tasks (owner_id, status, priority, due_at)',
            'CREATE INDEX ix_v2_tasks_archived_at ON v2_tasks (archived_at)',
            'CREATE INDEX ix_v2_tasks_assignee_user_id ON v2_tasks (assignee_user_id)',
            'CREATE INDEX ix_v2_tasks_automation_job_id ON v2_tasks (automation_job_id)',
            'CREATE INDEX ix_v2_tasks_campaign_id ON v2_tasks (campaign_id)',
            'CREATE INDEX ix_v2_tasks_company_id ON v2_tasks (company_id)',
            'CREATE INDEX ix_v2_tasks_contact_id ON v2_tasks (contact_id)',
            'CREATE INDEX ix_v2_tasks_conversation_id ON v2_tasks (conversation_id)',
            'CREATE INDEX ix_v2_tasks_due_at ON v2_tasks (due_at)',
            'CREATE INDEX ix_v2_tasks_enrollment_id ON v2_tasks (enrollment_id)',
            'CREATE INDEX ix_v2_tasks_owner_id ON v2_tasks (owner_id)',
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
    raise RuntimeError('Product V2 expand migrations are non-destructive and cannot be downgraded')
