"""Historical recovery checks for the ContactPoint identity migration.

These tests deliberately rebuild the ContactPoint table as it existed in an
already-stamped 0002 database.  That keeps the fixture independent from the
current Product V2 metadata used by the additive 0002 migration.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError


MIGRATION_0002 = "0002_product_v2_expand"
MIGRATION_0003 = "0003_contact_point_identity_hash"
TABLE_NAME = "v2_contact_points"
BATCH_TABLE_NAME = f"_alembic_tmp_{TABLE_NAME}"
HASH_COLUMN = "normalized_value_hash"
HASH_CONSTRAINT = "uq_v2_contact_point_owner_identity_hash"
LEGACY_CONSTRAINT = "uq_v2_contact_point_owner_value"


HISTORICAL_CONTACT_POINT_DDL = f"""
CREATE TABLE {TABLE_NAME} (
    id INTEGER NOT NULL PRIMARY KEY,
    owner_id INTEGER NOT NULL,
    company_id INTEGER NOT NULL,
    contact_id INTEGER NOT NULL,
    channel VARCHAR(8) NOT NULL,
    value VARCHAR(1000) NOT NULL,
    normalized_value VARCHAR(1000) NOT NULL,
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
    CONSTRAINT {LEGACY_CONSTRAINT}
        UNIQUE (owner_id, channel, normalized_value),
    CONSTRAINT uq_v2_contact_point_legacy_source
        UNIQUE (owner_id, legacy_source_table, legacy_id),
    CONSTRAINT ck_v2_contact_point_channel
        CHECK (channel IN ('email', 'linkedin', 'whatsapp', 'offline')),
    CONSTRAINT ck_v2_contact_point_verification
        CHECK (verification_status IN ('unverified', 'valid', 'invalid', 'catch_all', 'unknown')),
    CONSTRAINT ck_v2_contact_point_availability
        CHECK (availability_status IN ('available', 'restricted', 'unavailable')),
    FOREIGN KEY(owner_id) REFERENCES users (id) ON DELETE RESTRICT,
    FOREIGN KEY(company_id) REFERENCES v2_companies (id) ON DELETE RESTRICT,
    FOREIGN KEY(contact_id) REFERENCES v2_contacts (id) ON DELETE RESTRICT
)
"""


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _migration_config(database_url: str, monkeypatch: pytest.MonkeyPatch) -> Config:
    monkeypatch.setenv("DATABASE_URL", database_url)
    monkeypatch.setenv("AUTOLEADGEN_ENV", "test")
    return Config(str(Path(__file__).parents[1] / "alembic.ini"))


def _historical_0002_database(tmp_path, monkeypatch):
    database_path = tmp_path / "historical-0002.db"
    database_url = f"sqlite+pysqlite:///{database_path}"
    config = _migration_config(database_url, monkeypatch)

    # GIVEN: A complete database stamped at 0002, but with the historical
    # ContactPoint table that predates normalized_value_hash.
    command.upgrade(config, MIGRATION_0002)
    engine = create_engine(database_url)
    with engine.begin() as connection:
        connection.execute(text(f"DROP TABLE {TABLE_NAME}"))
        connection.execute(text(HISTORICAL_CONTACT_POINT_DDL))
        connection.execute(
            text(
                f"CREATE INDEX ix_v2_contact_point_owner_id "
                f"ON {TABLE_NAME} (owner_id)"
            )
        )
        connection.execute(
            text(
                f"CREATE INDEX ix_v2_contact_point_contact_channel "
                f"ON {TABLE_NAME} (contact_id, channel)"
            )
        )

    assert HASH_COLUMN not in {
        column["name"] for column in inspect(engine).get_columns(TABLE_NAME)
    }
    with engine.connect() as connection:
        assert connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == (
            MIGRATION_0002
        )
    return config, engine


def _seed_contact_points(engine, normalized_values: tuple[str, ...]) -> None:
    now = "2026-07-16 08:00:00"
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO users "
                "(id, username, hashed_password, is_admin, is_active, created_at) "
                "VALUES (9001, 'historical-migration-owner', 'x', 0, 1, :now)"
            ),
            {"now": now},
        )
        connection.execute(
            text(
                "INSERT INTO v2_companies "
                "(id, owner_id, name, normalized_domain, created_at, updated_at) "
                "VALUES (9101, 9001, 'Historical Co', 'historical.example', :now, :now)"
            ),
            {"now": now},
        )
        for index, normalized_value in enumerate(normalized_values, start=1):
            contact_id = 9200 + index
            contact_point_id = 9300 + index
            connection.execute(
                text(
                    "INSERT INTO v2_contacts "
                    "(id, owner_id, company_id, full_name, created_at, updated_at) "
                    "VALUES (:id, 9001, 9101, :name, :now, :now)"
                ),
                {"id": contact_id, "name": f"Historical Buyer {index}", "now": now},
            )
            connection.execute(
                text(
                    f"INSERT INTO {TABLE_NAME} "
                    "(id, owner_id, company_id, contact_id, channel, value, "
                    "normalized_value, verification_status, availability_status, "
                    "is_primary, created_at, updated_at, legacy_source_table, legacy_id) "
                    "VALUES (:id, 9001, 9101, :contact_id, 'email', :value, :normalized, "
                    "'valid', 'available', :is_primary, :now, :now, 'lead', :legacy_id)"
                ),
                {
                    "id": contact_point_id,
                    "contact_id": contact_id,
                    "value": normalized_value,
                    "normalized": normalized_value,
                    "is_primary": index == 1,
                    "now": now,
                    "legacy_id": str(index),
                },
            )


def _unique_constraint_names(engine) -> set[str]:
    return {
        constraint["name"]
        for constraint in inspect(engine).get_unique_constraints(TABLE_NAME)
        if constraint.get("name")
    }


def test_historical_0002_contact_points_upgrade_to_digest_identity(tmp_path, monkeypatch):
    config, engine = _historical_0002_database(tmp_path, monkeypatch)
    identities = ("buyer@example.com", "采购@example.cn")
    _seed_contact_points(engine, identities)

    # WHEN: The historical, already-stamped database is upgraded to 0003.
    command.upgrade(config, MIGRATION_0003)

    # THEN: Every identity is deterministically backfilled, source data is
    # preserved, and the legacy variable-width unique key is replaced.
    columns = {
        column["name"]: column for column in inspect(engine).get_columns(TABLE_NAME)
    }
    assert columns[HASH_COLUMN]["nullable"] is False
    assert HASH_CONSTRAINT in _unique_constraint_names(engine)
    assert LEGACY_CONSTRAINT not in _unique_constraint_names(engine)
    with engine.connect() as connection:
        rows = connection.execute(
            text(
                f"SELECT id, normalized_value, {HASH_COLUMN}, legacy_id "
                f"FROM {TABLE_NAME} ORDER BY id"
            )
        ).mappings().all()
        assert connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == (
            MIGRATION_0003
        )
    assert [row["normalized_value"] for row in rows] == list(identities)
    assert [row[HASH_COLUMN] for row in rows] == [_digest(value) for value in identities]
    assert [row["legacy_id"] for row in rows] == ["1", "2"]

    # AND: The new database constraint rejects another contact point with the
    # same owner/channel identity even if its display/source fields differ.
    with pytest.raises(IntegrityError):
        with engine.begin() as connection:
            connection.execute(
                text(
                    f"INSERT INTO {TABLE_NAME} "
                    "(id, owner_id, company_id, contact_id, channel, value, "
                    f"normalized_value, {HASH_COLUMN}, verification_status, "
                    "availability_status, is_primary, created_at, updated_at) "
                    f"SELECT 9399, owner_id, company_id, contact_id, channel, "
                    "'alias@example.com', 'alias@example.com', "
                    f"{HASH_COLUMN}, verification_status, availability_status, "
                    "0, created_at, updated_at "
                    f"FROM {TABLE_NAME} WHERE id = 9301"
                )
            )


def test_partially_backfilled_0003_migration_resumes_without_rewriting_completed_rows(
    tmp_path, monkeypatch
):
    config, engine = _historical_0002_database(tmp_path, monkeypatch)
    identities = ("completed@example.com", "pending@example.com")
    _seed_contact_points(engine, identities)
    completed_digest = _digest(identities[0])
    with engine.begin() as connection:
        connection.execute(
            text(f"ALTER TABLE {TABLE_NAME} ADD COLUMN {HASH_COLUMN} VARCHAR(64)")
        )
        connection.execute(
            text(
                f"UPDATE {TABLE_NAME} SET {HASH_COLUMN} = :digest WHERE id = 9301"
            ),
            {"digest": completed_digest},
        )

    # WHEN: A run interrupted after adding the column and backfilling one row
    # is resumed from its unchanged 0002 Alembic revision.
    command.upgrade(config, MIGRATION_0003)

    # THEN: The completed digest is retained, the remaining row is filled,
    # and the final non-null/unique schema is installed exactly once.
    with engine.connect() as connection:
        rows = connection.execute(
            text(
                f"SELECT id, {HASH_COLUMN} FROM {TABLE_NAME} ORDER BY id"
            )
        ).mappings().all()
    assert [row[HASH_COLUMN] for row in rows] == [
        completed_digest,
        _digest(identities[1]),
    ]
    columns = {
        column["name"]: column for column in inspect(engine).get_columns(TABLE_NAME)
    }
    assert columns[HASH_COLUMN]["nullable"] is False
    assert HASH_CONSTRAINT in _unique_constraint_names(engine)

    command.upgrade(config, MIGRATION_0003)
    with engine.connect() as connection:
        assert connection.execute(text("SELECT COUNT(*) FROM v2_contact_points")).scalar_one() == 2


def test_conflicting_partial_hashes_fail_closed_and_can_be_repaired_then_retried(
    tmp_path, monkeypatch
):
    config, engine = _historical_0002_database(tmp_path, monkeypatch)
    identities = ("first@example.com", "second@example.com")
    _seed_contact_points(engine, identities)
    conflicting_digest = _digest(identities[0])
    with engine.begin() as connection:
        connection.execute(
            text(f"ALTER TABLE {TABLE_NAME} ADD COLUMN {HASH_COLUMN} VARCHAR(64)")
        )
        connection.execute(
            text(f"UPDATE {TABLE_NAME} SET {HASH_COLUMN} = :digest"),
            {"digest": conflicting_digest},
        )

    # WHEN: Two different historical values contain the same pre-existing
    # digest, as could happen after a corrupt/interrupted manual recovery.
    with pytest.raises(IntegrityError):
        command.upgrade(config, MIGRATION_0003)

    # THEN: The migration fails closed: it neither stamps 0003 nor drops a
    # source row, and it does not expose a schema falsely claiming uniqueness.
    with engine.connect() as connection:
        assert connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == (
            MIGRATION_0002
        )
        assert connection.execute(text(f"SELECT COUNT(*) FROM {TABLE_NAME}")).scalar_one() == 2
    assert HASH_CONSTRAINT not in _unique_constraint_names(engine)

    # WHEN: The quarantined conflict is corrected and the same upgrade is
    # retried, without recreating or restamping the database by hand.
    with engine.begin() as connection:
        connection.execute(
            text(
                f"UPDATE {TABLE_NAME} SET {HASH_COLUMN} = :digest WHERE id = 9302"
            ),
            {"digest": _digest(identities[1])},
        )
    command.upgrade(config, MIGRATION_0003)

    # THEN: The retry reaches 0003 with both rows intact and protected by the
    # final fixed-width identity constraint.
    with engine.connect() as connection:
        assert connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == (
            MIGRATION_0003
        )
        assert connection.execute(text(f"SELECT COUNT(*) FROM {TABLE_NAME}")).scalar_one() == 2
    assert HASH_CONSTRAINT in _unique_constraint_names(engine)
    assert BATCH_TABLE_NAME not in inspect(engine).get_table_names()


def test_temp_only_interrupted_state_is_rejected_without_destroying_recovery_data(
    tmp_path, monkeypatch
):
    config, engine = _historical_0002_database(tmp_path, monkeypatch)
    _seed_contact_points(engine, ("authoritative@example.com",))
    with engine.begin() as connection:
        connection.execute(
            text(f"ALTER TABLE {TABLE_NAME} RENAME TO {BATCH_TABLE_NAME}")
        )

    # GIVEN: An ambiguous interrupted SQLite batch state where the original
    # table is missing and only Alembic's temporary table remains.
    assert TABLE_NAME not in inspect(engine).get_table_names()
    assert BATCH_TABLE_NAME in inspect(engine).get_table_names()

    # WHEN/THEN: 0003 refuses to guess that the temporary copy is authoritative.
    with pytest.raises(RuntimeError, match="refusing to guess"):
        command.upgrade(config, MIGRATION_0003)

    # AND: The recovery data and 0002 stamp are left untouched for an explicit
    # operator decision instead of being silently renamed or deleted.
    with engine.connect() as connection:
        assert connection.execute(
            text(f"SELECT COUNT(*) FROM {BATCH_TABLE_NAME}")
        ).scalar_one() == 1
        assert connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == (
            MIGRATION_0002
        )
