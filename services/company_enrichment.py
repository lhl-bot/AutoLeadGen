"""Resolve and pre-qualify a LeadContact employee's real company domain."""
from dataclasses import dataclass
import re
from typing import Any, Optional
from urllib.parse import urlparse

from services.research_quality import is_usable_company_domain, normalize_domain
from services.search_engine import search_company_results


GENERIC_TERMS = {
    "the", "and", "for", "with", "company", "group", "ltd", "limited", "inc",
    "llc", "gmbh", "plc", "co", "corp", "corporation", "international",
    "wholesale", "supplier", "manufacturer", "factory", "products", "product",
}


@dataclass(frozen=True)
class CompanyResolution:
    domain: str
    source: str
    relevance_verified: bool
    evidence: str


def _domain_from_value(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    if "@" in text and "://" not in text:
        text = text.rsplit("@", 1)[-1]
    parsed = urlparse(text if "://" in text else f"https://{text}")
    return normalize_domain(parsed.netloc or parsed.path)


def _tokens(value: Optional[str]) -> list[str]:
    tokens = []
    for token in re.findall(r"[a-z0-9]+", (value or "").lower()):
        if len(token) >= 3 and token not in GENERIC_TERMS and token not in tokens:
            tokens.append(token)
    return tokens


def _provider_company_domain(employee: dict[str, Any]) -> str:
    company = employee.get("company") if isinstance(employee.get("company"), dict) else {}
    for value in (
        employee.get("companyWebsite"),
        employee.get("companyDomain"),
        employee.get("website"),
        company.get("website"),
        company.get("domain"),
    ):
        domain = _domain_from_value(value)
        if is_usable_company_domain(domain):
            return domain
    return ""


def resolve_employee_company(
    employee: dict[str, Any],
    *,
    workflow_keywords: str,
    product_focus: str = "",
    target_region: str = "",
) -> CompanyResolution:
    company_name = str(employee.get("companyName") or "").strip()
    email_domain = _domain_from_value(employee.get("email"))
    provider_domain = _provider_company_domain(employee)
    target_tokens = _tokens(f"{workflow_keywords} {product_focus}")
    payload_text = " ".join(str(value or "") for value in employee.values()).lower()

    if provider_domain:
        target_match = not target_tokens or any(token in payload_text for token in target_tokens)
        return CompanyResolution(
            domain=provider_domain,
            source="leadcontact_company_website",
            relevance_verified=target_match,
            evidence=payload_text[:500],
        )

    if is_usable_company_domain(email_domain):
        target_match = not target_tokens or any(token in payload_text for token in target_tokens)
        return CompanyResolution(
            domain=email_domain,
            source="business_email_domain",
            relevance_verified=target_match,
            evidence=payload_text[:500],
        )

    if not company_name:
        return CompanyResolution("", "missing_company", False, "")

    query = " ".join(filter(None, (company_name, workflow_keywords, product_focus, target_region, "official website")))
    company_tokens = _tokens(company_name)
    best: tuple[int, Optional[dict[str, str]]] = (0, None)
    for result in search_company_results(query, count=5):
        domain = normalize_domain(result.get("domain"))
        if not is_usable_company_domain(domain):
            continue
        evidence = " ".join((result.get("title", ""), result.get("snippet", ""), domain)).lower()
        company_matches = sum(1 for token in company_tokens if token in evidence)
        target_matches = sum(1 for token in target_tokens if token in evidence)
        score = min(company_matches, 2) * 2 + min(target_matches, 2) * 3
        if score > best[0]:
            best = (score, result)

    result = best[1]
    if not result:
        return CompanyResolution("", "company_search_no_match", False, "")
    domain = normalize_domain(result.get("domain"))
    evidence = " ".join((result.get("title", ""), result.get("snippet", ""), domain)).strip()
    relevance_verified = best[0] >= 5 and (
        not target_tokens or any(token in evidence.lower() for token in target_tokens)
    )
    return CompanyResolution(
        domain=domain,
        source=result.get("source") or "company_search",
        relevance_verified=relevance_verified,
        evidence=evidence[:1000],
    )
