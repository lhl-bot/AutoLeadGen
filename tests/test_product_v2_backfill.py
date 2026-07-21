from datetime import timedelta
from decimal import Decimal
from pathlib import Path

import pytest

import models as legacy
from product_v2 import models
from product_v2.backfill import ProductV2Backfill, QuarantineItem
from product_v2.enums import EnrollmentStatus, TaskQueueScope, TaskType
from product_v2.services.domain import utcnow


def test_backfill_keyset_pagination_processes_every_small_batch(db_session, tmp_path):
    user = legacy.User(username="keyset-owner", hashed_password="x", is_active=True)
    db_session.add(user)
    db_session.flush()
    pool = legacy.ClientPool(user_id=user.id, name="Keyset pool")
    db_session.add(pool)
    db_session.flush()
    for index in range(3):
        db_session.add(
            legacy.Lead(
                client_pool_id=pool.id,
                domain=f"buyer-{index}.example",
                company_name=f"Buyer {index}",
                email=f"buyer-{index}@example.net",
            )
        )
    db_session.commit()

    report = ProductV2Backfill(
        db_session,
        apply=True,
        resume=False,
        batch_size=1,
        checkpoint_path=tmp_path / "keyset-checkpoint.json",
        quarantine_path=tmp_path / "keyset-quarantine.json",
    ).run()

    assert report.processed_leads == 3
    assert db_session.query(models.Company).count() == 3
    assert db_session.query(models.Contact).count() == 3
    assert db_session.query(models.ContactPoint).count() == 3


def test_backfill_is_resumable_idempotent_and_keeps_domain_scope_for_review(db_session, tmp_path):
    user = legacy.User(username="backfill-owner", hashed_password="x", is_active=True)
    db_session.add(user)
    db_session.flush()
    pool = legacy.ClientPool(user_id=user.id, name="EU buyers")
    db_session.add(pool)
    db_session.flush()
    workflow = legacy.Workflow(
        user_id=user.id,
        name="Legacy workflow",
        status="paused",
        search_keywords="home textile buyer",
        target_positions="buyer",
        client_pool_id=pool.id,
    )
    db_session.add(workflow)
    db_session.flush()
    lead = legacy.Lead(
        workflow_id=workflow.id,
        client_pool_id=pool.id,
        domain="buyer.example",
        company_name="Buyer Example",
        email="buyer@buyer.example",
        first_name="Ada",
        last_name="Buyer",
        job_title="Purchasing Manager",
        status="replied",
        email_verified=True,
        email_validation_status="valid",
        has_replied=True,
        reply_intent="more_info",
        reply_snippet="Please send a quote",
        fit_score=82,
        timezone="Europe/Berlin",
    )
    db_session.add(lead)
    db_session.flush()
    db_session.add(
        legacy.LeadBrief(
            lead_id=lead.id,
            company_overview="Retail buyer",
            research_status="valid",
            evidence_sources=[{"type": "official_website", "value": "https://buyer.example"}],
            quality_flags=["public_web:evidence_first"],
        )
    )
    db_session.add(
        legacy.EmailLog(
            lead_id=lead.id,
            direction="outbound",
            from_email="sales@example.com",
            to_email=lead.email,
            subject="Introduction",
            body="Hello",
            message_id="legacy-message-1",
        )
    )
    db_session.add(
        legacy.EmailSuppression(
            user_id=user.id,
            lead_id=lead.id,
            email=lead.email,
            domain=lead.domain,
            reason="unsubscribe",
            source="reply",
        )
    )
    db_session.add(
        legacy.ProviderUsageEvent(
            provider="snovio",
            operation="email_lookup",
            workflow_id=workflow.id,
            lead_id=lead.id,
            status="success",
            units=1,
            estimated_credits=2,
            result_count=1,
        )
    )
    db_session.commit()

    checkpoint = tmp_path / "checkpoint.json"
    quarantine = tmp_path / "quarantine.json"
    first = ProductV2Backfill(
        db_session,
        apply=True,
        resume=False,
        batch_size=1,
        checkpoint_path=checkpoint,
        quarantine_path=quarantine,
    ).run()
    second = ProductV2Backfill(
        db_session,
        apply=True,
        resume=True,
        batch_size=1,
        checkpoint_path=checkpoint,
        quarantine_path=quarantine,
    ).run()

    assert first.processed_leads == 1
    assert second.processed_leads == 0
    assert first.checksum == second.checksum
    assert first.quarantine == []
    restriction = db_session.query(models.ConsentRestriction).one()
    assert restriction.scope.value == "contact_point"
    assert restriction.metadata_json["domain_scope_applied"] is False
    assert db_session.query(models.Task).filter_by(title="Review legacy domain suppression scope").count() == 1
    assert db_session.query(models.Company).count() == 1
    assert db_session.query(models.Contact).count() == 1
    assert db_session.query(models.OutreachAttempt).count() == 1
    assert db_session.query(models.MessageEvent).count() == 1
    evidence = db_session.query(models.EvidenceSnapshot).order_by(models.EvidenceSnapshot.id).all()
    assert len(evidence) == 2
    assert evidence[0].source == "legacy_lead_brief"
    assert evidence[1].source == "public_web_enrichment"
    assert evidence[1].source_url == "https://buyer.example"
    assert evidence[1].evidence["quality_flags"] == ["public_web:evidence_first"]
    evidence[1].archived_at = utcnow()
    lead.brief.company_overview = "Updated public evidence"
    lead.brief.quality_flags = ["public_web:evidence_first", "public_web:revised"]
    db_session.commit()
    ProductV2Backfill(
        db_session,
        apply=True,
        resume=False,
        batch_size=1,
        checkpoint_path=tmp_path / "revision-checkpoint.json",
        quarantine_path=tmp_path / "revision-quarantine.json",
    ).run()
    revisions = db_session.query(models.EvidenceSnapshot).filter_by(
        legacy_source_table="lead_briefs_public_web",
    ).order_by(models.EvidenceSnapshot.version).all()
    assert len(revisions) == 2
    assert revisions[0].archived_at is not None
    assert revisions[1].archived_at is None
    assert revisions[1].version == 2
    assert revisions[1].evidence["company_overview"] == "Updated public evidence"
    cost = db_session.query(models.ProviderCostEvent).one()
    assert cost.native_unit == "credits"
    assert cost.normalized_amount is None


def test_backfill_reuses_one_enrollment_for_duplicate_legacy_leads_without_losing_messages(
    db_session,
    tmp_path,
):
    # GIVEN: The legacy database contains duplicate Lead rows for one Contact in
    # the same Workflow, each with distinct message history.
    user = legacy.User(username="duplicate-enrollment-owner", hashed_password="x", is_active=True)
    db_session.add(user)
    db_session.flush()
    pool = legacy.ClientPool(user_id=user.id, name="Duplicate enrollment pool")
    db_session.add(pool)
    db_session.flush()
    workflow = legacy.Workflow(
        user_id=user.id,
        name="Duplicate enrollment workflow",
        status="paused",
        search_keywords="buyer",
        target_positions="purchasing",
        client_pool_id=pool.id,
    )
    db_session.add(workflow)
    db_session.flush()
    first = legacy.Lead(
        workflow_id=workflow.id,
        client_pool_id=pool.id,
        domain="duplicate.example",
        company_name="Duplicate Buyer",
        email="buyer@duplicate.example",
        first_name="Same",
        last_name="Buyer",
        status="sent",
    )
    second = legacy.Lead(
        workflow_id=workflow.id,
        client_pool_id=pool.id,
        domain="duplicate.example",
        company_name="Duplicate Buyer",
        email="buyer@duplicate.example",
        first_name="Same",
        last_name="Buyer",
        status="replied",
        has_replied=True,
        reply_snippet="Second legacy row reply",
    )
    db_session.add_all([first, second])
    db_session.flush()
    db_session.add_all(
        [
            legacy.EmailLog(
                lead_id=first.id,
                direction="outbound",
                from_email="sales@example.com",
                to_email=first.email,
                subject="First history",
                body="First body",
                message_id="duplicate-history-1",
            ),
            legacy.EmailLog(
                lead_id=second.id,
                direction="outbound",
                from_email="sales@example.com",
                to_email=second.email,
                subject="Second history",
                body="Second body",
                message_id="duplicate-history-2",
            ),
        ]
    )
    db_session.commit()

    # WHEN: Source rows are migrated in stable Lead ID order.
    report = ProductV2Backfill(
        db_session,
        apply=True,
        resume=False,
        batch_size=10,
        checkpoint_path=tmp_path / "duplicate-enrollment-checkpoint.json",
        quarantine_path=tmp_path / "duplicate-enrollment-quarantine.json",
    ).run()

    # THEN: The V2 uniqueness invariant holds, both histories remain, and the
    # ambiguous duplicate status is explicitly queued for review.
    assert db_session.query(models.Company).count() == 1
    assert db_session.query(models.Contact).count() == 1
    assert db_session.query(models.Enrollment).count() == 1
    enrollment = db_session.query(models.Enrollment).one()
    assert enrollment.legacy_id == str(first.id)
    assert db_session.query(models.OutreachAttempt).count() == 2
    assert db_session.query(models.MessageEvent).count() == 2
    duplicates = [
        item
        for item in report.quarantine
        if item.reason == "duplicate_campaign_contact_enrollment"
    ]
    assert len(duplicates) == 1
    assert duplicates[0].source_id == second.id
    assert duplicates[0].details == {
        "campaign_id": enrollment.campaign_id,
        "contact_id": enrollment.contact_id,
        "canonical_enrollment_id": enrollment.id,
        "canonical_legacy_id": str(first.id),
    }
    reconciliation = db_session.query(models.Task).filter_by(
        owner_id=user.id,
        task_type=TaskType.DATA_GOVERNANCE,
        title=f"Reconcile legacy lead {second.id}: duplicate_campaign_contact_enrollment",
    ).one()
    assert reconciliation.queue_scope == TaskQueueScope.DATA_GOVERNANCE
    assert reconciliation.contact_id == enrollment.contact_id
    assert reconciliation.campaign_id == enrollment.campaign_id
    assert reconciliation.enrollment_id == enrollment.id
    assert reconciliation.metadata_json["legacy_source_id"] == second.id


def test_backfill_quarantines_owner_conflicts_without_guessing(db_session, tmp_path):
    # GIVEN: A legacy lead whose Workflow and Pool belong to different owners.
    workflow_owner = legacy.User(username="workflow-owner", hashed_password="x", is_active=True)
    pool_owner = legacy.User(username="pool-owner", hashed_password="x", is_active=True)
    db_session.add_all([workflow_owner, pool_owner])
    db_session.flush()
    pool = legacy.ClientPool(user_id=pool_owner.id, name="Conflicting pool")
    workflow = legacy.Workflow(
        user_id=workflow_owner.id,
        name="Conflicting workflow",
        search_keywords="buyer",
        target_positions="purchasing",
    )
    db_session.add_all([pool, workflow])
    db_session.flush()
    lead = legacy.Lead(
        workflow_id=workflow.id,
        client_pool_id=pool.id,
        company_name="Ambiguous Buyer",
        domain="ambiguous.example",
        email="buyer@ambiguous.example",
    )
    db_session.add(lead)
    db_session.commit()

    # WHEN: Applying the additive Product V2 backfill.
    report = ProductV2Backfill(
        db_session,
        apply=True,
        resume=False,
        batch_size=1,
        checkpoint_path=tmp_path / "conflict-checkpoint.json",
        quarantine_path=tmp_path / "conflict-quarantine.json",
    ).run()
    resumed = ProductV2Backfill(
        db_session,
        apply=True,
        resume=True,
        batch_size=1,
        checkpoint_path=tmp_path / "conflict-checkpoint.json",
        quarantine_path=tmp_path / "conflict-quarantine.json",
    ).run()

    # THEN: Ownership stays unresolved and no Company or Contact is guessed.
    assert report.processed_leads == 1
    assert len(report.quarantine) == 1
    assert report.quarantine[0].source_id == lead.id
    assert report.quarantine[0].reason == "workflow_pool_owner_conflict"
    assert len(resumed.quarantine) == 1
    assert resumed.quarantine[0].reason == "workflow_pool_owner_conflict"
    assert db_session.query(models.Company).count() == 0
    assert db_session.query(models.Contact).count() == 0


@pytest.mark.parametrize("conflicting_channel", ["email", "linkedin"])
def test_backfill_quarantines_contact_identity_reused_across_companies(
    db_session,
    tmp_path,
    conflicting_channel,
):
    # GIVEN: Two legacy companies share a globally unique email or LinkedIn identity.
    user = legacy.User(username=f"identity-{conflicting_channel}", hashed_password="x", is_active=True)
    db_session.add(user)
    db_session.flush()
    pool = legacy.ClientPool(user_id=user.id, name="Identity conflict pool")
    db_session.add(pool)
    db_session.flush()
    workflow = legacy.Workflow(
        user_id=user.id,
        name="Identity conflict workflow",
        status="paused",
        search_keywords="buyer",
        target_positions="purchasing",
        client_pool_id=pool.id,
    )
    db_session.add(workflow)
    db_session.flush()
    shared_email = "shared@alpha.example" if conflicting_channel == "email" else None
    shared_linkedin = (
        "https://linkedin.example/in/shared-person"
        if conflicting_channel == "linkedin"
        else None
    )
    first = legacy.Lead(
        workflow_id=workflow.id,
        client_pool_id=pool.id,
        domain="alpha.example",
        company_name="Alpha",
        email=shared_email or "first@alpha.example",
        linkedin_url=shared_linkedin,
        first_name="First",
        last_name="Buyer",
    )
    second = legacy.Lead(
        workflow_id=workflow.id,
        client_pool_id=pool.id,
        domain="beta.example",
        company_name="Beta",
        email=shared_email or "second@beta.example",
        linkedin_url=shared_linkedin,
        first_name="Second",
        last_name="Buyer",
    )
    db_session.add_all([first, second])
    db_session.commit()

    # WHEN: Both rows are processed in source order.
    report = ProductV2Backfill(
        db_session,
        apply=True,
        resume=False,
        batch_size=10,
        checkpoint_path=tmp_path / f"{conflicting_channel}-checkpoint.json",
        quarantine_path=tmp_path / f"{conflicting_channel}-quarantine.json",
    ).run()

    # THEN: The second identity is quarantined before a cross-company graph is created.
    conflicts = [
        item for item in report.quarantine
        if item.reason == "contact_identity_company_conflict"
    ]
    assert len(conflicts) == 1
    assert conflicts[0].source_id == second.id
    assert conflicts[0].details["channel"] == conflicting_channel
    assert conflicts[0].details["existing_company_domain"] == "alpha.example"
    assert conflicts[0].details["incoming_company_domain"] == "beta.example"
    assert conflicts[0].details["outbound_safety_lock"] is True
    assert db_session.query(models.Company).count() == 1
    assert db_session.query(models.Contact).count() == 1
    assert db_session.query(models.Enrollment).count() == 1
    safety_lock = db_session.query(models.SafetyLock).one()
    assert safety_lock.code == "company_identity_conflict"
    assert safety_lock.active is True
    task = db_session.query(models.Task).filter_by(task_type=TaskType.DATA_GOVERNANCE).one()
    assert task.queue_scope == TaskQueueScope.DATA_GOVERNANCE
    assert task.company_id == safety_lock.company_id
    assert task.contact_id is not None
    enrollment = db_session.query(models.Enrollment).one()
    contact = db_session.query(models.Contact).one()
    assert enrollment.contact_id == contact.id
    assert enrollment.company_id == contact.company_id


@pytest.mark.parametrize(
    "invalid_domain",
    ["unknown", "not a domain", "placeholder.invalid", "https://", "gmail.com"],
)
def test_backfill_quarantines_invalid_or_placeholder_company_domains(
    db_session,
    tmp_path,
    invalid_domain,
):
    # GIVEN: A legacy row has a valid-looking email but an explicit unusable domain.
    user = legacy.User(username=f"invalid-domain-{len(invalid_domain)}", hashed_password="x", is_active=True)
    db_session.add(user)
    db_session.flush()
    pool = legacy.ClientPool(user_id=user.id, name="Invalid domain pool")
    db_session.add(pool)
    db_session.flush()
    lead = legacy.Lead(
        client_pool_id=pool.id,
        domain=invalid_domain,
        company_name="Placeholder company",
        email="buyer@otherwise-valid.example",
    )
    db_session.add(lead)
    db_session.commit()

    # WHEN: The backfill validates Company identity.
    report = ProductV2Backfill(
        db_session,
        apply=True,
        resume=False,
        batch_size=1,
        checkpoint_path=tmp_path / "invalid-domain-checkpoint.json",
        quarantine_path=tmp_path / "invalid-domain-quarantine.json",
    ).run()

    # THEN: It does not silently replace the bad source domain with the email
    # domain. The customer remains visible under a durable outbound lock.
    assert [item.reason for item in report.quarantine] == ["invalid_company_domain"]
    assert report.quarantine[0].source_id == lead.id
    company = db_session.query(models.Company).one()
    contact = db_session.query(models.Contact).one()
    assert company.name == "Placeholder company"
    assert company.normalized_domain is None
    assert contact.company_id == company.id
    safety_lock = db_session.query(models.SafetyLock).one()
    assert safety_lock.company_id == company.id
    assert safety_lock.code == "company_identity_pending"
    assert safety_lock.active is True
    task = db_session.query(models.Task).filter_by(
        owner_id=user.id,
        task_type=TaskType.DATA_GOVERNANCE,
        title=f"Complete legacy lead {lead.id} company identity",
    ).one()
    assert task.queue_scope == TaskQueueScope.DATA_GOVERNANCE
    assert task.company_id == company.id
    assert task.contact_id == contact.id
    assert task.metadata_json["quarantine_reason"] == "invalid_company_domain"
    assert task.metadata_json["quarantine_details"]["company_record_visible"] is True
    assert task.metadata_json["quarantine_details"]["outbound_safety_lock"] is True


def test_backfill_hard_locks_public_evidence_domain_conflict(db_session, tmp_path):
    owner = legacy.User(username="public-domain-conflict", hashed_password="x", is_active=True)
    db_session.add(owner)
    db_session.flush()
    pool = legacy.ClientPool(user_id=owner.id, name="Conflict pool")
    db_session.add(pool)
    db_session.flush()
    lead = legacy.Lead(
        client_pool_id=pool.id,
        company_name="Example Buyer",
        domain="stored.example",
        email="buyer@stored.example",
    )
    db_session.add(lead)
    db_session.flush()
    db_session.add(legacy.LeadBrief(
        lead_id=lead.id,
        company_overview="Public evidence is available.",
        research_status="valid",
        evidence_sources=[{"type": "official_indexed_page", "value": "https://different.example"}],
        quality_flags=[
            "public_web:evidence_first",
            "public_web:stored_domain_conflict",
            "public_web:conflicting_domain=different.example",
        ],
    ))
    db_session.commit()

    report = ProductV2Backfill(
        db_session,
        apply=True,
        resume=False,
        batch_size=1,
        checkpoint_path=tmp_path / "domain-conflict-checkpoint.json",
        quarantine_path=tmp_path / "domain-conflict-quarantine.json",
    ).run()

    conflict = next(item for item in report.quarantine if item.reason == "public_web_company_domain_conflict")
    assert conflict.details["stored_company_domain"] == "stored.example"
    assert conflict.details["public_evidence_domain"] == "different.example"
    company = db_session.query(models.Company).one()
    assert company.normalized_domain == "stored.example"
    lock = db_session.query(models.SafetyLock).filter_by(code="company_identity_conflict").one()
    assert lock.company_id == company.id
    assert lock.active is True
    task = db_session.query(models.Task).filter_by(task_type=TaskType.DATA_GOVERNANCE).one()
    assert task.queue_scope == TaskQueueScope.DATA_GOVERNANCE
    assert task.company_id == company.id
    assert task.metadata_json["quarantine_reason"] == "public_web_company_domain_conflict"


def test_backfill_keeps_companyless_profile_visible_without_inventing_identity(
    db_session,
    tmp_path,
):
    owner = legacy.User(username="companyless-owner", hashed_password="x", is_active=True)
    db_session.add(owner)
    db_session.flush()
    pool = legacy.ClientPool(user_id=owner.id, name="Companyless pool")
    db_session.add(pool)
    db_session.flush()
    lead = legacy.Lead(
        client_pool_id=pool.id,
        first_name="Pending",
        last_name="Buyer",
        job_title="Purchasing lead",
        linkedin_url="https://www.linkedin.com/in/pending-buyer",
    )
    db_session.add(lead)
    db_session.commit()

    first = ProductV2Backfill(
        db_session,
        apply=True,
        resume=False,
        batch_size=1,
        checkpoint_path=tmp_path / "companyless-checkpoint.json",
        quarantine_path=tmp_path / "companyless-quarantine.json",
    ).run()
    second = ProductV2Backfill(
        db_session,
        apply=True,
        resume=False,
        batch_size=1,
        checkpoint_path=tmp_path / "companyless-second-checkpoint.json",
        quarantine_path=tmp_path / "companyless-second-quarantine.json",
    ).run()

    assert first.checksum == second.checksum
    company = db_session.query(models.Company).one()
    assert company.name == "Company identity pending · Pending Buyer"
    assert company.normalized_domain is None
    assert db_session.query(models.Contact).count() == 1
    assert db_session.query(models.ContactPoint).count() == 1
    assert db_session.query(models.SafetyLock).count() == 1
    assert db_session.query(models.Task).filter_by(
        task_type=TaskType.DATA_GOVERNANCE,
        queue_scope=TaskQueueScope.DATA_GOVERNANCE,
    ).count() == 1

    original_company_id = company.id
    lead.company_name = "Verified Buyer Company"
    lead.domain = "verified-buyer.example"
    db_session.commit()
    ProductV2Backfill(
        db_session,
        apply=True,
        resume=False,
        batch_size=1,
        checkpoint_path=tmp_path / "companyless-enriched-checkpoint.json",
        quarantine_path=tmp_path / "companyless-enriched-quarantine.json",
    ).run()

    upgraded = db_session.query(models.Company).one()
    assert upgraded.id == original_company_id
    assert upgraded.name == "Verified Buyer Company"
    assert upgraded.normalized_domain == "verified-buyer.example"
    assert upgraded.website == "https://verified-buyer.example"
    assert db_session.query(models.Contact).count() == 1
    assert db_session.query(models.SafetyLock).filter_by(active=True).count() == 1


def test_backfill_binds_legacy_email_accounts_without_copying_credentials(
    db_session,
    tmp_path,
):
    owner = legacy.User(username="mailbox-owner", hashed_password="x", is_active=True)
    db_session.add(owner)
    db_session.flush()
    source = legacy.EmailAccount(
        user_id=owner.id,
        email="sender@example.com",
        display_name="Sender",
        smtp_host="smtp.example.com",
        smtp_port=465,
        smtp_user="sender@example.com",
        smtp_pass="DO-NOT-COPY-ME",
        use_ssl=True,
        use_tls=False,
        imap_host="imap.example.com",
        imap_port=993,
    )
    db_session.add(source)
    db_session.commit()

    first = ProductV2Backfill(
        db_session,
        apply=True,
        resume=False,
        checkpoint_path=tmp_path / "mailbox-checkpoint.json",
        quarantine_path=tmp_path / "mailbox-quarantine.json",
    ).run()
    second = ProductV2Backfill(
        db_session,
        apply=True,
        resume=True,
        checkpoint_path=tmp_path / "mailbox-checkpoint.json",
        quarantine_path=tmp_path / "mailbox-quarantine.json",
    ).run()

    assert first.checksum == second.checksum
    account = db_session.query(models.ChannelAccount).one()
    assert account.owner_id == owner.id
    assert account.provider == "smtp"
    assert account.provider_account_id == source.email
    assert account.legacy_email_account_id == source.id
    assert account.daily_limit == 20
    assert account.health_status.value == "unknown"
    persisted = [getattr(account, column.name) for column in account.__table__.columns]
    assert "DO-NOT-COPY-ME" not in repr(persisted)


def test_backfill_quarantines_provider_usage_with_workflow_lead_owner_conflict(
    db_session,
    tmp_path,
):
    # GIVEN: Provider usage points at one owner's Workflow and another owner's Lead.
    workflow_owner = legacy.User(username="usage-workflow-owner", hashed_password="x", is_active=True)
    lead_owner = legacy.User(username="usage-lead-owner", hashed_password="x", is_active=True)
    db_session.add_all([workflow_owner, lead_owner])
    db_session.flush()
    workflow_a = legacy.Workflow(
        user_id=workflow_owner.id,
        name="Owner A workflow",
        search_keywords="buyer",
        target_positions="purchasing",
    )
    workflow_b = legacy.Workflow(
        user_id=lead_owner.id,
        name="Owner B workflow",
        search_keywords="buyer",
        target_positions="purchasing",
    )
    db_session.add_all([workflow_a, workflow_b])
    db_session.flush()
    lead = legacy.Lead(
        workflow_id=workflow_b.id,
        domain="provider-owner.example",
        company_name="Provider Owner",
        email="buyer@provider-owner.example",
    )
    db_session.add(lead)
    db_session.flush()
    usage = legacy.ProviderUsageEvent(
        provider="snovio",
        operation="email_lookup",
        workflow_id=workflow_a.id,
        lead_id=lead.id,
        status="success",
        units=1,
        estimated_credits=2,
        result_count=1,
    )
    db_session.add(usage)
    db_session.commit()

    # WHEN: Provider costs are attributed during backfill.
    report = ProductV2Backfill(
        db_session,
        apply=True,
        resume=False,
        batch_size=10,
        checkpoint_path=tmp_path / "provider-owner-checkpoint.json",
        quarantine_path=tmp_path / "provider-owner-quarantine.json",
    ).run()

    # THEN: Cross-tenant attribution is quarantined rather than guessed.
    conflicts = [
        item for item in report.quarantine
        if item.source_table == "provider_usage_events"
    ]
    assert len(conflicts) == 1
    assert conflicts[0].source_id == usage.id
    assert conflicts[0].reason == "workflow_lead_owner_conflict"
    assert conflicts[0].details["workflow_owner_id"] == workflow_owner.id
    assert conflicts[0].details["lead_owner_id"] == lead_owner.id
    assert db_session.query(models.ProviderCostEvent).count() == 0


def test_backfill_persists_quarantine_before_advancing_checkpoint(
    db_session,
    tmp_path,
    monkeypatch,
):
    # GIVEN: A pending quarantine item and an apply-mode backfill.
    checkpoint = tmp_path / "ordered-checkpoint.json"
    quarantine = tmp_path / "ordered-quarantine.json"
    backfill = ProductV2Backfill(
        db_session,
        apply=True,
        resume=False,
        checkpoint_path=checkpoint,
        quarantine_path=quarantine,
    )
    backfill.report.quarantine.append(QuarantineItem("leads", 42, "manual_review"))
    writes = []

    def record_write(path, payload):
        writes.append((path, payload))

    monkeypatch.setattr(backfill, "_atomic_json", record_write)

    # WHEN: The durable progress marker is written.
    backfill._checkpoint(42)

    # THEN: Review state is durable before resume can skip the source row.
    assert [path for path, _ in writes] == [quarantine, checkpoint]
    assert writes[0][1]["items"][0]["source_id"] == 42
    assert writes[1][1]["last_lead_id"] == 42


def test_backfill_checksum_covers_business_fields_foreign_keys_status_and_amounts(
    db_session,
    tmp_path,
):
    # GIVEN: A migrated graph with status, foreign keys and provider cost amounts.
    user = legacy.User(username="checksum-owner", hashed_password="x", is_active=True)
    db_session.add(user)
    db_session.flush()
    workflow = legacy.Workflow(
        user_id=user.id,
        name="Checksum workflow",
        search_keywords="buyer",
        target_positions="purchasing",
    )
    db_session.add(workflow)
    db_session.flush()
    lead = legacy.Lead(
        workflow_id=workflow.id,
        domain="checksum.example",
        company_name="Checksum Buyer",
        email="buyer@checksum.example",
        first_name="Check",
        last_name="Sum",
    )
    db_session.add(lead)
    db_session.flush()
    db_session.add(
        legacy.ProviderUsageEvent(
            provider="snovio",
            operation="email_lookup",
            workflow_id=workflow.id,
            lead_id=lead.id,
            status="success",
            units=1,
            estimated_credits=2,
            result_count=1,
        )
    )
    db_session.commit()
    backfill = ProductV2Backfill(
        db_session,
        apply=True,
        resume=False,
        batch_size=10,
        checkpoint_path=tmp_path / "checksum-checkpoint.json",
        quarantine_path=tmp_path / "checksum-quarantine.json",
    )
    report = backfill.run()
    baseline = report.checksum
    contact = db_session.query(models.Contact).one()
    enrollment = db_session.query(models.Enrollment).one()
    cost = db_session.query(models.ProviderCostEvent).one()

    # WHEN/THEN: Stable business content changes the checksum.
    original_name = contact.full_name
    contact.full_name = "Changed Business Name"
    db_session.flush()
    assert backfill.checksum() != baseline
    contact.full_name = original_name
    db_session.flush()
    assert backfill.checksum() == baseline

    original_status = enrollment.status
    enrollment.status = EnrollmentStatus.BLOCKED
    db_session.flush()
    assert backfill.checksum() != baseline
    enrollment.status = original_status
    db_session.flush()
    assert backfill.checksum() == baseline

    original_units = cost.units
    cost.units = Decimal("7")
    db_session.flush()
    assert backfill.checksum() != baseline
    cost.units = original_units
    db_session.flush()
    assert backfill.checksum() == baseline

    original_contact_id = cost.contact_id
    cost.contact_id = None
    db_session.flush()
    assert backfill.checksum() != baseline
    cost.contact_id = original_contact_id
    db_session.flush()
    assert backfill.checksum() == baseline

    # Generated time changes are deliberately excluded from repeatability checks.
    contact.updated_at = contact.updated_at + timedelta(days=1)
    db_session.flush()
    assert backfill.checksum() == baseline

    # MySQL consumes AUTO_INCREMENT values even when a dry-run rolls back.
    # Moving one generated PK together with every declared V2 FK must therefore
    # leave the logical checksum unchanged.
    old_contact_id = contact.id
    shifted_contact_id = old_contact_id + 10_000
    for table_name in sorted(models.V2_TABLE_NAMES):
        table = models.Base.metadata.tables[table_name]
        for column in table.c:
            if any(
                foreign_key.target_fullname == "v2_contacts.id"
                for foreign_key in column.foreign_keys
            ):
                db_session.execute(
                    table.update()
                    .where(column == old_contact_id)
                    .values({column.name: shifted_contact_id})
                )
    db_session.execute(
        models.Contact.__table__.update()
        .where(models.Contact.id == old_contact_id)
        .values(id=shifted_contact_id)
    )
    db_session.expire_all()
    assert backfill.checksum() == baseline
