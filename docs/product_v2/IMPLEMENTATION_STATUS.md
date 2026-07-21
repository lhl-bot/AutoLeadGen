# Product V2 implementation status

Updated: 2026-07-20. The production database was inspected read-only and its RDS
backup/recovery metadata was verified through Alibaba Cloud. In addition to the
earlier temporary restored-RDS rehearsal, the current production RDS contents
were copied read-only into an isolated local MySQL database. That local clone
completed the exact legacy-schema validation, `0001` stamp, `0002` → `0006`
migration, full dry-run, approved local apply, and repeatable zero-write resume.
No production database write, migration, process change, Provider call,
webhook, SMTP/IMAP call, LinkedIn call, or WhatsApp call was made. The repository
now contains the production deployment path; real environment execution remains
separately gated.

The current repository schema head is now `0007_acquisition_activation`. The
`0001` → `0006` statements below describe the completed historical restored-RDS
rehearsal. The additive `0007` revision has also passed fresh and repeated migration
coverage on an isolated local MySQL 9.6 instance, including interrupted-DDL index
repair. It has not been applied to production or the historical clone.

The planning-only production gates, evidence format, workflow #18 canary, and
rollback levels are documented in
[`PRODUCTION_CUTOVER_RUNBOOK.md`](PRODUCTION_CUTOVER_RUNBOOK.md). That draft is
not approval to execute any production action.

## Outcome

The Company-first Product V2 core is implemented behind `/api/v2` and the
primary UI now operates on V2 resources. It includes immutable Campaign
Revisions, independent multi-Campaign Enrollments, multi-step sequence controls,
human-governed reply and Opportunity flows, database-leased execution, durable
Provider accounting, contact/company safety locks, task-based review mode, and
an explicitly approved owner allowlist for production automatic Email.

The invitation-pilot activation flow is also implemented: a recoverable five-step
Chinese-first wizard, a shared CSV/AI AcquisitionRun candidate workspace,
evidence-first selection, verifier-owned ContactPoint validity, simplified REVIEW
plan launch, exact-copy per-message approval, and first-send completion tracking.
Formal accounts fail visibly on API errors instead of falling back to examples;
examples require explicit demo mode. Real acquisition remains fake-only behind
`ALLOW_REAL_ACQUISITION_CALLS=false` pending its independent safety and cost review.

The local acceptance gate passes with fake connectors and isolated MySQL.
An email-only production canary path now adds strict immutable message
templates, an SMTP connector, IMAP reply/bounce/abuse-report ingestion, recipient-bound
one-click unsubscribe, mounted secrets and runtime kill-switch files, preflight,
readiness/metrics, signed immutable release images, TLS reverse proxy, resource
limits, a no-send production SMTP/IMAP health probe, complaint-triggered sender
SafetyLocks, human/evidence-gated lock release, and rollback-oriented
Compose assets. This is **not** approval for production cutover: independent
restored-backup evidence, target-host staging/shadow evidence, real account
validation, and workflow #18 approval remain blockers.

## Milestone status

| Milestone | Status | Evidence and remaining gate |
| --- | --- | --- |
| M0 baseline protection | Complete locally | External worktree snapshot and credential-script quarantine are recorded in `BASELINE.md`; Alembic owns schema creation; the API starts neither schema creation nor workers. |
| M1 additive model and backfill | Current local full-RDS-clone acceptance passed; human disposition pending | Additive V2 tables, deterministic legacy keys, keyset-batched dry-run/apply/resume, checkpoint, stable logical checksum, quarantine, owner-conflict handling, and validator safeguards are covered. The 2026-07-20 local clone reached `0006`, processed all 985 Leads, and passed repeated zero-write resumes plus count/FK/owner/duplicate reconciliation. Every source row is represented by a visible V2 graph or canonical duplicate mapping. After strict public-evidence reconciliation, current quarantine is 550 items: 323 visible customers remain identity-pending, 54 source rows contain personal/invalid Company domains, 172 duplicate source rows require canonical Enrollment disposition, and one stored/public domain disagreement is hard-locked. Historical identity locks remain active until a human accepts the evidence and releases them. |
| M2 queue, safety, and channels | Production Email implementation complete; other real channels disabled | All prior lease, fence, SafetyLock, UNKNOWN, Consent, capacity, owner-path, and signed-ingress controls remain. Production Email adds SMTP delivery, IMAP thread-bound reply/bounce/complaint ingestion, runtime hard-pause recheck immediately before Provider I/O, one-click unsubscribe, exact-owner automatic-send approval, and complaint-triggered account containment. LinkedIn/WhatsApp and real prospecting/research/omnichannel remain intentionally unavailable. |
| M3 Campaign, tasks, opportunities, and AI governance | Core complete locally | Readiness, closed quality-gate schema, fail-closed sequence conditions, shadow/review/auto enforcement, diff checksum + human publish confirmation, Enrollment conflicts, Task Center, Conversation classification, qualified Opportunity lifecycle, and proposal-preview-confirm-audit flows are implemented. |
| M4 UI cutover, release, and acceptance | Repository release path and local clone acceptance complete; environment gates pending | Primary navigation/writes use V2. Multi-stage non-root images, digest-only promotion, SBOM/provenance/signing, Trivy gates, Caddy TLS, secret mounts, production Compose, migration/backfill/preflight jobs, readiness, metrics/alerts, and rollback controls are checked in. Target-host staging, an independent second restore, seven-day shadow, and canary execution remain pending. |

## Safety audit closure

The local safety audit found no unresolved P0. Its reproducible high-risk
findings now have regression coverage, including:

1. UNKNOWN results lock the affected Contact and, for cold outreach, Company;
   concurrent Campaigns cannot cross the Provider boundary. Authoritative events
   reconcile cooldowns, cost, tasks, locks, and audit without stale-worker
   overwrite.
2. A human-confirmed unsubscribe creates an idempotent scoped restriction and
   pauses every affected Enrollment.
3. Public Contact creation cannot claim `verification_status=valid`.
4. Contact, Company, and Global Consent cannot be incorrectly narrowed to one
   channel; ContactPoint scope is normalized to the real point channel.
5. Quality gates use a closed schema and `min_fit_score`; misspelled or unknown
   fields are rejected instead of silently ignored.
6. Sequence conditions and stop rules execute fail-closed. Shadow rejects real
   connectors; Review requires an exact Attempt approval Task. Auto remains
   fake-only in local/test and in production additionally requires the exact
   approved owner list, approval ID, and bounded sender-account daily limit at
   the final SMTP boundary.
7. Revision creation is always DRAFT. Publishing requires the current base id,
   canonical diff SHA-256, explicit human confirmation, and safe idempotent
   replay.
8. Billable Provider failures and paid misses consume Campaign budget; explicit
   non-billable failures and refunds do not.
9. Saved channel flags are runtime hard gates, global review policy creates an
   exact-Attempt approval, and the owner-scoped budget uses a MySQL current read
   under lock so concurrent Campaigns cannot spend against a stale snapshot.
10. Provider webhooks bind owner, Provider, timestamp, event id, and raw request
    bytes with HMAC-SHA256. Event id and idempotency are one durable identity;
    conflicting replay fails closed, secrets/signatures are never persisted,
    and unknown types create one reconciliation Task/Audit without guessed
    business side effects, including under concurrent MySQL delivery.
11. Owner cutover is a compare-and-switch control with an exact impact-preview
    checksum and idempotent receipt. Legacy HTTP writes, V2 writes, workers, and
    the final Provider boundary share owner fencing; MySQL tests prove the
    advisory lock remains held until the switch commit is visible. Legacy write
    recovery is independently approved and fails closed while V2 Consent,
    SafetyLock, uncertainty, reconciliation, or cooldown state is unprojected.
12. Every outbound Attempt freezes an immutable owner/channel sender identity.
    Readiness is read-only; trusted health updates are audited, idempotent, and
    reject secret-shaped error text. The Provider boundary re-locks the account
    and current-reads health, enablement, capacity, and account SafetyLocks, so
    concurrent Campaigns cannot consume the same final daily slot.
13. RFC 5965 abuse feedback is a first-class immutable `complained` event. It
    suppresses the ContactPoint, blocks its Enrollment, marks the sender
    unhealthy, creates an urgent deliverability Task and account SafetyLock,
    and pages a critical alert. Release requires a fresh no-send account probe,
    human confirmation, remediation evidence ID, and idempotent audit; transient
    Provider locks are never releasable through that API.

## Verified local acceptance evidence

- Backend full suite on the pinned dependency set: `374 passed, 13 skipped` on
  the local Python 3.12.13 runner. The production image remains pinned by digest
  to Python 3.11.15 and is validated separately in CI/deployment-host gates.
- Isolated MySQL 9.6 suite: `13 passed`, covering fresh
  and repeated migration, distinct `SKIP LOCKED` claims, lease fencing,
  concurrent Campaign/global-budget reservation, revision publish races, and an
  automated historical `0002` → `0003` Unicode identity migration in a derived
  disposable MySQL database. It also covers owner compare-and-switch,
  advisory-fence lifetime through commit, concurrent webhook replay, and
  account-row capacity contention. The fresh database reached
  `0007_acquisition_activation`; schema inspection also confirms the migration
  never creates an unsafe utf8mb4 index over the long normalized-email field.
- SQLite migration contract focus: `11 passed`, including AST protection against
  runtime-metadata imports, a full schema fingerprint, linear `0007` head,
  repeatable upgrade, `0007` interrupted-DDL index repair, and fail-closed
  constraints. Historical fixtures and the legacy validator also pass in the
  full backend suite.
- Frontend: ESLint passed; Vitest/React Testing Library passed `57` tests;
  OpenAPI snapshot/type drift checks passed; Next.js production build passed.
- The production build contains 34 routes, including the recoverable first-touch
  `/dashboard/get-started` and `/dashboard/find-customers` activation flow. CSV
  staging is owner-scoped, size-bounded, idempotent, and fail-closed on malformed,
  public-mailbox, or cross-domain candidate identities. Real/paid acquisition is
  rejected before job enqueue unless it receives a separate deployment approval.
- Playwright Chromium: `2 passed`, covering the six Product V2 destinations,
  removal of static health claims, keyboard entry, and mobile navigation.
- Live Playwright against the real local API: `4 passed`, covering accessible
  login, preview-confirmed settings persistence and restoration, mobile overflow
  plus serious/critical axe checks, and clean Legacy read-only rendering.
- Fresh `0001` → `0006` 30-company fake replay:
  `.local/shadow-replay/acceptance-20260720-1015.json`.
- Replay result: 30 companies, 30 attempts, 28 fake successes, 2 expected hard
  blocks, 0 external calls, 0 duplicates, 0 hard-gate bypasses, 30/30 Attempt
  traces, 30/30 sender-account traces, 28 Message/Cost events, 2 review Tasks,
  0 orphan Message/Cost/Task rows, 0 heartbeat-stage mismatches, and 0 billable
  or real-Provider events.
- Fresh isolated backfill dry-run/apply/resume produced the same checksum
  `f85ce35e22e8f2964ab8d1fab7d754b2eb142cadb75008f336ce2cca3ef37452`
  with an empty quarantine. This empty-database check validates command
  idempotency, not restored production data quality.
- A second isolated MySQL rehearsal exercised the production migration wrapper
  from an exact, unversioned 23-table legacy baseline: validation, explicit
  `0001` stamp, `0002` → `0006` upgrade, backfill dry-run, and approved apply
  all passed. The result was revision `0006_message_event_complaint`, 51 total
  tables, and 27 Product V2 tables. The production database remained read-only.
- Read-only Alibaba RDS verification confirmed a successful automated full
  snapshot backup, an active point-in-time recovery window, and a seven-day
  backup/log retention policy. The earlier temporary restored RDS preserved all 23
  legacy tables and 22,310 legacy rows exactly, migrated 984 Leads into 3,784
  V2 rows, reached `0006_message_event_complaint`, and produced no FK orphan,
  cross-owner mismatch, or duplicate V2 identity group. The one legacy
  unsubscribe became one V2 ConsentRestriction. Two resumes processed/created
  zero rows and matched logical checksum
  `59a013fe30295a0dd3d9c72e4ba07a868107ca4644ef4ba177e644a4f8616811`.
- The current 2026-07-20 read-only RDS copy into isolated local MySQL contained
  985 legacy Leads and now produces 683 Companies, 813 Contacts, 1,295
  ContactPoints, 813 Enrollments, 1,936 EvidenceSnapshots (1,913 active and 23
  archived), 373 imported legacy Attempts, and 399
  MessageEvents. The 172 duplicate source Leads reuse canonical Contacts and
  Enrollments, so these counts are not presented as 985 unique customers. All
  source quarantine items are materialized into the normal owner-scoped Task
  queue, and the previously omitted identity-pending rows now have
  visible Company/Contact/LinkedIn graphs plus active Company SafetyLocks. A
  second 320-company public-search retry was reviewed under stricter
  Company/domain affinity rules: 12 source rows received verified domains, one
  usable stored-domain disagreement retained the stored value and was hard-locked,
  and known false candidates were rejected. Public evidence is now an immutable
  revision chain: rollback archives evidence instead of deleting it, and a
  corrected replay appends a distinct version. The database has 569 total V2
  Tasks (563 open and 6 dismissed), 344 active SafetyLocks, and 928 active
  public-web evidence snapshots. Four legacy mailboxes are bound to V2 by
  source ID and public address without copying or
  exposing credentials. Repeated apply/resume runs created no rows and preserve
  checksum `270aad3a3df97e8366dfea0f9708ca618a8b814f49ea48331129b227f49d98b9`.
- Current-clone data review retained 172 duplicate legacy Lead-to-Enrollment
  mappings on one canonical Enrollment without losing their 373 Attempt and
  399 Message histories. The remaining 323 Leads without a supplied company
  identity and 54 rows with a personal/invalid Company domain
  are no longer hidden: exact source names (or an explicit identity-pending
  label), Contacts and LinkedIn points are visible, while Company SafetyLocks
  prevent outreach until evidence-based enrichment or an explicitly reviewed
  deferral. No domain, company identity, or email is invented.
- Python and frontend dependency audits report no known vulnerabilities.
- `git diff --check`, Python compilation, shell-script syntax, Compose/CI/release
  workflow YAML, and Prometheus alert-rule parsing pass. Container builds,
  Trivy image scans, and target-host Caddy validation remain CI/deployment-host
  gates because no local Docker daemon was available for this acceptance run.

## Production cutover blockers

1. Independently approve the completed restored-RDS `0001` → `0006` evidence,
   schema fingerprint, duration, and forward-repair procedure. The rehearsal
   does not make non-transactional MySQL DDL rollback-safe by itself.
2. Complete or explicitly defer the current-clone review queue: 172
   deterministic duplicate Enrollment mappings, 323 unresolved company
   identities, 54 personal/invalid Company-domain source rows, one active
   evidence conflict, and the retained historical enrichment
   Tasks whose newly found domains still require human acceptance. All affected
   customers are owner-scoped and visible in the V2 Work page, and unresolved
   or conflicting identities remain hard-locked; never invent a company or
   email merely to clear the queue.
3. Perform the required independent second restored-backup rehearsal with the
   same immutable application artifact and compare its logical checksum and
   reconciliation evidence. The completed apply plus two resumes prove
   idempotency on one restored instance, not independence across two restores.
4. Provision and validate the approved SMTP/IMAP canary account, independently
   rotate JWT/SMTP/unsubscribe/webhook secrets, bind it to the V2 owner, verify
   health freshness, DNS deliverability (SPF/DKIM/DMARC), complaint/bounce
   reporting, TLS/DNS, private metrics, alert routing, and managed backups.
   LinkedIn/WhatsApp and non-email real workers remain outside this release.
5. Run a seven-day fake-only shadow observation, then approve and execute the
   workflow #18 canary, rollback, and legacy-read feature-flag plan defined in
   the planning-only
   [`PRODUCTION_CUTOVER_RUNBOOK.md`](PRODUCTION_CUTOVER_RUNBOOK.md).
6. Rotate previously exposed production credentials under a separate deployment
   approval before any production work.
