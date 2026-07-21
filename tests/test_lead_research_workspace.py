import asyncio

import httpx

import models
from main import app
from services.auth import get_current_user


def test_lead_workspace_filters_research_and_email_status_and_returns_full_brief(db_session):
    owner = models.User(username="research-owner", hashed_password="x", is_active=True)
    db_session.add(owner)
    db_session.flush()
    workflow = models.Workflow(
        user_id=owner.id,
        name="Research workspace",
        search_keywords="home textiles",
        target_positions="buyer",
    )
    db_session.add(workflow)
    db_session.flush()

    ready = models.Lead(
        workflow_id=workflow.id,
        domain="ready.example",
        company_name="Ready Buyer",
        email="buyer@ready.example",
        email_validation_status="valid",
        status="found",
    )
    pending = models.Lead(
        workflow_id=workflow.id,
        domain="pending.example",
        company_name="Pending Buyer",
        email="buyer@pending.example",
        email_validation_status="unknown",
        status="found",
    )
    missing = models.Lead(
        workflow_id=workflow.id,
        domain="missing.example",
        company_name="Missing Buyer",
        email_validation_status="no_email",
        status="needs_email",
    )
    db_session.add_all([ready, pending, missing])
    db_session.flush()
    db_session.add_all([
        models.LeadBrief(
            lead_id=ready.id,
            company_overview="Verified public company overview",
            pain_points="Qualification hypotheses",
            value_proposition_alignment="Potential alignment",
            specific_products="Sheets; Duvet Covers",
            personalization_hook="Reference the bedding catalog",
            research_status="valid",
            quality_flags=["public_web:evidence_first"],
            evidence_sources=[{
                "type": "official_website",
                "value": "https://ready.example",
            }],
        ),
        models.LeadBrief(
            lead_id=pending.id,
            company_overview="Recorded company identity",
            pain_points="Qualification pending",
            value_proposition_alignment="No fit claim yet",
            research_status="insufficient",
            quality_flags=["public_web:company_unresolved"],
            evidence_sources=[{
                "type": "legacy_database_record",
                "value": f"lead_record:{pending.id}",
            }],
        ),
    ])
    db_session.commit()

    app.dependency_overrides[get_current_user] = lambda: owner

    async def request(path: str):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.get(path)

    try:
        filtered = asyncio.run(request(
            "/api/leads?research_status=valid&email_status=valid&contact_history=never_contacted"
        ))
        no_brief = asyncio.run(request("/api/leads?research_status=missing&email_status=no_email"))
        brief = asyncio.run(request(f"/api/leads/{ready.id}/brief"))
    finally:
        app.dependency_overrides.clear()

    assert filtered.status_code == 200
    assert [item["id"] for item in filtered.json()] == [ready.id]
    assert no_brief.status_code == 200
    assert [item["id"] for item in no_brief.json()] == [missing.id]
    assert brief.status_code == 200
    assert brief.json()["research_status"] == "valid"
    assert brief.json()["quality_flags"] == ["public_web:evidence_first"]
    assert brief.json()["evidence_sources"] == [{
        "type": "official_website",
        "value": "https://ready.example",
    }]
