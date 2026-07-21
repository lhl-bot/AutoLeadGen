"""Deterministic, no-network connectors used by every local Product V2 mode."""
from __future__ import annotations

import hashlib
from threading import Lock

from product_v2.connectors.base import ConnectorRequest, ConnectorResult
from product_v2.enums import Channel


class FakeConnector:
    is_fake = True

    def __init__(self, channel: Channel):
        self.channel = channel
        self.provider = f"fake-{channel.value}"
        self._lock = Lock()
        self._results: dict[str, ConnectorResult] = {}
        self.requests: list[ConnectorRequest] = []

    def send(self, request: ConnectorRequest) -> ConnectorResult:
        if request.channel != self.channel:
            raise ValueError(f"Connector {self.channel.value} cannot send {request.channel.value}")
        with self._lock:
            existing = self._results.get(request.idempotency_key)
            if existing:
                return existing
            digest = hashlib.sha256(request.idempotency_key.encode("utf-8")).hexdigest()[:24]
            result = ConnectorResult(
                accepted=True,
                provider=self.provider,
                provider_message_id=f"fake-{digest}",
                raw={"fake": True, "network_calls": 0},
            )
            self.requests.append(request)
            self._results[request.idempotency_key] = result
            return result

