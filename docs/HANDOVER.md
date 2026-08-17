# HANDOVER — drugref

> **The volatile half: where we are right now.** Regenerated at the end of every working session
> (nextsession rule 9) and kept **under 130 lines**, so a rewrite costs nothing.
>
> **THIS LINE IS THE ONLY HOME FOR THAT NUMBER.** Do not restate it in `CLAUDE.md`, a skill, or elsewhere.
> A bound is a vocabulary like any other, and this repo has repeatedly lost rounds to one rule kept in two
> places.
>
> **The stable half is [`PROJECT-NOTES.md`](PROJECT-NOTES.md)** — traps, state by layer, commands, schema and
> code map. Edited in place, under no bound. Slice sequencing is [`ROADMAP.md`](ROADMAP.md); canonical what/why
> is in the immutable specs under [`superpowers/specs/`](superpowers/specs/).

## ⇒ NEXT

**Current branch: `codex/reviewer-live-queue`, from merged PR #137 on `main` at `5a8eedd`.** Migrations through
**`db/044`** are frozen; this round adds no migration.

**⇒ JUST FINISHED — authenticated live, paginated reviewer queue.** Canonical design:
[`2026-08-17-drugref-reviewer-live-queue-design.md`](superpowers/specs/2026-08-17-drugref-reviewer-live-queue-design.md).
Any live reviewer session may call `GET /v1/review-queue`; Tauri attaches the bearer token from native memory and
the WebView retains no network permission or database credential. Page size is bounded, search is a literal
substring, and kind/source/relationship filters come from the current database queue.

**ONE MATERIALISED UNION OWNS EACH RESPONSE SNAPSHOT.** The service reads the interaction-rule and condition-
contradiction gap views, enriches stable natural-key targets from projection rows and `ingest_run`, and derives
totals, filters, filtered count and deterministic page rows together. Sources, releases and condition predicates
remain arrays because several assertions may support one source-neutral curated target. No queue table or cache
was added.

**THE CLINICAL QUEUE IS LIVE AND STILL DELIBERATELY READ-ONLY.** The fixture-only `in_review`, priority and
signature fields are removed; gaps say **Unreviewed**, because no curated row exists to sign. Search is debounced,
filters reset pagination, stale responses cannot overwrite a newer request, and inline failures preserve the last
successful page. The browser-only Vite adapter remains explicit preview data and is never a native fallback.

**GUI HOUSE-RULE CATCH-UP IS COMPLETE.** `CONTRIBUTING.md` is now the durable repository rule: functions and
public contracts require docstrings, behavioural values require documented constants, dynamically typed code
requires complete type hints, and pure reusable logic belongs in focused modules where meaningful. The GUI now
centralises validation and paging constants, keeps pure queue/presentation transforms outside components, and
enforces Rust public API documentation with `deny(missing_docs)`.

**⇒ DO THIS NEXT FOR THE GUI:** append-only annotations and evidence references without manufacturing a clinical
ruling. Then curated revision transactions and local key enrolment/signing in separate slices. The administration
tail remains profile correction, disable/enable, password rotation, all-session revocation and signing-key
enrolment UI over `db/044`. Do not enable a clinical decision or signing button in the annotation slice.

**Verification completed:** full Python/PostgreSQL suite 1,779 passed; domain 6; service 6 plus the populated-
database live-queue integration; Tauri 1; `ruff`; Rust formatting and clippy with warnings denied;
`npm run check` with 0 diagnostics; production frontend build; `npm audit` with 0 vulnerabilities; native debug
app bundle. Frontend output is 0.63 kB HTML +
18.10 kB CSS + 72.45 kB JS (26.04 kB gzipped). Two real reference-database queue reads took 11.34 s total,
including the known expensive interaction-gap view. No browser surface was available, so desktop/narrow visual
verification remains outstanding.

## Parallel project sequencing

The next **content** slice remains DrugCentral ([#101](https://github.com/cairn-ehr/drugref/issues/101)):
6,337 candidate public-domain moiety-grained pairs, rule 6 clear for `ddi_ref_id = 2` only. Every figure rests
on one unretained 1.4 GB dump run, so re-measure before design. It is the first content capable of populating
the class grain and meeting #105/#106/#112. Pregnancy/lactation and FDA toxicity remain ready but separately
gated as ROADMAP states.

Do not lose 5c.2g's two broad lessons: a comment claiming a guard exists is not evidence the code contains the
guard; and a plausible figure from a partially working parser is not a measurement. Full findings and seven
spec-figure corrections live in PROJECT-NOTES § "Slice 5c.2g".

## Open follow-ups

The full ledger lives once in [PROJECT-NOTES § "The standing open-issue ledger"](PROJECT-NOTES.md). For the
next rounds: #128/#129 and #132–#135 are FDA-CYP residue; #112/#105 wait on class-grain content; #124 is the
guard-round tail; #121/#123 remain open; #104 makes question counts depend on which unrelated ingest ran last;
#94's seven withheld entries need research. Before production: re-run every parser on current releases, resolve
#17, and complete the three outstanding rule-6 deeds (#6, #25, GSRS).

One decision is taken and not built: #86 adds `signed_by_unknown_key` as a fourth signature status in its own
vocabulary-widening round.

## Current DSN

- Test-only DSN: `host=localhost port=5532 dbname=drugref_test user=postgres`. Set `DRUGREF_TEST_DSN` for DB
  tests; never use this database for persistent reviewer accounts or GUI service data because pytest recreates it.
- The verification database and its exact migration state live once in PROJECT-NOTES § "How to run / test";
  do not copy that volatile map here.
