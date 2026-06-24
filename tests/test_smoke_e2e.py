"""End-to-end smoke test: exercises the real HTTP stack (routing, auth, response
models) for the features added this session, wired together as a user would hit them.

Auth is bypassed via a dependency override so we focus on routing/serialization.
Driven through httpx.ASGITransport (the installed starlette/httpx combo can't use
the classic TestClient)."""
import asyncio

import httpx

import models
from services.auth import get_current_user


def test_full_feature_flow_over_http(db_session):
    user = models.User(username="e2e", hashed_password="x", is_active=True, is_admin=False)
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)

    import main
    main.app.dependency_overrides[get_current_user] = lambda: user

    transport = httpx.ASGITransport(app=main.app)

    async def run():
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            await _flow(client, db_session)

    try:
        asyncio.run(run())
    finally:
        main.app.dependency_overrides.clear()


async def _flow(client, db_session):
    if True:
        # 1. Persona
        r = await client.post("/api/personas/", json={"name": "Importers"})
        assert r.status_code == 200, r.text
        persona_id = r.json()["id"]

        # 2. Email account
        r = await client.post("/api/email_accounts/", json={
            "email": "sales@acme.com", "smtp_host": "smtp.acme.com", "smtp_port": 587,
            "smtp_user": "sales@acme.com", "smtp_pass": "secretpw",
        })
        assert r.status_code == 200, r.text

        # 3. Client pool
        r = await client.post("/api/client_pools/", json={"name": "EU Buyers"})
        assert r.status_code == 200, r.text
        pool_id = r.json()["id"]

        # 4. Email template with A/B variant
        r = await client.post("/api/email_templates/", json={
            "name": "Opener", "body": "Hi {{first_name}} at {{company_name}}",
            "ab_group": "q3", "variant_label": "A",
        })
        assert r.status_code == 200, r.text
        template_id = r.json()["id"]

        # 5. Workflow with follow-up sequence + template (new fields)
        r = await client.post("/api/workflows/", json={
            "name": "Q3 Outreach", "search_keywords": "distributor", "target_positions": "buyer",
            "client_pool_id": pool_id, "persona_id": persona_id, "auto_followup": True,
            "followup_steps": [{"day_offset": 3}, {"day_offset": 7, "instruction": "share case study"}],
            "template_id": template_id, "email_account_ids": [],
        })
        assert r.status_code == 200, r.text
        wf = r.json()
        wf_id = wf["id"]
        assert wf["template_id"] == template_id
        assert len(wf["followup_steps"]) == 2

        # 6. Workflow detail — the 500-fix path; must not leak SMTP secret
        r = await client.get(f"/api/workflows/{wf_id}")
        assert r.status_code == 200, r.text
        assert "secretpw" not in r.text

        # 7. Onboarding progress
        r = await client.get("/api/analytics/onboarding")
        assert r.status_code == 200, r.text
        assert r.json()["total"] == 6

        # 8. Template stats list + preview with unknown-variable detection
        r = await client.get("/api/email_templates/")
        assert r.status_code == 200, r.text
        assert r.json()[0]["reply_rate"] == 0.0
        r = await client.post("/api/email_templates/preview", json={"subject": "Hi {{first_name}}", "body": "{{company_name}} {{oops}}"})
        assert r.status_code == 200, r.text
        assert r.json()["subject"] == "Hi Alex"
        assert "oops" in r.json()["unknown_variables"]

        # 9. Pipeline board — empty, then with a moved lead
        r = await client.get("/api/leads/board")
        assert r.status_code == 200, r.text
        assert r.json()["total_leads"] == 0

        lead = models.Lead(client_pool_id=pool_id, domain="buyer.example", email="p@buyer.example", status="found")
        db_session.add(lead)
        db_session.commit()
        db_session.refresh(lead)

        r = await client.post(f"/api/leads/{lead.id}/stage", json={"stage": "won"})
        assert r.status_code == 200, r.text
        r = await client.get("/api/leads/board")
        assert r.json()["totals"]["won"] == 1

        # 10. Bulk set_stage
        r = await client.post("/api/leads/bulk/action", json={"lead_ids": [lead.id], "action": "set_stage", "target_stage": "interested"})
        assert r.status_code == 200, r.text
        assert r.json()["succeeded"] == 1

        # 11. CRM webhook CRUD (created last so no delivery threads fire mid-test)
        r = await client.post("/api/crm_webhooks/", json={"name": "Zap", "url": "https://example.com/hook", "events": "lead.won", "secret": "shh"})
        assert r.status_code == 200, r.text
        assert r.json()["has_secret"] is True
        assert "secret" not in r.json()
        r = await client.get("/api/crm_webhooks/")
        assert r.status_code == 200 and len(r.json()) == 1

        # 12. Notifications endpoint
        r = await client.get("/api/notifications/")
        assert r.status_code == 200, r.text
        assert "unread_count" in r.json()

        # 13. Invalid inputs are rejected (validation wired through HTTP)
        assert (await client.post(f"/api/leads/{lead.id}/stage", json={"stage": "bogus"})).status_code == 400
        assert (await client.post("/api/crm_webhooks/", json={"name": "X", "url": "ftp://nope", "events": "lead.won"})).status_code == 422
