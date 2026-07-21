"""Staged acquisition and activation orchestration for Product V2.

The simple first-touch flow is a façade over the existing immutable Campaign,
Enrollment, review, Consent, cost, and Provider-boundary controls.  It never
marks a public ContactPoint valid from client input and never sends directly.
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import json
import os
import re
from typing import Iterable
from urllib.parse import urlsplit

from sqlalchemy import func
from sqlalchemy.orm import Session

from product_v2 import models
from product_v2.enums import (
    CampaignRunMode,
    Channel,
    ChannelAccountHealth,
    ContactPointAvailabilityStatus,
    ContactPointVerificationStatus,
    MessageDirection,
    MessageEventType,
    ProviderCostStatus,
    SafetyLockScope,
    StageStatus,
    TaskStatus,
    TaskType,
    WorkerType,
)
from product_v2.schemas import (
    ActivationLaunchDraft,
    ActivationLaunchPreview,
    ActivationRead,
    ActivationStepRead,
    CampaignQualityGates,
    CampaignRevisionCreate,
    SequenceStepCreate,
)
from product_v2.services.channel_accounts import evaluate_channel_account
from product_v2.services.domain import (
    add_audit,
    campaign_revision_diff,
    campaign_revision_diff_checksum,
    create_campaign_revision,
    create_enrollment,
    enqueue_job,
    is_usable_company_domain,
    normalize_domain,
    publish_campaign_revision,
    utcnow,
)
from product_v2.settings_policy import global_budget_snapshot, setting_document
from product_v2.settings_schemas import ProductSettingSection
from runtime_config import environment, read_flag, read_int
from services.csv_lead_import import normalize_email


_TEMPLATE_PLACEHOLDER = re.compile(r"{{\s*([a-z_][a-z0-9_]*)\s*}}")
_ALLOWED_TEMPLATE_FIELDS = {
    "company_name",
    "company_domain",
    "contact_name",
    "first_name",
    "job_title",
    "unsubscribe_url",
}


def is_fake_acquisition_runtime() -> bool:
    return (
        environment() in {"local", "test"}
        and os.environ.get("AUTOLEADGEN_CONNECTOR_MODE", "fake").strip().lower() == "fake"
    )


def real_acquisition_runtime_allowed() -> bool:
    if environment() not in {"staging", "production"}:
        return False
    if os.environ.get("AUTOLEADGEN_CONNECTOR_MODE", "fake").strip().lower() != "real":
        return False
    return read_flag("ALLOW_REAL_EXTERNAL_CALLS", default=False) and read_flag(
        "ALLOW_REAL_ACQUISITION_CALLS", default=False
    )


def acquisition_hard_paused() -> bool:
    """Independent emergency stop for all paid acquisition calls."""

    return read_flag(
        "ACQUISITION_HARD_PAUSE",
        default=environment() in {"staging", "production"},
    )


def acquisition_owner_allowed(owner_id: int) -> bool:
    raw = os.environ.get("ACQUISITION_OWNER_ALLOWLIST", "")
    allowed = {
        int(value.strip())
        for value in raw.split(",")
        if value.strip().isdigit()
    }
    return owner_id in allowed


def validate_real_acquisition_approval(*, owner_id: int, approval_id: str | None) -> None:
    if is_fake_acquisition_runtime():
        return
    if not real_acquisition_runtime_allowed():
        raise ValueError("real_acquisition_connector_not_approved")
    if acquisition_hard_paused():
        raise ValueError("acquisition_hard_paused")
    if not acquisition_owner_allowed(owner_id):
        raise ValueError("acquisition_owner_not_allowed")
    configured_approval = os.environ.get("ACQUISITION_APPROVAL_ID", "").strip()
    if not configured_approval or not approval_id or approval_id != configured_approval:
        raise ValueError("acquisition_approval_id_invalid")


def acquisition_search_daily_limit() -> int:
    return read_int("ACQUISITION_DAILY_SEARCH_LIMIT", default=5, minimum=1, maximum=100)


def acquisition_verification_daily_limit() -> int:
    return read_int("ACQUISITION_DAILY_VERIFICATION_LIMIT", default=20, minimum=1, maximum=1000)


def email_matches_company_domain(email: str | None, company_domain: str | None) -> bool:
    """Fail closed when a discovered address does not belong to its company."""

    normalized_email = normalize_email(email or "")
    normalized_company = normalize_domain(company_domain)
    if (
        not normalized_email
        or not normalized_company
        or not is_usable_company_domain(normalized_company)
    ):
        return False
    email_domain = normalize_domain(normalized_email.rsplit("@", 1)[-1])
    return email_domain == normalized_company


def _safe_http_url(value: object) -> str | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        parsed = urlsplit(raw)
    except ValueError:
        return None
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
    ):
        return None
    return raw[:2000]


def _record_cost(
    db: Session,
    *,
    run: models.AcquisitionRun,
    operation: str,
    provider: str,
    idempotency_key: str,
    native_unit: str,
    result_count: int,
    billable: bool,
    candidate: models.AcquisitionCandidate | None = None,
) -> models.ProviderCostEvent:
    existing = db.query(models.ProviderCostEvent).filter_by(idempotency_key=idempotency_key).first()
    if existing:
        existing.status = ProviderCostStatus.CHARGED
        existing.provider = provider
        existing.result_count = result_count
        existing.billable = billable
        existing.metadata_json = {
            **(existing.metadata_json or {}),
            "completed_at": utcnow().isoformat(),
            "fake": not billable,
        }
        return existing
    event = models.ProviderCostEvent(
        owner_id=run.owner_id,
        provider=provider,
        operation=operation,
        status=ProviderCostStatus.CHARGED,
        units=1,
        native_unit=native_unit,
        result_count=result_count,
        billable=billable,
        price_version=run.price_version,
        company_id=candidate.committed_company_id if candidate else None,
        contact_id=candidate.committed_contact_id if candidate else None,
        idempotency_key=idempotency_key,
        metadata_json={
            "acquisition_run_id": run.id,
            "acquisition_candidate_id": candidate.id if candidate else None,
            "fake": not billable,
            "network_calls": 0 if not billable else None,
        },
    )
    db.add(event)
    return event


def execute_search_run(db: Session, run: models.AcquisitionRun) -> dict:
    if run.status in {"ready", "verified", "committed"}:
        return {"run_id": run.id, "candidate_count": db.query(models.AcquisitionCandidate).filter_by(run_id=run.id).count()}
    if run.source != "ai":
        raise ValueError("acquisition_run_is_not_ai")
    if db.query(models.AcquisitionCandidate.id).filter_by(run_id=run.id).first():
        raise ValueError("acquisition_run_has_partial_candidates")

    criteria = run.criteria or {}
    limit = max(5, min(int(criteria.get("limit") or 10), 20))
    summary = str(criteria.get("product_summary") or "").strip()
    industries = [str(value).strip() for value in criteria.get("target_industries") or [] if str(value).strip()]
    roles = [str(value).strip() for value in criteria.get("target_roles") or [] if str(value).strip()]
    regions = [str(value).strip() for value in criteria.get("target_regions") or [] if str(value).strip()]
    keywords = ", ".join([summary, *industries, *regions]).strip(", ")

    fake = is_fake_acquisition_runtime()
    if fake:
        results = [
            {
                "title": f"{(industries[0] if industries else '目标行业')}客户 {index}",
                "domain": f"pilot-{run.id}-{index}.example",
                "url": f"https://pilot-{run.id}-{index}.example",
                "snippet": f"与“{summary}”相关的本地隔离候选，仅用于 fake 首次触达验收。",
                "source": "fake-search",
            }
            for index in range(1, limit + 1)
        ]
        provider = "fake-search"
    else:
        validate_real_acquisition_approval(
            owner_id=run.owner_id,
            approval_id=str((run.criteria or {}).get("approval_id") or "") or None,
        )
        from services.search_engine import search_company_results_governed

        outcome = search_company_results_governed(keywords, count=limit)
        provider = outcome.provider
        if outcome.cost_status != "charged":
            reservation = db.query(models.ProviderCostEvent).filter_by(
                idempotency_key=f"cost:acquisition-search:{run.id}"
            ).first()
            if reservation is not None:
                reservation.provider = provider
                reservation.status = ProviderCostStatus(outcome.cost_status)
                reservation.billable = False if outcome.cost_status == "failed" else True
                reservation.metadata_json = {
                    **(reservation.metadata_json or {}),
                    "error": outcome.error,
                    "automatic_retry_allowed": False,
                }
            raise ValueError(f"acquisition_search_{outcome.cost_status}:{provider}")
        results = outcome.results

    existing_domains = {
        value
        for (value,) in db.query(models.Company.normalized_domain).filter(
            models.Company.owner_id == run.owner_id,
            models.Company.normalized_domain.is_not(None),
            models.Company.archived_at.is_(None),
        ).all()
        if value
    }
    for index, result in enumerate(results[:limit], start=1):
        domain_source = str(result.get("domain") or result.get("url") or "").strip()
        domain = normalize_domain(domain_source)
        usable_domain = is_usable_company_domain(domain)
        if not usable_domain:
            domain = None
        duplicate = bool(domain and domain in existing_domains)
        status = "invalid" if not usable_domain else ("duplicate" if duplicate else "ready")
        rejection_reason = (
            "搜索结果缺少可用的公司域名"
            if not usable_domain
            else ("客户库中已存在相同域名" if duplicate else None)
        )
        db.add(
            models.AcquisitionCandidate(
                owner_id=run.owner_id,
                run_id=run.id,
                row_number=index,
                status=status,
                company_name=str(result.get("title") or domain or f"候选客户 {index}")[:255],
                normalized_domain=domain,
                job_title=roles[0][:255] if roles else None,
                source_url=_safe_http_url(result.get("url")),
                evidence={
                    "source": result.get("source") or provider,
                    "snippet": str(result.get("snippet") or "")[:4000],
                    "query": keywords,
                    "target_roles": roles,
                    "target_regions": regions,
                },
                confidence=Decimal("0.7000") if not fake else Decimal("1.0000"),
                rejection_reason=rejection_reason,
            )
        )
    run.provider = provider
    run.status = "ready"
    run.last_error = None
    _record_cost(
        db,
        run=run,
        operation="company_search",
        provider=provider,
        idempotency_key=f"cost:acquisition-search:{run.id}",
        native_unit="search_calls",
        result_count=len(results[:limit]),
        billable=not fake,
    )
    add_audit(
        db,
        owner_id=run.owner_id,
        actor_user_id=None,
        action="activation.source_run_completed",
        entity_type="acquisition_run",
        entity_id=run.id,
        after={"source": "ai", "candidate_count": len(results[:limit]), "provider": provider},
    )
    return {"run_id": run.id, "candidate_count": len(results[:limit]), "provider": provider}


def _fake_verified_email(candidate: models.AcquisitionCandidate) -> str | None:
    if candidate.normalized_email:
        return candidate.normalized_email
    if not candidate.normalized_domain:
        return None
    local = re.sub(r"[^a-z0-9]+", ".", (candidate.first_name or "buyer").lower()).strip(".") or "buyer"
    return f"{local}{candidate.row_number}@{candidate.normalized_domain}"


def _real_enrich_email(run: models.AcquisitionRun, candidate: models.AcquisitionCandidate) -> str | None:
    if candidate.normalized_email:
        return candidate.normalized_email
    if not candidate.normalized_domain:
        return None
    client_id = os.environ.get("SNOVIO_CLIENT_ID", "").strip()
    client_secret = os.environ.get("SNOVIO_CLIENT_SECRET", "").strip()
    if not client_id or not client_secret:
        return None
    from services.snovio_client import SnovioClient

    roles = [str(value) for value in (run.criteria or {}).get("target_roles") or []]
    client = SnovioClient(client_id, client_secret)
    prospects = client.search_prospects_by_domain(
        candidate.normalized_domain,
        roles,
        max_pages=1,
        allow_broad_fallback=False,
    )
    if not prospects:
        return None
    prospect = prospects[0]
    email = client.get_prospect_email(
        str(prospect.get("search_emails_start") or ""),
        candidate.normalized_domain,
    )
    if email:
        candidate.first_name = str(prospect.get("first_name") or "")[:120] or candidate.first_name
        candidate.last_name = str(prospect.get("last_name") or "")[:120] or candidate.last_name
        candidate.full_name = " ".join(filter(None, [candidate.first_name, candidate.last_name])).strip() or candidate.full_name
        candidate.job_title = str(prospect.get("position") or "")[:255] or candidate.job_title
        candidate.source_url = _safe_http_url(
            prospect.get("source_page") or candidate.source_url
        )
    return email


def execute_verify_run(
    db: Session,
    run: models.AcquisitionRun,
    candidate_ids: Iterable[int],
    approval_id: str | None = None,
) -> dict:
    requested = list(dict.fromkeys(int(value) for value in candidate_ids))
    if not requested or len(requested) > 20:
        raise ValueError("verify_requires_between_1_and_20_candidates")
    candidates = db.query(models.AcquisitionCandidate).filter(
        models.AcquisitionCandidate.owner_id == run.owner_id,
        models.AcquisitionCandidate.run_id == run.id,
        models.AcquisitionCandidate.id.in_(requested),
    ).order_by(models.AcquisitionCandidate.id.asc()).all()
    if len(candidates) != len(requested):
        raise ValueError("candidate_not_found_in_acquisition_run")

    fake = is_fake_acquisition_runtime()
    if not fake:
        validate_real_acquisition_approval(
            owner_id=run.owner_id,
            approval_id=approval_id or str((run.criteria or {}).get("approval_id") or "") or None,
        )

    verified = 0
    for candidate in candidates:
        if candidate.status == "committed":
            verified += int(candidate.verification_status == ContactPointVerificationStatus.VALID)
            continue
        if candidate.status in {"duplicate", "invalid"}:
            continue
        email = _fake_verified_email(candidate) if fake else _real_enrich_email(run, candidate)
        normalized_email = normalize_email(email or "")
        domain_matches = email_matches_company_domain(
            normalized_email,
            candidate.normalized_domain,
        )
        if not normalized_email or not domain_matches:
            candidate.status = "invalid"
            candidate.verification_status = ContactPointVerificationStatus.INVALID
            candidate.verification_source = "fake-format" if fake else "approved-enrichment"
            candidate.verification_checked_at = utcnow()
            candidate.rejection_reason = (
                "邮箱域名与公司域名不一致"
                if normalized_email
                else "未找到可验证的工作邮箱"
            )
            result_count = 0
        else:
            duplicate = db.query(models.ContactPoint.id).filter(
                models.ContactPoint.owner_id == run.owner_id,
                models.ContactPoint.channel == Channel.EMAIL,
                models.ContactPoint.normalized_value == normalized_email,
                models.ContactPoint.archived_at.is_(None),
            ).first()
            if duplicate:
                candidate.status = "duplicate"
                candidate.rejection_reason = "客户库中已存在相同邮箱"
                candidate.verification_status = ContactPointVerificationStatus.VALID
                candidate.verification_source = "existing-contact-point"
                result_count = 0
            elif fake:
                candidate.status = "selected"
                candidate.selected = True
                candidate.email = normalized_email
                candidate.normalized_email = normalized_email
                candidate.verification_status = ContactPointVerificationStatus.VALID
                candidate.verification_source = "fake-verifier"
                candidate.verification_checked_at = utcnow()
                candidate.rejection_reason = None
                verified += 1
                result_count = 1
            else:
                from services.email_verifier import verify_email_sync

                outcome = verify_email_sync(normalized_email)
                status_map = {
                    "valid": ContactPointVerificationStatus.VALID,
                    "invalid": ContactPointVerificationStatus.INVALID,
                    "catch-all": ContactPointVerificationStatus.CATCH_ALL,
                    "unknown": ContactPointVerificationStatus.UNKNOWN,
                }
                verification = status_map.get(str(outcome.get("status") or "unknown"), ContactPointVerificationStatus.UNKNOWN)
                candidate.email = normalized_email
                candidate.normalized_email = normalized_email
                candidate.verification_status = verification
                candidate.verification_source = str(outcome.get("source") or "approved-verifier")[:100]
                candidate.verification_checked_at = utcnow()
                candidate.selected = verification == ContactPointVerificationStatus.VALID
                candidate.status = "selected" if candidate.selected else "ready"
                candidate.rejection_reason = None if candidate.selected else f"邮箱验证结果：{verification.value}"
                verified += int(candidate.selected)
                result_count = int(candidate.selected)

        _record_cost(
            db,
            run=run,
            candidate=candidate,
            operation="email_verification",
            provider="fake-verifier" if fake else "approved-email-verifier",
            idempotency_key=f"cost:acquisition-verify:{run.id}:{candidate.id}",
            native_unit="verification_calls",
            result_count=result_count,
            billable=not fake,
        )
    run.status = "verified"
    run.last_error = None
    add_audit(
        db,
        owner_id=run.owner_id,
        actor_user_id=None,
        action="activation.candidates_verified",
        entity_type="acquisition_run",
        entity_id=run.id,
        after={"requested": len(requested), "verified": verified, "fake": fake},
    )
    return {"run_id": run.id, "requested": len(requested), "verified": verified}


def _candidate_company(db: Session, candidate: models.AcquisitionCandidate) -> models.Company:
    company = None
    if candidate.normalized_domain:
        company = db.query(models.Company).filter(
            models.Company.owner_id == candidate.owner_id,
            models.Company.normalized_domain == candidate.normalized_domain,
            models.Company.archived_at.is_(None),
        ).first()
    if company:
        return company
    company = models.Company(
        owner_id=candidate.owner_id,
        name=(candidate.company_name or candidate.normalized_domain or "待确认客户")[:255],
        normalized_domain=candidate.normalized_domain,
        website=f"https://{candidate.normalized_domain}" if candidate.normalized_domain else None,
    )
    db.add(company)
    db.flush()
    return company


def commit_candidates(
    db: Session,
    *,
    run: models.AcquisitionRun,
    candidate_ids: Iterable[int],
    actor_user_id: int,
    correlation_id: str,
) -> list[models.AcquisitionCandidate]:
    requested = list(dict.fromkeys(int(value) for value in candidate_ids))
    candidates = db.query(models.AcquisitionCandidate).filter(
        models.AcquisitionCandidate.owner_id == run.owner_id,
        models.AcquisitionCandidate.run_id == run.id,
        models.AcquisitionCandidate.id.in_(requested),
    ).order_by(models.AcquisitionCandidate.id.asc()).all()
    if len(candidates) != len(requested):
        raise ValueError("candidate_not_found_in_acquisition_run")
    invalid = [
        candidate.id
        for candidate in candidates
        if candidate.status != "committed"
        and (
            not candidate.selected
            or candidate.verification_status != ContactPointVerificationStatus.VALID
            or not candidate.normalized_email
        )
    ]
    if invalid:
        raise ValueError(f"only_verified_selected_candidates_can_be_committed:{invalid[0]}")

    for candidate in candidates:
        if candidate.status == "committed":
            continue
        existing_point = db.query(models.ContactPoint).filter(
            models.ContactPoint.owner_id == run.owner_id,
            models.ContactPoint.channel == Channel.EMAIL,
            models.ContactPoint.normalized_value == candidate.normalized_email,
            models.ContactPoint.archived_at.is_(None),
        ).first()
        if existing_point:
            candidate.committed_company_id = existing_point.company_id
            candidate.committed_contact_id = existing_point.contact_id
            candidate.committed_contact_point_id = existing_point.id
            candidate.status = "committed"
            continue

        company = _candidate_company(db, candidate)
        full_name = (
            candidate.full_name
            or " ".join(filter(None, [candidate.first_name, candidate.last_name])).strip()
            or (candidate.normalized_email or "联系人").split("@", 1)[0]
        )[:255]
        contact = models.Contact(
            owner_id=run.owner_id,
            company_id=company.id,
            first_name=candidate.first_name,
            last_name=candidate.last_name,
            full_name=full_name,
            job_title=candidate.job_title,
        )
        db.add(contact)
        db.flush()
        point = models.ContactPoint(
            owner_id=run.owner_id,
            company_id=company.id,
            contact_id=contact.id,
            channel=Channel.EMAIL,
            value=candidate.email or candidate.normalized_email,
            normalized_value=candidate.normalized_email,
            verification_status=ContactPointVerificationStatus.VALID,
            availability_status=ContactPointAvailabilityStatus.AVAILABLE,
            is_primary=True,
            verified_at=candidate.verification_checked_at or utcnow(),
        )
        db.add(point)
        db.flush()
        version = (
            db.query(func.coalesce(func.max(models.EvidenceSnapshot.version), 0))
            .filter(
                models.EvidenceSnapshot.owner_id == run.owner_id,
                models.EvidenceSnapshot.company_id == company.id,
                models.EvidenceSnapshot.contact_id == contact.id,
            )
            .scalar()
            + 1
        )
        db.add(
            models.EvidenceSnapshot(
                owner_id=run.owner_id,
                company_id=company.id,
                contact_id=contact.id,
                source=f"acquisition:{run.source}",
                source_url=candidate.source_url,
                evidence={
                    **(candidate.evidence or {}),
                    "acquisition_run_id": run.id,
                    "acquisition_candidate_id": candidate.id,
                    "email_verification_source": candidate.verification_source,
                },
                confidence=candidate.confidence,
                version=version,
            )
        )
        candidate.committed_company_id = company.id
        candidate.committed_contact_id = contact.id
        candidate.committed_contact_point_id = point.id
        candidate.status = "committed"
        add_audit(
            db,
            owner_id=run.owner_id,
            actor_user_id=actor_user_id,
            action="acquisition_candidate.committed",
            entity_type="acquisition_candidate",
            entity_id=candidate.id,
            correlation_id=correlation_id,
            after={"company_id": company.id, "contact_id": contact.id, "contact_point_id": point.id},
        )
    run.status = "committed"
    run.committed_at = utcnow()
    add_audit(
        db,
        owner_id=run.owner_id,
        actor_user_id=actor_user_id,
        action="activation.customers_committed",
        entity_type="acquisition_run",
        entity_id=run.id,
        correlation_id=correlation_id,
        after={"candidate_ids": requested, "count": len(requested)},
        metadata={
            "command": {
                "run_id": run.id,
                "candidate_ids": requested,
                "human_confirmed": True,
            }
        },
    )
    return candidates


def activation_snapshot(db: Session, *, owner_id: int) -> ActivationRead:
    icp = setting_document(db, owner_id=owner_id, section=ProductSettingSection.ICP_PLAYBOOK)
    channels = setting_document(db, owner_id=owner_id, section=ProductSettingSection.CHANNELS_INTEGRATIONS)
    icp_ready = bool(
        icp.configured
        and icp.values.get("proposal_status") == "published"
        and str(icp.values.get("summary") or "").strip()
    )
    account = db.query(models.ChannelAccount).filter(
        models.ChannelAccount.owner_id == owner_id,
        models.ChannelAccount.channel == Channel.EMAIL,
        models.ChannelAccount.enabled.is_(True),
        models.ChannelAccount.health_status == ChannelAccountHealth.HEALTHY,
        models.ChannelAccount.archived_at.is_(None),
    ).order_by(models.ChannelAccount.updated_at.desc()).first()
    mailbox_ready = bool(account and (account.provider.startswith("fake-") or account.health_checked_at))
    latest_run = db.query(models.AcquisitionRun).filter_by(owner_id=owner_id).order_by(
        models.AcquisitionRun.updated_at.desc(), models.AcquisitionRun.id.desc()
    ).first()
    customers_ready = bool(
        latest_run
        and db.query(models.AcquisitionCandidate.id).filter(
            models.AcquisitionCandidate.run_id == latest_run.id,
            models.AcquisitionCandidate.status == "committed",
        ).first()
    )
    activation_audit = db.query(models.AuditEvent).filter_by(
        owner_id=owner_id,
        action="activation.launch_completed",
        entity_type="campaign",
    ).order_by(models.AuditEvent.id.desc()).first()
    campaign = db.get(models.Campaign, int(activation_audit.entity_id)) if activation_audit else None
    plan_ready = bool(campaign and campaign.published_revision_number is not None)
    first_sent_at = None
    if campaign:
        first_sent_at = db.query(func.min(models.MessageEvent.occurred_at)).join(
            models.OutreachAttempt,
            models.OutreachAttempt.id == models.MessageEvent.outreach_attempt_id,
        ).filter(
            models.MessageEvent.owner_id == owner_id,
            models.MessageEvent.direction == MessageDirection.OUTBOUND,
            models.MessageEvent.event_type == MessageEventType.SENT,
            models.OutreachAttempt.campaign_id == campaign.id,
        ).scalar()
    review_tasks_open = 0
    if campaign:
        review_tasks_open = db.query(models.Task.id).filter(
            models.Task.owner_id == owner_id,
            models.Task.campaign_id == campaign.id,
            models.Task.task_type == TaskType.DRAFT_REVIEW,
            models.Task.status.in_((TaskStatus.OPEN, TaskStatus.IN_PROGRESS)),
            models.Task.archived_at.is_(None),
        ).count()
    send_ready = first_sent_at is not None
    started_at = db.query(func.min(models.AcquisitionRun.created_at)).filter(
        models.AcquisitionRun.owner_id == owner_id
    ).scalar()

    steps = [
        ActivationStepRead(key="icp", label="描述产品与理想客户", completed=icp_ready, detail="已发布理想客户画像" if icp_ready else "填写产品、行业、角色和证据要求", href="/dashboard/get-started?step=1"),
        ActivationStepRead(key="mailbox", label="确认发件邮箱", completed=mailbox_ready, detail=account.provider_account_id if mailbox_ready and account else "等待管理员配置并完成健康检查", href="/dashboard/get-started?step=2"),
        ActivationStepRead(key="customers", label="准备首批客户", completed=customers_ready, detail="已确认有效候选" if customers_ready else "导入 CSV 或使用 AI 找客户", href="/dashboard/get-started?step=3"),
        ActivationStepRead(key="plan", label="生成并审核邮件", completed=plan_ready, detail="触达计划已发布" if plan_ready else "创建 5–20 人试跑并逐封审核", href="/dashboard/get-started?step=4"),
        ActivationStepRead(key="send", label="发出第一封邮件", completed=send_ready, detail="首封邮件已成功发送" if send_ready else "批准草稿后等待安全发送", href="/dashboard/get-started?step=5"),
    ]
    first_incomplete = next((index for index, step in enumerate(steps, start=1) if not step.completed), 5)
    blockers: list[str] = []
    if not mailbox_ready:
        blockers.append("尚无健康可用的发件邮箱")
    if channels.configured and not channels.values.get("email_enabled"):
        blockers.append("Email 渠道策略尚未启用")
    if channels.configured and not str(channels.values.get("public_unsubscribe_url") or "").startswith("https://"):
        blockers.append("尚未配置 HTTPS 公共退订地址")
    return ActivationRead(
        activated=send_ready,
        current_step=first_incomplete,
        started_at=started_at,
        first_sent_at=first_sent_at,
        steps=steps,
        blockers=blockers,
        latest_run_id=latest_run.id if latest_run else None,
        campaign_id=campaign.id if campaign else None,
        review_tasks_open=review_tasks_open,
    )


def _heartbeat_live(db: Session, worker_type: WorkerType) -> bool:
    now = datetime.now(timezone.utc)
    heartbeat = db.query(models.WorkerHeartbeat).filter_by(worker_type=worker_type).order_by(
        models.WorkerHeartbeat.last_seen_at.desc()
    ).first()
    if not heartbeat or heartbeat.status not in {StageStatus.RUNNING, StageStatus.IDLE, StageStatus.BACKOFF}:
        return False
    expires = heartbeat.lease_expires_at
    if not expires:
        return False
    if expires.tzinfo is None:
        expires = expires.replace(tzinfo=timezone.utc)
    return expires >= now


def activation_launch_preview(
    db: Session,
    *,
    owner_id: int,
    draft: ActivationLaunchDraft,
) -> ActivationLaunchPreview:
    blockers: list[str] = []
    run = db.query(models.AcquisitionRun).filter_by(id=draft.run_id, owner_id=owner_id).first()
    candidates: list[models.AcquisitionCandidate] = []
    if not run:
        blockers.append("找不到本账号的候选客户批次")
    else:
        candidates = db.query(models.AcquisitionCandidate).filter(
            models.AcquisitionCandidate.owner_id == owner_id,
            models.AcquisitionCandidate.run_id == run.id,
            models.AcquisitionCandidate.id.in_(draft.candidate_ids),
        ).order_by(models.AcquisitionCandidate.id.asc()).all()
        if len(candidates) != len(draft.candidate_ids):
            blockers.append("候选客户列表已变化，请重新选择")
        elif any(
            candidate.status != "committed"
            or candidate.verification_status != ContactPointVerificationStatus.VALID
            or not candidate.committed_contact_point_id
            for candidate in candidates
        ):
            blockers.append("只有已确认且邮箱验证有效的客户才能进入试跑")

    account = db.query(models.ChannelAccount).filter_by(
        id=draft.channel_account_id,
        owner_id=owner_id,
        channel=Channel.EMAIL,
        archived_at=None,
    ).first()
    if not account:
        blockers.append("找不到本账号的发件邮箱")
    else:
        account_decision = evaluate_channel_account(
            db,
            account=account,
            owner_id=owner_id,
            channel=Channel.EMAIL,
        )
        blockers.extend(f"发件邮箱未就绪：{reason}" for reason in account_decision.blockers)
        if account.daily_limit is not None and draft.daily_limit > account.daily_limit:
            blockers.append(f"试跑数量超过邮箱每日上限 {account.daily_limit}")

    icp = setting_document(db, owner_id=owner_id, section=ProductSettingSection.ICP_PLAYBOOK)
    channels = setting_document(db, owner_id=owner_id, section=ProductSettingSection.CHANNELS_INTEGRATIONS)
    if not (icp.configured and icp.values.get("proposal_status") == "published"):
        blockers.append("请先发布理想客户画像")
    if not (channels.configured and channels.values.get("email_enabled")):
        blockers.append("请先启用 Email 渠道策略")
    unsubscribe_url = str(channels.values.get("public_unsubscribe_url") or "")
    if not unsubscribe_url.startswith("https://"):
        blockers.append("请先配置 HTTPS 公共退订地址")
    if channels.configured and channels.values.get("review_before_send") is not True:
        blockers.append("首次触达必须启用发送前审核")
    for field, template in (("主题", draft.subject_template), ("正文", draft.body_template)):
        unknown = sorted(set(_TEMPLATE_PLACEHOLDER.findall(template)) - _ALLOWED_TEMPLATE_FIELDS)
        if unknown:
            blockers.append(f"{field}包含不支持的变量：{unknown[0]}")
        remainder = _TEMPLATE_PLACEHOLDER.sub("", template)
        if "{{" in remainder or "}}" in remainder:
            blockers.append(f"{field}模板变量格式不完整")
    if "\r" in draft.subject_template or "\n" in draft.subject_template:
        blockers.append("主题不能包含换行符")
    if not _heartbeat_live(db, WorkerType.OUTBOUND):
        blockers.append("邮件发送执行器尚未就绪")
    if not _heartbeat_live(db, WorkerType.INBOX):
        blockers.append("收件箱监听执行器尚未就绪")
    active_lock = db.query(models.SafetyLock.id).filter(
        models.SafetyLock.owner_id == owner_id,
        models.SafetyLock.active.is_(True),
        models.SafetyLock.scope == SafetyLockScope.GLOBAL,
    ).first()
    if active_lock:
        blockers.append("账号存在全局安全锁")

    state = {
        "draft": draft.model_dump(mode="json"),
        "run_status": run.status if run else None,
        "candidates": [
            {
                "id": candidate.id,
                "status": candidate.status,
                "verification_status": candidate.verification_status.value,
                "contact_id": candidate.committed_contact_id,
                "contact_point_id": candidate.committed_contact_point_id,
            }
            for candidate in candidates
        ],
        "account": {
            "id": account.id,
            "enabled": account.enabled,
            "health": account.health_status.value,
            "daily_limit": account.daily_limit,
            "updated_at": account.updated_at.isoformat(),
        } if account else None,
        "settings_versions": {"icp": icp.version, "channels": channels.version},
        "blockers": blockers,
    }
    checksum = hashlib.sha256(
        json.dumps(state, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    count = len(candidates)
    return ActivationLaunchPreview(
        checksum=checksum,
        effects=[
            f"创建一个默认逐封审核的触达计划，并加入 {count} 名已验证联系人。",
            "保存不可变邮件版本；启动前和发送前仍会重新检查全部硬门槛。",
            "批准前不会调用邮件发送 Provider，拒绝草稿会取消对应发送。",
        ],
        blockers=list(dict.fromkeys(blockers)),
        candidate_count=count,
        estimated_send_count=min(count, draft.daily_limit),
    )


def execute_activation_launch(db: Session, job: models.AutomationJob) -> dict:
    draft = ActivationLaunchDraft.model_validate(job.payload.get("draft") or {})
    owner_id = job.owner_id
    actor_user_id = int(job.payload.get("actor_user_id") or owner_id)
    preview = activation_launch_preview(db, owner_id=owner_id, draft=draft)
    if preview.checksum != job.payload.get("preview_checksum"):
        raise ValueError("activation_launch_preview_is_stale")
    if preview.blockers:
        raise ValueError(f"activation_launch_blocked:{preview.blockers[0]}")

    account = db.query(models.ChannelAccount).filter_by(
        id=draft.channel_account_id,
        owner_id=owner_id,
        channel=Channel.EMAIL,
        archived_at=None,
    ).one()
    candidates = db.query(models.AcquisitionCandidate).filter(
        models.AcquisitionCandidate.owner_id == owner_id,
        models.AcquisitionCandidate.run_id == draft.run_id,
        models.AcquisitionCandidate.id.in_(draft.candidate_ids),
        models.AcquisitionCandidate.status == "committed",
    ).order_by(models.AcquisitionCandidate.id.asc()).all()
    channels = setting_document(db, owner_id=owner_id, section=ProductSettingSection.CHANNELS_INTEGRATIONS)
    icp = setting_document(db, owner_id=owner_id, section=ProductSettingSection.ICP_PLAYBOOK)
    global_budget = global_budget_snapshot(db, owner_id=owner_id)

    campaign = models.Campaign(
        owner_id=owner_id,
        name=draft.plan_name.strip(),
        description=draft.objective.strip(),
        run_mode=CampaignRunMode.REVIEW,
        priority=100,
    )
    db.add(campaign)
    db.flush()
    budget_definition = {
        "native_limit": draft.daily_limit,
        "native_unit": "email_attempts",
        "price_version": global_budget.price_version if global_budget.configured else ("fake-v1" if account.provider.startswith("fake-") else "email-direct-v1"),
    }
    if global_budget.configured and global_budget.limit is not None:
        budget_definition.update(
            {
                "normalized_unit_price": 0,
                "currency": global_budget.currency,
                "price_version": global_budget.price_version,
            }
        )
    revision_data = CampaignRevisionCreate(
        icp_definition={
            **icp.values,
            "activation_objective": draft.objective,
            "tone": draft.tone,
            "language": draft.language,
        },
        audience_definition={
            "acquisition_run_id": draft.run_id,
            "candidate_ids": draft.candidate_ids,
            "max_contacts": draft.daily_limit,
        },
        quality_gates=CampaignQualityGates(
            require_evidence=True,
            require_timezone=False,
            require_verified_contact_point=True,
        ),
        budget_definition=budget_definition,
        stop_conditions={
            "public_unsubscribe_url": channels.values.get("public_unsubscribe_url"),
            "stop_on_reply": True,
            "stop_on_unsubscribe": True,
        },
        sequence_steps=[
            SequenceStepCreate(
                position=1,
                channel=Channel.EMAIL,
                channel_account_id=account.id,
                wait_minutes=0,
                template_version=f"activation-{job.id}",
                subject_template=draft.subject_template,
                body_template=draft.body_template,
                conditions={},
                stop_conditions={"on_reply": True, "on_unsubscribe": True},
            )
        ],
    )
    revision = create_campaign_revision(
        db,
        campaign=campaign,
        actor_user_id=actor_user_id,
        data=revision_data,
    )
    # Publication refreshes the locked rows from the database before checking
    # the reviewed diff.  Persist JSON/Decimal normalization first so the
    # checksum is computed over that same durable representation.
    db.flush()
    db.refresh(revision)
    base, diff = campaign_revision_diff(db, campaign=campaign, proposed=revision)
    base_revision_id = base.id if base and base.id != revision.id else None
    diff_checksum = campaign_revision_diff_checksum(
        campaign_id=campaign.id,
        base_revision_id=base_revision_id,
        proposed_revision_id=revision.id,
        diff=diff,
    )
    publish_campaign_revision(
        db,
        campaign=campaign,
        revision=revision,
        actor_user_id=actor_user_id,
        idempotency_key=f"activation-publish:{job.idempotency_key}",
        base_revision_id=base_revision_id,
        reviewed_diff_checksum=diff_checksum,
        human_confirmed=True,
    )
    db.flush()
    db.refresh(campaign)
    enrollment_ids: list[int] = []
    for candidate in candidates:
        contact = db.query(models.Contact).filter_by(
            id=candidate.committed_contact_id,
            owner_id=owner_id,
            archived_at=None,
        ).one()
        enrollment, _ = create_enrollment(
            db,
            campaign=campaign,
            contact=contact,
            idempotency_key=f"activation-enroll:{job.idempotency_key}:{candidate.id}",
            scheduled_at=None,
            actor_user_id=actor_user_id,
        )
        enrollment_ids.append(enrollment.id)
    start_job = enqueue_job(
        db,
        owner_id=owner_id,
        job_type="campaign.start",
        idempotency_key=f"activation-start:{job.idempotency_key}",
        queue="campaign",
        campaign_id=campaign.id,
        payload={"campaign_id": campaign.id, "confirm_warnings": True},
        priority=campaign.priority,
    )
    add_audit(
        db,
        owner_id=owner_id,
        actor_user_id=actor_user_id,
        action="activation.launch_completed",
        entity_type="campaign",
        entity_id=campaign.id,
        correlation_id=job.idempotency_key,
        after={
            "campaign_id": campaign.id,
            "revision_id": revision.id,
            "enrollment_ids": enrollment_ids,
            "start_job_id": start_job.id,
            "run_mode": "review",
        },
    )
    return {
        "campaign_id": campaign.id,
        "revision_id": revision.id,
        "enrollment_ids": enrollment_ids,
        "start_job_id": start_job.id,
        "review_required": True,
    }
