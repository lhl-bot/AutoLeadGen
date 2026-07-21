"""Evidence-first public-web enrichment without paid contact providers.

This module deliberately separates collection from database writes.  It never
guesses a person's email address and never disables TLS verification.  Search
results are accepted as an official company domain only after the candidate
site itself matches the stored company identity.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from concurrent.futures import ThreadPoolExecutor, as_completed
import base64
import html
import math
import os
import re
import threading
import time
from typing import Iterable
from urllib.parse import parse_qs, urljoin, urlparse
from urllib.robotparser import RobotFileParser

from bs4 import BeautifulSoup
import requests

from services.http_client import build_resilient_session
from services.research_quality import (
    HOME_TEXTILE_TERMS,
    is_usable_company_domain,
    normalize_domain,
)
from services.search_engine import _is_junk_domain


USER_AGENT = "AutoLeadGenResearchBot/1.0 (+public company research)"
BROWSER_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)
MAX_RESPONSE_BYTES = 2_000_000
MAX_SOURCE_PAGES = 4
COMPANY_STOPWORDS = {
    "and", "the", "group", "company", "co", "inc", "llc", "ltd", "limited",
    "plc", "gmbh", "ag", "sa", "sas", "bv", "pty", "retail", "international",
    "global", "australia", "australian", "new", "zealand", "uk", "usa", "us",
}
GENERIC_LINK_TEXT = {
    "home", "shop", "shop now", "learn more", "read more", "view all", "discover",
    "products", "collections", "about", "about us", "contact", "menu", "next",
}
PRODUCT_PATH_MARKERS = (
    "product", "collection", "catalog", "category", "range", "shop", "bedding",
    "bedroom", "bath", "linen", "textile", "duvet", "quilt", "sheet", "pillow",
    "blanket", "towel", "curtain",
)
NEWS_PATH_MARKERS = ("news", "blog", "press", "journal", "story", "stories", "media")
ABOUT_PATH_MARKERS = ("about", "company", "our-story", "who-we-are")
JUNK_PUBLIC_EMAIL_DOMAINS = {"example.com", "sentry.io", "wixpress.com"}


def _clean_text(value: str | None, *, limit: int = 500) -> str:
    text = html.unescape(re.sub(r"\s+", " ", value or "")).strip(" \t\r\n-|•")
    return text[:limit]


def company_tokens(value: str | None) -> list[str]:
    tokens: list[str] = []
    for token in re.findall(r"[a-z0-9]+", (value or "").lower()):
        if len(token) < 3 or token in COMPANY_STOPWORDS or token in tokens:
            continue
        tokens.append(token)
    return tokens


def _host_matches(host: str, domain: str) -> bool:
    host = normalize_domain(host.split(":", 1)[0])
    domain = normalize_domain(domain)
    return host == domain or host.endswith(f".{domain}") or domain.endswith(f".{host}")


def _quality_link_text(value: str | None) -> str:
    text = _clean_text(value, limit=120)
    if (
        not text
        or text.lower() in GENERIC_LINK_TEXT
        or len(text) < 3
        or len(text) > 100
        or "cookie" in text.lower()
    ):
        return ""
    return text


def _unwrap_bing_url(value: str) -> str:
    parsed = urlparse(value)
    if not parsed.netloc.endswith("bing.com") or parsed.path != "/ck/a":
        return value
    encoded = (parse_qs(parsed.query).get("u") or [""])[0]
    if not encoded.startswith("a1"):
        return value
    raw = encoded[2:]
    try:
        raw += "=" * (-len(raw) % 4)
        decoded = base64.urlsafe_b64decode(raw.encode("ascii")).decode("utf-8")
        return decoded if decoded.startswith(("http://", "https://")) else value
    except (ValueError, UnicodeDecodeError):
        return value


def _unique(values: Iterable[str], *, limit: int) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        cleaned = _clean_text(value, limit=160)
        key = cleaned.lower()
        if cleaned and key not in seen:
            result.append(cleaned)
            seen.add(key)
        if len(result) >= limit:
            break
    return result


@dataclass
class PageEvidence:
    url: str
    title: str = ""
    description: str = ""
    headings: list[str] = field(default_factory=list)
    product_labels: list[str] = field(default_factory=list)
    news_labels: list[str] = field(default_factory=list)
    useful_links: list[str] = field(default_factory=list)
    public_emails: list[str] = field(default_factory=list)
    public_phones: list[str] = field(default_factory=list)
    text_sample: str = ""


@dataclass
class CompanyEvidence:
    domain: str
    collection_status: str
    expected_company_name: str = ""
    homepage_url: str = ""
    pages: list[PageEvidence] = field(default_factory=list)
    company_match_ratio: float = 0.0
    product_labels: list[str] = field(default_factory=list)
    news_labels: list[str] = field(default_factory=list)
    public_emails: list[str] = field(default_factory=list)
    public_phones: list[str] = field(default_factory=list)
    search_sources: list[str] = field(default_factory=list)
    error_code: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: dict) -> "CompanyEvidence":
        values = {
            key: value
            for key, value in payload.items()
            if key in cls.__dataclass_fields__
        }
        values["pages"] = [PageEvidence(**item) for item in payload.get("pages", [])]
        return cls(**values)


@dataclass(frozen=True)
class SearchCandidate:
    domain: str
    title: str
    source_url: str
    engine: str
    rank: int


class _EngineRateLimiter:
    def __init__(self, interval: float = 0.8):
        self.interval = interval
        self._lock = threading.Lock()
        self._last: dict[str, float] = {}

    def wait(self, engine: str) -> None:
        with self._lock:
            now = time.monotonic()
            delay = self.interval - (now - self._last.get(engine, 0.0))
            if delay > 0:
                time.sleep(delay)
            self._last[engine] = time.monotonic()


_SEARCH_LIMITER = _EngineRateLimiter()
_BAIDU_LIMITER = _EngineRateLimiter(interval=2.0)
_TAVILY_LIMITER = _EngineRateLimiter(interval=1.0)
_baidu_disabled_until = 0.0
_PUBLIC_API_SESSION = build_resilient_session(
    total=3,
    connect=3,
    read=2,
    backoff_factor=1.0,
    pool_connections=4,
    pool_maxsize=8,
)


class PublicSearchProviderLimited(RuntimeError):
    pass


def _session(*, retries: bool = True) -> requests.Session:
    session = build_resilient_session(
        total=2 if retries else 0,
        connect=2 if retries else 0,
        read=1 if retries else 0,
        backoff_factor=0.5,
    )
    session.headers.update({
        "User-Agent": BROWSER_USER_AGENT,
        "Accept-Language": "en-US,en;q=0.8",
        "Accept": "text/html,application/xhtml+xml",
    })
    return session


def parse_search_html(
    html_text: str,
    *,
    engine: str,
    company_name: str = "",
) -> list[SearchCandidate]:
    soup = BeautifulSoup(html_text, "html.parser")
    candidates: list[SearchCandidate] = []
    seen: set[str] = set()
    engine_domains = {
        "startpage": ("startpage.com",),
        "brave": ("search.brave.com", "brave.com"),
        "bing": ("bing.com", "microsoft.com"),
    }.get(engine, ())
    anchors = (
        soup.select("li.b_algo h2 a[href]")
        if engine == "bing"
        else soup.find_all("a", href=True)
    )
    for anchor in anchors:
        href = str(anchor.get("href") or "").strip()
        if engine == "bing":
            href = _unwrap_bing_url(href)
        if not href.startswith(("http://", "https://")):
            continue
        domain = normalize_domain(urlparse(href).netloc)
        if not domain or domain in seen or any(domain.endswith(blocked) for blocked in engine_domains):
            continue
        if not is_usable_company_domain(domain) or (
            _is_junk_domain(domain)
            and not domain_company_affinity(domain, company_name)
        ):
            continue
        title = _clean_text(anchor.get_text(" ", strip=True), limit=160)
        if not title:
            continue
        seen.add(domain)
        candidates.append(SearchCandidate(
            domain=domain,
            title=title,
            source_url=href,
            engine=engine,
            rank=len(candidates) + 1,
        ))
        if len(candidates) >= 12:
            break
    return candidates


def _search_html(query: str, *, engine: str) -> str:
    endpoints = {
        "startpage": ("https://www.startpage.com/sp/search", {"query": query}),
        "brave": ("https://search.brave.com/search", {"q": query, "source": "web"}),
        "bing": ("https://cn.bing.com/search", {"q": query, "ensearch": "1"}),
    }
    if engine not in endpoints:
        raise ValueError(f"unsupported search engine: {engine}")
    _SEARCH_LIMITER.wait(engine)
    url, params = endpoints[engine]
    try:
        response = _session().get(url, params=params, timeout=15)
        if response.status_code != 200 or len(response.content) > MAX_RESPONSE_BYTES:
            return ""
        return response.text
    except requests.RequestException:
        return ""


def search_public_web(
    query: str,
    *,
    engine: str,
    company_name: str = "",
) -> list[SearchCandidate]:
    html_text = _search_html(query, engine=engine)
    if not html_text:
        return []
    return parse_search_html(html_text, engine=engine, company_name=company_name)


def search_baidu_ai(query: str, *, count: int = 10) -> list[dict]:
    """Use the configured Baidu AI Search API and return public web references."""
    global _baidu_disabled_until
    api_key = os.environ.get("BAIDU_API_KEY", "").strip()
    if not api_key:
        return []
    if time.monotonic() < _baidu_disabled_until:
        raise PublicSearchProviderLimited("baidu_search_cooldown")
    payload = {
        "messages": [{"content": query, "role": "user"}],
        "search_source": "baidu_search_v2",
        "resource_type_filter": [{"type": "web", "top_k": max(1, min(count, 20))}],
        "search_filter": {},
    }
    _BAIDU_LIMITER.wait("baidu_ai")
    try:
        response = _PUBLIC_API_SESSION.post(
            "https://qianfan.baidubce.com/v2/ai_search/web_search",
            headers={
                "Authorization": f"Bearer {api_key}",
                "X-Appbuilder-From": "openclaw",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=15,
        )
        if response.status_code == 429:
            _baidu_disabled_until = time.monotonic() + 60
            raise PublicSearchProviderLimited("baidu_search_rate_limited")
        if response.status_code != 200:
            raise PublicSearchProviderLimited(f"baidu_search_http_{response.status_code}")
        data = response.json()
        if data.get("code"):
            raise PublicSearchProviderLimited("baidu_search_api_error")
        return [item for item in data.get("references", []) if isinstance(item, dict)]
    except PublicSearchProviderLimited:
        raise
    except (requests.RequestException, ValueError) as exc:
        raise PublicSearchProviderLimited("baidu_search_transport_error") from exc


def _baidu_candidates(query: str) -> list[SearchCandidate]:
    candidates: list[SearchCandidate] = []
    seen: set[str] = set()
    for item in search_baidu_ai(query, count=10):
        url = str(item.get("url") or "").strip()
        domain = normalize_domain(urlparse(url).netloc)
        if (
            not url.startswith(("http://", "https://"))
            or not domain
            or domain in seen
            or not is_usable_company_domain(domain)
            or _is_junk_domain(domain)
        ):
            continue
        seen.add(domain)
        candidates.append(SearchCandidate(
            domain=domain,
            title=_clean_text(item.get("title"), limit=160),
            source_url=url,
            engine="baidu_ai",
            rank=len(candidates) + 1,
        ))
    return candidates


def search_tavily_public(query: str, *, count: int = 10) -> list[dict]:
    api_key = os.environ.get("TAVILY_API_KEY", "").strip()
    if not api_key:
        return []
    _TAVILY_LIMITER.wait("tavily")
    try:
        response = _PUBLIC_API_SESSION.post(
            "https://api.tavily.com/search",
            headers={"Content-Type": "application/json"},
            json={
                "api_key": api_key,
                "query": query,
                "search_depth": "basic",
                "max_results": max(1, min(count, 20)),
            },
            timeout=20,
        )
        if response.status_code in {429, 432}:
            raise PublicSearchProviderLimited("tavily_search_rate_or_quota_limited")
        if response.status_code != 200:
            raise PublicSearchProviderLimited(f"tavily_search_http_{response.status_code}")
        return [item for item in response.json().get("results", []) if isinstance(item, dict)]
    except PublicSearchProviderLimited:
        raise
    except (requests.RequestException, ValueError) as exc:
        raise PublicSearchProviderLimited("tavily_search_transport_error") from exc


def _tavily_candidates(query: str) -> list[SearchCandidate]:
    candidates: list[SearchCandidate] = []
    seen: set[str] = set()
    for item in search_tavily_public(query, count=10):
        url = str(item.get("url") or "").strip()
        domain = normalize_domain(urlparse(url).netloc)
        if (
            not url.startswith(("http://", "https://"))
            or not domain
            or domain in seen
            or not is_usable_company_domain(domain)
            or _is_junk_domain(domain)
        ):
            continue
        seen.add(domain)
        candidates.append(SearchCandidate(
            domain=domain,
            title=_clean_text(item.get("title"), limit=160),
            source_url=url,
            engine="tavily",
            rank=len(candidates) + 1,
        ))
    return candidates


def _product_labels_from_index_text(text: str, target_terms: Iterable[str]) -> list[str]:
    lowered = text.lower()
    terms = sorted(
        {
            term.strip().lower()
            for term in [*HOME_TEXTILE_TERMS, *target_terms]
            if 3 <= len(term.strip()) <= 80
        },
        key=len,
        reverse=True,
    )
    labels: list[str] = []
    for term in terms:
        if term in lowered:
            labels.append(term.title())
    for phrase in re.split(r"[·|,;•]|\.{3,}|\s[-–—]\s", text):
        cleaned = _quality_link_text(phrase)
        if cleaned and any(term in cleaned.lower() for term in terms):
            labels.append(cleaned)
    return _unique(labels, limit=18)


def parse_indexed_company_pages(
    html_text: str,
    *,
    domain: str,
    target_terms: Iterable[str],
) -> list[PageEvidence]:
    """Extract official page evidence from Startpage result cards."""
    soup = BeautifulSoup(html_text, "html.parser")
    pages: list[PageEvidence] = []
    seen: set[str] = set()
    for container in soup.select("div.result, li.b_algo"):
        links: list[tuple[str, str]] = []
        for anchor in container.find_all("a", href=True):
            href = str(anchor.get("href") or "").strip()
            href = _unwrap_bing_url(href)
            if not href.startswith(("http://", "https://")):
                continue
            if _host_matches(urlparse(href).netloc, domain):
                links.append((href, _clean_text(anchor.get_text(" ", strip=True), limit=180)))
        if not links:
            continue
        page_url = links[0][0].split("?", 1)[0].rstrip("/") or links[0][0]
        if page_url in seen:
            continue
        seen.add(page_url)
        title_candidates = [
            title for _, title in links
            if title and "http" not in title.lower() and "visit in anonymous" not in title.lower()
        ]
        title = max(title_candidates, key=len, default="")
        card_text = _clean_text(container.get_text(" ", strip=True), limit=1200)
        pages.append(PageEvidence(
            url=page_url,
            title=title,
            description=card_text,
            product_labels=_product_labels_from_index_text(card_text, target_terms),
            news_labels=[title] if any(marker in page_url.lower() for marker in NEWS_PATH_MARKERS) and title else [],
            text_sample=card_text,
        ))
        if len(pages) >= MAX_SOURCE_PAGES:
            break
    return pages


def collect_search_index_evidence(
    domain: str,
    *,
    company_name: str,
    target_terms: Iterable[str],
    prior_error: str = "",
) -> CompanyEvidence:
    terms = list(target_terms)
    identity = company_name.strip() or domain
    query = f'"{identity}" "{domain}" {" ".join(terms[:5])} products collections official'.strip()
    indexed_pages: dict[str, PageEvidence] = {}
    product_query = f'"{identity}" {" ".join(terms[:8])} category catalog products official'.strip()
    provider_errors: list[str] = []
    provider_succeeded = False
    public_fallback_succeeded = False

    def add_items(items: Iterable[dict]) -> None:
        for item in items:
            url = str(item.get("url") or "").strip()
            if not _host_matches(urlparse(url).netloc, domain):
                continue
            clean_url = url.split("?", 1)[0]
            title = _clean_text(item.get("title"), limit=180)
            content = _clean_text(item.get("content"), limit=1800)
            email_values = re.findall(
                r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}",
                content,
                flags=re.I,
            )
            indexed_pages[clean_url] = PageEvidence(
                url=clean_url,
                title=title,
                description=content,
                product_labels=_product_labels_from_index_text(f"{title} {content}", terms),
                news_labels=[title] if any(marker in f"{url} {title}".lower() for marker in NEWS_PATH_MARKERS) and title else [],
                public_emails=_unique(email_values, limit=8),
                text_sample=content,
            )

    if os.environ.get("BAIDU_API_KEY", "").strip():
        try:
            add_items(search_baidu_ai(product_query, count=10))
            provider_succeeded = True
        except PublicSearchProviderLimited as exc:
            provider_errors.append(str(exc))
    if (
        (not indexed_pages or not any(page.product_labels for page in indexed_pages.values()))
        and os.environ.get("TAVILY_API_KEY", "").strip()
    ):
        try:
            add_items(search_tavily_public(product_query, count=10))
            provider_succeeded = True
        except PublicSearchProviderLimited as exc:
            provider_errors.append(str(exc))
    pages = sorted(
        indexed_pages.values(),
        key=lambda page: (not bool(page.product_labels), not bool(page.news_labels), page.url),
    )[:MAX_SOURCE_PAGES]
    html_text = ""
    api_search_configured = bool(
        os.environ.get("BAIDU_API_KEY", "").strip()
        or os.environ.get("TAVILY_API_KEY", "").strip()
    )
    if not pages and not api_search_configured:
        html_text = _search_html(query, engine="startpage")
        pages = parse_indexed_company_pages(html_text, domain=domain, target_terms=terms) if html_text else []
    if not pages and not api_search_configured:
        fallback_query = f'"{identity}" "{domain}" official website products'.strip()
        html_text = _search_html(fallback_query, engine="startpage")
        pages = parse_indexed_company_pages(html_text, domain=domain, target_terms=terms) if html_text else []
    if not pages and not api_search_configured:
        html_text = _search_html(query, engine="brave")
        candidates = parse_search_html(html_text, engine="brave") if html_text else []
        pages = [
            PageEvidence(url=item.source_url, title=item.title)
            for item in candidates
            if _host_matches(item.domain, domain)
        ][:MAX_SOURCE_PAGES]
    if not pages and provider_errors:
        html_text = _search_html(f'{product_query} site:{domain}', engine="bing")
        public_fallback_succeeded = bool(html_text)
        pages = parse_indexed_company_pages(
            html_text,
            domain=domain,
            target_terms=terms,
        ) if html_text else []
    evidence = CompanyEvidence(
        domain=normalize_domain(domain),
        collection_status=(
            "search_index" if pages else
            "provider_limited" if provider_errors and not (
                provider_succeeded or public_fallback_succeeded
            ) else
            "unreachable"
        ),
        expected_company_name=_clean_text(company_name, limit=255),
        homepage_url=pages[0].url if pages else "",
        pages=pages,
        product_labels=_unique((label for page in pages for label in page.product_labels), limit=18),
        news_labels=_unique((label for page in pages for label in page.news_labels), limit=8),
        public_emails=_unique((value for page in pages for value in page.public_emails), limit=8),
        search_sources=[],
        error_code=(
            "" if pages else
            ";".join(provider_errors) if provider_errors and not provider_succeeded else
            (prior_error or "search_index_no_result")
        ),
    )
    evidence.company_match_ratio = _company_match_ratio(company_name, evidence)
    return evidence


def _robots_allowed(session: requests.Session, base_url: str) -> bool:
    parsed = urlparse(base_url)
    robots_url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"
    try:
        response = session.get(robots_url, timeout=(3, 5))
        if response.status_code in {401, 403}:
            return False
        if response.status_code != 200:
            return True
        parser = RobotFileParser()
        parser.set_url(robots_url)
        parser.parse(response.text.splitlines())
        return parser.can_fetch(USER_AGENT, base_url)
    except requests.RequestException:
        return True


def _fetch_html(session: requests.Session, url: str, *, domain: str) -> tuple[str, str, str]:
    try:
        response = session.get(
            url,
            headers={"User-Agent": USER_AGENT, "Accept": "text/html,application/xhtml+xml"},
            timeout=(4, 8),
            allow_redirects=True,
            stream=True,
        )
        if response.status_code != 200:
            return "", "", f"http_{response.status_code}"
        final_host = urlparse(response.url).netloc
        if not _host_matches(final_host, domain):
            return "", "", "cross_domain_redirect"
        content_type = response.headers.get("Content-Type", "").lower()
        if "html" not in content_type:
            return "", "", "non_html"
        data = bytearray()
        for chunk in response.iter_content(64 * 1024):
            data.extend(chunk)
            if len(data) > MAX_RESPONSE_BYTES:
                return "", "", "response_too_large"
        encoding = response.encoding or "utf-8"
        return bytes(data).decode(encoding, errors="replace"), response.url, ""
    except requests.exceptions.SSLError:
        return "", "", "tls_error"
    except requests.exceptions.Timeout:
        return "", "", "timeout"
    except requests.RequestException:
        return "", "", "request_error"


def _race_urls(urls: Iterable[str], *, domain: str) -> tuple[str, str, str]:
    """Fetch URL variants concurrently and return the first valid HTML page."""
    candidates = list(urls)
    executor = ThreadPoolExecutor(max_workers=min(2, len(candidates) or 1))
    futures = {
        executor.submit(_fetch_html, _session(retries=False), url, domain=domain): url
        for url in candidates
    }
    errors: list[str] = []
    try:
        for future in as_completed(futures):
            html_text, final_url, error = future.result()
            if html_text:
                for pending in futures:
                    if pending is not future:
                        pending.cancel()
                return html_text, final_url, ""
            if error:
                errors.append(error)
        return "", "", errors[-1] if errors else "homepage_unavailable"
    finally:
        executor.shutdown(wait=False, cancel_futures=True)


def _page_evidence(html_text: str, url: str, *, domain: str, target_terms: Iterable[str]) -> PageEvidence:
    soup = BeautifulSoup(html_text, "html.parser")
    title = _clean_text(soup.title.get_text(" ", strip=True) if soup.title else "", limit=180)
    meta = soup.find("meta", attrs={"name": re.compile("^description$", re.I)})
    description = _clean_text(meta.get("content") if meta else "", limit=500)
    headings = _unique(
        (node.get_text(" ", strip=True) for node in soup.find_all(["h1", "h2", "h3"])),
        limit=16,
    )

    product_terms = {
        term.lower() for term in [*HOME_TEXTILE_TERMS, *target_terms] if len(term.strip()) >= 3
    }
    product_labels: list[str] = []
    news_labels: list[str] = []
    useful_links: list[str] = []
    public_emails: list[str] = []
    public_phones: list[str] = []
    for anchor in soup.find_all("a", href=True):
        href = str(anchor.get("href") or "").strip()
        text = _quality_link_text(anchor.get_text(" ", strip=True))
        lowered = f"{href} {text}".lower()
        full_url = urljoin(url, href)
        if href.lower().startswith("mailto:"):
            email = href.split(":", 1)[1].split("?", 1)[0].strip().lower()
            email_domain = email.rsplit("@", 1)[-1] if "@" in email else ""
            if (
                re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", email)
                and email_domain not in JUNK_PUBLIC_EMAIL_DOMAINS
            ):
                public_emails.append(email)
            continue
        if href.lower().startswith("tel:"):
            phone = re.sub(r"[^+0-9() -]", "", href.split(":", 1)[1]).strip()
            if len(re.sub(r"\D", "", phone)) >= 7:
                public_phones.append(phone)
            continue
        parsed = urlparse(full_url)
        if parsed.scheme not in {"http", "https"} or not _host_matches(parsed.netloc, domain):
            continue
        if any(marker in lowered for marker in (*PRODUCT_PATH_MARKERS, *NEWS_PATH_MARKERS, *ABOUT_PATH_MARKERS)):
            useful_links.append(full_url.split("#", 1)[0])
        if text and (
            any(term in lowered for term in product_terms)
            or any(marker in parsed.path.lower() for marker in PRODUCT_PATH_MARKERS)
        ):
            product_labels.append(text)
        if text and any(marker in parsed.path.lower() for marker in NEWS_PATH_MARKERS):
            news_labels.append(text)

    for heading in headings:
        lowered = heading.lower()
        if any(term in lowered for term in product_terms):
            product_labels.append(heading)

    for node in soup(["script", "style", "nav", "footer", "noscript", "svg"]):
        node.decompose()
    text_sample = _clean_text(soup.get_text(" ", strip=True), limit=2500)
    return PageEvidence(
        url=url,
        title=title,
        description=description,
        headings=headings,
        product_labels=_unique(product_labels, limit=15),
        news_labels=_unique(news_labels, limit=8),
        useful_links=_unique(useful_links, limit=25),
        public_emails=_unique(public_emails, limit=8),
        public_phones=_unique(public_phones, limit=6),
        text_sample=text_sample,
    )


def _company_match_ratio(company_name: str, evidence: CompanyEvidence) -> float:
    tokens = company_tokens(company_name)
    if not tokens:
        return 0.0
    blob = " ".join(
        [
            evidence.domain,
            *(
                f"{page.title} {page.description} {' '.join(page.headings)}"
                for page in evidence.pages
            ),
        ]
    ).lower()
    matched = sum(token in blob for token in tokens)
    return matched / len(tokens)


def collect_company_evidence(
    domain: str,
    *,
    company_name: str = "",
    target_terms: Iterable[str] = (),
    search_sources: Iterable[str] = (),
) -> CompanyEvidence:
    domain = normalize_domain(domain)
    evidence = CompanyEvidence(
        domain=domain,
        collection_status="unreachable",
        expected_company_name=_clean_text(company_name, limit=255),
        search_sources=_unique(search_sources, limit=8),
    )
    if not is_usable_company_domain(domain):
        evidence.error_code = "invalid_company_domain"
        return evidence

    session = _session(retries=False)
    homepage_html, homepage_url, last_error = _race_urls(
        (f"https://{domain}", f"https://www.{domain}"),
        domain=domain,
    )
    if not homepage_html:
        homepage_html, homepage_url, last_error = _race_urls(
            (f"http://{domain}", f"http://www.{domain}"),
            domain=domain,
        )
    if not homepage_html:
        return collect_search_index_evidence(
            domain,
            company_name=company_name,
            target_terms=target_terms,
            prior_error=last_error or "homepage_unavailable",
        )
    if not _robots_allowed(session, homepage_url):
        evidence.error_code = "robots_disallowed"
        return evidence

    homepage = _page_evidence(homepage_html, homepage_url, domain=domain, target_terms=target_terms)
    pages = [homepage]
    ordered_links = sorted(
        homepage.useful_links,
        key=lambda link: (
            0 if any(marker in link.lower() for marker in PRODUCT_PATH_MARKERS) else
            1 if any(marker in link.lower() for marker in ABOUT_PATH_MARKERS) else
            2 if any(marker in link.lower() for marker in NEWS_PATH_MARKERS) else 3
        ),
    )
    subpage_links = [
        link for link in ordered_links
        if link != homepage.url
    ][: MAX_SOURCE_PAGES - 1]
    if subpage_links:
        with ThreadPoolExecutor(max_workers=len(subpage_links)) as executor:
            futures = {
                executor.submit(
                    _fetch_html,
                    _session(retries=False),
                    link,
                    domain=domain,
                ): link
                for link in subpage_links
            }
            subpages: list[PageEvidence] = []
            for future in as_completed(futures):
                html_text, final_url, _ = future.result()
                if html_text:
                    subpages.append(
                        _page_evidence(
                            html_text,
                            final_url,
                            domain=domain,
                            target_terms=target_terms,
                        )
                    )
            pages.extend(sorted(subpages, key=lambda page: subpage_links.index(page.url) if page.url in subpage_links else 999))

    evidence.homepage_url = homepage_url
    evidence.pages = pages
    evidence.product_labels = _unique(
        (label for page in pages for label in page.product_labels),
        limit=18,
    )
    evidence.news_labels = _unique(
        (label for page in pages for label in page.news_labels),
        limit=8,
    )
    evidence.public_emails = _unique(
        (value for page in pages for value in page.public_emails),
        limit=8,
    )
    evidence.public_phones = _unique(
        (value for page in pages for value in page.public_phones),
        limit=6,
    )
    evidence.company_match_ratio = _company_match_ratio(company_name, evidence)
    evidence.collection_status = "collected"
    return evidence


def _candidate_score(candidate: SearchCandidate, company_name: str) -> int:
    tokens = company_tokens(company_name)
    if not tokens:
        return 0
    compact_domain = re.sub(r"[^a-z0-9]", "", candidate.domain.split(".", 1)[0])
    title = candidate.title.lower()
    score = max(0, 5 - candidate.rank)
    for token in tokens:
        if token in compact_domain:
            score += 6
        if token in title:
            score += 2
    compact_company = "".join(tokens)
    if compact_company and compact_company in compact_domain:
        score += 8
    if compact_company and compact_domain == compact_company:
        score += 12
    if candidate.domain.endswith(".com"):
        score += 3
    return score


def domain_company_affinity(domain: str, company_name: str) -> bool:
    """Require a domain-level company identity link before calling it official."""
    normalized = normalize_domain(domain)
    tokens = company_tokens(company_name)
    if not normalized or not tokens:
        return False
    compact_domain = re.sub(r"[^a-z0-9]", "", normalized.split(".", 1)[0])
    identity_tokens = [
        token
        for token in re.findall(r"[a-z0-9]+", (company_name or "").lower())
        if token not in COMPANY_STOPWORDS and (len(token) >= 2 or token.isdigit())
    ]
    compact_company = "".join(identity_tokens)
    meaningful = [token for token in tokens if len(token) >= 3 or token.isdigit()]
    acronym = "".join(token[0] for token in meaningful if token)
    matched_tokens = [token for token in meaningful if token in compact_domain]
    return bool(
        (len(compact_company) >= 6 and compact_company == compact_domain)
        or (
            len(identity_tokens) >= 2
            and len(compact_company) >= 6
            and compact_company in compact_domain
        )
        or len(matched_tokens) >= 2
        or (
            meaningful
            and len(meaningful[0]) >= 6
            and (
                compact_domain == meaningful[0]
                or (len(identity_tokens) >= 2 and compact_domain.startswith(meaningful[0]))
            )
        )
        or (
            len(identity_tokens) >= 2
            and len(acronym) >= 3
            and (compact_domain == acronym or compact_domain.startswith(acronym))
        )
    )


def resolve_company_domain_public(
    company_name: str,
    *,
    target_terms: Iterable[str] = (),
    index_only: bool = False,
) -> CompanyEvidence:
    tokens = company_tokens(company_name)
    if not tokens:
        return CompanyEvidence(
            domain="",
            collection_status="unresolved",
            expected_company_name=company_name,
            error_code="company_name_has_no_distinctive_tokens",
        )
    query = f'"{company_name}" official website'.strip()
    provider_errors: list[str] = []
    baidu: list[SearchCandidate] = []
    try:
        baidu = _baidu_candidates(query)
    except PublicSearchProviderLimited as exc:
        provider_errors.append(str(exc))
    tavily: list[SearchCandidate] = []
    if not baidu or _candidate_score(baidu[0], company_name) < 7:
        try:
            tavily = _tavily_candidates(query)
        except PublicSearchProviderLimited as exc:
            provider_errors.append(str(exc))
    api_configured = bool(
        os.environ.get("BAIDU_API_KEY", "").strip()
        or os.environ.get("TAVILY_API_KEY", "").strip()
    )
    startpage = [] if api_configured else search_public_web(
        query,
        engine="startpage",
        company_name=company_name,
    )
    candidates = sorted([*baidu, *tavily, *startpage], key=lambda item: _candidate_score(item, company_name), reverse=True)
    bing_fallback_succeeded = False
    if (not candidates or _candidate_score(candidates[0], company_name) < 7) and provider_errors:
        bing_html = _search_html(query, engine="bing")
        bing_fallback_succeeded = bool(bing_html)
        bing = parse_search_html(
            bing_html,
            engine="bing",
            company_name=company_name,
        ) if bing_html else []
        merged = {candidate.domain: candidate for candidate in [*candidates, *bing]}
        candidates = sorted(
            merged.values(),
            key=lambda item: _candidate_score(item, company_name),
            reverse=True,
        )
    if (not candidates or _candidate_score(candidates[0], company_name) < 7) and not api_configured:
        brave = search_public_web(query, engine="brave", company_name=company_name)
        merged = {candidate.domain: candidate for candidate in [*baidu, *tavily, *startpage, *brave]}
        candidates = sorted(merged.values(), key=lambda item: _candidate_score(item, company_name), reverse=True)
    if not candidates and provider_errors and not bing_fallback_succeeded:
        return CompanyEvidence(
            domain="",
            collection_status="provider_limited",
            expected_company_name=company_name,
            error_code=";".join(provider_errors),
        )

    accepted: list[tuple[int, CompanyEvidence]] = []
    verification_provider_errors: list[str] = []
    verification_candidates = candidates[:1]
    if (
        len(candidates) > 1
        and _candidate_score(candidates[1], company_name)
        >= _candidate_score(candidates[0], company_name) - 2
    ):
        verification_candidates = candidates[:2]
    for candidate in verification_candidates:
        raw_score = _candidate_score(candidate, company_name)
        if raw_score < 7 or not domain_company_affinity(candidate.domain, company_name):
            continue
        if index_only:
            evidence = collect_search_index_evidence(
                candidate.domain,
                company_name=company_name,
                target_terms=target_terms,
            )
            if candidate.source_url not in evidence.search_sources:
                evidence.search_sources.append(candidate.source_url)
        else:
            evidence = collect_company_evidence(
                candidate.domain,
                company_name=company_name,
                target_terms=target_terms,
                search_sources=[candidate.source_url],
            )
        if evidence.collection_status == "provider_limited":
            verification_provider_errors.append(
                evidence.error_code or "public_search_provider_limited"
            )
            continue
        required_matches = 1.0 if len(tokens) == 1 else math.ceil(len(tokens) * 0.6) / len(tokens)
        if (
            evidence.collection_status in {"collected", "search_index"}
            and evidence.company_match_ratio >= required_matches
        ):
            accepted.append((raw_score + round(evidence.company_match_ratio * 10), evidence))

    accepted.sort(key=lambda item: item[0], reverse=True)
    if not accepted:
        if verification_provider_errors:
            return CompanyEvidence(
                domain="",
                collection_status="provider_limited",
                expected_company_name=company_name,
                search_sources=[candidate.source_url for candidate in candidates[:3]],
                error_code=";".join(_unique(verification_provider_errors, limit=4)),
            )
        return CompanyEvidence(
            domain="",
            collection_status="unresolved",
            expected_company_name=company_name,
            search_sources=[candidate.source_url for candidate in candidates[:3]],
            error_code="no_verified_official_domain",
        )
    if len(accepted) > 1 and accepted[0][0] - accepted[1][0] <= 2:
        return CompanyEvidence(
            domain="",
            collection_status="ambiguous",
            expected_company_name=company_name,
            search_sources=[item[1].homepage_url for item in accepted[:2]],
            error_code="multiple_company_domains_match",
        )
    return accepted[0][1]


def build_brief_data(
    evidence: CompanyEvidence,
    *,
    company_name: str,
    contact_name: str,
    job_title: str,
    product_focus: str,
) -> dict:
    display_company = _clean_text(company_name, limit=255) or evidence.domain or "the recorded company"
    contact = " ".join(part for part in (_clean_text(contact_name, limit=120), _clean_text(job_title, limit=160)) if part)
    sources: list[dict[str, str]] = []
    if evidence.homepage_url:
        sources.append({
            "type": (
                "official_website"
                if evidence.collection_status == "collected"
                else "official_indexed_page"
            ),
            "value": evidence.homepage_url,
        })
    for page in evidence.pages[1:]:
        sources.append({"type": "official_subpage", "value": page.url})
    for value in evidence.search_sources:
        sources.append({"type": "public_search_result", "value": value})
    for value in evidence.public_emails:
        sources.append({"type": "public_company_email", "value": value})
    for value in evidence.public_phones:
        sources.append({"type": "public_company_phone", "value": value})
    deduplicated_sources: list[dict[str, str]] = []
    seen_source_values: set[str] = set()
    for source in sources:
        value = str(source.get("value") or "").strip()
        if value and value not in seen_source_values:
            deduplicated_sources.append(source)
            seen_source_values.add(value)
    sources = deduplicated_sources

    if evidence.collection_status not in {"collected", "search_index"}:
        overview = f"The database identifies {display_company}"
        if contact:
            overview += f" through the recorded contact {contact}"
        overview += ". An official company website has not yet been independently verified from public sources."
        return {
            "company_overview": overview,
            "specific_products": None,
            "recent_news": None,
            "recent_activity": None,
            "pain_points": "Qualification pending: company identity, active product categories, and buying authority require confirmation.",
            "value_proposition_alignment": "No product-fit claim is made until an official company source is verified.",
            "personalization_hook": None,
            "research_status": "insufficient",
            "quality_flags": [
                "public_web:company_unresolved",
                f"public_web:{evidence.error_code or evidence.collection_status}",
            ],
            "evidence_sources": sources,
        }

    title = next((page.title for page in evidence.pages if page.title), evidence.domain)
    description = next((page.description for page in evidence.pages if page.description), "")
    products = evidence.product_labels[:8]
    overview = f"{display_company} operates the verified public website {evidence.domain}."
    if description:
        overview += f" Its public site describes the business as: {_clean_text(description, limit=320)}"
    else:
        overview += f" The homepage is publicly titled “{_clean_text(title, limit=160)}”."
    if contact:
        overview += f" The associated database contact is {contact}."

    specific_products = "; ".join(products) if products else None
    hook = None
    if products:
        hook = (
            f"Reference {display_company}'s public catalog categories—"
            f"{', '.join(products[:3])}—and ask how its assortment plans relate to the proposed supply program."
        )
    news = "; ".join(evidence.news_labels[:4]) if evidence.news_labels else None
    focus = _clean_text(product_focus, limit=300)
    return {
        "company_overview": overview,
        "specific_products": specific_products,
        "recent_news": news,
        "recent_activity": news,
        "pain_points": (
            "Qualification hypotheses, not confirmed facts: assortment differentiation, "
            "supplier reliability, lead times, quality consistency, and minimum-order flexibility."
        ),
        "value_proposition_alignment": (
            f"Potential alignment to validate against the public catalog: {focus}."
            if focus else
            "Potential supplier alignment should be validated against the public catalog and the contact's buying authority."
        ),
        "personalization_hook": hook,
        "research_status": "valid" if products and len(sources) >= 2 else "insufficient",
        "quality_flags": [
            "public_web:evidence_first",
            "public_web:no_personal_email_guessing",
            "public_web:inferred_pain_points_labeled",
        ] + ([] if products else ["missing_target_product_evidence"]),
        "evidence_sources": sources,
    }
