from unittest.mock import patch

import pytest

import models, schemas
from services import crm_webhooks as cw
from routers.crm_webhooks import create_webhook, update_webhook, list_webhooks, delete_webhook


def _user(db, name="rep"):
    u = models.User(username=name, hashed_password="x", is_active=True)
    db.add(u)
    db.flush()
    return u


def _lead(db, **kw):
    pool = models.ClientPool(user_id=kw.pop("user_id"), name="P")
    db.add(pool)
    db.flush()
    lead = models.Lead(client_pool_id=pool.id, domain="acme.com", company_name="Acme", **kw)
    db.add(lead)
    db.flush()
    return lead


def test_parse_events():
    assert cw.parse_events("lead.won, lead.interested ,") == ["lead.won", "lead.interested"]
    assert cw.parse_events(None) == []


def test_sign_is_stable_hmac():
    body = b'{"a":1}'
    assert cw.sign(None, body) is None
    sig = cw.sign("topsecret", body)
    assert sig == cw.sign("topsecret", body)
    assert sig != cw.sign("other", body)


def test_dispatch_only_fires_subscribed_active_webhooks(db_session):
    user = _user(db_session)
    lead = _lead(db_session, user_id=user.id)
    db_session.add_all([
        models.CrmWebhook(user_id=user.id, name="won-hook", url="https://crm.example/won", events="lead.won", is_active=True),
        models.CrmWebhook(user_id=user.id, name="lost-hook", url="https://crm.example/lost", events="lead.lost", is_active=True),
        models.CrmWebhook(user_id=user.id, name="inactive", url="https://crm.example/x", events="lead.won", is_active=False),
    ])
    db_session.commit()

    with patch("services.crm_webhooks.threading.Thread") as Thread:
        scheduled = cw.dispatch_event(db_session, user.id, "lead.won", lead)
    assert scheduled == 1  # only the active won-hook
    assert Thread.called


def test_dispatch_noop_without_user(db_session):
    lead = _lead(db_session, user_id=_user(db_session).id)
    assert cw.dispatch_event(db_session, None, "lead.won", lead) == 0


def test_create_hides_secret_but_reports_has_secret(db_session):
    user = _user(db_session)
    wh = create_webhook(
        schemas.CrmWebhookCreate(name="H", url="https://crm.example/hook", events="lead.won", secret="s3cr3t"),
        db=db_session, user=user,
    )
    assert wh.has_secret is True
    # The read schema must not expose the raw secret.
    assert "secret" not in wh.model_dump()


def test_update_preserves_secret_when_blank(db_session):
    user = _user(db_session)
    created = create_webhook(
        schemas.CrmWebhookCreate(name="H", url="https://crm.example/hook", events="lead.won", secret="keepme"),
        db=db_session, user=user,
    )
    # Update without a secret should not wipe the stored one.
    update_webhook(
        created.id,
        schemas.CrmWebhookCreate(name="H2", url="https://crm.example/hook", events="lead.won,lead.lost", secret=None),
        db=db_session, user=user,
    )
    row = db_session.query(models.CrmWebhook).filter_by(id=created.id).first()
    assert row.secret == "keepme"
    assert row.name == "H2"
    assert "lead.lost" in row.events


def test_url_validation_rejects_non_http():
    with pytest.raises(Exception):
        schemas.CrmWebhookCreate(name="H", url="ftp://nope", events="lead.won")


def test_list_scoped_to_user(db_session):
    owner = _user(db_session, "owner")
    other = _user(db_session, "stranger")
    db_session.add(models.CrmWebhook(user_id=owner.id, name="H", url="https://x.example", events="lead.won"))
    db_session.commit()
    assert list_webhooks(db=db_session, user=other) == []
    assert len(list_webhooks(db=db_session, user=owner)) == 1
