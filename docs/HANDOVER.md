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

**Branch `claude/spl-copy-cost`, from `main` at `1272d02`**; **this round is open as
[PR #173](https://github.com/cairn-ehr/drugref/pull/173) and is not merged.** Migrations through **`db/052`** —
this round added **NO migration**. The suite total lives in PROJECT-NOTES § "How to run / test" and **nowhere
else** ([#146](https://github.com/cairn-ehr/drugref/issues/146)); read it there at the START of the session.

**⇒ JUST FINISHED — THE COPY-COST ROUND, CLOSING [#160](https://github.com/cairn-ehr/drugref/issues/160), AND
ITS OWN REVIEW ROUND** (below — the fix stood, three of its sentences did not). **The SPL ingest went from
12 min 51 s to 2 min 09 s**, every published count, both checksums and all five routes identical. Full account:
PROJECT-NOTES § "The COPY-cost round" · [record](superpowers/specs/2026-09-01-drugref-spl-copy-fk-plan.md).

**⇒ THE RE-VERIFICATION THE LAST ROUND OWED IS DONE AND CLEAN**, and that run carried #160's control, which
nobody had looked for: 73,867 subject rows took **630 s** while **1,436,131** occurrence + quote rows took
**35 s** — same transaction, writer and client, *19.4× more rows in 18× less time*. **All three of the issue's
candidate causes were wrong.**

**⇒ THE CAUSE CAME FROM A STACK SAMPLE, NOT THE HYPOTHESIS LIST.** **6,748 of 6,748 samples** inside
`RI_FKey_check_ins`. A foreign-key check is a QUERY, and on a freshly `COPY`d parent (`relpages = 0`) the primary
key and `spl_label_by_wording` **cost an identical 8.44** — the tie landed on the loose one, matching all 68,550
rows once per child row. Two `ANALYZE`s costing **112 ms** bought **365×**. ⇒ *Where a cost sits in one
statement, SAMPLE THE PROCESS before designing an experiment about it.*

**⇒ THE RULE, AND IT IS NOT "ANALYZE AT THE END": analyse a bulk-loaded table BEFORE loading anything that
references it.** Of all **138** foreign keys in the schema, **exactly one** parent offers a loose plan. Cause and
census are both pinned; **four mutants run, all four killed.** ⇒ *A refutation is a measurement plus an
explanation, and only the explanation is load-bearing once quoted forward* — the 175 ms that ruled this cause
out for a round was real; the reason beside it was invented.

**⇒ THE INGEST'S DURATION IS SPREAD ACROSS DOCSTRINGS, A TEST, ROADMAP AND PROJECT-NOTES**; all the ones `grep`
finds are corrected. ⇒ *The first pass claimed "five homes … all corrected" and had missed at least three.
**A count of a vocabulary's homes is itself a measurement.***

## ⇒ WHAT THE REVIEW CHANGED, AND WHY IT IS THE INTERESTING HALF

The fix was correct, complete and correctly ordered, and **no code defect was found.** Everything below is a
sentence that was wrong, or a guard that did not guard. Full account: PROJECT-NOTES § "The COPY-cost round".

**⇒ THE ROUND COMMITTED ITS OWN META-RULE'S ERROR WHILE WRITING IT DOWN.** The docstring said the RI plan is
*"CACHED for the rest of the session, so analysing afterwards cannot repair it"*. **Measured and false**: in one
session and one transaction, 3,000 child rows at first use took **4,874 ms**; after an `ANALYZE` the next two
batches took **15.7 ms** and **14.0 ms**. The rule survives — the reason is *ordering in time* — but the
mechanism was invented, one paragraph after the paragraph retracting an invented mechanism.
⇒ **A ROUND IS MOST LIKELY TO COMMIT THE FAILURE IT IS CURRENTLY NAMING.**

**⇒ THE GUARD ADMITTED THE STATE IT EXISTED TO FORBID.** `reltuples >= 0` is satisfied by **0.0** — what
`ANALYZE` of an *empty* table writes — and an empty parent pins the same catastrophic plan an unanalyzed one
does. Two mutants lived under it, including "consolidate both calls into `analyze_source_tables`". It is `> 0`
now. ⇒ **A GUARD WRITTEN AS "HAS STATISTICS" WHEN IT MEANS "HAS STATISTICS DESCRIBING ITS ROWS" IS OFF BY THE
BUG.**

**⇒ AND THE COVERAGE WAS HAND-LISTED, SO ITS "EVERY" WAS FALSE.** Three writers named, **four** edges exist, and
one watch was **inert** (two keyed on the same parent; `setdefault` kept the first). Edges now come from
`pg_constraint` at the `_copy` chokepoint. **Two more false sentences, both measured:** *"every other parent has
only its primary key"* — **26** carry a non-PK index; and *"the statistics … are rolled back"* — `pg_statistic`
does, `pg_class.relpages`/`reltuples` do **not**.

**Filed, not fixed: #174** — `ANALYZE` on a table the ingest role does not own is a **WARNING**: it skips,
returns success, and psycopg discards the warning, so the #160 fix silently reverts under an
admin-migrates/app-ingests role split. **#172** updated: 512/500, breach arrived as predicted.

## ⇒ DO THIS NEXT

**Choose one; none is blocked.**

1. **[#159](https://github.com/cairn-ehr/drugref/issues/159) — `finished_at − started_at` is not a duration for
   ANY feed.** The most valuable small one: this round cut real SPL runtime 6× and the column still reports
   49.9 s, so the number an operator would size a rebuild from is wrong in a new way.
2. **#174 with a notice handler in `db.connect`** — the fix and the mechanism that makes every future skipped
   `ANALYZE`, `NOTICE` and `WARNING` visible instead of discarded. Then **#163–#166**, **#168–#171** (#168 is
   three more homes of one vocabulary in `tools/`, the class this slice has now found five times).
3. **[#172](https://github.com/cairn-ehr/drugref/issues/172) — `spl_evidence.py` at 512/500**, breached by the
   review round. Seam: `Registry`/`load_registry` (a READ path in the SOLE WRITER); the census round's
   `spl_release.py` split is the precedent — verbatim move first, suite green, *then* behaviour.
4. **`5c.5` pregnancy & lactation is still spiked-not-designed** — LactMed puts 1,679 moieties outside MED-RT's
   thin lactation floor, gated on a **clinician review that has not happened** (23-row worklist in the spike).
5. **The class half of 5c.3**, where every unsolved problem lives (#155, #102, the word-order gap). **Deferring
   it RAISED the drug × drug yield by 193 pairs** — a round re-adding classes must expect a FALL, not a regression.

## Parallel project sequencing

DrugCentral is done: a **candidate-tier floor pinned to the 2023 release** — it does not refresh, and nothing
in that tier may auto-alert. **SPL is a fourth candidate source and DELIBERATELY NOT an arm of
`exact_ddi_pair`** — it means *a label's interactions section names both drugs*, not *an authority asserts they
interact*. FDA toxicity is cleared and unscheduled; class-grain content (#98) gates #112/#105.

## Open follow-ups

The full ledger lives once in [PROJECT-NOTES § "The standing open-issue ledger"](PROJECT-NOTES.md).
**#160 is CLOSED by this round; #162 was closed by the last.** New: **#172** (`spl_evidence.py`, now 512/500)
and **#174** (`ANALYZE` skipped with a discarded WARNING when the ingest role does not own the table). Still
open from the review rounds: **#163–#166**, **#168–#171**, **#159**. Still standing: **#155** (MED-RT's PK axis is not a
drug-class vocabulary) and **#102 re-opened** (the band is pair-scoped), both inherited by the deferred class
half; **#67** (salt↔base equivalence) is wanted by **three** sources and blocks a grain; **#158** (route 3's
calibration set) is untouched. Also: #148, #149, #151, #152, #153, #146, #128/#129, #132–#135 (FDA-CYP
residue), #124, #121/#123, #104, #94. Before production: re-run every parser on current releases, resolve #17,
and the three rule-6 deeds (#6, #25, GSRS). The verification-database map and migration state live once in
PROJECT-NOTES § "How to run / test".

## Current DSN

- Test-only DSN: `host=localhost port=5532 dbname=drugref_test user=postgres`. Set `DRUGREF_TEST_DSN` for DB
  tests; never for reviewer accounts or GUI data — pytest recreates it. **See #153 before running two sessions
  against it at once: concurrent runs drop the schema under each other and the failures look like real ones.**
- **`drugref_spl160fix` is THIS round's verification database**: `TEMPLATE drugref_spl` → `migrate` →
  `ingest spl`, **2 min 09 s**, reproducing every published figure (command: measurement record §1).
  **`drugref_spl160` is its BEFORE control** — same build minus the two `ANALYZE`s, 12 min 51 s; keep both,
  they are the only pair that shows the 630 s. **`drugref_spl162`**/**`drugref_spl051`** are still on disk.
  **Never patch a verification database — rebuild it under a new name.**
- **`drugref_spl`** is the pre-`db/051` database every SPL round templates from; **`drugref_dc049`**/
  **`drugref_dc101`** are DrugCentral's (`dc049` predates `db/050`).
- Corpora on disk: `downloads/OPENFDA/` (14 partitions, `export_date` 2026-08-22) and `downloads/DAILYMED/`
  (6 Human Rx parts, `last-modified` 2026-08-21, 17.6 GB). **`downloads/` is gitignored, so every SHA-256 is
  in the mining measurement record §2** — verify against that table, not a manifest that disappears with the
  bytes it describes. The combined `source_checksum` over all twenty files is `5d6a894b30ce…`, identical in all
  three runs above. The probe cache (`sections.jsonl`) is scratch; `tools/spl_recovery_probe.py` rebuilds it.
