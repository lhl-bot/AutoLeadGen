import os
from dataclasses import dataclass
from typing import Any, Optional

from sqlalchemy.orm import Session

import models


ACTION_COSTS: dict[str, int] = {
    "ai_email_draft": 2,
    "ai_reply_draft": 2,
    "email_send": 1,
    "linkedin_invite": 2,
    "whatsapp_message": 2,
}


@dataclass
class InsufficientCreditsError(Exception):
    user_id: int
    action: str
    required: int
    balance: int

    def __str__(self) -> str:
        return (
            f"Insufficient credits for {self.action}: "
            f"required {self.required}, balance {self.balance}"
        )


def credits_enabled() -> bool:
    raw = os.environ.get("CREDITS_ENABLED", "true").strip().lower()
    return raw not in {"0", "false", "no", "off"}


def default_initial_balance() -> int:
    try:
        return max(0, int(os.environ.get("CREDITS_DEFAULT_BALANCE", "100")))
    except (TypeError, ValueError):
        return 100


def get_credit_cost(action: str) -> int:
    env_name = f"CREDITS_COST_{action.upper()}"
    try:
        return max(0, int(os.environ.get(env_name, ACTION_COSTS.get(action, 0))))
    except (TypeError, ValueError):
        return ACTION_COSTS.get(action, 0)


def pricing_table() -> dict[str, int]:
    return {action: get_credit_cost(action) for action in ACTION_COSTS}


def ensure_credit_wallet(
    db: Session,
    user_id: int,
    *,
    initial_balance: Optional[int] = None,
    lock: bool = False,
) -> models.CreditWallet:
    query = db.query(models.CreditWallet).filter(models.CreditWallet.user_id == user_id)
    if lock:
        query = query.with_for_update()
    wallet = query.first()
    if wallet:
        return wallet

    balance = default_initial_balance() if initial_balance is None else max(0, int(initial_balance))
    wallet = models.CreditWallet(
        user_id=user_id,
        balance=balance,
        lifetime_granted=balance,
        lifetime_used=0,
    )
    db.add(wallet)
    db.flush()

    if balance > 0:
        db.add(models.CreditTransaction(
            wallet_id=wallet.id,
            user_id=user_id,
            amount=balance,
            balance_after=balance,
            transaction_type="grant",
            action="initial_grant",
            description="Initial credit balance",
            reference_type="system",
        ))

    return wallet


def get_credit_summary(db: Session, user_id: int) -> dict[str, Any]:
    wallet = ensure_credit_wallet(db, user_id)
    return {
        "user_id": user_id,
        "balance": wallet.balance,
        "lifetime_granted": wallet.lifetime_granted,
        "lifetime_used": wallet.lifetime_used,
        "pricing": pricing_table(),
        "credits_enabled": credits_enabled(),
        "updated_at": wallet.updated_at,
    }


def list_credit_transactions(
    db: Session,
    user_id: int,
    *,
    limit: int = 50,
    offset: int = 0,
) -> list[models.CreditTransaction]:
    ensure_credit_wallet(db, user_id)
    return (
        db.query(models.CreditTransaction)
        .filter(models.CreditTransaction.user_id == user_id)
        .order_by(models.CreditTransaction.id.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )


def _add_transaction(
    db: Session,
    wallet: models.CreditWallet,
    *,
    amount: int,
    transaction_type: str,
    action: str,
    description: Optional[str] = None,
    reference_type: Optional[str] = None,
    reference_id: Optional[str] = None,
    metadata: Optional[dict[str, Any]] = None,
    created_by_user_id: Optional[int] = None,
) -> models.CreditTransaction:
    wallet.balance += amount
    if amount > 0 and transaction_type in {"grant", "refund", "adjustment"}:
        wallet.lifetime_granted += amount
    if amount < 0:
        wallet.lifetime_used += abs(amount)

    tx = models.CreditTransaction(
        wallet_id=wallet.id,
        user_id=wallet.user_id,
        amount=amount,
        balance_after=wallet.balance,
        transaction_type=transaction_type,
        action=action,
        description=description,
        reference_type=reference_type,
        reference_id=str(reference_id) if reference_id is not None else None,
        metadata_json=metadata,
        created_by_user_id=created_by_user_id,
    )
    db.add(tx)
    return tx


def consume_credits(
    db: Session,
    user_id: int,
    action: str,
    *,
    units: int = 1,
    description: Optional[str] = None,
    reference_type: Optional[str] = None,
    reference_id: Optional[str] = None,
    metadata: Optional[dict[str, Any]] = None,
    commit: bool = True,
) -> Optional[models.CreditTransaction]:
    if not credits_enabled():
        return None

    unit_cost = get_credit_cost(action)
    required = unit_cost * max(1, units)
    if required <= 0:
        return None

    wallet = ensure_credit_wallet(db, user_id, initial_balance=0, lock=True)
    if wallet.balance < required:
        raise InsufficientCreditsError(
            user_id=user_id,
            action=action,
            required=required,
            balance=wallet.balance,
        )

    tx = _add_transaction(
        db,
        wallet,
        amount=-required,
        transaction_type="debit",
        action=action,
        description=description,
        reference_type=reference_type,
        reference_id=reference_id,
        metadata={**(metadata or {}), "unit_cost": unit_cost, "units": max(1, units)},
    )
    if commit:
        db.commit()
    return tx


def refund_credits(
    db: Session,
    user_id: int,
    action: str,
    *,
    amount: Optional[int] = None,
    units: int = 1,
    description: Optional[str] = None,
    reference_type: Optional[str] = None,
    reference_id: Optional[str] = None,
    metadata: Optional[dict[str, Any]] = None,
    commit: bool = True,
) -> Optional[models.CreditTransaction]:
    if not credits_enabled():
        return None

    refund_amount = amount if amount is not None else get_credit_cost(action) * max(1, units)
    refund_amount = max(0, int(refund_amount or 0))
    if refund_amount <= 0:
        return None

    wallet = ensure_credit_wallet(db, user_id, initial_balance=0, lock=True)
    tx = _add_transaction(
        db,
        wallet,
        amount=refund_amount,
        transaction_type="refund",
        action=action,
        description=description or "Refunded credits",
        reference_type=reference_type,
        reference_id=reference_id,
        metadata=metadata,
    )
    if commit:
        db.commit()
    return tx


def grant_credits(
    db: Session,
    user_id: int,
    amount: int,
    *,
    action: str = "manual_grant",
    description: Optional[str] = None,
    created_by_user_id: Optional[int] = None,
    metadata: Optional[dict[str, Any]] = None,
    commit: bool = True,
) -> models.CreditTransaction:
    amount = int(amount)
    if amount == 0:
        raise ValueError("Credit adjustment amount cannot be zero")

    wallet = ensure_credit_wallet(db, user_id, initial_balance=0, lock=True)
    if wallet.balance + amount < 0:
        raise InsufficientCreditsError(
            user_id=user_id,
            action=action,
            required=abs(amount),
            balance=wallet.balance,
        )

    tx = _add_transaction(
        db,
        wallet,
        amount=amount,
        transaction_type="grant" if amount > 0 else "adjustment",
        action=action,
        description=description or ("Manual credit grant" if amount > 0 else "Manual credit adjustment"),
        reference_type="admin",
        created_by_user_id=created_by_user_id,
        metadata=metadata,
    )
    if commit:
        db.commit()
    return tx
