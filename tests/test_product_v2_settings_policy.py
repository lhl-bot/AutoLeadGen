from decimal import Decimal

import models as legacy
from product_v2 import models
from product_v2.enums import (
    AttemptStatus,
    CampaignLifecycle,
    CampaignRevisionStatus,
    CampaignRunMode,
    Channel,
    ContactPointVerificationStatus,
    EnrollmentStatus,
    ProviderCostStatus,
    StageStatus,
    TaskStatus,
    TaskType,
    WorkerType,
)
from product_v2.connectors import build_local_registry
from product_v2.runtime.outbound import execute_attempt
from product_v2.runtime.queue import heartbeat
from product_v2.services.domain import campaign_readiness, evaluate_outreach_gates
from product_v2.settings_policy import (
    SETTINGS_ACTION,
    SETTINGS_ENTITY,
    channel_policy_allows,
    global_budget_snapshot,
    revision_unit_price,
)


def _setting_event(db, *, owner_id: int, section: str, version: int, values: dict):
    db.add(
        models.AuditEvent(
            owner_id=owner_id,
            actor_user_id=owner_id,
            action=SETTINGS_ACTION,
            entity_type=SETTINGS_ENTITY,
            entity_id=section,
            after_data={"version": version, "values": values},
        )
    )
    db.flush()


def _campaign_graph(db, *, owner_id: int, lifecycle=CampaignLifecycle.RUNNING):
    campaign = models.Campaign(
        owner_id=owner_id,
        name="Settings policy Campaign",
        lifecycle=lifecycle,
        published_revision_number=1,
    )
    db.add(campaign)
    db.flush()
    revision = models.CampaignRevision(
        owner_id=owner_id,
        campaign_id=campaign.id,
        revision_number=1,
        status=CampaignRevisionStatus.PUBLISHED,
        quality_gates={"require_evidence": False, "require_timezone": False},
        budget_definition={
            "native_limit": 100,
            "native_unit": "calls",
            "unit_price": "0.25",
            "currency": "USD",
            "price_version": "provider-v2",
        },
        stop_conditions={
            "public_unsubscribe_url": "http://127.0.0.1:3000/api/unsubscribe",
        },
    )
    db.add(revision)
    db.flush()
    step = models.SequenceStep(
        owner_id=owner_id,
        campaign_revision_id=revision.id,
        position=1,
        channel=Channel.EMAIL,
    )
    company = models.Company(
        owner_id=owner_id,
        name="Policy Buyer",
        normalized_domain="policy-buyer.example",
    )
    db.add_all([step, company])
    db.flush()
    contact = models.Contact(
        owner_id=owner_id,
        company_id=company.id,
        full_name="Policy Buyer",
        timezone="UTC",
    )
    db.add(contact)
    db.flush()
    point = models.ContactPoint(
        owner_id=owner_id,
        company_id=company.id,
        contact_id=contact.id,
        channel=Channel.EMAIL,
        value="buyer@policy-buyer.example",
        normalized_value="buyer@policy-buyer.example",
        verification_status=ContactPointVerificationStatus.VALID,
        is_primary=True,
    )
    enrollment = models.Enrollment(
        owner_id=owner_id,
        campaign_id=campaign.id,
        campaign_revision_id=revision.id,
        company_id=company.id,
        contact_id=contact.id,
        status=(
            EnrollmentStatus.ACTIVE
            if lifecycle == CampaignLifecycle.RUNNING
            else EnrollmentStatus.SCHEDULED
        ),
    )
    db.add_all([point, enrollment])
    db.flush()
    return campaign, revision, enrollment, point


def test_explicit_channel_policy_becomes_a_runtime_gate(db_session):
    # GIVEN: An owner migrated before settings existed.
    user = legacy.User(username="channel-policy-owner", hashed_password="x")
    db_session.add(user)
    db_session.flush()

    # THEN: The immutable Campaign Revision remains authoritative until the
    # owner explicitly publishes a global channel policy.
    assert channel_policy_allows(db_session, owner_id=user.id, channel=Channel.EMAIL) is True

    # WHEN: The owner publishes Email disabled while enabling LinkedIn.
    _setting_event(
        db_session,
        owner_id=user.id,
        section="channels_integrations",
        version=1,
        values={
            "email_enabled": False,
            "linkedin_enabled": True,
            "whatsapp_enabled": False,
            "public_unsubscribe_url": "",
            "review_before_send": True,
            "integration_notes": "",
        },
    )

    # THEN: Runtime policy denies Email and still allows the published channel.
    assert channel_policy_allows(db_session, owner_id=user.id, channel=Channel.EMAIL) is False
    assert channel_policy_allows(db_session, owner_id=user.id, channel=Channel.LINKEDIN) is True


def test_global_budget_counts_reserved_spend_and_fails_closed_on_unpriced_events(db_session):
    # GIVEN: A versioned USD 10 global budget and cross-Campaign ledger rows.
    user = legacy.User(username="global-budget-owner", hashed_password="x")
    db_session.add(user)
    db_session.flush()
    _setting_event(
        db_session,
        owner_id=user.id,
        section="providers",
        version=1,
        values={
            "global_budget_limit": 10,
            "currency": "USD",
            "price_version": "provider-v1",
            "paid_miss_requires_review": True,
            "provider_policy_notes": "",
        },
    )
    db_session.add_all(
        [
            models.ProviderCostEvent(
                owner_id=user.id,
                provider="paid-provider",
                operation="lookup",
                status=ProviderCostStatus.RESERVED,
                units=1,
                native_unit="call",
                unit_price=Decimal("3.50"),
                normalized_amount=Decimal("3.50"),
                normalized_currency="USD",
                price_version="provider-v1",
                billable=True,
                idempotency_key="global-budget-priced",
            ),
            models.ProviderCostEvent(
                owner_id=user.id,
                provider="paid-provider",
                operation="lookup",
                status=ProviderCostStatus.UNKNOWN,
                units=1,
                native_unit="call",
                price_version="provider-v1",
                billable=True,
                idempotency_key="global-budget-unpriced",
            ),
            models.ProviderCostEvent(
                owner_id=user.id,
                provider="paid-provider",
                operation="lookup",
                status=ProviderCostStatus.CHARGED,
                units=1,
                native_unit="call",
                unit_price=Decimal("2.00"),
                normalized_amount=Decimal("2.00"),
                normalized_currency="EUR",
                price_version="provider-v1",
                billable=True,
                idempotency_key="global-budget-incompatible-currency",
            ),
        ]
    )
    db_session.flush()

    # WHEN: Readiness/outbound reads the transactional global ledger.
    snapshot = global_budget_snapshot(db_session, owner_id=user.id)

    # THEN: Reserved money consumes capacity and unknown unpriced paid work is
    # surfaced as a hard accounting uncertainty.
    assert snapshot.limit == Decimal("10.0")
    assert snapshot.used == Decimal("3.500000")
    assert snapshot.remaining == Decimal("6.500000")
    # A different normalized currency cannot be silently omitted from an
    # owner-wide budget; it is reconciliation-required just like a missing
    # normalized amount.
    assert snapshot.unpriced_billable_events == 2


def test_revision_price_must_match_published_global_price_policy(db_session):
    user = legacy.User(username="price-policy-owner", hashed_password="x")
    db_session.add(user)
    db_session.flush()
    _setting_event(
        db_session,
        owner_id=user.id,
        section="providers",
        version=1,
        values={
            "global_budget_limit": 25,
            "currency": "USD",
            "price_version": "provider-v2",
            "paid_miss_requires_review": True,
            "provider_policy_notes": "",
        },
    )
    campaign = models.Campaign(
        owner_id=user.id,
        name="Priced campaign",
        lifecycle=CampaignLifecycle.READY,
        published_revision_number=1,
    )
    db_session.add(campaign)
    db_session.flush()
    revision = models.CampaignRevision(
        owner_id=user.id,
        campaign_id=campaign.id,
        revision_number=1,
        status=CampaignRevisionStatus.PUBLISHED,
        budget_definition={
            "native_limit": 100,
            "unit_price": "0.25",
            "currency": "USD",
            "price_version": "provider-v2",
        },
    )
    db_session.add(revision)
    db_session.flush()
    snapshot = global_budget_snapshot(db_session, owner_id=user.id)

    assert revision_unit_price(revision, snapshot) == (Decimal("0.25"), None)
    assert "global_budget_pricing" not in {
        item.code for item in campaign_readiness(db_session, campaign).blockers
    }
    revision.budget_definition = {**revision.budget_definition, "price_version": "stale-v1"}
    assert revision_unit_price(revision, snapshot) == (
        None,
        "global_budget_price_version_mismatch",
    )
    assert "global_budget_pricing" in {
        item.code for item in campaign_readiness(db_session, campaign).blockers
    }


def test_disabled_channel_blocks_readiness_and_execution_time_gate(db_session):
    # GIVEN: A fully provisioned READY Email Campaign and a published global
    # policy that explicitly disables Email.
    user = legacy.User(username="disabled-channel-owner", hashed_password="x")
    db_session.add(user)
    db_session.flush()
    campaign, _, enrollment, point = _campaign_graph(
        db_session,
        owner_id=user.id,
        lifecycle=CampaignLifecycle.READY,
    )
    db_session.add(
        legacy.EmailAccount(
            user_id=user.id,
            email="sales@policy-buyer.example",
            smtp_host="fake.invalid",
            smtp_user="sales@policy-buyer.example",
            smtp_pass="fake-not-used",
        )
    )
    heartbeat(
        db_session,
        worker_name="policy-outbound",
        worker_type=WorkerType.OUTBOUND,
        status=StageStatus.IDLE,
    )
    heartbeat(
        db_session,
        worker_name="policy-inbox",
        worker_type=WorkerType.INBOX,
        status=StageStatus.IDLE,
    )
    _setting_event(
        db_session,
        owner_id=user.id,
        section="channels_integrations",
        version=1,
        values={
            "email_enabled": False,
            "linkedin_enabled": True,
            "whatsapp_enabled": True,
            "public_unsubscribe_url": "http://127.0.0.1:3000/api/unsubscribe",
            "review_before_send": True,
            "integration_notes": "",
        },
    )

    # WHEN: Readiness evaluates the saved operating policy.
    readiness = campaign_readiness(db_session, campaign)

    # THEN: The setting is a blocker, not decorative UI state.
    assert readiness.ready is False
    assert "channel_policy_email" in {item.code for item in readiness.blockers}

    # AND: Even a stale RUNNING lifecycle cannot bypass the same execution gate.
    campaign.lifecycle = CampaignLifecycle.RUNNING
    enrollment.status = EnrollmentStatus.ACTIVE
    decision = evaluate_outreach_gates(
        db_session,
        enrollment=enrollment,
        contact_point=point,
        expected_channel=Channel.EMAIL,
    )
    assert decision.allowed is False
    assert "channel_policy_disabled" in decision.hard_blockers


def test_exhausted_global_budget_blocks_billable_attempt_across_campaigns(db_session):
    # GIVEN: An owner whose normalized global budget is already fully reserved.
    user = legacy.User(username="exhausted-global-budget", hashed_password="x")
    db_session.add(user)
    db_session.flush()
    _, _, enrollment, point = _campaign_graph(db_session, owner_id=user.id)
    _setting_event(
        db_session,
        owner_id=user.id,
        section="providers",
        version=1,
        values={
            "global_budget_limit": 1,
            "currency": "USD",
            "price_version": "provider-v2",
            "paid_miss_requires_review": True,
            "provider_policy_notes": "",
        },
    )
    db_session.add(
        models.ProviderCostEvent(
            owner_id=user.id,
            provider="paid-provider",
            operation="send",
            status=ProviderCostStatus.RESERVED,
            units=1,
            native_unit="calls",
            unit_price=Decimal("1"),
            normalized_amount=Decimal("1"),
            normalized_currency="USD",
            price_version="provider-v2",
            billable=True,
            idempotency_key="already-reserved-global-budget",
        )
    )
    db_session.flush()

    # WHEN: Another Campaign tries to reserve a real Provider call.
    decision = evaluate_outreach_gates(
        db_session,
        enrollment=enrollment,
        contact_point=point,
        expected_channel=Channel.EMAIL,
        provider_billable=True,
    )

    # THEN: The cross-Campaign normalized ledger is a hard runtime gate.
    assert decision.allowed is False
    assert "global_budget_exhausted" in decision.hard_blockers

    # The global ledger controls paid Provider boundaries. A local fake replay
    # neither reserves billable cost nor consumes that capacity and must remain
    # usable for isolated verification.
    fake_decision = evaluate_outreach_gates(
        db_session,
        enrollment=enrollment,
        contact_point=point,
        expected_channel=Channel.EMAIL,
        provider_billable=False,
    )
    assert fake_decision.allowed is True
    assert not any(code.startswith("global_budget_") for code in fake_decision.hard_blockers)


def test_global_review_policy_routes_auto_attempt_to_human_approval(db_session):
    # GIVEN: An AUTO Campaign whose owner explicitly requires review before
    # send, even though the only local connector is fake.
    user = legacy.User(username="global-review-owner", hashed_password="x")
    db_session.add(user)
    db_session.flush()
    campaign, revision, enrollment, point = _campaign_graph(db_session, owner_id=user.id)
    campaign.run_mode = CampaignRunMode.AUTO
    step = db_session.query(models.SequenceStep).filter_by(
        campaign_revision_id=revision.id,
        position=1,
    ).one()
    attempt = models.OutreachAttempt(
        owner_id=user.id,
        campaign_id=campaign.id,
        enrollment_id=enrollment.id,
        sequence_step_id=step.id,
        contact_point_id=point.id,
        channel=Channel.EMAIL,
        idempotency_key="global-review-auto-attempt",
    )
    db_session.add(attempt)
    _setting_event(
        db_session,
        owner_id=user.id,
        section="channels_integrations",
        version=1,
        values={
            "email_enabled": True,
            "linkedin_enabled": False,
            "whatsapp_enabled": False,
            "public_unsubscribe_url": "http://127.0.0.1:3000/api/unsubscribe",
            "review_before_send": True,
            "integration_notes": "",
        },
    )
    db_session.commit()
    registry = build_local_registry()

    # WHEN: The outbound runtime evaluates the queued Attempt.
    execute_attempt(db_session, attempt=attempt, registry=registry)
    db_session.commit()

    # THEN: It creates a durable exact-Attempt approval and performs no send.
    task = db_session.query(models.Task).filter_by(
        attempt_id=attempt.id,
        task_type=TaskType.DRAFT_REVIEW,
    ).one()
    assert task.status == TaskStatus.OPEN
    assert task.metadata_json["global_review_policy"] is True
    assert attempt.status == AttemptStatus.BLOCKED
    assert registry.get(Channel.EMAIL).requests == []
