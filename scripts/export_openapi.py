#!/usr/bin/env python3
"""Export the checked-in OpenAPI contract without starting workers or external connectors."""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "frontend" / "openapi.v2.json"
sys.path.insert(0, str(ROOT))

# Contract generation must never inherit a shell or repository production URL.
os.environ["DATABASE_URL"] = f"sqlite:///{ROOT / '.local' / 'openapi-export.db'}"
os.environ["APP_ENV"] = "local"
os.environ["AUTOLEADGEN_ENV"] = "local"
os.environ["AUTOLEADGEN_CONNECTOR_MODE"] = "fake"
os.environ["ALLOW_REAL_EXTERNAL_CALLS"] = "false"
os.environ["ALLOW_REAL_ACQUISITION_CALLS"] = "false"
os.environ["OUTBOUND_HARD_PAUSE"] = "1"
os.environ["PRODUCT_V2_LEGACY_READ_ONLY"] = "1"

from main import app  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--check",
        action="store_true",
        help="fail when the checked-in OpenAPI contract is not current",
    )
    args = parser.parse_args()
    rendered = json.dumps(app.openapi(), ensure_ascii=False, indent=2) + "\n"
    if args.check:
        if not OUTPUT.exists() or OUTPUT.read_text(encoding="utf-8") != rendered:
            raise SystemExit(
                "frontend/openapi.v2.json is stale; run scripts/export_openapi.py "
                "and regenerate frontend API types"
            )
        print(f"OpenAPI contract is current: {OUTPUT}")
        return

    OUTPUT.write_text(rendered, encoding="utf-8")
    print(f"Exported {OUTPUT}")


if __name__ == "__main__":
    main()
