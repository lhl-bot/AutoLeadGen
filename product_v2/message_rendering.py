"""Strict rendering for immutable Product V2 outreach templates."""
from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Optional
from urllib.parse import urlsplit

from product_v2.enums import Channel
from services.suppression import generate_v2_unsubscribe_token


_PLACEHOLDER = re.compile(r"{{\s*([a-z_][a-z0-9_]*)\s*}}")
_ALLOWED = frozenset(
    {
        "company_name",
        "company_domain",
        "contact_name",
        "first_name",
        "job_title",
        "unsubscribe_url",
    }
)


class MessageRenderError(ValueError):
    pass


@dataclass(frozen=True)
class RenderedMessage:
    subject: Optional[str]
    body: str
    unsubscribe_url: Optional[str]


def _render(template: str, values: dict[str, str], *, field: str) -> str:
    referenced = set(_PLACEHOLDER.findall(template))
    unknown = sorted(referenced - _ALLOWED)
    if unknown:
        raise MessageRenderError(f"{field}_unknown_placeholder:{unknown[0]}")
    rendered = _PLACEHOLDER.sub(lambda match: values.get(match.group(1), ""), template)
    if "{{" in rendered or "}}" in rendered:
        raise MessageRenderError(f"{field}_malformed_placeholder")
    return rendered.strip()


def render_sequence_message(
    *,
    channel: Channel,
    subject_template: Optional[str],
    body_template: Optional[str],
    company_name: str,
    company_domain: str,
    contact_name: str,
    job_title: str,
    owner_id: int,
    contact_point_id: int,
    contact_point_identity_hash: str,
    public_unsubscribe_base_url: str,
) -> RenderedMessage:
    body_source = (body_template or "").strip()
    if not body_source:
        raise MessageRenderError("body_template_missing")

    unsubscribe_url = None
    if channel == Channel.EMAIL:
        base = (public_unsubscribe_base_url or "").strip().rstrip("/")
        parsed = urlsplit(base)
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
        ):
            raise MessageRenderError("public_unsubscribe_https_url_missing")
        token = generate_v2_unsubscribe_token(
            owner_id=owner_id,
            contact_point_id=contact_point_id,
            identity_hash=contact_point_identity_hash,
        )
        unsubscribe_url = f"{base}/{token}"

    first_name = (contact_name or "").strip().split(" ", 1)[0]
    values = {
        "company_name": (company_name or "").strip(),
        "company_domain": (company_domain or "").strip(),
        "contact_name": (contact_name or "").strip(),
        "first_name": first_name,
        "job_title": (job_title or "").strip(),
        "unsubscribe_url": unsubscribe_url or "",
    }
    body = _render(body_source, values, field="body")
    if not body:
        raise MessageRenderError("body_template_rendered_empty")
    if channel == Channel.EMAIL and "unsubscribe_url" not in set(_PLACEHOLDER.findall(body_source)):
        body = f"{body}\n\nUnsubscribe: {unsubscribe_url}"
    if len(body) > 50_000:
        raise MessageRenderError("body_rendered_too_large")

    subject = None
    if channel == Channel.EMAIL:
        subject_source = (subject_template or "").strip()
        if not subject_source:
            raise MessageRenderError("email_subject_template_missing")
        subject = _render(subject_source, values, field="subject")
        if not subject or "\r" in subject or "\n" in subject:
            raise MessageRenderError("email_subject_invalid")
        if len(subject) > 998:
            raise MessageRenderError("email_subject_too_large")

    return RenderedMessage(
        subject=subject,
        body=body,
        unsubscribe_url=unsubscribe_url,
    )
