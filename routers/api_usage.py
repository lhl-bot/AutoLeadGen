import os
import re
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout, as_completed
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

import requests
from fastapi import APIRouter, Depends
from sqlalchemy import func
from sqlalchemy.orm import Session

import models
from database import get_db
from services.apollo_client import ApolloClient
from services.auth import require_admin
from services.leadcontact_client import LeadContactClient

router = APIRouter(prefix="/api/api-usage", tags=["api_usage"])

WINDOW_DAYS = 30
REALTIME_TIMEOUT_SECONDS = 12
UNKNOWN_BALANCE = "Not exposed by provider API"


def _env_configured(*names: str) -> bool:
    return any(bool(os.environ.get(name, "").strip()) for name in names)


def _safe_error(value: Any) -> str:
    text = str(value or "Unknown error")
    text = re.sub(r"(Bearer\s+)[A-Za-z0-9._\-+/=]+", r"\1***", text)
    text = re.sub(r"(sk-[A-Za-z0-9._\-+/=]+)", "***", text)
    return text[:180]


def _to_number(value: Any) -> Optional[float]:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _format_number(value: Any) -> str:
    number = _to_number(value)
    if number is None:
        return "Unavailable"
    if number.is_integer():
        return f"{int(number):,}"
    return f"{number:,.2f}"


def _count(query: Any) -> int:
    return int(query.scalar() or 0)


def _provider(
    *,
    key: str,
    name: str,
    category: str,
    configured: bool,
    status: str,
    usage_30d: int,
    usage_label: str,
    balance_label: Optional[str] = None,
    balance_value: Optional[float] = None,
    balance_unit: Optional[str] = None,
    details: Optional[Dict[str, Any]] = None,
    error: Optional[str] = None,
    docs_url: Optional[str] = None,
) -> Dict[str, Any]:
    if not configured:
        status = "missing"
        balance_label = "Not configured"
        balance_value = None
        error = None
    return {
        "key": key,
        "name": name,
        "category": category,
        "configured": configured,
        "status": status,
        "balance_label": balance_label or "Unavailable",
        "balance_value": balance_value,
        "balance_unit": balance_unit,
        "usage_30d": int(usage_30d or 0),
        "usage_label": usage_label,
        "details": details or {},
        "error": _safe_error(error) if error else None,
        "docs_url": docs_url,
    }


def _group_count(rows: List[Any]) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    for key, value in rows:
        normalized = (key or "unknown").strip().lower()
        counts[normalized] = int(value or 0)
    return counts


def _local_usage(db: Session, since: datetime) -> Dict[str, Any]:
    lead_source_rows = db.query(
        models.Lead.source_channel,
        func.count(models.Lead.id),
    ).filter(
        models.Lead.created_at >= since,
    ).group_by(models.Lead.source_channel).all()

    message_rows = db.query(
        models.MessageLog.channel,
        func.count(models.MessageLog.id),
    ).filter(
        models.MessageLog.direction == "outbound",
        models.MessageLog.sent_at >= since,
    ).group_by(models.MessageLog.channel).all()

    source_counts = _group_count(lead_source_rows)
    message_counts = _group_count(message_rows)
    search_source_keys = [
        "web", "search", "customs", "competitors", "trade_shows",
        "directories", "retail", "social",
    ]
    search_leads = sum(source_counts.get(key, 0) for key in search_source_keys)
    omnichannel_messages = message_counts.get("linkedin", 0) + message_counts.get("whatsapp", 0)

    email_outbound = _count(db.query(func.count(models.EmailLog.id)).filter(
        models.EmailLog.direction == "outbound",
        models.EmailLog.sent_at >= since,
    ))
    processed_domains = _count(db.query(func.count(models.ProcessedDomain.id)).filter(
        models.ProcessedDomain.created_at >= since,
    ))
    lead_briefs = _count(db.query(func.count(models.LeadBrief.id)).filter(
        models.LeadBrief.created_at >= since,
    ))
    ai_drafts = _count(db.query(func.count(models.Lead.id)).filter(
        models.Lead.ai_draft.isnot(None),
        models.Lead.updated_at >= since,
    ))
    snovio_enriched_leads = _count(db.query(func.count(models.Lead.id)).filter(
        models.Lead.created_at >= since,
        models.Lead.data_sources.ilike("%snovio%"),
    ))
    snovio_audit_events = _count(db.query(func.count(models.SnovioUsageEvent.id)).filter(
        models.SnovioUsageEvent.created_at >= since,
    ))
    snovio_billable_events = _count(db.query(func.count(models.SnovioUsageEvent.id)).filter(
        models.SnovioUsageEvent.created_at >= since,
        models.SnovioUsageEvent.estimated_credits > 0,
    ))
    snovio_estimated_credits = int(db.query(
        func.coalesce(func.sum(models.SnovioUsageEvent.estimated_credits), 0)
    ).filter(
        models.SnovioUsageEvent.created_at >= since,
    ).scalar() or 0)
    snovio_endpoint_rows = db.query(
        models.SnovioUsageEvent.endpoint,
        func.count(models.SnovioUsageEvent.id),
    ).filter(
        models.SnovioUsageEvent.created_at >= since,
    ).group_by(models.SnovioUsageEvent.endpoint).all()
    snovio_endpoint_counts = _group_count(snovio_endpoint_rows)
    chat_messages = _count(db.query(func.count(models.ChatMessage.id)).filter(
        models.ChatMessage.role == "user",
        models.ChatMessage.created_at >= since,
    ))

    return {
        "source_counts": source_counts,
        "message_counts": message_counts,
        "email_outbound": email_outbound,
        "processed_domains": processed_domains,
        "search_leads": search_leads,
        "snovio_enriched_leads": snovio_enriched_leads,
        "snovio_audit_events": snovio_audit_events,
        "snovio_billable_events": snovio_billable_events,
        "snovio_estimated_credits": snovio_estimated_credits,
        "snovio_endpoint_counts": snovio_endpoint_counts,
        "lead_briefs": lead_briefs,
        "ai_drafts": ai_drafts,
        "chat_messages": chat_messages,
        "omnichannel_messages": omnichannel_messages,
        "email_accounts": _count(db.query(func.count(models.EmailAccount.id))),
        "channel_accounts": _count(db.query(func.count(models.ChannelAccount.id))),
        "connected_channel_accounts": _count(db.query(func.count(models.ChannelAccount.id)).filter(
            models.ChannelAccount.status == "OK",
        )),
    }


def _leadcontact_provider(local: Dict[str, Any]) -> Dict[str, Any]:
    configured = _env_configured("LEADCONTACT_API_KEY")
    usage = local["source_counts"].get("leadcontact", 0)
    if not configured:
        return _provider(
            key="leadcontact",
            name="LeadContact",
            category="Contact data",
            configured=False,
            status="missing",
            usage_30d=usage,
            usage_label=f"{usage:,} LeadContact leads",
        )

    payload = LeadContactClient(os.environ["LEADCONTACT_API_KEY"]).get_credit_details()
    if payload.get("error"):
        return _provider(
            key="leadcontact",
            name="LeadContact",
            category="Contact data",
            configured=True,
            status="warning",
            usage_30d=usage,
            usage_label=f"{usage:,} LeadContact leads",
            balance_label="Balance unavailable",
            error=payload.get("error"),
        )

    balance = _to_number((payload.get("data") or {}).get("remainingPoints"))
    return _provider(
        key="leadcontact",
        name="LeadContact",
        category="Contact data",
        configured=True,
        status="ok" if balance is not None else "warning",
        usage_30d=usage,
        usage_label=f"{usage:,} LeadContact leads",
        balance_label=f"{_format_number(balance)} credits" if balance is not None else "Balance unavailable",
        balance_value=balance,
        balance_unit="credits",
        details={"endpoint": "GET /credits"},
    )


def _snovio_provider(local: Dict[str, Any]) -> Dict[str, Any]:
    configured = bool(
        os.environ.get("SNOVIO_CLIENT_ID", "").strip()
        and os.environ.get("SNOVIO_CLIENT_SECRET", "").strip()
    )
    audit_events = local.get("snovio_audit_events", 0)
    enriched_leads = local.get("snovio_enriched_leads", 0)
    estimated_credits = local.get("snovio_estimated_credits", 0)
    billable_events = local.get("snovio_billable_events", 0)
    endpoint_counts = local.get("snovio_endpoint_counts", {})
    usage = audit_events or enriched_leads
    usage_label = (
        f"{audit_events:,} audited Snov.io calls"
        if audit_events
        else f"{enriched_leads:,} Snov.io-enriched leads"
    )
    if not configured:
        return _provider(
            key="snovio",
            name="Snov.io",
            category="Email enrichment",
            configured=False,
            status="missing",
            usage_30d=usage,
            usage_label=usage_label,
        )

    try:
        auth_resp = requests.post(
            "https://api.snov.io/v1/oauth/access_token",
            data={
                "grant_type": "client_credentials",
                "client_id": os.environ.get("SNOVIO_CLIENT_ID", ""),
                "client_secret": os.environ.get("SNOVIO_CLIENT_SECRET", ""),
            },
            headers={"Accept": "application/json"},
            timeout=5,
        )
        if auth_resp.status_code != 200:
            raise RuntimeError(f"Auth HTTP {auth_resp.status_code}")
        token = auth_resp.json().get("access_token")
        if not token:
            raise RuntimeError("Snov.io did not return an access token")
        balance_resp = requests.get(
            "https://api.snov.io/v1/get-balance",
            headers={"Accept": "application/json"},
            params={"access_token": token},
            timeout=5,
        )
        if balance_resp.status_code != 200:
            raise RuntimeError(f"Balance HTTP {balance_resp.status_code}")
        payload = balance_resp.json()
    except Exception as e:
        return _provider(
            key="snovio",
            name="Snov.io",
            category="Email enrichment",
            configured=True,
            status="warning",
            usage_30d=usage,
            usage_label=usage_label,
            balance_label="Balance unavailable",
            error=e,
            docs_url="https://snov.io/api",
        )
    if not payload.get("success"):
        return _provider(
            key="snovio",
            name="Snov.io",
            category="Email enrichment",
            configured=True,
            status="warning",
            usage_30d=usage,
            usage_label=usage_label,
            balance_label="Balance unavailable",
            error=payload.get("message") or payload.get("error") or "Snov.io balance query failed",
            docs_url="https://snov.io/api",
        )

    data = payload.get("data") or {}
    balance = _to_number(data.get("balance"))
    return _provider(
        key="snovio",
        name="Snov.io",
        category="Email enrichment",
        configured=True,
        status="ok" if balance is not None else "warning",
        usage_30d=usage,
        usage_label=usage_label,
        balance_label=f"{_format_number(balance)} credits" if balance is not None else "Balance unavailable",
        balance_value=balance,
        balance_unit="credits",
        details={
            "audited_calls_30d": audit_events,
            "estimated_credits_30d": estimated_credits,
            "billable_events_30d": billable_events,
            "snovio_enriched_leads_30d": enriched_leads,
            "prospect_email_calls": endpoint_counts.get("domain-search/prospect-email", 0),
            "unique_recipients_used": data.get("unique_recipients_used") or data.get("recipients_used"),
            "limit_resets_in_days": data.get("limit_resets_in"),
            "subscription_expires_in_days": data.get("expires_in"),
        },
        docs_url="https://snov.io/api",
    )


def _apollo_usage_summary(payload: Dict[str, Any]) -> Dict[str, int]:
    consumed = 0
    limit = 0
    left = 0
    endpoints = 0
    for value in payload.values():
        if not isinstance(value, dict):
            continue
        day = value.get("day")
        if not isinstance(day, dict):
            continue
        endpoints += 1
        consumed += int(day.get("consumed") or 0)
        limit += int(day.get("limit") or 0)
        left += int(day.get("left_over") or 0)
    return {
        "day_consumed": consumed,
        "day_limit": limit,
        "day_left": left,
        "endpoint_count": endpoints,
    }


def _apollo_provider(local: Dict[str, Any]) -> Dict[str, Any]:
    configured = _env_configured("APOLLO_API_KEY")
    usage = local["source_counts"].get("apollo", 0)
    if not configured:
        return _provider(
            key="apollo",
            name="Apollo",
            category="Contact data",
            configured=False,
            status="missing",
            usage_30d=usage,
            usage_label=f"{usage:,} Apollo leads",
        )

    payload = ApolloClient(os.environ["APOLLO_API_KEY"]).get_usage_stats() or {}
    if payload.get("error"):
        message = payload.get("error")
        if payload.get("status_code") == 403:
            message = "Apollo usage stats require a master API key"
        return _provider(
            key="apollo",
            name="Apollo",
            category="Contact data",
            configured=True,
            status="warning",
            usage_30d=usage,
            usage_label=f"{usage:,} Apollo leads",
            balance_label="Usage stats unavailable",
            error=message,
            docs_url="https://docs.apollo.io/reference/view-api-usage-stats",
        )

    summary = _apollo_usage_summary(payload)
    return _provider(
        key="apollo",
        name="Apollo",
        category="Contact data",
        configured=True,
        status="ok",
        usage_30d=usage,
        usage_label=f"{usage:,} Apollo leads",
        balance_label=f"{summary['day_left']:,} daily quota left",
        balance_value=float(summary["day_left"]),
        balance_unit="daily_quota",
        details=summary,
        docs_url="https://docs.apollo.io/reference/view-api-usage-stats",
    )


def _tavily_provider(local: Dict[str, Any]) -> Dict[str, Any]:
    configured = _env_configured("TAVILY_API_KEY")
    usage = local["processed_domains"] + local["search_leads"]
    if not configured:
        return _provider(
            key="tavily",
            name="Tavily",
            category="Search",
            configured=False,
            status="missing",
            usage_30d=usage,
            usage_label=f"{usage:,} local search events",
        )

    try:
        resp = requests.get(
            "https://api.tavily.com/usage",
            headers={"Authorization": f"Bearer {os.environ['TAVILY_API_KEY']}"},
            timeout=5,
        )
        if resp.status_code != 200:
            return _provider(
                key="tavily",
                name="Tavily",
                category="Search",
                configured=True,
                status="warning",
                usage_30d=usage,
                usage_label=f"{usage:,} local search events",
                balance_label="Usage unavailable",
                error=f"HTTP {resp.status_code}",
                docs_url="https://docs.tavily.com/documentation/api-reference/endpoint/usage",
            )
        payload = resp.json()
    except Exception as e:
        return _provider(
            key="tavily",
            name="Tavily",
            category="Search",
            configured=True,
            status="warning",
            usage_30d=usage,
            usage_label=f"{usage:,} local search events",
            balance_label="Usage unavailable",
            error=e,
            docs_url="https://docs.tavily.com/documentation/api-reference/endpoint/usage",
        )

    account = payload.get("account") or {}
    key = payload.get("key") or {}
    plan_limit = _to_number(account.get("plan_limit") or key.get("limit"))
    plan_usage = _to_number(account.get("plan_usage") or key.get("usage"))
    remaining = plan_limit - plan_usage if plan_limit is not None and plan_usage is not None else None
    return _provider(
        key="tavily",
        name="Tavily",
        category="Search",
        configured=True,
        status="ok",
        usage_30d=usage,
        usage_label=f"{usage:,} local search events",
        balance_label=f"{_format_number(remaining)} credits remaining" if remaining is not None else "Usage available",
        balance_value=remaining,
        balance_unit="credits",
        details={
            "current_plan": account.get("current_plan"),
            "plan_usage": account.get("plan_usage"),
            "plan_limit": account.get("plan_limit"),
            "paygo_usage": account.get("paygo_usage"),
            "paygo_limit": account.get("paygo_limit"),
            "key_usage": key.get("usage"),
            "key_limit": key.get("limit"),
        },
        docs_url="https://docs.tavily.com/documentation/api-reference/endpoint/usage",
    )


def _bocha_provider(local: Dict[str, Any]) -> Dict[str, Any]:
    configured = _env_configured("BOCHA_API_KEY")
    usage = local["processed_domains"] + local["search_leads"]
    return _provider(
        key="bocha",
        name="Bocha",
        category="Search",
        configured=configured,
        status="ok" if configured else "missing",
        usage_30d=usage,
        usage_label=f"{usage:,} local search events",
        balance_label=UNKNOWN_BALANCE if configured else None,
        details={
            "freshness": os.environ.get("BOCHA_SEARCH_FRESHNESS", "noLimit") if configured else None,
            "summary_enabled": os.environ.get("BOCHA_SEARCH_SUMMARY", "false") if configured else None,
        },
    )


def _unipile_provider(local: Dict[str, Any]) -> Dict[str, Any]:
    configured = _env_configured("UNIPILE_API_KEY", "UNIPILE_DSN")
    connected = local["connected_channel_accounts"]
    total = local["channel_accounts"]
    status = "ok" if configured and connected > 0 else "warning" if configured else "missing"
    return _provider(
        key="unipile",
        name="Unipile",
        category="Omnichannel",
        configured=configured,
        status=status,
        usage_30d=local["omnichannel_messages"],
        usage_label=f"{local['omnichannel_messages']:,} LinkedIn/WhatsApp messages",
        balance_label=UNKNOWN_BALANCE if configured else None,
        details={
            "connected_accounts": connected,
            "total_accounts": total,
            "dsn_configured": bool(os.environ.get("UNIPILE_DSN", "").strip()),
        },
    )


def _llm_provider(local: Dict[str, Any]) -> Dict[str, Any]:
    configured = _env_configured("LLM_API_KEY", "MINIMAX_API_KEY")
    usage = local["lead_briefs"] + local["ai_drafts"] + local["chat_messages"]
    base_url = os.environ.get("LLM_BASE_URL", "https://api.minimaxi.com/v1/chat/completions")
    return _provider(
        key="llm",
        name="LLM Provider",
        category="AI generation",
        configured=configured,
        status="ok" if configured else "missing",
        usage_30d=usage,
        usage_label=f"{usage:,} local AI events",
        balance_label=UNKNOWN_BALANCE if configured else None,
        details={
            "model": os.environ.get("LLM_MODEL", "MiniMax-M2.7-highspeed") if configured else None,
            "base_url_host": re.sub(r"^https?://", "", base_url).split("/")[0] if configured else None,
            "lead_briefs": local["lead_briefs"],
            "ai_drafts": local["ai_drafts"],
            "chat_messages": local["chat_messages"],
        },
    )


def _smtp_provider(local: Dict[str, Any]) -> Dict[str, Any]:
    configured = local["email_accounts"] > 0
    return _provider(
        key="smtp",
        name="Email delivery",
        category="Delivery",
        configured=configured,
        status="ok" if configured else "missing",
        usage_30d=local["email_outbound"],
        usage_label=f"{local['email_outbound']:,} outbound emails",
        balance_label=UNKNOWN_BALANCE if configured else None,
        details={"configured_accounts": local["email_accounts"]},
    )


def _realtime_fallback_provider(key: str, local: Dict[str, Any], error: Any) -> Dict[str, Any]:
    usage_by_key = {
        "leadcontact": local["source_counts"].get("leadcontact", 0),
        "snovio": local.get("snovio_audit_events", 0) or local.get("snovio_enriched_leads", 0),
        "apollo": local["source_counts"].get("apollo", 0),
        "tavily": local["processed_domains"] + local["search_leads"],
    }
    label_by_key = {
        "leadcontact": f"{usage_by_key['leadcontact']:,} LeadContact leads",
        "snovio": (
            f"{local.get('snovio_audit_events', 0):,} audited Snov.io calls"
            if local.get("snovio_audit_events", 0)
            else f"{local.get('snovio_enriched_leads', 0):,} Snov.io-enriched leads"
        ),
        "apollo": f"{usage_by_key['apollo']:,} Apollo leads",
        "tavily": f"{usage_by_key['tavily']:,} local search events",
    }
    meta = {
        "leadcontact": ("LeadContact", "Contact data", _env_configured("LEADCONTACT_API_KEY")),
        "snovio": (
            "Snov.io",
            "Email enrichment",
            bool(
                os.environ.get("SNOVIO_CLIENT_ID", "").strip()
                and os.environ.get("SNOVIO_CLIENT_SECRET", "").strip()
            ),
        ),
        "apollo": ("Apollo", "Contact data", _env_configured("APOLLO_API_KEY")),
        "tavily": ("Tavily", "Search", _env_configured("TAVILY_API_KEY")),
    }
    name, category, configured = meta.get(key, (key.title(), "External API", True))
    return _provider(
        key=key,
        name=name,
        category=category,
        configured=configured,
        status="warning" if configured else "missing",
        usage_30d=usage_by_key.get(key, 0),
        usage_label=label_by_key.get(key, "0 local events"),
        balance_label="Query timed out" if configured else None,
        error=error,
    )


@router.get("/summary")
def api_usage_summary(
    db: Session = Depends(get_db),
    user: models.User = Depends(require_admin),
):
    """Return paid API configuration, balance, and local usage for admins."""
    now = datetime.now(timezone.utc)
    since = now - timedelta(days=WINDOW_DAYS)
    local = _local_usage(db, since)

    realtime_builders = [
        ("leadcontact", _leadcontact_provider),
        ("snovio", _snovio_provider),
        ("apollo", _apollo_provider),
        ("tavily", _tavily_provider),
    ]
    realtime_results: Dict[str, Dict[str, Any]] = {}
    executor = ThreadPoolExecutor(max_workers=len(realtime_builders))
    future_map = {
        executor.submit(builder, local): key
        for key, builder in realtime_builders
    }
    try:
        try:
            for future in as_completed(future_map, timeout=REALTIME_TIMEOUT_SECONDS):
                key = future_map[future]
                try:
                    realtime_results[key] = future.result()
                except Exception as e:
                    realtime_results[key] = _realtime_fallback_provider(key, local, e)
        except FuturesTimeout:
            pass
    finally:
        missing_keys = [key for key, _ in realtime_builders if key not in realtime_results]
        for key in missing_keys:
            realtime_results[key] = _realtime_fallback_provider(
                key,
                local,
                f"Timed out after {REALTIME_TIMEOUT_SECONDS}s",
            )
        executor.shutdown(wait=False, cancel_futures=True)

    providers = [
        realtime_results["leadcontact"],
        realtime_results["snovio"],
        realtime_results["apollo"],
        realtime_results["tavily"],
        _bocha_provider(local),
        _unipile_provider(local),
        _llm_provider(local),
        _smtp_provider(local),
    ]

    local_breakdown = [
        {"key": "email_outbound", "label": "Outbound emails", "count": local["email_outbound"]},
        {"key": "omnichannel_messages", "label": "LinkedIn / WhatsApp messages", "count": local["omnichannel_messages"]},
        {"key": "processed_domains", "label": "Processed domains", "count": local["processed_domains"]},
        {"key": "search_leads", "label": "Search-sourced leads", "count": local["search_leads"]},
        {"key": "snovio_audit_events", "label": "Snov.io audited calls", "count": local["snovio_audit_events"]},
        {"key": "snovio_estimated_credits", "label": "Snov.io estimated credits", "count": local["snovio_estimated_credits"]},
        {"key": "lead_briefs", "label": "AI research briefs", "count": local["lead_briefs"]},
        {"key": "ai_drafts", "label": "AI email drafts", "count": local["ai_drafts"]},
        {"key": "chat_messages", "label": "AI chat prompts", "count": local["chat_messages"]},
    ]

    return {
        "updated_at": now.isoformat(),
        "window_days": WINDOW_DAYS,
        "totals": {
            "configured_providers": sum(1 for item in providers if item["configured"]),
            "ok_providers": sum(1 for item in providers if item["status"] == "ok"),
            "warning_providers": sum(1 for item in providers if item["status"] == "warning"),
            "known_balance_providers": sum(1 for item in providers if item["balance_value"] is not None),
            "local_events_30d": sum(item["count"] for item in local_breakdown),
        },
        "providers": providers,
        "local_usage": local_breakdown,
    }
