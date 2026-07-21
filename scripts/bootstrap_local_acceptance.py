#!/usr/bin/env python3
"""Create an idempotent administrator only in an acknowledged isolated DB."""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from database import SQLALCHEMY_DATABASE_URL, SessionLocal
import models as legacy
from product_v2 import models
from product_v2.enums import OwnerWritePath
from product_v2.production import current_migration_revision, expected_migration_head
from runtime_config import environment, read_secret
from scripts.backfill_product_v2 import assert_isolated_database
from services.auth import hash_password


_USERNAME = re.compile(r"^[^\x00-\x1f\x7f]{3,255}$")


def main() -> int:
    if environment() not in {"local", "test"}:
        raise SystemExit("Acceptance bootstrap is restricted to local or test")
    assert_isolated_database(SQLALCHEMY_DATABASE_URL, apply=False)

    username = os.environ.get("LOCAL_ACCEPTANCE_USERNAME", "acceptance-admin").strip()
    if not _USERNAME.fullmatch(username):
        raise SystemExit("LOCAL_ACCEPTANCE_USERNAME is invalid")
    if os.environ.get("LOCAL_ACCEPTANCE_PASSWORD"):
        raise SystemExit("Acceptance password must be supplied through a file")
    if not os.environ.get("LOCAL_ACCEPTANCE_PASSWORD_FILE"):
        raise SystemExit("LOCAL_ACCEPTANCE_PASSWORD_FILE is required")
    password = read_secret("LOCAL_ACCEPTANCE_PASSWORD", required=True) or ""
    if len(password) < 12 or len(password) > 128:
        raise SystemExit("Acceptance password must contain 12 to 128 characters")

    db = SessionLocal()
    try:
        if current_migration_revision(db) != expected_migration_head():
            raise SystemExit("Acceptance database is not at the migration head")
        user = db.query(legacy.User).filter(legacy.User.username == username).first()
        if user is None:
            user = legacy.User(
                username=username,
                display_name="Acceptance Administrator",
                hashed_password=hash_password(password),
                is_admin=True,
                is_active=True,
            )
            db.add(user)
            db.flush()
            status = "created"
        else:
            user.hashed_password = hash_password(password)
            user.is_admin = True
            user.is_active = True
            status = "refreshed"

        migration = db.get(models.OwnerMigrationState, user.id)
        if migration is None:
            db.add(
                models.OwnerMigrationState(
                    owner_id=user.id,
                    current_path=OwnerWritePath.V2,
                    version=1,
                    switched_by_user_id=user.id,
                )
            )
        else:
            migration.current_path = OwnerWritePath.V2
            migration.version = max(int(migration.version or 0), 1)
            migration.switched_by_user_id = user.id
        db.commit()
        print(
            json.dumps(
                {"status": status, "user_id": user.id, "database": "isolated"},
                separators=(",", ":"),
            )
        )
        return 0
    except BaseException:
        db.rollback()
        raise
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
