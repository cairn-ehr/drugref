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

**Branch `claude/drugcentral-ddi-ingest`, from `main` at `ae1d1d3`** (PR #147 merged 2026-08-23). Migrations
through **`db/050`** — this round added `049`, then `050` as its review-fix round. The suite total lives in
PROJECT-NOTES § "How to run / test" and **nowhere else**
([#146](https://github.com/cairn-ehr/drugref/issues/146)); read it there at the START of the session.

**⇒ JUST FINISHED — the DrugCentral `ddi` ingest, [#101](https://github.com/cairn-ehr/drugref/issues/101),
`db/049` + `db/050`.**
[Design](superpowers/specs/2026-08-23-drugref-drugcentral-ddi-ingest-design.md);
[measurement](superpowers/specs/2026-08-23-drugref-drugcentral-ddi-ingest-measurement.md); full account and
**every figure**: PROJECT-NOTES § "The DrugCentral ddi ingest". Do not re-derive them from here.

**What shipped.** `source = 'DRUGCENTRAL'` admitted (the `ingest_run_source` / `ingest_run_writer` /
`ids._SOURCE_CANONICAL` / `provenance.py` trio whose failure mode is a silent no-op rebuild).
`ddi_source_severity`, the upstream-band → drugref-grade mapping **as seeded data**;
`drugcentral_ddi_assertion`; `drugcentral_ddi_pair`, orientation collapsed, most-severe-wins; **`exact_ddi_pair`
— the read path exact drug–drug pairs have never had**, MED-RT's 1,442 `moiety_contraindication` rows having
reached no consumer view at all before it; `gap_unresolved_ddi_endpoint` and the **eighteenth** question kind.
Plus `tools/drugcentral_{dump,resolve}.py` promoted into `src/drugref/ingest/` with **no re-export shims**, and
a standalone `drugref ingest drugcentral` subcommand (NOT a chain step — 1.4 GB, one pinned release).

**Measured end to end on `drugref_dc049`: 12 predictions, 12 MATCHED, 0 mismatched, no code defects.** 7,621
rows read, 50 excluded by rule 6, 7,571 bundleable → 7,501 pairs, 37 unresolvable over 10 endpoint names, 0
self-pairs, in 20.2 s; `ddi_candidate_pair` (21,664) and `substance_moiety` (19,438) did not move, that view's
plan byte-identical before and after. **Two measurements changed the design**: `description` carries **no
clinical content** (all 7,571 match `NAME1/NAME2 [VA Drug Interaction]`), so the severity band is the whole of
what this source adds; and **the source asserts an UNORDERED pair** — 33 in both orders, 4 disagreeing with
themselves — which rules out the directional `moiety_contraindication`.

**Rule 6 discharged, and `NOTICE` says so.** Only `ddi_ref_id = 2` (VHA NDF-RT, US federal) is ingested;
Stockley's and Lexicomp are out, and DrugCentral's own CC BY-SA over the compilation is not evidence of a right
to relicense either. The constant is not trusted alone: the orchestrator re-reads the dump's `reference` row and
**aborts** on a renumber — and both halves of that comparison are now tested, the `authors` half having been
decorative. The fixture carries all three references with excluded `description` redacted; **#152** asks whether
those rows should be synthesised instead.

**⇒ FOUR PUBLISHED FIGURES WERE WRONG AND WERE CORRECTED IN PLACE; three were this project's own state
files.** The gap kind is the **eighteenth**; **`MAOIs or RIMAs` DOES exist in the dump** (10 rows, all
`ddi_ref_id = 1` — it is only as an *endpoint* that it appears zero times, so the re-measurement's own
correction over-generalised); **`class_contraindication_source` did NOT need widening**, the CHECK a new source
really needs being `ingest_run_writer`, which no document had named; and the suite count was stale again.

**⇒ THEN TWO REVIEW ROUNDS, BOTH FULLY APPLIED.** The whole-branch review found 0 critical / 3 important / 7
minor: a writer transposing its two endpoint names survived all 1,959 tests because no test wrote a MIXED row.
**Then the five-agent PR review of #150 — 1 critical, 9 important, and `db/050` is its answer.** The critical
one: **a dump this code cannot read wiped the projection and exited 0.** Renaming ONE column took the fixture
from 4 rows to 0 and reported *"0 bundleable of 8 rows (8 excluded by rule 6)"* — blaming rule 6 for a loss rule
6 had no part in. Every guard passed vacuously, because **every reconciliation in the slice proved the
orchestrator self-consistent and none proved it published anything.** That diagnosis is the round's one
transferable lesson; the full account and every finding are in PROJECT-NOTES § "The DrugCentral ddi ingest".

`db/050` adds what db/049 stated in comments and enforced nowhere (`upstream_key <> ''`, a `blank_endpoint`
route, `ddi_source_severity.source`) plus a **route-aware** gap view, the question text having asserted the
`unresolved` story about three routes under an immortal `question_uuid`. In code: floor checks before
`open_run`; a disjoint `Outcome` enum replacing two overlapping booleans whose branch ORDER was load-bearing;
`first_wins` folding in the registry's own key space (it de-duplicated raw and let `Registry` re-key
**last**-wins, uncounted and collation-dependent); the REPEATABLE READ bump **removed**, `load_registry` being
one statement now — that snapshot used to cover `register_from_gaps` and abort the run on any concurrent
question write; and an autocommit refusal, Postgres answering a mis-placed `SET TRANSACTION` with a *notice*.

**Coverage was the other half.** Mutation testing found 17 survivors concentrated in the orchestrator's tail:
`register_from_gaps`, `finish_run`, `commit`, the `pairs` count, the checksum, the `open_run` arguments and the
rollback could each be deleted or transposed with the suite green. All now killed, each re-verified by re-running
its mutation. **A sixth instance of one lesson: a comment claiming a guard is not evidence of the guard.**

## ⇒ DO THIS NEXT

**ROADMAP's next content slice is `5c.3` — SPL/DailyMed mining**, whose prerequisite (`FDA-CYP`'s potency
vocabulary, `db/039`–`db/043`) has landed. **No spec yet; it opens with its own brainstorm/design round**, and
that round has one decision it cannot dodge: SPL section `34073-7` qualifies interactions **by potency band**
(tizanidine: *strong* CYP1A2 inhibitors contraindicated, *moderate or weak* "avoid") while MED-RT's
`Cytochrome P450 1A2 Inhibitors [MoA]` is one undifferentiated class. Carry the band or drop it and accept
over-warning — it cannot be ignored. OnSIDES's **method** is the precedent; its **data** is not a DDI source
and must stop being listed as one.

**The alternative, equally ready:** `5c.5` pregnancy & lactation is **spiked, not designed** — LactMed alone
puts 1,679 moieties outside MED-RT's thin lactation floor, and it is gated on a **clinician review that has not
happened** (a 23-row worklist ships with the spike results). **Whichever is chosen, brainstorm before designing
and measure before both** — that is now twice in a row that measuring first changed the shape of the slice.

## Parallel project sequencing

DrugCentral is done and is a **candidate-tier floor pinned to the 2023 release** — it does not refresh, and
nothing in that tier may auto-alert. FDA toxicity remains cleared and unscheduled; class-grain content (#98)
still gates #112/#105.

## Open follow-ups

The full ledger lives once in [PROJECT-NOTES § "The standing open-issue ledger"](PROJECT-NOTES.md). New this
round: **[#148](https://github.com/cairn-ehr/drugref/issues/148)** — `exact_ddi_pair` adds a THIRD population
to the ungraded cross-source disagreement question (**635 of the 7,501 pairs are already reachable through
MED-RT's class expansion and nothing compares them**), which is #97/#106 one tier down; and
**[#149](https://github.com/cairn-ehr/drugref/issues/149)** — `fda_cyp_run.FDA_CYP_TABLES` is not registered in
`test_source_clear_contract.py`'s `EXPECTED_TABLES`, so a table dropped from that tuple is caught by nothing
(pre-existing); **[#151](https://github.com/cairn-ehr/drugref/issues/151)** — `questions.py` is over rule 4's
guideline (pre-existing); **[#152](https://github.com/cairn-ehr/drugref/issues/152)** — synthesise the fixture's
excluded-reference rows rather than committing their text, a licensing-posture call rather than a defect since
the rule-6 filter never reads it; and **[#153](https://github.com/cairn-ehr/drugref/issues/153)** — two
concurrent pytest sessions on one database wipe each other's schema, inventing evidence against whatever branch
is under review (pre-existing). Still standing: #146, #128/#129 and #132–#135 (FDA-CYP residue), #124, #121/#123, #104, #94. Before production:
re-run every parser on current releases, resolve #17, and the three rule-6 deeds (#6, #25, GSRS).

## Current DSN

- Test-only DSN: `host=localhost port=5532 dbname=drugref_test user=postgres`. Set `DRUGREF_TEST_DSN` for DB
  tests; never use it for reviewer accounts or GUI service data — pytest recreates it, and see #153 before
  running two sessions against it at once.
- **`drugref_dc049`** is this round's measured database (`TEMPLATE drugref_dc101` + migrate to `049` + the
  ingest, `ingest_run_id` 6); **`drugref_dc101`** is the untouched `db/048` baseline the hot-path comparison was
  made against. Keep both, or rebuild `dc101` from the documented chain — which runs neither FDA-CYP nor
  DrugCentral, both being standalone subcommands. **`dc049` predates `db/050`**; migrate it before re-measuring.
- The dump lives at `downloads/DRUGCENTRAL/drugcentral.dump.11012023.sql.gz` (SHA-256 `0559…3e04f`, recorded
  on `ingest_run.source_checksum`) with a validated TSV extract cached beside it.
- The verification database and its migration state live once in PROJECT-NOTES § "How to run / test"; do not
  copy that volatile map here.
