import models
from services.lead_scoring import score_lead_fit


def _workflow():
    return models.Workflow(
        name="Home textiles",
        search_keywords="bedding duvet",
        target_positions="Buyer, Purchasing Manager",
        product_focus="bedding duvet cover",
    )


def _lead():
    return models.Lead(
        domain="retailer.example",
        company_name="Retailer",
        email="buyer@retailer.example",
        email_validation_status="valid",
        job_title="Buyer",
        source_channel="leadcontact",
    )


def test_no_product_evidence_caps_score_at_49():
    brief = models.LeadBrief(
        research_status="insufficient",
        company_overview="A retailer.",
        personalization_hook="The retailer opened a store.",
    )
    assert score_lead_fit(_lead(), workflow=_workflow(), brief=brief).score <= 49


def test_adjacent_textile_evidence_caps_score_at_69():
    brief = models.LeadBrief(
        research_status="insufficient",
        company_overview="A fashion textile company.",
        specific_products="Woven apparel fabric",
        personalization_hook="The company launched a woven fabric range.",
    )
    assert score_lead_fit(_lead(), workflow=_workflow(), brief=brief).score <= 69


def test_core_product_evidence_can_reach_auto_send_grade():
    brief = models.LeadBrief(
        research_status="valid",
        company_overview="A bedding retailer.",
        specific_products="Linen bedding and duvet covers",
        personalization_hook="The retailer launched a linen duvet cover range.",
        pain_points="Seasonal sourcing",
    )
    assert score_lead_fit(_lead(), workflow=_workflow(), brief=brief).score >= 70

