from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.orm import Session
from sqlalchemy import text
import os
import httpx
import time
from datetime import datetime, timezone, timedelta

import models
from database import get_db
from services.auth import get_current_user

router = APIRouter(prefix="/api/health", tags=["health"])
_health_cache: dict[str, tuple[float, dict]] = {}
_health_cache_ttl = float(os.environ.get("HEALTH_CACHE_TTL_SECONDS", "120"))


@router.get("/status")
async def system_health(
    request: Request,
    external: bool = Query(False, description="Also probe external provider APIs."),
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
):
    """Return real health status of system components."""
    cache_key = f"health:{user.id}:{int(bool(user.is_admin))}:{int(external)}"
    cached = _health_cache.get(cache_key)
    if cached and cached[0] > time.monotonic():
        return cached[1]

    # DB connectivity
    try:
        db.execute(text("SELECT 1"))
        db_ok = True
    except Exception:
        db_ok = False

    # Keep remote DB work to one round trip for the overview page.
    recent_cutoff = datetime.now(timezone.utc) - timedelta(minutes=30)
    counts = db.execute(text("""
        SELECT
            (SELECT COUNT(*) FROM workflows WHERE status = 'active') AS active_workflows,
            (SELECT COUNT(*) FROM email_logs WHERE sent_at >= :recent_cutoff) AS recent_emails,
            (SELECT COUNT(*) FROM channel_accounts WHERE status = 'OK') AS unipile_connected,
            (SELECT COUNT(*) FROM email_accounts) AS total_email_accounts
    """), {"recent_cutoff": recent_cutoff}).mappings().one()
    active_workflows = int(counts["active_workflows"] or 0)
    recent_emails = int(counts["recent_emails"] or 0)
    unipile_connected = int(counts["unipile_connected"] or 0)
    total_email_accounts = int(counts["total_email_accounts"] or 0)

    # Check Unipile API health via actual HTTP call
    unipile_api_key = os.environ.get("UNIPILE_API_KEY", "").strip()
    unipile_dsn = os.environ.get("UNIPILE_DSN", "").strip()
    unipile_status = "offline"
    if unipile_connected > 0:
        unipile_status = "online"
    if external and unipile_api_key and unipile_dsn:
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get(
                    f"{unipile_dsn}/api/v1/accounts",
                    headers={"X-API-KEY": unipile_api_key, "accept": "application/json"},
                    timeout=5.0
                )
                if resp.status_code == 200:
                    unipile_status = "online"
        except Exception:
            unipile_status = "offline"

    # Check background thread status
    background_threads = getattr(request.app.state, "background_threads", [])
    thread_map = {t.name: t for t in background_threads}

    thread_status = {}
    for name, env_var in [
        ("outbound-engine", "ENABLE_BACKGROUND_WORKERS"),
        ("prospecting-engine", "ENABLE_PROSPECTING_WORKER"),
        ("inbox-monitor", "ENABLE_INBOX_MONITOR_WORKER"),
        ("omnichannel-engine", "ENABLE_OMNICHANNEL_WORKER")
    ]:
        env_val = os.environ.get(env_var, "").lower()
        is_enabled = env_val in ("true", "1", "yes")

        thread_obj = thread_map.get(name)
        if is_enabled:
            if thread_obj and thread_obj.is_alive():
                thread_status[name.replace("-", "_")] = "running"
            else:
                thread_status[name.replace("-", "_")] = "stopped"
        else:
            thread_status[name.replace("-", "_")] = "disabled"

    # Determine outbound_engine status based on thread state
    outbound_thread_state = thread_status.get("outbound_engine", "disabled")
    if outbound_thread_state == "stopped":
        outbound_status = "error"
    elif outbound_thread_state == "disabled":
        outbound_status = "inactive"
    else:
        outbound_status = "active" if active_workflows > 0 and recent_emails > 0 else ("idle" if active_workflows > 0 else "inactive")

    has_active_email = total_email_accounts > 0

    # Check LLM API key
    llm_key_set = bool(os.environ.get("LLM_API_KEY"))

    result = {
        "database": "online" if db_ok else "error",
        "outbound_engine": outbound_status,
        "outbound_engine_thread": thread_status.get("outbound_engine"),
        "prospecting_engine_thread": thread_status.get("prospecting_engine"),
        "inbox_monitor_thread": thread_status.get("inbox_monitor"),
        "omnichannel_engine_thread": thread_status.get("omnichannel_engine"),
        "unipile_status": unipile_status,
        "active_workflows": active_workflows,
        "recent_emails_30m": recent_emails,
        "unipile_accounts": unipile_connected,
        "email_accounts": total_email_accounts,
        "has_active_email": has_active_email,
        "llm_api_configured": llm_key_set,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    if _health_cache_ttl > 0:
        _health_cache[cache_key] = (time.monotonic() + _health_cache_ttl, result)
    return result
