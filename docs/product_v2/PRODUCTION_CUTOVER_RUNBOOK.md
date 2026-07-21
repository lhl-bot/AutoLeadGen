# Product V2 production cutover, canary, and rollback runbook

> **Status: IMPLEMENTATION READY FOR EMAIL CANARY / EXECUTION NOT APPROVED**
>
> Updated: 2026-07-17
>
> This document does not authorize a production connection, schema change,
> backfill, Provider call, account change, webhook change, PM2 change, or real
> message. It deliberately contains no real hostname, credential, secret, or
> executable production target.

## 1. Purpose and non-negotiable boundaries

This runbook describes the evidence and approvals required to move the
Company-first Product V2 from local isolation to a controlled production
cutover. It uses an expand/migrate/canary/contract sequence and keeps the legacy
tables intact throughout the migration window.

For live containment and recovery after an alert, use
[`INCIDENT_RESPONSE.md`](INCIDENT_RESPONSE.md) together with Section 11.

The repository now contains a production-shaped, email-only release path. It
is still **not authorized to touch production** until the external approvals
and environment evidence in this runbook pass. In particular:

- Alembic revisions `0001` through `0007` are checked-in additive artifacts
  snapshots with local schema-fingerprint and MySQL migration coverage. They
  still require independent review and rehearsal against a sanitized production
  snapshot; their local freeze is not production approval.
- The isolated backfill CLI remains local-only. The separate
  `scripts/production_backfill.py` wrapper now requires MySQL, the exact
  reviewed database fingerprint, the release/change identifiers, frozen legacy
  writers, hard pause, disabled external calls, and an approved evidence mount.
  It has not yet been authorized or rehearsed against the sanitized snapshot.
- Owner write-path fencing, SMTP external delivery, IMAP reply/bounce/abuse-report ingestion,
  recipient-bound one-click unsubscribe, account ownership/capacity checks,
  signed Provider ingress, runtime emergency control files, and production
  preflight are implemented. The real credentials/accounts, DNS/TLS, managed
  database, monitoring route, backup/restore evidence, and canary content still
  require owner approval and environment validation.
- The initial real scope is Email only. LinkedIn and WhatsApp have no registered
  real connector and must remain disabled. Prospecting/research/omnichannel real
  automation is not part of this launch. The acquisition workspace may be used
  with fake connectors, but `ALLOW_REAL_ACQUISITION_CALLS` must remain false until
  search, enrichment, verification, safety, and cost controls receive a separate
  approval.
- The seven-day fake-only shadow observation and workflow #18 canary have not
  happened.
- Historical `migrate_v16.py`, `prepare_workflow18_*`,
  `verify_workflow18_*`, and workflow #18 one-off files are evidence only. They
  must not be executed as migration or canary automation.

No step may weaken ConsentRestriction, SafetyLock, verification, idempotency,
cooldown, budget, account-health, or global hard-pause enforcement. A human may
approve a documented soft quality override, but cannot override a hard gate.

## 2. Roles, records, and approval gates

Every production change must have one immutable change record. It contains only
secret-free artifact identifiers and links to restricted evidence storage.

| Role | Required responsibility |
| --- | --- |
| Release owner | Owns the timeline, reads every gate aloud, and is the only person who declares go/no-go. |
| Database owner | Owns backup/restore, frozen DDL review, locking assessment, migration execution, and database reconciliation. |
| Application owner | Owns API/UI version, worker topology, feature flags, smoke tests, and application rollback. |
| Security approver | Owns snapshot sanitization, credential rotation, secret scanning, access expiry, and webhook authentication review. |
| Compliance/sales approver | Owns Consent sampling, workflow #18 audience approval, message approval, daily canary release, and business stop thresholds. |
| Independent observer | Confirms evidence, timestamps each decision, and cannot be the operator for the step being approved. |

An approval is valid only when it names the artifact digest, environment,
operator, approver, UTC timestamp, result, and change-record ID. Chat reactions
or verbal approval are not sufficient.

| Gate | Decision | Minimum approvers | State now |
| --- | --- | --- | --- |
| G0 | Scope, ownership, maintenance window, and communication plan | Release + application + database | Pending |
| G1 | Frozen migration artifact and rollback-safe rehearsal | Database + independent observer | Static artifact prepared locally; production-snapshot review pending |
| G2 | Sanitized snapshot and data-handling review | Security + database | Pending |
| G3 | Two deterministic backfill rehearsals and quarantine disposition | Database + application + compliance | Pending |
| G4 | Real-mode topology, account binding, webhook, and kill-switch certification | Application + security | Local controls pass; real bindings, emergency reject, and approval blocked |
| G5 | Seven consecutive shadow days accepted | Release + application + compliance | Pending |
| G6 | Credential rotation and old-secret revocation | Security + service owners | Pending |
| G7 | Workflow #18 canary audience/content/budget approval | Compliance/sales + release | Pending |
| G8 | V2 read-path cutover and legacy fallback approval | Release + application + database | Pending |
| G9 | End of observation and later legacy contract/removal | All owners | Pending; separate change |

Failure or expiry of any approval returns the change to `NO-GO`. No gate is
implicitly inherited from local test evidence.

Gate numbers are stable references, not permission to run strictly in numeric
order. G6 must close before any application/operator migration connection to
production; G1–G4 must close before G5; and G1–G7 must all close before G8.

## 3. Required control-plane changes before G0

The following controls are implemented in the repository and must be exercised
in staging before scheduling a production window:

1. A per-owner migration state that atomically selects `legacy` or `v2` for
   **writes**, scoped first to an internal owner allowlist and then globally.
   Read routing/fallback is a separate feature flag and approval. The write
   switch requires explicit evidence that all legacy API/PM2/background writers
   are frozen, and it must prove that one owner never has two active write paths.
   `PRODUCT_V2_LEGACY_READ_ONLY` already protects legacy writes globally, but it
   is **not** a read-path selector or a cohort router.
2. A one-action global outbound kill switch that all real connectors re-check
   immediately before crossing the Provider boundary.
3. Per-channel and per-account kill switches, with account-level SafetyLocks.
4. The production-safe backfill entrypoint preserves
   `--dry-run`, `--apply`, `--resume`, checkpoint, checksum, and quarantine
   semantics, requires a change-record ID and artifact digest, and refuses an
   unapproved database identity. The local CLI remains unchanged and must not
   be bypassed.
5. `/metrics`, `deploy/monitoring/prometheus-rules.yml`, and the V2 operational
   APIs provide the base signals for worker heartbeat, StageRuntime, job lease age,
   Attempt/Message/Cost/Task traceability, Consent and SafetyLock decisions,
   Provider unknown results, budget reservations, and V1/V2 reconciliation.
6. An authenticated, signed, replay-protected webhook path with an emergency
   reject switch and a dead-letter/reconciliation workflow.
7. A tested write freeze. It must stop campaign starts, enrollments, automation
   jobs, and outbound work without preventing unsubscribe POSTs, inbound events,
   audit writes, or safety restrictions.

## 4. Credential rotation plan (G6)

Credential rotation is a separate, approved security change and happens before
any production migration connection or real-mode canary. The inventory must
cover database users, JWT/session secrets, SMTP/IMAP accounts, LinkedIn and
WhatsApp accounts, Provider API keys, webhook signing secrets, deployment
credentials, and any secret previously present in a quarantined script.

For each credential:

1. Create the replacement in the approved secret manager; never paste it into
   this repository, a ticket, terminal transcript, screenshot, or evidence
   bundle.
2. Grant the minimum role and, where supported, restrict source, scope, quota,
   and expiry.
3. Deploy a secret reference, verify the intended identity with a non-billable
   or read-only check, then revoke the old credential.
4. Search secret-manager and Provider audit logs for use of the old identity.
5. Record only the secret version/alias, rotation timestamp, revocation result,
   and approver in the evidence record.

Credential rotation is irreversible for rollback purposes: an old or exposed
secret is never re-enabled. Rollback uses the new secret with the previous
application version.

## 5. Sanitized production snapshot rehearsal (G2)

### 5.1 Capture and custody

The database owner creates a consistent, encrypted production snapshot through
the approved platform procedure. The snapshot is copied into a restricted
sanitization boundary; raw data never enters a developer laptop or this
workspace. Record database engine/version, SQL mode, timezone, collation,
snapshot timestamp, source log position, encrypted object ID, byte size, and
SHA-256 digest.

Security must verify that the destination has named users, least privilege,
access logging, encryption, a deletion deadline, and no route to real Provider
accounts.

### 5.2 Sanitization rules

The sanitization job must be versioned and independently reviewed. It must:

- remove passwords, tokens, connection strings, OAuth material, webhook
  secrets, session data, and deployment settings;
- deterministically tokenize company/contact identities when identity matching
  must be preserved, using a key held outside the dataset;
- replace message bodies, signatures, attachments, and free-form task/audit
  text with structural fixtures while preserving event direction, status,
  timestamps, relationships, and intent category needed for reconciliation;
- preserve ConsentRestriction, unsubscribe, bounce, spam, suppression,
  SafetyLock, cost, and immutable history semantics, while tokenizing the
  contact values they reference;
- prevent any sanitized email, phone number, LinkedIn URL, webhook URL, or
  account identifier from being routable to a real person or Provider;
- preserve primary/foreign-key relationships and deterministic duplicate cases;
  and
- emit before/after table counts, a sanitization manifest, exception count,
  tool version, and output digest without exposing row content.

The application owner restores the sanitized snapshot only into an isolated
MySQL environment with fake connectors, outbound hard-paused, no Provider
credentials, and blocked external egress. Security samples the restored copy
before G2 approval. Any unsanitized secret or routable contact is a hard stop.

## 6. Frozen DDL and migration rehearsal (G1)

1. Verify the checked-in static `0001`/`0002` DDL/index snapshots and every
   later revision as immutable source artifacts. No revision may import runtime
   model metadata to decide production DDL; a schema change requires a new
   revision rather than editing an already approved digest.
2. Review all MySQL types, lengths, enum/check behavior, nullability, defaults,
   generated indexes, foreign keys, constraint names, charset/collation, and
   expected lock/scan behavior.
3. Document the online/offline behavior and worst-case duration for every
   statement, especially `0003` identity-hash backfill and unique-index changes,
   the `0004` owner/account foreign keys and capacity indexes, the `0005`
   immutable outreach-template columns, the `0006` complaint-event check
   constraint, and the `0007` owner-scoped acquisition idempotency/index plan.
4. Run duplicate-data and invalid-value preflight queries against the sanitized
   snapshot. A preflight conflict goes to quarantine/repair; it is never
   auto-deleted or guessed.
5. Rehearse from a restored legacy snapshot, from historical `0002`, from an
   interrupted statement (including partially created `0007` tables/indexes),
   and with a competing migrator. Confirm the advisory lock, retry boundary, and
   final schema digest.
6. Reconcile the frozen manifest and `alembic_version`. Downgrade remains
   destructive and prohibited; rollback keeps additive V2 tables in place.

Because MySQL DDL can auto-commit, a failed migration is not repaired by simply
rerunning or restoring application code. The database owner must follow the
statement-specific forward-repair procedure proven during rehearsal. If the
observed schema state is not one of the rehearsed states, stop and restore to a
new database; do not improvise DDL in the cutover window.

## 7. Backfill rehearsal and quarantine (G3)

### 7.1 Planning commands for the isolated sanitized copy

The following examples describe the **existing isolated-only** tool. Placeholders
must resolve to a loopback `autoleadgen_v2*` database with fake connectors; they
are not production commands.

```bash
AUTOLEADGEN_ENV=local \
PRODUCT_V2_ISOLATED_DATABASE=true \
DATABASE_URL='<isolated loopback sanitized-copy URL>' \
venv/bin/python scripts/backfill_product_v2.py \
  --dry-run \
  --checkpoint '<restricted evidence dir>/dry-run-checkpoint.json' \
  --quarantine '<restricted evidence dir>/dry-run-quarantine.json'
```

After dry-run review, recreate the database from the same sanitized snapshot,
apply the frozen Alembic chain, and run the approved apply rehearsal:

```bash
AUTOLEADGEN_ENV=local \
PRODUCT_V2_ISOLATED_DATABASE=true \
PRODUCT_V2_BACKFILL_APPLY=true \
DATABASE_URL='<isolated loopback sanitized-copy URL>' \
venv/bin/python scripts/backfill_product_v2.py \
  --apply \
  --checkpoint '<restricted evidence dir>/apply-checkpoint.json' \
  --quarantine '<restricted evidence dir>/apply-quarantine.json'
```

Interrupt only at a pre-agreed batch boundary during a dedicated recovery
rehearsal, preserve the files, then resume with the same build and artifacts:

```bash
AUTOLEADGEN_ENV=local \
PRODUCT_V2_ISOLATED_DATABASE=true \
PRODUCT_V2_BACKFILL_APPLY=true \
DATABASE_URL='<same isolated loopback sanitized-copy URL>' \
venv/bin/python scripts/backfill_product_v2.py \
  --apply --resume \
  --checkpoint '<same restricted evidence dir>/apply-checkpoint.json' \
  --quarantine '<same restricted evidence dir>/apply-quarantine.json'
```

Run `--apply --resume` once more after completion. It must reuse existing
records, create no duplicates, preserve immutable history, and produce the same
business-state checksum. A second full rehearsal starts from another restore of
the exact same snapshot and must produce the same checksum again.

### 7.2 Reconciliation requirements

G3 requires all of the following:

- source and target counts reconciled by owner and source table;
- every V2 row attributable through `legacy_source_table + legacy_id` where
  applicable;
- no duplicate Company/Contact/ContactPoint identity and no cross-owner graph;
- all Message, Attempt, Cost, Consent, unsubscribe, and Audit histories retained
  and queryable;
- checksum equality across dry-run transient state, completed apply/resume,
  idempotent rerun, and the independent second rehearsal;
- checkpoint never advances past an unpersisted quarantine record;
- no unpriced cost silently treated as zero; and
- zero **unapproved** quarantine items.

Each quarantine item must include source table/id, reason, non-sensitive
details, owner, reviewer, decision, evidence, timestamp, and disposition. Valid
dispositions are: correct the source copy and rerun; add an explicitly reviewed
mapping; exclude the record while preserving the quarantine evidence; or defer
the entire cutover. Owner conflicts, missing ownership, ambiguous identity, and
company-level Consent scope are never guessed. Two people, including compliance
for Consent, must approve every non-mechanical disposition.

The production wrapper generates the same report and additionally records the
secret-free change, release, image, and approved database fingerprints. An
`--apply` command is still prohibited until its sanitized-snapshot dry run and
database fingerprint are approved in the change record.

## 8. Seven-day fake-only shadow observation (G5)

Shadow begins only after G1–G4 pass. It uses production-shaped data and event
flow but fake connectors, zero paid lookup, zero real webhook, and zero external
network call. `shadow` mode must reject a real connector even under
misconfiguration.

Run for seven **consecutive complete 24-hour periods**. A hard-stop event resets
the seven-day clock after remediation and a clean redeploy. For every day,
capture:

- deployed artifact and configuration digests;
- worker heartbeat and StageRuntime agreement for prospecting, research,
  outbound, inbox, and omnichannel workers;
- jobs claimed, retried, expired, failed, blocked, and reconciled;
- Attempts by gate outcome and confirmation of zero duplicate idempotency key;
- Message/Cost/Task/Audit traceability and orphan counts;
- Consent/SafetyLock decisions, unknown Provider simulations, budget and
  cooldown enforcement;
- V1 versus V2 row/count/outcome reconciliation; and
- explicit network/Provider evidence showing zero external calls and zero
  billable events.

Daily acceptance is 100% traceability, zero duplicate task/attempt, zero hard
gate bypass, zero orphan immutable event, zero external/billable event, zero
unreconciled owner mismatch, and heartbeat status matching the UI. Warnings
require a signed disposition before the next day; a blocker restarts the clock.

## 9. Workflow #18 canary (G7)

Workflow #18 is the only initial real-outbound canary. Its preserved 30-company
manifest is input evidence, not an execution script. Before importing through
the reviewed V2 API, re-verify every company, contact, evidence source,
verification result, Consent/cooldown state, ICP fit, owner, timezone, sender,
budget, unsubscribe URL, and message preview. Historical claims are not treated
as current verification.

Canary constraints:

- email only; LinkedIn and WhatsApp remain disabled;
- one Contact per Company and at most five first-touch messages per UTC day;
- Campaign mode `review`, never `auto`;
- every exact Attempt preview receives named sales/compliance approval;
- valid/verified email, current evidence, healthy sender, and remaining budget
  are mandatory hard gates; the public unsubscribe GET may only render a
  confirmation page and only an idempotent POST may create the restriction;
- the global 14-day ContactPoint cold-start cooldown and 24-hour Company
  first-touch cooldown remain enforced (the canary's one-Contact-per-Company
  limit is stricter than the normal Campaign limit);
- no paid lookup without a separately approved per-call budget and impact
  preview;
- a 24-hour observation separates daily batches, and the next batch requires a
  new signed go decision; and
- no automatic expansion beyond the 30 approved companies.

Suggested controlled sequence:

1. **C0 — no-send validation:** all 30 Company graphs pass readiness and produce
   previews through fake connectors. Reconcile every gate and trace.
2. **C1 — first five:** release no more than five approved messages, one at a
   time. Confirm Provider result, MessageEvent, cost, cooldown, and inbox cursor
   after each Attempt.
3. **C2–C6 — daily batches:** after the preceding 24-hour review, release at most
   five new first touches. A positive signal pauses that Contact's other
   Enrollments; a confirmed qualified Opportunity pauses the Company's other
   cold outreach.
4. **C7 — closeout:** reconcile all 30 Company records, including not-sent and
   blocked records. No follow-up sequence or broader audience is enabled by this
   canary approval.

An unsubscribe, hard bounce, complaint, or negative reply pauses the next batch
until its restriction, scope, cursor, tasks, and related Enrollments are manually
verified. A single safety-system failure invokes the hard-stop procedure below.

## 10. Cutover sequence (G8)

The exact maintenance-window times and commands belong in the approved change
record. The planned order is:

1. Announce the window and freeze Campaign/revision/settings changes. Keep
   unsubscribe POST and inbound safety processing available.
2. Enable global and channel hard pauses; drain or safely fence all leased jobs.
   Capture queue, cursor, heartbeat, budget, and database baselines.
3. Verify current backup/restore evidence, rotated credentials, artifact
   digests, database free space, replication health, and an on-call bridge.
4. Apply only the frozen additive Alembic artifact under the migration lock.
   Reconcile schema before starting any application process.
5. Run the approved production backfill `--dry-run`; compare it with the
   sanitized rehearsal. Release owner and database owner sign the result.
6. Run the approved `--apply`/`--resume`, retaining checkpoint and quarantine
   outside ephemeral hosts. Reconcile counts, checksum, history, and every
   quarantine decision before continuing.
7. Deploy API/UI/workers with real outbound still hard-paused. Keep all owners'
   read/write migration state on `legacy`; enable read-only V2 comparison for
   the internal owner allowlist without accepting V2 business writes.
8. Run read-only and fake-path smoke tests and verify real
   heartbeats/readiness. For the workflow #18 owner, first freeze and prove all
   V1 writers, then atomically select the V2 write path; switch the separately
   approved V2 read path before executing Section 9. If that owner-scoped
   exclusion cannot be proven, do not run the canary.
9. After canary acceptance, migrate graduated cohorts atomically: freeze the
   cohort, reconcile, record `PRODUCT_V2_LEGACY_WRITERS_FROZEN=true` only after
   every old writer is proven stopped, select its V2 write path, switch the
   independently approved read path, then unfreeze. Set
   `PRODUCT_V2_LEGACY_READ_ONLY=true` globally only after every
   required owner has migrated and reconciliation proves that no V1 writer is
   still required.
10. Observe for the approved stabilization period. Legacy tables and APIs stay
    present; deletion/contract is a separate G9 change after retention and
    rollback windows expire.

### 10.1 Production Compose command template

This is the checked-in single-host Email-canary procedure. The host must already
have Docker Engine with the Compose plugin, registry access, public DNS pointed
at the host, ports 80/443 reachable, a managed external MySQL endpoint, private
monitoring access, and the five mode-0400 secret files named in
`deploy/production.env.example`. Copy that example to an access-controlled path
outside the repository and fill only approved non-secret artifact metadata.
This topology is a controlled initial canary, not multi-host high availability.

Set one convenience variable to the reviewed metadata file and keep all
commands at the reviewed repository SHA:

```bash
AUTOLG_ENV_FILE=/secure/autoleadgen/production.env
docker compose --env-file "$AUTOLG_ENV_FILE" -f compose.production.yml config --quiet
```

Create both live controls in their safest state before starting a container.
The command writes atomically, rejects symlink targets, and emits a secret-free
audit record for the change log:

```bash
python3 scripts/set_runtime_control.py \
  --directory /secure/autoleadgen/runtime-control \
  --control outbound_hard_pause --value true --change-id CHG-0000
python3 scripts/set_runtime_control.py \
  --directory /secure/autoleadgen/runtime-control \
  --control webhook_reject_all --value true --change-id CHG-0000
```

Verify each release image by digest against the repository's GitHub Actions
OIDC identity before pulling it. Replace the organization/repository and the
two image coordinates with the exact approved values from the release summary:

```bash
cosign verify \
  --certificate-identity-regexp '^https://github.com/ORG/REPO/.github/workflows/release-images.yml@refs/heads/main$' \
  --certificate-oidc-issuer 'https://token.actions.githubusercontent.com' \
  'ghcr.io/ORG/REPO-api@sha256:APPROVED_DIGEST'
cosign verify \
  --certificate-identity-regexp '^https://github.com/ORG/REPO/.github/workflows/release-images.yml@refs/heads/main$' \
  --certificate-oidc-issuer 'https://token.actions.githubusercontent.com' \
  'ghcr.io/ORG/REPO-frontend@sha256:APPROVED_DIGEST'
docker compose --env-file "$AUTOLG_ENV_FILE" -f compose.production.yml pull
```

Before DDL, record and independently match the secret-free database fingerprint
to the change record, then take/verify the platform backup. The production
migration wrapper requires the exact fingerprint, backup/restore and staging
evidence IDs, frozen writers, outbound hard pause, disabled external calls, and
`PRODUCT_V2_PRODUCTION_MIGRATION_APPROVED=true`. A verified legacy database with
no `alembic_version` additionally requires
`PRODUCT_V2_LEGACY_BASELINE_STAMP_APPROVED=true`; the wrapper validates the exact
legacy table set, stamps `0001`, and only then upgrades to head. Run only the
frozen wrapper and fail-closed deploy preflight:

```bash
docker compose --env-file "$AUTOLG_ENV_FILE" -f compose.production.yml \
  --profile operations run --rm preflight python scripts/database_fingerprint.py
docker compose --env-file "$AUTOLG_ENV_FILE" -f compose.production.yml \
  --profile operations run --rm migrate
```

Run production backfill dry-run, review its checkpoint/quarantine/checksum, then
use the explicit approval only for the signed apply command. A completed resume
must be idempotent and reproduce the reviewed checksum:

```bash
docker compose --env-file "$AUTOLG_ENV_FILE" -f compose.production.yml \
  --profile operations run --rm backfill \
  --dry-run --checkpoint /evidence/dry-run-checkpoint.json \
  --quarantine /evidence/dry-run-quarantine.json
docker compose --env-file "$AUTOLG_ENV_FILE" -f compose.production.yml \
  --profile operations run --rm \
  -e PRODUCT_V2_PRODUCTION_BACKFILL_APPROVED=true backfill \
  --apply --resume --checkpoint /evidence/apply-checkpoint.json \
  --quarantine /evidence/apply-quarantine.json
```

For a greenfield database only, create the first administrator after migration
with the one-time bootstrap. Skip this command when an active administrator
already exists. The script refuses a second/different admin, direct password
environment variables, a database fingerprint mismatch, released outbound, or
an unapproved change:

```bash
docker compose --env-file "$AUTOLG_ENV_FILE" -f compose.production.yml \
  --profile operations run --rm \
  -e BOOTSTRAP_ADMIN_APPROVED=true \
  -e BOOTSTRAP_ADMIN_USERNAME=approved-admin-name \
  -e BOOTSTRAP_ADMIN_PASSWORD_FILE=/run/secrets/bootstrap_admin_password \
  -v /secure/autoleadgen/bootstrap_admin_password:/run/secrets/bootstrap_admin_password:ro \
  preflight python scripts/bootstrap_production_admin.py
```

Delete the host bootstrap-password file after the first successful login and
password-manager escrow; never use this entrypoint for routine admin changes.

Now run the deploy preflight. It requires the migrated schema, an active
administrator, release metadata, host/CORS policy, secret separation, paused
external effects, and zero unresolved Provider state:

```bash
docker compose --env-file "$AUTOLG_ENV_FILE" -f compose.production.yml \
  --profile operations run --rm preflight
```

Deploy with `ALLOW_REAL_EXTERNAL_CALLS=false` and both control files still
`true`. API/UI become available, but the real workers cannot cross an external
boundary. Confirm `/health/ready`, the private metrics scrape, alert delivery,
backup status, and the read/fake smoke tests before owner-path promotion:

```bash
docker compose --env-file "$AUTOLG_ENV_FILE" -f compose.production.yml \
  up -d api frontend gateway outbound-worker inbox-worker
docker compose --env-file "$AUTOLG_ENV_FILE" -f compose.production.yml ps
```

After the owner cohort, Email account, message previews, DNS policy, and G0–G7
evidence are approved, change only the external-call approval in the protected
metadata file to `ALLOW_REAL_EXTERNAL_CALLS=true`, recreate the two workers
while outbound remains paused, and run the approval-bound Email account probe.
The probe authenticates to SMTP and IMAP, selects INBOX read-only, sends no
message, and records only a stable health code plus an audit correlation id.
Use a new unique probe id for every changed result. Then run the safe-promotion
preflight. IMAP may now ingest safety events; SMTP remains blocked by the live
pause file:

```bash
docker compose --env-file "$AUTOLG_ENV_FILE" -f compose.production.yml \
  up -d --no-deps --force-recreate outbound-worker inbox-worker
docker compose --env-file "$AUTOLG_ENV_FILE" -f compose.production.yml \
  --profile operations run --rm \
  -e EMAIL_ACCOUNT_PROBE_APPROVED=true preflight \
  python scripts/probe_production_email_account.py \
  --owner-id APPROVED_OWNER_ID \
  --channel-account-id APPROVED_EMAIL_ACCOUNT_ID \
  --probe-id CHG-0000-email-probe-01
docker compose --env-file "$AUTOLG_ENV_FILE" -f compose.production.yml \
  --profile operations run --rm preflight \
  python scripts/production_preflight.py --phase enable-real
```

Only after that report is fully `pass`, release outbound atomically for the
approved release SHA and verify that both release-bound worker heartbeats are
fresh. The webhook reject control stays `true` for the SMTP/IMAP canary:

```bash
python3 scripts/set_runtime_control.py \
  --directory /secure/autoleadgen/runtime-control \
  --control outbound_hard_pause --value false --change-id CHG-0000 \
  --approved-release-sha APPROVED_RELEASE_SHA
docker compose --env-file "$AUTOLG_ENV_FILE" -f compose.production.yml \
  --profile operations run --rm preflight \
  python scripts/production_preflight.py --phase verify-live
```

If `verify-live` fails, immediately re-engage the pause; do not troubleshoot
with SMTP enabled. Public webhook ingress may be released in a later change only
after the specific integration's signed replay tests pass.

No step advances on a warning merely because the maintenance window is ending.
The safe default is paused outbound, legacy reads, preserved additive data, and
an extended window or reschedule.

### 10.2 Promote a reviewed owner from per-message review to automatic Email

Automatic Email is a separate post-canary change. It is never enabled merely
because real connectors are running. The change record must name the exact V2
owner IDs, approval ID, per-account ceiling, approved Campaign revisions, daily
volume, monitoring window, and stop authority. Start with one owner and one
sender account; expansion requires another reviewed change.

While the live outbound control remains `true`, set all four controls together
in the protected metadata file:

```text
PRODUCT_V2_PRODUCTION_AUTO_SEND_APPROVED=true
PRODUCT_V2_AUTO_SEND_APPROVAL_ID=APPROVED-NON-SECRET-RECORD-ID
PRODUCT_V2_AUTO_SEND_OWNER_IDS=APPROVED_OWNER_ID
PRODUCT_V2_AUTO_SEND_MAX_DAILY_PER_ACCOUNT=APPROVED_LIMIT
```

The owner list is an exact integer allowlist, not a wildcard. Every active Auto
Campaign must belong to that allowlist, and every bound sender account must
have a positive `daily_limit` no greater than the deployment ceiling (which is
itself capped at 100). Review Campaigns continue to require approval of the
exact immutable message snapshot. Shadow Campaigns may never use a real
connector.

Recreate the workers while outbound remains paused, run the `enable-real`
preflight, inspect active SafetyLocks and sender health, and record the report.
Release the live control only when the report passes and the on-call observer is
present. The SMTP connector independently rechecks the run mode, exact owner,
approval ID, review state, and account limit at the final Provider boundary, so
a stale worker cannot rely only on startup configuration.

Any RFC 5965 abuse report is ingested as a `complained` event. It immediately
suppresses the contact point, blocks the Enrollment, marks the sender unhealthy,
creates an urgent Task, and creates an account SafetyLock. Alert
`AutoLeadGenProviderComplaintHardStop` must page on-call. A lock may be released
only after a fresh no-send SMTP/IMAP probe and an authenticated
`POST /api/v2/safety-locks/{id}/release` with a unique `Idempotency-Key`, a
remediation reason, a non-secret evidence ID, and `human_confirmed=true`.
Transient `provider_in_flight` locks cannot be released through this API.

Return to Review mode or hard-pause immediately on any complaint, any unknown
Provider result, one duplicate/bypass, stale health/heartbeat, or a breached
bounce/unsubscribe threshold. Automatic volume never increases during the same
change that introduced a warning.

## 11. Stop, hold, and rollback criteria

### 11.1 Immediate hard stop

Immediately set global/channel outbound hard pause, stop new claims, fence
active leases, keep inbound/Consent processing alive, and page the release,
database, security, and compliance owners if any of these occurs:

```bash
python3 scripts/set_runtime_control.py \
  --directory /secure/autoleadgen/runtime-control \
  --control outbound_hard_pause --value true --change-id INCIDENT-0000
docker compose --env-file /secure/autoleadgen/production.env \
  -f compose.production.yml stop outbound-worker
```

The first command is the containment boundary. The second prevents further
claims while preserving the API, inbox worker, unsubscribe POST, Consent, and
audit paths. A send already accepted by SMTP cannot be recalled and must be
reconciled as an irreversible event.

- any real or billable call before the approved workflow #18 Attempt;
- any duplicate send/task, idempotency-key collision with different payload, or
  automatic retry of an `unknown` Provider result;
- any Consent, unsubscribe, SafetyLock, invalid/unverified contact, cooldown,
  account-health, budget, or global-pause bypass;
- any message sent to a Company/Contact outside the approved canary manifest;
- any credential or unsanitized/routable PII exposure;
- any untrusted/unsigned webhook accepted as authoritative;
- any owner/cross-company corruption, missing immutable history, checksum
  mismatch, or unreconciled quarantine item;
- an Alembic/schema state outside a rehearsed state, replication/data-integrity
  failure, or inability to restore;
- an untraceable Attempt/Message/Cost/Task/Audit chain; or
- loss of the kill switch, audit path, inbox cursor integrity, or incident
  communication channel.

### 11.2 Hold before advancing

Do not release the next cohort/batch when a worker is stale beyond the approved
heartbeat window, StageRuntime disagrees with heartbeat, budgets cannot be
priced exactly, a Provider result remains unknown, a canary unsubscribe/bounce/
complaint has not been reconciled, a daily shadow report has a warning, or
business quality exceeds the pre-signed bounce/complaint/unsubscribe limits.

### 11.3 Rollback levels

| Level | Action | Data rule |
| --- | --- | --- |
| R0: contain | Hard-pause all outbound; stop claims; preserve inbound Consent, webhook quarantine, and audit. | No data deletion. |
| R1: read fallback | Set the read-path feature flag back to `legacy` for affected cohorts, then globally if needed. | V2 writes stop or remain fenced; V2 tables remain for diagnosis. |
| R2: application rollback | Deploy the last approved API/UI/worker artifact using the **rotated** secrets. | Additive schema stays at current head; never run destructive downgrade. |
| R3: legacy-write recovery | Only under a new incident approval: freeze all V2 writers, export/reconcile the V2 delta, then re-enable a proven V1 write path. | Prevent split-brain. Consent and immutable V2 events must be projected back or enforced independently first. |
| R4: database restore/forward repair | Database owner follows the rehearsed statement-specific plan or restores to a new database and replays approved deltas. | Required only for proven database corruption; not a routine app rollback. |

The read-path fallback must be tested before G8. It must not mutate, delete, or
reinterpret V2 records. If V1 cannot enforce newly recorded Consent or safety
events, V1 writes stay disabled even while reads fall back.

## 12. Events that cannot be rolled back

Rollback cannot undo the outside world or erase compliance evidence:

- a delivered/accepted message cannot be unsent;
- a Provider lookup or send may remain billable, including a paid miss;
- unsubscribe, spam, bounce, ConsentRestriction, and authoritative Provider
  events remain permanently enforceable;
- MessageEvent, OutreachAttempt, ProviderCostEvent, AuditEvent, manual override,
  and reconciliation records are immutable and retained;
- inbound messages/webhooks may arrive after containment and must be safely
  ingested or quarantined;
- a credential revoked during rotation is not restored; and
- an Opportunity decision is corrected by a new audited transition, not by
  deleting history.

Compensation means creating restrictions, reconciliation Tasks, credits/refunds
where Provider policy permits, corrected projections, and customer follow-up
approved by compliance. It never means deleting evidence or automatically
resending an uncertain Attempt.

## 13. Evidence template

Copy this template into the restricted change record for every rehearsal,
shadow day, canary batch, cutover, and rollback. Do not include secrets, raw PII,
message bodies, access tokens, database URLs, or routable account identifiers.

```text
Change record:
Phase / gate:
Environment classification:
Start UTC / end UTC:
Release owner / operator / independent observer:

Artifacts
- source commit:
- build/image digest:
- Alembic revision + frozen DDL digest:
- configuration manifest digest (secret-free):
- sanitization/backfill tool versions:

Approvals
- approver / role / decision / UTC / artifact digest:
- expired or conditional approvals:

Database and snapshot
- engine/version, timezone, collation (no host):
- encrypted snapshot object ID + SHA-256:
- source log position and restore test ID:
- sanitization manifest digest + exception count:
- schema before/after digest and migration lock evidence:

Backfill
- mode: dry-run | apply | resume | idempotent-rerun
- checkpoint artifact digest and last source ID:
- quarantine artifact digest and counts by reason/disposition:
- source/target counts by owner/table:
- business-state checksum:
- immutable-history and owner-isolation reconciliation:

Shadow / canary
- day or batch ID and approved Company/Attempt IDs (non-PII):
- planned/claimed/succeeded/blocked/unknown counts:
- external calls and billable units:
- duplicate, bypass, orphan, and traceability counts:
- heartbeat/StageRuntime status:
- Consent/bounce/complaint/unsubscribe outcomes:
- budget reserved/settled/refunded with currency and price version:

Decision
- GO | HOLD | STOP | ROLLBACK:
- criteria evaluated and queries/dashboard snapshot IDs:
- deviations and signed dispositions:
- next allowed action and earliest UTC:

Incident / rollback (if applicable)
- first detection UTC and detector:
- containment UTC and kill-switch evidence:
- last known safe Attempt/job/cursor/schema position:
- rollback level and feature-flag audit event:
- irreversible events and compensation Tasks:
- reconciliation result and follow-up owner/due date:
```

## 14. Closure and later contract phase

Cutover is not complete when the V2 page loads or the first canary message is
accepted. G8 closes only after the stabilization period has clean evidence,
every unknown result and Task is reconciled, all cohorts use the intended read
path, rollback remains available, and all owners sign the final record.

Legacy API/table removal, data retention changes, real auto mode, LinkedIn or
WhatsApp enablement, larger budgets, broader audiences, and workflow #18
follow-ups each require a separate change. Until G9, the legacy schema remains
readable for reconciliation and the additive V2 histories remain untouched.
