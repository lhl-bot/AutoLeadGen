from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

from sqlalchemy import Column, MetaData, Table, Text, create_engine, inspect, text


PROJECT_ROOT = Path(__file__).parents[1]
SCRIPT_PATH = PROJECT_ROOT / "scripts" / "validate_legacy_v16_schema.py"


def _load_validator_module():
    spec = importlib.util.spec_from_file_location("legacy_v16_schema_validator", SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


VALIDATOR = _load_validator_module()


def _create_legacy_schema(database_path: Path, *, omit_table: str | None = None) -> str:
    database_url = f"sqlite+pysqlite:///{database_path}"
    metadata = MetaData()
    for table_name, column_names in VALIDATOR.EXPECTED_SCHEMA.items():
        if table_name == omit_table:
            continue
        Table(table_name, metadata, *(Column(column_name, Text) for column_name in column_names))
    metadata.create_all(create_engine(database_url))
    return database_url


def _run_validator(database_url: str, *arguments: str, **environment: str):
    process_environment = os.environ.copy()
    process_environment.update(
        {
            "LEGACY_V16_DATABASE_URL": database_url,
            # A conflicting application URL proves the validator never uses it.
            "DATABASE_URL": "sqlite+pysqlite:////path/that/must/not/be/used.db",
            **environment,
        }
    )
    completed = subprocess.run(
        [sys.executable, str(SCRIPT_PATH), *arguments],
        cwd=PROJECT_ROOT,
        env=process_environment,
        capture_output=True,
        text=True,
        check=False,
    )
    return completed, json.loads(completed.stdout)


def test_validator_accepts_complete_schema_without_stamping(tmp_path):
    database_path = tmp_path / "valid-legacy.db"
    database_url = _create_legacy_schema(database_path)

    completed, result = _run_validator(database_url)

    assert completed.returncode == 0, completed.stderr
    assert result["ok"] is True
    assert result["mode"] == "validate_only"
    assert result["stamped"] is False
    assert result["missing_tables"] == []
    assert result["unexpected_tables"] == []
    assert result["missing_columns"] == {}
    assert "alembic_version" not in inspect(create_engine(database_url)).get_table_names()


def test_validator_reports_missing_table_and_exits_nonzero(tmp_path):
    database_path = tmp_path / "missing-table.db"
    database_url = _create_legacy_schema(database_path, omit_table="leads")

    completed, result = _run_validator(database_url)

    assert completed.returncode == 1
    assert result["ok"] is False
    assert result["error_code"] == "SCHEMA_MISMATCH"
    assert result["missing_tables"] == ["leads"]
    assert "alembic_version" not in inspect(create_engine(database_url)).get_table_names()


def test_validator_rejects_unreviewed_extra_table(tmp_path):
    database_path = tmp_path / "extra-table.db"
    database_url = _create_legacy_schema(database_path)
    engine = create_engine(database_url)
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE unreviewed_table (id INTEGER PRIMARY KEY)"))

    completed, result = _run_validator(database_url)

    assert completed.returncode == 1
    assert result["ok"] is False
    assert result["unexpected_tables"] == ["unreviewed_table"]


def test_stamp_protection_fails_before_connecting_or_writing(tmp_path):
    database_path = tmp_path / "protected.db"
    database_url = _create_legacy_schema(database_path)

    completed, result = _run_validator(
        database_url,
        "--stamp",
        AUTOLEADGEN_ENV="production",
        PRODUCT_V2_ISOLATED_DATABASE="true",
    )

    assert completed.returncode == 2
    assert result["error_code"] == "STAMP_ENVIRONMENT_BLOCKED"
    assert result["stamped"] is False
    assert "alembic_version" not in inspect(create_engine(database_url)).get_table_names()


def test_explicit_stamp_is_isolated_validated_and_idempotent(tmp_path):
    database_path = tmp_path / "stamp-legacy.db"
    database_url = _create_legacy_schema(database_path)
    environment = {
        "AUTOLEADGEN_ENV": "test",
        "PRODUCT_V2_ISOLATED_DATABASE": "true",
    }

    completed, result = _run_validator(database_url, "--stamp", **environment)

    assert completed.returncode == 0, completed.stderr
    assert result["ok"] is True
    assert result["stamped"] is True
    engine = create_engine(database_url)
    with engine.connect() as connection:
        assert connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == VALIDATOR.REVISION

    repeated, repeated_result = _run_validator(database_url, "--stamp", **environment)
    assert repeated.returncode == 0, repeated.stderr
    assert repeated_result["stamped"] is False
    assert repeated_result["current_revision"] == VALIDATOR.REVISION
