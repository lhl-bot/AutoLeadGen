"""Connector registry with an application-level local network kill switch."""
from __future__ import annotations

import os

from product_v2.connectors.base import Connector
from product_v2.connectors.fake import FakeConnector
from product_v2.enums import Channel
from runtime_config import RuntimeConfigurationError, read_flag


def _enabled(name: str, default: bool = False) -> bool:
    try:
        return read_flag(name, default=default)
    except RuntimeConfigurationError as exc:
        raise RuntimeError(f"Invalid runtime control for {name}") from exc


class ConnectorRegistry:
    def __init__(self):
        self._connectors: dict[Channel, Connector] = {}

    def register(self, connector: Connector) -> None:
        self._assert_real_connector_allowed(connector)
        self._connectors[connector.channel] = connector

    @staticmethod
    def _assert_real_connector_allowed(connector: Connector) -> None:
        """Fail closed for real Provider I/O at registration and retrieval.

        Re-checking on ``get`` matters because an operator can engage the hard
        pause after a process registered its connectors. Fake connectors remain
        usable so local shadow/review acceptance can continue without network
        side effects.
        """

        if connector.is_fake:
            return
        environment = os.environ.get("AUTOLEADGEN_ENV", "local").strip().lower()
        mode = os.environ.get("AUTOLEADGEN_CONNECTOR_MODE", "fake").strip().lower()
        allows_real = _enabled("ALLOW_REAL_EXTERNAL_CALLS", False)
        hard_paused = _enabled("OUTBOUND_HARD_PAUSE", environment in {"local", "test"})
        if hard_paused:
            raise RuntimeError("Real connectors are disabled by OUTBOUND_HARD_PAUSE")
        if environment in {"local", "test"} or mode != "real" or not allows_real:
            raise RuntimeError("Real connectors are disabled by the Product V2 external-effects kill switch")

    def get(self, channel: Channel) -> Connector:
        connector = self._connectors.get(channel)
        if not connector:
            raise LookupError(f"No connector registered for {channel.value}")
        self._assert_real_connector_allowed(connector)
        return connector

    def assert_connector_allowed(self, connector: Connector) -> None:
        """Re-check runtime controls at the final Provider instruction."""

        self._assert_real_connector_allowed(connector)


def build_local_registry() -> ConnectorRegistry:
    registry = ConnectorRegistry()
    for channel in (Channel.EMAIL, Channel.LINKEDIN, Channel.WHATSAPP):
        registry.register(FakeConnector(channel))
    return registry


def build_real_registry() -> ConnectorRegistry:
    """Build production connectors; channel-specific controls remain fail closed."""

    from product_v2.connectors.smtp import SMTPConnector
    from product_v2.connectors.unipile import UnipileConnector

    registry = ConnectorRegistry()
    registry.register(SMTPConnector())
    registry.register(UnipileConnector(Channel.LINKEDIN))
    registry.register(UnipileConnector(Channel.WHATSAPP))
    return registry


def build_runtime_registry() -> ConnectorRegistry:
    environment = os.environ.get("AUTOLEADGEN_ENV", "local").strip().lower()
    mode = os.environ.get("AUTOLEADGEN_CONNECTOR_MODE", "fake").strip().lower()
    if environment in {"local", "test"} and mode == "fake":
        return build_local_registry()
    if environment in {"staging", "production"} and mode == "real":
        return build_real_registry()
    raise RuntimeError("No connector registry is valid for this runtime configuration")
