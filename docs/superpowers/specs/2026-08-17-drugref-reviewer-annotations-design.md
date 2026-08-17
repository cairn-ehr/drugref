# Drugref reviewer annotations and evidence references

**Date:** 2026-08-17
**Status:** implemented

## 1. Outcome

An authenticated reviewer can read and append Markdown working notes and structured
evidence references for a live interaction-rule or condition-contradiction target.
Every row is immutable, attributed to a stable reviewer account and ordered as durable
research history.

This slice does not create question state, an evidence verdict, a clinical ruling, a
curated assertion, a grade, a signature or a signing-key operation. The GUI's decision
fields and **Record & sign decision** control remain disabled.

## 2. Target identity

The queue now returns the open-question registry's frozen canonical gap-key shape:

- `MOIETY:{uuid}/CLASS:{uuid}/CI_AXIS:{relationship}` for an interaction rule;
- `MOIETY:{uuid}/CONDITION:{uuid}` for a condition contradiction.

Working-record endpoints resolve `(kind, targetKey)` through the current
`open_question` row. The service does not mint a second question UUID or accept a stale
target silently. A target that is no longer current returns not found; the next queue
read is the recovery path.

## 3. Database model (`db/045`)

`reviewer_annotation` stores question UUID, reviewer UUID, bounded non-blank Markdown
and recording time. `reviewer_evidence_reference` stores the same attribution plus one
of the existing resolvable schemes (`DOI`, `PMID`, `PMCID`, `NCT`, `SPL`, `URL`), a
bounded non-blank identifier or URL, and optional bounded Markdown context.

Both tables are insert-only under `forbid_any_rewrite`; they expose no supersession or
delete path because a working-note correction is a later note, not an edit to history.
The reference table deliberately has no `supports`/`refutes` verdict, confidence,
evidence grade or `applies` field. Those are clinical judgements and belong to the later
curated-revision transaction.

Both tables reference the immortal `open_question.question_uuid`. The registry rebuild
retention guard includes them, so closing a derived gap marks its cited question
non-current instead of deleting research history.

## 4. Service and native boundary

Any active reviewer session may use:

- `GET /v1/review-record?kind=...&targetKey=...`;
- `POST /v1/review-annotations`;
- `POST /v1/review-evidence-references`.

The shared Rust domain validates target shape and text bounds. The service ignores any
client authorship claim and writes the reviewer UUID from the authenticated bearer
session. Returned rows join current profile text for human-readable attribution while
retaining the stable reviewer UUID and username.

As before, the WebView never receives a bearer token or database credential. Tauri owns
the authenticated HTTP requests and exposes only typed working-record commands.

## 5. GUI behavior

Selecting a queue row loads its working history. Overlapping requests are sequenced so
a response for an old selection cannot replace the current target. Inline load/save
errors remain inside the detail surface.

The detail pane displays immutable note and reference history, enables a bounded
Markdown note form, and enables a citation scheme/value/context form. Browser preview
uses isolated in-memory records for interaction and layout work; it remains explicitly
non-persistent and is never a native fallback.

Attaching a source does not label it supporting or refuting, and saving a note does not
change the queue's **Unreviewed** status. The next functional slice is the separate,
transactional curated interaction/condition revision path.

## 6. Verification

The gate includes db/045 schema and registry-retention tests, a live PostgreSQL service
round-trip, shared-domain/service/Tauri unit tests, Rust formatting and clippy with
warnings denied, Svelte diagnostics, production frontend build, npm advisory audit,
the full PostgreSQL-backed Python suite, and a native no-bundle build. Desktop and
740 x 900 Chrome interaction/visual passes cover target-specific append history and
confirm that decision recording remains disabled.
