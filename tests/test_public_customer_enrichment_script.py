import models
from scripts.enrich_legacy_customers_public_web import plan_apply
from services.public_web_enrichment import CompanyEvidence, PageEvidence


def test_plan_apply_creates_an_evidence_profile_for_every_lead(db_session):
    workflow = models.Workflow(
        id=18,
        user_id=1,
        name="test",
        status="active",
        email_sending_paused=True,
        search_keywords="bedding, sheets",
        target_positions="buyer",
        product_focus="home textiles",
    )
    verified = models.Lead(
        id=1,
        workflow_id=18,
        company_name="Acme Home",
        domain="acme.co.uk",
        email="ada@acme.co.uk",
        email_validation_status="valid",
        first_name="Ada",
        last_name="Buyer",
        job_title="Buyer",
        linkedin_url="https://linkedin.com/in/ada-buyer",
        status="found",
        source_channel="import",
    )
    unresolved = models.Lead(
        id=2,
        workflow_id=18,
        first_name="Ben",
        last_name="Buyer",
        job_title="Buyer",
        linkedin_url="https://linkedin.com/in/ben-buyer",
        status="needs_email",
        source_channel="import",
    )
    no_public_source = models.Lead(
        id=3,
        workflow_id=18,
        first_name="Cara",
        job_title="Buyer",
        status="needs_research",
        source_channel="import",
    )
    db_session.add_all([workflow, verified, unresolved, no_public_source])
    db_session.flush()
    evidence = CompanyEvidence(
        domain="acme.co.uk",
        collection_status="search_index",
        expected_company_name="Acme Home",
        homepage_url="https://acme.co.uk",
        pages=[
            PageEvidence(url="https://acme.co.uk", title="Acme Home"),
            PageEvidence(url="https://acme.co.uk/bedding", title="Bedding"),
        ],
        product_labels=["Bedding", "Sheets"],
        company_match_ratio=1.0,
    )

    report, _ = plan_apply(db_session, {
        "version": 1,
        "domains": {"acme.co.uk": evidence.to_dict()},
        "companies": {},
    })

    assert report["all_leads_have_brief"] is True
    assert verified.brief.research_status == "valid"
    assert unresolved.brief.research_status == "insufficient"
    assert unresolved.brief.company_overview
    assert unresolved.brief.evidence_sources == [
        {"type": "public_contact_profile", "value": unresolved.linkedin_url}
    ]
    assert verified.email == "ada@acme.co.uk"
    assert verified.status == "found"
    assert unresolved.status == "needs_email"
    assert no_public_source.brief.evidence_sources == [{
        "type": "legacy_database_record",
        "value": "lead_record:3",
    }]


def test_plan_apply_upgrades_an_unresolved_profile_when_retry_finds_verified_domain(db_session):
    workflow = models.Workflow(
        id=19,
        user_id=1,
        name="retry",
        status="paused",
        email_sending_paused=True,
        search_keywords="hotel bedding",
        target_positions="buyer",
    )
    lead = models.Lead(
        id=4,
        workflow_id=19,
        company_name="Verified Buyer",
        first_name="Dana",
        status="needs_email",
        source_channel="import",
    )
    db_session.add_all([workflow, lead])
    db_session.flush()
    db_session.add(models.LeadBrief(
        lead_id=lead.id,
        company_overview="The recorded company was not independently verified.",
        pain_points="Qualification pending.",
        value_proposition_alignment="No fit claim is made.",
        research_status="insufficient",
        quality_flags=["public_web:company_unresolved"],
        evidence_sources=[{"type": "legacy_database_record", "value": "lead_record:4"}],
    ))
    db_session.flush()
    evidence = CompanyEvidence(
        domain="verifiedbuyer.example",
        collection_status="search_index",
        expected_company_name="Verified Buyer",
        homepage_url="https://verifiedbuyer.example",
        pages=[PageEvidence(url="https://verifiedbuyer.example", title="Verified Buyer")],
        product_labels=["Hotel bedding"],
        company_match_ratio=1.0,
    )

    report, _ = plan_apply(db_session, {
        "version": 1,
        "domains": {},
        "companies": {"verified buyer": evidence.to_dict()},
    })

    assert report["affected_leads"] == 1
    assert report["counters"]["public_profile_upgraded"] == 1
    assert lead.domain == "verifiedbuyer.example"
    assert "public_web:evidence_first" in lead.brief.quality_flags
    assert "public_web:company_unresolved" not in lead.brief.quality_flags
    assert lead.brief.evidence_sources[0] == {
        "type": "official_indexed_page",
        "value": "https://verifiedbuyer.example",
    }


def test_plan_apply_preserves_usable_stored_domain_when_public_evidence_disagrees(db_session):
    workflow = models.Workflow(
        id=20,
        user_id=1,
        name="domain-conflict",
        status="paused",
        email_sending_paused=True,
        search_keywords="buyer",
        target_positions="purchasing",
    )
    lead = models.Lead(
        id=5,
        workflow_id=20,
        company_name="Example Buyer",
        domain="stored.example",
        status="needs_research",
        source_channel="import",
    )
    db_session.add_all([workflow, lead])
    db_session.flush()
    evidence = CompanyEvidence(
        domain="examplebuyer.example",
        collection_status="search_index",
        expected_company_name="Example Buyer",
        homepage_url="https://examplebuyer.example",
        pages=[PageEvidence(url="https://examplebuyer.example", title="Example Buyer")],
        company_match_ratio=1.0,
    )

    report, _ = plan_apply(db_session, {
        "version": 1,
        "domains": {},
        "companies": {"example buyer": evidence.to_dict()},
    })

    assert lead.domain == "stored.example"
    assert report["counters"]["verified_domain_conflict"] == 1
    assert "public_web:stored_domain_conflict" in lead.brief.quality_flags
    assert "public_web:conflicting_domain=examplebuyer.example" in lead.brief.quality_flags
