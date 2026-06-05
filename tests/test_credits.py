import pytest

import models
from services.credits import (
    InsufficientCreditsError,
    consume_credits,
    ensure_credit_wallet,
    grant_credits,
)


def _user(db, username="owner"):
    user = models.User(username=username, hashed_password="x", is_active=True)
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def test_ensure_credit_wallet_grants_default_balance(db_session, monkeypatch):
    monkeypatch.setenv("CREDITS_DEFAULT_BALANCE", "25")
    user = _user(db_session)

    wallet = ensure_credit_wallet(db_session, user.id)
    db_session.commit()

    assert wallet.balance == 25
    assert wallet.lifetime_granted == 25
    grant_tx = db_session.query(models.CreditTransaction).one()
    assert grant_tx.amount == 25
    assert grant_tx.transaction_type == "grant"
    assert grant_tx.action == "initial_grant"


def test_consume_credits_debits_wallet_and_records_transaction(db_session, monkeypatch):
    monkeypatch.setenv("CREDITS_ENABLED", "true")
    monkeypatch.setenv("CREDITS_COST_EMAIL_SEND", "3")
    user = _user(db_session)
    grant_credits(db_session, user.id, 10)

    tx = consume_credits(
        db_session,
        user.id,
        "email_send",
        units=2,
        reference_type="lead",
        reference_id=123,
    )

    wallet = db_session.query(models.CreditWallet).filter_by(user_id=user.id).one()
    assert wallet.balance == 4
    assert wallet.lifetime_used == 6
    assert tx.amount == -6
    assert tx.balance_after == 4
    assert tx.metadata_json["unit_cost"] == 3
    assert tx.metadata_json["units"] == 2


def test_consume_credits_raises_without_mutating_wallet(db_session, monkeypatch):
    monkeypatch.setenv("CREDITS_ENABLED", "true")
    monkeypatch.setenv("CREDITS_COST_EMAIL_SEND", "5")
    user = _user(db_session)
    grant_credits(db_session, user.id, 4)

    with pytest.raises(InsufficientCreditsError) as exc:
        consume_credits(db_session, user.id, "email_send")

    assert exc.value.required == 5
    assert exc.value.balance == 4
    wallet = db_session.query(models.CreditWallet).filter_by(user_id=user.id).one()
    assert wallet.balance == 4
    assert wallet.lifetime_used == 0
