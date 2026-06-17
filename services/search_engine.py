import requests
from bs4 import BeautifulSoup
from dataclasses import dataclass
from typing import Dict, List, Optional
import time
import re
import random
import logging
import os

from services.http_client import http as _http

logger = logging.getLogger("outbound_engine")


_tavily_disabled_until = 0.0
_bocha_disabled_until = 0.0

EUROPEAN_MARKETS = [
    "Spain", "Italy", "Germany", "France", "Netherlands", "United Kingdom",
    "Portugal", "Belgium", "Sweden", "Denmark", "Norway", "Finland",
    "Poland", "Austria", "Switzerland", "Ireland", "Czech Republic",
]

GLOBAL_MARKETS = [
    "", "Europe", "United States", "Canada", "Australia", "United Kingdom",
    "Germany", "France", "Spain", "Italy", "Netherlands", "Scandinavia",
    "Eastern Europe", "Latin America", "Middle East", "Asia Pacific",
]

SELLER_SIDE_TERMS = [
    "oem", "odm", "factory", "factories", "manufacturer", "manufacturers",
    "manufacturing", "producer", "producers", "production", "supplier",
    "suppliers", "made in china", "china supplier",
]

BUYER_SIDE_TERMS = [
    "distributor", "distributors", "retailer", "retailers", "shop", "shops",
    "store", "stores", "pro shop", "pro shops", "importer", "importers",
    "wholesaler", "wholesalers", "dealer", "dealers",
]

DISCOVERY_SOURCE_ALIASES = {
    "web": "web",
    "website": "web",
    "search": "web",
    "customs": "customs",
    "custom": "customs",
    "trade_data": "customs",
    "competitor": "competitors",
    "competitors": "competitors",
    "trade_show": "trade_shows",
    "trade_shows": "trade_shows",
    "show": "trade_shows",
    "association": "directories",
    "associations": "directories",
    "directory": "directories",
    "directories": "directories",
    "retail": "retail",
    "ecommerce": "retail",
    "e-commerce": "retail",
    "social": "social",
    "social_media": "social",
}

DEFAULT_DISCOVERY_SOURCES = ["web", "directories", "retail", "social"]

SOURCE_DATA_LABELS = {
    "web": "web search, website",
    "customs": "customs/trade-data clue search, website",
    "competitors": "competitor/dealer mining, website",
    "trade_shows": "trade-show/exhibitor search, website",
    "directories": "association or B2B directory search, website",
    "retail": "retail/ecommerce reverse lookup, website",
    "social": "social profile discovery, website",
}


@dataclass(frozen=True)
class SearchQuerySpec:
    query: str
    source_channel: str
    data_sources: str

USER_AGENTS = [
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2 Safari/605.1.15",
]

def _int_env(name: str, default: int, min_value: int = 1, max_value: int = 1000) -> int:
    try:
        value = int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default
    return max(min_value, min(max_value, value))


def _bool_env(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.lower() not in {"0", "false", "no", "off"}


def _geo_terms_in_keywords(keywords: str) -> bool:
    text = f" {keywords.lower()} "
    terms = [
        "europe", "european", "uk", "united kingdom", "ireland",
        "spain", "portugal", "france", "germany", "italy", "netherlands",
        "belgium", "sweden", "norway", "finland", "denmark",
        "austria", "switzerland", "poland", "czech", "hungary",
        "romania", "greece", "turkey", "usa", "united states", "canada",
        "australia", "japan", "korea", "singapore", "brazil", "mexico",
        "uae", "saudi arabia", "chile", "colombia",
    ]
    return any(f" {term} " in text for term in terms)


def _split_keyword_phrases(keywords: str) -> List[str]:
    parts = re.split(r"[,;\n]+", keywords or "")
    phrases = []
    seen = set()
    for part in parts:
        phrase = re.sub(r"\s+", " ", part).strip()
        if not phrase:
            continue
        key = phrase.lower()
        if key not in seen:
            phrases.append(phrase)
            seen.add(key)
    return phrases or [re.sub(r"\s+", " ", keywords or "").strip()]


def _split_csv_terms(value: Optional[str], limit: int = 12) -> List[str]:
    parts = re.split(r"[,;\n|]+", value or "")
    terms = []
    seen = set()
    for part in parts:
        term = re.sub(r"\s+", " ", part).strip()
        if not term:
            continue
        key = term.lower()
        if key in seen:
            continue
        terms.append(term)
        seen.add(key)
        if len(terms) >= limit:
            break
    return terms


def _parse_discovery_sources(search_sources: Optional[str]) -> List[str]:
    configured = search_sources or os.environ.get(
        "SEARCH_DEFAULT_SOURCES",
        ",".join(DEFAULT_DISCOVERY_SOURCES),
    )
    sources = []
    seen = set()
    for raw in _split_csv_terms(configured, limit=12):
        key = raw.lower().replace("-", "_").replace(" ", "_")
        source = DISCOVERY_SOURCE_ALIASES.get(key)
        if not source or source in seen:
            continue
        sources.append(source)
        seen.add(source)
    return sources or DEFAULT_DISCOVERY_SOURCES[:]


def _market_terms() -> List[str]:
    return sorted(set(EUROPEAN_MARKETS + GLOBAL_MARKETS + [
        "Europe", "European", "UK", "USA", "United States", "UAE",
        "Mexico", "Argentina", "Brazil", "Chile", "Colombia",
        "Japan", "Korea", "Singapore", "Saudi Arabia",
    ]), key=len, reverse=True)


def _strip_market_terms(text: str) -> str:
    cleaned = f" {text or ''} "
    for market in _market_terms():
        if not market:
            continue
        cleaned = re.sub(r"\b" + re.escape(market) + r"\b", " ", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" ,;-")
    return cleaned


def _normalize_buyer_intent_query(text: str) -> str:
    """Rewrite seller-side product terms into buyer-side discovery terms.

    Outbound workflows usually need prospective buyers/distributors, not
    factories that sell the same thing. This keeps existing bad workflow
    keywords from repeatedly sending search toward OEM/manufacturer pages.
    """
    if not _bool_env("SEARCH_BUYER_INTENT_MODE", True):
        return re.sub(r"\s+", " ", text or "").strip()

    cleaned = f" {text or ''} "
    for term in sorted(SELLER_SIDE_TERMS, key=len, reverse=True):
        cleaned = re.sub(r"\b" + re.escape(term) + r"\b", " ", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" ,;-")
    return cleaned


def _markets_from_keywords(keywords: str) -> List[str]:
    text = f" {keywords or ''} "
    if re.search(r"\b(europe|european)\b", text, flags=re.IGNORECASE):
        return EUROPEAN_MARKETS

    found = []
    for market in _market_terms():
        if not market or market.lower() in {"europe", "european"}:
            continue
        if re.search(r"\b" + re.escape(market) + r"\b", text, flags=re.IGNORECASE):
            normalized = "United Kingdom" if market.upper() == "UK" else "United States" if market.upper() in {"USA", "US"} else market
            if normalized not in found:
                found.append(normalized)
    return found


def _market_for_offset(keywords: str, offset: int) -> str:
    markets = _markets_from_keywords(keywords)
    if not markets:
        markets = GLOBAL_MARKETS
    if not markets:
        return ""
    page_index = max(0, offset // 50)
    if page_index >= len(markets):
        return ""
    return markets[page_index]


def _search_query_for_offset(keywords: str, offset: int) -> str:
    phrases = _split_keyword_phrases(keywords)
    base = _normalize_buyer_intent_query(_strip_market_terms(phrases[0]) or phrases[0])
    market = _market_for_offset(keywords, offset)
    return f"{base} {market}".strip()


def _source_markets(keywords: str, target_region: Optional[str]) -> List[str]:
    markets = _markets_from_keywords(f"{keywords or ''} {target_region or ''}")
    if not markets and target_region:
        markets = _split_csv_terms(target_region, limit=8)
    if not markets:
        markets = [""]
    limit = _int_env("SOURCE_MARKETS_PER_ROTATION", 8, 1, 30)
    return markets[:limit]


def _base_product_phrases(keywords: str) -> List[str]:
    phrases = []
    for phrase in _split_keyword_phrases(keywords)[:4]:
        cleaned = _normalize_buyer_intent_query(_strip_market_terms(phrase) or phrase)
        cleaned = re.sub(r"\s+", " ", cleaned).strip()
        if cleaned and cleaned.lower() not in {p.lower() for p in phrases}:
            phrases.append(cleaned)
    return phrases or [_normalize_buyer_intent_query(_strip_market_terms(keywords) or keywords)]


def _append_source_spec(
    specs: List[SearchQuerySpec],
    seen: set[str],
    query: str,
    source: str,
) -> None:
    cleaned = re.sub(r"\s+", " ", query or "").strip(" ,;-")
    if not cleaned:
        return
    key = f"{source}:{cleaned.lower()}"
    if key in seen:
        return
    specs.append(SearchQuerySpec(
        query=cleaned,
        source_channel=source,
        data_sources=SOURCE_DATA_LABELS.get(source, "source search, website"),
    ))
    seen.add(key)


def build_source_search_queries(
    keywords: str,
    search_sources: Optional[str] = None,
    target_region: Optional[str] = None,
    competitor_names: Optional[str] = None,
    trade_show_names: Optional[str] = None,
) -> List[SearchQuerySpec]:
    """Build source-specific buyer discovery searches.

    This broadens lead discovery beyond one generic keyword search while still
    keeping every query tied to buyer intent and a traceable source channel.
    """
    sources = _parse_discovery_sources(search_sources)
    bases = _base_product_phrases(keywords)
    markets = _source_markets(keywords, target_region)
    competitors = _split_csv_terms(competitor_names, limit=10)
    trade_shows = _split_csv_terms(trade_show_names, limit=10)
    specs: List[SearchQuerySpec] = []
    seen: set[str] = set()

    for source in sources:
        for base in bases:
            for market in markets:
                market_part = market.strip()
                if source == "web":
                    for suffix in ["official website", "company", "importer", "distributor", "wholesaler"]:
                        _append_source_spec(specs, seen, f"{base} {market_part} {suffix}", source)

                elif source == "customs":
                    for suffix in [
                        "importer trade data",
                        "shipment records buyer",
                        "bill of lading importer",
                        "importers list",
                        "customs data buyer",
                    ]:
                        _append_source_spec(specs, seen, f"{base} {market_part} {suffix}", source)

                elif source == "competitors":
                    for competitor in competitors:
                        for suffix in ["distributor", "dealer", "stockist", "where to buy", "authorized retailer"]:
                            _append_source_spec(specs, seen, f'"{competitor}" {market_part} {suffix}', source)

                elif source == "trade_shows":
                    if trade_shows:
                        for show in trade_shows:
                            for suffix in ["exhibitor list", "buyer list", "visitor", "distributor", "brand"]:
                                _append_source_spec(specs, seen, f'"{show}" {base} {market_part} {suffix}', source)
                    else:
                        for suffix in ["trade show exhibitor list", "industry fair exhibitors", "expo distributor", "exhibition buyer"]:
                            _append_source_spec(specs, seen, f"{base} {market_part} {suffix}", source)

                elif source == "directories":
                    for suffix in [
                        "distributor directory",
                        "importers association",
                        "wholesale association",
                        "dealer directory",
                        "B2B directory",
                    ]:
                        _append_source_spec(specs, seen, f"{base} {market_part} {suffix}", source)

                elif source == "retail":
                    for suffix in ["where to buy", "stockists", "dealer locator", "online retailer", "multi brand store", "pro shop"]:
                        _append_source_spec(specs, seen, f"{base} {market_part} {suffix}", source)

                elif source == "social":
                    for suffix in ["LinkedIn company", "Instagram brand", "Facebook retailer", "social profile distributor"]:
                        _append_source_spec(specs, seen, f"{base} {market_part} {suffix}", source)

    return specs


def _dedupe_extend(target: List[str], values: List[str], limit: int) -> None:
    for value in values:
        if value and value not in target:
            target.append(value)
        if len(target) >= limit:
            return


def _search_google_direct_query(search_query: str, offset: int = 0, max_domains: int = 80) -> List[str]:
    direct_provider = os.environ.get("SEARCH_DIRECT_PROVIDER", "").strip().lower()
    if direct_provider == "bing" or _bool_env("SEARCH_PREFER_BING", False):
        logger.info("SEARCH_DIRECT_PROVIDER=bing; skipping Google/DuckDuckGo direct search")
        return _search_bing(search_query, offset)[:max_domains]

    logger.info(f"Searching Google directly for: {search_query} (offset: {offset})")

    headers = {
        "User-Agent": random.choice(USER_AGENTS),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.5",
    }

    query = search_query.replace(" ", "+")
    url = f"https://www.google.com/search?q={query}&num=50&hl=en&start={offset}"

    try:
        resp = _http.get(url, headers=headers, timeout=15)
        logger.info(f"Google response status: {resp.status_code}")

        if resp.status_code == 429:
            logger.warning("Google rate limited (429), backing off...")
            time.sleep(random.uniform(5, 15))
            return _search_duckduckgo(search_query, offset)[:max_domains]

        if resp.status_code != 200:
            logger.warning(f"Google returned {resp.status_code}, trying DuckDuckGo...")
            return _search_duckduckgo(search_query, offset)[:max_domains]

        soup = BeautifulSoup(resp.text, "html.parser")
        domains = []

        # Extract URLs from Google search results
        for a_tag in soup.find_all("a", href=True):
            href = a_tag["href"]
            if "/url?q=" in href:
                actual_url = href.split("/url?q=")[1].split("&")[0]
                domain = _extract_domain(actual_url)
                if domain and domain not in domains and not _is_junk_domain(domain):
                    domains.append(domain)

        # Also try direct link extraction
        if not domains:
            for a_tag in soup.find_all("a", href=True):
                href = a_tag["href"]
                if href.startswith("http") and "google" not in href:
                    domain = _extract_domain(href)
                    if domain and domain not in domains and not _is_junk_domain(domain):
                        domains.append(domain)

        logger.info(f"Found {len(domains)} domains from Google")

        if not domains:
            logger.info("Google returned no results, trying DuckDuckGo...")
            return _search_duckduckgo(search_query, offset)[:max_domains]

        return domains[:max_domains]

    except requests.exceptions.Timeout:
        logger.warning("Google search timed out, trying DuckDuckGo...")
        return _search_duckduckgo(search_query, offset)[:max_domains]
    except Exception as e:
        logger.error(f"Google search error: {e}, trying DuckDuckGo...")
        return _search_duckduckgo(search_query, offset)[:max_domains]


def _bocha_web_pages(data: Dict) -> List[Dict]:
    payload = data.get("data", data)
    web_pages = payload.get("webPages", {}) if isinstance(payload, dict) else {}
    if isinstance(web_pages, dict):
        pages = web_pages.get("value", [])
    elif isinstance(web_pages, list):
        pages = web_pages
    else:
        pages = []
    return [page for page in pages if isinstance(page, dict)]


def _bocha_enabled() -> bool:
    """False while Bocha is in a circuit-breaker cooldown (quota/auth failure)."""
    return time.time() >= _bocha_disabled_until


def _disable_bocha(reason: str):
    global _bocha_disabled_until
    cooldown = _int_env("BOCHA_DISABLE_COOLDOWN_SECONDS", 3600, 60, 86400)
    _bocha_disabled_until = time.time() + cooldown
    logger.warning(f"Bocha disabled for {cooldown}s: {reason}")


def _search_bocha_results(search_query: str, api_key: str, max_results: int = 10) -> List[Dict[str, str]]:
    if not _bocha_enabled():
        return []
    endpoint = os.environ.get("BOCHA_API_URL", "https://api.bochaai.com/v1/web-search")
    payload = {
        "query": search_query,
        "freshness": os.environ.get("BOCHA_SEARCH_FRESHNESS", "noLimit"),
        "summary": _bool_env("BOCHA_SEARCH_SUMMARY", False),
        "count": max(1, min(max_results, 50)),
    }
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    try:
        logger.info(f"Searching Bocha API for: '{search_query}'")
        resp = _http.post(endpoint, headers=headers, json=payload, timeout=_int_env("BOCHA_SEARCH_TIMEOUT", 20, 5, 60))
        if resp.status_code != 200:
            logger.warning(f"Bocha API returned {resp.status_code}: {resp.text[:300]}")
            # Persistent account-level failures (quota exhausted / bad key / rate
            # limit) won't recover on the next query — trip the breaker so we stop
            # hammering Bocha (and adding latency) on every single search.
            if resp.status_code in (401, 402, 403, 429):
                _disable_bocha(f"HTTP {resp.status_code} — check Bocha account balance/quota and API key")
            return []

        results = []
        for item in _bocha_web_pages(resp.json()):
            url = item.get("url") or item.get("link") or ""
            domain = _extract_domain(url)
            if not domain or _is_junk_domain(domain):
                continue
            results.append({
                "title": _clean_title(item.get("name") or item.get("title") or _title_from_domain(domain)),
                "url": url if url.startswith("http") else f"https://{domain}",
                "domain": domain,
                "snippet": (item.get("snippet") or item.get("summary") or "").strip(),
                "source": "bocha",
            })
            if len(results) >= max_results:
                break

        logger.info(f"Found {len(results)} results from Bocha API")
        return results
    except Exception as e:
        logger.error(f"Bocha API error: {e}")
        return []


def _search_bocha_query(search_query: str, api_key: str, max_domains: int = 30) -> List[str]:
    domains = []
    for item in _search_bocha_results(search_query, api_key, max_results=max_domains):
        domain = item.get("domain")
        if domain and domain not in domains:
            domains.append(domain)
    return domains[:max_domains]


def _search_plain_query(search_query: str, max_domains: int = 30) -> List[str]:
    bocha_api_key = os.environ.get("BOCHA_API_KEY")
    if bocha_api_key and _bocha_enabled():
        domains = _search_bocha_query(search_query, bocha_api_key, max_domains=max_domains)
        if domains:
            return domains[:max_domains]
        logger.warning("Bocha direct query returned no results or failed, trying Tavily...")

    tavily_api_key = os.environ.get("TAVILY_API_KEY")
    if tavily_api_key:
        domains = _search_tavily_query(search_query, "", tavily_api_key)
        if domains:
            return domains[:max_domains]
        logger.warning("Tavily direct query returned no results or failed, falling back to direct scraping...")
    return _search_google_direct_query(search_query, offset=0, max_domains=max_domains)


def search_domains(keywords: str, offset: int = 0, max_domains: int = None) -> List[str]:
    """Search APIs/direct search to find relevant company domains."""
    max_domains = max_domains or _int_env("SEARCH_MAX_DOMAINS_PER_BATCH", 80, 10, 250)
    search_query = _search_query_for_offset(keywords, offset)

    bocha_api_key = os.environ.get("BOCHA_API_KEY")
    if bocha_api_key and _bocha_enabled():
        domains = _search_bocha_query(search_query, bocha_api_key, max_domains=max_domains)
        if domains:
            return domains
        logger.warning("Bocha API returned no results or failed, trying Tavily...")

    tavily_api_key = os.environ.get("TAVILY_API_KEY")
    if tavily_api_key:
        domains = _search_tavily(keywords, offset, tavily_api_key, max_domains=max_domains)
        if domains:
            return domains
        logger.warning("Tavily API returned no results or failed, falling back to direct scraping...")

    return _search_google_direct_query(search_query, offset=offset, max_domains=max_domains)


def search_domain_results(
    keywords: str,
    offset: int = 0,
    max_domains: int = None,
    search_sources: Optional[str] = None,
    target_region: Optional[str] = None,
    competitor_names: Optional[str] = None,
    trade_show_names: Optional[str] = None,
) -> List[Dict[str, str]]:
    """Search for lead domains and preserve the discovery source.

    The older search_domains API returns only domains. This source-aware API
    fans out across buyer-intent search paths such as customs clues, trade
    shows, directories, competitor dealer pages, retail stockists, and social
    profiles, then returns the source for scoring and reporting.
    """
    if not _bool_env("SEARCH_MULTI_SOURCE_MODE", True):
        return [
            {
                "domain": domain,
                "source_channel": "web",
                "data_sources": SOURCE_DATA_LABELS["web"],
                "query": _search_query_for_offset(keywords, offset),
            }
            for domain in search_domains(keywords, offset=offset, max_domains=max_domains)
        ]

    max_domains = max_domains or _int_env("SEARCH_MAX_DOMAINS_PER_BATCH", 80, 10, 250)
    specs = build_source_search_queries(
        keywords=keywords,
        search_sources=search_sources,
        target_region=target_region,
        competitor_names=competitor_names,
        trade_show_names=trade_show_names,
    )
    if not specs:
        return [
            {
                "domain": domain,
                "source_channel": "web",
                "data_sources": SOURCE_DATA_LABELS["web"],
                "query": _search_query_for_offset(keywords, offset),
            }
            for domain in search_domains(keywords, offset=offset, max_domains=max_domains)
        ]

    queries_per_batch = _int_env("SOURCE_QUERIES_PER_BATCH", 6, 1, 20)
    page_index = max(0, offset // 50)
    start = page_index * queries_per_batch
    batch_specs = specs[start:start + queries_per_batch]
    if not batch_specs:
        logger.info(
            f"Source-aware search exhausted: page_index={page_index}, "
            f"specs={len(specs)}, keywords='{keywords}'"
        )
        return []

    per_query = max(5, min(25, (max_domains + len(batch_specs) - 1) // len(batch_specs)))
    results: List[Dict[str, str]] = []
    seen_domains = set()

    logger.info(
        f"Source-aware search batch: {len(batch_specs)} queries "
        f"(page_index={page_index}, max_domains={max_domains})"
    )
    for spec in batch_specs:
        if len(results) >= max_domains:
            break
        logger.info(f"[SOURCE:{spec.source_channel}] {spec.query}")
        domains = _search_plain_query(spec.query, max_domains=per_query)
        for domain in domains:
            if not domain or domain in seen_domains:
                continue
            seen_domains.add(domain)
            results.append({
                "domain": domain,
                "source_channel": spec.source_channel,
                "data_sources": spec.data_sources,
                "query": spec.query,
            })
            if len(results) >= max_domains:
                break

    logger.info(f"Source-aware search found {len(results)} unique domains")
    return results


def search_company_results(keywords: str, count: int = 10, offset: int = 0) -> List[Dict[str, str]]:
    """Return real company-like search results with title, URL, and domain.

    This is used by the AI Agent when the user asks for a company list rather
    than full contact enrichment. It keeps the result grounded in live search
    results, while the outbound pipeline can still use search_domains for bulk
    domain discovery.
    """
    count = _int_env("AGENT_COMPANY_SEARCH_COUNT", count, 1, 20)
    bocha_api_key = os.environ.get("BOCHA_API_KEY")
    if bocha_api_key:
        search_query = _search_query_for_offset(keywords, offset)
        results = _search_bocha_results(search_query, bocha_api_key, max_results=count)
        if results:
            return results

    tavily_api_key = os.environ.get("TAVILY_API_KEY")
    if tavily_api_key:
        results = _search_tavily_company_results(keywords, count=count, offset=offset, api_key=tavily_api_key)
        if results:
            return results

    domains = search_domains(keywords, offset=offset, max_domains=max(count * 2, 10))
    return [
        {
            "title": _title_from_domain(domain),
            "url": f"https://{domain}",
            "domain": domain,
            "snippet": "",
            "source": "search",
        }
        for domain in domains[:count]
    ]


def _search_tavily_company_results(keywords: str, count: int, offset: int, api_key: str) -> List[Dict[str, str]]:
    region_modifiers = ["", "Europe", "Spain", "Italy", "France", "Germany", "Netherlands", "UK", "Sweden"]
    page_index = offset // 10 if offset > 0 else 0
    modifier = region_modifiers[page_index % len(region_modifiers)]
    search_query = f"{keywords} {modifier}".strip()
    payload = {
        "api_key": api_key,
        "query": search_query,
        "search_depth": "basic",
        "max_results": min(max(count * 3, 10), 20),
    }
    try:
        resp = _http.post(
            "https://api.tavily.com/search",
            headers={"Content-Type": "application/json"},
            json=payload,
            timeout=20,
        )
        if resp.status_code != 200:
            logger.warning(f"Tavily API returned {resp.status_code}: {resp.text}")
            return []

        seen = set()
        company_results: List[Dict[str, str]] = []
        for item in resp.json().get("results", []):
            url = item.get("url", "")
            domain = _extract_domain(url)
            if not domain or domain in seen or _is_junk_domain(domain):
                continue
            seen.add(domain)
            company_results.append({
                "title": _clean_title(item.get("title") or _title_from_domain(domain)),
                "url": url if url.startswith("http") else f"https://{domain}",
                "domain": domain,
                "snippet": (item.get("content") or "").strip(),
                "source": "tavily",
            })
            if len(company_results) >= count:
                break
        return company_results
    except Exception as e:
        logger.error(f"Tavily company search error: {e}")
        return []


def _clean_title(title: str) -> str:
    title = re.sub(r"\s+", " ", title or "").strip(" -|")
    return title[:90] if title else ""


def _title_from_domain(domain: str) -> str:
    root = domain.split(".")[0].replace("-", " ").replace("_", " ")
    return root.title() if root else domain

def _build_query_variants(keywords: str) -> List[str]:
    phrases = [
        _normalize_buyer_intent_query(_strip_market_terms(phrase) or phrase)
        for phrase in _split_keyword_phrases(keywords)
    ]
    phrases = [phrase for phrase in phrases if phrase]
    if not phrases:
        return []

    suffixes = [
        "",
        "company",
        "official website",
        *BUYER_SIDE_TERMS,
        "wholesale",
    ]
    variants = []
    lower_seen = set()
    for phrase in phrases[:4]:
        for suffix in suffixes:
            query = f"{phrase} {suffix}".strip()
            key = query.lower()
            if key not in lower_seen:
                variants.append(query)
                lower_seen.add(key)
    return variants


def _search_tavily(keywords: str, offset: int, api_key: str, max_domains: int = 80) -> List[str]:
    """Search using Tavily API for high reliability and clean AI-optimized results.
    Since Tavily doesn't support pagination, we simulate it by appending
    geographic modifiers to the query based on the offset value and also
    broaden the wording per batch to avoid getting only the top few domains.
    """
    global _tavily_disabled_until
    if _bool_env("TAVILY_DISABLE_ON_USAGE_LIMIT", True) and time.time() < _tavily_disabled_until:
        remaining = int(_tavily_disabled_until - time.time())
        logger.info(f"Tavily disabled for {remaining}s due to usage limit, skipping batch")
        return []

    region_modifiers = _markets_from_keywords(keywords) or GLOBAL_MARKETS
    
    page_index = offset // 50 if offset > 0 else 0
    if page_index >= len(region_modifiers):
        # All markets exhausted, signal keyword mutation.
        return []
    
    variants = _build_query_variants(keywords)
    queries_per_batch = _int_env("TAVILY_QUERIES_PER_BATCH", 6, 1, 12)
    regions_per_batch = _int_env("TAVILY_REGIONS_PER_BATCH", 2, 1, 4)
    domains: List[str] = []
    attempts = 0

    for region_index in range(page_index, min(page_index + regions_per_batch, len(region_modifiers))):
        modifier = region_modifiers[region_index]
        for variant in variants:
            if attempts >= queries_per_batch or len(domains) >= max_domains:
                break
            attempts += 1
            _dedupe_extend(domains, _search_tavily_query(variant, modifier, api_key), max_domains)
        if attempts >= queries_per_batch or len(domains) >= max_domains:
            break

    logger.info(f"Found {len(domains)} domains from Tavily API batch (offset: {offset})")
    return domains


def _search_tavily_query(query: str, modifier: str, api_key: str) -> List[str]:
    global _tavily_disabled_until

    if _bool_env("TAVILY_DISABLE_ON_USAGE_LIMIT", True) and time.time() < _tavily_disabled_until:
        return []

    search_query = f"{query} {modifier}".strip()

    logger.info(f"Searching Tavily API for: '{search_query}' (region: '{modifier or 'global'}')")
    url = "https://api.tavily.com/search"
    
    payload = {
        "api_key": api_key,
        "query": search_query,
        "search_depth": "basic",
        "max_results": _int_env("TAVILY_MAX_RESULTS", 20, 5, 20)
    }
    
    headers = {
        'Content-Type': 'application/json'
    }
    
    try:
        resp = _http.post(url, headers=headers, json=payload, timeout=15)
        if resp.status_code != 200:
            logger.warning(f"Tavily API returned {resp.status_code}: {resp.text}")
            if resp.status_code == 432 and _bool_env("TAVILY_DISABLE_ON_USAGE_LIMIT", True):
                cooldown = _int_env("TAVILY_USAGE_LIMIT_COOLDOWN_SECONDS", 3600, 60, 86400)
                _tavily_disabled_until = time.time() + cooldown
                logger.warning(f"Tavily usage limit reached; pausing Tavily search for {cooldown} seconds.")
            return []
            
        data = resp.json()
        domains = []
        
        # Results array
        results = data.get("results", [])
        for item in results:
            link = item.get("url", "")
            domain = _extract_domain(link)
            if domain and domain not in domains and not _is_junk_domain(domain):
                domains.append(domain)
                
        logger.info(f"Found {len(domains)} domains from Tavily API query (region: '{modifier or 'global'}')")
        return domains
        
    except Exception as e:
        logger.error(f"Tavily API error: {e}")
        return []

def _search_duckduckgo(keywords: str, offset: int = 0) -> List[str]:
    """Fallback: search DuckDuckGo HTML version."""
    if offset > 0:
        logger.info("DuckDuckGo HTML pagination is unreliable, falling back to Bing...")
        return _search_bing(keywords, offset)
        
    logger.info(f"Searching DuckDuckGo for: {keywords}")
    
    headers = {
        "User-Agent": random.choice(USER_AGENTS),
    }
    
    url = f"https://html.duckduckgo.com/html/?q={keywords.replace(' ', '+')}"
    
    try:
        resp = _http.get(url, headers=headers, timeout=15)
        soup = BeautifulSoup(resp.text, "html.parser")
        domains = []
        
        for a_tag in soup.find_all("a", class_="result__a", href=True):
            href = a_tag["href"]
            if "uddg=" in href:
                from urllib.parse import unquote
                actual_url = unquote(href.split("uddg=")[1].split("&")[0])
                domain = _extract_domain(actual_url)
            else:
                domain = _extract_domain(href)
                
            if domain and domain not in domains and not _is_junk_domain(domain):
                domains.append(domain)
        
        logger.info(f"Found {len(domains)} domains from DuckDuckGo")
        
        if not domains:
            logger.info("DuckDuckGo returned no results, trying Bing...")
            return _search_bing(keywords, offset)
        
        return domains[:30]
        
    except Exception as e:
        logger.error(f"DuckDuckGo search error: {e}, trying Bing...")
        return _search_bing(keywords, offset)


def _search_bing(keywords: str, offset: int = 0) -> List[str]:
    """Final fallback: search Bing."""
    logger.info(f"Searching Bing for: {keywords} (offset: {offset})")
    headers = {
        "User-Agent": random.choice(USER_AGENTS),
        "Accept-Language": "en-US,en;q=0.9",
    }
    # setlang=en & setmkt=en-US forces English results
    url = f"https://www.bing.com/search?q={keywords.replace(' ', '+')}&count=50&first={offset}&setlang=en&setmkt=en-US"
    
    try:
        resp = _http.get(url, headers=headers, timeout=15)
        soup = BeautifulSoup(resp.text, "html.parser")
        domains = []
        
        # Method 1: Extract from <cite> tags (most reliable for Bing)
        for cite in soup.find_all("cite"):
            text = cite.get_text()
            # cite text looks like "https://example.com › page" or "example.com/page"
            url_text = text.split("›")[0].strip().split("…")[0].strip()
            if "://" not in url_text:
                url_text = "https://" + url_text
            domain = _extract_domain(url_text)
            if domain and domain not in domains and not _is_junk_domain(domain):
                domains.append(domain)
        
        # Method 2: Extract from organic result links (b_algo)
        for li in soup.find_all("li", class_="b_algo"):
            a_tag = li.find("a", href=True)
            if not a_tag:
                continue
            href = a_tag["href"]
            # Bing uses redirect links: bing.com/ck/a?...&u=a1REAL_URL_HERE&...
            if "bing.com/ck/a" in href and "&u=a1" in href:
                from urllib.parse import unquote
                try:
                    encoded_url = href.split("&u=a1")[1].split("&")[0]
                    actual_url = unquote(encoded_url)
                    domain = _extract_domain(actual_url)
                    if domain and domain not in domains and not _is_junk_domain(domain):
                        domains.append(domain)
                except:
                    pass
            elif href.startswith("http") and "bing.com" not in href and "microsoft.com" not in href:
                domain = _extract_domain(href)
                if domain and domain not in domains and not _is_junk_domain(domain):
                    domains.append(domain)
        
        logger.info(f"Found {len(domains)} domains from Bing")
        return domains[:30]
    except Exception as e:
        logger.error(f"Bing search error: {e}")
        return []


def _extract_domain(url: str) -> str:
    """Extract clean domain from a URL."""
    try:
        if "://" in url:
            domain = url.split("://")[1].split("/")[0]
        else:
            domain = url.split("/")[0]
        domain = domain.replace("www.", "").lower().strip()
        # Basic validation
        if "." in domain and len(domain) > 3:
            return domain
    except:
        pass
    return ""


def _is_non_commercial_domain(domain: str) -> bool:
    """Heuristic check for domains that are clearly not real B2B company websites."""
    # Sub-domains of marketplace platforms (e.g., hopewinn.en.made-in-china.com)
    parts = domain.split(".")
    if len(parts) > 3:
        # 4+ level sub-domains are almost never direct company sites
        return True

    # Domains that are clearly reference/utility sites
    non_commercial_keywords = [
        "dictionary", "wiki", "forum", "support", "docs", "translate",
        "answer", "tutorial", "learn", "study", "lesson", "grammar",
        "chinesewords", "english", "language",
    ]
    root = parts[0].lower() if parts else ""
    return any(kw in root for kw in non_commercial_keywords)


def _is_junk_domain(domain: str) -> bool:
    """Filter out search engines, social media, and other non-company domains."""
    if _is_non_commercial_domain(domain):
        return True

    junk = [
        # Search engines & social media
        "google.", "youtube.", "facebook.", "twitter.", "instagram.",
        "linkedin.", "wikipedia.", "amazon.", "reddit.", "pinterest.",
        "tiktok.", "x.com", "bing.", "yahoo.", "duckduckgo.",
        "quora.", "medium.", "github.", "stackoverflow.", "w3.",
        "schema.org", "googleapis.", "gstatic.", "cloudflare.",
        # Business directories & review sites
        "europages.", "yellowpages.", "yelp.", "trustpilot.",
        "zoominfo.", "crunchbase.", "bloomberg.", "forbes.",
        "glassdoor.", "apollo.io", "dnb.com", "g2.com", "capterra.",
        "kompass.com", "thomasnet.com", "alibaba.", "made-in-china.",
        "globalsources.", "indiamart.", "tradeindia.", "dhgate.",
        "exporthub.", "tradewheel.",
        # Chinese portals & platforms (not B2B targets)
        "baidu.com", "baidu.cn", "baidu.", "sohu.com", "taobao.com", "tmall.com",
        "1688.com", "jd.com", "sina.com", "sina.cn", "qq.com",
        "163.com", "126.com", "csdn.net", "zhihu.com", "bilibili.com",
        "douyin.com", "weibo.com", "tencent.com", "alipay.com",
        "aliyun.com", "huawei.com", "xiaomi.com", "pinduoduo.com",
        "meituan.com", "douban.com", "ifeng.com", "hexun.com",
        "eastmoney.com", "cifnews.com", "hktdc.com",
        "toutiao.com", "bytedance.com", "36kr.com", "jianshu.com",
        "chinesewords.org",
        # Japanese reference & language sites
        "weblio.jp", "ejje.weblio.jp", "kotobank.jp", "ei-navi.jp",
        "talking-english.net", "alc.co.jp", "goo.ne.jp", "jisho.org",
        # Dictionaries, language/reference pages, support/docs, forums, news
        "dictionary.", "cambridge.org", "oxfordlearnersdictionaries.",
        "merriam-webster.", "collinsdictionary.", "thefreedictionary.",
        "wordreference.", "wiktionary.", "linguee.", "deepl.", "reverso.",
        "support.microsoft.", "learn.microsoft.", "docs.", "developer.",
        "devforum.", "forum.", "community.", "help.", "stackoverflow.",
        "bbc.co.", "cnn.", "reuters.", "nytimes.", "theguardian.",
        "washingtonpost.", "ft.com", "wsj.", "news.",
        # SaaS / non-target tools
        "statista.com", "gotowebinar.com", "eventbrite.com",
        "zoom.us", "webex.com", "mailchimp.com", "hubspot.com",
        "salesforce.com", "shopify.com", "wordpress.com", "wix.com",
        "squarespace.com", "godaddy.com",
        # Marketplaces (sub-domains)
        "goldsupplier.com", "sparkshot.com",
        # Government & education
        ".gov", ".edu", ".mil",
    ]
    return any(j in domain for j in junk)


def is_domain_quality_candidate(domain: str, keywords: str = "") -> bool:
    """Fast pre-Snov filter for domains worth enrichment."""
    if not domain or _is_junk_domain(domain):
        return False

    labels = domain.split(".")
    if len(labels) >= 4 and any(label in {"api", "dev", "docs", "support", "help", "m", "en"} for label in labels[:-2]):
        return False

    return True
