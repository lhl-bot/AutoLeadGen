# Product V2 documentation

These documents describe the local Company-first implementation and the gates
that still separate it from production. Local acceptance is not production
approval.

- [`IMPLEMENTATION_STATUS.md`](IMPLEMENTATION_STATUS.md) — verified local scope,
  evidence, and remaining blockers.
- [`LOCAL_DEVELOPMENT.md`](LOCAL_DEVELOPMENT.md) — isolated MySQL, fake connector,
  migration, backfill, and shadow-replay instructions.
- [`BASELINE.md`](BASELINE.md) — protected pre-V2 baseline and quarantined legacy
  deployment material.
- [`PRODUCTION_CUTOVER_RUNBOOK.md`](PRODUCTION_CUTOVER_RUNBOOK.md) — planning-only
  approval, sanitized snapshot, migration/backfill, seven-day shadow, workflow
  #18 canary, read fallback, and rollback plan. It is not approved for execution.
- [`INCIDENT_RESPONSE.md`](INCIDENT_RESPONSE.md) — live containment, complaint
  handling, reconciliation, controlled recovery, and closure evidence.
