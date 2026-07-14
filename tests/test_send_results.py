import models
from services.send_results import record_send_failure, record_send_success


def _lead(db, *, status="drafted", send_fail_count=0):
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
        status=status,
        send_fail_count=send_fail_count,
    )
    db.add(lead)
    db.commit()
    db.refresh(lead)
    return lead


def test_record_send_success_marks_sent_and_creates_email_log(db_session):
    lead = _lead(db_session, send_fail_count=2)

    email_log = record_send_success(
        db_session,
        lead,
        from_email="sender@example.com",
        subject="Hello",
        body="Body",
        message_id="<msg@example.com>",
    )

    db_session.refresh(lead)
    assert lead.status == "sent"
    assert lead.send_fail_count == 0
    assert email_log.id is not None
    assert email_log.lead_id == lead.id
    assert email_log.from_email == "sender@example.com"
    assert email_log.to_email == lead.email
    assert email_log.message_id == "<msg@example.com>"


def test_record_send_failure_increments_without_changing_status_until_threshold(db_session):
    lead = _lead(db_session, status="drafted", send_fail_count=1)

    update = record_send_failure(db_session, lead, message="SMTP authentication failed")

    db_session.refresh(lead)
    assert update.fail_count == 2
    assert update.permanently_failed is False
    assert lead.status == "drafted"
    assert lead.send_fail_count == 2
    assert lead.reply_snippet == "Send failed (attempt 2/3): SMTP authentication failed"


def test_record_send_failure_marks_permanent_at_threshold(db_session):
    lead = _lead(db_session, status="drafted", send_fail_count=2)

    update = record_send_failure(db_session, lead)

    db_session.refresh(lead)
    assert update.fail_count == 3
    assert update.permanently_failed is True
    assert lead.status == "send_failed"
