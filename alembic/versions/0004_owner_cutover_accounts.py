"""Add owner cutover state and tenant-scoped V2 channel accounts.

This revision is an immutable, additive schema artifact.  It intentionally
does not import application models or ``Base.metadata``: the DDL and every
alter operation needed by the revision are declared below.  The upgrade is
restartable after non-transactional MySQL DDL interruptions.
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op
from sqlalchemy import Column, DateTime, Integer, inspect


revision: str = "0004_owner_cutover_accounts"
down_revision: str | None = "0003_contact_point_identity_hash"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# Frozen table DDL.  Do not regenerate this from application metadata; future
# model changes require another revision.
FROZEN_NEW_TABLES: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    (
        "v2_owner_migration_states",
        """
CREATE TABLE v2_owner_migration_states (
	owner_id INTEGER NOT NULL,
	current_path VARCHAR(6) NOT NULL,
	version INTEGER NOT NULL,
	switched_at DATETIME NOT NULL,
	switched_by_user_id INTEGER NOT NULL,
	created_at DATETIME NOT NULL,
	updated_at DATETIME NOT NULL,
	PRIMARY KEY (owner_id),
	CONSTRAINT ck_v2_owner_migration_state_path CHECK (current_path IN ('legacy', 'v2')),
	CONSTRAINT ck_v2_owner_migration_state_version CHECK (version >= 1),
	FOREIGN KEY(owner_id) REFERENCES users (id) ON DELETE RESTRICT,
	FOREIGN KEY(switched_by_user_id) REFERENCES users (id) ON DELETE RESTRICT
)
        """.strip(),
        (
            "CREATE INDEX ix_v2_owner_migration_state_path ON v2_owner_migration_states (current_path, updated_at)",
            "CREATE INDEX ix_v2_owner_migration_states_switched_by_user_id ON v2_owner_migration_states (switched_by_user_id)",
        ),
    ),
    (
        "v2_channel_accounts",
        """
CREATE TABLE v2_channel_accounts (
	id INTEGER NOT NULL,
	owner_id INTEGER NOT NULL,
	channel VARCHAR(8) NOT NULL,
	provider VARCHAR(100) NOT NULL,
	provider_account_id VARCHAR(255) NOT NULL,
	legacy_email_account_id INTEGER,
	legacy_channel_account_id INTEGER,
	enabled BOOLEAN NOT NULL,
	health_status VARCHAR(9) NOT NULL,
	health_checked_at DATETIME,
	daily_limit INTEGER,
	timezone VARCHAR(100) NOT NULL,
	last_error VARCHAR(1000),
	created_at DATETIME NOT NULL,
	updated_at DATETIME NOT NULL,
	archived_at DATETIME,
	PRIMARY KEY (id),
	CONSTRAINT uq_v2_channel_account_identity UNIQUE (owner_id, channel, provider, provider_account_id),
	CONSTRAINT uq_v2_channel_account_legacy_email UNIQUE (legacy_email_account_id),
	CONSTRAINT uq_v2_channel_account_legacy_channel UNIQUE (legacy_channel_account_id),
	CONSTRAINT ck_v2_channel_account_channel CHECK (channel IN ('email', 'linkedin', 'whatsapp', 'offline')),
	CONSTRAINT ck_v2_channel_account_health CHECK (health_status IN ('unknown', 'healthy', 'degraded', 'unhealthy')),
	CONSTRAINT ck_v2_channel_account_one_legacy_source CHECK (legacy_email_account_id IS NULL OR legacy_channel_account_id IS NULL),
	CONSTRAINT ck_v2_channel_account_email_source_channel CHECK (legacy_email_account_id IS NULL OR channel = 'email'),
	CONSTRAINT ck_v2_channel_account_omni_source_channel CHECK (legacy_channel_account_id IS NULL OR channel IN ('linkedin', 'whatsapp')),
	CONSTRAINT ck_v2_channel_account_daily_limit_nonnegative CHECK (daily_limit IS NULL OR daily_limit >= 0),
	CONSTRAINT ck_v2_channel_account_outbound_channel CHECK (channel <> 'offline'),
	FOREIGN KEY(owner_id) REFERENCES users (id) ON DELETE RESTRICT,
	FOREIGN KEY(legacy_email_account_id) REFERENCES email_accounts (id) ON DELETE RESTRICT,
	FOREIGN KEY(legacy_channel_account_id) REFERENCES channel_accounts (id) ON DELETE RESTRICT
)
        """.strip(),
        (
            "CREATE INDEX ix_v2_channel_accounts_archived_at ON v2_channel_accounts (archived_at)",
            "CREATE INDEX ix_v2_channel_accounts_owner_id ON v2_channel_accounts (owner_id)",
            "CREATE INDEX ix_v2_channel_accounts_legacy_email_account_id ON v2_channel_accounts (legacy_email_account_id)",
            "CREATE INDEX ix_v2_channel_accounts_legacy_channel_account_id ON v2_channel_accounts (legacy_channel_account_id)",
            "CREATE INDEX ix_v2_channel_account_owner_channel_health ON v2_channel_accounts (owner_id, channel, enabled, health_status, archived_at)",
        ),
    ),
)


ALTERATIONS: tuple[dict[str, object], ...] = (
    {
        "table": "v2_sequence_steps",
        "columns": (("channel_account_id", Integer(), True, "campaign_revision_id"),),
        "indexes": (
            ("ix_v2_sequence_steps_channel_account_id", ("channel_account_id",)),
        ),
        "foreign_keys": (
            (
                "fk_v2_sequence_step_channel_account",
                ("channel_account_id",),
                "v2_channel_accounts",
                ("id",),
                "RESTRICT",
            ),
        ),
        "checks": (),
    },
    {
        "table": "v2_outreach_attempts",
        "columns": (
            ("channel_account_id", Integer(), True, "contact_point_id"),
            ("capacity_reserved_at", DateTime(timezone=True), True, "sent_at"),
        ),
        "indexes": (
            ("ix_v2_outreach_attempts_channel_account_id", ("channel_account_id",)),
            ("ix_v2_outreach_attempts_capacity_reserved_at", ("capacity_reserved_at",)),
            (
                "ix_v2_attempt_account_capacity",
                ("channel_account_id", "capacity_reserved_at"),
            ),
        ),
        "foreign_keys": (
            (
                "fk_v2_attempt_channel_account",
                ("channel_account_id",),
                "v2_channel_accounts",
                ("id",),
                "RESTRICT",
            ),
        ),
        "checks": (),
    },
    {
        "table": "v2_safety_locks",
        "columns": (("channel_account_id", Integer(), True, "contact_id"),),
        "indexes": (
            ("ix_v2_safety_locks_channel_account_id", ("channel_account_id",)),
            (
                "ix_v2_safety_lock_account_active",
                ("channel_account_id", "active"),
            ),
        ),
        "foreign_keys": (
            (
                "fk_v2_safety_lock_channel_account",
                ("channel_account_id",),
                "v2_channel_accounts",
                ("id",),
                "RESTRICT",
            ),
        ),
        "checks": (
            (
                "ck_v2_safety_lock_account_target",
                "(scope = 'account' AND channel_account_id IS NOT NULL) OR "
                "(scope <> 'account' AND channel_account_id IS NULL)",
            ),
        ),
    },
)


def _dialect_sql(statement: str, dialect_name: str) -> str:
    if dialect_name == "sqlite":
        return statement
    if dialect_name != "mysql":
        raise RuntimeError(
            f"{revision} supports only SQLite and MySQL, not {dialect_name!r}"
        )
    return statement.replace(
        "\tid INTEGER NOT NULL,", "\tid INTEGER NOT NULL AUTO_INCREMENT,"
    ).replace("BOOLEAN", "BOOL")


def _index_contract(inspector, table_name: str) -> dict[str, tuple[str, ...]]:
    return {
        item["name"]: tuple(item.get("column_names") or ())
        for item in inspector.get_indexes(table_name)
        if item.get("name")
    }


def _foreign_key_contract(inspector, table_name: str) -> list[tuple[tuple[str, ...], str, tuple[str, ...], str]]:
    return [
        (
            tuple(item.get("constrained_columns") or ()),
            item["referred_table"],
            tuple(item.get("referred_columns") or ()),
            (item.get("options", {}).get("ondelete") or "").upper(),
        )
        for item in inspector.get_foreign_keys(table_name)
    ]


def _check_contract(inspector, table_name: str) -> set[str]:
    return {
        item["name"]
        for item in inspector.get_check_constraints(table_name)
        if item.get("name")
    }


def _create_or_resume_frozen_tables(bind, dialect_name: str) -> None:
    existing_tables = set(inspect(bind).get_table_names())
    for table_name, create_sql, index_statements in FROZEN_NEW_TABLES:
        if table_name not in existing_tables:
            bind.exec_driver_sql(_dialect_sql(create_sql, dialect_name))
            existing_tables.add(table_name)

        index_contract = _index_contract(inspect(bind), table_name)
        for index_sql in index_statements:
            index_name = index_sql.split(" ON ", 1)[0].rsplit(" ", 1)[-1]
            if index_name not in index_contract:
                bind.exec_driver_sql(_dialect_sql(index_sql, dialect_name))
                index_contract = _index_contract(inspect(bind), table_name)


def _validate_named_index(
    index_contract: dict[str, tuple[str, ...]],
    index_name: str,
    columns: tuple[str, ...],
) -> bool:
    existing = index_contract.get(index_name)
    if existing is None:
        return False
    if existing != columns:
        raise RuntimeError(
            f"{index_name} exists with columns {existing}, expected {columns}"
        )
    return True


def _desired_column_order(
    existing: list[str],
    definitions,
) -> list[str]:
    added_names = {item[0] for item in definitions}
    ordered = [name for name in existing if name not in added_names]
    for column_name, _column_type, _nullable, insert_after in definitions:
        if insert_after not in ordered:
            raise RuntimeError(
                f"Cannot position {column_name}: anchor {insert_after} is missing"
            )
        ordered.insert(ordered.index(insert_after) + 1, column_name)
    return ordered


def _alter_mysql(bind, alteration: dict[str, object]) -> None:
    table_name = str(alteration["table"])
    inspector = inspect(bind)
    existing_column_rows = inspector.get_columns(table_name)
    existing_columns = {item["name"] for item in existing_column_rows}
    for column_name, column_type, nullable, insert_after in alteration["columns"]:  # type: ignore[index]
        if column_name not in existing_columns:
            type_sql = "INTEGER" if isinstance(column_type, Integer) else "DATETIME"
            null_sql = "NULL" if nullable else "NOT NULL"
            bind.exec_driver_sql(
                f"ALTER TABLE {table_name} ADD COLUMN {column_name} "
                f"{type_sql} {null_sql} AFTER {insert_after}"
            )
            existing_columns.add(column_name)

    # MySQL supports deterministic column placement directly.  Re-apply only
    # the new-column contracts when resuming a partial ALTER that appended a
    # column or gave it the wrong nullability/type.
    column_rows = inspect(bind).get_columns(table_name)
    column_names = [item["name"] for item in column_rows]
    column_contract = {item["name"]: item for item in column_rows}
    for column_name, column_type, nullable, insert_after in alteration["columns"]:  # type: ignore[index]
        actual_index = column_names.index(column_name)
        type_matches = isinstance(column_contract[column_name]["type"], type(column_type))
        contract_matches = (
            actual_index > 0
            and column_names[actual_index - 1] == insert_after
            and bool(column_contract[column_name]["nullable"]) == bool(nullable)
            and type_matches
        )
        if not contract_matches:
            type_sql = "INTEGER" if isinstance(column_type, Integer) else "DATETIME"
            null_sql = "NULL" if nullable else "NOT NULL"
            bind.exec_driver_sql(
                f"ALTER TABLE {table_name} MODIFY COLUMN {column_name} "
                f"{type_sql} {null_sql} AFTER {insert_after}"
            )
            column_rows = inspect(bind).get_columns(table_name)
            column_names = [item["name"] for item in column_rows]
            column_contract = {item["name"]: item for item in column_rows}

    index_contract = _index_contract(inspect(bind), table_name)
    for index_name, columns in alteration["indexes"]:  # type: ignore[index]
        if not _validate_named_index(index_contract, index_name, columns):
            op.create_index(index_name, table_name, list(columns), unique=False)
            index_contract = _index_contract(inspect(bind), table_name)

    foreign_keys = _foreign_key_contract(inspect(bind), table_name)
    for fk_name, local_columns, remote_table, remote_columns, ondelete in alteration["foreign_keys"]:  # type: ignore[index]
        desired = (local_columns, remote_table, remote_columns, ondelete)
        conflicts = [fk for fk in foreign_keys if fk[0] == local_columns and fk != desired]
        if conflicts:
            raise RuntimeError(
                f"{table_name}.{local_columns!r} has an incompatible foreign key"
            )
        if desired not in foreign_keys:
            op.create_foreign_key(
                fk_name,
                table_name,
                remote_table,
                list(local_columns),
                list(remote_columns),
                ondelete=ondelete,
            )
            foreign_keys = _foreign_key_contract(inspect(bind), table_name)

    check_names = _check_contract(inspect(bind), table_name)
    for check_name, condition in alteration["checks"]:  # type: ignore[index]
        if check_name not in check_names:
            op.create_check_constraint(check_name, table_name, condition)
            check_names = _check_contract(inspect(bind), table_name)


def _alter_sqlite(bind, alteration: dict[str, object]) -> None:
    table_name = str(alteration["table"])
    temp_table = f"_alembic_tmp_{table_name}"
    tables = set(inspect(bind).get_table_names())
    if table_name not in tables:
        if temp_table in tables:
            raise RuntimeError(
                f"{table_name} is missing while {temp_table} exists; refusing "
                "to guess which table contains authoritative data"
            )
        raise RuntimeError(f"{table_name} must exist before applying {revision}")
    if temp_table in tables:
        # The original remains authoritative after a failed batch copy.  Drop
        # only Alembic's known artifact so a corrected retry is deterministic.
        op.drop_table(temp_table)

    inspector = inspect(bind)
    existing_column_rows = inspector.get_columns(table_name)
    existing_column_names = [item["name"] for item in existing_column_rows]
    existing_columns = set(existing_column_names)
    index_contract = _index_contract(inspector, table_name)
    foreign_keys = _foreign_key_contract(inspector, table_name)
    check_names = _check_contract(inspector, table_name)

    missing_columns = [
        item for item in alteration["columns"] if item[0] not in existing_columns  # type: ignore[index]
    ]
    missing_indexes = [
        item
        for item in alteration["indexes"]  # type: ignore[index]
        if not _validate_named_index(index_contract, item[0], item[1])
    ]
    missing_foreign_keys = []
    for item in alteration["foreign_keys"]:  # type: ignore[index]
        _fk_name, local_columns, remote_table, remote_columns, ondelete = item
        desired = (local_columns, remote_table, remote_columns, ondelete)
        conflicts = [fk for fk in foreign_keys if fk[0] == local_columns and fk != desired]
        if conflicts:
            raise RuntimeError(
                f"{table_name}.{local_columns!r} has an incompatible foreign key"
            )
        if desired not in foreign_keys:
            missing_foreign_keys.append(item)

    missing_checks = [
        item for item in alteration["checks"] if item[0] not in check_names  # type: ignore[index]
    ]

    desired_order = _desired_column_order(
        existing_column_names,
        alteration["columns"],  # type: ignore[index]
    )
    order_mismatch = existing_column_names != desired_order

    if not (
        missing_columns
        or missing_indexes
        or missing_foreign_keys
        or missing_checks
        or order_mismatch
    ):
        return

    with op.batch_alter_table(
        table_name,
        recreate="always",
        partial_reordering=[tuple(desired_order)],
    ) as batch:
        for column_name, column_type, nullable, insert_after in missing_columns:
            batch.add_column(Column(column_name, column_type, nullable=nullable))
        for index_name, columns in missing_indexes:
            batch.create_index(index_name, list(columns), unique=False)
        for fk_name, local_columns, remote_table, remote_columns, ondelete in missing_foreign_keys:
            batch.create_foreign_key(
                fk_name,
                remote_table,
                list(local_columns),
                list(remote_columns),
                ondelete=ondelete,
            )
        for check_name, condition in missing_checks:
            batch.create_check_constraint(check_name, condition)


def upgrade() -> None:
    bind = op.get_bind()
    dialect_name = bind.dialect.name
    if dialect_name not in {"sqlite", "mysql"}:
        raise RuntimeError(
            f"{revision} supports only SQLite and MySQL, not {dialect_name!r}"
        )

    _create_or_resume_frozen_tables(bind, dialect_name)
    table_names = set(inspect(bind).get_table_names())
    for alteration in ALTERATIONS:
        table_name = str(alteration["table"])
        if table_name not in table_names:
            raise RuntimeError(f"{table_name} must exist before applying {revision}")
        if dialect_name == "sqlite":
            _alter_sqlite(bind, alteration)
        else:
            _alter_mysql(bind, alteration)


def downgrade() -> None:
    raise RuntimeError(
        "Product V2 owner cutover migrations are non-destructive and cannot be downgraded"
    )
