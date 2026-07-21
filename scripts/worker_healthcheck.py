#!/usr/bin/env python3
from __future__ import annotations

import argparse
from datetime import timedelta
import os
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from database import SessionLocal
from product_v2 import models
from product_v2.enums import StageStatus, WorkerType
from product_v2.services.domain import utcnow
from runtime_config import read_flag


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("worker_type", choices=(WorkerType.OUTBOUND.value, WorkerType.INBOX.value))
    args = parser.parse_args()
    db = SessionLocal()
    try:
        row = db.query(models.WorkerHeartbeat).filter(
            models.WorkerHeartbeat.worker_type == WorkerType(args.worker_type),
            models.WorkerHeartbeat.last_seen_at >= utcnow() - timedelta(seconds=90),
        ).order_by(models.WorkerHeartbeat.last_seen_at.desc()).all()
        release_sha = os.environ.get("RELEASE_SHA", "").strip()
        image_digest = os.environ.get("IMAGE_DIGEST", "").strip()
        row = next(
            (
                candidate
                for candidate in row
                if (candidate.details or {}).get("release_sha") == release_sha
                and (candidate.details or {}).get("image_digest") == image_digest
            ),
            None,
        )
        if row is None:
            return 1
        if row.status == StageStatus.RUNNING:
            return 0
        # A deployed but intentionally paused worker is healthy infrastructure;
        # enable-real/verify-live preflight still refuses to call it live.
        if row.status == StageStatus.DISABLED and read_flag("OUTBOUND_HARD_PAUSE", default=True):
            return 0
        return 1
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
