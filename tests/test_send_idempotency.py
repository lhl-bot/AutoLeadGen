from datetime import datetime, timezone, timedelta

import models
from services.outbound_engine import _has_recent_outbound


def _lead_with_log(db, *, log_age_seconds=None, direction="outbound"):
    user = models.User(username="owner", hashed_password="x", is_active=True)
    db.add(user)
    db.flush()
    workflow = models.Workflow(
        user_id=user.id,
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
    db.flush()
    if log_age_seconds is not None:
        db.add(models.EmailLog(
            lead_id=lead.id,
            direction=direction,
            from_email="sender@example.com",
            to_email=lead.email,
            subject="Hi",
            body="Body",
            sent_at=datetime.now(timezone.utc) - timedelta(seconds=log_age_seconds),
        ))
    db.commit()
    db.refresh(lead)
    return lead


def test_recent_outbound_blocks_within_window(db_session):
    lead = _lead_with_log(db_session, log_age_seconds=30)
    assert _has_recent_outbound(lead.id, db_session, window_seconds=600) is True


def test_old_outbound_does_not_block(db_session):
    # A send from well before the window (e.g. a legitimate prior touch) must not block.
    lead = _lead_with_log(db_session, log_age_seconds=3600)
    assert _has_recent_outbound(lead.id, db_session, window_seconds=600) is False


def test_no_prior_send_does_not_block(db_session):
    lead = _lead_with_log(db_session, log_age_seconds=None)
    assert _has_recent_outbound(lead.id, db_session, window_seconds=600) is False


def test_inbound_reply_does_not_block_sending(db_session):
    # An inbound reply is not an outbound send and must never suppress a send.
    lead = _lead_with_log(db_session, log_age_seconds=30, direction="inbound")
    assert _has_recent_outbound(lead.id, db_session, window_seconds=600) is False


def test_zero_window_disables_guard(db_session):
    lead = _lead_with_log(db_session, log_age_seconds=1)
    assert _has_recent_outbound(lead.id, db_session, window_seconds=0) is False
