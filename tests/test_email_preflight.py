from datetime import datetime, timezone

import pytest

import models
from services.email_preflight import (
    is_email_good_for_lead,
    is_lead_sendable_now,
    quality_gate_reason,
    temporary_send_block_reason,
    validate_lead_before_send,
)


@pytest.fixture(autouse=True)
def _disable_network_email_checks(monkeypatch):
    monkeypatch.setenv("EMAIL_CHECK_RECIPIENT_DOMAIN_DNS", "false")
    monkeypatch.setenv("EMAIL_CHECK_RECIPIENT_MX", "false")


def _lead(db, **overrides):
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
    lead_data = {
        "workflow_id": workflow.id,
        "domain": "example.com",
        "company_name": "Example",
        "email": "buyer@example.com",
        "status": "drafted",
    }
    lead_data.update(overrides)
    lead = models.Lead(**lead_data)
    db.add(lead)
    db.commit()
    db.refresh(lead)
    return user, workflow, lead


def test_validate_lead_before_send_blocks_basic_invalid_reasons(db_session):
    _, _, lead = _lead(db_session, email=None)
    assert validate_lead_before_send(lead, db_session) == "missing_email"

    lead.email = "not-an-email"
    assert validate_lead_before_send(lead, db_session) == "invalid_email_format"

    lead.email = "alex.smith@example.com"
    assert validate_lead_before_send(lead, db_session) == "suspected_generated_mock_email"

    lead.email = "buyer@example.com"
    lead.email_validation_status = "invalid"
    assert validate_lead_before_send(lead, db_session) == "email_pre_verified_invalid"


def test_validate_lead_before_send_respects_suppression(db_session):
    user, _, lead = _lead(db_session)
    db_session.add(models.EmailSuppression(
        user_id=user.id,
        lead_id=lead.id,
        email=lead.email,
        domain=lead.domain,
        reason="unsubscribe",
        source="test",
    ))
    db_session.commit()

    assert validate_lead_before_send(lead, db_session) == "suppressed:unsubscribe:buyer@example.com"


def test_quality_gate_reason_checks_score_and_verification(db_session, monkeypatch):
    _, _, lead = _lead(db_session, fit_score=40)
    monkeypatch.setenv("EMAIL_REQUIRE_MIN_FIT_SCORE", "true")
    monkeypatch.setenv("EMAIL_MIN_FIT_SCORE", "60")

    assert quality_gate_reason(lead) == "fit_score_too_low(40<60)"

    lead.fit_score = 80
    lead.email_validation_status = "unknown"
    assert quality_gate_reason(lead) == "email_not_verified(unknown)"


def test_temporary_send_block_reason_checks_working_hours(db_session):
    _, _, lead = _lead(db_session, timezone="UTC")

    reason = temporary_send_block_reason(
        lead,
        db_session,
        now=datetime(2026, 6, 5, 20, tzinfo=timezone.utc),
    )

    assert reason == "outside_working_hours"


def test_temporary_send_block_reason_checks_domain_cooldown(db_session):
    _, _, lead = _lead(db_session)
    db_session.add(models.EmailLog(
        lead_id=lead.id,
        direction="outbound",
        from_email="sender@example.com",
        to_email=lead.email,
        sent_at=datetime(2026, 6, 5, 10, tzinfo=timezone.utc),
    ))
    db_session.commit()

    reason = temporary_send_block_reason(
        lead,
        db_session,
        now=datetime(2026, 6, 5, 12, tzinfo=timezone.utc),
    )

    assert reason == "domain_cooldown"


def test_is_lead_sendable_now_combines_all_gates(db_session):
    _, _, lead = _lead(db_session, email="buyer@example.com", email_validation_status="valid")

    assert is_lead_sendable_now(lead, db_session) == (True, None)

    lead.status = "rejected"
    assert is_lead_sendable_now(lead, db_session) == (False, "lead_status_rejected")


def test_is_email_good_for_lead_rejects_junk_and_domain_mismatch(monkeypatch):
    monkeypatch.setenv("SEARCH_REQUIRE_EMAIL_DOMAIN_MATCH", "true")

    assert is_email_good_for_lead("buyer@example.com", "example.com") is True
    assert is_email_good_for_lead("noreply@example.com", "example.com") is False
    assert is_email_good_for_lead("alex.smith@example.com", "example.com") is False
    assert is_email_good_for_lead("buyer@other.com", "example.com") is False
