import ast
from pathlib import Path
import re

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
import pytest
from sqlalchemy import CheckConstraint, UniqueConstraint, create_engine, inspect, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.dialects import sqlite

from database import Base
import models as legacy_models  # noqa: F401 - registers the frozen v16 comparison schema
from product_v2 import models
from product_v2.models import V2_TABLE_NAMES


PROJECT_ROOT = Path(__file__).parents[1]
MIGRATION_FILES = (
    PROJECT_ROOT / "alembic" / "versions" / "0001_legacy_v16_baseline.py",
    PROJECT_ROOT / "alembic" / "versions" / "0002_product_v2_expand.py",
    PROJECT_ROOT / "alembic" / "versions" / "0003_contact_point_identity_hash.py",
    PROJECT_ROOT / "alembic" / "versions" / "0004_owner_cutover_accounts.py",
    PROJECT_ROOT / "alembic" / "versions" / "0005_outreach_templates.py",
    PROJECT_ROOT / "alembic" / "versions" / "0006_message_event_complaint.py",
    PROJECT_ROOT / "alembic" / "versions" / "0007_acquisition_activation.py",
    PROJECT_ROOT / "alembic" / "versions" / "0008_go_live_batches_and_routes.py",
)


def _normalized_type(column_type) -> str:
    return re.sub(r"\s+", " ", column_type.compile(dialect=sqlite.dialect()).upper()).strip()


def _normalized_check(sqltext: str, table_name: str) -> str:
    normalized = sqltext.lower()
    for token in ('`', '"', '[', ']'):
        normalized = normalized.replace(token, "")
    normalized = normalized.replace(f"{table_name.lower()}.", "")
    return re.sub(r"\s+", "", normalized)


def _metadata_fingerprint(table) -> dict[str, object]:
    return {
        "columns": tuple(
            (
                column.name,
                _normalized_type(column.type),
                bool(column.nullable),
                bool(column.primary_key),
            )
            for column in table.columns
        ),
        "primary_key": tuple(column.name for column in table.primary_key.columns),
        "foreign_keys": {
            (
                tuple(element.parent.name for element in constraint.elements),
                constraint.elements[0].column.table.name,
                tuple(element.column.name for element in constraint.elements),
                (constraint.ondelete or "").upper(),
            )
            for constraint in table.foreign_key_constraints
        },
        "unique_constraints": {
            (constraint.name, tuple(column.name for column in constraint.columns))
            for constraint in table.constraints
            if isinstance(constraint, UniqueConstraint)
        },
        "indexes": {
            (index.name, tuple(column.name for column in index.columns), bool(index.unique))
            for index in table.indexes
        },
        "checks": {
            (
                constraint.name,
                _normalized_check(
                    str(
                        constraint.sqltext.compile(
                            dialect=sqlite.dialect(),
                            compile_kwargs={"literal_binds": True},
                        )
                    ),
                    table.name,
                ),
            )
            for constraint in table.constraints
            if isinstance(constraint, CheckConstraint)
        },
    }


def _database_fingerprint(inspector, table_name: str) -> dict[str, object]:
    columns = inspector.get_columns(table_name)
    return {
        "columns": tuple(
            (
                column["name"],
                _normalized_type(column["type"]),
                bool(column["nullable"]),
                bool(column.get("primary_key")),
            )
            for column in columns
        ),
        "primary_key": tuple(
            inspector.get_pk_constraint(table_name).get("constrained_columns") or ()
        ),
        "foreign_keys": {
            (
                tuple(constraint.get("constrained_columns") or ()),
                constraint["referred_table"],
                tuple(constraint.get("referred_columns") or ()),
                (constraint.get("options", {}).get("ondelete") or "").upper(),
            )
            for constraint in inspector.get_foreign_keys(table_name)
        },
        "unique_constraints": {
            (constraint.get("name"), tuple(constraint.get("column_names") or ()))
            for constraint in inspector.get_unique_constraints(table_name)
        },
        "indexes": {
            (
                index.get("name"),
                tuple(index.get("column_names") or ()),
                bool(index.get("unique")),
            )
            for index in inspector.get_indexes(table_name)
        },
        "checks": {
            (
                constraint.get("name"),
                _normalized_check(constraint["sqltext"], table_name),
            )
            for constraint in inspector.get_check_constraints(table_name)
        },
    }


def test_frozen_revisions_do_not_import_runtime_metadata():
    # GIVEN: Historical migrations must remain reproducible after ORM models evolve.
    for migration_path in MIGRATION_FILES:
        tree = ast.parse(migration_path.read_text(encoding="utf-8"))

        # THEN: Neither revision imports application model modules nor reads
        # Base.metadata. Their full DDL contract is stored in the revision itself.
        imported_modules = {
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        }
        imported_from = {
            node.module
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module
        }
        assert "models" not in imported_modules
        assert "database" not in imported_from
        assert "product_v2.models" not in imported_from
        assert not any(
            isinstance(node, ast.Attribute)
            and node.attr == "metadata"
            and isinstance(node.value, ast.Name)
            and node.value.id == "Base"
            for node in ast.walk(tree)
        )


def test_single_migration_head_fits_mysql_alembic_version_column():
    # GIVEN: MySQL's established alembic_version.version_num is VARCHAR(32).
    config = Config(str(PROJECT_ROOT / "alembic.ini"))
    script = ScriptDirectory.from_config(config)

    # THEN: This work adds exactly one linear head and its identifier is
    # persistable before any non-transactional schema DDL begins.
    heads = script.get_heads()
    assert heads == ["0008_go_live_batches_and_routes"]
    assert len(heads[0]) <= 32


def test_migrated_head_schema_matches_current_metadata_fingerprint(tmp_path, monkeypatch):
    # GIVEN: A completely empty isolated database upgraded only by Alembic.
    database_path = tmp_path / "product-v2-schema-fingerprint.db"
    database_url = f"sqlite+pysqlite:///{database_path}"
    monkeypatch.setenv("DATABASE_URL", database_url)
    monkeypatch.setenv("AUTOLEADGEN_ENV", "test")
    config = Config(str(PROJECT_ROOT / "alembic.ini"))
    command.upgrade(config, "head")

    # WHEN: The resulting schema is reduced to its durable structural contract.
    inspector = inspect(create_engine(database_url))
    actual_tables = set(inspector.get_table_names()) - {"alembic_version"}
    expected_tables = set(Base.metadata.tables)

    # THEN: Tables and every column/PK/FK/unique/index/CHECK definition match
    # today's runtime metadata. Any future model change must add a new revision.
    assert actual_tables == expected_tables
    for table_name in sorted(expected_tables):
        assert _database_fingerprint(inspector, table_name) == _metadata_fingerprint(
            Base.metadata.tables[table_name]
        ), table_name


def test_product_v2_check_constraint_names_are_mysql_safe_and_schema_unique():
    # GIVEN: Product V2's generated and hand-written CHECK constraints.
    constraints = [
        (table.name, constraint.name)
        for table in models.Base.metadata.sorted_tables
        if table.name in V2_TABLE_NAMES
        for constraint in table.constraints
        if isinstance(constraint, CheckConstraint)
    ]

    # THEN: Every name is explicit, within MySQL's identifier limit, and
    # unique across the whole schema (MySQL's scope for CHECK names).
    assert constraints
    assert all(name is not None for _, name in constraints)
    names = [name for _, name in constraints]
    assert len(names) == len(set(names))
    assert all(len(name) <= 64 for name in names)


def test_contact_point_identity_unique_key_uses_fixed_width_digest():
    # GIVEN: Contact point identifiers may retain up to 1000 UTF-8 characters.
    table = models.ContactPoint.__table__
    identity_constraint = next(
        constraint
        for constraint in table.constraints
        if isinstance(constraint, UniqueConstraint)
        and constraint.name == "uq_v2_contact_point_owner_identity_hash"
    )

    # THEN: The MySQL unique key uses a fixed-width digest instead of indexing
    # the potentially 4000-byte utf8mb4 normalized value directly.
    assert [column.name for column in identity_constraint.columns] == [
        "owner_id",
        "channel",
        "normalized_value_hash",
    ]
    assert table.c.normalized_value.type.length == 1000
    assert table.c.normalized_value_hash.type.length == 64


def test_acquisition_candidate_does_not_index_unbounded_email_text():
    # GIVEN: Acquisition candidates preserve an up-to-1000-character source value.
    table = models.AcquisitionCandidate.__table__

    # THEN: MySQL is never asked to build a potentially 4000-byte utf8mb4 key.
    assert table.c.normalized_email.type.length == 1000
    assert all(
        "normalized_email" not in [column.name for column in index.columns]
        for index in table.indexes
    )


def test_empty_database_upgrades_to_product_v2_and_is_repeatable(tmp_path, monkeypatch):
    # GIVEN: An empty isolated database.
    database_path = tmp_path / "product-v2.db"
    database_url = f"sqlite+pysqlite:///{database_path}"
    monkeypatch.setenv("DATABASE_URL", database_url)
    monkeypatch.setenv("AUTOLEADGEN_ENV", "test")
    config = Config(str(Path(__file__).parents[1] / "alembic.ini"))

    # WHEN: Running the complete migration chain twice.
    command.upgrade(config, "head")
    first_tables = set(inspect(create_engine(database_url)).get_table_names())
    command.upgrade(config, "head")
    engine = create_engine(database_url)
    second_tables = set(inspect(engine).get_table_names())

    # THEN: All V2 tables exist and the schema/revision remains stable.
    assert set(V2_TABLE_NAMES).issubset(first_tables)
    assert first_tables == second_tables
    with engine.connect() as connection:
        assert connection.execute(text("SELECT version_num FROM alembic_version")).scalar() == (
            ScriptDirectory.from_config(config).get_current_head()
        )
        contact_point_columns = {
            column["name"]: column
            for column in inspect(engine).get_columns("v2_contact_points")
        }
        assert contact_point_columns["normalized_value_hash"]["nullable"] is False


def test_expand_migration_recovers_from_a_partially_created_v2_schema(tmp_path, monkeypatch):
    # GIVEN: A legacy baseline where one V2 table was created before an interrupted expand.
    database_path = tmp_path / "interrupted-product-v2.db"
    database_url = f"sqlite+pysqlite:///{database_path}"
    monkeypatch.setenv("DATABASE_URL", database_url)
    monkeypatch.setenv("AUTOLEADGEN_ENV", "test")
    config = Config(str(Path(__file__).parents[1] / "alembic.ini"))
    command.upgrade(config, "0001_legacy_v16_baseline")
    engine = create_engine(database_url)
    models.Company.__table__.create(bind=engine, checkfirst=True)
    with engine.begin() as connection:
        connection.exec_driver_sql("DROP INDEX ix_v2_company_owner_archived")

    # WHEN: The additive expand migration is resumed.
    command.upgrade(config, "head")

    # THEN: checkfirst completes every missing table and stamps the one stable head.
    tables = set(inspect(engine).get_table_names())
    assert set(V2_TABLE_NAMES).issubset(tables)
    company_indexes = {
        index["name"] for index in inspect(engine).get_indexes("v2_companies")
    }
    assert "ix_v2_company_owner_archived" in company_indexes
    with engine.connect() as connection:
        assert connection.execute(text("SELECT version_num FROM alembic_version")).scalar() == (
            ScriptDirectory.from_config(config).get_current_head()
        )


def test_acquisition_migration_repairs_indexes_after_interrupted_mysql_ddl(
    tmp_path,
    monkeypatch,
):
    # GIVEN: Table creation committed, but MySQL stopped before later indexes.
    database_path = tmp_path / "interrupted-acquisition.db"
    database_url = f"sqlite+pysqlite:///{database_path}"
    monkeypatch.setenv("DATABASE_URL", database_url)
    monkeypatch.setenv("AUTOLEADGEN_ENV", "test")
    config = Config(str(PROJECT_ROOT / "alembic.ini"))
    command.upgrade(config, "0006_message_event_complaint")
    engine = create_engine(database_url)
    models.AcquisitionRun.__table__.create(bind=engine)
    models.AcquisitionCandidate.__table__.create(bind=engine)
    with engine.begin() as connection:
        connection.exec_driver_sql("DROP INDEX ix_v2_acquisition_run_owner_status")
        connection.exec_driver_sql("DROP INDEX ix_v2_acquisition_candidate_run_status")

    # WHEN: The same additive revision resumes.
    command.upgrade(config, "head")

    # THEN: Every post-table index is repaired before the revision is stamped.
    inspector = inspect(engine)
    assert "ix_v2_acquisition_run_owner_status" in {
        item["name"] for item in inspector.get_indexes("v2_acquisition_runs")
    }
    assert "ix_v2_acquisition_candidate_run_status" in {
        item["name"] for item in inspector.get_indexes("v2_acquisition_candidates")
    }


def test_owner_cutover_migration_resumes_partial_alters_with_exact_contract(
    tmp_path,
    monkeypatch,
):
    # GIVEN: A 0003 database where MySQL-style non-transactional progress left
    # one new nullable column appended, but no FK or index was created.
    database_path = tmp_path / "interrupted-owner-cutover.db"
    database_url = f"sqlite+pysqlite:///{database_path}"
    monkeypatch.setenv("DATABASE_URL", database_url)
    monkeypatch.setenv("AUTOLEADGEN_ENV", "test")
    config = Config(str(PROJECT_ROOT / "alembic.ini"))
    command.upgrade(config, "0003_contact_point_identity_hash")
    engine = create_engine(database_url)
    with engine.begin() as connection:
        connection.exec_driver_sql(
            "ALTER TABLE v2_sequence_steps ADD COLUMN channel_account_id INTEGER"
        )

    # WHEN: The one additive 0004 is resumed and then run again.
    command.upgrade(config, "head")
    command.upgrade(config, "head")

    # THEN: It restores deterministic column placement plus the complete
    # account FK/index/check contract without creating another migration head.
    inspector = inspect(engine)
    sequence_columns = [
        item["name"] for item in inspector.get_columns("v2_sequence_steps")
    ]
    assert sequence_columns.index("channel_account_id") == (
        sequence_columns.index("campaign_revision_id") + 1
    )
    assert any(
        tuple(item["constrained_columns"]) == ("channel_account_id",)
        and item["referred_table"] == "v2_channel_accounts"
        and (item.get("options", {}).get("ondelete") or "").upper() == "RESTRICT"
        for item in inspector.get_foreign_keys("v2_sequence_steps")
    )
    assert "ix_v2_sequence_steps_channel_account_id" in {
        item["name"] for item in inspector.get_indexes("v2_sequence_steps")
    }
    assert "ix_v2_attempt_account_capacity" in {
        item["name"] for item in inspector.get_indexes("v2_outreach_attempts")
    }
    assert "ix_v2_safety_lock_account_active" in {
        item["name"] for item in inspector.get_indexes("v2_safety_locks")
    }
    assert "ck_v2_safety_lock_account_target" in {
        item["name"] for item in inspector.get_check_constraints("v2_safety_locks")
    }
    with engine.connect() as connection:
        assert connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == (
            "0008_go_live_batches_and_routes"
        )


def test_owner_cutover_constraints_fail_closed_on_invalid_account_targets(
    tmp_path,
    monkeypatch,
):
    # GIVEN: A fully migrated isolated database and one owner.
    database_path = tmp_path / "owner-cutover-constraints.db"
    database_url = f"sqlite+pysqlite:///{database_path}"
    monkeypatch.setenv("DATABASE_URL", database_url)
    monkeypatch.setenv("AUTOLEADGEN_ENV", "test")
    config = Config(str(PROJECT_ROOT / "alembic.ini"))
    command.upgrade(config, "head")
    engine = create_engine(database_url)
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO users "
                "(id, username, hashed_password, is_admin, is_active, created_at) "
                "VALUES (8801, 'owner-cutover-constraints', 'x', 0, 1, "
                "'2026-07-16 12:00:00')"
            )
        )

    # WHEN/THEN: Offline cannot become an outbound sender identity, and an
    # account-scoped hard lock must always identify the exact sender account.
    with pytest.raises(IntegrityError):
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO v2_channel_accounts "
                    "(owner_id, channel, provider, provider_account_id, enabled, "
                    "health_status, timezone, created_at, updated_at) VALUES "
                    "(8801, 'offline', 'fake-offline', 'offline-1', 1, 'healthy', "
                    "'UTC', '2026-07-16 12:00:00', '2026-07-16 12:00:00')"
                )
            )
    with pytest.raises(IntegrityError):
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO v2_safety_locks "
                    "(owner_id, scope, code, reason, active, locked_at, created_at, updated_at) "
                    "VALUES (8801, 'account', 'missing-account', 'invalid target', 1, "
                    "'2026-07-16 12:00:00', '2026-07-16 12:00:00', "
                    "'2026-07-16 12:00:00')"
                )
            )
