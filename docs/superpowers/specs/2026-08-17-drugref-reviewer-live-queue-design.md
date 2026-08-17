# Drugref reviewer live queue

**Date:** 2026-08-17
**Status:** implemented

## 1. Outcome

The installed reviewer application replaces its bundled clinical workspace with an
authenticated, paginated read from PostgreSQL through `reviewer-service/`. The queue
is still strictly read-only: no annotation, decision, revision or signature endpoint
exists, and every clinical write control remains disabled.

The browser-only Vite surface keeps representative data for layout work. It is visibly
labelled as a preview and is never the native application's fallback when a live queue
request fails.

## 2. API contract

`GET /v1/review-queue` accepts `page`, `pageSize`, `kind`, `source`, `relationship`
and `search`. A request requires any live reviewer session; queue reads are not
administrator-only. Page size is bounded to 1–100, page numbers are bounded, filters
must be non-blank and short, and search is a literal case-insensitive substring rather
than a SQL wildcard expression.

The response carries:

- current totals for interaction-rule gaps, condition contradictions and expanded
  curated DDI pairs;
- the kinds, sources and relationships present in the current queue;
- page number, page size, filtered total and total pages;
- stable natural-key targets, names, all contributing relationships, assertion
  sources, upstream releases, expansion impact and explanatory review copy.

Shared request and response types live in `reviewer-domain/`. The Tauri core owns the
HTTP request and bearer token; the WebView receives structured queue data but no token
or database credential.

## 3. Database read

The service unions `gap_uncurated_interaction_rule` and
`gap_uncurated_condition_contradiction`, then enriches them from their projection rows
and `ingest_run`. The union is materialised once per request so metadata, filters,
filtered count and page rows describe one PostgreSQL statement snapshot. Source and
release fields are arrays because the curated natural key deliberately omits source
and more than one upstream authority may assert the same candidate.

Interaction targets remain `(subject_moiety_uuid, object_class_uuid, relationship)`;
condition targets remain `(subject_moiety_uuid, object_condition_uuid)`. Pagination
uses impact descending followed by names and UUIDs for deterministic ties. This slice
adds no migration, cache or stored queue state.

## 4. GUI behavior

Login waits for the first live page before opening the workspace. Search is debounced;
type, source and relationship filters reset to page one; stale overlapping responses
cannot replace a newer result. Previous/next controls use server pagination, retain a
valid selection where possible, and show an inline error while preserving the last
successful page.

The old fixture-only status and priority labels are removed because no database state
backs them. A gap row is labelled **Unreviewed**, not **Unsigned**: there is no curated
assertion to sign yet. The header distinguishes **Live read-only queue** from **Browser
queue preview**, and the footer states that signing remains disabled.

## 5. Deliberate limits

This endpoint is an internal reviewer worklist, not the public API. It uses bounded
offset pagination because the queue is small and no clinical write can mutate it in
this slice. If later write traffic makes stable traversal necessary, the API can move
to a cursor over its deterministic ordering without changing target identity.

No `in_review` state is invented. That state can exist only after an append-only
annotation or assignment model defines it. Clinical decision vocabularies become live
form options in the separate curated-write slice; this read slice exposes only queue
filter vocabularies already present in the database.

## 6. Verification

The gate includes shared-domain validation tests, service unit tests, a read-only
integration test against a populated reference database, Tauri tests, Rust formatting,
Svelte diagnostics, production frontend build, npm audit and the full PostgreSQL-backed
Python suite. A native no-bundle build verifies the IPC boundary. Desktop and narrow
visual checks remain required whenever a connected browser surface is available.
