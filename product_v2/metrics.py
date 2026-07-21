"""Low-cardinality Prometheus metrics without a runtime dependency."""
from __future__ import annotations

from collections import defaultdict
from threading import Lock
import time

from fastapi import Response
from sqlalchemy import func

from product_v2 import models
from product_v2.enums import ChannelAccountHealth, MessageEventType, WorkerType
from product_v2.services.domain import as_utc, utcnow
from runtime_config import read_flag


_lock = Lock()
_requests: dict[tuple[str, str, int], int] = defaultdict(int)
_duration_sum: dict[tuple[str, str], float] = defaultdict(float)
_duration_count: dict[tuple[str, str], int] = defaultdict(int)


def _escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace("\n", "\\n").replace('"', '\\"')


async def observe_http(request, call_next):
    started = time.monotonic()
    status_code = 500
    try:
        response = await call_next(request)
        status_code = response.status_code
        return response
    finally:
        route = request.scope.get("route")
        template = getattr(route, "path", None) or "unmatched"
        method = request.method
        elapsed = time.monotonic() - started
        with _lock:
            _requests[(method, template, status_code)] += 1
            _duration_sum[(method, template)] += elapsed
            _duration_count[(method, template)] += 1


def prometheus_metrics(db=None) -> Response:
    lines = [
        "# HELP autoleadgen_http_requests_total HTTP requests by route and status.",
        "# TYPE autoleadgen_http_requests_total counter",
    ]
    with _lock:
        request_items = list(_requests.items())
        duration_sums = list(_duration_sum.items())
        duration_counts = list(_duration_count.items())
    for (method, route, status), value in sorted(request_items):
        lines.append(
            'autoleadgen_http_requests_total{method="%s",route="%s",status="%s"} %d'
            % (_escape(method), _escape(route), status, value)
        )
    lines.extend(
        [
            "# HELP autoleadgen_http_request_duration_seconds HTTP request duration.",
            "# TYPE autoleadgen_http_request_duration_seconds summary",
        ]
    )
    for (method, route), value in sorted(duration_sums):
        labels = 'method="%s",route="%s"' % (_escape(method), _escape(route))
        lines.append(
            f"autoleadgen_http_request_duration_seconds_sum{{{labels}}} {value:.9f}"
        )
    for (method, route), value in sorted(duration_counts):
        labels = 'method="%s",route="%s"' % (_escape(method), _escape(route))
        lines.append(
            f"autoleadgen_http_request_duration_seconds_count{{{labels}}} {value}"
        )
    collection_success = 0
    if db is not None:
        try:
            for status, count in db.query(
                models.OutreachAttempt.status,
                func.count(models.OutreachAttempt.id),
            ).group_by(models.OutreachAttempt.status):
                value = status.value if hasattr(status, "value") else str(status)
                lines.append(
                    f'autoleadgen_outreach_attempts{{status="{_escape(value)}"}} {int(count)}'
                )
            for status, count in db.query(
                models.ProviderCostEvent.status,
                func.count(models.ProviderCostEvent.id),
            ).group_by(models.ProviderCostEvent.status):
                value = status.value if hasattr(status, "value") else str(status)
                lines.append(
                    f'autoleadgen_provider_cost_events{{status="{_escape(value)}"}} {int(count)}'
                )
            event_counts = {
                (event_type.value if hasattr(event_type, "value") else str(event_type)): int(count)
                for event_type, count in db.query(
                    models.MessageEvent.event_type,
                    func.count(models.MessageEvent.id),
                ).group_by(models.MessageEvent.event_type)
            }
            lines.extend(
                [
                    "# HELP autoleadgen_message_events_total Immutable Provider/message events by type.",
                    "# TYPE autoleadgen_message_events_total counter",
                ]
            )
            for event_type in MessageEventType:
                lines.append(
                    'autoleadgen_message_events_total{event_type="%s"} %d'
                    % (_escape(event_type.value), event_counts.get(event_type.value, 0))
                )
            for worker_type in (WorkerType.OUTBOUND, WorkerType.INBOX):
                seen = db.query(func.max(models.WorkerHeartbeat.last_seen_at)).filter(
                    models.WorkerHeartbeat.worker_type == worker_type
                ).scalar()
                age = (
                    max(0.0, (utcnow() - as_utc(seen)).total_seconds())
                    if seen
                    else 1_000_000_000.0
                )
                lines.append(
                    f'autoleadgen_worker_heartbeat_age_seconds{{worker_type="{worker_type.value}"}} {age:.3f}'
                )
            active_locks = db.query(func.count(models.SafetyLock.id)).filter(
                models.SafetyLock.active.is_(True)
            ).scalar()
            unhealthy_accounts = db.query(func.count(models.ChannelAccount.id)).filter(
                models.ChannelAccount.enabled.is_(True),
                models.ChannelAccount.archived_at.is_(None),
                models.ChannelAccount.health_status != ChannelAccountHealth.HEALTHY,
            ).scalar()
            lines.append(f"autoleadgen_active_safety_locks {int(active_locks or 0)}")
            lines.append(
                f"autoleadgen_unhealthy_enabled_channel_accounts {int(unhealthy_accounts or 0)}"
            )
            collection_success = 1
        except Exception:
            db.rollback()
    try:
        hard_pause = 1 if read_flag("OUTBOUND_HARD_PAUSE", default=True) else 0
    except Exception:
        hard_pause = 1
    lines.append(f"autoleadgen_outbound_hard_pause {hard_pause}")
    lines.append(f"autoleadgen_metrics_collection_success {collection_success}")
    return Response("\n".join(lines) + "\n", media_type="text/plain; version=0.0.4")
