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

**Branch `claude/spl-ddi-ingest`, from `main` at `dc6a504`** (PR #157 merged 2026-08-25); **this round is open
as [PR #161](https://github.com/cairn-ehr/drugref/pull/161) and is not merged.** Migrations through **`db/051`**
— **this round added it.** The suite total lives in PROJECT-NOTES § "How to run / test" and **nowhere else**
([#146](https://github.com/cairn-ehr/drugref/issues/146)); read it there at the START of the session — it was
**stale by 32** when this one began, the seventh occurrence, and that comment now records why.

**⇒ JUST FINISHED — slice 5c.3 IS BUILT AND MEASURED ON THE REAL RELEASES.**
[Design spec](superpowers/specs/2026-08-24-drugref-slice-5c3-spl-ddi-ingest-design.md) ·
[what it produced](superpowers/specs/2026-08-27-drugref-slice-5c3-spl-ddi-ingest-results.md). Full account and
**every figure**: PROJECT-NOTES § "Slice 5c.3 — the SPL ddi ingest". Do not re-derive them from here.

`drugref ingest spl --openfda <dir> --dailymed <parts...>` reads 19.3 GB in ~12.5 min and publishes **29,952
distinct candidate pairs, 26,598 (88.8%) novel** — clearing the design's `>= 29,258` / `>= 25,960` floor. The
census reproduces exactly (68,550 labels of 262,032 records, 27,406 wordings) and `substance_moiety` 19,438 /
`ddi_candidate_pair` 21,877 / `exact_ddi_pair` 8,943 are unmoved.

**⇒ THE DESIGN'S `unresolved` BUCKET SAID 14,680. IT IS 92.** It had filed **14,455 labels the probe never
read** into a bucket whose definition is *"present, read, and still unkeyable"*. Scanned for real, **30,386 of
41,056 targets are simply absent from the current DailyMed release**. So `gap_unresolved_spl_subject` is
**99.7% a RELEASE gap and 0.3% a registry gap** — the opposite of what that table would have anyone plan for,
and it points a future recovery route at a fuller corpus rather than at registry coverage.
⇒ *A population you did not read is not evidence about the population you did.*

**⇒ DEFERRING THE CLASS HALF MOVED THE DRUG × DRUG YIELD.** The openFDA arm gives **20,747** where the design
published **20,554**. Measured, not asserted (`tools/spl_class_vocabulary_delta.py`): adding the 8,534 class
entries back reproduces **20,554 over 26,721 wordings exactly**, because longest-match-wins had class names
consuming **11,169** moiety spans. ⇒ **A round that re-adds classes must expect the drug × drug yield to FALL
and must not read that as a regression.**

**⇒ THE END-TO-END FIXTURE COULD NOT SEE A WRONG QUOTE BUDGET, AND THAT WAS MEASURED.** Setting
`QUOTE_SHARE` from 0.25 to 0.95 left all 28 tests passing — db/050's vacuous-guard finding recurring inside the
round that quotes db/050 about it. The corpus now carries a wording where the budget BINDS; the same mutation
now fails **16**. Same story for raw-versus-normalised text: the synthetic wordings had no whitespace runs, so
`raw == normalised`; wrapped and double-spaced, that mutation fails four.

**⇒ THE INGEST DID NOT FINISH ON ITS FIRST RUN AND THE FIRST DIAGNOSIS WAS WRONG.** 25 min at 100% CPU in the
self-pair read-back. The obvious cause — FK checks against an unanalyzed parent — was **measured and refuted**
(175 ms for 20,000 rows). The real one was the same missing statistics one table on: the read-backs join tables
the transaction just `COPY`'d. `analyze_source_tables` fixes it and a test pins it by its **cause**
(`pg_class.reltuples >= 0`), because a performance property cannot be asserted as a stopwatch on a fixture.

## ⇒ DO THIS NEXT

**Choose one; none is blocked.**

1. **Review this branch.** Every previous 5c.3 round's review found a real defect in its own published
   arithmetic, and this one publishes more figures than any of them.
2. **[#160](https://github.com/cairn-ehr/drugref/issues/160) — the `spl_label_subject` `COPY`** runs >4 min at
   100% CPU for 73,867 rows against 1.0 s in a synthetic probe on the same schema. Two causes are ruled out in
   the issue; three are untried (COPY vs INSERT, ICU text collation on `set_id`/`version`, drop-and-rebuild
   indexes). Small, self-contained, and it is the whole of the ingest's cost.
3. **[#159](https://github.com/cairn-ehr/drugref/issues/159) — `finished_at − started_at` is not a duration for
   ANY feed.** One line to change and a decision to make about a column already on disk for nine feeds.
4. **`5c.5` pregnancy & lactation is still spiked-not-designed** — LactMed puts 1,679 moieties outside MED-RT's
   thin lactation floor, gated on a **clinician review that has not happened** (a 23-row worklist ships with
   the spike results).
5. **The class half of 5c.3**, which is where every unsolved problem lives (#155, #102, the word-order gap) —
   and see the yield warning above before measuring anything.

## Parallel project sequencing

DrugCentral is done and is a **candidate-tier floor pinned to the 2023 release** — it does not refresh, and
nothing in that tier may auto-alert. **SPL is now a fourth candidate source and is DELIBERATELY NOT an arm of
`exact_ddi_pair`**: it means *a label's interactions section names both drugs*, not *an authority asserts they
interact*, and merging them would make the stronger claim unfalsifiable. FDA toxicity remains cleared and
unscheduled; class-grain content (#98) still gates #112/#105.

## Open follow-ups

The full ledger lives once in [PROJECT-NOTES § "The standing open-issue ledger"](PROJECT-NOTES.md).
**New this round: #159 and #160**, both performance, both filed rather than fixed and both described above.
Still standing: **#155** (MED-RT's PK axis is not a drug-class vocabulary) and **#102 re-opened in new terms**
(the band is pair-scoped), both of which the deferred class half inherits; **#67** (salt↔base equivalence) is
wanted by **three** sources and is the one blocking a grain, not a nicety; **#158** (route 3's calibration set)
is untouched by this round. Also: #148, #149, #151, #152, #153, #146, #128/#129 and #132–#135 (FDA-CYP
residue), #124, #121/#123, #104, #94. Before production: re-run every parser on current releases, resolve #17,
and the three rule-6 deeds (#6, #25, GSRS).

## Current DSN

- Test-only DSN: `host=localhost port=5532 dbname=drugref_test user=postgres`. Set `DRUGREF_TEST_DSN` for DB
  tests; never use it for reviewer accounts or GUI service data — pytest recreates it, and see #153 before
  running two sessions against it at once.
- **`drugref_spl051`** is THIS round's verification database and the one to re-measure against: `TEMPLATE
  drugref_spl` → `migrate` (applies `db/051`) → `ingest spl`. Full command: results record §1. **Never patch a
  verification database — rebuild it under a new name.**
- **`drugref_spl`** remains the pre-`db/051` measurement database both design rounds used, and is the template
  above. **`drugref_dc049`** and **`drugref_dc101`** are the DrugCentral round's; `dc049` predates `db/050`.
- Corpora on disk: `downloads/OPENFDA/` (14 partitions, `export_date` 2026-08-22) and `downloads/DAILYMED/`
  (6 Human Rx parts, `last-modified` 2026-08-21, 17.6 GB). **`downloads/` is gitignored, so every SHA-256 is
  recorded in the mining measurement record §2** — re-fetch and verify against that table, not against a
  manifest file that disappears with the bytes it describes. The combined `source_checksum` this ingest
  recorded over all twenty files is `5d6a894b30ce…`.
- The probe cache (`sections.jsonl` and friends) is **scratch and is gone**; nothing in the shipped ingest needs
  it — `tools/spl_recovery_probe.py` rebuilds it in minutes if a measurement wants it back.
- The verification-database map and migration state live once in PROJECT-NOTES § "How to run / test"; do not
  copy that volatile map here.
