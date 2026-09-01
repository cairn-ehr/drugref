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

**Branch `claude/spl-reader-skip-census`, from `main` at `58d441c`**; **this round is open as
[PR #167](https://github.com/cairn-ehr/drugref/pull/167) and is not merged.** Migrations through **`db/052`** —
this round added **NO migration**. The suite total lives in PROJECT-NOTES § "How to run / test" and **nowhere else**
([#146](https://github.com/cairn-ehr/drugref/issues/146)); read it there at the START of the session.

**⇒ JUST FINISHED — THE READER-SKIP CENSUS, CLOSING [#162](https://github.com/cairn-ehr/drugref/issues/162).**
Full account: PROJECT-NOTES § "The reader-skip census round" ·
[measurement record](superpowers/specs/2026-08-31-drugref-spl-reader-skip-census.md). One 163.6 s pass over all
**54,813** documents, no database and no target set, settled every open reader question at once.

**⇒ THE STANDING RISK IS RETIRED.** *"The two new counters are unmeasured, and the next run may refuse where the
last succeeded"* — they are **ZERO**, and so is the third. **And it was already answerable from two published
numbers nobody had compared** (54,813 documents read; 54,813 outer members). ⇒ *Before measuring, check whether
the measurement has already been published in two halves.*

**⇒ #162'S OWN PROPOSED FIX WOULD HAVE ABORTED THE INGEST ON ITS OWN CORPUS.** It said *"fold 2 and 3 into
`total_dropped`"*; the release carries **`COLR` ten times**, so case 3 folded in aborts before the run row
exists. The guard is keyed on **the condition that harms** — an unknown classCode **carrying a UNII** — and the
release settles that too: all ten `COLR` ingredients are named WHITE/RED/BLUE/YELLOW and **carry no `<code>`
element**, so none could contribute a subject even if admitted as active. Cases 1 and 2 measured zero at
**outcome AND cause** and are drops.

**⇒ A COUNTER NOBODY REPORTS IS A SILENT SKIP WITH EXTRA STEPS.** `skipped_not_a_member_zip` was documented as
*"counted and reported"* for a whole slice and was reported **nowhere**; `skipped_unknown_class_code` would have
inherited that, making admitting `COLR` a way of HIDING it. `describe_reported_skips` now prints both **and
names the codes**, and rides on `SplSummary` — the review found the `say()` route is a no-op whenever
`progress` is None, which is every library caller and every test.

**⇒ VERIFIED WITH THE SHIPPED CODE, NOT ONLY THE PROBE** — `drugref_spl162`, **10 min 43 s** vs the published
~12.5 min, did not abort. `spl_ddi_pair` **29,952** (26,598 novel) · `spl_label_subject` **73,867** ·
`spl_wording_quote` **138,187** · `spl_entity_occurrence` **1,297,944**, reproducing 2026-08-27 exactly.
**⇒ IT PREDATES THE REVIEW'S FIXES** — the reader changed after it, so a re-run is the next round's first job.

**⇒ THE POPULATION TRAP, NOW A CONCRETE NUMBER.** `skipped_unknown_class_code` is **0** while the census counts
`COLR` **10**. Both right: the shipped counters are scoped to the documents the scan reads a subject from
(10,670 is the DE-DUPLICATED label count), the census is release-wide over 54,813. **Do not read one as a check
on the other.**

**⇒ THEN THE REVIEW FOUND SIX DEFECTS, ALL IN THE CODE THE CENSUS COULD NOT CHECK** — spec §6a records them.
The vocabulary went into **two homes and drifted inside one commit** (`COLR` into the shipped set, not the
census's copy, three lines under a comment warning of exactly that), so re-running the census would have called
`COLR` unruled — the instrument contradicting its own verdict. The census **disagreed with the reader on
`<versionNumber/>`** (junk to one, "absent" to the other) while the test pinning them compared only `version`,
`None` on both sides. Plus: `total_dropped` could exceed `documents_read` and keep a "dropped" row in `found`;
an unknown `encoding=` raises `LookupError`, not `ParseError`, aborting the scan; a corrupt member zip raised
`BadZipFile` naming nothing; and membership was decided by a `.zip` **suffix**, so `M.ZIP` lost a real label
through the one bucket that does not refuse. ⇒ *A census retires a risk about the CORPUS; only a test retires
one about the READER* — four are conditions this release lacks, and every fixture counter was seeded with **1**,
so two could be swapped and all 2402 tests passed.

**⇒ THE SUITE-COUNT DRIFT HAPPENED AN EIGHTH TIME, INSIDE THIS ROUND** — written off a `--collect-only` taken
before the last test existed. ⇒ *Even a number measured in the same session goes stale if measured before the
work stops.*

**⇒ `spl_release.py` WAS SPLIT OUT OF `spl_dailymed.py`** (rule 4: 491 lines, +100 needed). Verbatim move
first, whole suite green, *then* the counters — refactor risk and behaviour change never mixed in one step.
459 and 397 lines now, after the review round's fixes.

## ⇒ DO THIS NEXT

**Choose one; none is blocked.**

1. **[#160](https://github.com/cairn-ehr/drugref/issues/160) — the `spl_label_subject` `COPY`** runs >4 min at
   100% CPU for 73,867 rows against 1.0 s in a synthetic probe on the same schema. Two causes are ruled out in
   the issue; three are untried (COPY vs INSERT, ICU text collation on `set_id`/`version`, drop-and-rebuild
   indexes). Small, self-contained, and it is the whole of the ingest's cost — **`drugref_spl162` is a fresh
   verification database to measure against, built this round.**
2. **#163–#166 and #168–#171**, the two review rounds' deferrals, all smaller than #162 was. **#168 is the
   closest in kind to what this round just fixed**: three more homes of one vocabulary in `tools/`.
3. **[#159](https://github.com/cairn-ehr/drugref/issues/159) — `finished_at − started_at` is not a duration for
   ANY feed.** One line to change and a decision to make about a column already on disk for nine feeds.
4. **`5c.5` pregnancy & lactation is still spiked-not-designed** — LactMed puts 1,679 moieties outside MED-RT's
   thin lactation floor, gated on a **clinician review that has not happened** (23-row worklist in the spike).
5. **The class half of 5c.3**, which is where every unsolved problem lives (#155, #102, the word-order gap).
   **Deferring the class half RAISED the drug × drug yield by 193 pairs**, so a round re-adding classes must
   expect it to FALL and must not read that as a regression.

## Parallel project sequencing

DrugCentral is done and is a **candidate-tier floor pinned to the 2023 release** — it does not refresh, and
nothing in that tier may auto-alert. **SPL is a fourth candidate source and DELIBERATELY NOT an arm of
`exact_ddi_pair`**: it means *a label's interactions section names both drugs*, not *an authority asserts they
interact*. FDA toxicity remains cleared and unscheduled; class-grain content (#98) still gates #112/#105.

## Open follow-ups

The full ledger lives once in [PROJECT-NOTES § "The standing open-issue ledger"](PROJECT-NOTES.md).
**#162 is CLOSED by this round.** New from this round's own review: **#168** (three more homes of one
vocabulary + a second `iter_release_labels`, all pre-existing in `tools/`), **#169** (a `SkipReason` enum — when
the 12th counter arrives), **#170** (SPL version spelled three ways), **#171** (a census crash on the last part
discards every part already counted). Still new: **#159/#160** (performance) and **#163–#166**.
Still standing: **#155** (MED-RT's PK axis is not a drug-class vocabulary) and **#102 re-opened** (the band is
pair-scoped), both inherited by the deferred class half; **#67** (salt↔base equivalence) is wanted by **three**
sources and blocks a grain; **#158** (route 3's calibration set) is untouched. Also: #148, #149, #151, #152, #153, #146, #128/#129 and #132–#135 (FDA-CYP
residue), #124, #121/#123, #104, #94. Before production: re-run every parser on current releases, resolve #17,
and the three rule-6 deeds (#6, #25, GSRS).

## Current DSN

- Test-only DSN: `host=localhost port=5532 dbname=drugref_test user=postgres`. Set `DRUGREF_TEST_DSN` for DB
  tests; never use it for reviewer accounts or GUI service data — pytest recreates it, and see #153 before
  running two sessions against it at once.
- **`drugref_spl162`** is THIS round's verification database and the one to re-measure against: `TEMPLATE
  drugref_spl` → `migrate` → `ingest spl`, 10 min 43 s, reproducing every published figure. Full command:
  results record §1. **`drugref_spl051`** is the ingest round's and is still on disk. **Never patch a
  verification database — rebuild it under a new name.**
- **`drugref_spl`** is the pre-`db/051` database both design rounds used, and the template above.
  **`drugref_dc049`**/**`drugref_dc101`** are DrugCentral's; `dc049` predates `db/050`.
- Corpora on disk: `downloads/OPENFDA/` (14 partitions, `export_date` 2026-08-22) and `downloads/DAILYMED/`
  (6 Human Rx parts, `last-modified` 2026-08-21, 17.6 GB). **`downloads/` is gitignored, so every SHA-256 is
  recorded in the mining measurement record §2** — re-fetch and verify against that table, not against a
  manifest file that disappears with the bytes it describes. The combined `source_checksum` this ingest
  recorded over all twenty files is `5d6a894b30ce…`.
- The probe cache (`sections.jsonl`) is **scratch and gone**; `tools/spl_recovery_probe.py` rebuilds it in
  minutes. The verification-database map and migration state live once in PROJECT-NOTES § "How to run / test".
