# Product V2 local development

The fastest Product V2 workspace uses the repository's isolated SQLite entrypoint:

```bash
./scripts/dev.sh setup
./run.sh
```

This starts the API and frontend with fake connectors, a hard outbound pause, and
local state under `.local/dev/`. Use `./scripts/dev.sh password` for the generated
acceptance login and `./scripts/dev.sh check` for complete local verification.
No command in this document targets production.

The optional MySQL integration environment below uses an isolated MySQL 8.4
database and the same fake-only safety boundary.

## Start the isolated database

```bash
./scripts/product_v2_local.sh up
```

The script generates owner-only Docker secret files under `.local/secrets`, binds MySQL
only to `127.0.0.1:3307`, waits for its health check, and runs the complete Alembic
chain through `0008`. Docker Desktop or another compatible Docker daemon is required.

Run the MySQL integration suite against a separately named test database:

```bash
./scripts/product_v2_local.sh test
```

The command drops and recreates only the hard-coded `autoleadgen_v2_test` database
before each run, so interrupted test data cannot leak into the next result. The root
password is read from the in-container Docker secret rather than passed on the command
line. Pytest also refuses any MySQL test URL whose database name does not end in
`_test`, and all connectors stay fake.

## Safety defaults

Use these values for every local API and worker process:

```text
AUTOLEADGEN_ENV=local
AUTOLEADGEN_CONNECTOR_MODE=fake
ALLOW_REAL_EXTERNAL_CALLS=false
ALLOW_REAL_ACQUISITION_CALLS=false
PRODUCT_V2_LEGACY_READ_ONLY=true
PRODUCT_V2_OWNER_PATH_ENFORCEMENT=false
PRODUCT_V2_LEGACY_WRITERS_FROZEN=false
PRODUCT_V2_LEGACY_WRITE_RECOVERY_APPROVED=false
```

The connector registry rejects non-fake connectors in local/test environments even if
other flags are misconfigured. Paid search, enrichment, and verification additionally
require `ALLOW_REAL_ACQUISITION_CALLS=true` in an approved non-local environment;
the invitation pilot keeps it false and therefore fake-only. Auto mode still produces
only fake events locally.

## Migration policy

- `0001_legacy_v16_baseline` builds the declared legacy v16 baseline on an empty database.
- A future verified existing v16 database must be schema-checked before it can be stamped;
  it must not be replayed through v2-v16 scripts.
- `0002_product_v2_expand` only adds Product V2 tables and indexes.
- `0003_contact_point_identity_hash` repairs already-stamped isolated databases
  with the fixed-width ContactPoint identity digest required by MySQL utf8mb4.
- `0004_owner_cutover_accounts` adds the owner write-path state, tenant-scoped
  sender accounts, and account bindings/capacity indexes using frozen static DDL.
- `0005_outreach_templates` adds immutable per-step Email subject/body templates
  required by the real SMTP canary path.
- `0006_message_event_complaint` adds a first-class Provider complaint event and
  updates the frozen event-type constraint without deleting message history.
- `0007_acquisition_activation` adds the owner-scoped AcquisitionRun and
  AcquisitionCandidate staging workspace used by CSV and AI acquisition. It does
  not change existing Company, Contact, Campaign, Enrollment, or Attempt rows.
- `0008_go_live_batches_and_routes` adds go-live review batches, frozen
  consent, and task scoped routes required by the controlled Email canary.
- All eight revisions preserve application data; the `0006` downgrade additionally
  refuses to run while complaint evidence exists.
- MySQL migrations use a zero-wait advisory lock and fail if another migrator is active.

The owner migration state controls the **business write path only**. It is not
the read-fallback switch. Production-like legacy-to-V2 changes also require
`PRODUCT_V2_LEGACY_WRITERS_FROZEN=true` as explicit evidence that old API,
PM2, and background writers have already stopped. V2-to-legacy writes remain
blocked unless the independent R3 approval flag is set and all Consent,
SafetyLock, uncertainty, reconciliation, and cooldown projection checks pass.

`migrate_v16.py` and all workflow 18 scripts are historical evidence only and must not
be executed by Product V2 tooling.

### Validate an existing legacy v16 schema

The validator deliberately ignores the application's `DATABASE_URL` and repository
`.env`. Give it a dedicated target through `LEGACY_V16_DATABASE_URL`:

```bash
LEGACY_V16_DATABASE_URL='<explicit legacy database URL>' \
  .venv/bin/python scripts/validate_legacy_v16_schema.py --pretty
```

The default mode is read-only. It checks a frozen manifest of required legacy tables
and columns, emits JSON, exits non-zero on a mismatch, and never creates or stamps an
Alembic revision. File-backed SQLite targets must already exist and are opened in
read-only mode. The JSON target URL always redacts credentials.

Stamping is a separate, explicit isolated-local operation and is not a production
runbook:

```bash
AUTOLEADGEN_ENV=local \
PRODUCT_V2_ISOLATED_DATABASE=true \
LEGACY_V16_DATABASE_URL='<isolated SQLite or loopback autoleadgen_v2* MySQL URL>' \
  .venv/bin/python scripts/validate_legacy_v16_schema.py --stamp --pretty
```

`--stamp` fails before connecting unless the environment is `local`/`test`, the
isolation acknowledgement is present, and the target is SQLite or a loopback MySQL
database named `autoleadgen_v2*`. It also refuses to replace an existing different
Alembic revision. Schema validation must pass before `0001_legacy_v16_baseline` is
written.

`0001` and `0002` now contain checked-in static DDL and index snapshots; they no
longer import runtime SQLAlchemy metadata to decide their schema. Local schema
fingerprints cover column types/nullability, primary and foreign keys, unique and
ordinary indexes, and check constraints. This is still not production approval.
Before any production cutover, independently audit the frozen artifact (including
defaults, collation, locking, duration, interrupted-state repair, and MySQL behavior)
against a restored sanitized production snapshot. The legacy validator checks
table/column presence only; it does not replace that audit.

Disposable databases created earlier in this development branch should still be
recreated after Product V2 model changes. Alembic sees the same `0002` revision and
cannot infer columns added after that revision; a 2026-07-16 local replay confirmed
this failure mode. The explicit `0003` revision repairs the known ContactPoint digest
gap, but it is not a substitute for recreating disposable databases or auditing the
complete frozen migration DDL before testing an upgrade of any durable snapshot.

## Backfill an isolated copy

Backfill refuses production/remote databases. The target must be local/test, explicitly
marked as isolated, and either SQLite or loopback MySQL with a database name beginning
with `autoleadgen_v2`.

```bash
AUTOLEADGEN_ENV=local \
PRODUCT_V2_ISOLATED_DATABASE=true \
DATABASE_URL='<isolated autoleadgen_v2 database URL>' \
  .venv/bin/python scripts/backfill_product_v2.py --dry-run
```

Applying the backfill requires a second acknowledgement:

```bash
AUTOLEADGEN_ENV=local \
PRODUCT_V2_ISOLATED_DATABASE=true \
PRODUCT_V2_BACKFILL_APPLY=true \
DATABASE_URL='<isolated autoleadgen_v2 database URL>' \
  .venv/bin/python scripts/backfill_product_v2.py --apply --resume
```

## Shadow acceptance replay

Run the 30-company acceptance replay in its own disposable database:

```bash
.venv/bin/python scripts/shadow_replay_product_v2.py \
  --company-count 30 \
  --output .local/shadow-replay/acceptance.json
```

The command deliberately ignores the application's `DATABASE_URL`, creates a new
output-adjacent SQLite database, applies the complete Alembic chain, and refuses to
reuse an existing replay database. The JSON report fails closed unless all three fake
channels produce zero external calls, hard consent/safety cases remain blocked,
attempts are unique and traceable, and StageRuntime agrees with persisted worker
heartbeats.

## OpenAPI contract and generated frontend types

Regenerate both artifacts whenever a V2 request, response, enum, or error schema
changes:

```bash
.venv/bin/python scripts/export_openapi.py
npm --prefix frontend run generate:api-types
```

The exporter forces a local SQLite URL, fake connectors, read-only legacy mode, and the
outbound hard pause. CI/release checks should then use both drift guards:

```bash
.venv/bin/python scripts/export_openapi.py --check
npm --prefix frontend run check:api-types
```

## Signed Provider webhooks

Provider ingress is `POST /api/v2/webhooks/{owner_id}/{provider}/events`. It is
authenticated by HMAC rather than a user's JWT and requires these headers:

- `Idempotency-Key`
- `X-AutoLeadGen-Webhook-Timestamp` (Unix seconds)
- `X-AutoLeadGen-Webhook-Event-Id`
- `X-AutoLeadGen-Webhook-Signature` (`v1=<hex sha256>`)

`Idempotency-Key` must exactly equal the signed event id. The HMAC-SHA256 input
is the byte concatenation below; the final element is the request body exactly
as transmitted, without JSON reserialization:

```text
v1\n{provider}\n{owner_id}\n{timestamp}\n{event_id}\n{raw_body_bytes}
```

The canonical Provider value is its lowercase normalized path identifier;
`owner_id` and `timestamp` are base-10 integers. Newline/control characters are
not allowed in Provider or event-id fields, so the newline-delimited prefix is
unambiguous. Every routing/replay field and the exact body bytes are therefore
cryptographically bound.

Secrets must contain at least 32 bytes and are resolved without database access
in this order: `PRODUCT_V2_WEBHOOK_SECRET_OWNER_{owner_id}_{PROVIDER}`,
`PRODUCT_V2_WEBHOOK_SECRET_{PROVIDER}`, then `PRODUCT_V2_WEBHOOK_SECRET`. Dots
and hyphens in the upper-case Provider name become underscores. Never commit a
secret value to `.env`; local tests pass an ephemeral value, and any later real
deployment must inject an approved secret-manager reference.

The default timestamp window is 300 seconds and may be tightened (maximum 3600)
with `PRODUCT_V2_WEBHOOK_TOLERANCE_SECONDS`. Raw bodies are streamed into a
bounded buffer and default to a 1 MiB maximum; set
`PRODUCT_V2_WEBHOOK_MAX_BODY_BYTES` to a positive number no greater than 10 MiB
to use a smaller approved Provider limit. Oversized requests fail with `413`
before signature or JSON processing. Exact duplicate bytes return the
original immutable MessageEvent. Reusing an event id for different bytes fails
closed. Authenticated Provider event types outside the V2 enum become `unknown`
evidence and create exactly one reconciliation Task and audit event; they do not
guess at Attempt, Conversation, Consent, or deliverability state.

## Campaign run modes

- `shadow` may execute only through a fake connector and records simulated events.
- `review` creates a `draft_review` Task containing an exact Attempt preview; only an
  explicit owner approval requeues that Attempt, and dismissal cancels it.
- `auto` uses the normal gate path, but local/test registries still reject every real
  connector regardless of Campaign configuration.
