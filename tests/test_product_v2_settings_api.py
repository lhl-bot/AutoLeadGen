import asyncio
import time

import httpx

import models as legacy
from product_v2 import models
from product_v2.settings_api import _effective_locks
from services.auth import get_current_user


def _run_settings_flow(app, calls):
    async def flow():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return await calls(client)

    return asyncio.run(flow())


def test_effective_locks_read_live_control_file(tmp_path, monkeypatch):
    control = tmp_path / "outbound_hard_pause"
    control.write_text("true\n", encoding="utf-8")
    monkeypatch.setenv("AUTOLEADGEN_ENV", "production")
    monkeypatch.setenv("AUTOLEADGEN_CONNECTOR_MODE", "real")
    monkeypatch.setenv("ALLOW_REAL_EXTERNAL_CALLS", "true")
    monkeypatch.delenv("OUTBOUND_HARD_PAUSE", raising=False)
    monkeypatch.setenv("OUTBOUND_HARD_PAUSE_FILE", str(control))

    paused = _effective_locks()
    assert paused["outbound_hard_pause"] is True
    assert paused["real_external_calls_allowed"] is False

    control.write_text("false\n", encoding="utf-8")
    released = _effective_locks()
    assert released["outbound_hard_pause"] is False
    assert released["real_external_calls_allowed"] is True


def test_v2_settings_require_preview_support_versioning_and_replay(db_session):
    # GIVEN: An authenticated Product V2 owner with no saved operating policy.
    user = legacy.User(username="v2-settings-owner", hashed_password="x", is_active=True)
    db_session.add(user)
    db_session.commit()
    import main

    main.app.dependency_overrides[get_current_user] = lambda: user

    async def calls(client):
        defaults = await client.get("/api/v2/settings/icp_playbook")
        unconfirmed = await client.put(
            "/api/v2/settings/icp_playbook",
            headers={"Idempotency-Key": "settings-unconfirmed-1"},
            json={
                "expected_version": 0,
                "impact_preview_confirmed": False,
                "values": {},
            },
        )
        request = {
            "expected_version": 0,
            "impact_preview_confirmed": True,
            "values": {
                "summary": "EU apparel retailers with evidence-backed sourcing demand",
                "target_industries": ["apparel retail"],
                "target_roles": ["sourcing director"],
                "evidence_requirements": ["current supplier or assortment evidence"],
                "playbook_notes": "Lead with verified assortment evidence.",
                "proposal_status": "published",
            },
        }
        saved = await client.put(
            "/api/v2/settings/icp_playbook",
            headers={"Idempotency-Key": "settings-save-0001"},
            json=request,
        )
        replay = await client.put(
            "/api/v2/settings/icp_playbook",
            headers={"Idempotency-Key": "settings-save-0001"},
            json=request,
        )
        stale = await client.put(
            "/api/v2/settings/icp_playbook",
            headers={"Idempotency-Key": "settings-save-0002"},
            json={**request, "values": {**request["values"], "playbook_notes": "Changed"}},
        )
        return defaults, unconfirmed, saved, replay, stale

    try:
        defaults, unconfirmed, saved, replay, stale = _run_settings_flow(main.app, calls)
    finally:
        main.app.dependency_overrides.clear()

    # THEN: Defaults are explicit, writes require human preview confirmation,
    # replays are stable, and stale previews fail closed.
    assert defaults.status_code == 200
    assert defaults.json()["version"] == 0
    assert defaults.json()["effective_locks"]["real_external_calls_allowed"] is False
    assert unconfirmed.status_code == 422
    assert saved.status_code == 200, saved.text
    assert saved.json()["version"] == 1
    assert saved.json()["values"]["proposal_status"] == "published"
    assert replay.status_code == 200
    assert replay.json()["version"] == 1
    assert stale.status_code == 409
    assert stale.json()["detail"]["code"] == "SETTINGS_VERSION_CONFLICT"
    assert db_session.query(models.AuditEvent).filter_by(
        action="product_settings.updated",
        entity_id="icp_playbook",
    ).count() == 1
    assert db_session.query(models.AutomationJob).filter_by(
        idempotency_key="settings-save-0001"
    ).count() == 1


def test_v2_settings_reject_credentials_and_locked_policy_downgrades(db_session):
    # GIVEN: A local owner editing channel and permission policy documents.
    user = legacy.User(username="v2-settings-safety", hashed_password="x", is_active=True)
    db_session.add(user)
    db_session.commit()
    import main

    main.app.dependency_overrides[get_current_user] = lambda: user

    async def calls(client):
        secret = await client.put(
            "/api/v2/settings/channels_integrations",
            headers={"Idempotency-Key": "settings-secret-01"},
            json={
                "expected_version": 0,
                "impact_preview_confirmed": True,
                "values": {"api_token": "must-not-be-stored"},
            },
        )
        downgrade = await client.put(
            "/api/v2/settings/permissions",
            headers={"Idempotency-Key": "settings-policy-01"},
            json={
                "expected_version": 0,
                "impact_preview_confirmed": True,
                "values": {
                    "paid_actions_require_confirmation": True,
                    "bulk_mutations_require_confirmation": True,
                    "opportunity_requires_human_confirmation": False,
                    "review_mode_send_requires_confirmation": True,
                    "role_policy_notes": "",
                },
            },
        )
        return secret, downgrade

    try:
        secret, downgrade = _run_settings_flow(main.app, calls)
    finally:
        main.app.dependency_overrides.clear()

    # THEN: No plaintext credential or hard human-confirmation rule can be persisted.
    assert secret.status_code == 422
    assert secret.json()["detail"]["code"] == "CREDENTIALS_NOT_ACCEPTED"
    assert downgrade.status_code == 422
    assert db_session.query(models.AuditEvent).filter_by(action="product_settings.updated").count() == 0


def test_v2_production_settings_reject_unavailable_channels(db_session, monkeypatch):
    user = legacy.User(username="v2-production-channel-scope", hashed_password="x", is_active=True)
    db_session.add(user)
    db_session.commit()
    monkeypatch.setenv("AUTOLEADGEN_ENV", "production")
    import main

    main.app.dependency_overrides[get_current_user] = lambda: user

    async def calls(client):
        return await client.put(
            "/api/v2/settings/channels_integrations",
            headers={"Idempotency-Key": "settings-channel-scope-01"},
            json={
                "expected_version": 0,
                "impact_preview_confirmed": True,
                "values": {
                    "email_enabled": True,
                    "linkedin_enabled": True,
                    "whatsapp_enabled": False,
                    "review_before_send": True,
                },
            },
        )

    try:
        response = _run_settings_flow(main.app, calls)
    finally:
        main.app.dependency_overrides.clear()

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "CHANNEL_UNAVAILABLE"
    assert db_session.query(models.AuditEvent).filter_by(action="product_settings.updated").count() == 0
    assert db_session.query(models.AutomationJob).filter_by(
        idempotency_key="settings-channel-scope-01"
    ).count() == 0


def test_v2_settings_reject_credentials_embedded_in_text_without_receipts(db_session):
    # GIVEN: An authenticated owner pastes credential-shaped content into otherwise
    # valid free-text policy fields.
    user = legacy.User(username="v2-settings-text-secret", hashed_password="x", is_active=True)
    db_session.add(user)
    db_session.commit()
    import main

    main.app.dependency_overrides[get_current_user] = lambda: user

    async def calls(client):
        api_key = await client.put(
            "/api/v2/settings/channels_integrations",
            headers={"Idempotency-Key": "settings-text-secret-01"},
            json={
                "expected_version": 0,
                "impact_preview_confirmed": True,
                "values": {"integration_notes": "temporary api_key=sk-test-secret"},
            },
        )
        bearer = await client.put(
            "/api/v2/settings/providers",
            headers={"Idempotency-Key": "settings-text-secret-02"},
            json={
                "expected_version": 0,
                "impact_preview_confirmed": True,
                "values": {"provider_policy_notes": "Authorization: Bearer token"},
            },
        )
        return api_key, bearer

    try:
        api_key, bearer = _run_settings_flow(main.app, calls)
    finally:
        main.app.dependency_overrides.clear()

    # WHEN/THEN: Both commands fail before an immutable audit event or durable
    # idempotency receipt can be written.
    assert api_key.status_code == 422
    assert api_key.json()["detail"]["code"] == "CREDENTIALS_NOT_ACCEPTED"
    assert bearer.status_code == 422
    assert bearer.json()["detail"]["code"] == "CREDENTIALS_NOT_ACCEPTED"
    assert db_session.query(models.AuditEvent).filter_by(action="product_settings.updated").count() == 0
    assert db_session.query(models.AutomationJob).filter(
        models.AutomationJob.idempotency_key.in_(
            ["settings-text-secret-01", "settings-text-secret-02"]
        )
    ).count() == 0


def test_v2_settings_allocate_one_version_when_distinct_commands_race(
    db_session,
    monkeypatch,
):
    # GIVEN: Two independently idempotent commands were previewed from version 0.
    user = legacy.User(username="v2-settings-race", hashed_password="x", is_active=True)
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    db_session.expunge(user)
    import main
    from product_v2 import settings_api

    main.app.dependency_overrides[get_current_user] = lambda: user
    original_latest_event = settings_api._latest_event

    def delayed_latest_event(*args, **kwargs):
        event = original_latest_event(*args, **kwargs)
        # Widen the stale-read window so this test deterministically exercises
        # the compare-and-write critical section instead of merely issuing two
        # requests near each other.
        time.sleep(0.05)
        return event

    monkeypatch.setattr(settings_api, "_latest_event", delayed_latest_event)

    async def calls(client):
        async def update(key, notes):
            return await client.put(
                "/api/v2/settings/providers",
                headers={"Idempotency-Key": key},
                json={
                    "expected_version": 0,
                    "impact_preview_confirmed": True,
                    "values": {
                        "global_budget_limit": 250,
                        "currency": "USD",
                        "price_version": "local-v1",
                        "paid_miss_requires_review": True,
                        "provider_policy_notes": notes,
                    },
                },
            )

        return await asyncio.gather(
            update("settings-race-command-01", "first reviewed policy"),
            update("settings-race-command-02", "second reviewed policy"),
        )

    try:
        responses = _run_settings_flow(main.app, calls)
    finally:
        main.app.dependency_overrides.clear()

    # WHEN/THEN: Version comparison and allocation are atomic: one command owns
    # version 1 and the other receives a stale-preview conflict without a receipt.
    assert sorted(response.status_code for response in responses) == [200, 409]
    success = next(response for response in responses if response.status_code == 200)
    conflict = next(response for response in responses if response.status_code == 409)
    assert success.json()["version"] == 1
    assert conflict.json()["detail"]["code"] == "SETTINGS_VERSION_CONFLICT"
    assert db_session.query(models.AuditEvent).filter_by(
        action="product_settings.updated",
        entity_id="providers",
    ).count() == 1
    assert db_session.query(models.AutomationJob).filter(
        models.AutomationJob.idempotency_key.in_(
            ["settings-race-command-01", "settings-race-command-02"]
        )
    ).count() == 1
