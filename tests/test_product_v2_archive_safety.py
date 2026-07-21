import asyncio

import httpx

import models as legacy
from product_v2 import models
from product_v2.enums import (
    CampaignLifecycle,
    CampaignRevisionStatus,
    Channel,
    EnrollmentStatus,
    JobStatus,
)
from product_v2.runtime.outbound import create_first_attempt
from services.auth import get_current_user


def test_company_archive_cancels_pending_enrollment_work_and_cannot_reactivate(db_session):
    user = legacy.User(username="archive-safety-owner", hashed_password="x", is_active=True)
    db_session.add(user)
    db_session.flush()
    company = models.Company(owner_id=user.id, name="Archived Co", normalized_domain="archived.example")
    db_session.add(company)
    db_session.flush()
    contact = models.Contact(owner_id=user.id, company_id=company.id, full_name="Archived Buyer")
    db_session.add(contact)
    db_session.flush()
    point = models.ContactPoint(
        owner_id=user.id,
        company_id=company.id,
        contact_id=contact.id,
        channel=Channel.EMAIL,
        value="buyer@archived.example",
        normalized_value="buyer@archived.example",
        verification_status="valid",
    )
    campaign = models.Campaign(
        owner_id=user.id,
        name="Archive safety",
        lifecycle=CampaignLifecycle.READY,
        published_revision_number=1,
    )
    db_session.add_all((point, campaign))
    db_session.flush()
    revision = models.CampaignRevision(
        owner_id=user.id,
        campaign_id=campaign.id,
        revision_number=1,
        status=CampaignRevisionStatus.PUBLISHED,
    )
    db_session.add(revision)
    db_session.flush()
    db_session.add(models.SequenceStep(
        owner_id=user.id,
        campaign_revision_id=revision.id,
        position=1,
        channel=Channel.EMAIL,
    ))
    enrollment = models.Enrollment(
        owner_id=user.id,
        campaign_id=campaign.id,
        campaign_revision_id=revision.id,
        company_id=company.id,
        contact_id=contact.id,
        status=EnrollmentStatus.SCHEDULED,
    )
    db_session.add(enrollment)
    db_session.flush()
    job = models.AutomationJob(
        owner_id=user.id,
        campaign_id=campaign.id,
        enrollment_id=enrollment.id,
        job_type="enrollment.created",
        queue="campaign",
        idempotency_key="archive-enrollment-job",
        status=JobStatus.PENDING,
    )
    db_session.add(job)
    db_session.commit()
    import main

    main.app.dependency_overrides[get_current_user] = lambda: user

    async def archive_company():
        transport = httpx.ASGITransport(app=main.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.delete(f"/api/v2/companies/{company.id}")

    try:
        response = asyncio.run(archive_company())
    finally:
        main.app.dependency_overrides.clear()

    assert response.status_code == 200, response.text
    db_session.expire_all()
    archived_enrollment = db_session.get(models.Enrollment, enrollment.id)
    assert archived_enrollment.status == EnrollmentStatus.PAUSED
    assert archived_enrollment.paused_reason == "company_archived"
    assert db_session.get(models.AutomationJob, job.id).status == JobStatus.CANCELLED
    assert create_first_attempt(db_session, archived_enrollment) is None
    assert db_session.query(models.OutreachAttempt).count() == 0
