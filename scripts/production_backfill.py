#!/usr/bin/env python3
"""Approved, identity-bound production wrapper for the resumable V2 backfill."""
from __future__ import annotations

import argparse
import hmac
import json
import os
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from database import SessionLocal
from product_v2.backfill import ProductV2Backfill
from product_v2.production import (
    current_migration_revision,
    database_identity_fingerprint,
    expected_migration_head,
)
from runtime_config import environment, read_flag


def _inside(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def main() -> int:
    parser = argparse.ArgumentParser()
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--apply", action="store_true")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--batch-size", type=int, default=100)
    parser.add_argument("--checkpoint", type=Path, default=Path("/evidence/checkpoint.json"))
    parser.add_argument("--quarantine", type=Path, default=Path("/evidence/quarantine.json"))
    args = parser.parse_args()

    if environment() not in {"staging", "production"}:
        raise SystemExit("Production backfill requires staging or production")
    if not read_flag("OUTBOUND_HARD_PAUSE", default=True):
        raise SystemExit("Production backfill requires OUTBOUND_HARD_PAUSE=true")
    if read_flag("ALLOW_REAL_EXTERNAL_CALLS", default=False):
        raise SystemExit("Production backfill requires external calls disabled")
    if not read_flag("PRODUCT_V2_LEGACY_WRITERS_FROZEN", default=False):
        raise SystemExit("Production backfill requires frozen legacy writers")
    if args.apply and not read_flag(
        "PRODUCT_V2_PRODUCTION_BACKFILL_APPROVED", default=False
    ):
        raise SystemExit(
            "--apply requires PRODUCT_V2_PRODUCTION_BACKFILL_APPROVED=true"
        )
    for name in ("PRODUCTION_CHANGE_ID", "RELEASE_SHA", "IMAGE_DIGEST"):
        if not os.environ.get(name, "").strip():
            raise SystemExit(f"{name} is required")

    evidence_root = Path(os.environ.get("BACKFILL_EVIDENCE_ROOT", "/evidence")).resolve()
    checkpoint = args.checkpoint.resolve()
    quarantine = args.quarantine.resolve()
    if not evidence_root.is_dir() or not _inside(checkpoint, evidence_root) or not _inside(
        quarantine, evidence_root
    ):
        raise SystemExit("Checkpoint and quarantine must stay inside BACKFILL_EVIDENCE_ROOT")

    approved_fingerprint = os.environ.get(
        "PRODUCT_V2_APPROVED_DATABASE_FINGERPRINT", ""
    ).strip().lower()
    if len(approved_fingerprint) != 64:
        raise SystemExit("A reviewed production database fingerprint is required")

    db = SessionLocal()
    try:
        observed_fingerprint = database_identity_fingerprint(db)
        if not hmac.compare_digest(observed_fingerprint, approved_fingerprint):
            raise SystemExit("Connected database identity is not approved for this change")
        if current_migration_revision(db) != expected_migration_head():
            raise SystemExit("Database is not at the release migration head")
        report = ProductV2Backfill(
            db,
            apply=args.apply,
            resume=args.resume,
            batch_size=args.batch_size,
            checkpoint_path=checkpoint,
            quarantine_path=quarantine,
        ).run()
        output = {
            "change_id": os.environ["PRODUCTION_CHANGE_ID"],
            "release_sha": os.environ["RELEASE_SHA"],
            "image_digest": os.environ["IMAGE_DIGEST"],
            "database_fingerprint": observed_fingerprint,
            "report": report.to_dict(),
        }
        print(json.dumps(output, ensure_ascii=False, indent=2))
        return 0
    finally:
        db.rollback()
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
