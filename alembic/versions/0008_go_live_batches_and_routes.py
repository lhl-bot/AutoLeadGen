"""Add go-live review batches, frozen routes, consent, and task scopes."""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision: str = "0008_go_live_batches_and_routes"
down_revision: str | None = "0007_acquisition_activation"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _tables() -> set[str]:
    return set(inspect(op.get_bind()).get_table_names())


def _indexes(table_name: str) -> set[str]:
    return {item["name"] for item in inspect(op.get_bind()).get_indexes(table_name)}


def _ensure_indexes(table_name: str, indexes: tuple[tuple[str, tuple[str, ...]], ...]) -> None:
    existing = _indexes(table_name)
    for name, columns in indexes:
        if name not in existing:
            op.create_index(name, table_name, list(columns))


def _add_task_queue_scope() -> None:
    columns = {item["name"] for item in inspect(op.get_bind()).get_columns("v2_tasks")}
    check_rows = inspect(op.get_bind()).get_check_constraints("v2_tasks")
    checks = {item.get("name") for item in check_rows}
    task_type_check = next(
        (str(item.get("sqltext") or "").lower() for item in check_rows if item.get("name") == "ck_v2_task_type"),
        "",
    )
    task_type_is_current = "data_governance" in task_type_check
    if "queue_scope" in columns and "ck_v2_task_queue_scope" in checks and task_type_is_current:
        return
    expression = "queue_scope IN ('sales', 'admin', 'data_governance')"
    task_type_expression = (
        "task_type IN ('campaign_readiness', 'research_required', "
        "'contact_enrichment_required', 'draft_review', 'reply_triage', "
        "'send_failure', 'deliverability_alert', 'provider_budget_alert', "
        "'sales_handoff', 'reconciliation', 'data_governance')"
    )
    if op.get_bind().dialect.name == "sqlite":
        with op.batch_alter_table("v2_tasks", recreate="always") as batch:
            if "queue_scope" not in columns:
                batch.add_column(
                    sa.Column("queue_scope", sa.String(length=15), nullable=False, server_default="sales")
                )
            if "ck_v2_task_queue_scope" not in checks:
                batch.create_check_constraint("ck_v2_task_queue_scope", expression)
            if not task_type_is_current:
                if "ck_v2_task_type" in checks:
                    batch.drop_constraint("ck_v2_task_type", type_="check")
                batch.create_check_constraint("ck_v2_task_type", task_type_expression)
        return
    if "queue_scope" not in columns:
        op.add_column(
            "v2_tasks",
            sa.Column("queue_scope", sa.String(length=15), nullable=False, server_default="sales"),
        )
    if "ck_v2_task_queue_scope" not in checks:
        op.create_check_constraint("ck_v2_task_queue_scope", "v2_tasks", expression)
    if not task_type_is_current:
        if "ck_v2_task_type" in checks:
            op.drop_constraint("ck_v2_task_type", "v2_tasks", type_="check")
        op.create_check_constraint("ck_v2_task_type", "v2_tasks", task_type_expression)


def upgrade() -> None:
    _add_task_queue_scope()
    op.execute(
        sa.text(
            "UPDATE v2_tasks SET task_type = 'data_governance', queue_scope = 'data_governance' "
            "WHERE title LIKE 'Review legacy %' "
            "OR title LIKE 'Complete legacy lead %' "
            "OR title LIKE 'Reconcile legacy lead %'"
        )
    )
    existing = _tables()

    if "v2_route_proposals" not in existing:
        op.create_table(
            "v2_route_proposals",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("owner_id", sa.Integer(), nullable=False),
            sa.Column("enrollment_id", sa.Integer(), nullable=False),
            sa.Column("contact_id", sa.Integer(), nullable=False),
            sa.Column("status", sa.String(length=21), nullable=False),
            sa.Column("idempotency_key", sa.String(length=255), nullable=False),
            sa.Column("ai_model", sa.String(length=100), nullable=False),
            sa.Column("ai_reason", sa.Text(), nullable=False),
            sa.Column("confidence", sa.Numeric(5, 4), nullable=False),
            sa.Column("evidence_snapshot_ids", sa.JSON(), nullable=False),
            sa.Column("checksum", sa.String(length=64), nullable=False),
            sa.Column("proposed_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("rejected_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.CheckConstraint("confidence >= 0 AND confidence <= 1", name="ck_v2_route_proposal_confidence"),
            sa.CheckConstraint(
                "status IN ('draft', 'human_review_required', 'previewed', 'approved', 'rejected', 'superseded')",
                name="ck_v2_route_proposal_status",
            ),
            sa.ForeignKeyConstraint(["owner_id"], ["users.id"], ondelete="RESTRICT"),
            sa.ForeignKeyConstraint(["enrollment_id"], ["v2_enrollments.id"], ondelete="RESTRICT"),
            sa.ForeignKeyConstraint(["contact_id"], ["v2_contacts.id"], ondelete="RESTRICT"),
            sa.UniqueConstraint("owner_id", "idempotency_key", name="uq_v2_route_proposal_idempotency"),
        )
    _ensure_indexes(
        "v2_route_proposals",
        (
            ("ix_v2_route_proposals_owner_id", ("owner_id",)),
            ("ix_v2_route_proposals_enrollment_id", ("enrollment_id",)),
            ("ix_v2_route_proposals_contact_id", ("contact_id",)),
            ("ix_v2_route_proposal_owner_status", ("owner_id", "status", "created_at")),
            ("ix_v2_route_proposal_enrollment", ("enrollment_id", "status")),
        ),
    )

    if "v2_route_proposal_steps" not in _tables():
        op.create_table(
            "v2_route_proposal_steps",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("owner_id", sa.Integer(), nullable=False),
            sa.Column("route_proposal_id", sa.Integer(), nullable=False),
            sa.Column("sequence_step_id", sa.Integer(), nullable=False),
            sa.Column("attempt_id", sa.Integer(), nullable=True),
            sa.Column("contact_point_id", sa.Integer(), nullable=False),
            sa.Column("channel_account_id", sa.Integer(), nullable=False),
            sa.Column("position", sa.Integer(), nullable=False),
            sa.Column("channel", sa.String(length=8), nullable=False),
            sa.Column("scheduled_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("subject", sa.Text(), nullable=True),
            sa.Column("body", sa.Text(), nullable=False),
            sa.Column("ai_reason", sa.Text(), nullable=False),
            sa.Column("confidence", sa.Numeric(5, 4), nullable=False),
            sa.Column("evidence_snapshot_ids", sa.JSON(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.CheckConstraint("position >= 1 AND position <= 3", name="ck_v2_route_proposal_step_position"),
            sa.CheckConstraint("confidence >= 0 AND confidence <= 1", name="ck_v2_route_proposal_step_confidence"),
            sa.CheckConstraint("channel IN ('email', 'linkedin', 'whatsapp', 'offline')", name="ck_v2_route_proposal_step_channel"),
            sa.ForeignKeyConstraint(["owner_id"], ["users.id"], ondelete="RESTRICT"),
            sa.ForeignKeyConstraint(["route_proposal_id"], ["v2_route_proposals.id"], ondelete="RESTRICT"),
            sa.ForeignKeyConstraint(["sequence_step_id"], ["v2_sequence_steps.id"], ondelete="RESTRICT"),
            sa.ForeignKeyConstraint(["attempt_id"], ["v2_outreach_attempts.id"], ondelete="SET NULL"),
            sa.ForeignKeyConstraint(["contact_point_id"], ["v2_contact_points.id"], ondelete="RESTRICT"),
            sa.ForeignKeyConstraint(["channel_account_id"], ["v2_channel_accounts.id"], ondelete="RESTRICT"),
            sa.UniqueConstraint("route_proposal_id", "position", name="uq_v2_route_proposal_step_position"),
            sa.UniqueConstraint("route_proposal_id", "channel", name="uq_v2_route_proposal_step_channel"),
        )
    _ensure_indexes(
        "v2_route_proposal_steps",
        (
            ("ix_v2_route_proposal_steps_owner_id", ("owner_id",)),
            ("ix_v2_route_proposal_steps_route_proposal_id", ("route_proposal_id",)),
            ("ix_v2_route_proposal_steps_sequence_step_id", ("sequence_step_id",)),
            ("ix_v2_route_proposal_steps_attempt_id", ("attempt_id",)),
            ("ix_v2_route_proposal_steps_contact_point_id", ("contact_point_id",)),
            ("ix_v2_route_proposal_steps_channel_account_id", ("channel_account_id",)),
            ("ix_v2_route_proposal_step_account", ("channel_account_id", "scheduled_at")),
        ),
    )

    if "v2_review_batches" not in _tables():
        op.create_table(
            "v2_review_batches",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("owner_id", sa.Integer(), nullable=False),
            sa.Column("status", sa.String(length=9), nullable=False),
            sa.Column("idempotency_key", sa.String(length=255), nullable=False),
            sa.Column("approval_id", sa.String(length=255), nullable=False),
            sa.Column("item_count", sa.Integer(), nullable=False),
            sa.Column("preview_checksum", sa.String(length=64), nullable=True),
            sa.Column("estimated_cost", sa.Numeric(18, 6), nullable=False),
            sa.Column("price_version", sa.String(length=100), nullable=False),
            sa.Column("previewed_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("rejected_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("decided_by_user_id", sa.Integer(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.CheckConstraint("item_count >= 1 AND item_count <= 20", name="ck_v2_review_batch_item_count"),
            sa.CheckConstraint("status IN ('draft', 'previewed', 'approved', 'rejected', 'expired')", name="ck_v2_review_batch_status"),
            sa.ForeignKeyConstraint(["owner_id"], ["users.id"], ondelete="RESTRICT"),
            sa.ForeignKeyConstraint(["decided_by_user_id"], ["users.id"], ondelete="SET NULL"),
            sa.UniqueConstraint("owner_id", "idempotency_key", name="uq_v2_review_batch_idempotency"),
        )
    _ensure_indexes(
        "v2_review_batches",
        (
            ("ix_v2_review_batches_owner_id", ("owner_id",)),
            ("ix_v2_review_batch_owner_status", ("owner_id", "status", "created_at")),
        ),
    )

    if "v2_review_batch_items" not in _tables():
        op.create_table(
            "v2_review_batch_items",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("owner_id", sa.Integer(), nullable=False),
            sa.Column("review_batch_id", sa.Integer(), nullable=False),
            sa.Column("route_proposal_id", sa.Integer(), nullable=False),
            sa.Column("position", sa.Integer(), nullable=False),
            sa.Column("proposal_checksum", sa.String(length=64), nullable=False),
            sa.Column("preview_payload", sa.JSON(), nullable=False),
            sa.Column("edited", sa.Boolean(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(["owner_id"], ["users.id"], ondelete="RESTRICT"),
            sa.ForeignKeyConstraint(["review_batch_id"], ["v2_review_batches.id"], ondelete="RESTRICT"),
            sa.ForeignKeyConstraint(["route_proposal_id"], ["v2_route_proposals.id"], ondelete="RESTRICT"),
            sa.UniqueConstraint("review_batch_id", "route_proposal_id", name="uq_v2_review_batch_route"),
        )
    _ensure_indexes(
        "v2_review_batch_items",
        (
            ("ix_v2_review_batch_items_owner_id", ("owner_id",)),
            ("ix_v2_review_batch_items_review_batch_id", ("review_batch_id",)),
            ("ix_v2_review_batch_items_route_proposal_id", ("route_proposal_id",)),
            ("ix_v2_review_batch_item_batch_position", ("review_batch_id", "position")),
        ),
    )

    if "v2_enrollment_route_steps" not in _tables():
        op.create_table(
            "v2_enrollment_route_steps",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("owner_id", sa.Integer(), nullable=False),
            sa.Column("enrollment_id", sa.Integer(), nullable=False),
            sa.Column("route_proposal_id", sa.Integer(), nullable=False),
            sa.Column("route_proposal_step_id", sa.Integer(), nullable=False),
            sa.Column("review_batch_id", sa.Integer(), nullable=False),
            sa.Column("attempt_id", sa.Integer(), nullable=True),
            sa.Column("contact_point_id", sa.Integer(), nullable=False),
            sa.Column("channel_account_id", sa.Integer(), nullable=False),
            sa.Column("position", sa.Integer(), nullable=False),
            sa.Column("channel", sa.String(length=8), nullable=False),
            sa.Column("scheduled_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("subject", sa.Text(), nullable=True),
            sa.Column("body", sa.Text(), nullable=False),
            sa.Column("status", sa.String(length=20), nullable=False),
            sa.Column("approval_checksum", sa.String(length=64), nullable=False),
            sa.Column("approved_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.CheckConstraint("position >= 1 AND position <= 3", name="ck_v2_enrollment_route_position"),
            sa.CheckConstraint("channel IN ('email', 'linkedin', 'whatsapp', 'offline')", name="ck_v2_enrollment_route_channel"),
            sa.ForeignKeyConstraint(["owner_id"], ["users.id"], ondelete="RESTRICT"),
            sa.ForeignKeyConstraint(["enrollment_id"], ["v2_enrollments.id"], ondelete="RESTRICT"),
            sa.ForeignKeyConstraint(["route_proposal_id"], ["v2_route_proposals.id"], ondelete="RESTRICT"),
            sa.ForeignKeyConstraint(["route_proposal_step_id"], ["v2_route_proposal_steps.id"], ondelete="RESTRICT"),
            sa.ForeignKeyConstraint(["review_batch_id"], ["v2_review_batches.id"], ondelete="RESTRICT"),
            sa.ForeignKeyConstraint(["attempt_id"], ["v2_outreach_attempts.id"], ondelete="RESTRICT"),
            sa.ForeignKeyConstraint(["contact_point_id"], ["v2_contact_points.id"], ondelete="RESTRICT"),
            sa.ForeignKeyConstraint(["channel_account_id"], ["v2_channel_accounts.id"], ondelete="RESTRICT"),
            sa.UniqueConstraint("enrollment_id", "position", name="uq_v2_enrollment_route_position"),
            sa.UniqueConstraint("enrollment_id", "channel", name="uq_v2_enrollment_route_channel"),
            sa.UniqueConstraint("attempt_id", name="uq_v2_enrollment_route_attempt"),
            sa.UniqueConstraint("route_proposal_step_id", name="uq_v2_enrollment_route_steps_route_proposal_step_id"),
        )
    _ensure_indexes(
        "v2_enrollment_route_steps",
        (
            ("ix_v2_enrollment_route_steps_owner_id", ("owner_id",)),
            ("ix_v2_enrollment_route_steps_enrollment_id", ("enrollment_id",)),
            ("ix_v2_enrollment_route_steps_route_proposal_id", ("route_proposal_id",)),
            ("ix_v2_enrollment_route_steps_review_batch_id", ("review_batch_id",)),
            ("ix_v2_enrollment_route_execution", ("status", "scheduled_at")),
        ),
    )

    if "v2_whatsapp_consents" not in _tables():
        op.create_table(
            "v2_whatsapp_consents",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("owner_id", sa.Integer(), nullable=False),
            sa.Column("contact_id", sa.Integer(), nullable=False),
            sa.Column("contact_point_id", sa.Integer(), nullable=False),
            sa.Column("idempotency_key", sa.String(length=255), nullable=False),
            sa.Column("source", sa.String(length=100), nullable=False),
            sa.Column("evidence_url", sa.String(length=2000), nullable=True),
            sa.Column("evidence_text", sa.Text(), nullable=False),
            sa.Column("captured_by_user_id", sa.Integer(), nullable=True),
            sa.Column("granted_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("revoked_by_user_id", sa.Integer(), nullable=True),
            sa.Column("revocation_reason", sa.String(length=1000), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(["owner_id"], ["users.id"], ondelete="RESTRICT"),
            sa.ForeignKeyConstraint(["contact_id"], ["v2_contacts.id"], ondelete="RESTRICT"),
            sa.ForeignKeyConstraint(["contact_point_id"], ["v2_contact_points.id"], ondelete="RESTRICT"),
            sa.ForeignKeyConstraint(["captured_by_user_id"], ["users.id"], ondelete="SET NULL"),
            sa.ForeignKeyConstraint(["revoked_by_user_id"], ["users.id"], ondelete="SET NULL"),
            sa.UniqueConstraint("owner_id", "idempotency_key", name="uq_v2_whatsapp_consent_idempotency"),
        )
    _ensure_indexes(
        "v2_whatsapp_consents",
        (
            ("ix_v2_whatsapp_consents_owner_id", ("owner_id",)),
            ("ix_v2_whatsapp_consents_contact_id", ("contact_id",)),
            ("ix_v2_whatsapp_consents_contact_point_id", ("contact_point_id",)),
            ("ix_v2_whatsapp_consent_active", ("owner_id", "contact_point_id", "revoked_at", "expires_at")),
        ),
    )


def downgrade() -> None:
    raise RuntimeError(
        "0008 is intentionally forward-only; rollback by switching image digests and owner write paths"
    )
