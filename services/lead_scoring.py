from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Optional
import re

from sqlalchemy.orm import Session

import models


HANDOFF_INTENT_TERMS = [
    "quote", "quotation", "price", "pricing", "sample", "catalog", "moq",
    "order", "supplier", "procurement", "purchase plan", "rfq", "meeting",
    "call", "factory audit", "lead time", "customization", "booth", "visit",
]

DECISION_ROLE_TERMS = [
    "owner", "founder", "ceo", "president", "director", "head",
    "buyer", "purchasing", "procurement", "sourcing", "category manager",
    "merchandiser", "import manager", "sales manager", "business development",
]


@dataclass
class LeadScore:
    score: int
    grade: str
    handoff_recommended: bool
    notes: str


def _split_terms(value: Optional[str]) -> list[str]:
    if not value:
        return []
    parts = re.split(r"[,;\n|/]+", value)
    terms = []
    seen = set()
    stopwords = {"and", "or", "the", "for", "with", "of", "to", "in"}
    for part in parts:
        phrase = re.sub(r"\s+", " ", part).strip().lower()
        if not phrase:
            continue
        for term in [phrase, *re.split(r"\s+", phrase)]:
            term = term.strip()
            if not term or term in stopwords or len(term) < 3 or term in seen:
                continue
            terms.append(term)
            seen.add(term)
    return terms


def _text_blob(*values: object) -> str:
    return " ".join(str(v or "") for v in values).lower()


def _matched_terms(text: str, terms: Iterable[str]) -> list[str]:
    matches = []
    seen = set()
    for term in terms:
        term = term.strip().lower()
        if not term or term in seen:
            continue
        if len(term) <= 3:
            if re.search(r"\b" + re.escape(term) + r"\b", text):
                matches.append(term)
                seen.add(term)
        elif term in text:
            matches.append(term)
            seen.add(term)
    return matches


def _grade(score: int) -> str:
    if score >= 80:
        return "A"
    if score >= 65:
        return "B"
    if score >= 45:
        return "C"
    return "D"


def _brief_text(brief: Optional[models.LeadBrief]) -> str:
    if not brief:
        return ""
    return _text_blob(
        brief.company_overview,
        brief.recent_news,
        brief.pain_points,
        brief.value_proposition_alignment,
        brief.specific_products,
        brief.recent_activity,
        brief.personalization_hook,
    )


def _lead_text(lead: models.Lead, brief: Optional[models.LeadBrief]) -> str:
    return _text_blob(
        lead.domain,
        lead.company_name,
        lead.email,
        lead.first_name,
        lead.last_name,
        lead.job_title,
        lead.linkedin_url,
        lead.reply_snippet,
        lead.source_channel,
        lead.data_sources,
        _brief_text(brief),
    )


def score_lead_fit(
    lead: models.Lead,
    workflow: Optional[models.Workflow] = None,
    persona: Optional[models.CustomerPersona] = None,
    brief: Optional[models.LeadBrief] = None,
) -> LeadScore:
    score = 20
    signals: list[str] = []
    risks: list[str] = []
    text = _lead_text(lead, brief)

    if lead.email_validation_status == "valid":
        score += 15
        signals.append("verified email")
    elif lead.email:
        score += 10
        signals.append("email available")
    elif lead.status == "needs_email":
        score -= 5
        risks.append("needs email enrichment")

    if lead.email_validation_status in {"invalid", "catch-all"}:
        score -= 15 if lead.email_validation_status == "invalid" else 5
        risks.append(f"email status {lead.email_validation_status}")

    role_terms = _split_terms(getattr(workflow, "target_positions", None))
    if persona:
        role_terms.extend(_split_terms(persona.target_roles))
    role_matches = _matched_terms((lead.job_title or "").lower(), role_terms)
    if role_matches:
        score += 20
        signals.append(f"role match: {', '.join(role_matches[:3])}")
    elif _matched_terms((lead.job_title or "").lower(), DECISION_ROLE_TERMS):
        score += 12
        signals.append("decision-maker role")

    target_terms = []
    if workflow:
        target_terms.extend(_split_terms(workflow.search_keywords))
        target_terms.extend(_split_terms(workflow.target_customer_type))
        target_terms.extend(_split_terms(workflow.product_focus))
        target_terms.extend(_split_terms(workflow.target_region))
    if persona:
        target_terms.extend(_split_terms(persona.target_keywords))
        target_terms.extend(_split_terms(persona.customer_types))
        target_terms.extend(_split_terms(persona.product_categories))
        target_terms.extend(_split_terms(persona.target_countries))
        target_terms.extend(_split_terms(persona.target_industry))

    target_matches = _matched_terms(text, target_terms)
    if target_matches:
        score += min(20, 6 + len(target_matches[:5]) * 3)
        signals.append(f"profile evidence: {', '.join(target_matches[:5])}")

    negative_terms = []
    if persona:
        negative_terms.extend(_split_terms(persona.negative_keywords))
        negative_terms.extend(_split_terms(persona.disqualification_rules))
        negative_terms.extend(_split_terms(persona.negative_examples))
    negative_matches = _matched_terms(text, negative_terms)
    if negative_matches:
        score -= min(30, 12 + len(negative_matches[:4]) * 4)
        risks.append(f"negative evidence: {', '.join(negative_matches[:4])}")

    if brief:
        if brief.company_overview and "unavailable" not in brief.company_overview.lower():
            score += 8
            signals.append("website research completed")
        if brief.specific_products and "none found" not in brief.specific_products.lower():
            score += 8
            signals.append("specific products found")
        if brief.personalization_hook:
            score += 6
            signals.append("personalization hook found")
        if brief.pain_points:
            score += 4
            signals.append("pain points inferred")

    if lead.linkedin_url:
        score += 5
        signals.append("LinkedIn profile available")
    if lead.whatsapp_number:
        score += 6
        signals.append("WhatsApp available")
    if lead.timezone:
        score += 3
        signals.append("timezone resolved")

    source_text = _text_blob(lead.source_channel, lead.data_sources)
    if "customs" in source_text or "trade" in source_text or "competitor" in source_text:
        score += 10
        signals.append("transaction or trade-show source")
    elif "directory" in source_text or "association" in source_text or "retail" in source_text:
        score += 7
        signals.append("buyer directory or retail source")
    elif "website" in source_text or "snovio" in source_text or "apollo" in source_text or "social" in source_text:
        score += 4
        signals.append("third-party/contact source")

    if lead.user_rating == "positive":
        score += 10
        signals.append("user marked target")
    elif lead.user_rating == "negative":
        score -= 30
        risks.append("user marked non-target")

    score = max(0, min(100, score))
    grade = _grade(score)

    triggers = HANDOFF_INTENT_TERMS[:]
    if workflow and workflow.manual_handoff_triggers:
        triggers.extend(_split_terms(workflow.manual_handoff_triggers))
    is_contacted = lead.status not in {"found", "needs_email", "invalid_email", "drafted", "send_failed", "bounced", "low_score"}
    intent_matches = _matched_terms(lead.reply_snippet or "", HANDOFF_INTENT_TERMS)
    handoff = lead.status == "replied" or (is_contacted and score >= 80) or bool(intent_matches)
    if intent_matches:
        signals.append(f"handoff signal: {', '.join(intent_matches[:3])}")

    notes = "Signals: " + ("; ".join(signals[:8]) if signals else "limited evidence")
    if risks:
        notes += " | Risks: " + "; ".join(risks[:5])

    return LeadScore(
        score=score,
        grade=grade,
        handoff_recommended=handoff,
        notes=notes,
    )


def apply_lead_score(
    db: Session,
    lead: models.Lead,
    workflow: Optional[models.Workflow] = None,
    persona: Optional[models.CustomerPersona] = None,
) -> LeadScore:
    brief = db.query(models.LeadBrief).filter(models.LeadBrief.lead_id == lead.id).first()
    score = score_lead_fit(lead, workflow=workflow, persona=persona, brief=brief)
    lead.fit_score = score.score
    lead.fit_grade = score.grade
    lead.handoff_recommended = score.handoff_recommended
    lead.qualification_notes = score.notes
    return score


def build_outreach_context(
    workflow: Optional[models.Workflow],
    persona: Optional[models.CustomerPersona],
    score: Optional[LeadScore] = None,
) -> str:
    parts = []
    if workflow:
        if workflow.playbook_type:
            parts.append(f"Scenario/playbook: {workflow.playbook_type}")
        if workflow.target_customer_type:
            parts.append(f"Target customer type: {workflow.target_customer_type}")
        if workflow.target_region:
            parts.append(f"Target market/region: {workflow.target_region}")
        if workflow.product_focus:
            parts.append(f"Product focus: {workflow.product_focus}")
        if workflow.pilot_goal:
            parts.append(f"Pilot goal: {workflow.pilot_goal}")
    if persona:
        if persona.customer_types:
            parts.append(f"Buyer types to prioritize: {persona.customer_types}")
        if persona.product_categories:
            parts.append(f"Relevant product categories: {persona.product_categories}")
        if getattr(persona, "company_size", None):
            parts.append(f"Target company size: {persona.company_size}")
        if persona.qualification_rules:
            parts.append(f"Qualification rules: {persona.qualification_rules}")
        if persona.disqualification_rules:
            parts.append(f"Avoid if: {persona.disqualification_rules}")
        if persona.cultural_notes:
            parts.append(f"Localization/culture notes: {persona.cultural_notes}")
    if score:
        parts.append(f"Lead fit grade: {score.grade} ({score.score}/100). {score.notes}")
    return "\n".join(parts)
