# Restored RDS rehearsal evidence — 2026-07-17

Scope: temporary Alibaba RDS restored from the existing production backup.
The production RDS was queried read-only and received no schema or data writes.
Outbound hard pause stayed enabled and all real external calls stayed disabled.

## Result

- Exact 23-table legacy contract validated and separately stamped at
  `0001_legacy_v16_baseline`.
- Alembic upgraded through `0006_message_event_complaint`.
- All 984 legacy Leads were scanned with 25-row keyset batches.
- Apply created 3,784 V2 rows while all 23 legacy table counts remained equal
  to the source: 22,310 rows on each side.
- Two `--apply --resume` runs processed zero Leads and created zero rows.
- Stable logical checksum on both resumes:
  `59a013fe30295a0dd3d9c72e4ba07a868107ca4644ef4ba177e644a4f8616811`.
- Reconciliation found zero foreign-key orphans, zero cross-owner graph
  mismatches, and zero duplicate Company-domain, ContactPoint-identity,
  Enrollment, or legacy-mapping groups.
- The single legacy unsubscribe migrated to a V2 ConsentRestriction. Product
  V2 and the final SMTP boundary also perform an exact read-only legacy
  suppression check during coexistence.

## Data disposition still required

- 172 duplicate legacy Lead rows reused one canonical Campaign/Contact
  Enrollment. Their histories were retained; the result contains 373 Outreach
  Attempts and 399 Message Events. A reviewer must accept this mechanical
  mapping before production cutover.
- 407 Leads had neither a verified company domain nor an email, so no company
  identity was guessed. All 407 remain intact in the legacy tables and have a
  LinkedIn profile; 387 were already `needs_email` and 20 were `found`. They
  require enrichment, an explicitly reviewed mapping, or explicit deferral
  before the affected owner can leave the legacy write path.

This rehearsal closes the technical restored-data execution check on one
temporary instance. It does not approve production migration, owner cutover,
automatic sending, or the required independent second restore rehearsal.
