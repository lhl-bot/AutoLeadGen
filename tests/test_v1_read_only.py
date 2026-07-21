import asyncio

import httpx


def test_legacy_write_guard_blocks_business_mutations_but_not_consent(monkeypatch):
    # GIVEN: The local Product V2 cutover has placed legacy business APIs in read-only mode.
    monkeypatch.setenv("PRODUCT_V2_LEGACY_READ_ONLY", "true")
    import main

    async def run():
        transport = httpx.ASGITransport(app=main.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            # WHEN: A legacy page tries to mutate a business resource.
            blocked = await client.post("/api/personas", json={"name": "Legacy write"})

            # THEN: The API returns a stable read-only error before auth or routing.
            assert blocked.status_code == 409
            assert blocked.json()["detail"]["code"] == "LEGACY_API_READ_ONLY"

            # WHEN: A nominal GET endpoint would lazily create a credit wallet.
            writeful_get = await client.get("/api/credits/me")

            # THEN: Method naming cannot bypass the read-only policy.
            assert writeful_get.status_code == 409
            assert writeful_get.json()["detail"]["code"] == "LEGACY_API_READ_ONLY"

            # WHEN: legacy GET endpoints request a provider sync or live probe.
            effectful_gets = [
                "/api/channels/accounts?sync=true",
                "/api/api-usage/summary",
                "/api/health/status?external=1",
                "/api/workflows/18/health",
            ]

            # THEN: verb-shaped side effects cannot cross the read-only boundary.
            for endpoint in effectful_gets:
                response = await client.get(endpoint)
                assert response.status_code == 409, endpoint
                assert response.json()["detail"]["code"] == "LEGACY_API_READ_ONLY"

            # Pure local legacy reads are still available for migration comparison.
            harmless_get = await client.get("/api/channels/accounts?sync=false")
            assert harmless_get.status_code != 409

            # WHEN: A recipient posts an invalid unsubscribe confirmation.
            consent = await client.post("/api/unsubscribe/not-a-valid-token")

            # THEN: The consent endpoint is reachable and performs its own validation.
            assert consent.status_code == 400
            assert consent.json()["detail"] == "Invalid unsubscribe link"

    asyncio.run(run())
