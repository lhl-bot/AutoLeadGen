import asyncio

import httpx

import models
from product_v2 import models as v2_models
from product_v2.enums import Channel, RestrictionScope
from services.suppression import ensure_suppression, generate_unsubscribe_token


def _lead(db_session):
    user = models.User(username="unsubscribe-owner", hashed_password="x", is_active=True)
    db_session.add(user)
    db_session.flush()
    workflow = models.Workflow(
        user_id=user.id,
        name="Consent safety",
        search_keywords="buyers",
        target_positions="purchasing",
    )
    db_session.add(workflow)
    db_session.flush()
    lead = models.Lead(
        workflow_id=workflow.id,
        company_name="Example Buyer",
        domain="example.com",
        email="buyer@example.com",
        status="sent",
    )
    db_session.add(lead)
    company = v2_models.Company(owner_id=user.id, name="Example Buyer", normalized_domain="example.com")
    db_session.add(company)
    db_session.flush()
    contact = v2_models.Contact(owner_id=user.id, company_id=company.id, full_name="Buyer")
    db_session.add(contact)
    db_session.flush()
    db_session.add(
        v2_models.ContactPoint(
            owner_id=user.id,
            company_id=company.id,
            contact_id=contact.id,
            channel=Channel.EMAIL,
            value="buyer@example.com",
            normalized_value="buyer@example.com",
            verification_status="valid",
        )
    )
    db_session.commit()
    db_session.refresh(lead)
    return lead


def test_email_suppression_does_not_implicitly_expand_to_domain(db_session):
    suppression = ensure_suppression(
        db_session,
        email="buyer@example.com",
        reason="unsubscribe",
        source="test",
    )
    db_session.commit()

    assert suppression.email == "buyer@example.com"
    assert suppression.domain is None


def test_unsubscribe_get_is_read_only_and_post_is_idempotent(db_session):
    # GIVEN: A sent lead with a valid one-click token.
    lead = _lead(db_session)
    token = generate_unsubscribe_token(lead.id, lead.email)

    import main

    async def run_flow():
        transport = httpx.ASGITransport(app=main.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            # WHEN: A link scanner performs GET before the recipient confirms.
            get_response = await client.get(f"/api/unsubscribe/{token}")

            # THEN: The confirmation page is shown with no database mutation.
            assert get_response.status_code == 200
            assert "Confirm unsubscribe" in get_response.text
            assert db_session.query(models.EmailSuppression).count() == 0
            assert db_session.query(v2_models.ConsentRestriction).count() == 0
            db_session.refresh(lead)
            assert lead.status == "sent"

            # WHEN: The recipient submits POST twice.
            first_post = await client.post(f"/api/unsubscribe/{token}")
            second_post = await client.post(f"/api/unsubscribe/{token}")

            # THEN: Both calls succeed but only one email-scoped restriction exists.
            assert first_post.status_code == 200
            assert second_post.status_code == 200

    asyncio.run(run_flow())

    restrictions = db_session.query(models.EmailSuppression).all()
    assert len(restrictions) == 1
    assert restrictions[0].email == "buyer@example.com"
    assert restrictions[0].domain is None
    v2_restrictions = db_session.query(v2_models.ConsentRestriction).all()
    assert len(v2_restrictions) == 1
    assert v2_restrictions[0].scope == RestrictionScope.CONTACT_POINT
    assert v2_restrictions[0].contact_id is None
    assert v2_restrictions[0].company_id is None
    db_session.refresh(lead)
    assert lead.status == "unsubscribed"
