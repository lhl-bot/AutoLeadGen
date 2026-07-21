from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

import models as legacy
from product_v2 import models
from product_v2.enums import (
    CampaignLifecycle,
    CampaignRevisionStatus,
    Channel,
    EnrollmentStatus,
    OpportunityStage,
    JobStatus,
    ProviderCostStatus,
    ReplyAssessmentStatus,
    ReplyIntent,
    TaskStatus,
    TaskType,
)
from product_v2.schemas import OpportunityConfirm, OpportunityStageUpdate
from product_v2.services.domain import (
    confirm_opportunity,
    confirm_reply_assessment,
    campaign_revision_diff,
    campaign_revision_diff_checksum,
    campaign_budget_snapshot,
    create_enrollment,
    publish_campaign_revision,
    update_opportunity_stage,
    validate_campaign_command,
)
from product_v2.runtime import worker as job_worker


def _campaign(db, owner_id: int, name: str):
    campaign = models.Campaign(owner_id=owner_id, name=name)
    db.add(campaign)
    db.flush()
    revision = models.CampaignRevision(
        owner_id=owner_id,
        campaign_id=campaign.id,
        revision_number=1,
        status=CampaignRevisionStatus.PUBLISHED,
        icp_definition={"published": True},
        quality_gates={"require_evidence": False, "require_timezone": False},
        budget_definition={"native_limit": 100},
    )
    db.add(revision)
    db.flush()
    campaign.published_revision_number = 1
    db.add(models.SequenceStep(owner_id=owner_id, campaign_revision_id=revision.id, position=1, channel=Channel.EMAIL))
    return campaign, revision


def _company_contact(db, owner_id: int, suffix: str, company=None):
    company = company or models.Company(owner_id=owner_id, name="Buyer Co", normalized_domain="buyer.example")
    if company.id is None:
        db.add(company)
        db.flush()
    contact = models.Contact(owner_id=owner_id, company_id=company.id, full_name=f"Buyer {suffix}", timezone="UTC")
    db.add(contact)
    db.flush()
    point = models.ContactPoint(
        owner_id=owner_id,
        company_id=company.id,
        contact_id=contact.id,
        channel=Channel.EMAIL,
        value=f"{suffix}@buyer.example",
        normalized_value=f"{suffix}@buyer.example",
        verification_status="valid",
    )
    db.add(point)
    db.flush()
    return company, contact, point


def test_same_contact_can_join_multiple_campaigns_but_company_cap_is_two(db_session):
    user = legacy.User(username="campaign-owner", hashed_password="x", is_active=True)
    db_session.add(user)
    db_session.flush()
    campaign_a, _ = _campaign(db_session, user.id, "A")
    campaign_b, _ = _campaign(db_session, user.id, "B")
    company, contact_one, _ = _company_contact(db_session, user.id, "one")
    _, contact_two, _ = _company_contact(db_session, user.id, "two", company)
    _, contact_three, _ = _company_contact(db_session, user.id, "three", company)

    create_enrollment(db_session, campaign=campaign_a, contact=contact_one, idempotency_key="enroll-a-one", scheduled_at=None, actor_user_id=user.id)
    create_enrollment(db_session, campaign=campaign_a, contact=contact_two, idempotency_key="enroll-a-two", scheduled_at=None, actor_user_id=user.id)
    create_enrollment(db_session, campaign=campaign_b, contact=contact_one, idempotency_key="enroll-b-one", scheduled_at=None, actor_user_id=user.id)

    with pytest.raises(ValueError, match="at most two"):
        create_enrollment(db_session, campaign=campaign_a, contact=contact_three, idempotency_key="enroll-a-three", scheduled_at=None, actor_user_id=user.id)

    assert db_session.query(models.Enrollment).filter_by(contact_id=contact_one.id).count() == 2


def test_campaign_lifecycle_commands_are_ordered_and_resume_only_campaign_pauses(db_session, monkeypatch):
    user = legacy.User(username="campaign-lifecycle-owner", hashed_password="x", is_active=True)
    db_session.add(user)
    db_session.flush()
    campaign, revision = _campaign(db_session, user.id, "Lifecycle")
    company, contact, _ = _company_contact(db_session, user.id, "lifecycle")

    campaign.lifecycle = CampaignLifecycle.DRAFT
    with pytest.raises(ValueError, match="cannot start from draft"):
        validate_campaign_command(campaign, "start")

    campaign.lifecycle = CampaignLifecycle.PAUSED
    enrollment = models.Enrollment(
        owner_id=user.id,
        campaign_id=campaign.id,
        campaign_revision_id=revision.id,
        company_id=company.id,
        contact_id=contact.id,
        status=EnrollmentStatus.PAUSED,
        paused_reason="campaign_paused",
    )
    protected_enrollment = models.Enrollment(
        owner_id=user.id,
        campaign_id=campaign.id,
        campaign_revision_id=revision.id,
        company_id=company.id,
        contact_id=contact.id + 1,
        status=EnrollmentStatus.PAUSED,
        paused_reason="positive_reply",
    )
    # The second row needs an owned Contact but must remain paused for its safety reason.
    _, protected_contact, _ = _company_contact(db_session, user.id, "protected", company)
    protected_enrollment.contact_id = protected_contact.id
    db_session.add_all((enrollment, protected_enrollment))
    db_session.flush()
    job = models.AutomationJob(
        owner_id=user.id,
        campaign_id=campaign.id,
        job_type="campaign.start",
        queue="campaign",
        payload={"campaign_id": campaign.id, "confirm_warnings": False},
        idempotency_key="campaign-resume-test",
        status=JobStatus.CLAIMED,
        attempts=1,
    )
    db_session.add(job)
    db_session.flush()
    monkeypatch.setattr(
        job_worker,
        "campaign_readiness",
        lambda *_args, **_kwargs: type("Readiness", (), {"ready": True, "warnings": []})(),
    )

    job_worker.execute_job(db_session, job)

    assert campaign.lifecycle == CampaignLifecycle.RUNNING
    assert enrollment.status == EnrollmentStatus.ACTIVE
    assert enrollment.paused_reason is None
    assert protected_enrollment.status == EnrollmentStatus.PAUSED
    assert protected_enrollment.paused_reason == "positive_reply"


def test_positive_reply_requires_human_confirmation_before_opportunity(db_session):
    user = legacy.User(username="sales-owner", hashed_password="x", is_active=True)
    db_session.add(user)
    db_session.flush()
    campaign_a, revision_a = _campaign(db_session, user.id, "Primary")
    campaign_b, revision_b = _campaign(db_session, user.id, "Other")
    company, contact, point = _company_contact(db_session, user.id, "primary")
    current = models.Enrollment(
        owner_id=user.id,
        campaign_id=campaign_a.id,
        campaign_revision_id=revision_a.id,
        company_id=company.id,
        contact_id=contact.id,
        status=EnrollmentStatus.ACTIVE,
    )
    other = models.Enrollment(
        owner_id=user.id,
        campaign_id=campaign_b.id,
        campaign_revision_id=revision_b.id,
        company_id=company.id,
        contact_id=contact.id,
        status=EnrollmentStatus.ACTIVE,
    )
    db_session.add_all([current, other])
    db_session.flush()
    conversation = models.Conversation(
        owner_id=user.id,
        company_id=company.id,
        contact_id=contact.id,
        contact_point_id=point.id,
        channel=Channel.EMAIL,
        latest_reply_body="Please quote 500 pieces",
    )
    db_session.add(conversation)
    db_session.flush()
    assessment = models.ReplyAssessment(
        owner_id=user.id,
        conversation_id=conversation.id,
        enrollment_id=current.id,
        intent=ReplyIntent.MORE_INFO,
        is_positive=True,
        status=ReplyAssessmentStatus.PROPOSED,
        latest_reply_body="Please quote 500 pieces",
    )
    db_session.add(assessment)
    db_session.flush()

    handoff = confirm_reply_assessment(
        db_session,
        assessment=assessment,
        actor_user_id=user.id,
        intent=ReplyIntent.MORE_INFO,
        is_positive=True,
    )

    assert assessment.status == ReplyAssessmentStatus.CONFIRMED
    assert current.status == EnrollmentStatus.PAUSED
    assert current.positive_signal_at is not None
    assert other.status == EnrollmentStatus.PAUSED
    assert other.positive_signal_at is not None
    assert db_session.query(models.Opportunity).count() == 0

    foreign_override = models.ManualOverride(
        owner_id=user.id,
        gate="fit",
        enrollment_id=other.id,
        reason="Approved only for the other Campaign",
        expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        created_by_user_id=user.id,
    )
    db_session.add(foreign_override)
    db_session.flush()
    with pytest.raises(ValueError, match="fit override"):
        confirm_opportunity(
            db_session,
            owner_id=user.id,
            actor_user_id=user.id,
            data=OpportunityConfirm(
                reply_assessment_id=assessment.id,
                source_task_id=handoff.id,
                assignee_user_id=user.id,
                fit_confirmed=False,
                fit_override_id=foreign_override.id,
                next_action="Prepare sample and quote",
                next_action_due_at=datetime.now(timezone.utc) + timedelta(days=2),
            ),
        )

    data = OpportunityConfirm(
        reply_assessment_id=assessment.id,
        source_task_id=handoff.id,
        assignee_user_id=user.id,
        fit_confirmed=True,
        next_action="Prepare sample and quote",
        next_action_due_at=datetime.now(timezone.utc) + timedelta(days=2),
    )
    opportunity = confirm_opportunity(db_session, owner_id=user.id, actor_user_id=user.id, data=data)

    assert opportunity.stage == OpportunityStage.QUALIFIED_REPLY
    assert handoff.status == TaskStatus.COMPLETED
    assert current.status == EnrollmentStatus.PAUSED

    update_opportunity_stage(
        db_session,
        opportunity=opportunity,
        actor_user_id=user.id,
        data=OpportunityStageUpdate(stage=OpportunityStage.LOST, lost_reason="Budget was withdrawn"),
    )
    with pytest.raises(ValueError, match="Terminal Opportunity stage lost"):
        update_opportunity_stage(
            db_session,
            opportunity=opportunity,
            actor_user_id=user.id,
            data=OpportunityStageUpdate(stage=OpportunityStage.DISCOVERY),
        )


def test_won_and_lost_require_terminal_fields(db_session):
    with pytest.raises(ValidationError, match="Won requires"):
        OpportunityStageUpdate(stage=OpportunityStage.WON)
    with pytest.raises(ValidationError, match="Lost requires"):
        OpportunityStageUpdate(stage=OpportunityStage.LOST)


def test_non_positive_confirmation_never_creates_sales_handoff(db_session):
    # GIVEN: An AI-proposed reply that a human confirms as non-positive.
    user = legacy.User(username="negative-reply-owner", hashed_password="x", is_active=True)
    db_session.add(user)
    db_session.flush()
    company, contact, point = _company_contact(db_session, user.id, "negative")
    conversation = models.Conversation(
        owner_id=user.id,
        company_id=company.id,
        contact_id=contact.id,
        contact_point_id=point.id,
        channel=Channel.EMAIL,
        latest_reply_body="Not a fit for us",
    )
    db_session.add(conversation)
    db_session.flush()
    assessment = models.ReplyAssessment(
        owner_id=user.id,
        conversation_id=conversation.id,
        intent=ReplyIntent.OTHER,
        is_positive=False,
        status=ReplyAssessmentStatus.PROPOSED,
        latest_reply_body="Not a fit for us",
    )
    db_session.add(assessment)
    db_session.flush()

    # WHEN: Sales confirms the reply as not interested.
    task = confirm_reply_assessment(
        db_session,
        assessment=assessment,
        actor_user_id=user.id,
        intent=ReplyIntent.NOT_INTERESTED,
        is_positive=False,
    )

    # THEN: Triage closes without creating a sales handoff or opportunity.
    assert task.task_type == TaskType.REPLY_TRIAGE
    assert task.status == TaskStatus.COMPLETED
    assert db_session.query(models.Task).filter_by(task_type=TaskType.SALES_HANDOFF).count() == 0
    assert db_session.query(models.Opportunity).count() == 0


def test_reply_positive_flag_cannot_contradict_confirmed_intent(db_session):
    # GIVEN: A reply assessment awaiting a human intent decision.
    user = legacy.User(username="contradictory-reply-owner", hashed_password="x", is_active=True)
    db_session.add(user)
    db_session.flush()
    company, contact, point = _company_contact(db_session, user.id, "contradictory")
    conversation = models.Conversation(
        owner_id=user.id,
        company_id=company.id,
        contact_id=contact.id,
        contact_point_id=point.id,
        channel=Channel.EMAIL,
    )
    db_session.add(conversation)
    db_session.flush()
    assessment = models.ReplyAssessment(
        owner_id=user.id,
        conversation_id=conversation.id,
        intent=ReplyIntent.OTHER,
        is_positive=False,
        status=ReplyAssessmentStatus.PROPOSED,
        latest_reply_body="unsubscribe",
    )
    db_session.add(assessment)
    db_session.flush()

    # WHEN/THEN: UNSUBSCRIBE can never be confirmed as a positive sales signal.
    with pytest.raises(ValueError, match="positivity must match"):
        confirm_reply_assessment(
            db_session,
            assessment=assessment,
            actor_user_id=user.id,
            intent=ReplyIntent.UNSUBSCRIBE,
            is_positive=True,
        )
    assert assessment.status == ReplyAssessmentStatus.PROPOSED
    assert db_session.query(models.Task).filter_by(task_type=TaskType.SALES_HANDOFF).count() == 0


def test_icp_change_stays_draft_until_human_publishes_versioned_diff(db_session):
    # GIVEN: A published ICP and an immutable draft proposal with changed criteria.
    user = legacy.User(username="revision-owner", hashed_password="x", is_active=True)
    db_session.add(user)
    db_session.flush()
    campaign, published = _campaign(db_session, user.id, "ICP proposal")
    draft = models.CampaignRevision(
        owner_id=user.id,
        campaign_id=campaign.id,
        revision_number=2,
        status=CampaignRevisionStatus.DRAFT,
        icp_definition={"published": True, "industry": "home textile"},
        audience_definition={},
        quality_gates={"min_fit_score": 70},
        budget_definition={"native_limit": 100},
        stop_conditions={},
    )
    db_session.add(draft)
    db_session.flush()
    db_session.add(models.SequenceStep(
        owner_id=user.id,
        campaign_revision_id=draft.id,
        position=1,
        channel=Channel.LINKEDIN,
        wait_minutes=30,
        template_version="linkedin-v2",
        condition_definition={"fake_only": True},
        stop_condition_definition={"stop_on_reply": True},
    ))
    db_session.flush()

    # WHEN: The proposal is previewed and then explicitly published twice with one key.
    base, diff = campaign_revision_diff(db_session, campaign=campaign, proposed=draft)
    checksum = campaign_revision_diff_checksum(
        campaign_id=campaign.id,
        base_revision_id=base.id,
        proposed_revision_id=draft.id,
        diff=diff,
    )
    with pytest.raises(ValueError, match="checksum"):
        publish_campaign_revision(
            db_session,
            campaign=campaign,
            revision=draft,
            actor_user_id=user.id,
            idempotency_key="publish-revision-mismatch",
            base_revision_id=base.id,
            reviewed_diff_checksum="0" * 64,
            human_confirmed=True,
        )
    first = publish_campaign_revision(
        db_session,
        campaign=campaign,
        revision=draft,
        actor_user_id=user.id,
        idempotency_key="publish-revision-0001",
        base_revision_id=base.id,
        reviewed_diff_checksum=checksum,
        human_confirmed=True,
    )
    duplicate = publish_campaign_revision(
        db_session,
        campaign=campaign,
        revision=draft,
        actor_user_id=user.id,
        idempotency_key="publish-revision-0001",
        base_revision_id=base.id,
        reviewed_diff_checksum=checksum,
        human_confirmed=True,
    )
    db_session.flush()

    # THEN: The diff is explicit, content is unchanged, and publish is idempotent and audited.
    assert base.id == published.id
    assert any(item["path"] == "icp_definition.industry" for item in diff["added"])
    assert any(item["path"] == "sequence_steps" for item in diff["changed"])
    assert first.id == duplicate.id == draft.id
    assert draft.icp_definition == {"published": True, "industry": "home textile"}
    assert draft.status == CampaignRevisionStatus.PUBLISHED
    assert published.status == CampaignRevisionStatus.SUPERSEDED
    assert campaign.published_revision_number == 2
    assert db_session.query(models.AuditEvent).filter_by(action="campaign_revision.published").count() == 1


def test_human_confirmed_unsubscribe_creates_one_scoped_restriction_and_pauses_affected_enrollments(
    db_session,
):
    user = legacy.User(username="unsubscribe-owner", hashed_password="x", is_active=True)
    db_session.add(user)
    db_session.flush()
    email_campaign, email_revision = _campaign(db_session, user.id, "Email current")
    email_campaign_two, email_revision_two = _campaign(db_session, user.id, "Email other")
    linkedin_campaign, linkedin_revision = _campaign(db_session, user.id, "LinkedIn only")
    db_session.flush()
    linkedin_step = db_session.query(models.SequenceStep).filter_by(
        campaign_revision_id=linkedin_revision.id
    ).one()
    linkedin_step.channel = Channel.LINKEDIN
    company, contact, point = _company_contact(db_session, user.id, "unsubscribe")
    current = models.Enrollment(
        owner_id=user.id,
        campaign_id=email_campaign.id,
        campaign_revision_id=email_revision.id,
        company_id=company.id,
        contact_id=contact.id,
        status=EnrollmentStatus.ACTIVE,
    )
    other_email = models.Enrollment(
        owner_id=user.id,
        campaign_id=email_campaign_two.id,
        campaign_revision_id=email_revision_two.id,
        company_id=company.id,
        contact_id=contact.id,
        status=EnrollmentStatus.SCHEDULED,
    )
    linkedin_only = models.Enrollment(
        owner_id=user.id,
        campaign_id=linkedin_campaign.id,
        campaign_revision_id=linkedin_revision.id,
        company_id=company.id,
        contact_id=contact.id,
        status=EnrollmentStatus.ACTIVE,
    )
    db_session.add_all((current, other_email, linkedin_only))
    db_session.flush()
    conversation = models.Conversation(
        owner_id=user.id,
        company_id=company.id,
        contact_id=contact.id,
        contact_point_id=point.id,
        channel=Channel.EMAIL,
    )
    db_session.add(conversation)
    db_session.flush()
    assessment = models.ReplyAssessment(
        owner_id=user.id,
        conversation_id=conversation.id,
        enrollment_id=current.id,
        intent=ReplyIntent.OTHER,
        is_positive=False,
        status=ReplyAssessmentStatus.PROPOSED,
        latest_reply_body="Please unsubscribe me",
    )
    db_session.add(assessment)
    db_session.flush()

    first_task = confirm_reply_assessment(
        db_session,
        assessment=assessment,
        actor_user_id=user.id,
        intent=ReplyIntent.UNSUBSCRIBE,
        is_positive=False,
    )
    replayed_task = confirm_reply_assessment(
        db_session,
        assessment=assessment,
        actor_user_id=user.id,
        intent=ReplyIntent.UNSUBSCRIBE,
        is_positive=False,
    )
    db_session.flush()

    restriction = db_session.query(models.ConsentRestriction).one()
    assert restriction.scope.value == "contact_point"
    assert restriction.channel == Channel.EMAIL
    assert restriction.contact_point_id == point.id
    assert restriction.contact_id is None
    assert restriction.company_id is None
    assert current.status == EnrollmentStatus.PAUSED
    assert other_email.status == EnrollmentStatus.PAUSED
    assert linkedin_only.status == EnrollmentStatus.ACTIVE
    assert first_task.id == replayed_task.id
    assert db_session.query(models.ConsentRestriction).count() == 1
    assert db_session.query(models.AuditEvent).filter_by(
        action="consent_restriction.created"
    ).count() == 1


def test_unsubscribe_without_contact_point_restricts_contact_and_pauses_all_channels(db_session):
    user = legacy.User(username="contact-unsubscribe-owner", hashed_password="x", is_active=True)
    db_session.add(user)
    db_session.flush()
    campaign_a, revision_a = _campaign(db_session, user.id, "Contact A")
    campaign_b, revision_b = _campaign(db_session, user.id, "Contact B")
    company, contact, _ = _company_contact(db_session, user.id, "contact-unsubscribe")
    enrollments = [
        models.Enrollment(
            owner_id=user.id,
            campaign_id=campaign.id,
            campaign_revision_id=revision.id,
            company_id=company.id,
            contact_id=contact.id,
            status=status,
        )
        for campaign, revision, status in (
            (campaign_a, revision_a, EnrollmentStatus.SCHEDULED),
            (campaign_b, revision_b, EnrollmentStatus.ACTIVE),
        )
    ]
    db_session.add_all(enrollments)
    db_session.flush()
    conversation = models.Conversation(
        owner_id=user.id,
        company_id=company.id,
        contact_id=contact.id,
        contact_point_id=None,
        channel=Channel.OFFLINE,
    )
    db_session.add(conversation)
    db_session.flush()
    assessment = models.ReplyAssessment(
        owner_id=user.id,
        conversation_id=conversation.id,
        intent=ReplyIntent.OTHER,
        is_positive=False,
        status=ReplyAssessmentStatus.PROPOSED,
        latest_reply_body="Do not contact me again",
    )
    db_session.add(assessment)
    db_session.flush()

    confirm_reply_assessment(
        db_session,
        assessment=assessment,
        actor_user_id=user.id,
        intent=ReplyIntent.UNSUBSCRIBE,
        is_positive=False,
    )
    db_session.flush()

    restriction = db_session.query(models.ConsentRestriction).one()
    assert restriction.scope.value == "contact"
    assert restriction.channel is None
    assert restriction.contact_id == contact.id
    assert all(row.status == EnrollmentStatus.PAUSED for row in enrollments)


def test_revision_publish_rejects_a_stale_reviewed_base(db_session):
    user = legacy.User(username="stale-revision-owner", hashed_password="x", is_active=True)
    db_session.add(user)
    db_session.flush()
    campaign, base = _campaign(db_session, user.id, "Stale review")
    stale = models.CampaignRevision(
        owner_id=user.id,
        campaign_id=campaign.id,
        revision_number=2,
        status=CampaignRevisionStatus.DRAFT,
        icp_definition={"proposal": "stale"},
    )
    winner = models.CampaignRevision(
        owner_id=user.id,
        campaign_id=campaign.id,
        revision_number=3,
        status=CampaignRevisionStatus.DRAFT,
        icp_definition={"proposal": "winner"},
    )
    db_session.add_all((stale, winner))
    db_session.flush()
    stale_base, stale_diff = campaign_revision_diff(db_session, campaign=campaign, proposed=stale)
    stale_checksum = campaign_revision_diff_checksum(
        campaign_id=campaign.id,
        base_revision_id=stale_base.id,
        proposed_revision_id=stale.id,
        diff=stale_diff,
    )
    winner_base, winner_diff = campaign_revision_diff(db_session, campaign=campaign, proposed=winner)
    winner_checksum = campaign_revision_diff_checksum(
        campaign_id=campaign.id,
        base_revision_id=winner_base.id,
        proposed_revision_id=winner.id,
        diff=winner_diff,
    )
    publish_campaign_revision(
        db_session,
        campaign=campaign,
        revision=winner,
        actor_user_id=user.id,
        idempotency_key="publish-winning-revision",
        base_revision_id=base.id,
        reviewed_diff_checksum=winner_checksum,
        human_confirmed=True,
    )

    with pytest.raises(ValueError, match="stale"):
        publish_campaign_revision(
            db_session,
            campaign=campaign,
            revision=stale,
            actor_user_id=user.id,
            idempotency_key="publish-stale-revision",
            base_revision_id=base.id,
            reviewed_diff_checksum=stale_checksum,
            human_confirmed=True,
        )


def test_billable_failed_provider_calls_still_consume_campaign_budget(db_session):
    # GIVEN: A real Provider call consumed a paid unit even though the
    # Provider rejected the outreach result, plus a confirmed non-billable miss.
    user = legacy.User(username="paid-miss-owner", hashed_password="x", is_active=True)
    db_session.add(user)
    db_session.flush()
    campaign, revision = _campaign(db_session, user.id, "Paid miss budget")
    revision.budget_definition = {"native_limit": 5, "native_unit": "credits"}
    db_session.add_all(
        (
            models.ProviderCostEvent(
                owner_id=user.id,
                provider="paid-provider",
                operation="email_send",
                status=ProviderCostStatus.FAILED,
                units=2,
                native_unit="credits",
                billable=True,
                price_version="paid-v1",
                campaign_id=campaign.id,
                idempotency_key="paid-failed-call",
            ),
            models.ProviderCostEvent(
                owner_id=user.id,
                provider="paid-provider",
                operation="email_send",
                status=ProviderCostStatus.FAILED,
                units=3,
                native_unit="credits",
                billable=False,
                price_version="paid-v1",
                campaign_id=campaign.id,
                idempotency_key="nonbillable-failed-call",
            ),
        )
    )
    db_session.flush()

    # WHEN: Readiness/outbound calculates the remaining native budget.
    snapshot = campaign_budget_snapshot(db_session, revision, campaign.id)

    # THEN: paid misses count, while explicit non-billable failures do not.
    assert snapshot.used == 2
    assert snapshot.remaining == 3
