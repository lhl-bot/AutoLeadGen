"""Persistent IMAP cursor using UIDVALIDITY and last UID."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from sqlalchemy.orm import Session

from product_v2 import models
from product_v2.enums import Channel


def prepare_cursor(
    db: Session,
    *,
    owner_id: int,
    channel_account_id: str,
    uid_validity: str,
) -> tuple[models.InboxCursor, int]:
    query = db.query(models.InboxCursor).filter_by(
        owner_id=owner_id,
        channel_account_id=channel_account_id,
    ).populate_existing()
    if db.bind and db.bind.dialect.name == "mysql":
        # One account must have only one advancing IMAP consumer per
        # transaction. Do not use SKIP LOCKED: the caller needs the latest
        # committed UID before deciding where to resume.
        query = query.with_for_update()
    cursor = query.first()
    if not cursor:
        cursor = models.InboxCursor(
            owner_id=owner_id,
            channel=Channel.EMAIL,
            channel_account_id=channel_account_id,
            uid_validity=uid_validity,
            last_uid=0,
        )
        db.add(cursor)
        db.flush()
    elif cursor.uid_validity != uid_validity:
        cursor.uid_validity = uid_validity
        cursor.last_uid = 0
        cursor.last_error = "uidvalidity_changed_cursor_reset"
    return cursor, int(cursor.last_uid) + 1


def record_success(db: Session, cursor: models.InboxCursor, *, last_uid: int) -> None:
    if int(last_uid) < int(cursor.last_uid or 0):
        raise ValueError("Inbox UID cursor cannot move backwards")
    cursor.last_uid = last_uid
    cursor.last_success_at = datetime.now(timezone.utc)
    cursor.last_error = None


def record_failure(db: Session, cursor: models.InboxCursor, error: str) -> None:
    cursor.last_error = error[:4000]
