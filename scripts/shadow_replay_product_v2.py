#!/usr/bin/env python3
"""Run the fake-only 30-company Product V2 acceptance replay."""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--company-count", type=int, default=30)
    parser.add_argument("--output", type=Path, default=Path(".local/shadow-replay/report.json"))
    args = parser.parse_args()

    environment = os.environ.get("AUTOLEADGEN_ENV", "local").lower()
    connector_mode = os.environ.get("AUTOLEADGEN_CONNECTOR_MODE", "fake").lower()
    real_allowed = os.environ.get("ALLOW_REAL_EXTERNAL_CALLS", "false").lower() in {"1", "true", "yes", "on"}
    if environment not in {"local", "test"} or connector_mode != "fake" or real_allowed:
        raise SystemExit("Refusing shadow replay without local/test + fake connector kill switch")

    output_path = args.output.expanduser().resolve()
    database_path = output_path.with_suffix(".db")
    if database_path.exists():
        raise SystemExit(f"Refusing to reuse shadow replay database: {database_path}")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    os.environ["AUTOLEADGEN_ENV"] = "test"
    os.environ["AUTOLEADGEN_CONNECTOR_MODE"] = "fake"
    os.environ["ALLOW_REAL_EXTERNAL_CALLS"] = "false"
    os.environ["DATABASE_URL"] = f"sqlite+pysqlite:///{database_path}"

    from alembic import command
    from alembic.config import Config

    alembic_config = Config(str(ROOT / "alembic.ini"))
    alembic_config.set_main_option("script_location", str(ROOT / "alembic"))
    command.upgrade(alembic_config, "head")

    from database import SessionLocal
    from product_v2.shadow_replay import run_shadow_replay

    db = SessionLocal()
    try:
        report = run_shadow_replay(db, company_count=args.company_count)
        payload = json.dumps(report.to_dict(), ensure_ascii=False, indent=2) + "\n"
        output_path.write_text(payload, encoding="utf-8")
        os.chmod(output_path, 0o600)
        os.chmod(database_path, 0o600)
        print(payload, end="")
        if not report.passed:
            raise SystemExit(1)
    finally:
        db.close()


if __name__ == "__main__":
    main()
