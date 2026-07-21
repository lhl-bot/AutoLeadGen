from services import company_enrichment
from services.research_quality import (
    assess_research,
    is_personal_email_domain,
    outbound_content_quality_reason,
    sanitize_brief_data,
)


def test_placeholders_are_removed_before_prompt_or_storage():
    cleaned, flags = sanitize_brief_data({
        "company_overview": "Information unavailable.",
        "specific_products": "None found",
        "personalization_hook": "No information available",
    })

    assert cleaned["company_overview"] is None
    assert cleaned["specific_products"] is None
    assert cleaned["personalization_hook"] is None
    assert "placeholder:specific_products" in flags


def test_valid_research_requires_product_hook_and_second_source():
    assessment = assess_research(
        domain="example-textiles.com",
        brief_data={
            "company_overview": "A home textile retailer.",
            "specific_products": "Bedding and duvet cover collection",
            "personalization_hook": "The company launched its linen duvet range this spring.",
        },
        target_terms=["bedding", "duvet"],
        source_labels=["leadcontact"],
    )

    assert assessment.status == "valid"
    assert assessment.evidence_level == "core"
    assert len(assessment.evidence_sources) >= 2


def test_personal_email_portal_is_invalid_research_source():
    assert is_personal_email_domain("gmail.com") is True
    assessment = assess_research(
        domain="gmail.com",
        brief_data={
            "specific_products": "Bedding",
            "personalization_hook": "A bedding range",
        },
        target_terms=["bedding"],
        source_labels=["leadcontact"],
    )
    assert assessment.status == "invalid_source"


def test_outbound_content_blocks_research_noise_and_placeholders():
    assert outbound_content_quality_reason("Hi Ana, None found. Can we talk?")
    assert outbound_content_quality_reason("Hi {{first_name}}") == "unresolved_template_placeholder"
    assert outbound_content_quality_reason("Hi Ana, I saw your linen bedding collection.") is None


def test_company_resolution_never_uses_personal_email_domain(monkeypatch):
    monkeypatch.setattr(company_enrichment, "search_company_results", lambda *a, **k: [{
        "domain": "acme-home.com",
        "title": "Acme Home Bedding",
        "snippet": "Official bedding and duvet retailer",
        "source": "tavily",
    }])
    resolution = company_enrichment.resolve_employee_company(
        {"companyName": "Acme Home", "email": "buyer@gmail.com", "title": "Buyer"},
        workflow_keywords="bedding retailer",
    )

    assert resolution.domain == "acme-home.com"
    assert resolution.relevance_verified is True

