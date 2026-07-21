from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import httpx

import models as legacy
from product_v2 import models
from product_v2.enums import (
    AttemptStatus,
    CampaignLifecycle,
    CampaignRevisionStatus,
    CampaignRunMode,
    Channel,
    ChannelAccountHealth,
    ContactPointVerificationStatus,
    EnrollmentStatus,
    TaskPriority,
    TaskStatus,
    TaskType,
)
from services.auth import get_current_user


def _seed_reviewable_route(db_session, *, channel: Channel = Channel.EMAIL):
    user = legacy.User(username=f"route-owner-{channel.value}", hashed_password="x", is_active=True)
    db_session.add(user)
    db_session.flush()
    company = models.Company(owner_id=user.id, name="Route Company", normalized_domain="route.example")
    db_session.add(company)
    db_session.flush()
    contact = models.Contact(owner_id=user.id, company_id=company.id, full_name="Route Buyer")
    db_session.add(contact)
    db_session.flush()
    address = "buyer@route.example" if channel == Channel.EMAIL else "+8613800138000"
    contact_point = models.ContactPoint(
        owner_id=user.id,
        company_id=company.id,
        contact_id=contact.id,
        channel=channel,
        value=address,
        normalized_value=address,
        verification_status=ContactPointVerificationStatus.VALID,
        is_primary=True,
    )
    account = models.ChannelAccount(
        owner_id=user.id,
        channel=channel,
        provider="test-provider",
        provider_account_id=f"account-{channel.value}",
        enabled=True,
        health_status=ChannelAccountHealth.HEALTHY,
    )
    campaign = models.Campaign(
        owner_id=user.id,
        name=f"Route campaign {channel.value}",
        lifecycle=CampaignLifecycle.RUNNING,
        run_mode=CampaignRunMode.REVIEW,
        published_revision_number=1,
    )
    db_session.add_all([contact_point, account, campaign])
    db_session.flush()
    revision = models.CampaignRevision(
        owner_id=user.id,
        campaign_id=campaign.id,
        revision_number=1,
        status=CampaignRevisionStatus.PUBLISHED,
    )
    evidence = models.EvidenceSnapshot(
        owner_id=user.id,
        company_id=company.id,
        contact_id=contact.id,
        source="official_site",
        evidence={"signal": "verified"},
        confidence=Decimal("0.9500"),
    )
    db_session.add_all([revision, evidence])
    db_session.flush()
    step = models.SequenceStep(
        owner_id=user.id,
        campaign_revision_id=revision.id,
        channel_account_id=account.id,
        position=1,
        channel=channel,
        body_template="Original body",
    )
    enrollment = models.Enrollment(
        owner_id=user.id,
        campaign_id=campaign.id,
        campaign_revision_id=revision.id,
        company_id=company.id,
        contact_id=contact.id,
        status=EnrollmentStatus.BLOCKED,
        paused_reason="review_approval_required",
    )
    db_session.add_all([step, enrollment])
    db_session.flush()
    attempt = models.OutreachAttempt(
        owner_id=user.id,
        campaign_id=campaign.id,
        enrollment_id=enrollment.id,
        sequence_step_id=step.id,
        contact_point_id=contact_point.id,
        channel_account_id=account.id,
        channel=channel,
        idempotency_key=f"route-attempt-{channel.value}",
        status=AttemptStatus.BLOCKED,
        last_error="review_approval_required",
    )
    db_session.add(attempt)
    db_session.flush()
    db_session.add(
        models.Task(
            owner_id=user.id,
            task_type=TaskType.DRAFT_REVIEW,
            status=TaskStatus.OPEN,
            priority=TaskPriority.HIGH,
            title="Review route",
            campaign_id=campaign.id,
            enrollment_id=enrollment.id,
            attempt_id=attempt.id,
        )
    )
    db_session.commit()
    return user, contact_point, account, enrollment, step, attempt, evidence


def test_review_batch_edit_invalidates_checksum_and_atomic_repreview_approves(db_session):
    user, contact_point, account, enrollment, step, attempt, evidence = _seed_reviewable_route(db_session)
    import main

    main.app.dependency_overrides[get_current_user] = lambda: user

    async def flow():
        transport = httpx.ASGITransport(app=main.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            scheduled_at = datetime.now(timezone.utc) + timedelta(hours=2)
            proposal = await client.post(
                "/api/v2/route-proposals",
                json={
                    "enrollment_id": enrollment.id,
                    "idempotency_key": "route-proposal-1",
                    "ai_model": "route-planner-test",
                    "ai_reason": "Verified buyer and healthy mailbox",
                    "confidence": 0.95,
                    "evidence_snapshot_ids": [evidence.id],
                    "steps": [
                        {
                            "position": 1,
                            "sequence_step_id": step.id,
                            "attempt_id": attempt.id,
                            "contact_point_id": contact_point.id,
                            "channel_account_id": account.id,
                            "channel": "email",
                            "scheduled_at": scheduled_at.isoformat(),
                            "subject": "Original subject",
                            "body": "Original body",
                            "ai_reason": "Best verified channel",
                            "confidence": 0.96,
                            "evidence_snapshot_ids": [evidence.id],
                        }
                    ],
                },
            )
            preview = await client.post(
                "/api/v2/review-batches/preview",
                json={
                    "route_proposal_ids": [proposal.json()["id"]],
                    "idempotency_key": "review-batch-1",
                    "approval_id": "PILOT-APPROVAL-1",
                    "price_version": "pilot-2026-07",
                    "estimated_cost": 0.02,
                },
            )
            original_checksum = preview.json()["preview_checksum"]
            edited = await client.patch(
                f"/api/v2/review-batches/{preview.json()['id']}/items/{preview.json()['items'][0]['id']}",
                json={"subject": "Edited subject", "body": "Edited body"},
            )
            stale_approval = await client.post(
                f"/api/v2/review-batches/{preview.json()['id']}/approve",
                json={
                    "preview_checksum": original_checksum,
                    "approval_id": "PILOT-APPROVAL-1",
                    "human_confirmed": True,
                },
            )
            refreshed = await client.post(
                "/api/v2/review-batches/preview",
                json={
                    "route_proposal_ids": [proposal.json()["id"]],
                    "idempotency_key": "review-batch-1",
                    "batch_id": preview.json()["id"],
                    "approval_id": "PILOT-APPROVAL-1",
                    "price_version": "pilot-2026-07",
                    "estimated_cost": 0.02,
                },
            )
            approved = await client.post(
                f"/api/v2/review-batches/{preview.json()['id']}/approve",
                json={
                    "preview_checksum": refreshed.json()["preview_checksum"],
                    "approval_id": "PILOT-APPROVAL-1",
                    "human_confirmed": True,
                },
            )
            return proposal, preview, edited, stale_approval, refreshed, approved

    try:
        proposal, preview, edited, stale_approval, refreshed, approved = asyncio.run(flow())
    finally:
        main.app.dependency_overrides.clear()

    assert proposal.status_code == 201, proposal.text
    assert preview.status_code == 200, preview.text
    assert edited.status_code == 200, edited.text
    assert edited.json()["preview_checksum"] is None
    assert stale_approval.status_code == 409
    assert stale_approval.json()["detail"]["code"] == "REVIEW_BATCH_REPREVIEW_REQUIRED"
    assert refreshed.status_code == 200, refreshed.text
    assert refreshed.json()["preview_checksum"] != preview.json()["preview_checksum"]
    assert approved.status_code == 200, approved.text
    assert approved.json()["status"] == "approved"
    frozen = db_session.query(models.EnrollmentRouteStep).one()
    assert frozen.subject == "Edited subject"
    assert frozen.body == "Edited body"
    db_session.refresh(attempt)
    assert attempt.status == AttemptStatus.QUEUED


def test_whatsapp_route_requires_active_affirmative_consent_and_revocation_blocks_preview(db_session):
    user, contact_point, account, enrollment, step, attempt, evidence = _seed_reviewable_route(
        db_session,
        channel=Channel.WHATSAPP,
    )
    import main

    main.app.dependency_overrides[get_current_user] = lambda: user

    async def flow():
        transport = httpx.ASGITransport(app=main.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            route = {
                "enrollment_id": enrollment.id,
                "idempotency_key": "whatsapp-route-1",
                "ai_model": "route-planner-test",
                "ai_reason": "Existing opted-in WhatsApp contact",
                "confidence": 0.95,
                "evidence_snapshot_ids": [evidence.id],
                "steps": [
                    {
                        "position": 1,
                        "sequence_step_id": step.id,
                        "attempt_id": attempt.id,
                        "contact_point_id": contact_point.id,
                        "channel_account_id": account.id,
                        "channel": "whatsapp",
                        "scheduled_at": (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat(),
                        "body": "Consent-only message",
                        "ai_reason": "The contact explicitly opted in",
                        "confidence": 0.95,
                        "evidence_snapshot_ids": [evidence.id],
                    }
                ],
            }
            blocked = await client.post("/api/v2/route-proposals", json=route)
            consent = await client.post(
                "/api/v2/whatsapp-consents",
                json={
                    "contact_point_id": contact_point.id,
                    "idempotency_key": "wa-consent-1",
                    "source": "signed_form",
                    "evidence_text": "Contact checked the WhatsApp opt-in box",
                    "granted_at": datetime.now(timezone.utc).isoformat(),
                },
            )
            allowed = await client.post("/api/v2/route-proposals", json=route)
            revoked = await client.post(
                f"/api/v2/whatsapp-consents/{consent.json()['id']}/revoke",
                json={"reason": "Contact withdrew consent"},
            )
            preview = await client.post(
                "/api/v2/review-batches/preview",
                json={
                    "route_proposal_ids": [allowed.json()["id"]],
                    "idempotency_key": "wa-batch-1",
                    "approval_id": "WA-APPROVAL-1",
                    "price_version": "pilot-2026-07",
                },
            )
            approval = await client.post(
                f"/api/v2/review-batches/{preview.json()['id']}/approve",
                json={
                    "preview_checksum": preview.json()["preview_checksum"],
                    "approval_id": "WA-APPROVAL-1",
                    "human_confirmed": True,
                },
            )
            return blocked, consent, allowed, revoked, preview, approval

    try:
        blocked, consent, allowed, revoked, preview, approval = asyncio.run(flow())
    finally:
        main.app.dependency_overrides.clear()

    assert blocked.status_code == 409
    assert blocked.json()["detail"]["code"] == "WHATSAPP_CONSENT_REQUIRED"
    assert consent.status_code == 201, consent.text
    assert allowed.status_code == 201, allowed.text
    assert revoked.status_code == 200, revoked.text
    assert preview.status_code == 200, preview.text
    assert approval.status_code == 409
    assert approval.json()["detail"]["code"] == "WHATSAPP_CONSENT_REQUIRED"
