#!/usr/bin/env python3
"""Run the local Product V2 backfill.  Never imports or executes migrate_v16.py."""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy.engine import make_url


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def _enabled(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "on"}


def assert_isolated_database(database_url: str, *, apply: bool) -> None:
    environment = os.environ.get("AUTOLEADGEN_ENV", "").strip().lower()
    if environment not in {"local", "test"}:
        raise SystemExit("Backfill is restricted to AUTOLEADGEN_ENV=local or test")
    if not _enabled("PRODUCT_V2_ISOLATED_DATABASE"):
        raise SystemExit("Set PRODUCT_V2_ISOLATED_DATABASE=true for an isolated copy")
    if apply and not _enabled("PRODUCT_V2_BACKFILL_APPLY"):
        raise SystemExit("--apply requires PRODUCT_V2_BACKFILL_APPLY=true")

    url = make_url(database_url)
    backend = url.get_backend_name()
    if backend == "sqlite":
        return
    if backend != "mysql":
        raise SystemExit(f"Unsupported isolated backfill database backend: {backend}")
    if (url.host or "").lower() not in {"127.0.0.1", "localhost", "::1"}:
        raise SystemExit("MySQL backfill is restricted to a loopback host")
    if not (url.database or "").startswith("autoleadgen_v2"):
        raise SystemExit("MySQL backfill database name must start with autoleadgen_v2")


def main() -> None:
    parser = argparse.ArgumentParser()
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--apply", action="store_true")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--batch-size", type=int, default=100)
    parser.add_argument("--checkpoint", type=Path, default=Path(".local/backfill/checkpoint.json"))
    parser.add_argument("--quarantine", type=Path, default=Path(".local/backfill/quarantine.json"))
    args = parser.parse_args()

    load_dotenv(ROOT / ".env", override=False)
    database_url = os.environ.get("DATABASE_URL", "")
    if not database_url:
        raise SystemExit("DATABASE_URL must point to an isolated migrated database")
    assert_isolated_database(database_url, apply=args.apply)

    from database import SessionLocal
    import models  # noqa: F401
    import product_v2.models  # noqa: F401
    from product_v2.backfill import ProductV2Backfill

    db = SessionLocal()
    try:
        report = ProductV2Backfill(
            db,
            apply=args.apply,
            resume=args.resume,
            batch_size=args.batch_size,
            checkpoint_path=args.checkpoint,
            quarantine_path=args.quarantine,
        ).run()
        print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2))
    finally:
        db.close()


if __name__ == "__main__":
    main()
