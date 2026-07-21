import pytest

import models
from scripts.backfill_legacy_customer_data import plan_backfill


def _workflow(*, email_paused: bool = True) -> models.Workflow:
    return models.Workflow(
        id=18,
        user_id=1,
        name="test",
        status="active",
        email_sending_paused=email_paused,
        search_keywords="bedding",
        target_positions="buyer",
    )


def test_plan_backfill_uses_only_unambiguous_existing_evidence(db_session):
    db_session.add(_workflow())
    source = models.Lead(
        id=1,
        workflow_id=18,
        company_name="Acme Home",
        domain="acme.co.uk",
        email="buyer@acme.co.uk",
        email_validation_status="valid",
        email_verified=True,
        source_channel="import",
        first_name="Ada",
        last_name="Buyer",
        job_title="Buyer",
    )
    target = models.Lead(
        id=2,
        workflow_id=18,
        company_name="Acme Home",
        domain=None,
        email=None,
        email_validation_status=None,
        source_channel="import",
        first_name="Ben",
        last_name="Buyer",
        job_title="Buyer",
    )
    malformed = models.Lead(
        id=3,
        workflow_id=18,
        company_name="Broken Contact",
        domain="broken.example",
        email="two addresses@example.com",
        email_validation_status="valid",
        email_verified=True,
        source_channel="import",
    )
    brief = models.LeadBrief(
        lead=source,
        company_overview="Acme sells bedding to UK households.",
        specific_products="duvet covers and bed sheets",
        personalization_hook="Acme recently expanded its duvet-cover collection.",
        research_status="valid",
        evidence_sources=[{"type": "official_website", "value": "https://acme.co.uk"}],
    )
    db_session.add_all([source, target, malformed, brief])
    db_session.flush()

    plan = plan_backfill(db_session)

    assert target.domain == "acme.co.uk"
    assert target.timezone == "Europe/London"
    assert target.data_sources == "import"
    assert target.email_validation_status == "no_email"
    assert target.fit_score is not None
    assert target.fit_grade in {"A", "B", "C", "D"}
    assert target.brief is not None
    assert target.brief.research_status == "valid"
    assert plan.counters["domain"] == 1
    assert plan.counters["strict_valid_brief"] == 1
    assert plan.counters["malformed_email_safely_invalidated"] == 1
    assert malformed.email_validation_status == "invalid"
    assert malformed.email_verified is False
    assert plan.affected_lead_ids == [1, 2, 3]


def test_plan_backfill_refuses_active_unpaused_workflow(db_session):
    db_session.add(_workflow(email_paused=False))
    db_session.add(models.Lead(id=1, workflow_id=18, source_channel="import"))
    db_session.flush()

    with pytest.raises(RuntimeError, match="not email-paused"):
        plan_backfill(db_session)
