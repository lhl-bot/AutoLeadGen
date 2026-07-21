"""Authenticated, replay-protected Product V2 Provider webhooks.

Signing secrets are deliberately supplied at call time or resolved from the
process environment.  They are never returned in verification metadata and
must never be written to a database or log.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
import hmac
import os
import re
from threading import Lock
from typing import Iterator, Optional

from runtime_config import RuntimeConfigurationError, environment, read_flag, read_secret


SIGNATURE_HEADER = "X-AutoLeadGen-Webhook-Signature"
TIMESTAMP_HEADER = "X-AutoLeadGen-Webhook-Timestamp"
EVENT_ID_HEADER = "X-AutoLeadGen-Webhook-Event-Id"
DEFAULT_TOLERANCE_SECONDS = 300
MAX_TOLERANCE_SECONDS = 3600
DEFAULT_MAX_BODY_BYTES = 1_048_576
MAX_MAX_BODY_BYTES = 10_485_760
_PROVIDER_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._-]{0,99}$")
# The signed event id is also the API idempotency key and therefore must fit
# the existing 255-character durable idempotency columns.
_EVENT_ID_PATTERN = re.compile(r"^[\x21-\x7e]{8,255}$")
_SIGNATURE_PATTERN = re.compile(r"^v1=([0-9a-fA-F]{64})$")
_locks_guard = Lock()
_event_locks: dict[str, tuple[Lock, int]] = {}


class WebhookSecurityError(ValueError):
    """A fail-closed webhook authentication error safe to expose to clients."""

    def __init__(self, code: str, message: str, *, status_code: int = 401):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


@dataclass(frozen=True)
class WebhookVerification:
    provider: str
    owner_id: int
    event_id: str
    timestamp: int
    body_sha256: str
    signature_version: str = "v1"
    _authentication_value_sha256: tuple[bytes, ...] = field(
        default=(),
        repr=False,
        compare=False,
    )

    def safe_metadata(self) -> dict[str, object]:
        """Return persistence-safe evidence with no secret or signature value."""

        return {
            "verified": True,
            "signature_version": self.signature_version,
            "signed_at_unix": self.timestamp,
            "event_id": self.event_id,
            "body_sha256": self.body_sha256,
        }

    def matches_authentication_value(self, value: object) -> bool:
        """Return whether a scalar exactly equals secret/signature material.

        Only one-way digests are retained for the short lifetime of the
        verification object.  They are deliberately excluded from repr,
        equality, and durable verification metadata.
        """

        if not isinstance(value, (str, bytes)):
            return False
        encoded = value if isinstance(value, bytes) else value.encode("utf-8")
        digest = hashlib.sha256(encoded).digest()
        return any(
            hmac.compare_digest(digest, candidate)
            for candidate in self._authentication_value_sha256
        )


def normalize_provider(provider: str) -> str:
    normalized = (provider or "").strip().lower()
    if not _PROVIDER_PATTERN.fullmatch(normalized):
        raise WebhookSecurityError(
            "WEBHOOK_PROVIDER_INVALID",
            "Provider identifier is invalid",
            status_code=422,
        )
    return normalized


def _secret_environment_keys(provider: str, owner_id: int) -> tuple[str, ...]:
    suffix = re.sub(r"[^A-Z0-9]", "_", provider.upper())
    return (
        f"PRODUCT_V2_WEBHOOK_SECRET_OWNER_{owner_id}_{suffix}",
        f"PRODUCT_V2_WEBHOOK_SECRET_{suffix}",
        "PRODUCT_V2_WEBHOOK_SECRET",
    )


def resolve_webhook_secret(
    *,
    provider: str,
    owner_id: int,
    secret: Optional[str | bytes] = None,
) -> bytes:
    """Resolve a signing secret without exposing its value outside this call."""

    candidate: Optional[str | bytes] = secret
    if candidate is None:
        for key in _secret_environment_keys(provider, owner_id):
            try:
                value = read_secret(key)
            except RuntimeConfigurationError as exc:
                raise WebhookSecurityError(
                    "WEBHOOK_SECRET_INVALID",
                    "Webhook authentication is not configured securely",
                    status_code=503,
                ) from exc
            if value:
                candidate = value
                break
    if candidate is None:
        raise WebhookSecurityError(
            "WEBHOOK_SECRET_NOT_CONFIGURED",
            "Webhook authentication is not configured",
            status_code=503,
        )
    encoded = candidate if isinstance(candidate, bytes) else candidate.encode("utf-8")
    if len(encoded) < 32:
        raise WebhookSecurityError(
            "WEBHOOK_SECRET_INVALID",
            "Webhook authentication is not configured securely",
            status_code=503,
        )
    return encoded


def webhook_ingress_rejected() -> bool:
    """Emergency fail-closed switch checked before body parsing or DB access."""

    try:
        return read_flag(
            "PRODUCT_V2_WEBHOOK_REJECT_ALL",
            default=environment() == "production",
        )
    except RuntimeConfigurationError as exc:
        raise WebhookSecurityError(
            "WEBHOOK_INGRESS_POLICY_INVALID",
            "Webhook ingress policy is unavailable",
            status_code=503,
        ) from exc


def _canonical_message(
    *,
    provider: str,
    owner_id: int,
    timestamp: int,
    event_id: str,
    raw_body: bytes,
) -> bytes:
    prefix = f"v1\n{provider}\n{owner_id}\n{timestamp}\n{event_id}\n".encode("utf-8")
    return prefix + raw_body


def sign_webhook(
    *,
    secret: str | bytes,
    provider: str,
    owner_id: int,
    timestamp: int,
    event_id: str,
    raw_body: bytes,
) -> str:
    """Return the v1 HMAC header value for a byte-exact request body."""

    normalized_provider = normalize_provider(provider)
    encoded_secret = resolve_webhook_secret(
        provider=normalized_provider,
        owner_id=owner_id,
        secret=secret,
    )
    digest = hmac.new(
        encoded_secret,
        _canonical_message(
            provider=normalized_provider,
            owner_id=owner_id,
            timestamp=timestamp,
            event_id=event_id,
            raw_body=raw_body,
        ),
        hashlib.sha256,
    ).hexdigest()
    return f"v1={digest}"


def _configured_tolerance(explicit: Optional[int]) -> int:
    if explicit is not None:
        tolerance = explicit
    else:
        raw = os.environ.get(
            "PRODUCT_V2_WEBHOOK_TOLERANCE_SECONDS",
            str(DEFAULT_TOLERANCE_SECONDS),
        )
        try:
            tolerance = int(raw)
        except ValueError as exc:
            raise WebhookSecurityError(
                "WEBHOOK_TOLERANCE_INVALID",
                "Webhook timestamp policy is invalid",
                status_code=503,
            ) from exc
    if tolerance < 1 or tolerance > MAX_TOLERANCE_SECONDS:
        raise WebhookSecurityError(
            "WEBHOOK_TOLERANCE_INVALID",
            "Webhook timestamp policy is invalid",
            status_code=503,
        )
    return tolerance


def configured_max_body_bytes(explicit: Optional[int] = None) -> int:
    """Return the bounded raw-body limit used before HMAC/JSON processing."""

    if explicit is not None:
        limit = explicit
    else:
        raw = os.environ.get(
            "PRODUCT_V2_WEBHOOK_MAX_BODY_BYTES",
            str(DEFAULT_MAX_BODY_BYTES),
        )
        try:
            limit = int(raw)
        except ValueError as exc:
            raise WebhookSecurityError(
                "WEBHOOK_BODY_LIMIT_INVALID",
                "Webhook body-size policy is invalid",
                status_code=503,
            ) from exc
    if limit < 1 or limit > MAX_MAX_BODY_BYTES:
        raise WebhookSecurityError(
            "WEBHOOK_BODY_LIMIT_INVALID",
            "Webhook body-size policy is invalid",
            status_code=503,
        )
    return limit


def verify_webhook(
    *,
    provider: str,
    owner_id: int,
    timestamp_header: str,
    event_id_header: str,
    signature_header: str,
    raw_body: bytes,
    secret: Optional[str | bytes] = None,
    tolerance_seconds: Optional[int] = None,
    now: Optional[datetime] = None,
) -> WebhookVerification:
    """Verify HMAC, byte-exact body, timestamp window, and signed event id."""

    normalized_provider = normalize_provider(provider)
    event_id = (event_id_header or "").strip()
    if not _EVENT_ID_PATTERN.fullmatch(event_id):
        raise WebhookSecurityError(
            "WEBHOOK_EVENT_ID_INVALID",
            "Webhook event id is missing or invalid",
            status_code=422,
        )
    try:
        timestamp = int((timestamp_header or "").strip())
    except ValueError as exc:
        raise WebhookSecurityError(
            "WEBHOOK_TIMESTAMP_INVALID",
            "Webhook timestamp is missing or invalid",
        ) from exc
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    current_timestamp = int(current.timestamp())
    if abs(current_timestamp - timestamp) > _configured_tolerance(tolerance_seconds):
        raise WebhookSecurityError(
            "WEBHOOK_TIMESTAMP_OUTSIDE_TOLERANCE",
            "Webhook timestamp is outside the accepted window",
        )
    match = _SIGNATURE_PATTERN.fullmatch((signature_header or "").strip())
    if not match:
        raise WebhookSecurityError(
            "WEBHOOK_SIGNATURE_INVALID",
            "Webhook signature is missing or invalid",
        )
    secret_resolution_failed = False
    try:
        encoded_secret = resolve_webhook_secret(
            provider=normalized_provider,
            owner_id=owner_id,
            secret=secret,
        )
    except WebhookSecurityError as exc:
        if exc.code not in {
            "WEBHOOK_SECRET_NOT_CONFIGURED",
            "WEBHOOK_SECRET_INVALID",
        }:
            raise
        # Perform the same body-sized HMAC work with a fixed non-secret key so
        # an unauthenticated caller cannot enumerate owner-specific secret
        # configuration from either the response or a coarse timing signal.
        encoded_secret = hashlib.sha256(
            b"AutoLeadGen Product V2 webhook authentication dummy key v1"
        ).digest()
        secret_resolution_failed = True
    expected = hmac.new(
        encoded_secret,
        _canonical_message(
            provider=normalized_provider,
            owner_id=owner_id,
            timestamp=timestamp,
            event_id=event_id,
            raw_body=raw_body,
        ),
        hashlib.sha256,
    ).hexdigest()
    signature_value = (signature_header or "").strip()
    if secret_resolution_failed or not hmac.compare_digest(match.group(1).lower(), expected):
        raise WebhookSecurityError(
            "WEBHOOK_AUTHENTICATION_FAILED",
            "Webhook authentication failed",
        )
    return WebhookVerification(
        provider=normalized_provider,
        owner_id=owner_id,
        event_id=event_id,
        timestamp=timestamp,
        body_sha256=hashlib.sha256(raw_body).hexdigest(),
        _authentication_value_sha256=(
            hashlib.sha256(encoded_secret).digest(),
            hashlib.sha256(signature_value.encode("utf-8")).digest(),
        ),
    )


@contextmanager
def webhook_event_lock(*, owner_id: int, provider: str, event_id: str) -> Iterator[None]:
    """Serialize same-process duplicate deliveries through transaction commit.

    The database unique key remains the cross-process authority.  This bounded
    critical section makes local/API concurrency deterministic and avoids doing
    duplicate Task/Audit work before that database fence is reached.
    """

    key = f"{owner_id}:{provider}:{event_id}"
    with _locks_guard:
        event_lock, users = _event_locks.get(key, (Lock(), 0))
        _event_locks[key] = (event_lock, users + 1)
    event_lock.acquire()
    try:
        yield
    finally:
        event_lock.release()
        with _locks_guard:
            current_lock, users = _event_locks[key]
            if current_lock is not event_lock:
                raise RuntimeError("Webhook event lock registry is inconsistent")
            if users == 1:
                _event_locks.pop(key, None)
            else:
                _event_locks[key] = (event_lock, users - 1)
