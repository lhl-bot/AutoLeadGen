import asyncio
from decimal import Decimal

import httpx

import models as legacy
from product_v2 import models
from product_v2.enums import (
    Channel,
    ChannelAccountHealth,
    ContactPointVerificationStatus,
    StageStatus,
    TaskPriority,
    TaskStatus,
    TaskType,
    WorkerType,
)
from product_v2.runtime.queue import heartbeat
from product_v2.runtime.worker import execute_job
from product_v2.services.channel_accounts import create_account_safety_lock
from product_v2.services.domain import utcnow
from services.auth import get_current_user


def test_production_revision_rejects_channels_without_real_connectors(db_session, monkeypatch):
    user = legacy.User(username="production-email-scope", hashed_password="x", is_active=True)
    db_session.add(user)
    db_session.commit()
    monkeypatch.setenv("AUTOLEADGEN_ENV", "production")
    import main

    main.app.dependency_overrides[get_current_user] = lambda: user

    async def flow():
        transport = httpx.ASGITransport(app=main.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            campaign = await client.post(
                "/api/v2/campaigns",
                json={"name": "Production email scope", "run_mode": "review"},
            )
            assert campaign.status_code == 201, campaign.text
            return await client.post(
                f"/api/v2/campaigns/{campaign.json()['id']}/revisions",
                json={
                    "sequence_steps": [
                        {
                            "position": 1,
                            "channel": "linkedin",
                            "template_version": "unsupported-v1",
                            "body_template": "This must never become a production step.",
                        }
                    ],
                },
            )

    try:
        response = asyncio.run(flow())
    finally:
        main.app.dependency_overrides.clear()

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "CHANNEL_UNAVAILABLE"
    assert db_session.query(models.CampaignRevision).count() == 0


def test_contacts_and_tasks_support_stable_offset_pagination(db_session):
    user = legacy.User(username="pagination-owner", hashed_password="x", is_active=True)
    db_session.add(user)
    db_session.flush()
    company = models.Company(owner_id=user.id, name="Pagination Company", normalized_domain="pagination.example")
    db_session.add(company)
    db_session.flush()
    db_session.add_all([
        models.Contact(owner_id=user.id, company_id=company.id, full_name="First Contact"),
        models.Contact(owner_id=user.id, company_id=company.id, full_name="Second Contact"),
        models.Task(
            owner_id=user.id,
            task_type=TaskType.CONTACT_ENRICHMENT_REQUIRED,
            status=TaskStatus.OPEN,
            priority=TaskPriority.HIGH,
            title="First pagination task",
        ),
        models.Task(
            owner_id=user.id,
            task_type=TaskType.RECONCILIATION,
            status=TaskStatus.OPEN,
            priority=TaskPriority.HIGH,
            title="Second pagination task",
        ),
    ])
    db_session.commit()
    import main

    main.app.dependency_overrides[get_current_user] = lambda: user

    async def flow():
        transport = httpx.ASGITransport(app=main.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return (
                await client.get("/api/v2/contacts?limit=1&offset=0"),
                await client.get("/api/v2/contacts?limit=1&offset=1"),
                await client.get("/api/v2/tasks?status=open&limit=1&offset=0"),
                await client.get("/api/v2/tasks?status=open&limit=1&offset=1"),
            )

    try:
        contact_a, contact_b, task_a, task_b = asyncio.run(flow())
    finally:
        main.app.dependency_overrides.clear()

    assert {contact_a.status_code, contact_b.status_code, task_a.status_code, task_b.status_code} == {200}
    assert contact_a.json()[0]["id"] != contact_b.json()[0]["id"]
    assert task_a.json()[0]["id"] != task_b.json()[0]["id"]


def test_company_workspace_exposes_owned_evidence_and_audited_safe_edits(db_session):
    user = legacy.User(username="customer-workspace-owner", hashed_password="x", is_active=True)
    other = legacy.User(username="customer-workspace-other", hashed_password="x", is_active=True)
    db_session.add_all([user, other])
    db_session.flush()
    company = models.Company(
        owner_id=user.id,
        name="Original Company",
        normalized_domain="original.example",
        website="https://original.example",
    )
    db_session.add(company)
    db_session.flush()
    contact = models.Contact(
        owner_id=user.id,
        company_id=company.id,
        full_name="Original Buyer",
        job_title="Buyer",
    )
    db_session.add(contact)
    db_session.flush()
    db_session.add_all([
        models.ContactPoint(
            owner_id=user.id,
            company_id=company.id,
            contact_id=contact.id,
            channel=Channel.EMAIL,
            value="buyer@original.example",
            normalized_value="buyer@original.example",
            verification_status=ContactPointVerificationStatus.VALID,
            is_primary=True,
        ),
        models.EvidenceSnapshot(
            owner_id=user.id,
            company_id=company.id,
            contact_id=contact.id,
            source="official_site",
            source_url="https://original.example/about",
            evidence={
                "company_overview": "Verified public company profile",
                "specific_products": ["Bedding", "Towels"],
                "quality_flags": ["official-site"],
            },
            confidence=Decimal("0.9500"),
        ),
    ])
    db_session.commit()
    import main

    active_user = {"value": user}
    main.app.dependency_overrides[get_current_user] = lambda: active_user["value"]

    async def flow():
        transport = httpx.ASGITransport(app=main.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            company_update = await client.patch(
                f"/api/v2/companies/{company.id}",
                json={"name": "Updated Company", "region": "Europe"},
            )
            contact_update = await client.patch(
                f"/api/v2/contacts/{contact.id}",
                json={"full_name": "Updated Buyer", "job_title": "Senior Buyer"},
            )
            workspace = await client.get(f"/api/v2/companies/{company.id}/workspace")
            active_user["value"] = other
            cross_owner = await client.get(f"/api/v2/companies/{company.id}/workspace")
            return company_update, contact_update, workspace, cross_owner

    try:
        company_update, contact_update, workspace, cross_owner = asyncio.run(flow())
    finally:
        main.app.dependency_overrides.clear()

    assert company_update.status_code == 200
    assert company_update.json()["name"] == "Updated Company"
    assert contact_update.status_code == 200
    assert contact_update.json()["full_name"] == "Updated Buyer"
    assert workspace.status_code == 200
    payload = workspace.json()
    assert payload["company"]["region"] == "Europe"
    assert payload["contacts"][0]["job_title"] == "Senior Buyer"
    assert payload["contacts"][0]["contact_points"][0]["verification_status"] == "valid"
    assert payload["evidence_snapshots"][0]["evidence"]["specific_products"] == ["Bedding", "Towels"]
    assert payload["outreach"] == {
        "enrollment_count": 0,
        "sent_count": 0,
        "reply_count": 0,
        "last_contact_at": None,
    }
    assert cross_owner.status_code == 404
    assert db_session.query(models.AuditEvent).filter_by(action="company.updated").count() == 1
    assert db_session.query(models.AuditEvent).filter_by(action="contact.updated").count() == 1


def test_email_account_binding_is_previewed_owner_scoped_and_credential_free(db_session):
    user = legacy.User(username="email-binding-owner", hashed_password="x", is_active=True)
    other = legacy.User(username="email-binding-other", hashed_password="x", is_active=True)
    db_session.add_all([user, other])
    db_session.flush()
    source = legacy.EmailAccount(
        user_id=user.id,
        email="sender@example.com",
        display_name="Sales Sender",
        smtp_host="smtp.example.com",
        smtp_port=465,
        smtp_user="sender@example.com",
        smtp_pass="DO-NOT-RETURN-THIS-SECRET",
        use_ssl=True,
        use_tls=False,
        imap_host="imap.example.com",
        imap_port=993,
    )
    foreign = legacy.EmailAccount(
        user_id=other.id,
        email="other@example.com",
        smtp_host="smtp.example.com",
        smtp_user="other@example.com",
        smtp_pass="FOREIGN-SECRET",
        use_ssl=True,
        use_tls=False,
        imap_host="imap.example.com",
    )
    db_session.add_all([source, foreign])
    db_session.commit()
    import main

    main.app.dependency_overrides[get_current_user] = lambda: user

    async def flow():
        transport = httpx.ASGITransport(app=main.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            draft = {
                "legacy_email_account_id": source.id,
                "daily_limit": 20,
                "timezone": "UTC",
            }
            preview = await client.post(
                "/api/v2/channel-accounts/email-bindings/preview",
                json=draft,
            )
            foreign_preview = await client.post(
                "/api/v2/channel-accounts/email-bindings/preview",
                json={**draft, "legacy_email_account_id": foreign.id},
            )
            applied = await client.post(
                "/api/v2/channel-accounts/email-bindings",
                headers={"Idempotency-Key": "email-bind-api-0001"},
                json={
                    **draft,
                    "preview_checksum": preview.json()["preview_checksum"],
                    "human_confirmed": True,
                },
            )
            replay = await client.post(
                "/api/v2/channel-accounts/email-bindings",
                headers={"Idempotency-Key": "email-bind-api-0001"},
                json={
                    **draft,
                    "preview_checksum": preview.json()["preview_checksum"],
                    "human_confirmed": True,
                },
            )
            conflict = await client.post(
                "/api/v2/channel-accounts/email-bindings",
                headers={"Idempotency-Key": "email-bind-api-0001"},
                json={
                    **draft,
                    "daily_limit": 21,
                    "preview_checksum": preview.json()["preview_checksum"],
                    "human_confirmed": True,
                },
            )
            listed = await client.get("/api/v2/channel-accounts")
            return preview, foreign_preview, applied, replay, conflict, listed

    try:
        preview, foreign_preview, applied, replay, conflict, listed = asyncio.run(flow())
    finally:
        main.app.dependency_overrides.clear()

    assert preview.status_code == 200
    assert preview.json()["effects"] == {
        "credential_copy_count": 0,
        "message_send_count": 0,
        "external_provider_call_count": 0,
        "outbound_hard_pause_unchanged": True,
        "health_after_binding": "unknown_until_no_send_probe",
    }
    assert foreign_preview.status_code == 404
    assert applied.status_code == 200, applied.text
    assert replay.status_code == 200
    assert applied.json()["id"] == replay.json()["id"]
    assert conflict.status_code == 409
    assert conflict.json()["detail"]["code"] == "IDEMPOTENCY_KEY_CONFLICT"
    assert listed.status_code == 200
    assert len(listed.json()) == 1
    summary = listed.json()[0]
    assert summary["address"] == source.email
    assert summary["health_status"] == "unknown"
    assert summary["credentials_configured"] is True
    assert "smtp_pass" not in summary
    assert "DO-NOT-RETURN-THIS-SECRET" not in listed.text
    account = db_session.query(models.ChannelAccount).one()
    assert account.daily_limit == 20
    assert db_session.query(models.AuditEvent).filter_by(
        action="channel_account.email_binding_applied",
        correlation_id="email-bind-api-0001",
    ).count() == 1


def test_complaint_safety_lock_release_requires_fresh_probe_and_audits_replay(
    db_session,
):
    user = legacy.User(username="complaint-lock-owner", hashed_password="x", is_active=True)
    db_session.add(user)
    db_session.flush()
    account = models.ChannelAccount(
        owner_id=user.id,
        channel=Channel.EMAIL,
        provider="smtp",
        provider_account_id="complaint-lock@example.com",
        enabled=True,
        health_status=ChannelAccountHealth.UNHEALTHY,
        health_checked_at=utcnow(),
        last_error="provider_complaint_requires_review",
        daily_limit=5,
        timezone="UTC",
    )
    db_session.add(account)
    db_session.flush()
    safety_lock = create_account_safety_lock(
        db_session,
        account=account,
        reason="Provider abuse complaint requires review",
        code="provider_complaint:123",
    )
    task = models.Task(
        owner_id=user.id,
        task_type=TaskType.DELIVERABILITY_ALERT,
        status=TaskStatus.OPEN,
        priority=TaskPriority.URGENT,
        title="Resolve complaint",
        metadata_json={"safety_lock_id": safety_lock.id},
    )
    db_session.add(task)
    db_session.commit()
    import main

    main.app.dependency_overrides[get_current_user] = lambda: user
    payload = {
        "reason": "Suppression and sender reputation review completed",
        "evidence_id": "INC-20260717-complaint-123",
        "human_confirmed": True,
    }

    async def flow():
        transport = httpx.ASGITransport(app=main.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            blocked = await client.post(
                f"/api/v2/safety-locks/{safety_lock.id}/release",
                headers={"Idempotency-Key": "release-complaint-123"},
                json=payload,
            )
            account.health_status = ChannelAccountHealth.HEALTHY
            account.health_checked_at = utcnow()
            account.last_error = None
            db_session.commit()
            released = await client.post(
                f"/api/v2/safety-locks/{safety_lock.id}/release",
                headers={"Idempotency-Key": "release-complaint-123"},
                json=payload,
            )
            replay = await client.post(
                f"/api/v2/safety-locks/{safety_lock.id}/release",
                headers={"Idempotency-Key": "release-complaint-123"},
                json=payload,
            )
            conflict = await client.post(
                f"/api/v2/safety-locks/{safety_lock.id}/release",
                headers={"Idempotency-Key": "release-complaint-123"},
                json={**payload, "evidence_id": "INC-different"},
            )
            listed = await client.get("/api/v2/safety-locks?active=false")
            return blocked, released, replay, conflict, listed

    try:
        blocked, released, replay, conflict, listed = asyncio.run(flow())
    finally:
        main.app.dependency_overrides.clear()

    assert blocked.status_code == 409
    assert blocked.json()["detail"]["code"] == "FRESH_HEALTH_PROBE_REQUIRED"
    assert released.status_code == 200
    assert released.json()["active"] is False
    assert replay.status_code == 200
    assert conflict.status_code == 409
    assert conflict.json()["detail"]["code"] == "IDEMPOTENCY_KEY_CONFLICT"
    assert [item["id"] for item in listed.json()] == [safety_lock.id]
    db_session.expire_all()
    assert db_session.get(models.Task, task.id).status == TaskStatus.COMPLETED
    audit = db_session.query(models.AuditEvent).filter_by(
        owner_id=user.id,
        action="safety_lock.released",
        correlation_id="release-complaint-123",
    ).one()
    assert audit.after_data["evidence_id"] == payload["evidence_id"]


def test_sales_handoff_task_cannot_bypass_opportunity_confirmation(db_session):
    user = legacy.User(username="handoff-task-owner", hashed_password="x", is_active=True)
    db_session.add(user)
    db_session.flush()
    task = models.Task(
        owner_id=user.id,
        task_type=TaskType.SALES_HANDOFF,
        status=TaskStatus.OPEN,
        priority=TaskPriority.HIGH,
        title="Confirm qualified sales opportunity",
    )
    db_session.add(task)
    db_session.commit()
    db_session.refresh(task)
    import main

    main.app.dependency_overrides[get_current_user] = lambda: user

    async def flow():
        transport = httpx.ASGITransport(app=main.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.patch(
                f"/api/v2/tasks/{task.id}",
                json={"status": "completed"},
            )

    try:
        response = asyncio.run(flow())
    finally:
        main.app.dependency_overrides.clear()

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "TASK_REQUIRES_OPPORTUNITY_CONFIRMATION"
    db_session.expire_all()
    assert db_session.get(models.Task, task.id).status == TaskStatus.OPEN


def test_v2_company_campaign_readiness_and_async_start_flow(db_session):
    # GIVEN: An authenticated local owner and the real ASGI stack.
    user = legacy.User(username="v2-api-owner", hashed_password="x", is_active=True)
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    import main

    main.app.dependency_overrides[get_current_user] = lambda: user

    async def flow():
        transport = httpx.ASGITransport(app=main.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            # WHEN: Creating the company-first customer and immutable campaign revision.
            invalid_company = await client.post(
                "/api/v2/companies",
                json={"name": "Invalid Domain", "domain": "http://127.0.0.1/internal"},
            )
            assert invalid_company.status_code == 422
            assert invalid_company.json()["detail"]["code"] == "INVALID_COMPANY_DOMAIN"
            company_response = await client.post(
                "/api/v2/companies",
                json={"name": "Nordic Living", "domain": "https://www.nordic.example/about"},
            )
            assert company_response.status_code == 201, company_response.text
            company_id = company_response.json()["id"]
            forged_verification = await client.post(
                "/api/v2/contacts",
                json={
                    "company_id": company_id,
                    "full_name": "Forged Verification",
                    "contact_points": [
                        {
                            "channel": "email",
                            "value": "forged@nordic.example",
                            "verification_status": "valid",
                        }
                    ],
                },
            )
            assert forged_verification.status_code == 422
            contact_response = await client.post(
                "/api/v2/contacts",
                json={
                    "company_id": company_id,
                    "full_name": "Sofia Buyer",
                    "timezone": "Europe/Stockholm",
                    "contact_points": [
                        {
                            "channel": "email",
                            "value": "SOFIA@nordic.example",
                            "availability_status": "available",
                            "is_primary": True,
                        }
                    ],
                },
            )
            assert contact_response.status_code == 201, contact_response.text
            contact_id = contact_response.json()["id"]
            assert contact_response.json()["contact_points"][0]["normalized_value"] == "sofia@nordic.example"
            assert contact_response.json()["contact_points"][0]["verification_status"] == "unverified"

            # A trusted server-side verification result is the only path that
            # may promote a newly submitted point to valid.
            point = db_session.query(models.ContactPoint).filter_by(contact_id=contact_id).one()
            point.verification_status = ContactPointVerificationStatus.VALID
            db_session.commit()

            campaign_response = await client.post(
                "/api/v2/campaigns",
                json={"name": "Nordic buyers", "run_mode": "shadow", "priority": 200},
            )
            assert campaign_response.status_code == 201, campaign_response.text
            campaign_id = campaign_response.json()["id"]
            direct_publish_bypass = await client.post(
                f"/api/v2/campaigns/{campaign_id}/revisions",
                json={
                    "quality_gates": {"min_fit_score": 60},
                    "sequence_steps": [{"position": 1, "channel": "email"}],
                    "publish": True,
                },
            )
            assert direct_publish_bypass.status_code == 422
            misspelled_gate = await client.post(
                f"/api/v2/campaigns/{campaign_id}/revisions",
                json={"quality_gates": {"minimum_fit_score": 60}},
            )
            assert misspelled_gate.status_code == 422
            disabled_hard_gate = await client.post(
                f"/api/v2/campaigns/{campaign_id}/revisions",
                json={"quality_gates": {"require_verified_contact_point": False}},
            )
            assert disabled_hard_gate.status_code == 422
            revision_response = await client.post(
                f"/api/v2/campaigns/{campaign_id}/revisions",
                json={
                    "icp_definition": {"industry": "home textile retail"},
                    "audience_definition": {"region": "Nordics"},
                    "quality_gates": {"min_fit_score": 60, "require_evidence": False, "require_timezone": True},
                    "budget_definition": {"native_limit": 50, "native_unit": "fake_calls"},
                    "stop_conditions": {"public_unsubscribe_url": "http://127.0.0.1:3000/api/unsubscribe"},
                    "sequence_steps": [
                        {"position": 1, "channel": "email", "wait_minutes": 0, "template_version": "intro-v1"}
                    ],
                },
            )
            assert revision_response.status_code == 201, revision_response.text
            assert revision_response.json()["status"] == "draft"
            assert revision_response.json()["quality_gates"]["require_verified_contact_point"] is True
            revision_id = revision_response.json()["id"]
            diff_response = await client.get(
                f"/api/v2/campaigns/{campaign_id}/revisions/{revision_id}/diff"
            )
            assert diff_response.status_code == 200, diff_response.text
            assert len(diff_response.json()["checksum"]) == 64
            publish_response = await client.post(
                f"/api/v2/campaigns/{campaign_id}/revisions/{revision_id}/publish",
                headers={"Idempotency-Key": "api-publish-0001"},
                json={
                    "base_revision_id": diff_response.json()["base_revision_id"],
                    "reviewed_diff_checksum": diff_response.json()["checksum"],
                    "human_confirmed": True,
                },
            )
            assert publish_response.status_code == 200, publish_response.text
            publish_replay = await client.post(
                f"/api/v2/campaigns/{campaign_id}/revisions/{revision_id}/publish",
                headers={"Idempotency-Key": "api-publish-0001"},
                json={
                    "base_revision_id": diff_response.json()["base_revision_id"],
                    "reviewed_diff_checksum": diff_response.json()["checksum"],
                    "human_confirmed": True,
                },
            )
            assert publish_replay.status_code == 200, publish_replay.text
            assert publish_replay.json()["id"] == publish_response.json()["id"]
            conflicting_replay = await client.post(
                f"/api/v2/campaigns/{campaign_id}/revisions/{revision_id}/publish",
                headers={"Idempotency-Key": "api-publish-0001"},
                json={
                    "base_revision_id": diff_response.json()["base_revision_id"],
                    "reviewed_diff_checksum": "0" * 64,
                    "human_confirmed": True,
                },
            )
            assert conflicting_replay.status_code == 409
            campaigns_after_publish = await client.get("/api/v2/campaigns")
            assert campaigns_after_publish.status_code == 200
            assert campaigns_after_publish.json()[0]["lifecycle"] == "ready"

            enrollment_response = await client.post(
                f"/api/v2/campaigns/{campaign_id}/enrollments",
                headers={"Idempotency-Key": "api-enrollment-0001"},
                json={"contact_id": contact_id},
            )
            assert enrollment_response.status_code == 202, enrollment_response.text

            # THEN: local fake mode creates a credential-free V2 sender identity,
            # while real worker heartbeats remain mandatory database state.
            blocked = await client.post(
                f"/api/v2/campaigns/{campaign_id}/start",
                headers={"Idempotency-Key": "api-start-0001"},
                json={"confirm_warnings": False},
            )
            assert blocked.status_code == 409
            assert blocked.json()["detail"]["code"] == "CAMPAIGN_NOT_READY"
            blocker_codes = {item["code"] for item in blocked.json()["detail"]["blockers"]}
            assert {"worker_outbound", "worker_inbox"}.issubset(blocker_codes)
            assert "channel_account_step_1" not in blocker_codes
            fake_account = db_session.query(models.ChannelAccount).filter_by(
                owner_id=user.id,
                channel=Channel.EMAIL,
                provider="fake-email",
            ).one()
            assert fake_account.legacy_email_account_id is None
            assert (
                publish_response.json()["sequence_steps"][0]["channel_account_id"]
                == fake_account.id
            )

            db_session.add(
                legacy.EmailAccount(
                    user_id=user.id,
                    email="sales@nordic.example",
                    smtp_host="fake.invalid",
                    smtp_user="sales@nordic.example",
                    smtp_pass="not-used",
                )
            )
            heartbeat(db_session, worker_name="outbound-test", worker_type=WorkerType.OUTBOUND, status=StageStatus.IDLE)
            heartbeat(db_session, worker_name="inbox-test", worker_type=WorkerType.INBOX, status=StageStatus.IDLE)
            db_session.add(
                models.StageRuntime(
                    owner_id=user.id,
                    campaign_id=campaign_id,
                    stage_name="outbound",
                    status=StageStatus.IDLE,
                    details={"source": "database"},
                )
            )
            db_session.commit()

            readiness = await client.get(f"/api/v2/campaigns/{campaign_id}/readiness")
            assert readiness.status_code == 200, readiness.text
            assert readiness.json()["ready"] is True

            stages = await client.get(f"/api/v2/runtime/stages?campaign_id={campaign_id}")
            assert stages.status_code == 200, stages.text
            assert stages.json()[0]["stage_name"] == "outbound"
            assert stages.json()[0]["status"] == "idle"

            forged_heartbeat = await client.post(
                "/api/v2/runtime/heartbeats",
                json={
                    "worker_name": "forged-browser-worker",
                    "worker_type": "outbound",
                    "status": "running",
                    "lease_seconds": 3600,
                    "details": {},
                },
            )
            assert forged_heartbeat.status_code == 405

            # WHEN: Requesting start twice with the same idempotency key.
            started = await client.post(
                f"/api/v2/campaigns/{campaign_id}/start",
                headers={"Idempotency-Key": "api-start-0002"},
                json={"confirm_warnings": False},
            )
            duplicate = await client.post(
                f"/api/v2/campaigns/{campaign_id}/start",
                headers={"Idempotency-Key": "api-start-0002"},
                json={"confirm_warnings": False},
            )

            # THEN: Both return the same durable asynchronous job.
            assert started.status_code == 202, started.text
            assert duplicate.status_code == 202, duplicate.text
            assert started.json()["job_id"] == duplicate.json()["job_id"]
            return campaign_id, started.json()["job_id"], company_id

    try:
        campaign_id, job_id, company_id = asyncio.run(flow())
    finally:
        main.app.dependency_overrides.clear()

    db_session.expire_all()
    job = db_session.get(models.AutomationJob, job_id)
    execute_job(db_session, job)
    db_session.commit()
    campaign = db_session.get(models.Campaign, campaign_id)
    assert campaign.lifecycle.value == "running"

    # A replay remains idempotent after the first job changed lifecycle state, while
    # a distinct start command cannot start an already-running Campaign.
    main.app.dependency_overrides[get_current_user] = lambda: user

    async def replay_after_execution():
        transport = httpx.ASGITransport(app=main.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            replayed = await client.post(
                f"/api/v2/campaigns/{campaign_id}/start",
                headers={"Idempotency-Key": "api-start-0002"},
                json={"confirm_warnings": False},
            )
            invalid_new_command = await client.post(
                f"/api/v2/campaigns/{campaign_id}/start",
                headers={"Idempotency-Key": "api-start-0003"},
                json={"confirm_warnings": False},
            )
            return replayed, invalid_new_command

    try:
        replayed, invalid_new_command = asyncio.run(replay_after_execution())
    finally:
        main.app.dependency_overrides.clear()
    assert replayed.status_code == 202, replayed.text
    assert replayed.json()["job_id"] == job_id
    assert invalid_new_command.status_code == 409
    assert invalid_new_command.json()["detail"]["code"] == "INVALID_CAMPAIGN_TRANSITION"

    # Archiving the company never removes immutable jobs, attempts, messages, costs, or audit.
    company = db_session.get(models.Company, company_id)
    company.archived_at = company.created_at
    db_session.commit()
    assert db_session.get(models.AutomationJob, job_id) is not None
    assert db_session.query(models.AuditEvent).count() >= 4


def test_consent_restriction_scope_validation_and_normalized_idempotency(db_session):
    user = legacy.User(username="consent-api-owner", hashed_password="x", is_active=True)
    db_session.add(user)
    db_session.flush()
    company = models.Company(
        owner_id=user.id,
        name="Consent Buyer",
        normalized_domain="consent-buyer.example",
    )
    db_session.add(company)
    db_session.flush()
    contact = models.Contact(owner_id=user.id, company_id=company.id, full_name="Consent Buyer")
    db_session.add(contact)
    db_session.flush()
    point = models.ContactPoint(
        owner_id=user.id,
        company_id=company.id,
        contact_id=contact.id,
        channel="email",
        value="buyer@consent-buyer.example",
        normalized_value="buyer@consent-buyer.example",
        verification_status="valid",
    )
    db_session.add(point)
    db_session.commit()
    import main

    main.app.dependency_overrides[get_current_user] = lambda: user

    async def flow():
        transport = httpx.ASGITransport(app=main.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            contact_with_channel = await client.post(
                "/api/v2/consent-restrictions",
                headers={"Idempotency-Key": "consent-invalid-channel"},
                json={
                    "scope": "contact",
                    "contact_id": contact.id,
                    "channel": "email",
                    "reason": "No contact",
                },
            )
            contact_with_extra_target = await client.post(
                "/api/v2/consent-restrictions",
                headers={"Idempotency-Key": "consent-invalid-target"},
                json={
                    "scope": "contact",
                    "contact_id": contact.id,
                    "company_id": company.id,
                    "reason": "No contact",
                },
            )
            point_channel_mismatch = await client.post(
                "/api/v2/consent-restrictions",
                headers={"Idempotency-Key": "consent-channel-mismatch"},
                json={
                    "scope": "contact_point",
                    "contact_point_id": point.id,
                    "channel": "linkedin",
                    "reason": "No email",
                },
            )
            created = await client.post(
                "/api/v2/consent-restrictions",
                headers={"Idempotency-Key": "consent-normalized-replay"},
                json={
                    "scope": "contact_point",
                    "contact_point_id": point.id,
                    "reason": "  No email  ",
                    "source": "  manual  ",
                },
            )
            replayed = await client.post(
                "/api/v2/consent-restrictions",
                headers={"Idempotency-Key": "consent-normalized-replay"},
                json={
                    "scope": "contact_point",
                    "contact_point_id": point.id,
                    "channel": "email",
                    "reason": "No email",
                    "source": "manual",
                },
            )
            conflict = await client.post(
                "/api/v2/consent-restrictions",
                headers={"Idempotency-Key": "consent-normalized-replay"},
                json={
                    "scope": "contact_point",
                    "contact_point_id": point.id,
                    "reason": "A different command",
                },
            )
            company_without_confirmation = await client.post(
                "/api/v2/consent-restrictions",
                headers={"Idempotency-Key": "consent-company-unconfirmed"},
                json={
                    "scope": "company",
                    "company_id": company.id,
                    "reason": "No company contact",
                },
            )
            global_as_non_admin = await client.post(
                "/api/v2/consent-restrictions",
                headers={"Idempotency-Key": "consent-global-non-admin"},
                json={"scope": "global", "reason": "Stop all"},
            )
            contact_created = await client.post(
                "/api/v2/consent-restrictions",
                headers={"Idempotency-Key": "consent-contact-valid"},
                json={"scope": "contact", "contact_id": contact.id, "reason": "Stop all channels"},
            )
            return (
                contact_with_channel,
                contact_with_extra_target,
                point_channel_mismatch,
                created,
                replayed,
                conflict,
                company_without_confirmation,
                global_as_non_admin,
                contact_created,
            )

    try:
        responses = asyncio.run(flow())
    finally:
        main.app.dependency_overrides.clear()

    (
        contact_with_channel,
        contact_with_extra_target,
        point_channel_mismatch,
        created,
        replayed,
        conflict,
        company_without_confirmation,
        global_as_non_admin,
        contact_created,
    ) = responses
    assert contact_with_channel.status_code == 422
    assert contact_with_extra_target.status_code == 422
    assert point_channel_mismatch.status_code == 422
    assert point_channel_mismatch.json()["detail"]["code"] == "CONTACT_POINT_CHANNEL_MISMATCH"
    assert created.status_code == replayed.status_code == 201
    assert created.json()["id"] == replayed.json()["id"]
    assert created.json()["channel"] == "email"
    assert created.json()["reason"] == "No email"
    assert conflict.status_code == 409
    assert conflict.json()["detail"]["code"] == "IDEMPOTENCY_KEY_CONFLICT"
    assert company_without_confirmation.status_code == 409
    assert company_without_confirmation.json()["detail"]["code"] == "COMPANY_SCOPE_CONFIRMATION_REQUIRED"
    assert global_as_non_admin.status_code == 403
    assert contact_created.status_code == 201
    assert contact_created.json()["channel"] is None
