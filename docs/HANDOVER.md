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

**Current branch: `codex/reviewer-gui`, from merged `main` at `2cc9f45`.** The FDA-CYP slice and review are
merged through **`db/043`**; every migration through 043 is frozen. A new schema change starts at **044**.

**⇒ JUST FINISHED — the human reviewer GUI foundation, no migration.** Canonical design:
[`2026-08-17-drugref-reviewer-gui-foundation-design.md`](superpowers/specs/2026-08-17-drugref-reviewer-gui-foundation-design.md).
`reviewer-app/` is Tauri 2 + plain Svelte/TypeScript with a Rust IPC boundary. It presents a preview-only login
and reviewer profile, queue totals and filters, live-shaped review records, provenance, decision fields,
annotations and signature posture. One committed JSON fixture feeds both native IPC and browser preview;
Rust validates the fingerprint, complete targets and target uniqueness.

**THE FOUNDATION IS DELIBERATELY READ-ONLY.** All clinical-write, annotation and signing controls are disabled
and labelled. It has no database connection, account table, session, private-key store or migration. Do not
enable a button by wiring the Tauri client directly to PostgreSQL: a shared client credential makes the login
cosmetic and bypassable.

**The production trust boundary is decided:** Tauri client → authenticated Rust review service → PostgreSQL.
Passwords are server-side Argon2id hashes. Private Ed25519 keys stay encrypted on the reviewer's device; the
existing `signing_key` registry remains the public-key authority. Login authorises an operation but is not a
clinical signature. The service independently re-derives every signed payload before inserting the detached
signature.

**⇒ DO THIS NEXT FOR THE GUI:** design and build the reviewer-account migration plus authenticated Rust service
skeleton. Likely storage: stable `reviewer_account`; append-only `reviewer_profile` and
`reviewer_password_credential`; `reviewer_key` mapping to `signing_key`; revocable sessions; append-only notes
on `question_uuid`. Exact DDL is not yet accepted — start with its own design/TDD round. Then replace the fixture
with live, paginated read-only queue endpoints before taking any clinical write.

**Verification completed:** 2 Rust unit tests; `cargo fmt --check`; `npm run check` with 0 errors/warnings;
`npm run build`; `npm audit` with 0 advisories; debug and release native builds. The frontend output is
0.63 kB HTML + 14.51 kB CSS + 58.21 kB JS (21.45 kB gzipped); the release macOS app is **8.3 MB** with an
**8.0 MB** executable. The in-app browser was unavailable, so the
desktop/narrow visual pass in the design spec remains to run when a browser surface is connected.

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
