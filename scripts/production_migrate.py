#!/usr/bin/env python3
"""Approval-bound, identity-bound Product V2 production migration wrapper."""
from __future__ import annotations

import hmac
import json
import os
from pathlib import Path
import re
import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory

from database import SessionLocal
from product_v2.production import (
    current_migration_revision,
    database_identity_fingerprint,
    expected_migration_head,
)
from runtime_config import environment, read_flag
from scripts.validate_legacy_v16_schema import REVISION as LEGACY_REVISION
from scripts.validate_legacy_v16_schema import validate_schema


_FINGERPRINT = re.compile(r"^[0-9a-f]{64}$")
_IMAGE_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
_EVIDENCE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{2,199}$")


def _required(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise SystemExit(f"{name} is required")
    return value


def _evidence(name: str) -> str:
    value = _required(name)
    if _EVIDENCE_ID.fullmatch(value) is None:
        raise SystemExit(f"{name} must be a non-secret evidence identifier")
    return value


def _database_state() -> tuple[str, str | None, object]:
    db = SessionLocal()
    try:
        return (
            database_identity_fingerprint(db),
            current_migration_revision(db),
            db.get_bind(),
        )
    finally:
        db.rollback()
        db.close()


def _known_ancestor(config: Config, revision: str, head: str) -> bool:
    script = ScriptDirectory.from_config(config)
    return revision in {
        item.revision for item in script.iterate_revisions(head, "base")
    }


def main() -> int:
    if environment() not in {"staging", "production"}:
        raise SystemExit("Production migration requires staging or production")
    if not read_flag("OUTBOUND_HARD_PAUSE", default=True):
        raise SystemExit("Production migration requires OUTBOUND_HARD_PAUSE=true")
    if read_flag("ALLOW_REAL_EXTERNAL_CALLS", default=False):
        raise SystemExit("Production migration requires external calls disabled")
    if not read_flag("PRODUCT_V2_LEGACY_WRITERS_FROZEN", default=False):
        raise SystemExit("Production migration requires frozen legacy writers")
    if not read_flag("PRODUCT_V2_PRODUCTION_MIGRATION_APPROVED", default=False):
        raise SystemExit(
            "Production migration requires PRODUCT_V2_PRODUCTION_MIGRATION_APPROVED=true"
        )

    change_id = _required("PRODUCTION_CHANGE_ID")
    release_sha = _required("RELEASE_SHA")
    image_digest = _required("IMAGE_DIGEST").lower()
    if _IMAGE_DIGEST.fullmatch(image_digest) is None:
        raise SystemExit("IMAGE_DIGEST must be an immutable sha256 digest")
    backup_evidence = _evidence("PRODUCT_V2_BACKUP_RESTORE_EVIDENCE_ID")
    staging_evidence = _evidence("PRODUCT_V2_STAGING_ACCEPTANCE_EVIDENCE_ID")

    approved_fingerprint = os.environ.get(
        "PRODUCT_V2_APPROVED_DATABASE_FINGERPRINT", ""
    ).strip().lower()
    if _FINGERPRINT.fullmatch(approved_fingerprint) is None:
        raise SystemExit("A reviewed production database fingerprint is required")

    observed_fingerprint, revision_before, bind = _database_state()
    if not hmac.compare_digest(observed_fingerprint, approved_fingerprint):
        raise SystemExit("Connected database identity is not approved for this change")

    config = Config(str(ROOT / "alembic.ini"))
    expected_head = expected_migration_head()
    stamped_legacy_baseline = False
    legacy_validation: dict[str, object] | None = None

    if revision_before is None:
        if not read_flag("PRODUCT_V2_LEGACY_BASELINE_STAMP_APPROVED", default=False):
            raise SystemExit(
                "An unversioned legacy database requires "
                "PRODUCT_V2_LEGACY_BASELINE_STAMP_APPROVED=true"
            )
        legacy_validation = validate_schema(bind)
        if not legacy_validation["ok"]:
            raise SystemExit("Legacy v16 schema does not match the reviewed baseline")
        command.stamp(config, LEGACY_REVISION)
        stamped_legacy_baseline = True
    elif revision_before != expected_head and not _known_ancestor(
        config, revision_before, expected_head
    ):
        raise SystemExit("Database revision is not an ancestor of this release head")

    command.upgrade(config, "head")

    fingerprint_after, revision_after, _ = _database_state()
    if not hmac.compare_digest(fingerprint_after, approved_fingerprint):
        raise SystemExit("Database identity changed during migration")
    if revision_after != expected_head:
        raise SystemExit("Database did not reach the release migration head")

    output = {
        "change_id": change_id,
        "release_sha": release_sha,
        "image_digest": image_digest,
        "database_fingerprint": fingerprint_after,
        "backup_restore_evidence_id": backup_evidence,
        "staging_acceptance_evidence_id": staging_evidence,
        "revision_before": revision_before,
        "legacy_revision_stamped": stamped_legacy_baseline,
        "revision_after": revision_after,
        "legacy_validation": legacy_validation,
    }
    print(json.dumps(output, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
