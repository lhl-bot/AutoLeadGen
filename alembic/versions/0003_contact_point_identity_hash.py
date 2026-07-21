"""Repair durable ContactPoint identity indexing with a fixed-width digest.

Early isolated ``0002`` databases indexed the full normalized contact value.
The Product V2 model later moved that identity key to a SHA-256 digest so the
constraint is valid under MySQL utf8mb4 key-length limits.  This explicit
revision upgrades those already-stamped databases while remaining a no-op for
fresh ``0002`` databases that were created from the newer metadata.
"""
from __future__ import annotations

from collections.abc import Sequence
import hashlib

from alembic import op
from sqlalchemy import Column, String, inspect, text


revision: str = "0003_contact_point_identity_hash"
down_revision: str | None = "0002_product_v2_expand"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE_NAME = "v2_contact_points"
BATCH_TABLE_NAME = f"_alembic_tmp_{TABLE_NAME}"
HASH_COLUMN = "normalized_value_hash"
HASH_CONSTRAINT = "uq_v2_contact_point_owner_identity_hash"
LEGACY_IDENTITY_CONSTRAINTS = {
    "uq_v2_contact_point_owner_value",
    "uq_v2_contact_point_owner_identity",
}


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    table_names = set(inspector.get_table_names())
    if TABLE_NAME not in table_names:
        if bind.dialect.name == "sqlite" and BATCH_TABLE_NAME in table_names:
            raise RuntimeError(
                f"{TABLE_NAME} is missing while {BATCH_TABLE_NAME} exists; "
                "refusing to guess which table contains the authoritative data"
            )
        raise RuntimeError(f"{TABLE_NAME} must exist before applying {revision}")

    # SQLite's non-transactional batch alter can leave its fixed temporary
    # table behind when copying rows into a new unique constraint fails.  If
    # the original table still exists it remains authoritative, so removing
    # only that known batch artifact makes a corrected migration retryable.
    # A temp-only state is deliberately rejected above instead of guessed at.
    if bind.dialect.name == "sqlite" and BATCH_TABLE_NAME in table_names:
        op.drop_table(BATCH_TABLE_NAME)
        inspector = inspect(bind)

    columns = {column["name"]: column for column in inspector.get_columns(TABLE_NAME)}
    if HASH_COLUMN not in columns:
        with op.batch_alter_table(TABLE_NAME) as batch:
            batch.add_column(Column(HASH_COLUMN, String(64), nullable=True))

    rows = bind.execute(
        text(f"SELECT id, normalized_value FROM {TABLE_NAME} WHERE {HASH_COLUMN} IS NULL")
    ).mappings()
    for row in rows:
        bind.execute(
            text(
                f"UPDATE {TABLE_NAME} SET {HASH_COLUMN} = :digest "
                "WHERE id = :contact_point_id"
            ),
            {
                "digest": _digest(str(row["normalized_value"])),
                "contact_point_id": row["id"],
            },
        )

    inspector = inspect(bind)
    columns = {column["name"]: column for column in inspector.get_columns(TABLE_NAME)}
    unique_names = {
        constraint["name"]
        for constraint in inspector.get_unique_constraints(TABLE_NAME)
        if constraint.get("name")
    }
    legacy_names = sorted(unique_names & LEGACY_IDENTITY_CONSTRAINTS)
    needs_not_null = bool(columns[HASH_COLUMN].get("nullable", True))
    needs_hash_constraint = HASH_CONSTRAINT not in unique_names

    if legacy_names or needs_not_null or needs_hash_constraint:
        with op.batch_alter_table(TABLE_NAME) as batch:
            for name in legacy_names:
                batch.drop_constraint(name, type_="unique")
            if needs_not_null:
                batch.alter_column(
                    HASH_COLUMN,
                    existing_type=String(64),
                    nullable=False,
                )
            if needs_hash_constraint:
                batch.create_unique_constraint(
                    HASH_CONSTRAINT,
                    ["owner_id", "channel", HASH_COLUMN],
                )


def downgrade() -> None:
    raise RuntimeError("Product V2 safety migrations are non-destructive and cannot be downgraded")
