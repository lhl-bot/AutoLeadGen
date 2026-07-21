"""Read persisted Product V2 operating policy at runtime.

The settings API stores immutable, versioned policy snapshots in AuditEvent.
This module is deliberately independent from FastAPI so readiness checks and
workers consume the exact same persisted policy instead of merely displaying
it in the UI.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any, Optional

from sqlalchemy import and_, func, or_
from sqlalchemy.orm import Session

import models as legacy
from product_v2 import models
from product_v2.enums import Channel, ProviderCostStatus
from product_v2.settings_schemas import (
    ProductSettingSection,
    default_setting_values,
    validate_setting_values,
)


SETTINGS_ACTION = "product_settings.updated"
SETTINGS_ENTITY = "product_setting"


@dataclass(frozen=True)
class SettingDocument:
    section: ProductSettingSection
    version: int
    values: dict[str, Any]

    @property
    def configured(self) -> bool:
        return self.version > 0


@dataclass(frozen=True)
class GlobalBudgetSnapshot:
    configured: bool
    limit: Optional[Decimal]
    used: Decimal
    remaining: Optional[Decimal]
    currency: str
    price_version: str
    unpriced_billable_events: int


def setting_document(
    db: Session,
    *,
    owner_id: int,
    section: ProductSettingSection,
    lock: bool = False,
) -> SettingDocument:
    mysql_lock = bool(lock and db.bind and db.bind.dialect.name == "mysql")
    if mysql_lock:
        # Settings writes use the same stable owner row as their durable
        # mutex.  Take it before reading the immutable AuditEvent stream so a
        # worker cannot observe the old policy after waiting for a concurrent
        # settings update to commit.
        db.query(legacy.User.id).filter(legacy.User.id == owner_id).with_for_update().one()
    query = (
        db.query(models.AuditEvent)
        .filter_by(
            owner_id=owner_id,
            action=SETTINGS_ACTION,
            entity_type=SETTINGS_ENTITY,
            entity_id=section.value,
        )
        .order_by(models.AuditEvent.id.desc())
    )
    # MySQL's normal SELECT may continue using a REPEATABLE READ snapshot that
    # predates the owner lock.  A locking read is a current read and therefore
    # sees the policy committed by the writer we just waited for.
    if mysql_lock:
        query = query.with_for_update()
    event = query.first()
    if event is None:
        return SettingDocument(section, 0, default_setting_values(section))
    stored = event.after_data or {}
    version = int(stored.get("version", 0))
    values = validate_setting_values(section, stored.get("values") or {})
    return SettingDocument(section, version, values)


def channel_policy_allows(
    db: Session,
    *,
    owner_id: int,
    channel: Channel,
    lock: bool = False,
) -> bool:
    """Honor explicit channel policy while preserving pre-policy migrations.

    Version zero means the owner has not yet published a global channel policy;
    in that migration-compatible state, the immutable Campaign Revision remains
    authoritative.  Once any channels policy is saved, its booleans become hard
    runtime gates.
    """

    document = setting_document(
        db,
        owner_id=owner_id,
        section=ProductSettingSection.CHANNELS_INTEGRATIONS,
        lock=lock,
    )
    if not document.configured or channel == Channel.OFFLINE:
        return True
    return bool(document.values.get(f"{channel.value}_enabled", False))


def review_policy_required(db: Session, *, owner_id: int, lock: bool = False) -> bool:
    document = setting_document(
        db,
        owner_id=owner_id,
        section=ProductSettingSection.CHANNELS_INTEGRATIONS,
        lock=lock,
    )
    return document.configured and bool(document.values.get("review_before_send", True))


def configured_public_unsubscribe_url(db: Session, *, owner_id: int) -> str:
    document = setting_document(
        db,
        owner_id=owner_id,
        section=ProductSettingSection.CHANNELS_INTEGRATIONS,
    )
    return str(document.values.get("public_unsubscribe_url") or "") if document.configured else ""


def _decimal(value: Any) -> Optional[Decimal]:
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    return parsed if parsed.is_finite() else None


def global_budget_snapshot(
    db: Session,
    *,
    owner_id: int,
    lock: bool = False,
) -> GlobalBudgetSnapshot:
    document = setting_document(
        db,
        owner_id=owner_id,
        section=ProductSettingSection.PROVIDERS,
        lock=lock,
    )
    limit = _decimal(document.values.get("global_budget_limit")) if document.configured else None
    if limit is not None and limit <= 0:
        limit = None
    currency = str(document.values.get("currency") or "USD").upper()
    price_version = str(document.values.get("price_version") or "local-unpriced")

    consuming = or_(
        models.ProviderCostEvent.status.in_(
            (
                ProviderCostStatus.RESERVED,
                ProviderCostStatus.CHARGED,
                ProviderCostStatus.UNKNOWN,
            )
        ),
        and_(
            models.ProviderCostEvent.status == ProviderCostStatus.FAILED,
            models.ProviderCostEvent.billable.is_(True),
        ),
    )
    base_filter = (
        models.ProviderCostEvent.owner_id == owner_id,
        models.ProviderCostEvent.billable.is_(True),
        consuming,
    )
    mysql_lock = bool(lock and db.bind and db.bind.dialect.name == "mysql")
    if mysql_lock:
        # This must be a current read, not an aggregate over the transaction's
        # earlier REPEATABLE READ snapshot.  The owner mutex serializes inserts;
        # locking the matching rows makes the visibility rule explicit and also
        # protects future code paths that update unsettled reservations.
        rows = (
            db.query(
                models.ProviderCostEvent.normalized_amount,
                models.ProviderCostEvent.normalized_currency,
            )
            .filter(*base_filter)
            .with_for_update()
            .all()
        )
        used_decimal = Decimal("0")
        unpriced = 0
        for row in rows:
            amount = _decimal(row.normalized_amount)
            row_currency = str(row.normalized_currency or "").upper()
            if amount is None or amount < 0 or row_currency != currency:
                unpriced += 1
                continue
            used_decimal += amount
    else:
        used = db.query(
            func.coalesce(func.sum(models.ProviderCostEvent.normalized_amount), 0)
        ).filter(
            *base_filter,
            models.ProviderCostEvent.normalized_currency == currency,
            models.ProviderCostEvent.normalized_amount >= 0,
        ).scalar()
        unpriced = db.query(models.ProviderCostEvent.id).filter(
            *base_filter,
            or_(
                models.ProviderCostEvent.normalized_amount.is_(None),
                models.ProviderCostEvent.normalized_amount < 0,
                models.ProviderCostEvent.normalized_currency.is_(None),
                models.ProviderCostEvent.normalized_currency != currency,
            ),
        ).count()
        used_decimal = Decimal(str(used or 0))
    remaining = None if limit is None else max(Decimal("0"), limit - used_decimal)
    return GlobalBudgetSnapshot(
        configured=document.configured,
        limit=limit,
        used=used_decimal,
        remaining=remaining,
        currency=currency,
        price_version=price_version,
        unpriced_billable_events=unpriced,
    )


def revision_unit_price(
    revision: Optional[models.CampaignRevision],
    budget: GlobalBudgetSnapshot,
) -> tuple[Optional[Decimal], Optional[str]]:
    """Return a reviewed normalized unit price or a fail-closed reason."""

    if not budget.configured or budget.limit is None:
        return None, None
    definition = revision.budget_definition if revision else {}
    unit_price = _decimal(
        definition.get("normalized_unit_price", definition.get("unit_price"))
    )
    currency = str(definition.get("currency") or "").upper()
    price_version = str(definition.get("price_version") or "")
    if unit_price is None or unit_price < 0:
        return None, "global_budget_unit_price_missing"
    if currency != budget.currency:
        return None, "global_budget_currency_mismatch"
    if price_version != budget.price_version:
        return None, "global_budget_price_version_mismatch"
    return unit_price, None
