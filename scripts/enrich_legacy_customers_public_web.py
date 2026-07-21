#!/usr/bin/env python3
"""Collect and apply evidence-first public customer profiles.

Collection is resumable and does not write the database.  Apply is identity-
bound, creates a private before-image backup, keeps outbound automation paused,
and never changes a lead's email address, draft, or outreach status.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import time
from typing import Any

from sqlalchemy.exc import OperationalError


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from database import SessionLocal, engine
import models
from services.lead_scoring import score_lead_fit
from services.public_web_enrichment import (
    CompanyEvidence,
    build_brief_data,
    collect_search_index_evidence,
    domain_company_affinity,
    resolve_company_domain_public,
)
from services.research_quality import is_usable_company_domain, normalize_domain, utcnow


DEFAULT_CHECKPOINT = ROOT / ".local" / "enrichment" / "public-customer-evidence.json"
CHECKPOINT_VERSION = 1
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
LEAD_FIELDS = (
    "company_name",
    "domain",
    "timezone",
    "fit_score",
    "fit_grade",
    "qualification_notes",
    "handoff_recommended",
    "data_sources",
    "updated_at",
)


def _blank(value: Any) -> bool:
    return not str(value or "").strip()


def _company_key(value: str | None) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", (value or "").lower()))


def _database_fingerprint() -> str:
    url = engine.url
    identity = "|".join((url.drivername or "", url.host or "", str(url.port or ""), url.database or ""))
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()


def _session_with_retry():
    for attempt in range(5):
        db = SessionLocal()
        try:
            db.query(models.Lead.id).limit(1).all()
            return db
        except OperationalError:
            db.close()
            if attempt == 4:
                raise
            time.sleep(attempt + 1)
    raise RuntimeError("database unavailable")


def _strict_valid(brief: models.LeadBrief | None) -> bool:
    return bool(
        brief
        and brief.research_status == "valid"
        and not _blank(brief.company_overview)
        and not _blank(brief.specific_products)
        and not _blank(brief.personalization_hook)
        and brief.evidence_sources
    )


def _public_profile_complete(brief: models.LeadBrief | None) -> bool:
    flags = brief.quality_flags if brief and isinstance(brief.quality_flags, list) else []
    return bool(
        brief
        and any(str(flag).startswith("public_web:") for flag in flags)
        and not _blank(brief.company_overview)
        and not _blank(brief.pain_points)
        and not _blank(brief.value_proposition_alignment)
        and brief.evidence_sources
    )


def _target_terms(workflow: models.Workflow | None) -> list[str]:
    values = []
    if workflow:
        values.extend((workflow.search_keywords, workflow.product_focus, workflow.target_customer_type))
    terms: list[str] = []
    for value in values:
        for part in re.split(r"[,;|/\n]+", value or ""):
            cleaned = re.sub(r"\s+", " ", part).strip().lower()
            if len(cleaned) >= 3 and cleaned not in terms:
                terms.append(cleaned)
    return terms[:12]


def _append_source(current: str | None, value: str) -> str:
    items = [part.strip() for part in (current or "").split(",") if part.strip()]
    if value not in items:
        items.append(value)
    return ",".join(items)


def _load_checkpoint(path: Path) -> dict:
    if not path.exists():
        return {
            "version": CHECKPOINT_VERSION,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "domains": {},
            "companies": {},
        }
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("version") != CHECKPOINT_VERSION:
        raise RuntimeError("unsupported checkpoint version")
    payload.setdefault("domains", {})
    payload.setdefault("companies", {})
    return payload


def _write_json_private(path: Path, payload: dict, *, replace: bool) -> None:
    resolved = path.resolve()
    allowed_root = (ROOT / ".local").resolve()
    try:
        resolved.relative_to(allowed_root)
    except ValueError as exc:
        raise RuntimeError("output must stay inside .local") from exc
    resolved.parent.mkdir(parents=True, exist_ok=True)
    if replace:
        temporary = resolved.with_suffix(resolved.suffix + ".tmp")
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2, default=str)
        os.replace(temporary, resolved)
        os.chmod(resolved, 0o600)
    else:
        descriptor = os.open(resolved, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2, default=str)


def _collection_work() -> tuple[list[dict], list[dict], dict[str, int]]:
    db = _session_with_retry()
    try:
        leads = db.query(models.Lead).order_by(models.Lead.id).all()
        workflows = {workflow.id: workflow for workflow in db.query(models.Workflow).all()}
        briefs = {brief.lead_id: brief for brief in db.query(models.LeadBrief).all()}
        domains: dict[str, dict] = {}
        companies: dict[str, dict] = {}
        route_counts: Counter[str] = Counter()
        for lead in leads:
            if _strict_valid(briefs.get(lead.id)):
                continue
            workflow = workflows.get(lead.workflow_id)
            terms = _target_terms(workflow)
            domain = normalize_domain(lead.domain)
            company_key = _company_key(lead.company_name)
            if is_usable_company_domain(domain):
                item = domains.setdefault(domain, {
                    "key": domain,
                    "company_name": str(lead.company_name or "").strip(),
                    "target_terms": [],
                    "workflow_ids": [],
                })
                item["target_terms"] = sorted(set(item["target_terms"] + terms))[:20]
                if lead.workflow_id is not None:
                    item["workflow_ids"] = sorted(set(item["workflow_ids"] + [lead.workflow_id]))
                if not item["company_name"] and lead.company_name:
                    item["company_name"] = str(lead.company_name).strip()
                route_counts["domain"] += 1
            elif company_key:
                item = companies.setdefault(company_key, {
                    "key": company_key,
                    "company_name": str(lead.company_name or "").strip(),
                    "target_terms": [],
                    "workflow_ids": [],
                })
                item["target_terms"] = sorted(set(item["target_terms"] + terms))[:20]
                if lead.workflow_id is not None:
                    item["workflow_ids"] = sorted(set(item["workflow_ids"] + [lead.workflow_id]))
                route_counts["company"] += 1
            else:
                route_counts["profile_fallback"] += 1
        priority = lambda item: (
            0 if 18 in item["workflow_ids"] else
            1 if 3 in item["workflow_ids"] else 2,
            item["key"],
        )
        return sorted(domains.values(), key=priority), sorted(companies.values(), key=priority), dict(route_counts)
    finally:
        db.rollback()
        db.close()


def collect(
    checkpoint_path: Path,
    *,
    routes: str,
    workers: int,
    limit: int | None,
    retry_failures: bool,
    retry_status: str | None = None,
) -> dict:
    checkpoint = _load_checkpoint(checkpoint_path)
    domain_work, company_work, route_counts = _collection_work()
    company_work_by_key = {item["key"]: item for item in company_work}
    for item in domain_work:
        state = checkpoint.get("domains", {}).get(item["key"], {})
        if state.get("collection_status") in {"collected", "search_index"}:
            continue
        company_key = _company_key(item.get("company_name"))
        if not company_key:
            continue
        existing = company_work_by_key.get(company_key)
        if existing:
            existing["target_terms"] = sorted(set(existing["target_terms"] + item["target_terms"]))[:20]
            existing["workflow_ids"] = sorted(set(existing["workflow_ids"] + item["workflow_ids"]))
        else:
            company_work_by_key[company_key] = {
                "key": company_key,
                "company_name": item["company_name"],
                "target_terms": item["target_terms"],
                "workflow_ids": item["workflow_ids"],
            }
    company_work = sorted(
        company_work_by_key.values(),
        key=lambda item: (
            0 if 18 in item["workflow_ids"] else
            1 if 3 in item["workflow_ids"] else 2,
            item["key"],
        ),
    )
    tasks: list[tuple[str, dict]] = []
    def needs_collection(route: str, item: dict) -> bool:
        existing = checkpoint[route].get(item["key"])
        if not existing:
            return True
        if (
            route == "companies"
            and existing.get("collection_status") in {"collected", "search_index"}
            and not domain_company_affinity(
                str(existing.get("domain") or ""),
                str(item.get("company_name") or ""),
            )
        ):
            return True
        attempts = int(existing.get("_attempts", 1) or 1)
        collection_status = existing.get("collection_status")
        retryable = collection_status in {
            "unreachable",
            "unresolved",
            "provider_limited",
        }
        if retry_status and collection_status != retry_status:
            return False
        return retry_failures and retryable and attempts < 6

    if routes in {"domains", "all"}:
        tasks.extend(("domains", item) for item in domain_work if needs_collection("domains", item))
    if routes in {"companies", "all"}:
        tasks.extend(("companies", item) for item in company_work if needs_collection("companies", item))
    if limit is not None:
        tasks = tasks[:limit]

    def run(task: tuple[str, dict]) -> tuple[str, str, dict]:
        route, item = task
        if route == "domains":
            evidence = collect_search_index_evidence(
                item["key"],
                company_name=item["company_name"],
                target_terms=item["target_terms"],
            )
        else:
            evidence = resolve_company_domain_public(
                item["company_name"],
                target_terms=item["target_terms"],
                index_only=True,
            )
        return route, item["key"], evidence.to_dict()

    completed = 0
    statuses: Counter[str] = Counter()
    if tasks:
        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = {executor.submit(run, task): task for task in tasks}
            for future in as_completed(futures):
                route, key, evidence = future.result()
                previous = checkpoint[route].get(key) or {}
                evidence["_attempts"] = int(previous.get("_attempts", 0) or 0) + 1
                checkpoint[route][key] = evidence
                completed += 1
                statuses[evidence.get("collection_status") or "unknown"] += 1
                _write_json_private(checkpoint_path, checkpoint, replace=True)
                if completed % 10 == 0 or completed == len(tasks):
                    print(json.dumps({
                        "progress": completed,
                        "scheduled": len(tasks),
                        "statuses": dict(statuses),
                    }), flush=True)
    return {
        "status": "collected",
        "checkpoint": str(checkpoint_path.resolve()),
        "scheduled": len(tasks),
        "completed": completed,
        "statuses": dict(statuses),
        "route_lead_counts": route_counts,
        "checkpoint_totals": {
            "domains": len(checkpoint["domains"]),
            "companies": len(checkpoint["companies"]),
        },
    }


def _assert_outbound_safe(workflows: list[models.Workflow], leads: list[models.Lead]) -> None:
    populated = {lead.workflow_id for lead in leads if lead.workflow_id is not None}
    unsafe = [
        workflow.id for workflow in workflows
        if workflow.id in populated and workflow.status == "active" and not workflow.email_sending_paused
    ]
    if unsafe:
        raise RuntimeError(f"active workflows are not email-paused: {unsafe}")


def _brief_snapshot(brief: models.LeadBrief | None) -> dict | None:
    if brief is None:
        return None
    return {
        "id": brief.id,
        "lead_id": brief.lead_id,
        "updated_at": brief.updated_at,
        **{field: deepcopy(getattr(brief, field)) for field in BRIEF_FIELDS},
    }


def _lead_snapshot(lead: models.Lead) -> dict:
    return {"id": lead.id, **{field: deepcopy(getattr(lead, field)) for field in LEAD_FIELDS}}


def plan_apply(db, checkpoint: dict) -> tuple[dict, dict]:
    leads = db.query(models.Lead).order_by(models.Lead.id).all()
    workflows_list = db.query(models.Workflow).all()
    _assert_outbound_safe(workflows_list, leads)
    workflows = {workflow.id: workflow for workflow in workflows_list}
    persona_ids = {workflow.persona_id for workflow in workflows_list if workflow.persona_id}
    personas = {
        persona.id: persona
        for persona in db.query(models.CustomerPersona).filter(models.CustomerPersona.id.in_(persona_ids)).all()
    } if persona_ids else {}
    briefs = {brief.lead_id: brief for brief in db.query(models.LeadBrief).all()}
    counters: Counter[str] = Counter()
    before = {"leads": {}, "briefs": {}}
    now = utcnow()

    with db.no_autoflush:
        for lead in leads:
            existing = briefs.get(lead.id)
            if _strict_valid(existing):
                missing_pain = _blank(existing.pain_points)
                missing_alignment = _blank(existing.value_proposition_alignment)
                missing_sources = not existing.evidence_sources
                if not (missing_pain or missing_alignment or missing_sources):
                    counters["already_strict_valid"] += 1
                    continue
                before["leads"][lead.id] = _lead_snapshot(lead)
                before["briefs"][lead.id] = _brief_snapshot(existing)
                if missing_pain:
                    existing.pain_points = (
                        "Qualification hypotheses, not confirmed facts: assortment differentiation, "
                        "supplier reliability, lead times, quality consistency, and minimum-order flexibility."
                    )
                    counters["pain_points_repaired"] += 1
                if missing_alignment:
                    existing.value_proposition_alignment = (
                        "Potential supplier alignment should be validated against the public catalog "
                        "and the contact's buying authority."
                    )
                    counters["value_alignment_repaired"] += 1
                if missing_sources:
                    existing.evidence_sources = [{
                        "type": "legacy_database_record",
                        "value": f"lead_record:{lead.id}",
                    }]
                    counters["evidence_sources_repaired"] += 1
                existing.researched_at = now
                counters["strict_valid_repaired"] += 1
                continue
            domain = normalize_domain(lead.domain)
            company_key = _company_key(lead.company_name)
            payload = None
            domain_payload = None
            if is_usable_company_domain(domain):
                domain_payload = checkpoint.get("domains", {}).get(domain)
                if (domain_payload or {}).get("collection_status") in {"collected", "search_index"}:
                    payload = domain_payload
            if payload is None and company_key:
                company_payload = checkpoint.get("companies", {}).get(company_key)
                if company_payload and (
                    company_payload.get("collection_status") not in {"collected", "search_index"}
                    or domain_company_affinity(
                        str(company_payload.get("domain") or ""),
                        str(lead.company_name or ""),
                    )
                ):
                    payload = company_payload
            if payload is None:
                payload = domain_payload

            payload_is_resolved = bool(
                payload
                and payload.get("collection_status") in {"collected", "search_index"}
                and is_usable_company_domain(str(payload.get("domain") or ""))
            )
            existing_flags = existing.quality_flags if existing and isinstance(existing.quality_flags, list) else []
            existing_is_unresolved = any(str(flag) == "public_web:company_unresolved" for flag in existing_flags)
            if _public_profile_complete(existing) and not (payload_is_resolved and existing_is_unresolved):
                counters["already_public_profile_complete"] += 1
                continue
            if payload_is_resolved and existing_is_unresolved:
                counters["public_profile_upgraded"] += 1

            if payload:
                evidence = CompanyEvidence.from_dict(payload)
            else:
                evidence = CompanyEvidence(
                    domain="",
                    collection_status="unresolved",
                    expected_company_name=str(lead.company_name or "").strip(),
                    error_code="public_collection_not_resolved",
                )

            before_lead = _lead_snapshot(lead)
            before_brief = _brief_snapshot(existing)
            evidence_domain = normalize_domain(evidence.domain)
            stored_domain_conflict = bool(
                domain
                and is_usable_company_domain(domain)
                and evidence_domain
                and is_usable_company_domain(evidence_domain)
                and evidence.collection_status in {"collected", "search_index"}
                and evidence_domain != domain
            )
            if stored_domain_conflict:
                # A public-search result is evidence, not authority to replace a
                # usable stored Company identity. Preserve both sides and force
                # an explicit reconciliation in V2.
                counters["verified_domain_conflict"] += 1
            elif (
                evidence_domain
                and is_usable_company_domain(evidence_domain)
                and evidence.collection_status in {"collected", "search_index"}
                and evidence_domain != domain
            ):
                lead.domain = evidence.domain
                domain = evidence.domain
                counters["verified_domain"] += 1
            if _blank(lead.company_name) and evidence.expected_company_name:
                lead.company_name = evidence.expected_company_name
                counters["company_name"] += 1

            workflow = workflows.get(lead.workflow_id)
            product_focus = " | ".join(filter(None, (
                getattr(workflow, "product_focus", None),
                getattr(workflow, "search_keywords", None),
            ))) if workflow else ""
            contact_name = " ".join(filter(None, (
                str(lead.first_name or "").strip(),
                str(lead.last_name or "").strip(),
            )))
            brief_data = build_brief_data(
                evidence,
                company_name=str(lead.company_name or "").strip(),
                contact_name=contact_name,
                job_title=str(lead.job_title or "").strip(),
                product_focus=product_focus,
            )
            if stored_domain_conflict:
                flags = list(brief_data.get("quality_flags") or [])
                for flag in (
                    "public_web:stored_domain_conflict",
                    f"public_web:conflicting_domain={evidence_domain}",
                ):
                    if flag not in flags:
                        flags.append(flag)
                brief_data["quality_flags"] = flags
            if lead.linkedin_url and all(
                item.get("value") != lead.linkedin_url
                for item in brief_data["evidence_sources"]
            ):
                brief_data["evidence_sources"].append({
                    "type": "public_contact_profile",
                    "value": lead.linkedin_url,
                })
            if not brief_data["evidence_sources"]:
                brief_data["evidence_sources"].append({
                    "type": "legacy_database_record",
                    "value": f"lead_record:{lead.id}",
                })

            brief = existing or models.LeadBrief(lead=lead)
            for field in BRIEF_FIELDS:
                if field == "researched_at":
                    setattr(brief, field, now)
                else:
                    setattr(brief, field, deepcopy(brief_data.get(field)))
            if existing is None:
                db.add(brief)
                briefs[lead.id] = brief
                counters["brief_created"] += 1
            else:
                counters["brief_updated"] += 1

            lead.data_sources = _append_source(lead.data_sources, "public_search_index")
            persona = personas.get(workflow.persona_id) if workflow else None
            score = score_lead_fit(lead, workflow=workflow, persona=persona, brief=brief)
            lead.fit_score = score.score
            lead.fit_grade = score.grade
            lead.qualification_notes = score.notes
            lead.handoff_recommended = score.handoff_recommended
            counters[f"research_status:{brief.research_status}"] += 1

            before["leads"][lead.id] = before_lead
            before["briefs"][lead.id] = before_brief

    report = {
        "affected_leads": len(before["leads"]),
        "counters": dict(sorted(counters.items())),
        "lead_total": len(leads),
        "brief_total_after": len(briefs),
        "all_leads_have_brief": len(briefs) == len(leads),
    }
    return report, before


def apply_checkpoint(
    checkpoint_path: Path,
    *,
    apply: bool,
    expected_total: int | None,
    expected_fingerprint: str | None,
) -> dict:
    checkpoint = _load_checkpoint(checkpoint_path)
    fingerprint = _database_fingerprint()
    db = _session_with_retry()
    backup_path = None
    try:
        total = db.query(models.Lead).count()
        if expected_total is not None and total != expected_total:
            raise RuntimeError(f"lead total mismatch: expected {expected_total}, observed {total}")
        if apply:
            if expected_total is None:
                raise RuntimeError("--apply requires --expected-total")
            if (expected_fingerprint or "").strip().lower() != fingerprint:
                raise RuntimeError("--apply requires the reviewed database fingerprint")
        report, before = plan_apply(db, checkpoint)
        if apply:
            stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            backup_path = ROOT / ".local" / "backfill" / f"public-customer-enrichment-{stamp}.json"
            _write_json_private(backup_path, {
                "created_at": datetime.now(timezone.utc),
                "database_fingerprint": fingerprint,
                "checkpoint": str(checkpoint_path.resolve()),
                "before": before,
            }, replace=False)
            db.commit()
            status = "applied"
        else:
            db.rollback()
            status = "dry_run"
        return {
            "status": status,
            "database_fingerprint": fingerprint,
            "checkpoint": str(checkpoint_path.resolve()),
            "backup": str(backup_path.resolve()) if backup_path else None,
            "outbound_messages_sent": 0,
            "personal_email_guesses": 0,
            "report": report,
        }
    finally:
        db.rollback()
        db.close()


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--collect", action="store_true")
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--apply", action="store_true")
    parser.add_argument("--checkpoint", type=Path, default=DEFAULT_CHECKPOINT)
    parser.add_argument("--routes", choices=("domains", "companies", "all"), default="all")
    parser.add_argument("--workers", type=int, default=3)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--retry-failures", action="store_true")
    parser.add_argument(
        "--retry-status",
        choices=("unreachable", "unresolved", "provider_limited"),
        help="with --retry-failures, retry only this checkpoint status",
    )
    parser.add_argument("--expected-total", type=int)
    parser.add_argument("--expected-database-fingerprint")
    return parser


def main() -> int:
    args = _parser().parse_args()
    checkpoint = args.checkpoint.resolve()
    if args.collect:
        if not 1 <= args.workers <= 6:
            raise SystemExit("--workers must be between 1 and 6")
        result = collect(
            checkpoint,
            routes=args.routes,
            workers=args.workers,
            limit=args.limit,
            retry_failures=args.retry_failures,
            retry_status=args.retry_status,
        )
    else:
        result = apply_checkpoint(
            checkpoint,
            apply=args.apply,
            expected_total=args.expected_total,
            expected_fingerprint=args.expected_database_fingerprint,
        )
    print(json.dumps(result, ensure_ascii=False, indent=2), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
