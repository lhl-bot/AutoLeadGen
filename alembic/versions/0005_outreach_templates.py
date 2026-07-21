"""Store immutable outbound message templates on sequence revisions."""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op
from sqlalchemy import Column, Text, inspect


revision: str = "0005_outreach_templates"
down_revision: str | None = "0004_owner_cutover_accounts"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


TABLE = "v2_sequence_steps"
COLUMNS = (
    ("subject_template", Text()),
    ("body_template", Text()),
)


def upgrade() -> None:
    # MySQL DDL is non-transactional. Inspect first so an interrupted release
    # can safely rerun the same migration before Alembic stamps the revision.
    existing = {column["name"] for column in inspect(op.get_bind()).get_columns(TABLE)}
    for name, column_type in COLUMNS:
        if name not in existing:
            op.add_column(TABLE, Column(name, column_type, nullable=True))


def downgrade() -> None:
    existing = {column["name"] for column in inspect(op.get_bind()).get_columns(TABLE)}
    for name, _column_type in reversed(COLUMNS):
        if name in existing:
            op.drop_column(TABLE, name)
