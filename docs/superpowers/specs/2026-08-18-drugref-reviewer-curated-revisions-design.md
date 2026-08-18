# Drugref reviewer curated revisions

**Date:** 2026-08-18
**Status:** implemented

## 1. Outcome

An authenticated reviewer can record an interaction judgement or condition ruling
from the live queue. The service writes the existing `curated_interaction` or
`curated_condition` overlay with its required insert-then-supersede transaction and
returns the database-derived immutable history.

This slice deliberately stops before signing. A newly recorded revision is visibly
unsigned, and no private-key, enrolment or assertion-signature operation is added.

## 2. Target and concurrency contract

The request carries the queue's frozen `(kind, targetKey)` and the revision identifier
the reviewer saw, or null when no revision existed. The service resolves the immortal
open-question UUID and natural-key UUIDs itself; it does not accept a client-supplied
question UUID, reviewer name, release string or curated row identifier.

Each write takes a transaction-scoped advisory lock for the canonical target, reads the
live predecessor, and compares it with `expectedRevisionId`. A stale form receives a
conflict instead of silently superseding a decision recorded by another reviewer.

## 3. Clinical transaction

The shared request distinguishes interaction `applies` / `does_not_apply` decisions
from condition `contraindicated` / `indicated` / `context_dependent` / `spurious`
rulings. Applying and non-spurious decisions require a severity and evidence grade;
retiring/spurious decisions require both to be absent. Mechanism and management remain
bounded optional prose.

Inside one PostgreSQL transaction the service:

1. resolves the open question and canonical natural key;
2. locks and checks the expected live predecessor;
3. derives `reviewed_against` from current candidate ingest releases, falling back to
   the predecessor only for a correction whose candidate has since disappeared;
4. inserts the new row with `source = 'DRUGREF'` and `reviewed_by` from the current
   authenticated reviewer profile;
5. points the predecessor at the later row; and
6. returns the ordered revision history before committing.

The existing deferred single-live and append-only triggers remain the database floor.
No migration is required; migrations through `db/045` remain frozen.

## 4. Service and native boundary

Any active reviewer session may use `GET /v1/review-decision` and
`POST /v1/review-decision`. Shared Rust types validate bounded input. Tauri retains the
bearer token and forwards only typed decision requests and responses to the WebView.

History includes the detached signature status already computed by the database. This
slice only renders that state; it cannot create a signature.

## 5. GUI behavior

The detail pane loads target-scoped decision history beside working records. The form
changes vocabulary for interaction and condition targets, enforces the ruling-dependent
grade fields, and previews the immutable values plus the predecessor it will supersede.
The reviewer confirms **Record revision** in a second step.

After success, the detail shows the stored row as unsigned and refreshes the queue so
the newly answered gap leaves the worklist. Browser preview uses isolated in-memory
revision history and remains explicitly non-persistent.

## 6. Verification

The gate covers shared validation, transaction helpers, a live PostgreSQL initial-write,
revision and stale-write conflict round trip, Tauri compilation, Svelte diagnostics,
the production frontend build, Rust formatting/clippy, npm audit, relevant Python
curation/schema tests and `git diff --check`.
