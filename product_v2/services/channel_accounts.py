"""Tenant-safe V2 sender-account binding and capacity gates.

The V2 row is deliberately a credential-free identity.  During the migration
window it may point at one legacy credential store, but SMTP passwords and
Provider tokens are never copied into Product V2 tables, Tasks, or audits.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from datetime import datetime, time, timedelta, timezone
from typing import Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

import models as legacy
from product_v2 import models
from product_v2.enums import (
    AttemptStatus,
    Channel,
    ChannelAccountHealth,
    ProviderCostStatus,
    SafetyLockScope,
)


_UNSET = object()


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _enabled(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def fake_account_fallback_allowed() -> bool:
    """Whether a missing Sequence binding may resolve to a local fake account."""

    environment = os.environ.get("AUTOLEADGEN_ENV", "local").strip().lower()
    mode = os.environ.get("AUTOLEADGEN_CONNECTOR_MODE", "fake").strip().lower()
    required = _enabled(
        "PRODUCT_V2_ACCOUNT_BINDING_REQUIRED",
        environment not in {"local", "test"} or mode != "fake",
    )
    return environment in {"local", "test"} and mode == "fake" and not required


@dataclass
class ChannelAccountDecision:
    account: Optional[models.ChannelAccount]
    blockers: list[str] = field(default_factory=list)
    observed_health: Optional[ChannelAccountHealth] = None
    used_capacity: int = 0
    remaining_capacity: Optional[int] = None
    window_started_at: Optional[datetime] = None
    window_ends_at: Optional[datetime] = None

    @property
    def allowed(self) -> bool:
        return self.account is not None and not self.blockers


def _fake_provider_account_id(owner_id: int, channel: Channel) -> str:
    return f"local-fake:{owner_id}:{channel.value}"


def ensure_fake_channel_account(
    db: Session,
    *,
    owner_id: int,
    channel: Channel,
    provider: Optional[str] = None,
) -> models.ChannelAccount:
    """Return a deterministic credential-free account for isolated fake I/O."""

    if channel == Channel.OFFLINE:
        raise ValueError("Offline evidence does not use a sender account")
    provider_name = provider or f"fake-{channel.value}"
    if not provider_name.startswith("fake-"):
        raise ValueError("A local fake account must use a fake Provider")
    provider_account_id = _fake_provider_account_id(owner_id, channel)
    account = db.query(models.ChannelAccount).filter_by(
        owner_id=owner_id,
        channel=channel,
        provider=provider_name,
        provider_account_id=provider_account_id,
    ).first()
    if account is None:
        candidate = models.ChannelAccount(
            owner_id=owner_id,
            channel=channel,
            provider=provider_name,
            provider_account_id=provider_account_id,
            enabled=True,
            health_status=ChannelAccountHealth.HEALTHY,
            health_checked_at=utcnow(),
            daily_limit=None,
            timezone="UTC",
        )
        try:
            with db.begin_nested():
                db.add(candidate)
                db.flush()
            account = candidate
        except IntegrityError:
            account = db.query(models.ChannelAccount).filter_by(
                owner_id=owner_id,
                channel=channel,
                provider=provider_name,
                provider_account_id=provider_account_id,
            ).one()
    return account


def bind_legacy_email_account(
    db: Session,
    *,
    owner_id: int,
    legacy_email_account_id: int,
    provider: str = "smtp",
    daily_limit: Optional[int] = None,
    account_timezone: str = "UTC",
    actor_user_id: Optional[int] = None,
) -> models.ChannelAccount:
    """Bind a legacy email credential by FK without copying its secret fields."""

    source = db.query(legacy.EmailAccount).filter(
        legacy.EmailAccount.id == legacy_email_account_id,
        legacy.EmailAccount.user_id == owner_id,
    ).first()
    if source is None:
        raise ValueError("Legacy email account is not owned by this owner")
    if daily_limit is not None and daily_limit < 0:
        raise ValueError("daily_limit must be non-negative")
    try:
        ZoneInfo(account_timezone)
    except ZoneInfoNotFoundError as exc:
        raise ValueError("Unknown account timezone") from exc

    account_query = db.query(models.ChannelAccount).filter_by(
        legacy_email_account_id=source.id,
    )
    if db.get_bind().dialect.name == "mysql":
        account_query = account_query.with_for_update()
    account = account_query.first()
    if account is None:
        account = models.ChannelAccount(
            owner_id=owner_id,
            channel=Channel.EMAIL,
            provider=provider,
            provider_account_id=source.email,
            legacy_email_account_id=source.id,
            enabled=True,
            health_status=ChannelAccountHealth.UNKNOWN,
            daily_limit=daily_limit,
            timezone=account_timezone,
        )
        db.add(account)
    elif account.owner_id != owner_id:
        raise ValueError("Legacy email account is already bound to another owner")
    elif (
        account.channel != Channel.EMAIL
        or account.provider != provider
        or account.provider_account_id != source.email
    ):
        raise ValueError("Channel account identity is immutable; create a new binding")
    before_policy = {"daily_limit": account.daily_limit, "timezone": account.timezone}
    account.daily_limit = daily_limit
    account.timezone = account_timezone
    _observe_health(db, account)
    db.flush()
    _audit_account_policy_change(
        db,
        account=account,
        before=before_policy,
        actor_user_id=actor_user_id,
    )
    return account


def bind_legacy_channel_account(
    db: Session,
    *,
    owner_id: int,
    legacy_channel_account_id: int,
    provider: str = "unipile",
    daily_limit: Optional[int] = None,
    account_timezone: str = "UTC",
    actor_user_id: Optional[int] = None,
) -> models.ChannelAccount:
    """Bind LinkedIn/WhatsApp by legacy FK and public Provider account id."""

    source = db.query(legacy.ChannelAccount).filter(
        legacy.ChannelAccount.id == legacy_channel_account_id,
        legacy.ChannelAccount.user_id == owner_id,
    ).first()
    if source is None:
        raise ValueError("Legacy channel account is not owned by this owner")
    channel_name = str(source.account_type or "").strip().lower()
    if channel_name not in {Channel.LINKEDIN.value, Channel.WHATSAPP.value}:
        raise ValueError("Legacy account type is not supported by Product V2")
    if daily_limit is not None and daily_limit < 0:
        raise ValueError("daily_limit must be non-negative")
    try:
        ZoneInfo(account_timezone)
    except ZoneInfoNotFoundError as exc:
        raise ValueError("Unknown account timezone") from exc

    account_query = db.query(models.ChannelAccount).filter_by(
        legacy_channel_account_id=source.id,
    )
    if db.get_bind().dialect.name == "mysql":
        account_query = account_query.with_for_update()
    account = account_query.first()
    if account is None:
        account = models.ChannelAccount(
            owner_id=owner_id,
            channel=Channel(channel_name),
            provider=provider,
            provider_account_id=source.unipile_account_id,
            legacy_channel_account_id=source.id,
            enabled=True,
            health_status=ChannelAccountHealth.UNKNOWN,
            daily_limit=daily_limit,
            timezone=account_timezone,
        )
        db.add(account)
    elif account.owner_id != owner_id:
        raise ValueError("Legacy channel account is already bound to another owner")
    elif (
        account.channel != Channel(channel_name)
        or account.provider != provider
        or account.provider_account_id != source.unipile_account_id
    ):
        raise ValueError("Channel account identity is immutable; create a new binding")
    before_policy = {"daily_limit": account.daily_limit, "timezone": account.timezone}
    account.daily_limit = daily_limit
    account.timezone = account_timezone
    _observe_health(db, account)
    db.flush()
    _audit_account_policy_change(
        db,
        account=account,
        before=before_policy,
        actor_user_id=actor_user_id,
    )
    return account


def _audit_account_policy_change(
    db: Session,
    *,
    account: models.ChannelAccount,
    before: dict,
    actor_user_id: Optional[int],
) -> None:
    after = {"daily_limit": account.daily_limit, "timezone": account.timezone}
    if before == after:
        return
    db.add(
        models.AuditEvent(
            owner_id=account.owner_id,
            actor_user_id=actor_user_id,
            action="channel_account.policy_updated",
            entity_type="channel_account",
            entity_id=str(account.id),
            before_data=before,
            after_data=after,
        )
    )


def update_channel_account_policy(
    db: Session,
    *,
    owner_id: int,
    channel_account_id: int,
    actor_user_id: int,
    enabled: Optional[bool] = None,
    daily_limit=_UNSET,
    account_timezone: Optional[str] = None,
) -> models.ChannelAccount:
    """Serialize mutable policy updates against the Provider execution gate."""

    query = db.query(models.ChannelAccount).filter(
        models.ChannelAccount.id == channel_account_id,
        models.ChannelAccount.owner_id == owner_id,
    )
    if db.get_bind().dialect.name == "mysql":
        query = query.with_for_update()
    account = query.one()
    before = {
        "enabled": account.enabled,
        "daily_limit": account.daily_limit,
        "timezone": account.timezone,
    }
    if enabled is not None:
        account.enabled = enabled
    if daily_limit is not _UNSET:
        if daily_limit is not None and (not isinstance(daily_limit, int) or daily_limit < 0):
            raise ValueError("daily_limit must be a non-negative integer or null")
        account.daily_limit = daily_limit
    if account_timezone is not None:
        try:
            ZoneInfo(account_timezone)
        except ZoneInfoNotFoundError as exc:
            raise ValueError("Unknown account timezone") from exc
        account.timezone = account_timezone
    after = {
        "enabled": account.enabled,
        "daily_limit": account.daily_limit,
        "timezone": account.timezone,
    }
    if before != after:
        db.add(
            models.AuditEvent(
                owner_id=owner_id,
                actor_user_id=actor_user_id,
                action="channel_account.policy_updated",
                entity_type="channel_account",
                entity_id=str(account.id),
                before_data=before,
                after_data=after,
            )
        )
    db.flush()
    return account


def _observe_health(
    db: Session,
    account: models.ChannelAccount,
    *,
    persist: bool = True,
) -> tuple[ChannelAccountHealth, Optional[str]]:
    """Observe only presence/status; never return or persist a credential value."""

    status = account.health_status or ChannelAccountHealth.UNKNOWN
    error: Optional[str] = None
    observed_failure = False
    if account.legacy_email_account_id is not None:
        source = db.query(legacy.EmailAccount).filter(
            legacy.EmailAccount.id == account.legacy_email_account_id,
            legacy.EmailAccount.user_id == account.owner_id,
        ).first()
        if source is None:
            status, error = ChannelAccountHealth.UNHEALTHY, "legacy_email_account_missing"
            observed_failure = True
        elif account.channel != Channel.EMAIL or source.email != account.provider_account_id:
            status, error = ChannelAccountHealth.UNHEALTHY, "legacy_email_identity_mismatch"
            observed_failure = True
        elif not all((source.smtp_host, source.smtp_user, source.smtp_pass)):
            status, error = ChannelAccountHealth.UNHEALTHY, "legacy_email_credentials_incomplete"
            observed_failure = True
    elif account.legacy_channel_account_id is not None:
        source = db.query(legacy.ChannelAccount).filter(
            legacy.ChannelAccount.id == account.legacy_channel_account_id,
            legacy.ChannelAccount.user_id == account.owner_id,
        ).first()
        expected_type = account.channel.value.upper()
        if source is None:
            status, error = ChannelAccountHealth.UNHEALTHY, "legacy_channel_account_missing"
            observed_failure = True
        elif (
            str(source.account_type or "").upper() != expected_type
            or source.unipile_account_id != account.provider_account_id
        ):
            status, error = ChannelAccountHealth.UNHEALTHY, "legacy_channel_identity_mismatch"
            observed_failure = True
        elif str(source.status or "").upper() != "OK":
            status, error = ChannelAccountHealth.UNHEALTHY, "legacy_channel_account_not_ready"
            observed_failure = True
    elif account.provider.startswith("fake-"):
        status = ChannelAccountHealth.HEALTHY
        if persist:
            account.health_checked_at = utcnow()
            account.last_error = None

    if persist:
        account.health_status = status
    if observed_failure and persist:
        account.health_checked_at = utcnow()
        account.last_error = error
    return status, error


def refresh_channel_account_health(
    db: Session,
    *,
    account: models.ChannelAccount,
) -> models.ChannelAccount:
    _observe_health(db, account)
    db.flush()
    return account


def record_trusted_channel_account_health(
    db: Session,
    *,
    account: models.ChannelAccount,
    status: ChannelAccountHealth,
    checked_at: Optional[datetime] = None,
    error_code: Optional[str] = None,
    actor_user_id: Optional[int] = None,
    source: str = "connector_probe",
    correlation_id: Optional[str] = None,
) -> models.ChannelAccount:
    """Persist and audit a trusted connector health probe, idempotently."""

    if error_code and (
        len(error_code) > 100 or re.fullmatch(r"[a-z0-9_.:-]+", error_code) is None
    ):
        raise ValueError("Health error_code must be a short non-secret code")
    if (
        not source
        or len(source) > 100
        or re.fullmatch(r"[a-zA-Z0-9_.:-]+", source) is None
    ):
        raise ValueError("Health source must be a short non-secret identifier")
    if correlation_id and (
        len(correlation_id) > 255
        or re.fullmatch(r"[a-zA-Z0-9_.:/-]+", correlation_id) is None
    ):
        raise ValueError("Health correlation_id must be a non-secret identifier")

    db.flush()
    query = db.query(models.ChannelAccount).filter(
        models.ChannelAccount.id == account.id,
        models.ChannelAccount.owner_id == account.owner_id,
    )
    if db.get_bind().dialect.name == "mysql":
        query = query.with_for_update()
    account = query.populate_existing().one()

    if correlation_id:
        replay_query = db.query(models.AuditEvent).filter_by(
            owner_id=account.owner_id,
            action="channel_account.health_recorded",
            correlation_id=correlation_id,
        ).order_by(models.AuditEvent.id.asc())
        if db.get_bind().dialect.name == "mysql":
            replay_query = replay_query.with_for_update()
        replay = replay_query.first()
        if replay is not None:
            after = replay.after_data or {}
            same_probe = (
                replay.entity_type == "channel_account"
                and replay.entity_id == str(account.id)
                and after.get("health_status") == status.value
                and after.get("error_code") == error_code
                and (replay.metadata_json or {}).get("source") == source
                and (
                    checked_at is None
                    or after.get("health_checked_at") == _health_datetime(checked_at)
                )
            )
            if not same_probe:
                raise ValueError("Health correlation_id conflicts with another probe result")
            return account

    observed_at = checked_at or utcnow()
    before = _health_audit_snapshot(account)
    after = {
        "channel_account_id": account.id,
        "health_status": status.value,
        "health_checked_at": _health_datetime(observed_at),
        "error_code": error_code,
    }
    if before == after:
        return account

    account.health_status = status
    account.health_checked_at = observed_at
    account.last_error = error_code
    db.add(
        models.AuditEvent(
            owner_id=account.owner_id,
            actor_user_id=actor_user_id,
            action="channel_account.health_recorded",
            entity_type="channel_account",
            entity_id=str(account.id),
            correlation_id=correlation_id,
            before_data=before,
            after_data=after,
            metadata_json={
                "source": source,
                "contains_credentials": False,
            },
        )
    )
    db.flush()
    return account


def _health_datetime(value: Optional[datetime]) -> Optional[str]:
    if value is None:
        return None
    normalized = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    return normalized.astimezone(timezone.utc).isoformat()


def _health_audit_snapshot(account: models.ChannelAccount) -> dict:
    raw_error = account.last_error
    safe_error = raw_error
    if raw_error and (
        len(raw_error) > 100 or re.fullmatch(r"[a-z0-9_.:-]+", raw_error) is None
    ):
        safe_error = "redacted_unsafe_legacy_error"
    status = account.health_status
    return {
        "channel_account_id": account.id,
        "health_status": status.value if hasattr(status, "value") else str(status),
        "health_checked_at": _health_datetime(account.health_checked_at),
        "error_code": safe_error,
    }


def validate_sequence_account_reference(
    db: Session,
    *,
    owner_id: int,
    channel: Channel,
    channel_account_id: int,
) -> models.ChannelAccount:
    if channel == Channel.OFFLINE:
        raise ValueError("Offline evidence does not use a sender account")
    account = db.get(models.ChannelAccount, channel_account_id)
    if account is None or account.owner_id != owner_id:
        raise ValueError("Sequence channel account is not owned by this owner")
    if account.channel != channel:
        raise ValueError("Sequence channel account does not match the step channel")
    if account.archived_at is not None:
        raise ValueError("Sequence channel account is archived")
    return account


def resolve_attempt_account(
    db: Session,
    *,
    attempt: models.OutreachAttempt,
    step: Optional[models.SequenceStep],
    connector,
) -> ChannelAccountDecision:
    """Resolve once and freeze the actual account onto the immutable Attempt."""

    account_id = attempt.channel_account_id
    if account_id is None and step is not None:
        account_id = step.channel_account_id
    if account_id is None:
        if not connector.is_fake or not fake_account_fallback_allowed():
            return ChannelAccountDecision(None, ["channel_account_binding_missing"])
        account = ensure_fake_channel_account(
            db,
            owner_id=attempt.owner_id,
            channel=attempt.channel,
            provider=connector.provider,
        )
        account_id = account.id
    account = db.get(models.ChannelAccount, account_id)
    if account is None:
        return ChannelAccountDecision(None, ["channel_account_missing"])
    if attempt.channel_account_id is not None and step and step.channel_account_id is not None:
        if attempt.channel_account_id != step.channel_account_id:
            return ChannelAccountDecision(account, ["attempt_channel_account_mismatch"])
    if attempt.channel_account_id is None:
        attempt.channel_account_id = account.id
        db.flush()
    blockers = _identity_blockers(
        account,
        owner_id=attempt.owner_id,
        channel=attempt.channel,
        provider=connector.provider,
    )
    return ChannelAccountDecision(account, blockers)


def _identity_blockers(
    account: models.ChannelAccount,
    *,
    owner_id: int,
    channel: Channel,
    provider: Optional[str] = None,
) -> list[str]:
    blockers: list[str] = []
    if account.owner_id != owner_id:
        blockers.append("channel_account_owner_mismatch")
    if account.channel != channel:
        blockers.append("channel_account_channel_mismatch")
    if provider is not None and account.provider != provider:
        blockers.append("channel_account_provider_mismatch")
    if account.archived_at is not None:
        blockers.append("channel_account_archived")
    if not account.enabled:
        blockers.append("channel_account_disabled")
    return blockers


def _capacity_window(
    account: models.ChannelAccount,
    now: datetime,
) -> tuple[Optional[datetime], Optional[datetime], Optional[str]]:
    try:
        zone = ZoneInfo(account.timezone or "UTC")
    except ZoneInfoNotFoundError:
        return None, None, "channel_account_timezone_invalid"
    current = now if now.tzinfo else now.replace(tzinfo=timezone.utc)
    local_day = current.astimezone(zone).date()
    start = datetime.combine(local_day, time.min, tzinfo=zone).astimezone(timezone.utc)
    end = datetime.combine(local_day + timedelta(days=1), time.min, tzinfo=zone).astimezone(
        timezone.utc
    )
    return start, end, None


def _capacity_reservation_ids(
    db: Session,
    *,
    account_id: int,
    started_at: datetime,
    ends_at: datetime,
    lock: bool,
) -> list[int]:
    query = db.query(models.OutreachAttempt.id).filter(
        models.OutreachAttempt.channel_account_id == account_id,
        models.OutreachAttempt.capacity_reserved_at.isnot(None),
        models.OutreachAttempt.capacity_reserved_at >= started_at,
        models.OutreachAttempt.capacity_reserved_at < ends_at,
    )
    if lock and db.get_bind().dialect.name == "mysql":
        # This is a MySQL current/locking read.  It observes reservations that
        # committed while this transaction was waiting for the account row.
        query = query.with_for_update()
    return [row[0] for row in query.order_by(models.OutreachAttempt.id.asc()).all()]


def _active_account_locks(
    db: Session,
    *,
    account: models.ChannelAccount,
    lock: bool,
) -> list[int]:
    query = db.query(models.SafetyLock.id).filter(
        models.SafetyLock.owner_id == account.owner_id,
        models.SafetyLock.active.is_(True),
        or_(
            models.SafetyLock.channel_account_id == account.id,
            models.SafetyLock.scope == SafetyLockScope.GLOBAL,
        ),
    )
    if lock and db.get_bind().dialect.name == "mysql":
        query = query.with_for_update()
    return [row[0] for row in query.all()]


def evaluate_channel_account(
    db: Session,
    *,
    account: models.ChannelAccount,
    owner_id: int,
    channel: Channel,
    provider: Optional[str] = None,
    now: Optional[datetime] = None,
    lock: bool = False,
) -> ChannelAccountDecision:
    """Evaluate the same hard gates used by readiness and execution."""

    current = now or utcnow()
    blockers = _identity_blockers(
        account,
        owner_id=owner_id,
        channel=channel,
        provider=provider,
    )
    observed, _ = _observe_health(db, account, persist=False)
    if observed != ChannelAccountHealth.HEALTHY:
        blockers.append("channel_account_unhealthy")
    elif not account.provider.startswith("fake-"):
        checked = account.health_checked_at
        if checked is None:
            blockers.append("channel_account_health_stale")
        else:
            checked_utc = checked if checked.tzinfo else checked.replace(tzinfo=timezone.utc)
            max_age = max(1, int(os.environ.get("PRODUCT_V2_ACCOUNT_HEALTH_TTL_SECONDS", "300")))
            if (current - checked_utc).total_seconds() > max_age:
                blockers.append("channel_account_health_stale")

    lock_ids = _active_account_locks(db, account=account, lock=lock)
    if lock_ids:
        blockers.append("channel_account_safety_lock")

    start, end, timezone_error = _capacity_window(account, current)
    used = 0
    remaining = None
    if timezone_error:
        blockers.append(timezone_error)
    elif account.daily_limit is not None and start is not None and end is not None:
        used = len(
            _capacity_reservation_ids(
                db,
                account_id=account.id,
                started_at=start,
                ends_at=end,
                lock=lock,
            )
        )
        remaining = max(account.daily_limit - used, 0)
        if remaining <= 0:
            blockers.append("channel_account_capacity_exhausted")
    return ChannelAccountDecision(
        account=account,
        blockers=list(dict.fromkeys(blockers)),
        observed_health=observed,
        used_capacity=used,
        remaining_capacity=remaining,
        window_started_at=start,
        window_ends_at=end,
    )


def lock_and_reserve_attempt_account(
    db: Session,
    *,
    attempt: models.OutreachAttempt,
    connector_provider: str,
    now: Optional[datetime] = None,
) -> ChannelAccountDecision:
    """MySQL row-lock, re-check, then reserve one account/day capacity slot."""

    if attempt.channel_account_id is None:
        return ChannelAccountDecision(None, ["channel_account_binding_missing"])
    query = db.query(models.ChannelAccount).filter(
        models.ChannelAccount.id == attempt.channel_account_id,
    )
    if db.get_bind().dialect.name == "mysql":
        query = query.with_for_update()
    account = query.populate_existing().first()
    if account is None:
        return ChannelAccountDecision(None, ["channel_account_missing"])
    decision = evaluate_channel_account(
        db,
        account=account,
        owner_id=attempt.owner_id,
        channel=attempt.channel,
        provider=connector_provider,
        now=now,
        lock=True,
    )
    if not decision.allowed:
        return decision
    if attempt.capacity_reserved_at is None:
        attempt.capacity_reserved_at = now or utcnow()
        db.flush()
    return decision


def create_account_safety_lock(
    db: Session,
    *,
    account: models.ChannelAccount,
    reason: str,
    code: str,
    actor_user_id: Optional[int] = None,
) -> models.SafetyLock:
    """Linearize an ACCOUNT hard stop against the execution reservation gate."""

    query = db.query(models.ChannelAccount).filter(models.ChannelAccount.id == account.id)
    if db.get_bind().dialect.name == "mysql":
        query = query.with_for_update()
    locked = query.one()
    existing = db.query(models.SafetyLock).filter_by(
        owner_id=locked.owner_id,
        scope=SafetyLockScope.ACCOUNT,
        channel_account_id=locked.id,
        code=code,
        active=True,
    ).first()
    if existing:
        return existing
    safety_lock = models.SafetyLock(
        owner_id=locked.owner_id,
        scope=SafetyLockScope.ACCOUNT,
        channel_account_id=locked.id,
        channel=locked.channel,
        code=code,
        reason=reason,
        active=True,
    )
    db.add(safety_lock)
    db.flush()
    db.add(
        models.AuditEvent(
            owner_id=locked.owner_id,
            actor_user_id=actor_user_id,
            action="safety_lock.account_created",
            entity_type="safety_lock",
            entity_id=str(safety_lock.id),
            after_data={
                "scope": SafetyLockScope.ACCOUNT.value,
                "channel_account_id": locked.id,
                "channel": locked.channel.value,
                "code": code,
            },
        )
    )
    return safety_lock


def release_attempt_capacity(
    db: Session,
    *,
    attempt_id: int,
    reason: str,
    actor_user_id: Optional[int] = None,
    confirmed_not_sent: bool = False,
) -> bool:
    """Release capacity only with durable refund or explicit not-sent proof.

    UNKNOWN is intentionally never treated as not sent.  This operation is
    idempotent and always leaves an immutable audit when it changes capacity.
    """

    query = db.query(models.OutreachAttempt).filter(models.OutreachAttempt.id == attempt_id)
    if db.get_bind().dialect.name == "mysql":
        query = query.with_for_update()
    attempt = query.populate_existing().one()
    if attempt.capacity_reserved_at is None:
        return False
    costs = db.query(models.ProviderCostEvent).filter_by(
        outreach_attempt_id=attempt.id,
    ).all()
    refunded = bool(costs) and all(cost.status == ProviderCostStatus.REFUNDED for cost in costs)
    terminal_not_sent = (
        confirmed_not_sent
        and attempt.status
        in {AttemptStatus.FAILED, AttemptStatus.BLOCKED, AttemptStatus.CANCELLED}
        and attempt.provider_message_id is None
        and not any(
            cost.status in {ProviderCostStatus.CHARGED, ProviderCostStatus.UNKNOWN}
            for cost in costs
        )
    )
    if not refunded and not terminal_not_sent:
        raise ValueError("Capacity can be released only after refund or confirmed not-sent evidence")

    prior_reserved_at = attempt.capacity_reserved_at
    attempt.capacity_reserved_at = None
    db.add(
        models.AuditEvent(
            owner_id=attempt.owner_id,
            actor_user_id=actor_user_id,
            action="channel_account.capacity_released",
            entity_type="outreach_attempt",
            entity_id=str(attempt.id),
            correlation_id=attempt.idempotency_key,
            before_data={
                "channel_account_id": attempt.channel_account_id,
                "capacity_reserved_at": prior_reserved_at.isoformat(),
            },
            after_data={
                "channel_account_id": attempt.channel_account_id,
                "capacity_reserved_at": None,
                "reason": reason,
                "refunded": refunded,
                "confirmed_not_sent": terminal_not_sent,
            },
        )
    )
    db.flush()
    return True
