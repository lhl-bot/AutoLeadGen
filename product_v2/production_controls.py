"""Fail-closed controls for reviewed production automation cohorts."""
from __future__ import annotations

from dataclasses import dataclass
import os
import re
from typing import Iterable

from runtime_config import RuntimeConfigurationError, environment, read_flag, read_int


AUTO_SEND_APPROVAL_FLAG = "PRODUCT_V2_PRODUCTION_AUTO_SEND_APPROVED"
AUTO_SEND_APPROVAL_ID = "PRODUCT_V2_AUTO_SEND_APPROVAL_ID"
AUTO_SEND_OWNER_IDS = "PRODUCT_V2_AUTO_SEND_OWNER_IDS"
AUTO_SEND_MAX_DAILY_PER_ACCOUNT = "PRODUCT_V2_AUTO_SEND_MAX_DAILY_PER_ACCOUNT"
_APPROVAL_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{2,199}$")


def configured_auto_send_owner_ids() -> frozenset[int]:
    """Return the exact secret-free Owner allow-list or fail on ambiguity."""

    raw = os.environ.get(AUTO_SEND_OWNER_IDS, "").strip()
    if not raw:
        return frozenset()
    values: set[int] = set()
    for item in raw.split(","):
        normalized = item.strip()
        if not normalized or not normalized.isascii() or not normalized.isdigit():
            raise RuntimeConfigurationError(
                f"{AUTO_SEND_OWNER_IDS} must be a comma-separated list of positive integers"
            )
        owner_id = int(normalized)
        if owner_id < 1:
            raise RuntimeConfigurationError(
                f"{AUTO_SEND_OWNER_IDS} must contain only positive integers"
            )
        values.add(owner_id)
    return frozenset(values)


def production_auto_send_daily_cap() -> int:
    """Return the deployment-wide ceiling for any automatic sender account."""

    return read_int(
        AUTO_SEND_MAX_DAILY_PER_ACCOUNT,
        default=20,
        minimum=1,
        maximum=100,
    )


@dataclass(frozen=True)
class AutoSendApproval:
    passed: bool
    approved: bool
    configured_owner_ids: frozenset[int]
    active_owner_ids: frozenset[int]
    daily_cap: int
    approval_id: str
    message: str


def auto_send_approval(active_owner_ids: Iterable[int]) -> AutoSendApproval:
    """Require an exact, non-latent approval for the active automatic cohort."""

    active = frozenset(int(owner_id) for owner_id in active_owner_ids)
    approval_id = os.environ.get(AUTO_SEND_APPROVAL_ID, "").strip()
    try:
        approved = read_flag(AUTO_SEND_APPROVAL_FLAG, default=False)
        configured = configured_auto_send_owner_ids()
        daily_cap = production_auto_send_daily_cap()
    except RuntimeConfigurationError as exc:
        return AutoSendApproval(
            passed=False,
            approved=False,
            configured_owner_ids=frozenset(),
            active_owner_ids=active,
            daily_cap=0,
            approval_id="",
            message=str(exc),
        )

    if not active:
        passed = not approved and not configured and not approval_id
        message = (
            "Automatic production sending is disabled with no latent Owner approval"
            if passed
            else "Automatic sending approval must be disabled when no active Owner uses it"
        )
    else:
        passed = (
            approved
            and configured == active
            and _APPROVAL_ID.fullmatch(approval_id) is not None
        )
        message = (
            f"Automatic sending is approved for the exact {len(active)}-Owner cohort"
            if passed
            else "Automatic sending requires explicit approval and an exact active Owner allow-list"
        )
    return AutoSendApproval(
        passed=passed,
        approved=approved,
        configured_owner_ids=configured,
        active_owner_ids=active,
        daily_cap=daily_cap,
        approval_id=approval_id,
        message=message,
    )


def unreviewed_auto_send_allowed(owner_id: int) -> bool:
    """Re-check one Owner at the final real Provider boundary."""

    if environment() not in {"staging", "production"}:
        return False
    try:
        return (
            read_flag(AUTO_SEND_APPROVAL_FLAG, default=False)
            and int(owner_id) in configured_auto_send_owner_ids()
            and _APPROVAL_ID.fullmatch(
                os.environ.get(AUTO_SEND_APPROVAL_ID, "").strip()
            )
            is not None
        )
    except (RuntimeConfigurationError, TypeError, ValueError):
        return False
