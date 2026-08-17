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

**Current branch: `codex/reviewer-user-management`, from merged PR #136 on `main` at `511f16f`.** Migrations
through **`db/043`** are frozen; this round adds **`db/044`**.

**⇒ JUST FINISHED — reviewer accounts, service authentication and first-run administration.** Canonical design:
[`2026-08-17-drugref-reviewer-user-management-design.md`](superpowers/specs/2026-08-17-drugref-reviewer-user-management-design.md).
`reviewer-domain/` shares API types; `reviewer-service/` is the Axum/SQLx trust boundary; Tauri keeps bearer
tokens in native memory and the WebView retains no network permission. `db/044` adds stable accounts,
append-only profile/password/key-enrolment history, digest-only sessions and insert-only revocations.

**FIRST RUN BLOCKS BEFORE WORKSPACE LOAD.** With no live administrator profile, the app shows only first-admin
registration. Bootstrap is advisory-lock serialized, rechecked inside its transaction, forces the administrator
role, and closes permanently once an administrator exists (disablement does not reopen it). Administrators can
list/create users in the GUI; the service checks the role independently. Login is Argon2id-backed, rate-limited
and constant-shape for missing/wrong/disabled accounts. Login remains authorisation, never a clinical signature.

**THE CLINICAL QUEUE IS STILL DELIBERATELY READ-ONLY AND FIXTURE-BACKED.** Authentication is live; decision,
annotation and signing controls are not. Do not wire Tauri directly to PostgreSQL and do not enable a clinical
button in the next read slice.

**⇒ DO THIS NEXT FOR THE GUI:** replace the bundled queue with live, paginated read-only service endpoints and
database-derived vocabularies/filters. Then append-only annotations/evidence, curated revision transactions, and
local key enrolment/signing in separate slices. The administration tail is profile correction, disable/enable,
password rotation, all-session revocation and signing-key enrolment UI over `db/044`.

**Verification completed:** full Python/PostgreSQL suite 1,779 passed; Rust domain/service/Tauri suites 3 + 4 +
3; `ruff`; `cargo fmt --check`; `npm run check` with 0 diagnostics; `npm run build`; `npm audit` with 0
vulnerabilities; native no-bundle build; local end-to-end bootstrap → login → administrator create/list. Frontend
output is 0.63 kB HTML + 17.54 kB CSS + 69.14 kB JS (24.75 kB gzipped). The prior desktop/narrow visual-pass
limitation remains until a browser surface is connected.

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

- Dev DSN: `host=localhost port=5532 dbname=drugref_test user=postgres`. Set `DRUGREF_TEST_DSN` for DB tests.
- The verification database and its exact migration state live once in PROJECT-NOTES § "How to run / test";
  do not copy that volatile map here.
