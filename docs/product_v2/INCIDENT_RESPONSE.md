# Product V2 incident response

Updated: 2026-07-17. This runbook assumes an approved production deployment and
named on-call roles. It does not authorize a production change by itself.

## First five minutes

The first operator contains external effects before investigating:

```bash
python3 scripts/set_runtime_control.py \
  --directory /secure/autoleadgen/runtime-control \
  --control outbound_hard_pause --value true --change-id INCIDENT-0000
docker compose --env-file /secure/autoleadgen/production.env \
  -f compose.production.yml stop outbound-worker
```

Keep the API and inbox worker running unless they are the source of compromise.
They preserve unsubscribe, complaint, reply, ConsentRestriction, audit, and late
Provider-result ingestion. Record detection time, containment time, release SHA,
current database revision, worker heartbeats, last known safe Attempt ID, alert
snapshot ID, and incident commander. Do not resend, delete evidence, downgrade
the schema, unlock an in-flight Provider state, or rotate a control off while
diagnosing.

Escalate as severity 1 for an unauthorized/duplicate send, Consent or SafetyLock
bypass, leaked credential or customer data, cross-owner data access, database
corruption, failed containment, or inability to account for an accepted SMTP
request. A single abuse complaint is at least severity 2 and is an immediate
sender-account hold.

## Triage paths

- `sending`/`unknown` Attempt or reserved/unknown cost: keep its transient locks,
  stop automatic retries, reconcile against authoritative Provider evidence, and
  close the reconciliation Task only after sent/not-sent status is proven.
- Abuse complaint: confirm the `complained` MessageEvent, recipient suppression,
  unhealthy sender status, urgent Task, and account SafetyLock. Review audience,
  content, DNS/reputation, and Provider feedback-loop evidence.
- Bounce/unsubscribe threshold: stop the affected cohort, preserve restrictions,
  sample the approved manifest, and compare the immutable sent-event denominator.
- Credential exposure: revoke and rotate through the secret manager, retain audit
  logs, recreate affected processes with the new secret reference, and never
  restore the exposed value during rollback.
- Database/schema incident: stop all writers, preserve the migration advisory-lock
  evidence, and use the rehearsed forward repair or restore-to-new-database plan.
  Application rollback leaves the additive schema at its current head.

## Controlled recovery

Recovery requires a new change decision, a healthy private metrics scrape, fresh
API/outbound/inbox heartbeats, zero unresolved Provider boundaries, and completed
reconciliation. For a complaint lock, run the approval-bound no-send SMTP/IMAP
probe while outbound is paused. Then release only the exact durable lock through
the authenticated safety-lock API with:

```json
{
  "reason": "Documented remediation of at least ten characters",
  "evidence_id": "INCIDENT-0000-remediation",
  "human_confirmed": true
}
```

The request also requires a unique `Idempotency-Key`. The API rejects stale or
unhealthy account state, conflicting replay, already released locks, and all
transient `provider_in_flight` locks. It completes the linked deliverability Task
and records who released the lock and which evidence was reviewed.

Recreate the outbound worker while the live pause remains engaged, run
`production_preflight.py --phase enable-real`, and obtain the incident
commander's explicit go decision before releasing the pause. Start in Review
mode at a lower daily cap; automatic mode needs its own still-valid approval.

## Closure evidence

Closure records the root cause, affected owner/account/Campaign/Attempt IDs
(non-PII), irreversible external events, suppression and customer obligations,
credential versions changed, exact rollback/recovery actions, monitoring proof,
all open follow-ups with owners and dates, and a test that would have detected
the failure earlier. MessageEvent, ConsentRestriction, SafetyLock, Provider cost,
and Audit history are retained; correction is a new audited event, never deletion.
