"""Rendering and A/B-variant selection for reusable email templates.

Templates use ``{{variable}}`` placeholders. Rendering is intentionally simple and
safe: unknown placeholders are left blank, and a small set of lead-derived
variables is supported.
"""
import random
import re
from typing import Any, Dict, List, Optional

# Variables exposed to templates, mapped from a lead-like object.
TEMPLATE_VARIABLES = [
    "first_name",
    "last_name",
    "full_name",
    "company_name",
    "job_title",
    "domain",
]

_TOKEN_RE = re.compile(r"\{\{\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*\}\}")


def lead_variables(lead: Any) -> Dict[str, str]:
    """Build the substitution map from a lead (ORM object or dict)."""
    def get(field: str) -> str:
        if isinstance(lead, dict):
            value = lead.get(field)
        else:
            value = getattr(lead, field, None)
        return ("" if value is None else str(value)).strip()

    first = get("first_name")
    last = get("last_name")
    full = (first + " " + last).strip()
    return {
        "first_name": first,
        "last_name": last,
        "full_name": full,
        "company_name": get("company_name"),
        "job_title": get("job_title"),
        "domain": get("domain"),
    }


def render(text: Optional[str], variables: Dict[str, str]) -> str:
    """Replace ``{{var}}`` tokens; unknown tokens render as empty strings."""
    if not text:
        return ""

    def repl(match: "re.Match") -> str:
        key = match.group(1)
        return variables.get(key, "")

    rendered = _TOKEN_RE.sub(repl, text)
    # Collapse whitespace left by blank substitutions, but preserve newlines.
    rendered = re.sub(r"[ \t]{2,}", " ", rendered)
    return rendered


def render_template(template: Any, lead: Any) -> Dict[str, str]:
    """Render a template's subject and body against a lead."""
    variables = lead_variables(lead)
    subject = getattr(template, "subject", None) if not isinstance(template, dict) else template.get("subject")
    body = getattr(template, "body", None) if not isinstance(template, dict) else template.get("body")
    return {
        "subject": render(subject, variables),
        "body": render(body, variables),
    }


def find_missing_variables(text: Optional[str]) -> List[str]:
    """Return placeholder names in ``text`` that are not supported variables."""
    if not text:
        return []
    found = {m.group(1) for m in _TOKEN_RE.finditer(text)}
    return sorted(v for v in found if v not in TEMPLATE_VARIABLES)


def pick_variant(templates: List[Any], *, rng: Optional[random.Random] = None) -> Optional[Any]:
    """Weighted-random pick among active templates (the A/B split).

    Inactive templates and non-positive weights are ignored. Returns None if there
    is nothing to pick.
    """
    active = [t for t in templates if getattr(t, "is_active", True)]
    weighted = [(t, max(0, int(getattr(t, "weight", 1) or 0))) for t in active]
    weighted = [(t, w) for t, w in weighted if w > 0]
    if not weighted:
        return active[0] if active else None
    total = sum(w for _, w in weighted)
    r = (rng or random).uniform(0, total)
    upto = 0.0
    for t, w in weighted:
        upto += w
        if r <= upto:
            return t
    return weighted[-1][0]
