#!/usr/bin/env python3
"""Restore a reviewed local enrichment before-image and archive derived V2 evidence."""
from __future__ import annotations

import argparse
from datetime import datetime
import json
import os
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from database import SessionLocal, engine
import models as legacy
from product_v2 import models as v2
from product_v2.enums import TaskStatus, TaskType
from product_v2.services.domain import is_usable_company_domain, normalize_domain, utcnow
from scripts.enrich_legacy_customers_public_web import (
    BRIEF_FIELDS,
    LEAD_FIELDS,
    _assert_outbound_safe,
    _database_fingerprint,
)


def _parse_datetime(value):
    if value in (None, "") or isinstance(value, datetime):
        return value
    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))


def _restore_fields(row, snapshot: dict, fields: tuple[str, ...]) -> None:
    for field in fields:
        value = snapshot.get(field)
        column = row.__table__.columns.get(field)
        if column is not None and column.type.python_type is datetime:
            value = _parse_datetime(value)
        setattr(row, field, value)


def _assert_local_target() -> None:
    environment = os.environ.get("AUTOLEADGEN_ENV", "local").strip().lower()
    if environment not in {"local", "test"}:
        raise RuntimeError("rollback is restricted to local/test")
    url = engine.url
    if url.get_backend_name() == "sqlite":
        return
    host = (url.host or "").strip().lower()
    database = (url.database or "").strip().lower()
    if host not in {"127.0.0.1", "localhost", "::1"} or not database.startswith("autoleadgen_v2"):
        raise RuntimeError("rollback requires an isolated loopback autoleadgen_v2 database")


def rollback(backup_path: Path, *, apply: bool, expected_fingerprint: str) -> dict:
    _assert_local_target()
    resolved = backup_path.resolve()
    try:
        resolved.relative_to((ROOT / ".local" / "backfill").resolve())
    except ValueError as exc:
        raise RuntimeError("backup must stay inside .local/backfill") from exc
    payload = json.loads(resolved.read_text(encoding="utf-8"))
    fingerprint = _database_fingerprint()
    if expected_fingerprint.strip().lower() != fingerprint:
        raise RuntimeError("database fingerprint does not match the reviewed target")
    if str(payload.get("database_fingerprint") or "").lower() != fingerprint:
        raise RuntimeError("backup belongs to a different database target")

    before = payload.get("before") or {}
    lead_snapshots = before.get("leads") or {}
    brief_snapshots = before.get("briefs") or {}
    affected_ids = {int(value) for value in lead_snapshots}
    db = SessionLocal()
    counters = {
        "legacy_leads_restored": 0,
        "legacy_briefs_restored": 0,
        "v2_companies_reverted": 0,
        "v2_public_evidence_archived": 0,
        "v2_conflict_locks_deactivated": 0,
        "v2_conflict_tasks_dismissed": 0,
    }
    now = utcnow()
    try:
        leads = {
            row.id: row
            for row in db.query(legacy.Lead).filter(legacy.Lead.id.in_(affected_ids)).all()
        }
        if set(leads) != affected_ids:
            raise RuntimeError("backup Lead set does not match the target database")
        _assert_outbound_safe(db.query(legacy.Workflow).all(), list(leads.values()))

        for lead_id, lead in leads.items():
            snapshot = lead_snapshots[str(lead_id)]
            current_domain = normalize_domain(lead.domain)
            before_domain_raw = snapshot.get("domain")
            before_domain = normalize_domain(before_domain_raw) if is_usable_company_domain(before_domain_raw) else None
            company = db.query(v2.Company).filter_by(
                legacy_source_table="leads",
                legacy_id=str(lead_id),
            ).first()
            if (
                company is not None
                and current_domain
                and company.normalized_domain == current_domain
                and current_domain != before_domain
            ):
                company.normalized_domain = before_domain
                if company.website == f"https://{current_domain}":
                    company.website = f"https://{before_domain}" if before_domain else None
                counters["v2_companies_reverted"] += 1
            _restore_fields(lead, snapshot, LEAD_FIELDS)
            counters["legacy_leads_restored"] += 1

            brief_snapshot = brief_snapshots.get(str(lead_id))
            brief = db.query(legacy.LeadBrief).filter_by(lead_id=lead_id).first()
            if brief_snapshot is None or brief is None:
                raise RuntimeError("rollback requires an existing before-image LeadBrief")
            _restore_fields(brief, brief_snapshot, BRIEF_FIELDS)
            counters["legacy_briefs_restored"] += 1

            public_snapshots = db.query(v2.EvidenceSnapshot).filter(
                v2.EvidenceSnapshot.legacy_source_table == "lead_briefs_public_web",
                v2.EvidenceSnapshot.archived_at.is_(None),
                (
                    (v2.EvidenceSnapshot.legacy_id == str(brief.id))
                    | v2.EvidenceSnapshot.legacy_id.like(f"{brief.id}:%")
                ),
            ).all()
            for public_snapshot in public_snapshots:
                public_snapshot.archived_at = now
                counters["v2_public_evidence_archived"] += 1

        for lock in db.query(v2.SafetyLock).filter_by(code="company_identity_conflict", active=True).all():
            source_id = (lock.metadata_json or {}).get("legacy_source_id")
            if source_id in affected_ids:
                lock.active = False
                lock.unlocked_at = now
                lock.metadata_json = {
                    **(lock.metadata_json or {}),
                    "deactivated_by_enrichment_rollback": resolved.name,
                }
                counters["v2_conflict_locks_deactivated"] += 1

        for task in db.query(v2.Task).filter(v2.Task.task_type == TaskType.RECONCILIATION).all():
            metadata = task.metadata_json or {}
            if (
                metadata.get("legacy_source_id") in affected_ids
                and metadata.get("quarantine_reason") in {
                    "contact_identity_company_conflict",
                    "enriched_company_domain_conflict",
                }
                and task.status in {TaskStatus.OPEN, TaskStatus.IN_PROGRESS}
            ):
                task.status = TaskStatus.DISMISSED
                task.completed_at = now
                task.metadata_json = {
                    **metadata,
                    "dismissed_by_enrichment_rollback": resolved.name,
                }
                counters["v2_conflict_tasks_dismissed"] += 1

        if apply:
            db.commit()
            status = "applied"
        else:
            db.rollback()
            status = "dry_run"
        return {
            "status": status,
            "database_fingerprint": fingerprint,
            "backup": str(resolved),
            "outbound_messages_sent": 0,
            "affected_leads": len(affected_ids),
            "counters": counters,
        }
    finally:
        db.rollback()
        db.close()


def main() -> int:
    parser = argparse.ArgumentParser()
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--apply", action="store_true")
    parser.add_argument("--backup", type=Path, required=True)
    parser.add_argument("--expected-database-fingerprint", required=True)
    args = parser.parse_args()
    result = rollback(
        args.backup,
        apply=args.apply,
        expected_fingerprint=args.expected_database_fingerprint,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
