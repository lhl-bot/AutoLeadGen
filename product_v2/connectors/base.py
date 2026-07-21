"""Side-effect connector contract shared by email, LinkedIn, and WhatsApp."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from product_v2.enums import Channel


@dataclass(frozen=True)
class ConnectorRequest:
    channel: Channel
    idempotency_key: str
    recipient: str
    body: str
    subject: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ConnectorResult:
    accepted: bool
    provider: str
    provider_message_id: str | None
    raw: dict[str, Any] = field(default_factory=dict)


class Connector(Protocol):
    channel: Channel
    provider: str
    is_fake: bool

    def send(self, request: ConnectorRequest) -> ConnectorResult:
        ...

