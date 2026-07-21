"""Bounded IMAP adapter for Product V2 SMTP canary accounts."""
from __future__ import annotations

from contextlib import suppress
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from email import policy
from email.header import decode_header
from email.parser import BytesParser
from email.utils import parsedate_to_datetime, parseaddr
import hashlib
import imaplib
import re
import ssl
from typing import Optional

from sqlalchemy.orm import Session

import models as legacy
from product_v2 import models
from product_v2.enums import (
    AttemptStatus,
    Channel,
    MessageDirection,
    MessageEventType,
    OwnerWritePath,
)
from product_v2.runtime.events import ingest_provider_event
from product_v2.runtime.reply_parser import extract_latest_reply
from product_v2.schemas import WebhookEventCreate
from runtime_config import read_int
from services.auth import decrypt_smtp_pass


_MESSAGE_ID = re.compile(r"<[^<>\s]{3,500}>")
_RFC822_SIZE = re.compile(rb"RFC822\.SIZE\s+(\d+)", re.IGNORECASE)
_BOUNCE_MARKERS = (
    "mailer-daemon",
    "postmaster",
    "delivery status notification",
    "undelivered mail",
    "mail delivery failed",
    "returned mail",
    "undeliverable",
)
_COMPLAINT_FEEDBACK_TYPES = frozenset({"abuse", "fraud", "virus"})
_FEEDBACK_TYPE = re.compile(r"^feedback-type\s*:\s*([^\s;]+)", re.IGNORECASE | re.MULTILINE)


@dataclass(frozen=True)
class IMAPAccount:
    channel_account_id: int
    owner_id: int
    email: str
    host: str
    port: int
    username: str
    password: str


@dataclass
class IMAPPollResult:
    accounts: int = 0
    scanned: int = 0
    ingested: int = 0
    duplicates: int = 0
    unmatched: int = 0
    failures: int = 0

    @property
    def did_work(self) -> bool:
        return self.ingested > 0

    def safe_details(self) -> dict[str, int]:
        return {
            "accounts": self.accounts,
            "scanned": self.scanned,
            "ingested": self.ingested,
            "duplicates": self.duplicates,
            "unmatched": self.unmatched,
            "failures": self.failures,
        }


def _decode_header(value: object) -> str:
    parts: list[str] = []
    for item, encoding in decode_header(str(value or "")):
        if isinstance(item, bytes):
            try:
                parts.append(item.decode(encoding or "utf-8", errors="replace"))
            except LookupError:
                parts.append(item.decode("utf-8", errors="replace"))
        else:
            parts.append(item)
    return "".join(parts).strip()


def _body(message) -> str:
    plain: list[str] = []
    html: list[str] = []
    parts = message.walk() if message.is_multipart() else (message,)
    for part in parts:
        if part.get_content_disposition() == "attachment":
            continue
        content_type = part.get_content_type()
        if content_type not in {"text/plain", "text/html"}:
            continue
        try:
            text = part.get_content()
        except Exception:
            payload = part.get_payload(decode=True) or b""
            text = payload.decode(part.get_content_charset() or "utf-8", errors="replace")
        (plain if content_type == "text/plain" else html).append(str(text))
    selected = "\n".join(plain or html)
    if not plain:
        selected = re.sub(r"<[^>]+>", " ", selected)
    return extract_latest_reply(selected)[:100_000]


def _references(message) -> list[str]:
    found: list[str] = []
    parts = message.walk() if message.is_multipart() else (message,)
    for part in parts:
        for header in (
            "In-Reply-To",
            "References",
            "Original-Message-ID",
            "X-Original-Message-ID",
        ):
            raw = str(part.get(header) or "").strip()
            if not raw:
                continue
            references = _MESSAGE_ID.findall(raw)
            found.extend(references or [raw[:500]])
    return list(dict.fromkeys(found))


def _feedback_type(message) -> Optional[str]:
    """Return a normalized RFC 5965 Feedback-Type without trusting free text."""

    direct = str(message.get("Feedback-Type") or "").strip().lower()
    if direct:
        return direct.split(";", 1)[0].split()[0]
    parts = message.walk() if message.is_multipart() else (message,)
    for part in parts:
        direct = str(part.get("Feedback-Type") or "").strip().lower()
        if direct:
            return direct.split(";", 1)[0].split()[0]
        if part.get_content_type() != "message/feedback-report":
            continue
        try:
            payload = part.get_payload(decode=True)
            if payload is None:
                payload = str(part.get_payload() or "").encode("utf-8", errors="replace")
            report = payload.decode(part.get_content_charset() or "utf-8", errors="replace")
        except Exception:
            continue
        matched = _FEEDBACK_TYPE.search(report)
        if matched:
            return matched.group(1).strip().lower()
    return None


def _event_id(message, raw: bytes) -> str:
    supplied = str(message.get("Message-ID") or "").strip()
    if 3 <= len(supplied) <= 500:
        return supplied
    return f"imap-sha256:{hashlib.sha256(raw).hexdigest()}"


def _payload_bytes(payload) -> bytes:
    return next(
        (
            item[1]
            for item in payload or []
            if isinstance(item, tuple) and isinstance(item[1], bytes)
        ),
        b"",
    )


def _reported_size(payload) -> Optional[int]:
    for item in payload or []:
        candidate = item[0] if isinstance(item, tuple) else item
        if not isinstance(candidate, bytes):
            continue
        matched = _RFC822_SIZE.search(candidate)
        if matched:
            return int(matched.group(1))
    return None


def _occurred_at(message) -> datetime:
    try:
        parsed = parsedate_to_datetime(str(message.get("Date") or ""))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except (TypeError, ValueError, OverflowError):
        return datetime.now(timezone.utc)


def _accounts(db: Session) -> list[IMAPAccount]:
    rows = db.query(models.ChannelAccount, legacy.EmailAccount).join(
        legacy.EmailAccount,
        models.ChannelAccount.legacy_email_account_id == legacy.EmailAccount.id,
    ).join(
        models.OwnerMigrationState,
        models.OwnerMigrationState.owner_id == models.ChannelAccount.owner_id,
    ).filter(
        models.ChannelAccount.channel == Channel.EMAIL,
        models.ChannelAccount.provider == "smtp",
        models.ChannelAccount.enabled.is_(True),
        models.ChannelAccount.archived_at.is_(None),
        models.OwnerMigrationState.current_path == OwnerWritePath.V2,
        legacy.EmailAccount.imap_host.isnot(None),
    ).all()
    accounts: list[IMAPAccount] = []
    for account, source in rows:
        if source.user_id != account.owner_id or source.email != account.provider_account_id:
            continue
        accounts.append(
            IMAPAccount(
                channel_account_id=account.id,
                owner_id=account.owner_id,
                email=source.email.strip().lower(),
                host=(source.imap_host or "").strip(),
                port=int(source.imap_port or 993),
                username=source.smtp_user,
                password=decrypt_smtp_pass(source.smtp_pass),
            )
        )
    return accounts


def _matched_attempt(
    db: Session,
    *,
    account: IMAPAccount,
    references: list[str],
) -> Optional[models.OutreachAttempt]:
    if not references:
        return None
    return db.query(models.OutreachAttempt).filter(
        models.OutreachAttempt.owner_id == account.owner_id,
        models.OutreachAttempt.channel_account_id == account.channel_account_id,
        models.OutreachAttempt.channel == Channel.EMAIL,
        models.OutreachAttempt.provider_message_id.in_(references),
        models.OutreachAttempt.status.in_(
            (AttemptStatus.SUCCEEDED, AttemptStatus.SENDING, AttemptStatus.UNKNOWN)
        ),
    ).order_by(models.OutreachAttempt.id.desc()).first()


def poll_imap_inbox(db: Session) -> IMAPPollResult:
    result = IMAPPollResult()
    lookback = read_int("IMAP_LOOKBACK_DAYS", default=7, minimum=1, maximum=30)
    max_messages = read_int(
        "IMAP_MAX_MESSAGES_PER_ACCOUNT", default=200, minimum=1, maximum=2000
    )
    max_bytes = read_int(
        "IMAP_MAX_MESSAGE_BYTES", default=5_242_880, minimum=1024, maximum=20_971_520
    )
    timeout = read_int("IMAP_TIMEOUT_SECONDS", default=20, minimum=5, maximum=60)

    for account in _accounts(db):
        result.accounts += 1
        mailbox = None
        try:
            if not account.host or not 1 <= account.port <= 65535:
                raise ValueError("invalid IMAP account configuration")
            mailbox = imaplib.IMAP4_SSL(
                account.host,
                account.port,
                timeout=timeout,
                ssl_context=ssl.create_default_context(),
            )
            mailbox.login(account.username, account.password)
            status, _ = mailbox.select("INBOX", readonly=True)
            if status != "OK":
                raise RuntimeError("IMAP inbox unavailable")
            since = (datetime.now() - timedelta(days=lookback)).strftime("%d-%b-%Y")
            status, identifiers = mailbox.search(None, f'(SINCE "{since}")')
            if status != "OK":
                raise RuntimeError("IMAP search failed")
            ids = (identifiers[0].split() if identifiers and identifiers[0] else [])[-max_messages:]
            for identifier in ids:
                status, header_payload = mailbox.fetch(
                    identifier,
                    "(RFC822.SIZE BODY.PEEK[HEADER])",
                )
                if status != "OK":
                    result.failures += 1
                    continue
                header_raw = _payload_bytes(header_payload)
                reported_size = _reported_size(header_payload)
                if (
                    not header_raw
                    or len(header_raw) > max_bytes
                    or (reported_size is not None and reported_size > max_bytes)
                ):
                    result.failures += 1
                    continue
                result.scanned += 1
                header_message = BytesParser(policy=policy.default).parsebytes(header_raw)
                inbound_id = _event_id(header_message, header_raw)
                provider_event_id = inbound_id[:500]
                idempotency_key = (
                    f"imap:{account.channel_account_id}:"
                    f"{hashlib.sha256(inbound_id.encode('utf-8')).hexdigest()}"
                )
                if db.query(models.MessageEvent.id).filter_by(
                    owner_id=account.owner_id,
                    ingest_idempotency_key=idempotency_key,
                ).first():
                    result.duplicates += 1
                    continue
                status, payload = mailbox.fetch(
                    identifier,
                    f"(BODY.PEEK[]<0.{max_bytes + 1}>)",
                )
                raw = _payload_bytes(payload)
                if status != "OK" or not raw or len(raw) > max_bytes:
                    result.failures += 1
                    continue
                message = BytesParser(policy=policy.default).parsebytes(raw)
                attempt = _matched_attempt(
                    db,
                    account=account,
                    references=_references(message),
                )
                if attempt is None:
                    result.unmatched += 1
                    continue
                body = _body(message)
                subject = _decode_header(message.get("Subject"))[:1000]
                sender = parseaddr(_decode_header(message.get("From")))[1].strip().lower()
                point = db.get(models.ContactPoint, attempt.contact_point_id)
                haystack = f"{sender} {subject} {body[:2000]}".lower()
                bounced = any(marker in haystack for marker in _BOUNCE_MARKERS)
                feedback_type = _feedback_type(message)
                complained = feedback_type in _COMPLAINT_FEEDBACK_TYPES
                if not bounced and not complained and (
                    point is None or sender != point.normalized_value
                ):
                    result.unmatched += 1
                    continue
                event = ingest_provider_event(
                    db,
                    owner_id=account.owner_id,
                    provider="smtp-imap",
                    idempotency_key=idempotency_key,
                    payload=WebhookEventCreate(
                        channel=Channel.EMAIL,
                        direction=(
                            MessageDirection.OUTBOUND
                            if bounced
                            else MessageDirection.INBOUND
                        ),
                        event_type=(
                            MessageEventType.BOUNCED
                            if bounced
                            else (
                                MessageEventType.COMPLAINED
                                if complained
                                else MessageEventType.REPLIED
                            )
                        ),
                        attempt_id=attempt.id,
                        provider_event_id=provider_event_id,
                        provider_message_id=attempt.provider_message_id,
                        subject=subject or None,
                        body=body or None,
                        occurred_at=_occurred_at(message),
                        metadata_json={
                            "adapter": "imap",
                            "channel_account_id": account.channel_account_id,
                            "feedback_type": feedback_type,
                        },
                    ),
                )
                db.commit()
                if event:
                    result.ingested += 1
        except Exception:
            db.rollback()
            result.failures += 1
        finally:
            if mailbox is not None:
                with suppress(Exception):
                    mailbox.logout()
    return result
