"""Resumable, additive legacy-to-Product-V2 backfill.

Every insert has a deterministic legacy key or idempotency key.  A process may
therefore crash after committing a batch but before writing its checkpoint and
still resume without duplicating records.
"""
from __future__ import annotations

import hashlib
import json
import os
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum
from pathlib import Path
from typing import Any, Optional

from sqlalchemy import Date, DateTime, func, or_
from sqlalchemy.orm import Session

import models as legacy
from product_v2 import models
from product_v2.enums import (
    AttemptStatus,
    CampaignLifecycle,
    CampaignRevisionStatus,
    CampaignRunMode,
    Channel,
    ContactPointAvailabilityStatus,
    ContactPointVerificationStatus,
    ConversationStatus,
    EnrollmentStatus,
    MessageDirection,
    MessageEventType,
    ProviderCostStatus,
    ReplyAssessmentStatus,
    ReplyIntent,
    RestrictionScope,
    SafetyLockScope,
    TaskPriority,
    TaskQueueScope,
    TaskStatus,
    TaskType,
)
from product_v2.services.channel_accounts import bind_legacy_email_account
from product_v2.services.domain import (
    is_usable_company_domain,
    normalize_contact_point,
    normalize_domain,
    utcnow,
)


@dataclass
class QuarantineItem:
    source_table: str
    source_id: int
    reason: str
    details: dict[str, Any] = field(default_factory=dict)


@dataclass
class BackfillReport:
    mode: str
    resumed_after_lead_id: int
    processed_leads: int = 0
    created: dict[str, int] = field(default_factory=dict)
    reused: dict[str, int] = field(default_factory=dict)
    quarantine: list[QuarantineItem] = field(default_factory=list)
    checksum: str = ""

    def bump(self, bucket: str, table: str) -> None:
        target = self.created if bucket == "created" else self.reused
        target[table] = target.get(table, 0) + 1

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["quarantine"] = [asdict(item) for item in self.quarantine]
        return data


def _owner_for_lead(db: Session, lead: legacy.Lead) -> tuple[Optional[int], Optional[str]]:
    workflow_owner = None
    pool_owner = None
    if lead.workflow_id:
        workflow_owner = db.query(legacy.Workflow.user_id).filter_by(id=lead.workflow_id).scalar()
    if lead.client_pool_id:
        pool_owner = db.query(legacy.ClientPool.user_id).filter_by(id=lead.client_pool_id).scalar()
    if workflow_owner and pool_owner and workflow_owner != pool_owner:
        return None, "workflow_pool_owner_conflict"
    owner = workflow_owner or pool_owner
    return (owner, None) if owner else (None, "missing_owner")


def _legacy(db: Session, model, owner_id: int, table: str, source_id: int):
    return db.query(model).filter_by(
        owner_id=owner_id,
        legacy_source_table=table,
        legacy_id=str(source_id),
    ).first()


class ProductV2Backfill:
    def __init__(
        self,
        db: Session,
        *,
        apply: bool,
        resume: bool,
        batch_size: int = 100,
        checkpoint_path: Path,
        quarantine_path: Path,
    ):
        self.db = db
        self.apply = apply
        self.resume = resume
        self.batch_size = max(1, min(batch_size, 1000))
        self.checkpoint_path = checkpoint_path
        self.quarantine_path = quarantine_path
        self.start_after = self._read_checkpoint() if resume else 0
        self.report = BackfillReport(mode="apply" if apply else "dry-run", resumed_after_lead_id=self.start_after)
        if resume:
            self.report.quarantine.extend(self._read_quarantine())

    def _read_checkpoint(self) -> int:
        if not self.checkpoint_path.exists():
            return 0
        data = json.loads(self.checkpoint_path.read_text(encoding="utf-8"))
        if data.get("version") != 1:
            raise ValueError("Unsupported Product V2 backfill checkpoint version")
        return int(data.get("last_lead_id") or 0)

    def _atomic_json(self, path: Path, payload: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        os.chmod(tmp, 0o600)
        tmp.replace(path)

    def _read_quarantine(self) -> list[QuarantineItem]:
        if not self.quarantine_path.exists():
            return []
        data = json.loads(self.quarantine_path.read_text(encoding="utf-8"))
        if data.get("version") != 1:
            raise ValueError("Unsupported Product V2 quarantine version")
        return [QuarantineItem(**item) for item in data.get("items", [])]

    def _dedupe_quarantine(self) -> None:
        unique: list[QuarantineItem] = []
        seen: set[tuple[str, int, str, str]] = set()
        for item in self.report.quarantine:
            key = (
                item.source_table,
                item.source_id,
                item.reason,
                json.dumps(item.details, ensure_ascii=False, sort_keys=True, default=str),
            )
            if key not in seen:
                seen.add(key)
                unique.append(item)
        self.report.quarantine = unique

    def _checkpoint(self, last_lead_id: int) -> None:
        if not self.apply:
            return
        self._dedupe_quarantine()
        # Persist quarantine first. If that write fails, no checkpoint advances
        # past a source row whose manual review record was not made durable.
        self._atomic_json(
            self.quarantine_path,
            {"version": 1, "items": [asdict(item) for item in self.report.quarantine]},
        )
        self._atomic_json(
            self.checkpoint_path,
            {"version": 1, "last_lead_id": last_lead_id, "updated_at": utcnow().isoformat()},
        )

    def _record(self, created: bool, table: str):
        self.report.bump("created" if created else "reused", table)

    def _company_domain(self, lead: legacy.Lead) -> Optional[str]:
        raw_domain = (lead.domain or "").strip()
        source_value = raw_domain or lead.email
        domain = normalize_domain(source_value)
        if domain and is_usable_company_domain(source_value):
            return domain
        reason = "invalid_company_domain" if source_value else "missing_company_identity"
        self.report.quarantine.append(
            QuarantineItem(
                "leads",
                lead.id,
                reason,
                {"provided_domain": raw_domain or None},
            )
        )
        return None

    def _identity_company_conflict(
        self,
        lead: legacy.Lead,
        owner_id: int,
        normalized_domain: str,
    ) -> bool:
        """Reject cross-company reuse of globally unique contact identities."""

        for channel, value in ((Channel.EMAIL, lead.email), (Channel.LINKEDIN, lead.linkedin_url)):
            if not value:
                continue
            normalized_value = normalize_contact_point(channel, value)
            if not normalized_value:
                continue
            point = self.db.query(models.ContactPoint).filter_by(
                owner_id=owner_id,
                channel=channel,
                normalized_value=normalized_value,
            ).first()
            if not point:
                continue
            existing_company = self.db.get(models.Company, point.company_id)
            if existing_company and existing_company.normalized_domain == normalized_domain:
                continue
            if (
                existing_company
                and existing_company.legacy_source_table == "leads"
                and existing_company.legacy_id == str(lead.id)
                and existing_company.normalized_domain is None
            ):
                # Evidence-based enrichment is allowed to upgrade the exact
                # source-scoped placeholder in place. Its SafetyLock remains
                # active until the normal human release workflow is completed.
                continue
            if existing_company is not None:
                self._ensure_company_conflict_lock(
                    owner_id=owner_id,
                    company=existing_company,
                    lead=lead,
                    reason="A stored contact identity now maps to a different evidence-verified company domain.",
                    metadata={
                        "channel": channel.value,
                        "incoming_company_domain": normalized_domain,
                    },
                )
            self.report.quarantine.append(
                QuarantineItem(
                    "leads",
                    lead.id,
                    "contact_identity_company_conflict",
                    {
                        "channel": channel.value,
                        "existing_company_id": point.company_id,
                        "existing_company_domain": (
                            existing_company.normalized_domain if existing_company else None
                        ),
                        "incoming_company_domain": normalized_domain,
                        "company_id": existing_company.id if existing_company else None,
                        "contact_id": point.contact_id,
                        "company_record_visible": existing_company is not None,
                        "outbound_safety_lock": existing_company is not None,
                    },
                )
            )
            return True
        return False

    def _ensure_company_conflict_lock(
        self,
        *,
        owner_id: int,
        company: models.Company,
        lead: legacy.Lead,
        reason: str,
        metadata: dict[str, Any],
    ) -> models.SafetyLock:
        lock = self.db.query(models.SafetyLock).filter_by(
            owner_id=owner_id,
            scope=SafetyLockScope.COMPANY,
            company_id=company.id,
            code="company_identity_conflict",
            active=True,
        ).first()
        if lock is not None:
            self._record(False, lock.__tablename__)
            return lock
        lock = models.SafetyLock(
            owner_id=owner_id,
            scope=SafetyLockScope.COMPANY,
            company_id=company.id,
            code="company_identity_conflict",
            reason=reason,
            active=True,
            metadata_json={
                "legacy_source_table": "leads",
                "legacy_source_id": lead.id,
                "release_requires_human_reconciliation": True,
                **metadata,
            },
        )
        self.db.add(lock)
        self.db.flush()
        self._record(True, lock.__tablename__)
        return lock

    def _ensure_company(
        self,
        lead: legacy.Lead,
        owner_id: int,
        domain: Optional[str],
    ) -> models.Company:
        source_company = _legacy(self.db, models.Company, owner_id, "leads", lead.id)
        if domain:
            domain_company = self.db.query(models.Company).filter_by(
                owner_id=owner_id,
                normalized_domain=domain,
            ).first()
            if source_company is not None:
                if domain_company is not None and domain_company.id != source_company.id:
                    self._ensure_company_conflict_lock(
                        owner_id=owner_id,
                        company=source_company,
                        lead=lead,
                        reason="Evidence-verified domain already belongs to another V2 Company; merge requires human review.",
                        metadata={
                            "domain_company_id": domain_company.id,
                            "verified_domain": domain,
                        },
                    )
                    self.report.quarantine.append(
                        QuarantineItem(
                            "leads",
                            lead.id,
                            "enriched_company_domain_conflict",
                            {
                                "source_company_id": source_company.id,
                                "domain_company_id": domain_company.id,
                                "verified_domain": domain,
                                "company_id": source_company.id,
                                "outbound_safety_lock": True,
                            },
                        )
                    )
                elif source_company.normalized_domain is None:
                    source_company.normalized_domain = domain
                    source_company.website = source_company.website or f"https://{domain}"
                    if (lead.company_name or "").strip() and source_company.name.startswith("Company identity pending ·"):
                        source_company.name = lead.company_name.strip()
                self._record(False, source_company.__tablename__)
                return source_company
            company = domain_company
        else:
            # A name-only or unresolved legacy profile still needs to be visible
            # in V2. Keep it one-to-one with the source Lead so two people are
            # never silently merged merely because their company is unknown.
            company = source_company
            if company is None:
                # Repair rehearsals produced before personal mailbox domains
                # were rejected as Company identities. Reuse the exact
                # identity-linked graph so it can be hard-locked and reviewed;
                # do not create a second empty Company beside the same Contact.
                for channel, value in ((Channel.EMAIL, lead.email), (Channel.LINKEDIN, lead.linkedin_url)):
                    normalized_value = normalize_contact_point(channel, value) if value else None
                    if not normalized_value:
                        continue
                    point = self.db.query(models.ContactPoint).filter_by(
                        owner_id=owner_id,
                        channel=channel,
                        normalized_value=normalized_value,
                    ).first()
                    if point is not None:
                        company = self.db.get(models.Company, point.company_id)
                        if company is not None:
                            break
        if company:
            self._record(False, company.__tablename__)
            return company
        full_name = f"{lead.first_name or ''} {lead.last_name or ''}".strip()
        company_name = (lead.company_name or "").strip()
        if not company_name:
            profile_label = full_name or f"legacy lead {lead.id}"
            company_name = f"Company identity pending · {profile_label}"
        company = models.Company(
            owner_id=owner_id,
            name=company_name or domain or f"Company identity pending · legacy lead {lead.id}",
            normalized_domain=domain,
            website=f"https://{domain}" if domain else None,
            timezone=lead.timezone,
            legacy_source_table="leads",
            legacy_id=str(lead.id),
        )
        self.db.add(company)
        self.db.flush()
        self._record(True, company.__tablename__)
        return company

    def _ensure_company_identity_lock(
        self,
        *,
        owner_id: int,
        company: models.Company,
        lead: legacy.Lead,
    ) -> models.SafetyLock:
        """Keep name-only customer records visible but impossible to contact."""

        lock = self.db.query(models.SafetyLock).filter_by(
            owner_id=owner_id,
            scope=SafetyLockScope.COMPANY,
            company_id=company.id,
            code="company_identity_pending",
        ).first()
        if lock is not None:
            self._record(False, lock.__tablename__)
            return lock
        lock = models.SafetyLock(
            owner_id=owner_id,
            scope=SafetyLockScope.COMPANY,
            company_id=company.id,
            code="company_identity_pending",
            reason=(
                "Legacy customer is visible for enrichment, but its company domain "
                "has not been verified. Outbound remains blocked until human review."
            ),
            active=True,
            metadata_json={
                "legacy_source_table": "leads",
                "legacy_source_id": lead.id,
                "release_requires_verified_company_identity": True,
            },
        )
        self.db.add(lock)
        self.db.flush()
        self._record(True, lock.__tablename__)
        return lock

    def _ensure_public_web_domain_conflict(
        self,
        *,
        lead: legacy.Lead,
        owner_id: int,
        company: models.Company,
    ) -> None:
        """Materialize a public-evidence/stored-domain disagreement safely."""

        brief = self.db.query(legacy.LeadBrief).filter_by(lead_id=lead.id).first()
        flags = brief.quality_flags if brief and isinstance(brief.quality_flags, list) else []
        if "public_web:stored_domain_conflict" not in flags:
            return
        conflicting_domain = next(
            (
                str(flag).split("=", 1)[1]
                for flag in flags
                if str(flag).startswith("public_web:conflicting_domain=")
                and str(flag).split("=", 1)[1]
            ),
            None,
        )
        self._ensure_company_conflict_lock(
            owner_id=owner_id,
            company=company,
            lead=lead,
            reason=(
                "Public evidence points to a different usable company domain; "
                "the stored domain was preserved and requires human reconciliation."
            ),
            metadata={
                "stored_company_domain": company.normalized_domain,
                "public_evidence_domain": conflicting_domain,
            },
        )
        self.report.quarantine.append(
            QuarantineItem(
                "leads",
                lead.id,
                "public_web_company_domain_conflict",
                {
                    "company_id": company.id,
                    "stored_company_domain": company.normalized_domain,
                    "public_evidence_domain": conflicting_domain,
                    "evidence_sources": brief.evidence_sources if brief else [],
                    "company_record_visible": True,
                    "outbound_safety_lock": True,
                },
            )
        )

    def _ensure_contact(
        self,
        lead: legacy.Lead,
        owner_id: int,
        company: models.Company,
    ) -> Optional[models.Contact]:
        existing = _legacy(self.db, models.Contact, owner_id, "leads", lead.id)
        if existing:
            if existing.company_id != company.id:
                self.report.quarantine.append(
                    QuarantineItem(
                        "leads",
                        lead.id,
                        "legacy_contact_company_conflict",
                        {
                            "existing_company_id": existing.company_id,
                            "incoming_company_id": company.id,
                        },
                    )
                )
                return None
            self._record(False, existing.__tablename__)
            return existing
        contact = None
        for channel, value in ((Channel.EMAIL, lead.email), (Channel.LINKEDIN, lead.linkedin_url)):
            if value:
                point = self.db.query(models.ContactPoint).filter_by(
                    owner_id=owner_id,
                    channel=channel,
                    normalized_value=normalize_contact_point(channel, value),
                ).first()
                if point:
                    contact = self.db.get(models.Contact, point.contact_id)
                    break
        full_name = f"{lead.first_name or ''} {lead.last_name or ''}".strip()
        if not contact and full_name:
            contact = self.db.query(models.Contact).filter_by(
                owner_id=owner_id,
                company_id=company.id,
                full_name=full_name,
                job_title=lead.job_title,
            ).first()
        if contact:
            if contact.company_id != company.id:
                self.report.quarantine.append(
                    QuarantineItem(
                        "leads",
                        lead.id,
                        "contact_identity_company_conflict",
                        {
                            "existing_company_id": contact.company_id,
                            "incoming_company_id": company.id,
                        },
                    )
                )
                return None
            self._record(False, contact.__tablename__)
            return contact
        contact = models.Contact(
            owner_id=owner_id,
            company_id=company.id,
            first_name=lead.first_name,
            last_name=lead.last_name,
            full_name=full_name or lead.email or f"Legacy contact {lead.id}",
            job_title=lead.job_title,
            timezone=lead.timezone,
            legacy_source_table="leads",
            legacy_id=str(lead.id),
        )
        self.db.add(contact)
        self.db.flush()
        self._record(True, contact.__tablename__)
        return contact

    def _ensure_points(self, lead: legacy.Lead, owner_id: int, company: models.Company, contact: models.Contact):
        values = [
            (Channel.EMAIL, lead.email),
            (Channel.LINKEDIN, lead.linkedin_url),
            (Channel.WHATSAPP, lead.whatsapp_number),
        ]
        for channel, value in values:
            if not value:
                continue
            normalized = normalize_contact_point(channel, value)
            point = self.db.query(models.ContactPoint).filter_by(
                owner_id=owner_id, channel=channel, normalized_value=normalized
            ).first()
            if point:
                self._record(False, point.__tablename__)
                continue
            verification = ContactPointVerificationStatus.UNVERIFIED
            availability = ContactPointAvailabilityStatus.AVAILABLE
            if channel == Channel.EMAIL:
                status = (lead.email_validation_status or "").lower()
                if status in {"valid", "verified"} or lead.email_verified:
                    verification = ContactPointVerificationStatus.VALID
                elif status == "invalid":
                    verification = ContactPointVerificationStatus.INVALID
                    availability = ContactPointAvailabilityStatus.UNAVAILABLE
                elif status == "catch-all":
                    verification = ContactPointVerificationStatus.CATCH_ALL
            point = models.ContactPoint(
                owner_id=owner_id,
                company_id=company.id,
                contact_id=contact.id,
                channel=channel,
                value=value,
                normalized_value=normalized,
                verification_status=verification,
                availability_status=availability,
                is_primary=channel == Channel.EMAIL,
                legacy_source_table="leads",
                legacy_id=f"{lead.id}:{channel.value}",
            )
            self.db.add(point)
            self._record(True, point.__tablename__)

    def _ensure_list(self, pool_id: int, owner_id: int) -> Optional[models.AudienceList]:
        pool = self.db.get(legacy.ClientPool, pool_id)
        if not pool or pool.user_id != owner_id:
            return None
        row = _legacy(self.db, models.AudienceList, owner_id, "client_pools", pool.id)
        if row:
            self._record(False, row.__tablename__)
            return row
        row = models.AudienceList(
            owner_id=owner_id,
            name=pool.name,
            description=pool.description,
            legacy_source_table="client_pools",
            legacy_id=str(pool.id),
        )
        self.db.add(row)
        self.db.flush()
        self._record(True, row.__tablename__)
        return row

    def _ensure_membership(self, audience: models.AudienceList, contact: models.Contact, lead_id: int, owner_id: int):
        row = self.db.query(models.ListMembership).filter_by(
            audience_list_id=audience.id, contact_id=contact.id
        ).first()
        if row:
            self._record(False, row.__tablename__)
            return
        row = models.ListMembership(
            owner_id=owner_id,
            audience_list_id=audience.id,
            contact_id=contact.id,
            legacy_source_table="leads",
            legacy_id=f"{lead_id}:pool",
        )
        self.db.add(row)
        self._record(True, row.__tablename__)

    def _ensure_campaign(self, workflow_id: int, owner_id: int) -> tuple[Optional[models.Campaign], Optional[models.CampaignRevision]]:
        workflow = self.db.get(legacy.Workflow, workflow_id)
        if not workflow or workflow.user_id != owner_id:
            return None, None
        campaign = _legacy(self.db, models.Campaign, owner_id, "workflows", workflow.id)
        if not campaign:
            lifecycle = CampaignLifecycle.PAUSED if workflow.status == "active" else (
                CampaignLifecycle.COMPLETED if workflow.status == "completed" else CampaignLifecycle.READY
            )
            campaign = models.Campaign(
                owner_id=owner_id,
                name=workflow.name,
                lifecycle=lifecycle,
                run_mode=CampaignRunMode.SHADOW,
                priority=100,
                published_revision_number=1,
                legacy_source_table="workflows",
                legacy_id=str(workflow.id),
            )
            self.db.add(campaign)
            self.db.flush()
            self._record(True, campaign.__tablename__)
        else:
            self._record(False, campaign.__tablename__)
        revision = _legacy(self.db, models.CampaignRevision, owner_id, "workflows", workflow.id)
        if not revision:
            persona = self.db.get(legacy.CustomerPersona, workflow.persona_id) if workflow.persona_id else None
            revision = models.CampaignRevision(
                owner_id=owner_id,
                campaign_id=campaign.id,
                revision_number=1,
                status=CampaignRevisionStatus.PUBLISHED,
                icp_definition={
                    "persona_id": workflow.persona_id,
                    "industry": getattr(persona, "target_industry", None),
                    "countries": getattr(persona, "target_countries", None),
                    "roles": workflow.target_positions,
                },
                audience_definition={"legacy_pool_id": workflow.client_pool_id, "keywords": workflow.search_keywords},
                quality_gates={"min_fit_score": 60, "require_evidence": True, "require_timezone": True},
                budget_definition={"native_limit": workflow.daily_limit, "native_unit": "daily_messages"},
                stop_conditions={"public_unsubscribe_url": "http://127.0.0.1:3000/api/unsubscribe"},
                published_at=workflow.created_at or utcnow(),
                published_by_user_id=owner_id,
                legacy_source_table="workflows",
                legacy_id=str(workflow.id),
            )
            self.db.add(revision)
            self.db.flush()
            template = self.db.get(legacy.EmailTemplate, workflow.template_id) if workflow.template_id else None
            self.db.add(
                models.SequenceStep(
                    owner_id=owner_id,
                    campaign_revision_id=revision.id,
                    position=1,
                    channel=Channel.EMAIL,
                    wait_minutes=0,
                    template_version=f"legacy-template:{workflow.template_id or 'ai'}",
                    subject_template=getattr(template, "subject", None),
                    body_template=getattr(template, "body", None),
                    condition_definition={},
                    stop_condition_definition={"stop_on_reply": True},
                )
            )
            self._record(True, revision.__tablename__)
        else:
            self._record(False, revision.__tablename__)
        return campaign, revision

    def _ensure_enrollment(self, lead: legacy.Lead, owner_id: int, company: models.Company, contact: models.Contact, campaign, revision):
        if not campaign or not revision:
            return None
        row = _legacy(self.db, models.Enrollment, owner_id, "leads", lead.id)
        if row:
            self._record(False, row.__tablename__)
            return row
        row = self.db.query(models.Enrollment).filter_by(
            campaign_id=campaign.id,
            contact_id=contact.id,
        ).first()
        if row:
            # Legacy can contain several Lead rows for the same person in one
            # Workflow, while V2 deliberately permits only one Enrollment for a
            # Campaign/Contact pair. Reuse the first source-ordered Enrollment so
            # messages and evidence from every duplicate Lead are retained, and
            # quarantine the ambiguous Enrollment status for human reconciliation.
            self.report.quarantine.append(
                QuarantineItem(
                    "leads",
                    lead.id,
                    "duplicate_campaign_contact_enrollment",
                    {
                        "campaign_id": campaign.id,
                        "contact_id": contact.id,
                        "canonical_enrollment_id": row.id,
                        "canonical_legacy_id": row.legacy_id,
                    },
                )
            )
            self._record(False, row.__tablename__)
            return row
        status_map = {
            "sent": EnrollmentStatus.ACTIVE,
            "replied": EnrollmentStatus.PAUSED,
            "unsubscribed": EnrollmentStatus.BLOCKED,
            "rejected": EnrollmentStatus.COMPLETED,
            "bounced": EnrollmentStatus.BLOCKED,
        }
        row = models.Enrollment(
            owner_id=owner_id,
            campaign_id=campaign.id,
            campaign_revision_id=revision.id,
            company_id=company.id,
            contact_id=contact.id,
            status=status_map.get(lead.status, EnrollmentStatus.SCHEDULED),
            scheduled_at=lead.created_at or utcnow(),
            priority_snapshot=campaign.priority,
            paused_reason=lead.automation_block_reason,
            positive_signal_at=lead.last_reply_at if lead.has_replied else None,
            legacy_source_table="leads",
            legacy_id=str(lead.id),
        )
        self.db.add(row)
        self.db.flush()
        self._record(True, row.__tablename__)
        return row

    def _ensure_evidence(self, lead: legacy.Lead, owner_id: int, company: models.Company, contact: models.Contact):
        brief = self.db.query(legacy.LeadBrief).filter_by(lead_id=lead.id).first()
        if not brief:
            return
        row = _legacy(self.db, models.EvidenceSnapshot, owner_id, "lead_briefs", brief.id)
        if row:
            self._record(False, row.__tablename__)
            return
        evidence = {
            "company_overview": brief.company_overview,
            "recent_news": brief.recent_news,
            "pain_points": brief.pain_points,
            "value_proposition_alignment": brief.value_proposition_alignment,
            "specific_products": brief.specific_products,
            "recent_activity": brief.recent_activity,
            "personalization_hook": brief.personalization_hook,
            "quality_flags": brief.quality_flags,
            "fit_score": lead.fit_score,
            "fit_grade": lead.fit_grade,
        }
        row = models.EvidenceSnapshot(
            owner_id=owner_id,
            company_id=company.id,
            contact_id=contact.id,
            source="legacy_lead_brief",
            evidence=evidence,
            confidence=0 if brief.research_status != "valid" else 1,
            captured_at=brief.researched_at or brief.created_at or utcnow(),
            legacy_source_table="lead_briefs",
            legacy_id=str(brief.id),
        )
        self.db.add(row)
        self._record(True, row.__tablename__)

    def _ensure_public_web_evidence(self, lead: legacy.Lead, owner_id: int, company: models.Company, contact: models.Contact):
        """Append evidence-first enrichment without rewriting the migration snapshot."""

        brief = self.db.query(legacy.LeadBrief).filter_by(lead_id=lead.id).first()
        flags = brief.quality_flags if brief and isinstance(brief.quality_flags, list) else []
        if not brief or not any(str(flag).startswith("public_web:") for flag in flags):
            return
        evidence_sources = brief.evidence_sources if isinstance(brief.evidence_sources, list) else []
        source_url = next(
            (
                str(item.get("value") or "").strip()
                for item in evidence_sources
                if isinstance(item, dict)
                and str(item.get("value") or "").strip().startswith(("http://", "https://"))
            ),
            None,
        )
        evidence = {
            "company_overview": brief.company_overview,
            "recent_news": brief.recent_news,
            "pain_points": brief.pain_points,
            "value_proposition_alignment": brief.value_proposition_alignment,
            "specific_products": brief.specific_products,
            "recent_activity": brief.recent_activity,
            "personalization_hook": brief.personalization_hook,
            "quality_flags": flags,
            "evidence_sources": evidence_sources,
            "research_status": brief.research_status,
            "fit_score": lead.fit_score,
            "fit_grade": lead.fit_grade,
        }
        prior_rows = self.db.query(models.EvidenceSnapshot).filter(
            models.EvidenceSnapshot.owner_id == owner_id,
            models.EvidenceSnapshot.legacy_source_table == "lead_briefs_public_web",
            or_(
                models.EvidenceSnapshot.legacy_id == str(brief.id),
                models.EvidenceSnapshot.legacy_id.like(f"{brief.id}:%"),
            ),
        ).order_by(models.EvidenceSnapshot.version).all()
        active_match = next(
            (
                row for row in prior_rows
                if row.archived_at is None
                and row.company_id == company.id
                and row.contact_id == contact.id
                and row.source_url == source_url
                and row.evidence == evidence
            ),
            None,
        )
        if active_match is not None:
            self._record(False, active_match.__tablename__)
            return
        evidence_identity = hashlib.sha256(json.dumps(
            {
                "company_id": company.id,
                "contact_id": contact.id,
                "source_url": source_url,
                "evidence": evidence,
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ).encode()).hexdigest()[:20]
        legacy_id = f"{brief.id}:{evidence_identity}"
        same_revision = next((row for row in prior_rows if row.legacy_id == legacy_id), None)
        if same_revision is not None:
            # An archived exact revision stays archived: rollback is an explicit
            # safety decision and must never be undone by a generic replay.
            self._record(False, same_revision.__tablename__)
            return
        row = models.EvidenceSnapshot(
            owner_id=owner_id,
            company_id=company.id,
            contact_id=contact.id,
            source="public_web_enrichment",
            source_url=source_url,
            evidence=evidence,
            confidence=Decimal("1") if brief.research_status == "valid" else Decimal("0"),
            version=max((row.version for row in prior_rows), default=0) + 1,
            captured_at=brief.researched_at or brief.updated_at or utcnow(),
            legacy_source_table="lead_briefs_public_web",
            legacy_id=legacy_id,
        )
        self.db.add(row)
        self._record(True, row.__tablename__)

    def _ensure_messages(self, lead: legacy.Lead, owner_id: int, company: models.Company, contact: models.Contact, enrollment):
        logs = self.db.query(legacy.EmailLog).filter_by(lead_id=lead.id).order_by(legacy.EmailLog.id).all()
        if not logs and not lead.reply_snippet:
            return
        conversation = self.db.query(models.Conversation).filter_by(
            owner_id=owner_id, contact_id=contact.id, channel=Channel.EMAIL
        ).first()
        if not conversation:
            conversation = models.Conversation(
                owner_id=owner_id,
                company_id=company.id,
                contact_id=contact.id,
                channel=Channel.EMAIL,
                status=ConversationStatus.WAITING_ON_US if lead.has_replied else ConversationStatus.WAITING_ON_CONTACT,
                latest_reply_body=lead.reply_snippet,
                last_message_at=lead.last_reply_at,
            )
            self.db.add(conversation)
            self.db.flush()
            self._record(True, conversation.__tablename__)
        point = self.db.query(models.ContactPoint).filter_by(contact_id=contact.id, channel=Channel.EMAIL).first()
        step = None
        if enrollment:
            step = self.db.query(models.SequenceStep).filter_by(campaign_revision_id=enrollment.campaign_revision_id).order_by(models.SequenceStep.position).first()
        for log in logs:
            provider_event_id = f"legacy-email-log:{log.id}"
            if self.db.query(models.MessageEvent).filter_by(owner_id=owner_id, provider="legacy", provider_event_id=provider_event_id).first():
                self._record(False, models.MessageEvent.__tablename__)
                continue
            attempt = None
            if log.direction == "outbound" and enrollment and point and step:
                attempt = self.db.query(models.OutreachAttempt).filter_by(idempotency_key=provider_event_id).first()
                if not attempt:
                    attempt = models.OutreachAttempt(
                        owner_id=owner_id,
                        campaign_id=enrollment.campaign_id,
                        enrollment_id=enrollment.id,
                        sequence_step_id=step.id,
                        contact_point_id=point.id,
                        channel=Channel.EMAIL,
                        idempotency_key=provider_event_id,
                        status=AttemptStatus.SUCCEEDED,
                        provider="legacy",
                        provider_message_id=log.message_id,
                        sent_at=log.sent_at,
                        scheduled_at=log.sent_at,
                    )
                    self.db.add(attempt)
                    self.db.flush()
                    self._record(True, attempt.__tablename__)
            event = models.MessageEvent(
                owner_id=owner_id,
                conversation_id=conversation.id,
                outreach_attempt_id=attempt.id if attempt else None,
                channel=Channel.EMAIL,
                direction=MessageDirection.INBOUND if log.direction == "inbound" else MessageDirection.OUTBOUND,
                event_type=MessageEventType.REPLIED if log.direction == "inbound" else MessageEventType.SENT,
                provider="legacy",
                provider_event_id=provider_event_id,
                provider_message_id=log.message_id,
                subject=log.subject,
                body=log.body,
                latest_body=lead.reply_snippet if log.direction == "inbound" else None,
                occurred_at=log.sent_at,
            )
            self.db.add(event)
            self._record(True, event.__tablename__)
        if lead.has_replied and not self.db.query(models.ReplyAssessment).filter_by(conversation_id=conversation.id).first():
            intent_value = lead.reply_intent if lead.reply_intent in {item.value for item in ReplyIntent} else ReplyIntent.OTHER.value
            assessment = models.ReplyAssessment(
                owner_id=owner_id,
                conversation_id=conversation.id,
                enrollment_id=enrollment.id if enrollment else None,
                intent=ReplyIntent(intent_value),
                is_positive=intent_value in {ReplyIntent.INTERESTED.value, ReplyIntent.MORE_INFO.value},
                status=ReplyAssessmentStatus.PROPOSED,
                latest_reply_body=lead.reply_snippet or "Legacy reply",
                rationale="Imported as a proposal; human confirmation is still required.",
                assessed_by="legacy_import",
            )
            self.db.add(assessment)
            self._record(True, assessment.__tablename__)

    def _process_lead(self, lead: legacy.Lead):
        owner_id, issue = _owner_for_lead(self.db, lead)
        if issue:
            self.report.quarantine.append(QuarantineItem("leads", lead.id, issue))
            return
        quarantine_start = len(self.report.quarantine)
        domain = self._company_domain(lead)
        if domain and self._identity_company_conflict(lead, owner_id, domain):
            return
        company = self._ensure_company(lead, owner_id, domain)
        if not domain:
            self._ensure_company_identity_lock(
                owner_id=owner_id,
                company=company,
                lead=lead,
            )
        else:
            self._ensure_public_web_domain_conflict(
                lead=lead,
                owner_id=owner_id,
                company=company,
            )
        contact = self._ensure_contact(lead, owner_id, company)
        if not contact:
            return
        company_lock_active = self.db.query(models.SafetyLock.id).filter_by(
            owner_id=owner_id,
            scope=SafetyLockScope.COMPANY,
            company_id=company.id,
            active=True,
        ).first() is not None
        for item in self.report.quarantine[quarantine_start:]:
            if item.source_table == "leads" and item.source_id == lead.id:
                item.details.update(
                    {
                        "company_id": company.id,
                        "contact_id": contact.id,
                        "company_record_visible": True,
                        "outbound_safety_lock": company_lock_active,
                    }
                )
        self._ensure_points(lead, owner_id, company, contact)
        if lead.client_pool_id:
            audience = self._ensure_list(lead.client_pool_id, owner_id)
            if audience:
                self._ensure_membership(audience, contact, lead.id, owner_id)
        campaign = revision = None
        if lead.workflow_id:
            campaign, revision = self._ensure_campaign(lead.workflow_id, owner_id)
        enrollment = self._ensure_enrollment(lead, owner_id, company, contact, campaign, revision)
        self._ensure_evidence(lead, owner_id, company, contact)
        self._ensure_public_web_evidence(lead, owner_id, company, contact)
        self._ensure_messages(lead, owner_id, company, contact, enrollment)

    def _migrate_email_accounts(self) -> None:
        """Create credential-free V2 bindings for every owned legacy mailbox."""

        for source in self.db.query(legacy.EmailAccount).order_by(legacy.EmailAccount.id):
            owner_exists = self.db.query(legacy.User.id).filter_by(id=source.user_id).first()
            if owner_exists is None:
                self.report.quarantine.append(
                    QuarantineItem(
                        "email_accounts",
                        source.id,
                        "missing_owner",
                    )
                )
                continue
            existing = self.db.query(models.ChannelAccount).filter_by(
                legacy_email_account_id=source.id,
            ).first()
            daily_limit = (
                existing.daily_limit
                if existing is not None and existing.daily_limit is not None
                else 20
            )
            account_timezone = (
                existing.timezone
                if existing is not None and existing.timezone
                else "UTC"
            )
            account = bind_legacy_email_account(
                self.db,
                owner_id=source.user_id,
                legacy_email_account_id=source.id,
                daily_limit=daily_limit,
                account_timezone=account_timezone,
            )
            self._record(existing is None, account.__tablename__)

    def _migrate_suppressions(self):
        for row in self.db.query(legacy.EmailSuppression).order_by(legacy.EmailSuppression.id):
            owner_id = row.user_id
            if not owner_id and row.lead_id:
                lead = self.db.get(legacy.Lead, row.lead_id)
                owner_id, _ = _owner_for_lead(self.db, lead) if lead else (None, None)
            if not owner_id or not row.email:
                self.report.quarantine.append(QuarantineItem("email_suppressions", row.id, "missing_owner_or_email"))
                continue
            point = self.db.query(models.ContactPoint).filter_by(
                owner_id=owner_id,
                channel=Channel.EMAIL,
                normalized_value=normalize_contact_point(Channel.EMAIL, row.email),
            ).first()
            if not point:
                self.report.quarantine.append(QuarantineItem("email_suppressions", row.id, "email_contact_point_not_found"))
                continue
            key = f"legacy-email-suppression:{row.id}"
            restriction = self.db.query(models.ConsentRestriction).filter_by(idempotency_key=key).first()
            if not restriction:
                restriction = models.ConsentRestriction(
                    owner_id=owner_id,
                    idempotency_key=key,
                    scope=RestrictionScope.CONTACT_POINT,
                    channel=Channel.EMAIL,
                    contact_point_id=point.id,
                    contact_id=point.contact_id,
                    company_id=point.company_id,
                    reason=row.reason,
                    source=f"legacy:{row.source}",
                    metadata_json={"legacy_domain": row.domain, "domain_scope_applied": False},
                )
                self.db.add(restriction)
                self._record(True, restriction.__tablename__)
            if row.domain and not self.db.query(models.Task).filter_by(
                owner_id=owner_id,
                title="Review legacy domain suppression scope",
                company_id=point.company_id,
                contact_id=point.contact_id,
            ).first():
                self.db.add(
                    models.Task(
                        owner_id=owner_id,
                        task_type=TaskType.DATA_GOVERNANCE,
                        queue_scope=TaskQueueScope.DATA_GOVERNANCE,
                        status=TaskStatus.OPEN,
                        priority=TaskPriority.HIGH,
                        company_id=point.company_id,
                        contact_id=point.contact_id,
                        title="Review legacy domain suppression scope",
                        description=f"Legacy suppression {row.id} contained both email and domain. Only email scope was migrated.",
                        metadata_json={"legacy_suppression_id": row.id, "legacy_domain": row.domain},
                    )
                )

    def _migrate_provider_costs(self):
        for row in self.db.query(legacy.ProviderUsageEvent).order_by(legacy.ProviderUsageEvent.id):
            key = f"legacy-provider-usage:{row.id}"
            if self.db.query(models.ProviderCostEvent).filter_by(idempotency_key=key).first():
                continue
            owner_id = None
            workflow_owner_id = None
            lead_owner_id = None
            campaign = None
            contact = None
            company = None
            enrollment = None
            if row.workflow_id:
                workflow = self.db.get(legacy.Workflow, row.workflow_id)
                workflow_owner_id = workflow.user_id if workflow else None
            if row.lead_id:
                lead = self.db.get(legacy.Lead, row.lead_id)
                if lead:
                    lead_owner_id, lead_owner_issue = _owner_for_lead(self.db, lead)
                    if lead_owner_issue:
                        self.report.quarantine.append(
                            QuarantineItem(
                                "provider_usage_events",
                                row.id,
                                "lead_owner_unresolved",
                                {"lead_id": lead.id, "lead_owner_issue": lead_owner_issue},
                            )
                        )
                        continue
            if workflow_owner_id and lead_owner_id and workflow_owner_id != lead_owner_id:
                self.report.quarantine.append(
                    QuarantineItem(
                        "provider_usage_events",
                        row.id,
                        "workflow_lead_owner_conflict",
                        {
                            "workflow_id": row.workflow_id,
                            "workflow_owner_id": workflow_owner_id,
                            "lead_id": row.lead_id,
                            "lead_owner_id": lead_owner_id,
                        },
                    )
                )
                continue
            owner_id = workflow_owner_id or lead_owner_id
            if not owner_id:
                self.report.quarantine.append(QuarantineItem("provider_usage_events", row.id, "missing_owner"))
                continue
            if row.workflow_id:
                campaign = _legacy(self.db, models.Campaign, owner_id, "workflows", row.workflow_id)
            if row.lead_id:
                contact = _legacy(self.db, models.Contact, owner_id, "leads", row.lead_id)
                company = self.db.get(models.Company, contact.company_id) if contact else None
                enrollment = _legacy(self.db, models.Enrollment, owner_id, "leads", row.lead_id)
            event = models.ProviderCostEvent(
                owner_id=owner_id,
                provider=row.provider,
                operation=row.operation,
                status=ProviderCostStatus.CHARGED if row.status == "success" else ProviderCostStatus.FAILED,
                units=row.estimated_credits if row.estimated_credits is not None else row.units,
                native_unit="credits" if row.estimated_credits is not None else "calls",
                unit_price=None,
                normalized_amount=None,
                normalized_currency=None,
                result_count=row.result_count,
                billable=row.estimated_credits is not None,
                price_version="legacy-unknown",
                campaign_id=campaign.id if campaign else None,
                enrollment_id=enrollment.id if enrollment else None,
                company_id=company.id if company else None,
                contact_id=contact.id if contact else None,
                idempotency_key=key,
                metadata_json={"legacy_status": row.status, "pricing_unknown": True},
            )
            self.db.add(event)
            self._record(True, event.__tablename__)

    def _materialize_quarantine_tasks(self) -> None:
        """Expose owner-resolvable quarantine items in the admin governance queue.

        The restricted JSON artifact remains the migration evidence of record.
        Tasks carry only non-secret references so operators can resolve every
        customer omission without inventing a company identity or losing the
        source Lead during cutover.
        """

        for item in self.report.quarantine:
            if item.source_table != "leads":
                continue
            lead = self.db.get(legacy.Lead, item.source_id)
            if not lead:
                continue
            owner_id, owner_issue = _owner_for_lead(self.db, lead)
            if owner_issue or not owner_id:
                continue

            identity_issue = item.reason in {
                "missing_company_identity",
                "invalid_company_domain",
            }
            task_type = TaskType.DATA_GOVERNANCE
            title = (
                f"Complete legacy lead {lead.id} company identity"
                if identity_issue
                else f"Reconcile legacy lead {lead.id}: {item.reason}"
            )
            existing = self.db.query(models.Task).filter_by(
                owner_id=owner_id,
                title=title,
            ).first()
            details = item.details or {}

            def owned_reference(model, key: str) -> int | None:
                value = details.get(key)
                if not isinstance(value, int) or isinstance(value, bool):
                    return None
                row = self.db.query(model.id).filter(
                    model.id == value,
                    model.owner_id == owner_id,
                ).first()
                return value if row else None

            company_id = owned_reference(models.Company, "company_id")
            contact_id = owned_reference(models.Contact, "contact_id")
            if existing:
                # Older rehearsals created the Task before name-only customer
                # records became visible. Repair only missing owned references;
                # never rewrite an operator's existing assignment or decision.
                if existing.company_id is None:
                    existing.company_id = company_id
                if existing.contact_id is None:
                    existing.contact_id = contact_id
                existing.metadata_json = {
                    **(existing.metadata_json or {}),
                    "quarantine_details": details,
                }
                existing.task_type = TaskType.DATA_GOVERNANCE
                existing.queue_scope = TaskQueueScope.DATA_GOVERNANCE
                self.db.flush()
                self._record(False, existing.__tablename__)
                continue

            task = models.Task(
                owner_id=owner_id,
                task_type=task_type,
                queue_scope=TaskQueueScope.DATA_GOVERNANCE,
                status=TaskStatus.OPEN,
                priority=TaskPriority.HIGH if identity_issue else TaskPriority.NORMAL,
                company_id=company_id,
                contact_id=contact_id,
                campaign_id=owned_reference(models.Campaign, "campaign_id"),
                enrollment_id=owned_reference(models.Enrollment, "canonical_enrollment_id"),
                title=title,
                description=(
                    "The customer is visible in V2 under an outbound SafetyLock, but a usable company domain "
                    "is missing or invalid. Review the saved public profile evidence and resolve the company "
                    "identity; do not guess."
                    if identity_issue
                    else "The legacy source maps ambiguously to a V2 record. Review the preserved source and "
                    "canonical V2 references before disposition."
                ),
                metadata_json={
                    "legacy_source_table": item.source_table,
                    "legacy_source_id": item.source_id,
                    "quarantine_reason": item.reason,
                    "quarantine_details": details,
                },
            )
            self.db.add(task)
            self.db.flush()
            self._record(True, task.__tablename__)

    @staticmethod
    def _checksum_value(value: Any) -> Any:
        if isinstance(value, Enum):
            return value.value
        if isinstance(value, Decimal):
            return format(value, "f")
        if isinstance(value, dict):
            return {
                str(key): ProductV2Backfill._checksum_value(nested)
                for key, nested in sorted(value.items(), key=lambda item: str(item[0]))
            }
        if isinstance(value, (list, tuple)):
            return [ProductV2Backfill._checksum_value(item) for item in value]
        return value

    def checksum(self) -> str:
        """Hash stable V2 business state, excluding generated ids and time.

        A rolled-back MySQL dry-run still consumes AUTO_INCREMENT values.  Raw
        V2 foreign-key integers therefore cannot be part of a repeatability
        checksum.  Replace each V2 FK with a digest of the referenced row's
        stable natural identity while retaining legacy/user foreign keys.
        """

        table_rows: dict[str, list[dict[str, Any]]] = {}
        rows_by_pk: dict[str, dict[Any, dict[str, Any]]] = {}
        for table_name in sorted(models.V2_TABLE_NAMES):
            table = models.Base.metadata.tables[table_name]
            rows = [dict(row) for row in self.db.execute(table.select()).mappings()]
            table_rows[table_name] = rows
            primary_columns = list(table.primary_key.columns)
            if len(primary_columns) == 1:
                primary_name = primary_columns[0].name
                rows_by_pk[table_name] = {
                    row[primary_name]: row for row in rows
                }

        identity_cache: dict[tuple[str, Any], str] = {}

        def transformed_value(table_name: str, column, value: Any, stack: frozenset) -> Any:
            if value is None:
                return None
            for foreign_key in column.foreign_keys:
                target_table = foreign_key.column.table.name
                if target_table in models.V2_TABLE_NAMES:
                    return row_identity(target_table, value, stack)
            return self._checksum_value(value)

        def row_identity(table_name: str, primary_value: Any, stack: frozenset) -> str:
            cache_key = (table_name, primary_value)
            if cache_key in identity_cache:
                return identity_cache[cache_key]
            table = models.Base.metadata.tables[table_name]
            row = rows_by_pk.get(table_name, {}).get(primary_value)
            if row is None:
                return f"{table_name}:missing-reference"
            if cache_key in stack:
                # No migrated graph currently contains a V2 FK cycle.  Keep a
                # deterministic fail-closed marker if one is introduced later.
                return f"{table_name}:cyclic-reference"
            nested_stack = stack | {cache_key}
            columns = {column.name: column for column in table.c}
            candidate: dict[str, Any] = {}

            if row.get("legacy_source_table") and row.get("legacy_id"):
                for name in ("owner_id", "legacy_source_table", "legacy_id"):
                    if name in columns:
                        candidate[name] = transformed_value(
                            table_name,
                            columns[name],
                            row.get(name),
                            nested_stack,
                        )
            else:
                for name in (
                    "idempotency_key",
                    "ingest_idempotency_key",
                    "normalized_value_hash",
                    "worker_name",
                ):
                    if row.get(name) is not None:
                        candidate = {
                            "owner_id": self._checksum_value(row.get("owner_id")),
                            name: self._checksum_value(row[name]),
                        }
                        break

            if not candidate:
                unique_constraints = sorted(
                    (
                        tuple(sorted(column.name for column in constraint.columns))
                        for constraint in table.constraints
                        if constraint.__class__.__name__ == "UniqueConstraint"
                    ),
                    key=lambda names: (len(names), names),
                )
                for names in unique_constraints:
                    if names and all(row.get(name) is not None for name in names):
                        candidate = {
                            name: transformed_value(
                                table_name,
                                columns[name],
                                row[name],
                                nested_stack,
                            )
                            for name in names
                        }
                        break

            if not candidate:
                candidate = {
                    column.name: transformed_value(
                        table_name,
                        column,
                        row.get(column.name),
                        nested_stack,
                    )
                    for column in table.c
                    if not column.primary_key
                    and not isinstance(column.type, (Date, DateTime))
                }
            canonical = json.dumps(
                candidate,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            token = f"{table_name}:{hashlib.sha256(canonical.encode()).hexdigest()}"
            identity_cache[cache_key] = token
            return token

        digest = hashlib.sha256()
        for table_name in sorted(models.V2_TABLE_NAMES):
            table = models.Base.metadata.tables[table_name]
            rows = table_rows[table_name]
            digest.update(f"{table_name}:{len(rows)}\n".encode())
            stable_columns = [
                column
                for column in table.c
                if not column.primary_key and not isinstance(column.type, (Date, DateTime))
            ]
            canonical_rows = []
            for row in rows:
                payload = {
                    column.name: transformed_value(
                        table_name,
                        column,
                        row.get(column.name),
                        frozenset(),
                    )
                    for column in stable_columns
                }
                canonical_rows.append(
                    json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
                )
            for canonical_row in sorted(canonical_rows):
                digest.update((canonical_row + "\n").encode())
        return digest.hexdigest()

    def run(self) -> BackfillReport:
        last_id = self.start_after
        while True:
            # Keyset pagination is deliberate.  MySQL/PyMySQL cannot safely
            # keep an unbuffered ``yield_per`` cursor open while _process_lead
            # issues queries on the same connection; doing so silently stopped
            # after the first driver buffer and made small-batch resumes skip
            # source rows.
            batch = (
                self.db.query(legacy.Lead)
                .filter(legacy.Lead.id > last_id)
                .order_by(legacy.Lead.id)
                .limit(self.batch_size)
                .all()
            )
            if not batch:
                break
            for lead in batch:
                self._process_lead(lead)
                self.report.processed_leads += 1
                last_id = lead.id
            if self.apply:
                self.db.commit()
                self._checkpoint(last_id)
        self._migrate_suppressions()
        self._migrate_provider_costs()
        self._migrate_email_accounts()
        self._materialize_quarantine_tasks()
        if self.apply:
            self.db.commit()
            self._checkpoint(last_id)
        self.report.checksum = self.checksum()
        if not self.apply:
            self.db.rollback()
        self._dedupe_quarantine()
        return self.report
