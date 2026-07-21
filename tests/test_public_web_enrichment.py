from services.public_web_enrichment import (
    CompanyEvidence,
    PageEvidence,
    build_brief_data,
    company_tokens,
    domain_company_affinity,
    parse_indexed_company_pages,
    parse_search_html,
)


def test_company_tokens_remove_legal_and_location_noise():
    assert company_tokens("Acme Retail Group Australia Pty Ltd") == ["acme"]


def test_domain_company_affinity_rejects_reseller_and_accepts_name_or_acronym():
    assert domain_company_affinity("1888mills.com", "1888 Mills") is True
    assert domain_company_affinity("ams.com", "Advanced Medical Solutions") is True
    assert domain_company_affinity("airbus.com", "Airbus Defence and Space") is True
    assert domain_company_affinity("hotels4humanity.com", "1888 Mills") is False
    assert domain_company_affinity("disneyplus.com", "Power Plus Communications") is False
    assert domain_company_affinity("link.com", "Link IQ") is False
    assert domain_company_affinity("freelancehunt.com", "Freelance") is False
    assert domain_company_affinity("swift.com", "Swift & Company, Inc") is False
    assert domain_company_affinity("dhl.com", "DHL") is False


def test_parse_search_html_keeps_public_company_results_only():
    html = """
    <a href="https://www.startpage.com/privacy">Privacy</a>
    <a href="https://www.linkedin.com/company/acme">LinkedIn</a>
    <a href="https://www.acme-home.co.uk/about">Acme Home Official Site</a>
    """

    results = parse_search_html(html, engine="startpage")

    assert [item.domain for item in results] == ["acme-home.co.uk"]


def test_parse_bing_search_html_uses_organic_results_only():
    html = """
    <header><a href="https://www.microsoft.com">Microsoft</a></header>
    <ol><li class="b_algo"><h2>
      <a href="https://www.acme-home.co.uk/about">Acme Home Official Site</a>
    </h2><p>Acme Home company profile.</p></li></ol>
    """

    results = parse_search_html(html, engine="bing")

    assert [item.domain for item in results] == ["acme-home.co.uk"]


def test_build_brief_data_labels_hypotheses_and_preserves_evidence():
    evidence = CompanyEvidence(
        domain="acme.co.uk",
        collection_status="collected",
        homepage_url="https://acme.co.uk/",
        pages=[
            PageEvidence(url="https://acme.co.uk/", title="Acme Home"),
            PageEvidence(url="https://acme.co.uk/bedding", title="Bedding"),
        ],
        product_labels=["Organic Cotton Sheets", "Duvet Covers"],
        public_emails=["sales@acme.co.uk"],
    )

    brief = build_brief_data(
        evidence,
        company_name="Acme Home",
        contact_name="Ada Buyer",
        job_title="Category Buyer",
        product_focus="bedding supply",
    )

    assert brief["research_status"] == "valid"
    assert "hypotheses" in brief["pain_points"].lower()
    assert "Organic Cotton Sheets" in brief["specific_products"]
    assert {item["type"] for item in brief["evidence_sources"]} >= {
        "official_website",
        "official_subpage",
        "public_company_email",
    }


def test_indexed_official_pages_can_ground_a_detailed_brief():
    html = """
    <div class="result">
      <a href="https://acme.co.uk/">Acme Home | Bedding</a>
      <p>Acme Home sells organic cotton sheets, duvet covers and pillows.</p>
    </div>
    <div class="result">
      <a href="https://acme.co.uk/collections/bedding">Bedding Collection</a>
      <p>Browse sheets, quilts and mattress protectors.</p>
    </div>
    """

    pages = parse_indexed_company_pages(
        html,
        domain="acme.co.uk",
        target_terms=["bedding", "sheets", "duvet"],
    )
    evidence = CompanyEvidence(
        domain="acme.co.uk",
        collection_status="search_index",
        homepage_url=pages[0].url,
        pages=pages,
        product_labels=[label for page in pages for label in page.product_labels],
    )
    brief = build_brief_data(
        evidence,
        company_name="Acme Home",
        contact_name="Ada Buyer",
        job_title="Buyer",
        product_focus="bedding",
    )

    assert len(pages) == 2
    assert brief["research_status"] == "valid"
    assert brief["evidence_sources"][0]["type"] == "official_indexed_page"


def test_build_brief_data_is_explicit_when_company_is_unresolved():
    brief = build_brief_data(
        CompanyEvidence(
            domain="",
            collection_status="unresolved",
            error_code="no_verified_official_domain",
        ),
        company_name="Acme",
        contact_name="Ada Buyer",
        job_title="Buyer",
        product_focus="bedding",
    )

    assert brief["research_status"] == "insufficient"
    assert brief["specific_products"] is None
    assert brief["personalization_hook"] is None
    assert "not yet been independently verified" in brief["company_overview"]
