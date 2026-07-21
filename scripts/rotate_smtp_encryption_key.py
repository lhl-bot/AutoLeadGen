#!/usr/bin/env python3
"""Verify or rotate stored SMTP credentials without exposing account data."""
from __future__ import annotations

import argparse
import base64
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from cryptography.fernet import Fernet, InvalidToken

from database import SessionLocal
import models
from runtime_config import read_secret


def _cipher(secret: str) -> Fernet:
    encoded = secret.encode("utf-8")
    if len(encoded) < 32:
        raise RuntimeError("SMTP rotation keys must contain at least 32 bytes")
    return Fernet(base64.urlsafe_b64encode(encoded[:32]))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--owner-id", type=int, required=True)
    parser.add_argument("--expected-count", type=int, required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    if args.owner_id < 1 or args.expected_count < 0:
        raise SystemExit("owner-id and expected-count must be non-negative")

    old_key = read_secret("OLD_SMTP_ENCRYPTION_KEY", required=True) or ""
    new_key = read_secret("SMTP_ENCRYPTION_KEY", required=True) or ""
    if old_key == new_key:
        raise SystemExit("Old and new SMTP encryption keys must differ")
    old_cipher = _cipher(old_key)
    new_cipher = _cipher(new_key)

    db = SessionLocal()
    try:
        query = db.query(models.EmailAccount).filter(
            models.EmailAccount.user_id == args.owner_id
        ).order_by(models.EmailAccount.id.asc())
        if db.get_bind().dialect.name == "mysql":
            query = query.with_for_update()
        accounts = query.all()
        if len(accounts) != args.expected_count:
            raise SystemExit(
                f"Expected {args.expected_count} account(s), found {len(accounts)}; refusing rotation"
            )
        rotated: list[tuple[models.EmailAccount, str]] = []
        for account in accounts:
            try:
                plaintext = old_cipher.decrypt(account.smtp_pass.encode("utf-8"))
            except InvalidToken as exc:
                raise SystemExit(
                    "At least one stored credential is not encrypted by the supplied old key"
                ) from exc
            rotated.append(
                (account, new_cipher.encrypt(plaintext).decode("utf-8"))
            )
        for account, ciphertext in rotated:
            account.smtp_pass = ciphertext
        if args.apply:
            db.commit()
        else:
            db.rollback()
        print(
            json.dumps(
                {
                    "status": "applied" if args.apply else "verified_dry_run",
                    "owner_id": args.owner_id,
                    "account_count": len(accounts),
                    "secrets_exposed": False,
                },
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
