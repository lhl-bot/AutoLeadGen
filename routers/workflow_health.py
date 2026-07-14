"""Read-only workflow health/observability endpoint (P0-2).

Surfaces, for one workflow, the lead funnel, where leads are getting parked, the
external-provider configuration status, and recent activity — so "why isn't it
working" is a glance instead of a log-diving session.
"""
import os
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

import models
from database import get_db
from services.auth import get_current_user
from services.email_preflight import VERIFIED_EMAIL_STATUSES

router = APIRouter(prefix="/api/workflows", tags=["workflow_health"])

# Lead statuses grouped into a readable funnel + "stuck" buckets.
_FUNNEL_ORDER = ["found", "drafted", "sent", "replied"]
_STUCK_REASONS = {
    "needs_email": "无可用邮箱(off-target 跳过 / 查无邮箱)",
    "low_score": "匹配度过低(fit 闸拦截)",
    "invalid_email": "邮箱无效",
    "send_failed": "发送失败",
    "rejected": "已拒绝 / 加黑名单",
    "unsubscribed": "已退订",
    "bounced": "退信",
}


def _provider_status(name: str, *env_keys: str) -> str:
    return "configured" if all(os.environ.get(k, "").strip() for k in env_keys) else "missing"


def _leadcontact_remaining_points() -> Optional[int]:
    api_key = os.environ.get("LEADCONTACT_API_KEY", "").strip()
    if not api_key:
        return None
    try:
        from services.leadcontact_client import LeadContactClient

        payload = LeadContactClient(api_key).get_credit_details()
        data = payload.get("data", payload) if isinstance(payload, dict) else {}
        points = data.get("remainingPoints") if isinstance(data, dict) else None
        return int(points) if points is not None else None
    except Exception:
        return None


@router.get("/{workflow_id}/health")
def workflow_health(workflow_id: int, db: Session = Depends(get_db), user: models.User = Depends(get_current_user)):
    wf = db.query(models.Workflow).filter(models.Workflow.id == workflow_id).first()
    if not wf or (not user.is_admin and wf.user_id != user.id):
        raise HTTPException(status_code=404, detail="Workflow not found")

    lead_q = db.query(models.Lead).filter(models.Lead.workflow_id == workflow_id)

    # Status distribution
    rows = (
        db.query(models.Lead.status, func.count(models.Lead.id))
        .filter(models.Lead.workflow_id == workflow_id)
        .group_by(models.Lead.status)
        .all()
    )
    by_status = {s: int(c) for s, c in rows}
    total = sum(by_status.values())
    with_email = lead_q.filter(models.Lead.email.isnot(None), models.Lead.email != "").count()
    usable_email = lead_q.filter(
        models.Lead.email.isnot(None),
        models.Lead.email != "",
        models.Lead.email_validation_status.in_(list(VERIFIED_EMAIL_STATUSES)),
    ).count()
    needs_email = by_status.get("needs_email", 0)
    invalid_email = by_status.get("invalid_email", 0)
    bounced = by_status.get("bounced", 0)

    funnel = {stage: by_status.get(stage, 0) for stage in _FUNNEL_ORDER}
    funnel["replied"] = lead_q.filter(
        (models.Lead.has_replied.is_(True)) | (models.Lead.status == "replied")
    ).count()
    stuck = [
        {"status": s, "count": by_status[s], "reason": _STUCK_REASONS.get(s, s)}
        for s in by_status
        if s in _STUCK_REASONS and by_status[s] > 0
    ]
    stuck.sort(key=lambda x: x["count"], reverse=True)

    # Recent activity
    now = datetime.now(timezone.utc)
    day_ago = now - timedelta(hours=24)
    leads_24h = lead_q.filter(models.Lead.created_at >= day_ago).count()
    last_lead = lead_q.order_by(models.Lead.created_at.desc()).first()
    emails_24h = (
        db.query(func.count(models.EmailLog.id))
        .join(models.Lead, models.EmailLog.lead_id == models.Lead.id)
        .filter(
            models.Lead.workflow_id == workflow_id,
            models.EmailLog.direction == "outbound",
            models.EmailLog.sent_at >= day_ago,
        )
        .scalar()
    )

    # Source breakdown
    src_rows = (
        db.query(models.Lead.source_channel, func.count(models.Lead.id))
        .filter(models.Lead.workflow_id == workflow_id)
        .group_by(models.Lead.source_channel)
        .all()
    )
    leadcontact_total = lead_q.filter(models.Lead.source_channel == "leadcontact").count()
    leadcontact_without_email = lead_q.filter(
        models.Lead.source_channel == "leadcontact",
        (models.Lead.email.is_(None)) | (models.Lead.email == ""),
    ).count()
    leadcontact_usable_email = lead_q.filter(
        models.Lead.source_channel == "leadcontact",
        models.Lead.email.isnot(None),
        models.Lead.email != "",
        models.Lead.email_validation_status.in_(list(VERIFIED_EMAIL_STATUSES)),
    ).count()

    # External provider configuration (presence, not live health — that's P1-1)
    sender_accounts = db.query(func.count(models.WorkflowEmail.id)).filter(
        models.WorkflowEmail.workflow_id == workflow_id
    ).scalar()
    leadcontact_points = _leadcontact_remaining_points()
    providers = {
        "leadcontact": _provider_status("leadcontact", "LEADCONTACT_API_KEY"),
        "leadcontact_remaining_points": leadcontact_points,
        "snovio": _provider_status("snovio", "SNOVIO_CLIENT_ID", "SNOVIO_CLIENT_SECRET"),
        "tavily": _provider_status("tavily", "TAVILY_API_KEY"),
        "bocha": _provider_status("bocha", "BOCHA_API_KEY"),
        "sender_accounts": int(sender_accounts or 0),
        "email_require_verified": os.environ.get("EMAIL_REQUIRE_VERIFIED", "true").strip().lower() not in {"0", "false", "no", "off"},
        "auto_send_drafts": os.environ.get("OUTBOUND_AUTO_SEND_DRAFTS", "false").strip().lower() in {"1", "true", "yes", "on"},
    }

    # Lightweight warnings the user should act on.
    warnings = []
    bad_count = needs_email + invalid_email + bounced + by_status.get("low_score", 0)
    bad_ratio = bad_count / total if total else 0
    if leadcontact_points == 0:
        warnings.append("LeadContact 余额为 0:停止依赖 LeadContact，改用已有线索/Snov/搜索源")
    if providers["sender_accounts"] == 0:
        warnings.append("未绑定发信邮箱:邮件无法发送")
    if with_email == 0 and total > 0:
        warnings.append("所有线索都没有邮箱:检查邮箱补全(LeadContact/Snov)与相关性闸")
    if with_email > 0 and usable_email == 0 and providers["email_require_verified"]:
        warnings.append("有邮箱但没有可验证邮箱:EMAIL_REQUIRE_VERIFIED 会阻止发送")
    if total >= 50 and bad_ratio >= 0.8:
        warnings.append(f"坏线索比例过高:{bad_count}/{total}，应先清理/补全而不是继续搜索")
    if leadcontact_total and leadcontact_without_email / max(1, leadcontact_total) >= 0.5:
        warnings.append(f"LeadContact 无邮箱比例过高:{leadcontact_without_email}/{leadcontact_total}")
    if by_status.get("drafted", 0) > 0 and providers["auto_send_drafts"] is False:
        warnings.append("有草稿待发,但 auto_send 关闭(审核模式):需在审核中心人工发送")
    if funnel["sent"] == 0 and by_status.get("drafted", 0) == 0 and with_email == 0:
        warnings.append("整条链路尚无可发邮件:卡在搜索/补全阶段")

    return {
        "workflow": {"id": wf.id, "name": wf.name, "status": wf.status},
        "totals": {
            "total_leads": total,
            "with_email": with_email,
            "usable_email": int(usable_email or 0),
            "without_email": total - with_email,
            "needs_email": needs_email,
            "invalid_email": invalid_email,
            "bounced": bounced,
            "leadcontact_leads": int(leadcontact_total or 0),
            "leadcontact_without_email": int(leadcontact_without_email or 0),
            "leadcontact_usable_email": int(leadcontact_usable_email or 0),
        },
        "funnel": funnel,
        "stuck": stuck,
        "by_source": {(s or "unknown"): int(c) for s, c in src_rows},
        "recent": {
            "leads_24h": leads_24h,
            "emails_sent_24h": int(emails_24h or 0),
            "last_lead_at": last_lead.created_at.isoformat() if last_lead and last_lead.created_at else None,
        },
        "providers": providers,
        "warnings": warnings,
    }
