import pytest
from fastapi import HTTPException

import models
from routers.leads import _verify_lead_ownership, delete_lead


def _seed_users(db):
    owner = models.User(username="owner", hashed_password="x", is_active=True)
    other = models.User(username="other", hashed_password="x", is_active=True)
    admin = models.User(username="admin", hashed_password="x", is_active=True, is_admin=True)
    db.add_all([owner, other, admin])
    db.flush()
    return owner, other, admin


def _seed_workflow_lead(db, owner):
    workflow = models.Workflow(
        user_id=owner.id,
        name="Outreach",
        status="active",
        search_keywords="padel distributor",
        target_positions="buyer",
    )
    db.add(workflow)
    db.flush()
    lead = models.Lead(
        workflow_id=workflow.id,
        domain="example.com",
        company_name="Example",
        email="buyer@example.com",
        status="drafted",
    )
    db.add(lead)
    db.commit()
    db.refresh(lead)
    return workflow, lead


def test_verify_lead_ownership_allows_owner_and_admin(db_session):
    owner, other, admin = _seed_users(db_session)
    _, lead = _seed_workflow_lead(db_session, owner)

    assert _verify_lead_ownership(lead.id, db_session, owner).id == lead.id
    assert _verify_lead_ownership(lead.id, db_session, admin).id == lead.id

    with pytest.raises(HTTPException) as exc:
        _verify_lead_ownership(lead.id, db_session, other)
    assert exc.value.status_code == 404


def test_delete_lead_removes_child_records(db_session):
    owner, _, _ = _seed_users(db_session)
    _, lead = _seed_workflow_lead(db_session, owner)
    db_session.add_all([
        models.EmailLog(
            lead_id=lead.id,
            direction="outbound",
            from_email="sender@example.com",
            to_email=lead.email,
        ),
        models.MessageLog(lead_id=lead.id, channel="linkedin", direction="outbound"),
        models.LeadBrief(lead_id=lead.id, company_overview="Brief"),
        models.LeadFeedback(user_id=owner.id, lead_id=lead.id, rating="positive"),
    ])
    db_session.commit()

    result = delete_lead(lead.id, db_session, owner)

    assert result == {"ok": True}
    assert db_session.get(models.Lead, lead.id) is None
    assert db_session.query(models.EmailLog).filter_by(lead_id=lead.id).count() == 0
    assert db_session.query(models.MessageLog).filter_by(lead_id=lead.id).count() == 0
    assert db_session.query(models.LeadBrief).filter_by(lead_id=lead.id).count() == 0
    assert db_session.query(models.LeadFeedback).filter_by(lead_id=lead.id).count() == 0
