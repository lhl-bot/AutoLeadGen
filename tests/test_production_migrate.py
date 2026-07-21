from __future__ import annotations

import json

import pytest

from scripts import production_migrate


HEAD = "0007_acquisition_activation"
FINGERPRINT = "a" * 64


class _FakeSession:
    def __init__(self, bind):
        self.bind = bind
        self.closed = False

    def get_bind(self):
        return self.bind

    def rollback(self):
        return None

    def close(self):
        self.closed = True


def _approved_environment(monkeypatch):
    values = {
        "AUTOLEADGEN_ENV": "production",
        "OUTBOUND_HARD_PAUSE": "true",
        "ALLOW_REAL_EXTERNAL_CALLS": "false",
        "PRODUCT_V2_LEGACY_WRITERS_FROZEN": "true",
        "PRODUCT_V2_PRODUCTION_MIGRATION_APPROVED": "true",
        "PRODUCT_V2_LEGACY_BASELINE_STAMP_APPROVED": "true",
        "PRODUCTION_CHANGE_ID": "CHG-2026-0717",
        "RELEASE_SHA": "reviewed-release",
        "IMAGE_DIGEST": f"sha256:{'b' * 64}",
        "PRODUCT_V2_BACKUP_RESTORE_EVIDENCE_ID": "rds-backup-3079351548",
        "PRODUCT_V2_STAGING_ACCEPTANCE_EVIDENCE_ID": "staging-rehearsal-0717",
        "PRODUCT_V2_APPROVED_DATABASE_FINGERPRINT": FINGERPRINT,
    }
    for name, value in values.items():
        monkeypatch.setenv(name, value)


def test_production_migrate_refuses_without_explicit_approval(monkeypatch):
    _approved_environment(monkeypatch)
    monkeypatch.setenv("PRODUCT_V2_PRODUCTION_MIGRATION_APPROVED", "false")
    monkeypatch.setattr(
        production_migrate,
        "SessionLocal",
        lambda: pytest.fail("database must not be opened before approval"),
    )

    with pytest.raises(SystemExit, match="PRODUCTION_MIGRATION_APPROVED"):
        production_migrate.main()


def test_production_migrate_validates_stamps_and_upgrades_legacy_database(
    monkeypatch,
    capsys,
):
    _approved_environment(monkeypatch)
    legacy_bind = object()
    sessions = iter([_FakeSession(legacy_bind), _FakeSession(object())])
    monkeypatch.setattr(production_migrate, "SessionLocal", lambda: next(sessions))
    monkeypatch.setattr(
        production_migrate,
        "database_identity_fingerprint",
        lambda _db: FINGERPRINT,
    )
    revisions = iter([None, HEAD])
    monkeypatch.setattr(
        production_migrate,
        "current_migration_revision",
        lambda _db: next(revisions),
    )
    monkeypatch.setattr(production_migrate, "expected_migration_head", lambda: HEAD)
    validation = {
        "ok": True,
        "expected_revision": production_migrate.LEGACY_REVISION,
        "current_revision": None,
        "expected_table_count": 23,
        "validated_table_count": 23,
        "missing_tables": [],
        "unexpected_tables": [],
        "missing_columns": {},
    }
    monkeypatch.setattr(
        production_migrate,
        "validate_schema",
        lambda bind: validation if bind is legacy_bind else pytest.fail("wrong bind"),
    )
    calls = []
    monkeypatch.setattr(
        production_migrate.command,
        "stamp",
        lambda _config, revision: calls.append(("stamp", revision)),
    )
    monkeypatch.setattr(
        production_migrate.command,
        "upgrade",
        lambda _config, revision: calls.append(("upgrade", revision)),
    )

    assert production_migrate.main() == 0

    assert calls == [
        ("stamp", production_migrate.LEGACY_REVISION),
        ("upgrade", "head"),
    ]
    output = json.loads(capsys.readouterr().out)
    assert output["database_fingerprint"] == FINGERPRINT
    assert output["revision_before"] is None
    assert output["legacy_revision_stamped"] is True
    assert output["revision_after"] == HEAD
    assert output["legacy_validation"] == validation
