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

**Branch `claude/ingest-run-duration`, STACKED on `claude/spl-copy-cost`** because
[PR #173](https://github.com/cairn-ehr/drugref/pull/173) (the COPY-cost round) **is still open and unmerged**,
and this round's verification needs its `conn.rollback()`. **⇒ MERGE #173 FIRST**; this round's PR diff
collapses to its own commits once it does. Migrations through **`db/053`** — this round added it. The suite
total lives in PROJECT-NOTES § "How to run / test" and **nowhere else**
([#146](https://github.com/cairn-ehr/drugref/issues/146)); read it there at the START of the session.

**⇒ JUST FINISHED — THE INGEST-DURATION ROUND, CLOSING
[#159](https://github.com/cairn-ehr/drugref/issues/159).** Both `ingest_run` stamps are clock readings now, and
`open_run` backdates `started_at` to the orchestrator's first line. Full account: PROJECT-NOTES § "The
ingest-duration round" · [record](superpowers/specs/2026-09-02-drugref-ingest-run-duration.md).

**⇒ THE COLUMN MEASURED THE TIME AN ORCHESTRATOR SPENT *NOT* TOUCHING THE DATABASE.** `now()` is
`transaction_timestamp()` and `open_run` COMMITS, so the two stamps sat in two transactions and the subtraction
gave the gap between their starts. Eight of nine feeds read **1.3–24 ms**; the ninth, `mesh_rel_run` at
**48.32 s**, was reporting how long it takes to parse 750 MB of MeSH before its first write.

**⇒ THE ISSUE'S HEADLINE FIGURE HAD ALREADY EVAPORATED, AND THREE THINGS READ PAST IT.** #159 cited **49.85 s**
for `spl_run`. Five days later the COPY-cost round put a `conn.rollback()` in front of the DailyMed scan, moving
`open_run` past it — **0.0026 s** on both databases that round built. The issue, the suite and that round's own
review all missed it. ⇒ *A NUMBER IN A FILED ISSUE IS A MEASUREMENT WITH NO OWNER: the round that moves it is
not the round that reads it. Re-measure the premise before designing against it.*

**⇒ VERIFIED AS A RATIO, NINE TIMES, ON A DATABASE BUILT FROM NOTHING** (`drugref_dur159` — a template would
have carried nine rows written under the old meaning). `/usr/bin/time -p` against the recorded duration:
`ingest chain` (five feeds) **137.46 / 137.82 s = 99.7%**, `drugcentral` 19.64/20.00, `onchigh` 3.87/4.26,
`fda-cyp` 4.11/4.44, `spl` **135.86 / 140.06 = 97.0%**. `spl_run` went **0.0026 s → 135.86 s**. The residual is
the 0.29–0.34 s interpreter start (measured), plus SPL's ~3.8 s final COMMIT, which the stamp cannot cover
without breaking `finish_run`'s no-commit contract and which the column comment therefore names.

**⇒ `mesh_rel_run` IS THE CROSS-CHECK:** its old 48.32 s is now a *subset* of its new **56.81 s** (parse 48 s +
writes 9 s), and nothing about that orchestrator changed. The SPL run reproduced every published figure —
68,550 labels, 27,406 wordings, 29,952 pairs, all five routes, 138,187 quoted windows.

**⇒ THE CHECK CAUGHT THE ROUND'S OWN BLIND SPOT, IN THE SUITE.** `db/053`'s
`finished_at >= started_at` failed five tests instantly on rows that **finished 3.8 ms before they started**:
two helpers stamped `finished_at = now()` against a `clock_timestamp()` default. ⇒ **MIXING `now()` AND
`clock_timestamp()` IN ONE TRANSACTION PRODUCES A NEGATIVE DURATION**, and both would have gone on doing it
silently.

**⇒ OLD ROWS ARE REFUSED AT THE OPERATOR SURFACE, NOT ONLY IN A COMMENT.** `drugref status` prints
`pre-db/053` rather than a runtime for a run that predates the migration on that database (watershed read from
the ledger). Verified on both paths without patching anything: `drugref_spl160fix` unmigrated, and
`drugref_dur159mixed` — a fresh clone then `migrate`, the production upgrade path, where **`db/053` applied
over nine pre-existing rows and the CHECK validated all nine**.

**⇒ AND THE DERIVED CONTRACT COULD NOT KILL ITS OWN MUTANT.** The grep contract (every module calling
`open_run` also calls `start_clock` — **eleven, derived from the tree**) passed unchanged against
`start_clock()` moved down to the line above `open_run`, which measures nothing. Only the test that injects a
delay into work done *before* `open_run` kills it: 59.5 ms against the 250 ms required. ⇒ *A DERIVED CHECK
OUTLIVES A HAND-LISTED ONE ONLY FOR WHAT IT DERIVES.*

## ⇒ DO THIS NEXT

**Choose one; none is blocked.**

1. **#174 with a notice handler in `db.connect`** — the most valuable one left, and now the only *correctness*
   item on the list: `ANALYZE` on a table the ingest role does not own is a **WARNING**, so it skips, returns
   success, and psycopg discards it — the #160 fix silently reverts under an admin-migrates/app-ingests split
   while the ingest still reports success. The handler makes every future skipped `ANALYZE`, `NOTICE` and
   `WARNING` visible instead of discarded. Then **#163–#166**, **#168–#171** (#168 is three more homes of one
   vocabulary in `tools/`, the class this slice has now found five times).
2. **[#172](https://github.com/cairn-ehr/drugref/issues/172) — `spl_evidence.py` at 512/500.** Seam:
   `Registry`/`load_registry` (a READ path in the SOLE WRITER); the census round's `spl_release.py` split is the
   precedent — verbatim move first, suite green, *then* behaviour. **Note `cli.py` is now 489/500** after this
   round's four lines: the next block added there needs a `cli_*.py` split, not another paragraph.
3. **`5c.5` pregnancy & lactation is still spiked-not-designed** — LactMed puts 1,679 moieties outside MED-RT's
   thin lactation floor, gated on a **clinician review that has not happened** (23-row worklist in the spike).
4. **The class half of 5c.3**, where every unsolved problem lives (#155, #102, the word-order gap). **Deferring
   it RAISED the drug × drug yield by 193 pairs** — a round re-adding classes must expect a FALL, not a
   regression.

## Parallel project sequencing

DrugCentral is done: a **candidate-tier floor pinned to the 2023 release** — it does not refresh, and nothing
in that tier may auto-alert. **SPL is a fourth candidate source and DELIBERATELY NOT an arm of
`exact_ddi_pair`** — it means *a label's interactions section names both drugs*, not *an authority asserts they
interact*. FDA toxicity is cleared and unscheduled; class-grain content (#98) gates #112/#105.

## Open follow-ups

The full ledger lives once in [PROJECT-NOTES § "The standing open-issue ledger"](PROJECT-NOTES.md).
**#159 is CLOSED by this round; #160 was closed by the last.** Still open from the review rounds:
**#174** (`ANALYZE` skipped with a discarded WARNING), **#172** (`spl_evidence.py` at 512), **#163–#166**,
**#168–#171**. Still standing: **#155** (MED-RT's PK axis is not a drug-class vocabulary) and **#102 re-opened**
(the band is pair-scoped), both inherited by the deferred class half; **#67** (salt↔base equivalence) is wanted
by **three** sources and blocks a grain; **#158** (route 3's calibration set) is untouched. Also: #148, #149,
#151, #152, #153, #146, #128/#129, #132–#135 (FDA-CYP residue), #124, #121/#123, #104, #94. Before production:
re-run every parser on current releases, resolve #17, and the three rule-6 deeds (#6, #25, GSRS). The
verification-database map and migration state live once in PROJECT-NOTES § "How to run / test".

## Current DSN

- Test-only DSN: `host=localhost port=5532 dbname=drugref_test user=postgres`. Set `DRUGREF_TEST_DSN` for DB
  tests; never for reviewer accounts or GUI data — pytest recreates it. **See #153 before running two sessions
  against it at once: concurrent runs drop the schema under each other and the failures look like real ones.**
- **`drugref_dur159` is THIS round's verification database** — built from **nothing** (`createdb` → `migrate` →
  `ingest chain` + `onchigh` + `fda-cyp` + `drugcentral` + `spl`), because a template carries nine `ingest_run`
  rows written under the old meaning. **`drugref_dur159mixed`** is its counterpart: a clone of
  `drugref_spl160fix` then `migrate`, the only artefact showing `db/053` applying over rows that predate it.
  Commands: measurement record §1. Keep both.
- **`drugref_spl160fix`** (2 min 09 s) and its BEFORE control **`drugref_spl160`** (12 min 51 s) are the only
  pair that shows #160's 630 s. **`drugref_spl162`**/**`drugref_spl051`** are still on disk; `drugref_spl051` is
  where #159's now-evaporated 49.85 s can still be read. **`drugref_spl`** is the pre-`db/051` base every SPL
  round templates from; **`drugref_dc049`**/**`drugref_dc101`** are DrugCentral's (`dc049` predates `db/050`).
  **Never patch a verification database — rebuild it under a new name.**
- Corpora on disk: `downloads/OPENFDA/` (14 partitions, `export_date` 2026-08-22) and `downloads/DAILYMED/`
  (6 Human Rx parts, `last-modified` 2026-08-21, 17.6 GB). **`downloads/` is gitignored, so every SHA-256 is
  in the mining measurement record §2** — verify against that table, not a manifest that disappears with the
  bytes it describes. The combined `source_checksum` over all twenty files is `5d6a894b30ce…`, identical in all
  four SPL runs to date. The probe cache (`sections.jsonl`) is scratch; `tools/spl_recovery_probe.py` rebuilds it.
