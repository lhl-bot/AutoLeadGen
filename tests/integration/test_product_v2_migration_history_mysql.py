"""MySQL 8 historical recovery coverage for ContactPoint identity hashing."""

from __future__ import annotations

import hashlib
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import URL, make_url
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

import models as legacy
from product_v2 import models


pytestmark = pytest.mark.mysql

MIGRATION_0002 = "0002_product_v2_expand"
TABLE_NAME = "v2_contact_points"
HASH_COLUMN = "normalized_value_hash"
HASH_CONSTRAINT = "uq_v2_contact_point_owner_identity_hash"
SAFE_DATABASE_NAME = re.compile(r"^[A-Za-z0-9_]+$")


def _configured_mysql_test_url() -> URL:
    raw_url = os.environ.get("PRODUCT_V2_MYSQL_TEST_URL")
    if not raw_url:
        pytest.skip("PRODUCT_V2_MYSQL_TEST_URL is not configured")
    url = make_url(raw_url)
    if not url.drivername.startswith("mysql"):
        pytest.fail("PRODUCT_V2_MYSQL_TEST_URL must use MySQL")
    if not (url.database or "").endswith("_test"):
        pytest.fail("Refusing to test against a database whose name does not end in _test")
    if not SAFE_DATABASE_NAME.fullmatch(url.database or ""):
        pytest.fail("MySQL test database name contains unsafe identifier characters")
    return url


def _temporary_database_name(configured_name: str) -> str:
    suffix = f"_history_{uuid4().hex[:10]}_test"
    stem = configured_name[: 64 - len(suffix)]
    name = f"{stem}{suffix}"
    assert name.endswith("_test")
    assert len(name) <= 64
    assert SAFE_DATABASE_NAME.fullmatch(name)
    return name


def _url_string(url: URL) -> str:
    return url.render_as_string(hide_password=False)


def _alembic_config() -> Config:
    return Config(str(Path(__file__).parents[2] / "alembic.ini"))


def _set_migration_database(monkeypatch: pytest.MonkeyPatch, url: URL) -> None:
    assert (url.database or "").endswith("_test")
    monkeypatch.setenv("DATABASE_URL", _url_string(url))
    monkeypatch.setenv("AUTOLEADGEN_ENV", "test")


def _database_ddl(admin_engine, statement: str) -> None:
    with admin_engine.connect() as connection:
        connection = connection.execution_options(isolation_level="AUTOCOMMIT")
        connection.exec_driver_sql(statement)


def _drop_hash_identity_from_0002(engine) -> None:
    inspector = inspect(engine)
    hash_indexes = {
        item["name"]
        for item in (
            inspector.get_unique_constraints(TABLE_NAME)
            + inspector.get_indexes(TABLE_NAME)
        )
        if item.get("name") and HASH_COLUMN in (item.get("column_names") or [])
    }
    with engine.begin() as connection:
        for index_name in sorted(hash_indexes):
            if not SAFE_DATABASE_NAME.fullmatch(index_name):
                raise AssertionError(f"unsafe reflected index name: {index_name!r}")
            connection.exec_driver_sql(
                f"ALTER TABLE `{TABLE_NAME}` DROP INDEX `{index_name}`"
            )
        if HASH_COLUMN in {
            column["name"] for column in inspect(connection).get_columns(TABLE_NAME)
        }:
            connection.exec_driver_sql(
                f"ALTER TABLE `{TABLE_NAME}` DROP COLUMN `{HASH_COLUMN}`"
            )


def _seed_historical_unicode_identity(engine) -> tuple[int, str]:
    Session = sessionmaker(bind=engine, autoflush=False)
    db = Session()
    try:
        suffix = uuid4().hex[:10]
        user = legacy.User(
            username=f"mysql-history-{suffix}",
            hashed_password="x",
            is_active=True,
        )
        db.add(user)
        db.flush()
        company = models.Company(
            owner_id=user.id,
            name="历史迁移公司",
            normalized_domain=f"history-{suffix}.example",
        )
        db.add(company)
        db.flush()
        contact = models.Contact(
            owner_id=user.id,
            company_id=company.id,
            full_name="采购负责人",
            timezone="Asia/Shanghai",
        )
        db.add(contact)
        db.flush()

        normalized_value = "采购@example.cn"
        now = datetime.now(timezone.utc).replace(microsecond=0)
        result = db.execute(
            text(
                f"INSERT INTO {TABLE_NAME} "
                "(owner_id, company_id, contact_id, channel, value, normalized_value, "
                "verification_status, availability_status, is_primary, created_at, updated_at, "
                "legacy_source_table, legacy_id) "
                "VALUES (:owner_id, :company_id, :contact_id, 'email', :value, :normalized_value, "
                "'valid', 'available', 1, :now, :now, 'lead', 'unicode-history-1')"
            ),
            {
                "owner_id": user.id,
                "company_id": company.id,
                "contact_id": contact.id,
                "value": normalized_value,
                "normalized_value": normalized_value,
                "now": now,
            },
        )
        db.commit()
        return int(result.lastrowid), normalized_value
    finally:
        db.close()


def test_mysql_historical_0002_contact_point_upgrades_to_digest_identity(
    monkeypatch,
):
    configured_url = _configured_mysql_test_url()
    configured_name = configured_url.database or ""
    temporary_name = _temporary_database_name(configured_name)
    temporary_url = configured_url.set(database=temporary_name)
    admin_engine = create_engine(configured_url.set(database=None), pool_pre_ping=True)
    temporary_engine = None
    temporary_created = False
    config = _alembic_config()
    expected_head = ScriptDirectory.from_config(config).get_current_head()

    # GIVEN: A disposable MySQL database derived only from an explicitly
    # configured *_test URL, leaving the configured test schema untouched.
    try:
        _database_ddl(
            admin_engine,
            f"CREATE DATABASE `{temporary_name}` "
            "CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci",
        )
        temporary_created = True
        _set_migration_database(monkeypatch, temporary_url)
        command.upgrade(config, MIGRATION_0002)
        temporary_engine = create_engine(temporary_url, pool_pre_ping=True)

        # AND: ContactPoint is explicitly restored to the historical 0002
        # shape with no digest column, regardless of the current frozen 0002 DDL.
        _drop_hash_identity_from_0002(temporary_engine)
        assert HASH_COLUMN not in {
            column["name"]
            for column in inspect(temporary_engine).get_columns(TABLE_NAME)
        }
        with temporary_engine.connect() as connection:
            assert connection.execute(
                text("SELECT version_num FROM alembic_version")
            ).scalar_one() == MIGRATION_0002
        contact_point_id, normalized_value = _seed_historical_unicode_identity(
            temporary_engine
        )

        # WHEN: The real MySQL migration chain advances the historical schema
        # from its unchanged 0002 stamp to head.
        command.upgrade(config, "head")

        # THEN: The Unicode identity is preserved and deterministically hashed,
        # while the final digest column is non-null and protected by its key.
        columns = {
            column["name"]: column
            for column in inspect(temporary_engine).get_columns(TABLE_NAME)
        }
        constraints = {
            constraint["name"]: constraint.get("column_names") or []
            for constraint in inspect(temporary_engine).get_unique_constraints(TABLE_NAME)
            if constraint.get("name")
        }
        assert columns[HASH_COLUMN]["nullable"] is False
        assert columns[HASH_COLUMN]["type"].length == 64
        assert constraints[HASH_CONSTRAINT] == [
            "owner_id",
            "channel",
            HASH_COLUMN,
        ]
        expected_digest = hashlib.sha256(normalized_value.encode("utf-8")).hexdigest()
        with temporary_engine.connect() as connection:
            row = connection.execute(
                text(
                    f"SELECT normalized_value, {HASH_COLUMN} FROM {TABLE_NAME} "
                    "WHERE id = :contact_point_id"
                ),
                {"contact_point_id": contact_point_id},
            ).mappings().one()
            assert row["normalized_value"] == normalized_value
            assert row[HASH_COLUMN] == expected_digest
            assert connection.execute(
                text("SELECT version_num FROM alembic_version")
            ).scalar_one() == expected_head

        # AND: A duplicate owner/channel digest is rejected by MySQL itself,
        # without inserting or replacing either source row.
        with pytest.raises(IntegrityError):
            with temporary_engine.begin() as connection:
                connection.execute(
                    text(
                        f"INSERT INTO {TABLE_NAME} "
                        "(owner_id, company_id, contact_id, channel, value, normalized_value, "
                        f"{HASH_COLUMN}, verification_status, availability_status, is_primary, "
                        "created_at, updated_at) "
                        "SELECT owner_id, company_id, contact_id, channel, 'alias@example.cn', "
                        f"'alias@example.cn', {HASH_COLUMN}, verification_status, "
                        "availability_status, 0, created_at, updated_at "
                        f"FROM {TABLE_NAME} WHERE id = :contact_point_id"
                    ),
                    {"contact_point_id": contact_point_id},
                )
        with temporary_engine.connect() as connection:
            assert connection.execute(
                text(f"SELECT COUNT(*) FROM {TABLE_NAME}")
            ).scalar_one() == 1

        command.upgrade(config, "head")
        with temporary_engine.connect() as connection:
            assert connection.execute(
                text("SELECT version_num FROM alembic_version")
            ).scalar_one() == expected_head
    finally:
        # THEN: Cleanup never repurposes the disposable schema.  If it exists,
        # attempt to leave it at head before dropping it, and always restore and
        # verify the separately configured *_test schema at head afterward.
        try:
            if temporary_created:
                try:
                    _set_migration_database(monkeypatch, temporary_url)
                    command.upgrade(config, "head")
                finally:
                    if temporary_engine is not None:
                        temporary_engine.dispose()
                    _database_ddl(admin_engine, f"DROP DATABASE `{temporary_name}`")
        finally:
            admin_engine.dispose()
            _set_migration_database(monkeypatch, configured_url)
            command.upgrade(config, "head")
            configured_engine = create_engine(configured_url, pool_pre_ping=True)
            try:
                with configured_engine.connect() as connection:
                    assert connection.execute(
                        text("SELECT version_num FROM alembic_version")
                    ).scalar_one() == expected_head
            finally:
                configured_engine.dispose()
