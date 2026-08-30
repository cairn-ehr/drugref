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
as [PR #161](https://github.com/cairn-ehr/drugref/pull/161) and is not merged.** Migrations through **`db/052`**
— the ingest round added `db/051`, its review round added `db/052` (comments only). The suite total lives in
PROJECT-NOTES § "How to run / test" and **nowhere else**
([#146](https://github.com/cairn-ehr/drugref/issues/146)); read it there at the START of the session.

**⇒ JUST FINISHED — PR #161 WAS REVIEWED AND THE FINDINGS ARE FIXED.** Full account: PROJECT-NOTES §
"Slice 5c.3's review round". The slice is built and measured
([design](superpowers/specs/2026-08-24-drugref-slice-5c3-spl-ddi-ingest-design.md) ·
[results](superpowers/specs/2026-08-27-drugref-slice-5c3-spl-ddi-ingest-results.md) · every figure in
PROJECT-NOTES § "Slice 5c.3 — the SPL ddi ingest"): 19.3 GB in ~12.5 min, **29,952 pairs, 26,598 (88.8%)
novel**, clearing the `>= 29,258` / `>= 25,960` floor.

**⇒ THE ROUND'S OWN HEADLINE CAME TRUE FIVE MORE TIMES.** #161 led with *"the fixture could not see a wrong
quote budget"*. The review found that class of vacuity in five further places — **including the guard enforcing
the licensing determination that headline is about**:

- **The quote budget had THREE homes, and the test named for pinning it was the third.** It ran
  `SELECT ceil(0.25 * %s)` with the literal typed in the test, so mutating `db/051`'s trigger to `ceil(0.35 *
  ...)` left all 29 tests in that file green. It now reads `pg_proc.prosrc`; the `share=` override that was the
  third home is gone. ⇒ *A test that restates the number it is checking cannot detect the disagreement it is
  named for.*
- **`spl_checks.reconcile` could be DELETED without failing a test** — three mutations, all green, on the only
  check comparing Python's belief against what the database holds.
- **The 12.5-minute scan ran inside an open snapshot**, pinning `xmin` database-wide: `load_registry` opens a
  transaction and nothing closed it until `open_run`. One `conn.rollback()`, pinned by a test asserting the
  CAUSE, because a fixture that scans in milliseconds cannot see a cost that is duration.
- **`scan_release`/`iter_release_labels` had no direct test**, and two skips sat *inside the generator*, before
  `documents_read` — so `check_scan_dropped_nothing` could not refuse them. *"All counters measured zero"* was
  a measurement over the documents that reached the counters. **The two new counters are still unmeasured on a
  real release, and the next real run may refuse where the last succeeded.**
- **The novel-pair floor was never watched refusing anything**, while `cli_spl` asserts it on every real run.

**⇒ AND REVIEWING THE FIXES CAUGHT THE SAME SHAPE TWICE MORE — the third consecutive round in this slice.**
The new `Registry` type broke two committed tools that no test exercises (one of them the measurement
`spl_match`'s docstring cites as its evidence), and the brand-new entity guard shipped an assertion that
passed with the guard deleted — over a regex that would have matched inside a legal XML comment and aborted
the whole ingest. Both fixed; `Registry` is now a dataclass so it cannot be destructured at all.
⇒ *Review the fix the way the thing being fixed was reviewed.*

**⇒ `db/051` SHIPPED THE DESIGN'S NUMBERS INTO THE DATABASE CATALOG.** Its `COMMENT ON` for `unresolved` said
**14,680** where the answer is **92** — the very figure this slice's headline corrects — and a column comment
named `spl_run.SUBJECT_ROUTES`, **which does not exist**. `db/052` fixes both. A catalog comment is not a
schema edit, so it is a new file and `db/051` stays immutable.

**⇒ STILL TRUE.** The `unresolved` bucket is 92, not 14,680 (the design filed 14,455 labels its probe never
read into a bucket meaning *"read"*); the register is **99.7% a RELEASE gap**. And **deferring the class half
RAISED the yield by 193 pairs** — a round re-adding classes must expect it to FALL, not read that as a
regression.

## ⇒ DO THIS NEXT

**Choose one; none is blocked.**

1. **Merge PR #161.** Reviewed, fixed, suite green. Every previous 5c.3 round's review found a real defect in
   its own published arithmetic and this one was no exception — but the arithmetic held; what did not was the
   guarding of it.
2. **[#162](https://github.com/cairn-ehr/drugref/issues/162) — three DailyMed reader skips are still
   uncounted**, each becoming `absent_from_dailymed`. Needs a run against the real 17.6 GB release to know
   whether folding them into `total_dropped` would start refusing legitimate releases: a measurement, not an
   edit. #163–#166 are the review's other deferrals and are all smaller.
3. **[#160](https://github.com/cairn-ehr/drugref/issues/160) — the `spl_label_subject` `COPY`** runs >4 min at
   100% CPU for 73,867 rows against 1.0 s in a synthetic probe on the same schema. Two causes are ruled out in
   the issue; three are untried (COPY vs INSERT, ICU text collation on `set_id`/`version`, drop-and-rebuild
   indexes). Small, self-contained, and it is the whole of the ingest's cost.
4. **[#159](https://github.com/cairn-ehr/drugref/issues/159) — `finished_at − started_at` is not a duration for
   ANY feed.** One line to change and a decision to make about a column already on disk for nine feeds.
5. **`5c.5` pregnancy & lactation is still spiked-not-designed** — LactMed puts 1,679 moieties outside MED-RT's
   thin lactation floor, gated on a **clinician review that has not happened** (a 23-row worklist ships with
   the spike results).
6. **The class half of 5c.3**, which is where every unsolved problem lives (#155, #102, the word-order gap) —
   and see the yield warning above before measuring anything.

## Parallel project sequencing

DrugCentral is done and is a **candidate-tier floor pinned to the 2023 release** — it does not refresh, and
nothing in that tier may auto-alert. **SPL is now a fourth candidate source and is DELIBERATELY NOT an arm of
`exact_ddi_pair`**: it means *a label's interactions section names both drugs*, not *an authority asserts they
interact*, and merging them would make the stronger claim unfalsifiable. FDA toxicity remains cleared and
unscheduled; class-grain content (#98) still gates #112/#105.

## Open follow-ups

The full ledger lives once in [PROJECT-NOTES § "The standing open-issue ledger"](PROJECT-NOTES.md).
**New: #159 and #160** (performance, from the ingest round) and **#162–#166** (from its review round —
uncounted reader skips, the openFDA absent-versus-blank conflation, db/051's unreachable NULL guard, frozen
dataclasses over live dicts, and no size cap on nested zips).
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
