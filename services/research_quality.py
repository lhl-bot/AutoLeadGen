"""Deterministic research and outbound-content quality checks.

The LLM may summarize evidence, but it must never decide whether an email is
safe to send.  These helpers keep placeholder text, personal-email portals and
off-target company research out of drafts and the send path.
"""
from dataclasses import dataclass
from datetime import datetime, timezone
import re
from typing import Any, Iterable, Optional


RESEARCH_STATUSES = {"pending", "valid", "insufficient", "invalid_source"}

PLACEHOLDER_MARKERS = {
    "none found",
    "no information available",
    "information unavailable",
    "unknown",
    "n/a",
    "not available",
    "could not access",
    "no detailed brief available",
}

PERSONAL_EMAIL_DOMAINS = {
    "gmail.com", "googlemail.com", "yahoo.com", "yahoo.co.uk", "outlook.com",
    "hotmail.com", "live.com", "msn.com", "icloud.com", "me.com", "mac.com",
    "aol.com", "proton.me", "protonmail.com", "gmx.com", "mail.com", "qq.com",
    "163.com", "126.com", "sina.com",
}

PORTAL_DOMAINS = PERSONAL_EMAIL_DOMAINS | {
    "login.microsoftonline.com", "accounts.google.com", "linkedin.com",
    "facebook.com", "instagram.com", "x.com", "twitter.com",
}

LOGIN_MARKERS = {
    "sign in", "log in", "forgot password", "create account", "enter your password",
    "continue with google", "continue with microsoft",
}

HOME_TEXTILE_TERMS = {
    "bedding", "bed linen", "bed sheet", "bedsheet", "duvet", "quilt", "comforter",
    "pillow", "pillowcase", "blanket", "mattress protector", "towel", "bath linen",
    "curtain", "home textile", "home textiles",
}

ADJACENT_TEXTILE_TERMS = {
    "textile", "fabric", "yarn", "woven", "nonwoven", "apparel", "garment",
    "clothing", "fashion", "upholstery",
}

TARGET_STOPWORDS = {
    "the", "and", "for", "with", "from", "wholesale", "supplier", "suppliers",
    "manufacturer", "manufacturers", "factory", "factories", "company", "companies",
    "oem", "odm", "custom", "product", "products", "buyer", "buyers", "distributor",
    "distributors", "retailer", "retailers", "importer", "importers", "brand", "brands",
}


@dataclass(frozen=True)
class ResearchAssessment:
    status: str
    flags: list[str]
    evidence_sources: list[dict[str, str]]
    evidence_level: str


def normalize_domain(domain: Optional[str]) -> str:
    value = (domain or "").strip().lower()
    value = re.sub(r"^https?://", "", value).split("/", 1)[0]
    return value.removeprefix("www.").strip(".")


def is_personal_email_domain(domain: Optional[str]) -> bool:
    return normalize_domain(domain) in PERSONAL_EMAIL_DOMAINS


def is_usable_company_domain(domain: Optional[str]) -> bool:
    value = normalize_domain(domain)
    if not value or "." not in value or value in PORTAL_DOMAINS:
        return False
    return not any(value == blocked or value.endswith(f".{blocked}") for blocked in PORTAL_DOMAINS)


def _normalized_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (list, tuple, set)):
        return ", ".join(str(item) for item in value if item is not None).strip()
    return str(value).strip()


def is_placeholder_text(value: Any) -> bool:
    text = _normalized_text(value).lower().strip(" .:-")
    if not text:
        return True
    return any(text == marker or text.startswith(f"{marker}.") for marker in PLACEHOLDER_MARKERS)


def sanitize_research_value(value: Any) -> Optional[str]:
    text = _normalized_text(value)
    return None if is_placeholder_text(text) else text


def sanitize_brief_data(brief_data: dict[str, Any]) -> tuple[dict[str, Optional[str]], list[str]]:
    field_map = {
        "company_overview": "company_overview",
        "pain_points": "pain_points",
        "recent_news": "recent_news",
        "value_proposition_alignment": "value_proposition_alignment",
        "specific_products": "specific_products",
        "recent_activity": "recent_activity",
        "personalization_hook": "personalization_hook",
    }
    sanitized: dict[str, Optional[str]] = {}
    flags: list[str] = []
    for target, source in field_map.items():
        raw = brief_data.get(source)
        if target == "recent_news" and raw is None:
            raw = brief_data.get("recent_activity")
        value = sanitize_research_value(raw)
        if raw not in (None, "") and value is None:
            flags.append(f"placeholder:{target}")
        sanitized[target] = value
    return sanitized, flags


def _split_target_terms(values: Iterable[Optional[str]]) -> list[str]:
    terms: list[str] = []
    for value in values:
        for term in re.split(r"[,;|/\n]+", value or ""):
            normalized = re.sub(r"\s+", " ", term).strip().lower()
            if not normalized:
                continue
            words = [word for word in re.findall(r"[a-z0-9]+", normalized) if word not in TARGET_STOPWORDS]
            candidate = " ".join(words)
            if len(candidate) >= 3 and candidate not in terms:
                terms.append(candidate)
    return terms


def target_terms_for(workflow=None, persona=None) -> list[str]:
    values = []
    if workflow is not None:
        values.extend([
            getattr(workflow, "search_keywords", None),
            getattr(workflow, "product_focus", None),
            getattr(workflow, "target_customer_type", None),
        ])
    if persona is not None:
        values.extend([
            getattr(persona, "target_keywords", None),
            getattr(persona, "product_categories", None),
            getattr(persona, "target_industry", None),
        ])
    return _split_target_terms(values)


def _contains_term(text: str, term: str) -> bool:
    if len(term) <= 3:
        return re.search(r"\b" + re.escape(term) + r"\b", text) is not None
    return term in text


def classify_product_evidence(brief_data: dict[str, Any], target_terms: Iterable[str]) -> str:
    specific_products = sanitize_research_value(brief_data.get("specific_products"))
    if not specific_products:
        return "none"
    text = " ".join(
        filter(None, (
            specific_products,
            sanitize_research_value(brief_data.get("company_overview")),
            sanitize_research_value(brief_data.get("personalization_hook")),
        ))
    ).lower()
    targets = [term.lower() for term in target_terms if term]
    if targets and any(_contains_term(text, term) for term in targets):
        return "core"
    if any(_contains_term(text, term) for term in HOME_TEXTILE_TERMS):
        return "core"
    if any(_contains_term(text, term) for term in ADJACENT_TEXTILE_TERMS):
        return "adjacent"
    return "none" if targets else "core"


def looks_like_login_or_portal(domain: Optional[str], scraped_text: Optional[str]) -> bool:
    if not is_usable_company_domain(domain):
        return True
    text = (scraped_text or "").lower()
    if not text:
        return False
    if any(marker in text for marker in ("could not access website", "failed to fetch", "access denied")):
        return True
    marker_count = sum(1 for marker in LOGIN_MARKERS if marker in text)
    return marker_count >= 2


def assess_research(
    *,
    domain: Optional[str],
    brief_data: dict[str, Any],
    target_terms: Iterable[str] = (),
    scraped_text: Optional[str] = None,
    source_labels: Optional[Iterable[str]] = None,
) -> ResearchAssessment:
    flags: list[str] = []
    normalized_domain = normalize_domain(domain)
    if looks_like_login_or_portal(normalized_domain, scraped_text):
        flags.append("invalid_company_source")

    _, placeholder_flags = sanitize_brief_data(brief_data)
    flags.extend(placeholder_flags)

    evidence_level = classify_product_evidence(brief_data, target_terms)
    if evidence_level == "none":
        flags.append("missing_target_product_evidence")
    elif evidence_level == "adjacent":
        flags.append("adjacent_product_evidence_only")

    hook = sanitize_research_value(brief_data.get("personalization_hook"))
    if not hook:
        flags.append("missing_personalization_hook")

    if "invalid_company_source" in flags:
        status = "invalid_source"
    elif evidence_level == "core" and hook:
        status = "valid"
    else:
        status = "insufficient"

    evidence_sources = []
    if normalized_domain and is_usable_company_domain(normalized_domain):
        evidence_sources.append({"type": "official_website", "value": normalized_domain})
    for source in source_labels or ():
        cleaned = str(source).strip()
        if cleaned and all(item["value"] != cleaned for item in evidence_sources):
            evidence_sources.append({"type": "discovery", "value": cleaned})

    if status == "valid" and len(evidence_sources) < 2:
        status = "insufficient"
        flags.append("missing_second_source")

    return ResearchAssessment(
        status=status,
        flags=list(dict.fromkeys(flags)),
        evidence_sources=evidence_sources,
        evidence_level=evidence_level,
    )


def outbound_content_quality_reason(content: Optional[str]) -> Optional[str]:
    text = (content or "").strip()
    if not text:
        return "empty_outbound_content"
    lowered = text.lower()
    for marker in PLACEHOLDER_MARKERS:
        if marker in lowered:
            return f"research_placeholder_in_content({marker})"
    if any(token in lowered for token in ("{{", "}}", "[first name]", "[company name]", "<first_name>")):
        return "unresolved_template_placeholder"
    if sum(1 for marker in LOGIN_MARKERS if marker in lowered) >= 2:
        return "login_page_content"
    return None


def utcnow() -> datetime:
    return datetime.now(timezone.utc)
