import pytest
from fastapi import HTTPException

import models
from services import sales_stages as ss
from routers.leads import (
    sales_board, set_lead_stage, bulk_lead_action,
    LeadStageRequest, BulkLeadActionRequest,
)


def _seed(db, username="rep"):
    user = models.User(username=username, hashed_password="x", is_active=True)
    db.add(user)
    db.flush()
    pool = models.ClientPool(user_id=user.id, name="Pool")
    db.add(pool)
    db.flush()
    return user, pool


def _lead(db, pool, **kw):
    lead = models.Lead(client_pool_id=pool.id, domain=kw.pop("domain", "x.example"), **kw)
    db.add(lead)
    db.flush()
    return lead


def test_effective_stage_derives_from_status():
    assert ss.effective_stage("new", "replied") == "interested"
    assert ss.effective_stage(None, "sent") == "contacted"
    assert ss.effective_stage(None, "found") == "new"
    assert ss.effective_stage("bounced".replace("bounced", "won"), "found") == "won"  # explicit wins


def test_effective_stage_explicit_overrides_status():
    # An explicit non-default stage always wins over status-derived.
    assert ss.effective_stage("quoting", "replied") == "quoting"


def test_board_groups_by_effective_stage(db_session):
    user, pool = _seed(db_session)
    _lead(db_session, pool, email="a@x.example", status="found")        # new
    _lead(db_session, pool, email="b@x.example", status="sent")         # contacted
    _lead(db_session, pool, email="c@x.example", status="replied")      # interested
    _lead(db_session, pool, email="d@x.example", status="found", sales_stage="won")  # explicit won

    board = sales_board(pool_id=pool.id, workflow_id=None, limit_per_stage=100, db=db_session, user=user)
    assert board["totals"]["new"] == 1
    assert board["totals"]["contacted"] == 1
    assert board["totals"]["interested"] == 1
    assert board["totals"]["won"] == 1
    assert board["total_leads"] == 4
    assert board["stages"][0] == "new"


def test_set_lead_stage_validates_and_persists(db_session):
    user, pool = _seed(db_session)
    lead = _lead(db_session, pool, email="a@x.example", status="found")

    result = set_lead_stage(lead.id, LeadStageRequest(stage="quoting"), db=db_session, user=user)
    assert result["sales_stage"] == "quoting"
    db_session.refresh(lead)
    assert lead.sales_stage == "quoting"

    with pytest.raises(HTTPException) as exc:
        set_lead_stage(lead.id, LeadStageRequest(stage="bogus"), db=db_session, user=user)
    assert exc.value.status_code == 400


def test_set_stage_rejects_unowned_lead(db_session):
    user, pool = _seed(db_session)
    _other, other_pool = _seed(db_session, username="stranger")
    foreign = _lead(db_session, other_pool, email="f@x.example", status="found")

    with pytest.raises(HTTPException) as exc:
        set_lead_stage(foreign.id, LeadStageRequest(stage="won"), db=db_session, user=user)
    assert exc.value.status_code == 404


def test_bulk_set_stage(db_session):
    user, pool = _seed(db_session)
    a = _lead(db_session, pool, email="a@x.example", status="found")
    b = _lead(db_session, pool, email="b@x.example", status="found")

    result = bulk_lead_action(
        BulkLeadActionRequest(lead_ids=[a.id, b.id], action="set_stage", target_stage="interested"),
        db=db_session, user=user,
    )
    assert result["succeeded"] == 2
    db_session.refresh(a)
    assert a.sales_stage == "interested"

    with pytest.raises(HTTPException):
        bulk_lead_action(
            BulkLeadActionRequest(lead_ids=[a.id], action="set_stage", target_stage="nope"),
            db=db_session, user=user,
        )
