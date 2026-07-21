"""Transactional boundary for authenticated Product V2 Provider webhooks.

The caller verifies the byte-exact request before invoking this module.  This
module binds that verification to durable MessageEvent uniqueness, commits all
business side effects atomically, and safely resolves concurrent replays.
"""
from __future__ import annotations

import json
import re
from typing import Any

from pydantic import ValidationError
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from product_v2 import models
from product_v2.enums import Channel, MessageDirection, MessageEventType
from product_v2.runtime.events import ingest_provider_event
from product_v2.schemas import WebhookEventCreate
from product_v2.webhook_security import WebhookVerification, webhook_event_lock


_SENSITIVE_METADATA_KEY = re.compile(
    r"(?:authorization|proxy[-_]?authorization|cookie|set[-_]?cookie|"
    r"api[-_]?key|access[-_]?token|refresh[-_]?token|client[-_]?secret|"
    r"webhook[-_]?secret|private[-_]?key|password|passwd|credential|"
    r"signature|(?:^|[-_])(?:token|secret)(?:$|[-_]))",
    re.IGNORECASE,
)
_SENSITIVE_METADATA_VALUE = re.compile(
    r"(?:\b(?:bearer|basic)\s+[A-Za-z0-9._~+/=-]{8,}|"
    r"\bv1=[0-9a-f]{64}\b|"
    r"\b(?:authorization|api[-_]?key|access[-_]?token|refresh[-_]?token|"
    r"client[-_]?secret|webhook[-_]?secret|password|passwd)\s*[:=]\s*\S+|"
    r"\bsk-[a-z0-9][a-z0-9_-]{6,}\b|"
    r"\b(?:gh[pousr]_[a-z0-9]{10,}|xox[baprs]-[a-z0-9-]{10,})\b|"
    r"\bAKIA[A-Z0-9]{16}\b|"
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----|"
    r"\beyJ[A-Za-z0-9_-]+\.eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b)",
    re.IGNORECASE,
)
_SAFE_PROVIDER_EVENT_TYPE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,199}$")


class VerifiedWebhookError(ValueError):
    """A structured error that is safe to expose without request contents."""

    def __init__(self, code: str, message: str, *, status_code: int):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


def _normalize_unipile_payload(decoded: dict[str, Any]) -> dict[str, Any]:
    """Convert signed Unipile webhook variants into the unified MessageEvent shape."""

    event_name = str(decoded.get("event") or decoded.get("type") or decoded.get("event_type") or "unknown").lower()
    data = decoded.get("data") if isinstance(decoded.get("data"), dict) else decoded
    source = str(
        data.get("provider")
        or data.get("account_type")
        or data.get("source")
        or decoded.get("provider")
        or ""
    ).lower()
    channel = Channel.WHATSAPP if "whatsapp" in source or "whatsapp" in event_name else Channel.LINKEDIN
    if any(token in event_name for token in ("received", "incoming", "new_message")):
        direction = MessageDirection.INBOUND
        event_type = MessageEventType.REPLIED
    elif "deliver" in event_name:
        direction = MessageDirection.OUTBOUND
        event_type = MessageEventType.DELIVERED
    elif "read" in event_name or "open" in event_name:
        direction = MessageDirection.OUTBOUND
        event_type = MessageEventType.OPENED
    elif "complaint" in event_name or "spam" in event_name:
        direction = MessageDirection.INBOUND
        event_type = MessageEventType.COMPLAINED
    elif "unsubscribe" in event_name or "opt_out" in event_name:
        direction = MessageDirection.INBOUND
        event_type = MessageEventType.UNSUBSCRIBED
    elif "bounce" in event_name:
        direction = MessageDirection.OUTBOUND
        event_type = MessageEventType.BOUNCED
    elif "fail" in event_name or "error" in event_name:
        direction = MessageDirection.OUTBOUND
        event_type = MessageEventType.FAILED
    elif "sent" in event_name:
        direction = MessageDirection.OUTBOUND
        event_type = MessageEventType.SENT
    else:
        direction = MessageDirection.INBOUND
        event_type = MessageEventType.UNKNOWN
    message = data.get("message") if isinstance(data.get("message"), dict) else data
    provider_message_id = (
        message.get("provider_message_id")
        or message.get("message_id")
        or message.get("id")
        or data.get("chat_id")
    )
    body = message.get("text") or message.get("body") or message.get("content")
    occurred_at = data.get("occurred_at") or data.get("timestamp") or decoded.get("timestamp")
    return {
        "channel": channel.value,
        "direction": direction.value,
        "event_type": event_type.value,
        "provider_message_id": str(provider_message_id) if provider_message_id is not None else None,
        "body": str(body) if body is not None else None,
        "occurred_at": occurred_at,
        "metadata_json": {
            "provider_event_type": event_name,
            "account_id": data.get("account_id"),
            "chat_id": data.get("chat_id"),
        },
    }


def _redact_sensitive_metadata(value: Any, verification: WebhookVerification) -> Any:
    """Remove credential-shaped metadata before any durable write."""

    if isinstance(value, dict):
        return {
            str(key): (
                "[REDACTED]"
                if _SENSITIVE_METADATA_KEY.search(str(key))
                else _redact_sensitive_metadata(item, verification)
            )
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_redact_sensitive_metadata(item, verification) for item in value]
    if isinstance(value, str) and (
        verification.matches_authentication_value(value)
        or _SENSITIVE_METADATA_VALUE.search(value)
    ):
        return "[REDACTED]"
    return value


def parse_verified_webhook_payload(
    *,
    raw_body: bytes,
    verification: WebhookVerification,
) -> WebhookEventCreate:
    """Parse only after HMAC verification and attach persistence-safe proof."""

    try:
        decoded = json.loads(raw_body)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise VerifiedWebhookError(
            "WEBHOOK_PAYLOAD_INVALID",
            "Webhook payload is not valid JSON",
            status_code=422,
        ) from exc
    if not isinstance(decoded, dict):
        raise VerifiedWebhookError(
            "WEBHOOK_PAYLOAD_INVALID",
            "Webhook payload must be a JSON object",
            status_code=422,
        )

    if verification.provider.lower() == "unipile":
        decoded = _normalize_unipile_payload(decoded)

    supplied_event_id = decoded.get("provider_event_id")
    if supplied_event_id is not None and supplied_event_id != verification.event_id:
        raise VerifiedWebhookError(
            "WEBHOOK_EVENT_ID_MISMATCH",
            "Signed event id does not match the payload",
            status_code=409,
        )

    raw_event_type = decoded.get("event_type")
    known_event_types = {item.value for item in MessageEventType}
    provider_event_type = None
    if isinstance(raw_event_type, str) and raw_event_type not in known_event_types:
        provider_event_type = (
            raw_event_type
            if _SAFE_PROVIDER_EVENT_TYPE.fullmatch(raw_event_type)
            and not _SENSITIVE_METADATA_KEY.search(raw_event_type)
            and not _SENSITIVE_METADATA_VALUE.search(raw_event_type)
            and not verification.matches_authentication_value(raw_event_type)
            else "[REDACTED]"
        )
        decoded["event_type"] = MessageEventType.UNKNOWN.value

    # Provider identifiers and message content have distinct business meaning,
    # so they are not pattern-redacted.  An exact copy of the active webhook
    # secret/signature is never valid business content, however, and must not
    # cross the durable boundary under a neutral field name.
    for field_name in ("provider_message_id", "subject", "body"):
        if verification.matches_authentication_value(decoded.get(field_name)):
            decoded[field_name] = "[REDACTED]"

    metadata = decoded.get("metadata_json")
    if metadata is None:
        metadata = {}
    if not isinstance(metadata, dict):
        raise VerifiedWebhookError(
            "WEBHOOK_PAYLOAD_INVALID",
            "Webhook metadata must be a JSON object",
            status_code=422,
        )
    safe_metadata = _redact_sensitive_metadata(metadata, verification)
    safe_metadata["webhook_verification"] = verification.safe_metadata()
    if provider_event_type is not None:
        safe_metadata["provider_event_type"] = provider_event_type
    decoded["metadata_json"] = safe_metadata
    decoded["provider_event_id"] = verification.event_id

    try:
        return WebhookEventCreate.model_validate(decoded)
    except ValidationError as exc:
        # Pydantic errors include rejected input values.  Do not echo them into
        # HTTP responses, logs, tasks, or audit records.
        raise VerifiedWebhookError(
            "WEBHOOK_PAYLOAD_INVALID",
            "Webhook payload does not match the Provider event contract",
            status_code=422,
        ) from exc


def _existing_event(
    db: Session,
    *,
    verification: WebhookVerification,
    idempotency_key: str,
) -> models.MessageEvent | None:
    return db.query(models.MessageEvent).filter(
        models.MessageEvent.owner_id == verification.owner_id,
        models.MessageEvent.provider == verification.provider,
        models.MessageEvent.provider_event_id == verification.event_id,
        models.MessageEvent.ingest_idempotency_key == idempotency_key,
    ).first()


def _validate_exact_replay(
    event: models.MessageEvent,
    *,
    verification: WebhookVerification,
) -> None:
    stored = (event.metadata_json or {}).get("webhook_verification") or {}
    if stored.get("body_sha256") != verification.body_sha256:
        raise VerifiedWebhookError(
            "WEBHOOK_REPLAY_CONFLICT",
            "Signed event id is already bound to different request bytes",
            status_code=409,
        )


def ingest_verified_webhook(
    db: Session,
    *,
    verification: WebhookVerification,
    idempotency_key: str,
    raw_body: bytes,
) -> tuple[models.MessageEvent, bool]:
    """Persist one verified delivery and return ``(event, was_replay)``.

    The process lock keeps duplicate work deterministic locally.  The two
    MessageEvent unique constraints remain authoritative across processes.  A
    loser of a database race rolls back all tentative side effects and returns
    the committed winner only when its byte hash is identical.
    """

    if idempotency_key != verification.event_id:
        raise VerifiedWebhookError(
            "WEBHOOK_IDEMPOTENCY_MISMATCH",
            "Idempotency-Key must equal the signed event id",
            status_code=409,
        )
    payload = parse_verified_webhook_payload(
        raw_body=raw_body,
        verification=verification,
    )

    with webhook_event_lock(
        owner_id=verification.owner_id,
        provider=verification.provider,
        event_id=verification.event_id,
    ):
        existing = _existing_event(
            db,
            verification=verification,
            idempotency_key=idempotency_key,
        )
        if existing is not None:
            _validate_exact_replay(existing, verification=verification)
            db.commit()  # release any read transaction before the lock exits
            return existing, True

        try:
            event = ingest_provider_event(
                db,
                owner_id=verification.owner_id,
                provider=verification.provider,
                idempotency_key=idempotency_key,
                payload=payload,
            )
            db.commit()
            db.refresh(event)
            return event, False
        except IntegrityError as exc:
            db.rollback()
            winner = _existing_event(
                db,
                verification=verification,
                idempotency_key=idempotency_key,
            )
            if winner is None:
                raise VerifiedWebhookError(
                    "WEBHOOK_EVENT_CONFLICT",
                    "Webhook event conflicts with an existing durable record",
                    status_code=409,
                ) from exc
            _validate_exact_replay(winner, verification=verification)
            db.commit()
            return winner, True
