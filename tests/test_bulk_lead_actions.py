import pytest
from fastapi import HTTPException

import models
from routers.leads import bulk_lead_action, BulkLeadActionRequest


def _seed(db, username="rep"):
    user = models.User(username=username, hashed_password="x", is_active=True)
    db.add(user)
    db.flush()
    pool = models.ClientPool(user_id=user.id, name="Pool A")
    db.add(pool)
    db.flush()
    return user, pool


def _add_lead(db, pool, **kwargs):
    lead = models.Lead(client_pool_id=pool.id, domain=kwargs.pop("domain", "x.example"), **kwargs)
    db.add(lead)
    db.flush()
    return lead


def test_bulk_score_sets_fit_score(db_session):
    user, pool = _seed(db_session)
    lead = _add_lead(db_session, pool, email="a@x.example", email_validation_status="valid")

    result = bulk_lead_action(
        BulkLeadActionRequest(lead_ids=[lead.id], action="score"),
        db=db_session, user=user,
    )
    assert result["succeeded"] == 1
    db_session.refresh(lead)
    assert lead.fit_score is not None
    assert lead.fit_grade


def test_bulk_blacklist_suppresses_and_rejects(db_session):
    user, pool = _seed(db_session)
    lead = _add_lead(db_session, pool, email="b@x.example", domain="x.example")

    result = bulk_lead_action(
        BulkLeadActionRequest(lead_ids=[lead.id], action="blacklist"),
        db=db_session, user=user,
    )
    assert result["succeeded"] == 1
    db_session.refresh(lead)
    assert lead.status == "rejected"
    sup = db_session.query(models.EmailSuppression).filter_by(email="b@x.example").first()
    assert sup is not None


def test_bulk_move_pool_requires_owned_target(db_session):
    user, pool = _seed(db_session)
    other_user, other_pool = _seed(db_session, username="stranger")
    lead = _add_lead(db_session, pool, email="c@x.example")

    # Moving into a pool the user does not own must be rejected.
    with pytest.raises(HTTPException) as exc:
        bulk_lead_action(
            BulkLeadActionRequest(lead_ids=[lead.id], action="move_pool", target_pool_id=other_pool.id),
            db=db_session, user=user,
        )
    assert exc.value.status_code == 404

    dest = models.ClientPool(user_id=user.id, name="Pool B")
    db_session.add(dest)
    db_session.flush()
    result = bulk_lead_action(
        BulkLeadActionRequest(lead_ids=[lead.id], action="move_pool", target_pool_id=dest.id),
        db=db_session, user=user,
    )
    assert result["succeeded"] == 1
    db_session.refresh(lead)
    assert lead.client_pool_id == dest.id


def test_bulk_delete_removes_leads(db_session):
    user, pool = _seed(db_session)
    lead = _add_lead(db_session, pool, email="d@x.example")
    lead_id = lead.id

    result = bulk_lead_action(
        BulkLeadActionRequest(lead_ids=[lead_id], action="delete"),
        db=db_session, user=user,
    )
    assert result["succeeded"] == 1
    assert db_session.query(models.Lead).filter_by(id=lead_id).first() is None


def test_bulk_action_ignores_unowned_leads(db_session):
    user, pool = _seed(db_session)
    other_user, other_pool = _seed(db_session, username="stranger")
    foreign_lead = _add_lead(db_session, other_pool, email="e@x.example")

    result = bulk_lead_action(
        BulkLeadActionRequest(lead_ids=[foreign_lead.id], action="delete"),
        db=db_session, user=user,
    )
    assert result["failed"] == 1
    assert db_session.query(models.Lead).filter_by(id=foreign_lead.id).first() is not None
