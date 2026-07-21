"""Extract the newest human reply before intent or unsubscribe classification."""
from __future__ import annotations

import re
from dataclasses import dataclass

from bs4 import BeautifulSoup

from product_v2.enums import RestrictionScope


QUOTE_MARKERS = (
    re.compile(r"^\s*>"),
    re.compile(r"^\s*On .+wrote:\s*$", re.IGNORECASE),
    re.compile(r"^\s*-{2,}\s*Original Message\s*-{2,}\s*$", re.IGNORECASE),
    re.compile(r"^\s*(From|Sent|To|Subject):\s+", re.IGNORECASE),
    re.compile(r"^\s*在.+写道[：:]?\s*$"),
    re.compile(r"^\s*-{2,}\s*原始邮件\s*-{2,}\s*$"),
)
SIGNATURE_MARKERS = (
    re.compile(r"^\s*--\s*$"),
    re.compile(r"^\s*(best|kind|warm) regards[,]?\s*$", re.IGNORECASE),
    re.compile(r"^\s*(thanks|thank you)[,!]??\s*$", re.IGNORECASE),
    re.compile(r"^\s*(此致|顺祝商祺|谢谢)[！!,，。]?\s*$"),
)


def _html_to_text(value: str) -> str:
    if "<" not in value or ">" not in value:
        return value
    soup = BeautifulSoup(value, "html.parser")
    for selector in ("blockquote", ".gmail_quote", ".yahoo_quoted", "[data-skiff-mail]"):
        for node in soup.select(selector):
            node.decompose()
    return soup.get_text("\n")


def extract_latest_reply(text: str) -> str:
    value = _html_to_text(text or "").replace("\r\n", "\n").replace("\r", "\n")
    lines = value.split("\n")
    latest: list[str] = []
    for line in lines:
        if any(pattern.search(line) for pattern in QUOTE_MARKERS):
            break
        if latest and any(pattern.search(line) for pattern in SIGNATURE_MARKERS):
            break
        latest.append(line.rstrip())
    compact: list[str] = []
    for line in latest:
        if line.strip() or (compact and compact[-1]):
            compact.append(line.strip())
    return "\n".join(compact).strip()


@dataclass(frozen=True)
class UnsubscribeIntent:
    is_unsubscribe: bool
    scope: RestrictionScope | None = None
    matched_phrase: str | None = None
    requires_company_confirmation: bool = False

    def __bool__(self) -> bool:
        return self.is_unsubscribe


CONTACT_PHRASES = (
    "do not contact me",
    "don't contact me",
    "stop contacting me",
    "never contact me again",
    "不要再联系我",
    "不要联系我",
)
COMPANY_PHRASES = (
    "do not contact anyone at",
    "do not contact our company",
    "stop contacting our company",
    "不要联系我司",
    "不要联系我们公司",
)
POINT_PHRASES = (
    "unsubscribe",
    "opt out",
    "opt-out",
    "remove this email",
    "remove me from your list",
    "stop emailing",
    "退订",
    "取消订阅",
)


def detect_unsubscribe_intent(latest_text: str, subject: str = "") -> UnsubscribeIntent:
    haystack = f"{subject}\n{latest_text}".lower()
    for phrase in COMPANY_PHRASES:
        if phrase in haystack:
            return UnsubscribeIntent(True, RestrictionScope.COMPANY, phrase, True)
    for phrase in CONTACT_PHRASES:
        if phrase in haystack:
            return UnsubscribeIntent(True, RestrictionScope.CONTACT, phrase)
    for phrase in POINT_PHRASES:
        if phrase in haystack:
            return UnsubscribeIntent(True, RestrictionScope.CONTACT_POINT, phrase)
    return UnsubscribeIntent(False)
