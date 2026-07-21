#!/usr/bin/env python3
"""One-time, approval-bound production administrator bootstrap."""
from __future__ import annotations

import hmac
import json
import os
from pathlib import Path
import re
import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from database import SessionLocal
import models as legacy
from product_v2 import models
from product_v2.production import (
    current_migration_revision,
    database_identity_fingerprint,
    expected_migration_head,
)
from runtime_config import environment, read_flag, read_secret
from services.auth import hash_password


_USERNAME = re.compile(r"^[^\x00-\x1f\x7f]{3,255}$")


def main() -> int:
    if environment() not in {"staging", "production"}:
        raise SystemExit("Administrator bootstrap requires staging or production")
    if not read_flag("BOOTSTRAP_ADMIN_APPROVED", default=False):
        raise SystemExit("BOOTSTRAP_ADMIN_APPROVED=true is required")
    if not read_flag("OUTBOUND_HARD_PAUSE", default=True):
        raise SystemExit("Administrator bootstrap requires outbound hard pause")
    if read_flag("ALLOW_REAL_EXTERNAL_CALLS", default=False):
        raise SystemExit("Administrator bootstrap requires external calls disabled")
    if not read_flag("PRODUCT_V2_LEGACY_WRITERS_FROZEN", default=False):
        raise SystemExit("Administrator bootstrap requires frozen legacy writers")
    for name in ("PRODUCTION_CHANGE_ID", "RELEASE_SHA"):
        if not os.environ.get(name, "").strip():
            raise SystemExit(f"{name} is required")

    username = os.environ.get("BOOTSTRAP_ADMIN_USERNAME", "").strip()
    if not _USERNAME.fullmatch(username):
        raise SystemExit("BOOTSTRAP_ADMIN_USERNAME is invalid")
    if os.environ.get("BOOTSTRAP_ADMIN_PASSWORD"):
        raise SystemExit("Bootstrap password must be supplied only through a secret file")
    if not os.environ.get("BOOTSTRAP_ADMIN_PASSWORD_FILE"):
        raise SystemExit("BOOTSTRAP_ADMIN_PASSWORD_FILE is required")
    password = read_secret("BOOTSTRAP_ADMIN_PASSWORD", required=True) or ""
    if len(password) < 16 or len(password) > 128:
        raise SystemExit("Bootstrap password must contain 16 to 128 characters")

    approved_fingerprint = os.environ.get(
        "PRODUCT_V2_APPROVED_DATABASE_FINGERPRINT", ""
    ).strip().lower()
    if len(approved_fingerprint) != 64:
        raise SystemExit("A reviewed production database fingerprint is required")

    db = SessionLocal()
    try:
        if db.get_bind().dialect.name != "mysql":
            raise SystemExit("Administrator bootstrap requires MySQL")
        observed_fingerprint = database_identity_fingerprint(db)
        if not hmac.compare_digest(observed_fingerprint, approved_fingerprint):
            raise SystemExit("Connected database identity is not approved for this change")
        if current_migration_revision(db) != expected_migration_head():
            raise SystemExit("Database is not at the release migration head")

        existing_admins = db.query(legacy.User).filter(
            legacy.User.is_admin.is_(True)
        ).all()
        same = next((row for row in existing_admins if row.username == username), None)
        if same is not None and same.is_active:
            print(
                json.dumps(
                    {"status": "already_exists", "user_id": same.id},
                    separators=(",", ":"),
                )
            )
            return 0
        if existing_admins:
            raise SystemExit("An administrator already exists; bootstrap is closed")
        if db.query(legacy.User.id).filter(legacy.User.username == username).first():
            raise SystemExit("Bootstrap username already belongs to another account")

        user = legacy.User(
            username=username,
            display_name="Production Administrator",
            hashed_password=hash_password(password),
            is_admin=True,
            is_active=True,
        )
        db.add(user)
        db.flush()
        db.add(
            models.AuditEvent(
                owner_id=user.id,
                actor_user_id=user.id,
                action="production_admin.bootstrapped",
                entity_type="user",
                entity_id=str(user.id),
                correlation_id=os.environ["PRODUCTION_CHANGE_ID"],
                after_data={
                    "is_admin": True,
                    "is_active": True,
                    "password_algorithm": "argon2",
                },
                metadata_json={
                    "release_sha": os.environ["RELEASE_SHA"],
                    "database_fingerprint": observed_fingerprint,
                    "contains_credentials": False,
                },
            )
        )
        db.commit()
        print(
            json.dumps(
                {"status": "created", "user_id": user.id},
                separators=(",", ":"),
            )
        )
        return 0
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
