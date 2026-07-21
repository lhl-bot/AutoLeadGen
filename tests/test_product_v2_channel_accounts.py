"""GWT regressions for credential-free V2 sender-account enforcement."""

from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError
from sqlalchemy.exc import IntegrityError

import models as legacy
from product_v2 import models
from product_v2.connectors import build_local_registry
from product_v2.connectors.base import ConnectorRequest, ConnectorResult
from product_v2.connectors.registry import ConnectorRegistry
from product_v2.enums import (
    AttemptStatus,
    CampaignLifecycle,
    CampaignRunMode,
    CampaignRevisionStatus,
    Channel,
    ChannelAccountHealth,
    ContactPointVerificationStatus,
    EnrollmentStatus,
    OverrideGate,
    OwnerWritePath,
    ProviderCostStatus,
    SafetyLockScope,
)
from product_v2.runtime.outbound import execute_attempt
from product_v2.schemas import SequenceStepCreate
from product_v2.services.channel_accounts import (
    bind_legacy_email_account,
    create_account_safety_lock,
    evaluate_channel_account,
    record_trusted_channel_account_health,
    refresh_channel_account_health,
    release_attempt_capacity,
    update_channel_account_policy,
)
from product_v2.services.domain import campaign_readiness, evaluate_outreach_gates


def _account_graph(db, *, bind_step=True, daily_limit=None, account_timezone="UTC"):
    user = legacy.User(username="account-owner", hashed_password="x", is_active=True)
    db.add(user)
    db.flush()
    company = models.Company(
        owner_id=user.id,
        name="Account Co",
        normalized_domain="account.example",
    )
    db.add(company)
    db.flush()
    contact = models.Contact(
        owner_id=user.id,
        company_id=company.id,
        full_name="Account Buyer",
        timezone="UTC",
    )
    db.add(contact)
    db.flush()
    point = models.ContactPoint(
        owner_id=user.id,
        company_id=company.id,
        contact_id=contact.id,
        channel=Channel.EMAIL,
        value="buyer@account.example",
        normalized_value="buyer@account.example",
        verification_status=ContactPointVerificationStatus.VALID,
    )
    account = models.ChannelAccount(
        owner_id=user.id,
        channel=Channel.EMAIL,
        provider="fake-email",
        provider_account_id=f"local-fake:{user.id}:email",
        enabled=True,
        health_status=ChannelAccountHealth.HEALTHY,
        health_checked_at=datetime.now(timezone.utc),
        daily_limit=daily_limit,
        timezone=account_timezone,
    )
    campaign = models.Campaign(
        owner_id=user.id,
        name="Account campaign",
        lifecycle=CampaignLifecycle.RUNNING,
        published_revision_number=1,
    )
    db.add_all([point, account, campaign])
    db.flush()
    revision = models.CampaignRevision(
        owner_id=user.id,
        campaign_id=campaign.id,
        revision_number=1,
        status=CampaignRevisionStatus.PUBLISHED,
        quality_gates={"require_evidence": False, "require_timezone": False},
        budget_definition={"native_limit": 10},
        stop_conditions={"public_unsubscribe_url": "http://127.0.0.1/unsubscribe"},
    )
    db.add(revision)
    db.flush()
    step = models.SequenceStep(
        owner_id=user.id,
        campaign_revision_id=revision.id,
        channel_account_id=account.id if bind_step else None,
        position=1,
        channel=Channel.EMAIL,
    )
    db.add(step)
    db.flush()
    enrollment = models.Enrollment(
        owner_id=user.id,
        campaign_id=campaign.id,
        campaign_revision_id=revision.id,
        company_id=company.id,
        contact_id=contact.id,
        status=EnrollmentStatus.ACTIVE,
    )
    db.add(enrollment)
    db.flush()
    attempt = models.OutreachAttempt(
        owner_id=user.id,
        campaign_id=campaign.id,
        enrollment_id=enrollment.id,
        sequence_step_id=step.id,
        contact_point_id=point.id,
        channel_account_id=account.id if bind_step else None,
        channel=Channel.EMAIL,
        idempotency_key="account-attempt",
    )
    db.add(attempt)
    db.commit()
    return user, company, contact, point, account, campaign, revision, step, enrollment, attempt


def test_legacy_binding_keeps_identity_immutable_and_never_copies_secret(db_session):
    # GIVEN: A legacy SMTP record containing a recognizable password.
    owner = legacy.User(username="legacy-account-owner", hashed_password="x", is_active=True)
    db_session.add(owner)
    db_session.flush()
    source = legacy.EmailAccount(
        user_id=owner.id,
        email="sender@example.com",
        smtp_host="smtp.example.com",
        smtp_user="sender@example.com",
        smtp_pass="DO-NOT-COPY-SECRET",
    )
    db_session.add(source)
    db_session.flush()

    # WHEN: V2 binds the source and a trusted probe later reports health.
    account = bind_legacy_email_account(
        db_session,
        owner_id=owner.id,
        legacy_email_account_id=source.id,
        daily_limit=25,
    )
    assert account.health_status == ChannelAccountHealth.UNKNOWN
    assert account.health_checked_at is None
    record_trusted_channel_account_health(
        db_session,
        account=account,
        status=ChannelAccountHealth.HEALTHY,
    )
    db_session.commit()

    # THEN: V2 contains only the legacy FK/public identity and no secret value.
    persisted_values = [getattr(account, column.name) for column in account.__table__.columns]
    assert "DO-NOT-COPY-SECRET" not in repr(persisted_values)
    assert account.legacy_email_account_id == source.id
    assert account.provider_account_id == source.email

    # AND: changing Provider or source identity cannot rewrite historical Attempt identity.
    with pytest.raises(ValueError, match="identity is immutable"):
        bind_legacy_email_account(
            db_session,
            owner_id=owner.id,
            legacy_email_account_id=source.id,
            provider="different-provider",
        )
    db_session.rollback()
    account.provider_account_id = "silent-history-rewrite@example.com"
    with pytest.raises(ValueError, match="identity is immutable"):
        db_session.flush()
    db_session.rollback()
    source.email = "rotated@example.com"
    db_session.commit()
    with pytest.raises(ValueError, match="identity is immutable"):
        bind_legacy_email_account(
            db_session,
            owner_id=owner.id,
            legacy_email_account_id=source.id,
        )


def test_legacy_presence_cannot_forge_fresh_healthy_status(db_session):
    owner = legacy.User(username="legacy-health-owner", hashed_password="x", is_active=True)
    db_session.add(owner)
    db_session.flush()
    source = legacy.EmailAccount(
        user_id=owner.id,
        email="health@example.com",
        smtp_host="smtp.example.com",
        smtp_user="health@example.com",
        smtp_pass="present-is-not-health",
    )
    db_session.add(source)
    db_session.flush()
    account = bind_legacy_email_account(
        db_session,
        owner_id=owner.id,
        legacy_email_account_id=source.id,
    )

    # A field-presence check remains UNKNOWN and stale until a trusted probe.
    decision = evaluate_channel_account(
        db_session,
        account=account,
        owner_id=owner.id,
        channel=Channel.EMAIL,
    )
    assert "channel_account_unhealthy" in decision.blockers
    assert account.health_checked_at is None

    record_trusted_channel_account_health(
        db_session,
        account=account,
        status=ChannelAccountHealth.HEALTHY,
        checked_at=datetime.now(timezone.utc) - timedelta(minutes=10),
    )
    stale = evaluate_channel_account(
        db_session,
        account=account,
        owner_id=owner.id,
        channel=Channel.EMAIL,
    )
    assert "channel_account_health_stale" in stale.blockers
    with pytest.raises(ValueError, match="non-secret"):
        record_trusted_channel_account_health(
            db_session,
            account=account,
            status=ChannelAccountHealth.UNHEALTHY,
            error_code="password=DO-NOT-PERSIST",
        )


def test_trusted_health_probe_is_audited_without_credentials_and_replays_idempotently(
    db_session,
):
    user, _, _, _, account, _, _, _, _, _ = _account_graph(db_session)
    observed_at = datetime(2026, 7, 17, 1, 2, 3, tzinfo=timezone.utc)

    record_trusted_channel_account_health(
        db_session,
        account=account,
        status=ChannelAccountHealth.UNHEALTHY,
        checked_at=observed_at,
        error_code="smtp_timeout",
        actor_user_id=user.id,
        source="smtp_probe",
        correlation_id="health-probe-account-1-0001",
    )
    db_session.commit()

    audit = db_session.query(models.AuditEvent).filter_by(
        action="channel_account.health_recorded",
        entity_id=str(account.id),
    ).one()
    assert audit.actor_user_id == user.id
    assert audit.correlation_id == "health-probe-account-1-0001"
    assert audit.before_data == {
        "channel_account_id": account.id,
        "health_status": "healthy",
        "health_checked_at": audit.before_data["health_checked_at"],
        "error_code": None,
    }
    assert audit.after_data == {
        "channel_account_id": account.id,
        "health_status": "unhealthy",
        "health_checked_at": observed_at.isoformat(),
        "error_code": "smtp_timeout",
    }
    assert audit.metadata_json == {
        "source": "smtp_probe",
        "contains_credentials": False,
    }
    serialized_audit = repr(
        {"before": audit.before_data, "after": audit.after_data, "meta": audit.metadata_json}
    )
    assert "provider_account_id" not in serialized_audit
    assert "smtp_pass" not in serialized_audit

    # Retrying the same trusted probe receipt is a no-op, including its audit.
    record_trusted_channel_account_health(
        db_session,
        account=account,
        status=ChannelAccountHealth.UNHEALTHY,
        checked_at=observed_at,
        error_code="smtp_timeout",
        actor_user_id=user.id,
        source="smtp_probe",
        correlation_id="health-probe-account-1-0001",
    )
    db_session.commit()
    assert db_session.query(models.AuditEvent).filter_by(
        action="channel_account.health_recorded",
        entity_id=str(account.id),
    ).count() == 1

    # Exact state replay is also idempotent without a receipt key.
    record_trusted_channel_account_health(
        db_session,
        account=account,
        status=ChannelAccountHealth.UNHEALTHY,
        checked_at=observed_at,
        error_code="smtp_timeout",
        actor_user_id=user.id,
        source="smtp_probe",
    )
    db_session.commit()
    assert db_session.query(models.AuditEvent).filter_by(
        action="channel_account.health_recorded",
        entity_id=str(account.id),
    ).count() == 1

    with pytest.raises(ValueError, match="conflicts"):
        record_trusted_channel_account_health(
            db_session,
            account=account,
            status=ChannelAccountHealth.HEALTHY,
            checked_at=observed_at,
            actor_user_id=user.id,
            source="smtp_probe",
            correlation_id="health-probe-account-1-0001",
        )


def test_readiness_is_read_only_and_reports_account_hard_gates(db_session):
    user, _, _, _, account, campaign, _, step, _, attempt = _account_graph(
        db_session,
        daily_limit=1,
    )
    update_channel_account_policy(
        db_session,
        owner_id=user.id,
        channel_account_id=account.id,
        actor_user_id=user.id,
        enabled=False,
    )
    attempt.capacity_reserved_at = datetime.now(timezone.utc)
    db_session.commit()
    before_accounts = db_session.query(models.ChannelAccount).count()
    before_checked_at = account.health_checked_at

    readiness = campaign_readiness(db_session, campaign)

    check = next(item for item in readiness.blockers if item.code == "channel_account_step_1")
    assert "channel_account_disabled" in check.details["blockers"]
    assert "channel_account_capacity_exhausted" in check.details["blockers"]
    assert db_session.query(models.ChannelAccount).count() == before_accounts
    assert account.health_checked_at == before_checked_at
    assert step.channel_account_id == account.id
    assert db_session.query(models.AuditEvent).filter_by(
        action="channel_account.policy_updated",
        entity_id=str(account.id),
    ).count() == 1


def test_readiness_virtual_fake_compatibility_never_creates_rows(db_session):
    *_, account, campaign, _, step, _, _ = _account_graph(db_session, bind_step=False)
    db_session.delete(account)
    db_session.commit()

    readiness = campaign_readiness(db_session, campaign)

    assert db_session.query(models.ChannelAccount).count() == 0
    assert step.channel_account_id is None
    assert "channel_account_step_1" not in {item.code for item in readiness.blockers}


def test_fake_execution_freezes_actual_account_and_capacity(db_session):
    *_, account, _, _, step, _, attempt = _account_graph(db_session, bind_step=False)
    db_session.delete(account)
    db_session.commit()
    assert db_session.query(models.ChannelAccount).count() == 0

    execute_attempt(db_session, attempt=attempt, registry=build_local_registry())
    db_session.commit()

    assert attempt.status == AttemptStatus.SUCCEEDED
    assert attempt.channel_account_id is not None
    assert attempt.capacity_reserved_at is not None
    bound = db_session.get(models.ChannelAccount, attempt.channel_account_id)
    assert bound.provider == "fake-email"
    assert bound.legacy_email_account_id is None
    assert bound.legacy_channel_account_id is None
    assert step.channel_account_id is None  # immutable legacy fixture remains unchanged
    cost = db_session.query(models.ProviderCostEvent).filter_by(
        outreach_attempt_id=attempt.id
    ).one()
    assert cost.metadata_json["channel_account_id"] == bound.id


def test_real_mode_missing_explicit_binding_fails_before_provider(
    db_session,
    monkeypatch,
):
    *_, account, campaign, _, _, _, attempt = _account_graph(db_session, bind_step=False)
    db_session.delete(account)
    campaign.run_mode = CampaignRunMode.AUTO
    db_session.add(
        models.OwnerMigrationState(
            owner_id=campaign.owner_id,
            current_path=OwnerWritePath.V2,
            version=1,
            switched_by_user_id=campaign.owner_id,
        )
    )
    db_session.commit()

    class RealConnector:
        channel = Channel.EMAIL
        provider = "smtp"
        is_fake = False

        def __init__(self):
            self.calls = 0

        def send(self, request: ConnectorRequest) -> ConnectorResult:
            self.calls += 1
            return ConnectorResult(True, self.provider, "must-not-send")

    monkeypatch.setenv("AUTOLEADGEN_ENV", "production")
    monkeypatch.setenv("AUTOLEADGEN_CONNECTOR_MODE", "real")
    monkeypatch.setenv("ALLOW_REAL_EXTERNAL_CALLS", "true")
    monkeypatch.setenv("OUTBOUND_HARD_PAUSE", "false")
    connector = RealConnector()
    registry = ConnectorRegistry()
    registry.register(connector)

    execute_attempt(db_session, attempt=attempt, registry=registry)
    db_session.commit()

    assert connector.calls == 0
    assert attempt.status == AttemptStatus.BLOCKED, attempt.last_error
    assert attempt.last_error == "channel_account_binding_missing"
    assert db_session.query(models.ProviderCostEvent).count() == 0


def test_account_safety_lock_is_hard_and_cannot_be_soft_overridden(db_session):
    user, _, contact, point, account, _, revision, _, enrollment, attempt = _account_graph(db_session)
    contact.timezone = None
    revision.quality_gates = {"require_evidence": False, "require_timezone": True}
    override = models.ManualOverride(
        owner_id=user.id,
        gate=OverrideGate.TIMEZONE,
        enrollment_id=enrollment.id,
        reason="Known buyer timezone",
        expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        created_by_user_id=user.id,
    )
    db_session.add(override)
    create_account_safety_lock(
        db_session,
        account=account,
        code="provider_account_incident",
        reason="Provider account is under investigation",
    )
    db_session.commit()

    decision = evaluate_outreach_gates(
        db_session,
        enrollment=enrollment,
        contact_point=point,
        attempt_id=attempt.id,
        channel_account_id=account.id,
    )
    assert "safety_lock" in decision.hard_blockers
    assert decision.soft_blockers == []
    assert override.id in decision.overrides

    registry = build_local_registry()
    execute_attempt(db_session, attempt=attempt, registry=registry)
    db_session.commit()
    assert attempt.status == AttemptStatus.BLOCKED
    assert registry.get(Channel.EMAIL).requests == []
    assert override.consumed_at is None
    assert db_session.query(models.ProviderCostEvent).count() == 0


def test_invalid_timezone_and_offline_sequence_fail_closed(db_session):
    *_, account, _, _, _, _, _ = _account_graph(
        db_session,
        account_timezone="Not/A_Timezone",
    )
    decision = evaluate_channel_account(
        db_session,
        account=account,
        owner_id=account.owner_id,
        channel=Channel.EMAIL,
    )
    assert "channel_account_timezone_invalid" in decision.blockers
    with pytest.raises(ValidationError, match="Offline evidence"):
        SequenceStepCreate(position=1, channel=Channel.OFFLINE)


def test_database_rejects_offline_accounts_and_ambiguous_lock_scope(db_session):
    user, _, _, _, account, _, _, _, _, _ = _account_graph(db_session)
    db_session.add(
        models.ChannelAccount(
            owner_id=user.id,
            channel=Channel.OFFLINE,
            provider="fake-offline",
            provider_account_id="forbidden-offline-account",
            health_status=ChannelAccountHealth.HEALTHY,
            timezone="UTC",
        )
    )
    with pytest.raises(IntegrityError):
        db_session.flush()
    db_session.rollback()

    db_session.add(
        models.SafetyLock(
            owner_id=user.id,
            scope=SafetyLockScope.CONTACT,
            contact_id=None,
            channel_account_id=account.id,
            code="ambiguous_scope",
            reason="must not target both contact scope and account",
        )
    )
    with pytest.raises(IntegrityError):
        db_session.flush()


def test_capacity_release_requires_refund_or_confirmed_not_sent(db_session):
    *_, attempt = _account_graph(db_session)
    execute_attempt(db_session, attempt=attempt, registry=build_local_registry())
    db_session.commit()

    with pytest.raises(ValueError, match="refund or confirmed not-sent"):
        release_attempt_capacity(
            db_session,
            attempt_id=attempt.id,
            reason="unsupported release",
            confirmed_not_sent=True,
        )
    db_session.rollback()
    cost = db_session.query(models.ProviderCostEvent).filter_by(
        outreach_attempt_id=attempt.id
    ).one()
    cost.status = ProviderCostStatus.REFUNDED
    db_session.commit()

    assert release_attempt_capacity(
        db_session,
        attempt_id=attempt.id,
        reason="provider refund confirmed",
    ) is True
    db_session.commit()
    assert attempt.capacity_reserved_at is None
    audit = db_session.query(models.AuditEvent).filter_by(
        action="channel_account.capacity_released",
        entity_id=str(attempt.id),
    ).one()
    assert audit.after_data["refunded"] is True
