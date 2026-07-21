#!/usr/bin/env python3
"""Emit a redacted production preflight report and fail on any blocker."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from database import SessionLocal
from product_v2.production import (
    deployment_configuration_checks,
    domain_preflight_checks,
    report,
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--phase",
        choices=("deploy", "enable-real", "verify-live"),
        default="deploy",
    )
    args = parser.parse_args()
    checks = deployment_configuration_checks(phase=args.phase)
    db = SessionLocal()
    try:
        checks.extend(domain_preflight_checks(db, phase=args.phase))
    finally:
        db.rollback()
        db.close()
    payload = report(checks, phase=args.phase)
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if payload["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
