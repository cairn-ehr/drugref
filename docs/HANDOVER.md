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

**⇒ THE STANDING RISK IS RETIRED.** *"The two new counters are unmeasured on a real release, and the next real
run may refuse where the last succeeded"* — they are **ZERO**, and so is the third. **And it was already
answerable from two published numbers nobody had compared**: the results record says 54,813 documents read, and
the six parts hold exactly 54,813 outer members; a skipped member yields no document, so the two being equal
already implied all three counters were zero. ⇒ *Before measuring, check whether the measurement has already
been published in two halves.*

**⇒ #162'S OWN PROPOSED FIX WOULD HAVE ABORTED THE INGEST ON ITS OWN CORPUS.** It said *"fold 2 and 3 into
`total_dropped`"*. The release carries **`COLR` ten times**, so case 3 folded in makes `total_dropped` = 10 and
the guard aborts before the run row exists. The shipped guard is therefore keyed on **the condition that
harms** — an unknown classCode **carrying a UNII** — and the release settles that too: all ten `COLR`
ingredients are named WHITE/RED/BLUE/YELLOW and **carry no `<code>` element at all**, so none could contribute
a subject even if admitted as active. `COLR` is now ruled on, not merely tolerated. Cases 1 and 2 measured zero
at **outcome AND cause** and are now drops.

**⇒ A COUNTER NOBODY REPORTS IS A SILENT SKIP WITH EXTRA STEPS.** `skipped_not_a_member_zip` was documented as
*"counted and reported"* for a whole slice and was reported **nowhere** — no `say()`, no summary field.
`skipped_unknown_class_code` would have inherited exactly that, which would have made admitting `COLR` a way of
HIDING it. `spl_release.describe_reported_skips` now prints both, and is empty when there is nothing to say.

**⇒ VERIFIED WITH THE SHIPPED CODE, NOT ONLY THE PROBE** — `drugref_spl162`, **10 min 43 s** against the
published ~12.5 min. It did not abort, so every new drop counter is zero as measured by the code that ships.
`spl_ddi_pair` **29,952** (26,598 novel) · `spl_label_subject` **73,867** · `spl_wording_quote` **138,187** ·
`spl_entity_occurrence` **1,297,944** — all reproducing 2026-08-27 exactly.

**⇒ THE POPULATION TRAP, NOW A CONCRETE NUMBER.** `skipped_unknown_class_code` is **0** while the census counts
`COLR` **10**. Both right: the shipped counters are scoped to the 10,670 documents the scan reads a subject
from, the census is release-wide over 54,813. **Do not read one as a check on the other.**

**⇒ AND THE SUITE-COUNT DRIFT HAPPENED AN EIGHTH TIME, INSIDE THIS ROUND.** The count was written off a
`--collect-only` taken before the round's last test existed. Caught by re-reading it off the run that verified
green. ⇒ *Even a number measured in the same session goes stale if it is measured before the work stops.*

**⇒ `spl_release.py` WAS SPLIT OUT OF `spl_dailymed.py`** (rule 4: 491 lines, +100 needed). Verbatim move
first, whole suite green, *then* the counters — refactor risk and behaviour change never mixed in one step.
411 and 275 lines now.

## ⇒ DO THIS NEXT

**Choose one; none is blocked.**

1. **[#160](https://github.com/cairn-ehr/drugref/issues/160) — the `spl_label_subject` `COPY`** runs >4 min at
   100% CPU for 73,867 rows against 1.0 s in a synthetic probe on the same schema. Two causes are ruled out in
   the issue; three are untried (COPY vs INSERT, ICU text collation on `set_id`/`version`, drop-and-rebuild
   indexes). Small, self-contained, and it is the whole of the ingest's cost — **`drugref_spl162` is a fresh
   verification database to measure against, built this round.**
2. **[#163](https://github.com/cairn-ehr/drugref/issues/163)–[#166](https://github.com/cairn-ehr/drugref/issues/166)**,
   the review's other deferrals, all smaller than #162 was: the openFDA absent-versus-blank conflation,
   `db/051`'s unreachable NULL guard, frozen dataclasses over live dicts, and no size cap on nested zips.
3. **[#159](https://github.com/cairn-ehr/drugref/issues/159) — `finished_at − started_at` is not a duration for
   ANY feed.** One line to change and a decision to make about a column already on disk for nine feeds.
4. **`5c.5` pregnancy & lactation is still spiked-not-designed** — LactMed puts 1,679 moieties outside MED-RT's
   thin lactation floor, gated on a **clinician review that has not happened** (a 23-row worklist ships with
   the spike results).
5. **The class half of 5c.3**, which is where every unsolved problem lives (#155, #102, the word-order gap).
   **Deferring the class half RAISED the drug × drug yield by 193 pairs**, so a round re-adding classes must
   expect it to FALL and must not read that as a regression.

## Parallel project sequencing

DrugCentral is done and is a **candidate-tier floor pinned to the 2023 release** — it does not refresh, and
nothing in that tier may auto-alert. **SPL is now a fourth candidate source and is DELIBERATELY NOT an arm of
`exact_ddi_pair`**: it means *a label's interactions section names both drugs*, not *an authority asserts they
interact*, and merging them would make the stronger claim unfalsifiable. FDA toxicity remains cleared and
unscheduled; class-grain content (#98) still gates #112/#105.

## Open follow-ups

The full ledger lives once in [PROJECT-NOTES § "The standing open-issue ledger"](PROJECT-NOTES.md).
**#162 is CLOSED by this round.** Still new: **#159 and #160** (performance, from the ingest round) and
**#163–#166** (from the review round — the openFDA absent-versus-blank conflation, db/051's unreachable NULL
guard, frozen dataclasses over live dicts, and no size cap on nested zips).
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
- **`drugref_spl162`** is THIS round's verification database and the one to re-measure against: `TEMPLATE
  drugref_spl` → `migrate` → `ingest spl`, 10 min 43 s, reproducing every published figure. Full command:
  results record §1. **`drugref_spl051`** is the ingest round's and is still on disk. **Never patch a
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
