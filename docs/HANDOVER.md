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

**Branch `claude/drugcentral-ddi-ingest`, from `main` at `ae1d1d3`** (PR #147, the re-measurement, merged
2026-08-23). Migrations through **`db/049`** — this round added it. The suite total lives in PROJECT-NOTES
§ "How to run / test" and **nowhere else** ([#146](https://github.com/cairn-ehr/drugref/issues/146)); read it
there at the START of the session, because that is what caught the last drift.

**⇒ JUST FINISHED — the DrugCentral `ddi` ingest, [#101](https://github.com/cairn-ehr/drugref/issues/101),
`db/049`.** Design:
[`drugcentral-ddi-ingest`](superpowers/specs/2026-08-23-drugref-drugcentral-ddi-ingest-design.md); measurement
on the real release:
[`…-ingest-measurement`](superpowers/specs/2026-08-23-drugref-drugcentral-ddi-ingest-measurement.md); full
account and **every figure**: PROJECT-NOTES § "The DrugCentral ddi ingest". Do not re-derive them from here.

**What shipped.** `source = 'DRUGCENTRAL'` admitted (`ingest_run_source` + `ingest_run_writer` + an explicit
`ids._SOURCE_CANONICAL` entry + `provenance.py`, all in one commit — the trio whose failure mode is a silent
no-op rebuild). `ddi_source_severity`, the upstream-band → drugref-grade mapping **as seeded data**.
`drugcentral_ddi_assertion`, every bundleable row as published. `drugcentral_ddi_pair`, orientation collapsed,
most-severe-wins. **`exact_ddi_pair` — the read path exact drug–drug pairs have never had**: MED-RT's 1,442
`moiety_contraindication` rows reached no consumer view at all before this. `gap_unresolved_ddi_endpoint` and
the **eighteenth** question kind. Plus `tools/drugcentral_{dump,resolve}.py` promoted into
`src/drugref/ingest/` with **no re-export shims**, so the instrument and the ingest are the same code, and a
standalone `drugref ingest drugcentral` subcommand (NOT a chain step — 1.4 GB, one pinned release).

**Measured end to end on `drugref_dc049`: 12 predictions, 12 MATCHED, 0 mismatched, no code defects.** 7,621
rows read, 50 excluded by rule 6, 7,571 bundleable → 7,501 pairs, 37 unresolvable over 10 endpoint names, 0
self-pairs, in 20.2 s of which the DB transaction is ~1.5 ms. `ddi_candidate_pair` (21,664) and
`substance_moiety` (19,438) did not move, and that view's plan is byte-identical before and after once
run-to-run noise is blanked.

**Two measurements taken for the design changed it.** (1) `description` carries **no clinical content** — all
7,571 match `NAME1/NAME2 [VA Drug Interaction]` — so the severity band is the whole of what this source adds.
(2) **The source asserts an UNORDERED pair**: 33 published in both orders, 4 disagreeing with themselves,
which rules out the directional `moiety_contraindication`. The 4 are named in PROJECT-NOTES, not here.

**Rule 6 discharged, and `NOTICE` says so.** Only `ddi_ref_id = 2` (VHA NDF-RT, US federal) is ingested;
Stockley's and Lexicomp are out, and DrugCentral's own CC BY-SA over the compilation is not treated as evidence
of a right to relicense either. The constant is not trusted alone: the orchestrator re-reads the dump's
`reference` row and **aborts** on a renumber. The committed fixture carries all three references with the
excluded descriptions redacted — `description` is the ONLY column redacted, and `NOTICE` now records a
determination for each of the four text columns kept (`drug_class1`, `drug_class2`, `ddi_risk`, `source_id`).

**⇒ FOUR PUBLISHED FIGURES WERE WRONG AND ARE CORRECTED IN PLACE. Three were this project's own state files.**

1. **The gap kind is the EIGHTEENTH.** The live CHECK already held seventeen (`db/039` added
   `fda_cyp_unadjudicated`) while PROJECT-NOTES § "Plan A" said SIXTEEN and both the spec and the plan said
   "kind 17". The migration was right — it copies the live CHECK verbatim — and § "Plan A" is the count's ONE
   home and now says EIGHTEEN.
2. **`MAOIs or RIMAs` DOES exist in the dump.** The re-measurement's own correction 4 said *"does not exist in
   the table"*; measured, it is on **10 `ddi.source_id` rows, every one `ddi_ref_id = 1`**. It appears as an
   ENDPOINT zero times, which is the claim that holds. Decision impact nil; the point is that **the correction
   round's own file was wrong in the way the round exists to prevent** — a sentence's scope is a figure too.
3. **`class_contraindication_source` did NOT need widening.** This file said it did, quoting PROJECT-NOTES
   § "The 5c.3 source evaluation", which said it first. DrugCentral writes no class rule; that CHECK stays
   `('MED-RT','ONCHIGH')` and a test pins that it is untouched. **The CHECK a new source really needs is
   `ingest_run_writer`, which no document had named at all.**
4. **The suite count** was stale again — 71 tests over this round without it moving. Written in its ONE home
   and deliberately not restated here.

**⇒ THEN THE FINAL WHOLE-BRANCH REVIEW: 0 critical, 3 important, 7 minor — ALL APPLIED.** Transposing the
writer's two endpoint names (or its two routes) survived all 1959 tests, because no test wrote a MIXED row and
37 real rows are; `NOTICE` said ONE field was committed unredacted on excluded rows when four were, and
`ddi_risk` had a determination nowhere; `BUNDLEABLE_REF_IDS` had grown two more homes; the summary reconciled
only against itself. **A fourth and fifth instance of the same two lessons: a comment claiming a guard is not
evidence of the guard, and a figure nobody re-derives decays silently — including a correction.**

## ⇒ DO THIS NEXT

**ROADMAP's next content slice is `5c.3` — SPL/DailyMed mining**, whose prerequisite (`FDA-CYP`'s potency
vocabulary, `db/039`–`db/043`) has landed. **No spec yet; it opens with its own brainstorm/design round**, and
that round has one decision it cannot dodge: SPL section `34073-7` qualifies interactions **by potency band**
(tizanidine: *strong* CYP1A2 inhibitors contraindicated, *moderate or weak* "avoid") while MED-RT's
`Cytochrome P450 1A2 Inhibitors [MoA]` is one undifferentiated class. Carry the band or drop it and accept
over-warning — it cannot be ignored. OnSIDES's **method** is the precedent; its **data** is not a DDI source
and must stop being listed as one.

**The alternative, equally ready:** `5c.5` pregnancy & lactation is **spiked, not designed** — LactMed alone
puts 1,679 moieties outside MED-RT's thin lactation floor. It is gated on a **clinician review that has not
happened** (a 23-row worklist ships with the spike results), and its three sources have unlike grains that must
not be collapsed into one recommendation.

**Whichever is chosen, brainstorm before designing, and measure before both.** That is now twice in a row that
measuring first changed the shape of the slice.

## Parallel project sequencing

DrugCentral is done and is a **candidate-tier floor pinned to the 2023 release** — it does not refresh, and
nothing in that tier may auto-alert. FDA toxicity (DICTrank/DIRIL/DILIrank, a non-firing evidence projection)
remains cleared and unscheduled. Class-grain content (#98) still gates #112/#105.

## Open follow-ups

The full ledger lives once in [PROJECT-NOTES § "The standing open-issue ledger"](PROJECT-NOTES.md). New this
round: **[#148](https://github.com/cairn-ehr/drugref/issues/148)** — `exact_ddi_pair` adds a THIRD population
to the ungraded cross-source disagreement question (**635 of the 7,501 pairs are already reachable through
MED-RT's class expansion and nothing compares them**), which is #97/#106 one tier down; and
**[#149](https://github.com/cairn-ehr/drugref/issues/149)** — `fda_cyp_run.FDA_CYP_TABLES` is not registered in
`test_source_clear_contract.py`'s `EXPECTED_TABLES`, so a table dropped from that tuple is caught by nothing
(pre-existing); and **[#151](https://github.com/cairn-ehr/drugref/issues/151)** — `questions.py` is over rule
4's guideline and **71% of it is the one `_GAP_SOURCES` literal** (pre-existing; split out of #89 as #130 was
for `cli.py`). `gap_unresolved_ddi_endpoint`'s `endpoint_name <> ''` exclusion is **now tested**. Still
standing: #146, #128/#129 and #132–#135 (FDA-CYP residue), #124, #121/#123, #104, #94. Before production:
re-run every parser on current releases, resolve #17, and the three rule-6 deeds (#6, #25, GSRS).

## Current DSN

- Test-only DSN: `host=localhost port=5532 dbname=drugref_test user=postgres`. Set `DRUGREF_TEST_DSN` for DB
  tests; never use this database for persistent reviewer accounts or GUI service data because pytest recreates it.
- **`drugref_dc049`** is this round's measured database — `TEMPLATE drugref_dc101` plus `drugref migrate` to
  `049` plus the DrugCentral ingest (`ingest_run_id` 6). **`drugref_dc101`** is the untouched `db/048` baseline
  the hot-path comparison was made against; keep both, or rebuild `dc101` from the documented chain — note that
  chain runs neither FDA-CYP nor DrugCentral, both being standalone subcommands.
- The dump lives at `downloads/DRUGCENTRAL/drugcentral.dump.11012023.sql.gz` (SHA-256 `0559…3e04f`, recorded
  on `ingest_run.source_checksum`) with a validated TSV extract cached beside it.
- The verification database and its exact migration state live once in PROJECT-NOTES § "How to run / test";
  do not copy that volatile map here.
