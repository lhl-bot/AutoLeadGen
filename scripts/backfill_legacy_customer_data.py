#!/usr/bin/env python3
"""Conservatively complete legacy customer records without external calls.

The command defaults to a read-only dry-run.  It only derives values from
existing database evidence, skips ambiguous matches, never changes outbound
status/drafts/email addresses, and refuses to run while any populated workflow
can send automatically.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sys
from typing import Any
from urllib.parse import urlparse


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from database import SessionLocal, engine
import models
from services.lead_scoring import score_lead_fit
from services.research_quality import is_usable_company_domain, normalize_domain
from services.timezone_resolver import guess_timezone_from_domain


PROTECTED_LEAD_IDS = {1978}
EMAIL_SYNTAX = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
MUTABLE_LEAD_FIELDS = (
    "company_name",
    "domain",
    "email_validation_status",
    "email_verified",
    "timezone",
    "fit_score",
    "fit_grade",
    "qualification_notes",
    "handoff_recommended",
    "data_sources",
)
BRIEF_FIELDS = (
    "company_overview",
    "recent_news",
    "pain_points",
    "value_proposition_alignment",
    "specific_products",
    "recent_activity",
    "personalization_hook",
    "research_status",
    "quality_flags",
    "evidence_sources",
    "researched_at",
)


def _blank(value: Any) -> bool:
    return not str(value or "").strip()


def _company_key(value: str | None) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", (value or "").lower()))


def _linkedin_key(value: str | None) -> str:
    text = (value or "").strip().lower()
    if not text:
        return ""
    parsed = urlparse(text if "://" in text else f"https://{text}")
    host = parsed.netloc.removeprefix("www.")
    if host.endswith(".linkedin.com"):
        host = "linkedin.com"
    return f"{host}{parsed.path.rstrip('/')}"


def _database_fingerprint() -> str:
    url = engine.url
    identity = "|".join(
        (
            url.drivername or "",
            url.host or "",
            str(url.port or ""),
            url.database or "",
        )
    )
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()


def _canonical_name(leads: list[models.Lead]) -> str | None:
    names = [str(lead.company_name or "").strip() for lead in leads]
    names = [name for name in names if _company_key(name)]
    normalized = {_company_key(name) for name in names}
    if len(normalized) != 1:
        return None
    frequencies = Counter(names)
    return sorted(frequencies, key=lambda name: (-frequencies[name], name.lower()))[0]


def _strict_valid_brief(brief: models.LeadBrief | None) -> bool:
    return bool(
        brief
        and brief.research_status == "valid"
        and not _blank(brief.company_overview)
        and not _blank(brief.specific_products)
        and not _blank(brief.personalization_hook)
        and brief.evidence_sources
    )


def _brief_fingerprint(brief: models.LeadBrief) -> str:
    payload = {
        field_name: getattr(brief, field_name)
        for field_name in BRIEF_FIELDS
        if field_name != "researched_at"
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()


def _snapshot_lead(lead: models.Lead) -> dict[str, Any]:
    return {
        "id": lead.id,
        "updated_at": lead.updated_at,
        **{field_name: deepcopy(getattr(lead, field_name)) for field_name in MUTABLE_LEAD_FIELDS},
    }


def _snapshot_brief(brief: models.LeadBrief | None) -> dict[str, Any] | None:
    if brief is None:
        return None
    return {
        "id": brief.id,
        "lead_id": brief.lead_id,
        "updated_at": brief.updated_at,
        **{field_name: deepcopy(getattr(brief, field_name)) for field_name in BRIEF_FIELDS},
    }


def _completeness(leads: list[models.Lead], briefs: dict[int, models.LeadBrief]) -> dict[str, int]:
    return {
        "total": len(leads),
        "company_name": sum(not _blank(lead.company_name) for lead in leads),
        "domain": sum(is_usable_company_domain(normalize_domain(lead.domain)) for lead in leads),
        "timezone": sum(not _blank(lead.timezone) for lead in leads),
        "email_status": sum(not _blank(lead.email_validation_status) for lead in leads),
        "data_sources": sum(not _blank(lead.data_sources) for lead in leads),
        "fit_score": sum(lead.fit_score is not None for lead in leads),
        "qualification_notes": sum(not _blank(lead.qualification_notes) for lead in leads),
        "strict_valid_brief": sum(_strict_valid_brief(briefs.get(lead.id)) for lead in leads),
    }


@dataclass
class BackfillPlan:
    counters: Counter[str] = field(default_factory=Counter)
    conflicts: Counter[str] = field(default_factory=Counter)
    before_leads: dict[int, dict[str, Any]] = field(default_factory=dict)
    before_briefs: dict[int, dict[str, Any] | None] = field(default_factory=dict)
    before_completeness: dict[str, int] = field(default_factory=dict)
    after_completeness: dict[str, int] = field(default_factory=dict)

    @property
    def affected_lead_ids(self) -> list[int]:
        return sorted(self.before_leads)

    def record_before(self, lead: models.Lead, brief: models.LeadBrief | None) -> None:
        if lead.id not in self.before_leads:
            self.before_leads[lead.id] = _snapshot_lead(lead)
            self.before_briefs[lead.id] = _snapshot_brief(brief)

    def report(self) -> dict[str, Any]:
        return {
            "affected_leads": len(self.before_leads),
            "field_updates": dict(sorted(self.counters.items())),
            "ambiguous_matches_skipped": dict(sorted(self.conflicts.items())),
            "protected_leads_skipped": sorted(PROTECTED_LEAD_IDS),
            "before": self.before_completeness,
            "after": self.after_completeness,
        }


def _assert_outbound_safe(workflows: list[models.Workflow], leads: list[models.Lead]) -> None:
    populated_workflow_ids = {lead.workflow_id for lead in leads if lead.workflow_id is not None}
    unsafe = [
        workflow.id
        for workflow in workflows
        if workflow.id in populated_workflow_ids
        and workflow.status == "active"
        and not bool(workflow.email_sending_paused)
    ]
    if unsafe:
        raise RuntimeError(f"active workflows are not email-paused: {unsafe}")


def plan_backfill(db) -> BackfillPlan:
    leads = db.query(models.Lead).order_by(models.Lead.id).all()
    workflows = db.query(models.Workflow).order_by(models.Workflow.id).all()
    _assert_outbound_safe(workflows, leads)

    workflow_by_id = {workflow.id: workflow for workflow in workflows}
    persona_ids = {workflow.persona_id for workflow in workflows if workflow.persona_id}
    personas = (
        {
            persona.id: persona
            for persona in db.query(models.CustomerPersona)
            .filter(models.CustomerPersona.id.in_(persona_ids))
            .all()
        }
        if persona_ids
        else {}
    )
    brief_by_lead_id = {
        brief.lead_id: brief
        for brief in db.query(models.LeadBrief).order_by(models.LeadBrief.id).all()
    }

    by_domain: dict[str, list[models.Lead]] = defaultdict(list)
    by_company: dict[str, list[models.Lead]] = defaultdict(list)
    by_linkedin: dict[str, list[models.Lead]] = defaultdict(list)
    for lead in leads:
        domain = normalize_domain(lead.domain)
        company = _company_key(lead.company_name)
        linkedin = _linkedin_key(lead.linkedin_url)
        if domain:
            by_domain[domain].append(lead)
        if company:
            by_company[company].append(lead)
        if linkedin:
            by_linkedin[linkedin].append(lead)

    valid_briefs_by_domain: dict[str, dict[str, models.LeadBrief]] = defaultdict(dict)
    for domain, domain_leads in by_domain.items():
        for lead in domain_leads:
            brief = brief_by_lead_id.get(lead.id)
            if _strict_valid_brief(brief):
                valid_briefs_by_domain[domain][_brief_fingerprint(brief)] = brief

    plan = BackfillPlan()
    plan.before_completeness = _completeness(leads, brief_by_lead_id)

    with db.no_autoflush:
        for lead in leads:
            if lead.id in PROTECTED_LEAD_IDS:
                continue
            brief = brief_by_lead_id.get(lead.id)
            original_lead = _snapshot_lead(lead)
            original_brief = _snapshot_brief(brief)

            domain = normalize_domain(lead.domain)
            if not domain:
                candidates: set[str] = set()
                email = str(lead.email or "").strip().lower()
                email_domain = email.rsplit("@", 1)[-1] if "@" in email else ""
                if is_usable_company_domain(email_domain):
                    candidates.add(email_domain)
                company = _company_key(lead.company_name)
                if company:
                    candidates.update(
                        normalize_domain(item.domain)
                        for item in by_company[company]
                        if is_usable_company_domain(normalize_domain(item.domain))
                    )
                linkedin = _linkedin_key(lead.linkedin_url)
                if linkedin:
                    candidates.update(
                        normalize_domain(item.domain)
                        for item in by_linkedin[linkedin]
                        if is_usable_company_domain(normalize_domain(item.domain))
                    )
                if len(candidates) == 1:
                    domain = next(iter(candidates))
                    lead.domain = domain
                    plan.counters["domain"] += 1
                elif len(candidates) > 1:
                    plan.conflicts["domain"] += 1

            if _blank(lead.company_name) and domain:
                canonical_name = _canonical_name(by_domain.get(domain, []))
                if canonical_name:
                    lead.company_name = canonical_name
                    plan.counters["company_name"] += 1
                elif by_domain.get(domain):
                    plan.conflicts["company_name"] += 1

            if _blank(lead.timezone) and domain:
                inferred_timezone = guess_timezone_from_domain(domain)
                if inferred_timezone:
                    lead.timezone = inferred_timezone
                    plan.counters["timezone"] += 1

            if _blank(lead.data_sources) and not _blank(lead.source_channel):
                lead.data_sources = str(lead.source_channel).strip()
                plan.counters["data_sources"] += 1

            if _blank(lead.email_validation_status):
                lead.email_validation_status = "no_email" if _blank(lead.email) else "unknown"
                lead.email_verified = False
                plan.counters["email_validation_status"] += 1

            normalized_email = str(lead.email or "").strip()
            if (
                normalized_email
                and not EMAIL_SYNTAX.fullmatch(normalized_email)
                and lead.email_validation_status != "invalid"
            ):
                lead.email_validation_status = "invalid"
                lead.email_verified = False
                plan.counters["malformed_email_safely_invalidated"] += 1

            source_briefs = valid_briefs_by_domain.get(domain, {}) if domain else {}
            if brief is None and len(source_briefs) == 1:
                source = next(iter(source_briefs.values()))
                brief = models.LeadBrief(lead=lead)
                for field_name in BRIEF_FIELDS:
                    setattr(brief, field_name, deepcopy(getattr(source, field_name)))
                quality_flags = list(brief.quality_flags or [])
                if "backfilled:same_company_domain" not in quality_flags:
                    quality_flags.append("backfilled:same_company_domain")
                brief.quality_flags = quality_flags
                db.add(brief)
                brief_by_lead_id[lead.id] = brief
                plan.counters["strict_valid_brief"] += 1
            elif brief is None and len(source_briefs) > 1:
                plan.conflicts["brief"] += 1

            if (
                lead.fit_score is None
                or _blank(lead.fit_grade)
                or _blank(lead.qualification_notes)
            ):
                workflow = workflow_by_id.get(lead.workflow_id)
                persona = personas.get(workflow.persona_id) if workflow else None
                score = score_lead_fit(lead, workflow=workflow, persona=persona, brief=brief)
                lead.fit_score = score.score
                lead.fit_grade = score.grade
                lead.qualification_notes = score.notes
                lead.handoff_recommended = score.handoff_recommended
                plan.counters["fit_score_bundle"] += 1

            after_lead = _snapshot_lead(lead)
            after_brief = _snapshot_brief(brief)
            if after_lead != original_lead or after_brief != original_brief:
                plan.before_leads[lead.id] = original_lead
                plan.before_briefs[lead.id] = original_brief

    plan.after_completeness = _completeness(leads, brief_by_lead_id)
    return plan


def _inside(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _write_backup(plan: BackfillPlan, fingerprint: str, path: Path) -> None:
    backup_root = (ROOT / ".local" / "backfill").resolve()
    resolved = path.resolve()
    if not _inside(resolved, backup_root):
        raise RuntimeError("backup path must stay inside .local/backfill")
    resolved.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "created_at": datetime.now(timezone.utc),
        "database_fingerprint": fingerprint,
        "affected_lead_ids": plan.affected_lead_ids,
        "leads_before": plan.before_leads,
        "briefs_before": plan.before_briefs,
    }
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    descriptor = os.open(resolved, flags, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2, default=str)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--apply", action="store_true")
    parser.add_argument("--expected-total", type=int)
    parser.add_argument("--expected-database-fingerprint")
    parser.add_argument("--backup", type=Path)
    return parser


def main() -> int:
    args = _parser().parse_args()
    fingerprint = _database_fingerprint()
    db = SessionLocal()
    backup_path: Path | None = None
    try:
        total = db.query(models.Lead).count()
        if args.expected_total is not None and total != args.expected_total:
            raise SystemExit(f"lead total mismatch: expected {args.expected_total}, observed {total}")
        if args.apply:
            expected = str(args.expected_database_fingerprint or "").strip().lower()
            if len(expected) != 64 or expected != fingerprint:
                raise SystemExit("--apply requires the reviewed database fingerprint")
            if args.expected_total is None:
                raise SystemExit("--apply requires --expected-total")

        plan = plan_backfill(db)
        if args.apply:
            stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            backup_path = args.backup or Path(f".local/backfill/legacy-customer-{stamp}.json")
            _write_backup(plan, fingerprint, backup_path)
            db.commit()
            status = "applied"
        else:
            db.rollback()
            status = "dry_run"

        print(
            json.dumps(
                {
                    "status": status,
                    "database_fingerprint": fingerprint,
                    "external_calls": False,
                    "outbound_messages_sent": 0,
                    "backup": str(backup_path.resolve()) if backup_path else None,
                    "report": plan.report(),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0
    finally:
        db.rollback()
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
