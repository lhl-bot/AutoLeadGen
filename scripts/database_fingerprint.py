#!/usr/bin/env python3
"""Print only the reviewable digest of the configured MySQL target."""
from __future__ import annotations

import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from database import SessionLocal
from product_v2.production import (
    current_migration_revision,
    database_identity_fingerprint,
    expected_migration_head,
)


def main() -> int:
    db = SessionLocal()
    try:
        print(
            json.dumps(
                {
                    "database_fingerprint": database_identity_fingerprint(db),
                    "current_revision": current_migration_revision(db),
                    "expected_revision": expected_migration_head(),
                },
                separators=(",", ":"),
            )
        )
        return 0
    finally:
        db.rollback()
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
