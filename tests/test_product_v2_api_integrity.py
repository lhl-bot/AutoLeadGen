"""GWT regression coverage for V2 database-integrity API responses."""

import asyncio

import httpx
from sqlalchemy.exc import IntegrityError

import models as legacy
from product_v2 import api as v2_api
from services.auth import get_current_user


def test_create_resource_flush_conflicts_are_structured_409(db_session, monkeypatch):
    # GIVEN: An authenticated owner and resources protected by V2 unique
    # constraints.
    user = legacy.User(username="v2-integrity-owner", hashed_password="x", is_active=True)
    db_session.add(user)
    db_session.commit()
    import main

    main.app.dependency_overrides[get_current_user] = lambda: user

    async def create_duplicates():
        transport = httpx.ASGITransport(app=main.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            company_payload = {
                "name": "Unique buyer",
                "domain": "unique-buyer.example",
            }
            campaign_payload = {"name": "Unique campaign", "run_mode": "shadow"}

            # WHEN: Company and Campaign inserts violate their owner-scoped
            # uniqueness constraints during the route's explicit flush.
            assert (await client.post("/api/v2/companies", json=company_payload)).status_code == 201
            company_conflict = await client.post("/api/v2/companies", json=company_payload)
            campaign = await client.post("/api/v2/campaigns", json=campaign_payload)
            assert campaign.status_code == 201
            campaign_conflict = await client.post("/api/v2/campaigns", json=campaign_payload)
            return company_conflict, campaign_conflict, campaign.json()["id"]

    try:
        company_conflict, campaign_conflict, campaign_id = asyncio.run(create_duplicates())

        # A revision number collision originates inside the domain service before
        # the route reaches its commit helper.  Simulate that exact SQLAlchemy
        # boundary to verify the route applies the same response contract.
        def revision_flush_conflict(*args, **kwargs):
            raise IntegrityError("INSERT", {}, Exception("duplicate revision number"))

        monkeypatch.setattr(v2_api, "create_campaign_revision", revision_flush_conflict)

        async def create_conflicting_revision():
            transport = httpx.ASGITransport(app=main.app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                return await client.post(
                    f"/api/v2/campaigns/{campaign_id}/revisions",
                    json={"sequence_steps": [{"position": 1, "channel": "email"}]},
                )

        revision_conflict = asyncio.run(create_conflicting_revision())
    finally:
        main.app.dependency_overrides.clear()

    # THEN: No flush-time IntegrityError escapes as a 500, and every affected
    # create route returns the same safe, structured conflict response.
    for response in (company_conflict, campaign_conflict, revision_conflict):
        assert response.status_code == 409
        assert response.json() == {
            "detail": {
                "code": "CONFLICT",
                "message": (
                    "The request conflicts with an existing resource or relational constraint"
                ),
            }
        }

