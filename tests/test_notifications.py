import models
from services import notifications as notif
from routers.notifications import list_notifications, mark_read, mark_all_read


def _user(db, name="rep"):
    u = models.User(username=name, hashed_password="x", is_active=True)
    db.add(u)
    db.flush()
    return u


def test_notify_creates_and_dedups(db_session):
    user = _user(db_session)
    a = notif.notify(db_session, user.id, "low_balance", "Low", reference_type="wallet", reference_id=user.id)
    assert a is not None
    # Same (type, reference) while unread → returns the existing one, no duplicate.
    b = notif.notify(db_session, user.id, "low_balance", "Low again", reference_type="wallet", reference_id=user.id)
    assert b.id == a.id
    count = db_session.query(models.Notification).filter_by(user_id=user.id).count()
    assert count == 1


def test_notify_no_dedup_after_read(db_session):
    user = _user(db_session)
    a = notif.notify(db_session, user.id, "send_failed", "F1", reference_type="lead", reference_id=5)
    a.is_read = True
    db_session.commit()
    # Once the prior one is read, a new alert for the same reference is allowed.
    b = notif.notify(db_session, user.id, "send_failed", "F2", reference_type="lead", reference_id=5)
    assert b.id != a.id


def test_notify_distinct_references_not_deduped(db_session):
    user = _user(db_session)
    notif.notify(db_session, user.id, "high_intent_reply", "L1", reference_type="lead", reference_id=1)
    notif.notify(db_session, user.id, "high_intent_reply", "L2", reference_type="lead", reference_id=2)
    assert db_session.query(models.Notification).filter_by(user_id=user.id).count() == 2


def test_notify_none_user_is_noop(db_session):
    assert notif.notify(db_session, None, "x", "t") is None


def test_owner_id_for_lead_via_pool_and_workflow(db_session):
    user = _user(db_session)
    pool = models.ClientPool(user_id=user.id, name="P")
    db_session.add(pool)
    db_session.flush()
    lead = models.Lead(client_pool_id=pool.id, domain="x.example")
    db_session.add(lead)
    db_session.flush()
    assert notif.owner_id_for_lead(db_session, lead) == user.id


def test_router_list_unread_count_and_mark(db_session):
    user = _user(db_session)
    notif.notify(db_session, user.id, "send_failed", "A", reference_type="lead", reference_id=1)
    notif.notify(db_session, user.id, "send_failed", "B", reference_type="lead", reference_id=2)

    data = list_notifications(limit=30, unread_only=False, db=db_session, user=user)
    assert data["unread_count"] == 2
    assert len(data["items"]) == 2

    first = data["items"][0]
    mark_read(first.id, db=db_session, user=user)
    data2 = list_notifications(limit=30, unread_only=False, db=db_session, user=user)
    assert data2["unread_count"] == 1

    res = mark_all_read(db=db_session, user=user)
    assert res["marked_read"] == 1
    data3 = list_notifications(limit=30, unread_only=False, db=db_session, user=user)
    assert data3["unread_count"] == 0


def test_router_scopes_to_user(db_session):
    owner = _user(db_session, "owner")
    other = _user(db_session, "stranger")
    notif.notify(db_session, owner.id, "low_balance", "Owner only", reference_type="wallet", reference_id=owner.id)

    data = list_notifications(limit=30, unread_only=False, db=db_session, user=other)
    assert data["unread_count"] == 0
    assert data["items"] == []
