"""Add staged acquisition runs and candidates for first-touch activation."""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision: str = "0007_acquisition_activation"
down_revision: str | None = "0006_message_event_complaint"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


RUN_TABLE = "v2_acquisition_runs"
CANDIDATE_TABLE = "v2_acquisition_candidates"


def _tables() -> set[str]:
    return set(inspect(op.get_bind()).get_table_names())


def _ensure_indexes(table_name: str, indexes: tuple[tuple[str, tuple[str, ...]], ...]) -> None:
    """Repair indexes left behind by interrupted non-transactional MySQL DDL."""

    for index_name, columns in indexes:
        existing = {
            item["name"]
            for item in inspect(op.get_bind()).get_indexes(table_name)
        }
        if index_name not in existing:
            op.create_index(index_name, table_name, list(columns))


def upgrade() -> None:
    existing = _tables()
    if RUN_TABLE not in existing:
        op.create_table(
            RUN_TABLE,
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("owner_id", sa.Integer(), nullable=False),
            sa.Column("source", sa.String(length=20), nullable=False),
            sa.Column("status", sa.String(length=20), nullable=False),
            sa.Column("name", sa.String(length=255), nullable=False),
            sa.Column("idempotency_key", sa.String(length=255), nullable=False),
            sa.Column("criteria", sa.JSON(), nullable=False),
            sa.Column("column_mapping", sa.JSON(), nullable=False),
            sa.Column("provider", sa.String(length=100), nullable=True),
            sa.Column("estimated_units", sa.Numeric(18, 4), nullable=True),
            sa.Column("price_version", sa.String(length=100), nullable=False),
            sa.Column("last_error", sa.Text(), nullable=True),
            sa.Column("committed_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.CheckConstraint("source IN ('csv', 'ai')", name="ck_v2_acquisition_run_source"),
            sa.CheckConstraint(
                "status IN ('draft', 'ready', 'processing', 'verified', 'committed', 'failed')",
                name="ck_v2_acquisition_run_status",
            ),
            sa.ForeignKeyConstraint(["owner_id"], ["users.id"], ondelete="RESTRICT"),
            sa.UniqueConstraint(
                "owner_id",
                "idempotency_key",
                name="uq_v2_acquisition_run_owner_idempotency",
            ),
        )
    _ensure_indexes(
        RUN_TABLE,
        (
            ("ix_v2_acquisition_runs_owner_id", ("owner_id",)),
            ("ix_v2_acquisition_run_owner_updated", ("owner_id", "updated_at")),
            ("ix_v2_acquisition_run_owner_status", ("owner_id", "status")),
        ),
    )

    existing = _tables()
    if CANDIDATE_TABLE not in existing:
        op.create_table(
            CANDIDATE_TABLE,
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("owner_id", sa.Integer(), nullable=False),
            sa.Column("run_id", sa.Integer(), nullable=False),
            sa.Column("row_number", sa.Integer(), nullable=False),
            sa.Column("status", sa.String(length=20), nullable=False),
            sa.Column("selected", sa.Boolean(), nullable=False),
            sa.Column("company_name", sa.String(length=255), nullable=True),
            sa.Column("normalized_domain", sa.String(length=255), nullable=True),
            sa.Column("first_name", sa.String(length=120), nullable=True),
            sa.Column("last_name", sa.String(length=120), nullable=True),
            sa.Column("full_name", sa.String(length=255), nullable=True),
            sa.Column("job_title", sa.String(length=255), nullable=True),
            sa.Column("email", sa.String(length=1000), nullable=True),
            sa.Column("normalized_email", sa.String(length=1000), nullable=True),
            sa.Column("source_url", sa.String(length=2000), nullable=True),
            sa.Column("evidence", sa.JSON(), nullable=False),
            sa.Column("confidence", sa.Numeric(5, 4), nullable=False),
            sa.Column("verification_status", sa.String(length=10), nullable=False),
            sa.Column("verification_source", sa.String(length=100), nullable=True),
            sa.Column("verification_checked_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("rejection_reason", sa.String(length=1000), nullable=True),
            sa.Column("committed_company_id", sa.Integer(), nullable=True),
            sa.Column("committed_contact_id", sa.Integer(), nullable=True),
            sa.Column("committed_contact_point_id", sa.Integer(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.CheckConstraint(
                "status IN ('ready', 'duplicate', 'invalid', 'selected', 'committed')",
                name="ck_v2_acquisition_candidate_status",
            ),
            sa.CheckConstraint(
                "verification_status IN ('unverified', 'valid', 'invalid', 'catch_all', 'unknown')",
                name="ck_v2_acquisition_candidate_verification",
            ),
            sa.ForeignKeyConstraint(["owner_id"], ["users.id"], ondelete="RESTRICT"),
            sa.ForeignKeyConstraint(["run_id"], [f"{RUN_TABLE}.id"], ondelete="RESTRICT"),
            sa.ForeignKeyConstraint(["committed_company_id"], ["v2_companies.id"], ondelete="SET NULL"),
            sa.ForeignKeyConstraint(["committed_contact_id"], ["v2_contacts.id"], ondelete="SET NULL"),
            sa.ForeignKeyConstraint(["committed_contact_point_id"], ["v2_contact_points.id"], ondelete="SET NULL"),
            sa.UniqueConstraint("run_id", "row_number", name="uq_v2_acquisition_candidate_run_row"),
        )
    _ensure_indexes(
        CANDIDATE_TABLE,
        (
            ("ix_v2_acquisition_candidates_owner_id", ("owner_id",)),
            ("ix_v2_acquisition_candidates_run_id", ("run_id",)),
            ("ix_v2_acquisition_candidate_run_status", ("run_id", "status")),
            (
                "ix_v2_acquisition_candidates_committed_company_id",
                ("committed_company_id",),
            ),
            (
                "ix_v2_acquisition_candidates_committed_contact_id",
                ("committed_contact_id",),
            ),
            (
                "ix_v2_acquisition_candidates_committed_contact_point_id",
                ("committed_contact_point_id",),
            ),
        ),
    )


def downgrade() -> None:
    existing = _tables()
    if CANDIDATE_TABLE in existing:
        op.drop_table(CANDIDATE_TABLE)
    existing = _tables()
    if RUN_TABLE in existing:
        op.drop_table(RUN_TABLE)
