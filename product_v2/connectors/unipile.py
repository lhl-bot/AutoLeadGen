"""Fail-closed Unipile connector for approved LinkedIn and WhatsApp routes."""
from __future__ import annotations

from datetime import datetime, timezone
import re
from typing import Callable

import httpx
from sqlalchemy import or_

from database import SessionLocal
from product_v2 import models
from product_v2.connectors.base import ConnectorRequest, ConnectorResult
from product_v2.enums import (
    AttemptStatus,
    Channel,
    ChannelAccountHealth,
)
from runtime_config import read_flag, read_int, read_secret


class UnipileConfigurationError(RuntimeError):
    """The call was rejected before reaching Unipile."""


class UnipileDeliveryUncertain(RuntimeError):
    """Unipile may have accepted the request; automatic retry is forbidden."""


def _owner_allowlist(channel: Channel) -> set[int]:
    raw = read_secret(f"{channel.value.upper()}_OWNER_ALLOWLIST", default="") or ""
    return {int(value.strip()) for value in raw.split(",") if value.strip().isdigit()}


class UnipileConnector:
    provider = "unipile"
    is_fake = False

    def __init__(
        self,
        channel: Channel,
        *,
        session_factory: Callable = SessionLocal,
        http_client_factory: Callable[..., httpx.Client] = httpx.Client,
    ):
        if channel not in {Channel.LINKEDIN, Channel.WHATSAPP}:
            raise ValueError("UnipileConnector supports LinkedIn and WhatsApp only")
        self.channel = channel
        self._session_factory = session_factory
        self._http_client_factory = http_client_factory

    def _runtime_context(self, request: ConnectorRequest) -> tuple[str, str]:
        try:
            owner_id = int(request.metadata["owner_id"])
            account_id = int(request.metadata["channel_account_id"])
        except (KeyError, TypeError, ValueError) as exc:
            raise UnipileConfigurationError("unipile_account_binding_missing") from exc
        control = f"{self.channel.value.upper()}_HARD_PAUSE"
        if read_flag(control, default=True):
            raise UnipileConfigurationError(f"{self.channel.value}_hard_paused")
        if owner_id not in _owner_allowlist(self.channel):
            raise UnipileConfigurationError(f"{self.channel.value}_owner_not_allowed")
        expected_approval = read_secret(f"{self.channel.value.upper()}_APPROVAL_ID", default="") or ""
        if not expected_approval or request.metadata.get("approval_id") != expected_approval:
            raise UnipileConfigurationError(f"{self.channel.value}_approval_id_invalid")
        expected_price = read_secret(f"{self.channel.value.upper()}_PRICE_VERSION", default="") or ""
        if not expected_price or request.metadata.get("price_version") != expected_price:
            raise UnipileConfigurationError(f"{self.channel.value}_price_version_invalid")

        db = self._session_factory()
        try:
            account = db.query(models.ChannelAccount).filter_by(
                id=account_id,
                owner_id=owner_id,
                channel=self.channel,
                provider=self.provider,
                enabled=True,
                archived_at=None,
            ).first()
            if account is None or account.health_status != ChannelAccountHealth.HEALTHY:
                raise UnipileConfigurationError("unipile_account_unhealthy")
            configured_cap = read_int(
                f"{self.channel.value.upper()}_DAILY_ACCOUNT_LIMIT",
                default=5,
                minimum=1,
                maximum=100,
            )
            effective_cap = min(configured_cap, account.daily_limit or configured_cap)
            day_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
            sent_today = db.query(models.OutreachAttempt.id).filter(
                models.OutreachAttempt.channel_account_id == account.id,
                models.OutreachAttempt.status == AttemptStatus.SUCCEEDED,
                models.OutreachAttempt.sent_at >= day_start,
            ).count()
            if sent_today >= effective_cap:
                raise UnipileConfigurationError("unipile_daily_account_limit")
            if self.channel == Channel.WHATSAPP:
                contact_point_id = int(request.metadata.get("contact_point_id") or 0)
                consent = db.query(models.WhatsAppConsent.id).filter(
                    models.WhatsAppConsent.owner_id == owner_id,
                    models.WhatsAppConsent.contact_point_id == contact_point_id,
                    models.WhatsAppConsent.granted_at <= datetime.now(timezone.utc),
                    models.WhatsAppConsent.revoked_at.is_(None),
                    or_(
                        models.WhatsAppConsent.expires_at.is_(None),
                        models.WhatsAppConsent.expires_at > datetime.now(timezone.utc),
                    ),
                ).first()
                if consent is None:
                    raise UnipileConfigurationError("whatsapp_consent_required")
            return account.provider_account_id, str(owner_id)
        finally:
            db.close()

    @staticmethod
    def _provider_message_id(payload: object) -> str | None:
        if not isinstance(payload, dict):
            return None
        for key in ("message_id", "provider_message_id", "chat_id", "id"):
            value = payload.get(key)
            if isinstance(value, (str, int)) and str(value).strip():
                return str(value)
        nested = payload.get("data")
        if isinstance(nested, dict):
            return UnipileConnector._provider_message_id(nested)
        return None

    def send(self, request: ConnectorRequest) -> ConnectorResult:
        if request.channel != self.channel:
            raise UnipileConfigurationError("unipile_channel_mismatch")
        account_id, _owner_id = self._runtime_context(request)
        dsn = (read_secret("UNIPILE_DSN", default="") or "").rstrip("/")
        api_key = read_secret("UNIPILE_API_KEY", required=True)
        if not dsn or not api_key:
            raise UnipileConfigurationError("unipile_credentials_missing")
        recipient = request.recipient.strip()
        if self.channel == Channel.WHATSAPP:
            digits = "".join(character for character in recipient if character.isdigit())
            if len(digits) < 8:
                raise UnipileConfigurationError("whatsapp_recipient_invalid")
            attendee = f"{digits}@s.whatsapp.net"
        else:
            match = re.search(r"linkedin\.com/in/([^/?#]+)", recipient, flags=re.IGNORECASE)
            attendee = match.group(1) if match else recipient
            if not attendee or any(character.isspace() for character in attendee):
                raise UnipileConfigurationError("linkedin_recipient_invalid")
        headers = {
            "X-API-KEY": api_key,
            "Content-Type": "application/json",
            "Accept": "application/json",
            "Idempotency-Key": request.idempotency_key,
        }
        try:
            with self._http_client_factory(timeout=15.0) as client:
                response = client.post(
                    f"{dsn}/api/v1/chats",
                    headers=headers,
                    json={"account_id": account_id, "attendees_ids": [attendee], "text": request.body},
                )
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            raise UnipileDeliveryUncertain(type(exc).__name__) from exc
        if response.status_code not in {200, 201}:
            return ConnectorResult(
                accepted=False,
                provider=self.provider,
                provider_message_id=None,
                raw={"status_code": response.status_code, "reason": "unipile_rejected", "provider_called": True},
            )
        try:
            payload = response.json()
        except ValueError as exc:
            raise UnipileDeliveryUncertain("unipile_success_response_not_json") from exc
        provider_message_id = self._provider_message_id(payload)
        if provider_message_id is None:
            raise UnipileDeliveryUncertain("unipile_message_id_missing")
        return ConnectorResult(
            accepted=True,
            provider=self.provider,
            provider_message_id=provider_message_id,
            raw={"provider_called": True, "status_code": response.status_code},
        )
