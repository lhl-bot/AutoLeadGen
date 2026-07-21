import asyncio

import models
from routers.leads import (
    BulkLeadActionRequest,
    BulkLeadRequest,
    bulk_lead_action,
    bulk_send_reviewed_drafts,
    review_center,
)


def _seed_owner(db):
    owner = models.User(username="owner", hashed_password="x", is_active=True)
    other = models.User(username="other", hashed_password="x", is_active=True)
    db.add_all([owner, other])
    db.flush()
    owner_workflow = models.Workflow(
        user_id=owner.id,
        name="Owner workflow",
        search_keywords="distributor",
        target_positions="buyer",
    )
    other_workflow = models.Workflow(
        user_id=other.id,
        name="Other workflow",
        search_keywords="distributor",
        target_positions="buyer",
    )
    db.add_all([owner_workflow, other_workflow])
    db.flush()
    return owner, other, owner_workflow, other_workflow


def test_review_center_returns_owned_operational_queues(db_session):
    owner, _, workflow, other_workflow = _seed_owner(db_session)
    db_session.add_all([
        models.Lead(workflow_id=workflow.id, domain="draft.example", status="drafted", ai_draft="Hi"),
        models.Lead(workflow_id=workflow.id, domain="missing.example", status="needs_email"),
        models.Lead(workflow_id=workflow.id, domain="research.example", status="needs_research"),
        models.Lead(workflow_id=workflow.id, domain="failed.example", status="send_failed", ai_draft="Hi"),
        models.Lead(
            workflow_id=workflow.id,
            domain="intent.example",
            status="sent",
            handoff_recommended=True,
        ),
        models.Lead(workflow_id=other_workflow.id, domain="other.example", status="drafted"),
    ])
    db_session.commit()

    result = review_center(100, db_session, owner)

    assert result["counts"] == {
        "drafted": 1,
        "needs_email": 1,
        "needs_research": 1,
        "send_failed": 1,
        "high_intent": 1,
    }
    assert result["queues"]["drafted"][0].domain == "draft.example"
    assert result["queues"]["needs_research"][0].domain == "research.example"


def test_bulk_actions_reject_drafts_and_retry_failed_sends(db_session):
    owner, _, workflow, _ = _seed_owner(db_session)
    drafted = models.Lead(workflow_id=workflow.id, domain="draft.example", status="drafted")
    failed = models.Lead(
        workflow_id=workflow.id,
        domain="failed.example",
        email="buyer@failed.example",
        status="send_failed",
        ai_draft="Hi",
        send_fail_count=3,
    )
    db_session.add_all([drafted, failed])
    db_session.commit()

    rejected = bulk_lead_action(
        BulkLeadActionRequest(lead_ids=[drafted.id], action="reject"),
        db_session,
        owner,
    )
    retried = bulk_lead_action(
        BulkLeadActionRequest(lead_ids=[failed.id], action="retry"),
        db_session,
        owner,
    )

    assert rejected["succeeded"] == 1
    assert drafted.status == "rejected"
    assert retried["succeeded"] == 1
    assert failed.status == "drafted"
    assert failed.send_fail_count == 0


def test_bulk_send_isolates_failures_and_preserves_ownership(db_session, monkeypatch):
    owner, _, workflow, other_workflow = _seed_owner(db_session)
    sendable = models.Lead(
        workflow_id=workflow.id,
        domain="send.example",
        email="buyer@send.example",
        status="drafted",
        ai_draft="Hi",
    )
    missing_email = models.Lead(
        workflow_id=workflow.id,
        domain="missing.example",
        status="drafted",
        ai_draft="Hi",
    )
    other = models.Lead(
        workflow_id=other_workflow.id,
        domain="other.example",
        email="buyer@other.example",
        status="drafted",
        ai_draft="Hi",
    )
    db_session.add_all([sendable, missing_email, other])
    db_session.commit()

    async def fake_send(lead, _workflow, db, **kwargs):
        lead.status = "sent"
        db.commit()

    monkeypatch.setattr("services.outbound_engine.send_lead_email", fake_send)

    result = asyncio.run(bulk_send_reviewed_drafts(
        BulkLeadRequest(lead_ids=[sendable.id, missing_email.id, other.id]),
        db_session,
        owner,
    ))

    assert result["requested"] == 3
    assert result["succeeded"] == 1
    assert result["failed"] == 2
    assert sendable.status == "sent"
    assert other.status == "drafted"


def test_bulk_send_reports_send_result_message(db_session, monkeypatch):
    owner, _, workflow, _ = _seed_owner(db_session)
    blocked = models.Lead(
        workflow_id=workflow.id,
        domain="blocked.example",
        email="buyer@blocked.example",
        status="drafted",
        ai_draft="Hi",
    )
    db_session.add(blocked)
    db_session.commit()

    async def fake_send(lead, _workflow, db, **kwargs):
        return {"success": False, "message": "All active sender email accounts have reached their daily caps"}

    monkeypatch.setattr("services.outbound_engine.send_lead_email", fake_send)

    result = asyncio.run(bulk_send_reviewed_drafts(
        BulkLeadRequest(lead_ids=[blocked.id]),
        db_session,
        owner,
    ))

    assert result["succeeded"] == 0
    assert result["failed"] == 1
    assert result["results"][0]["message"] == "All active sender email accounts have reached their daily caps"
