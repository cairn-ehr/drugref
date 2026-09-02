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
`open_run` backdates `started_at` to the orchestrator's first line; reviewed and corrected in the same branch.
Full account: PROJECT-NOTES § "The ingest-duration round" ·
[record](superpowers/specs/2026-09-02-drugref-ingest-run-duration.md).

**⇒ THE COLUMN MEASURED THE TIME AN ORCHESTRATOR SPENT *NOT* TOUCHING THE DATABASE.** `now()` is
`transaction_timestamp()` and `open_run` COMMITS, so the two stamps sat in two transactions and the subtraction
gave the gap between their starts. Eight of nine feeds read **1.3–24 ms**; the ninth, `mesh_rel_run` at
**48.32 s**, was reporting how long it takes to parse 750 MB of MeSH before its first write.

**⇒ THE ISSUE'S HEADLINE FIGURE HAD ALREADY EVAPORATED, AND THREE THINGS READ PAST IT.** #159 cited **49.85 s**
for `spl_run`. Five days later the COPY-cost round put a `conn.rollback()` in front of the DailyMed scan, moving
`open_run` past it — **0.0026 s** on both databases that round built. The issue, the suite and that round's own
review all missed it. ⇒ *A NUMBER IN A FILED ISSUE IS A MEASUREMENT WITH NO OWNER: the round that moves it is
not the round that reads it. Re-measure the premise before designing against it.*

**⇒ THE REVIEW ROUND FIXED THREE SHIPPED DEFECTS AND LODGED THE FOURTH.** (a) `drugref status` **crashed
mid-output** on a ledger-less database — a shape `migration_guard`'s docstring names reachable — skipping five
of six blocks. (b) db/053's `started_at` comment refuted itself in nine words (*"every one of the nine … and
the one that reported anything else"*) while all four docs said *eight of nine*. (c) `format_run_duration`
printed **`1m60s`** for 0.83 % of runs over a minute: the `< 60` branch tested `round(seconds, 1)`, the minutes
branch re-rounded the *unrounded* remainder. ⇒ *TWO ROUNDINGS OF ONE QUANTITY IS ONE RULE IN TWO PLACES.*
Lodged as **[#176](https://github.com/cairn-ehr/drugref/issues/176)**: the watershed dates rows by **time**,
not **writer**, so an older client on a migrated database publishes a confident `0.0s` for a two-second run —
159's own failure mode, reproduced. Nothing in the catalog now claims it cannot happen.

**⇒ VERIFIED AS A RATIO, NINE TIMES, ON A DATABASE BUILT FROM NOTHING** (`drugref_dur159`; a template carries
rows written under the old meaning). Recorded vs `/usr/bin/time -p`: chain **99.7%**, spl **97.0%**
(0.0026 s → 135.86 s). Residual = 0.29–0.34 s interpreter start plus SPL's ~3.8 s final COMMIT, which
`finish_run`'s no-commit contract puts outside the stamp and the column comment names. Per-feed table: the spec.

**⇒ `mesh_rel_run` IS THE CROSS-CHECK:** its old 48.32 s is now a *subset* of its new **56.81 s** (parse 48 +
writes 9), with nothing about it changed. SPL reproduced every published figure (68,550 labels, 29,952 pairs).

**⇒ THE CHECK CAUGHT THE ROUND'S OWN BLIND SPOT, IN THE SUITE.** `finished_at >= started_at` failed five tests
instantly on rows that **finished 3.8 ms before they started**: two helpers stamped `finished_at = now()`
against a `clock_timestamp()` default. ⇒ **MIXING `now()` AND `clock_timestamp()` IN ONE TRANSACTION PRODUCES A
NEGATIVE DURATION.** Its causes — including a backward *server* clock, which the migration first denied — are
now in a `COMMENT ON CONSTRAINT`, the only new object an operator meets by name.

**⇒ OLD ROWS ARE REFUSED AT THE OPERATOR SURFACE, NOT ONLY IN A COMMENT.** `status` prints `pre-db/053`, not a
runtime, for a run predating the migration — verified unpatched on `drugref_spl160fix` (unmigrated) and
`drugref_dur159mixed` (clone then `migrate`), where **db/053 applied over nine rows and validated all nine**.

**⇒ AND THE DERIVED CONTRACT COULD NOT KILL ITS OWN MUTANT — NOW IT DOES.** The grep (every module calling
`open_run` also calls `start_clock`) passed against `start_clock()` moved to the line above `open_run`, which
measures nothing; the one behavioural killer drives `ingest_unii` alone, so the mutation stayed invisible in
`spl_run` and `mesh_rel_run`, the two writers the figures come from — and it matched a *comment* in
`onchigh_run.py`. Replaced by an **AST** test (`start_clock()` is the first executable statement of all eleven
entry points), mutation-verified against `spl_run`. ⇒ *A GREP DERIVES TEXT, NOT STRUCTURE.*

## ⇒ DO THIS NEXT

**Choose one; none is blocked.**

1. **#174 with a notice handler in `db.connect`** — the most valuable one left, and now the only *correctness*
   item on the list: `ANALYZE` on a table the ingest role does not own is a **WARNING**, so it skips, returns
   success, and psycopg discards it — the #160 fix silently reverts under an admin-migrates/app-ingests split
   while the ingest still reports success. The handler makes every future skipped `ANALYZE`, `NOTICE` and
   `WARNING` visible instead of discarded. Then **#163–#166**, **#168–#171** (#168 is three more homes of one
   vocabulary in `tools/`, the class this slice has now found five times). **[#176](https://github.com/cairn-ehr/drugref/issues/176)**
   belongs here too: a boolean set by `open_run` replaces the time-based watershed.
2. **[#172](https://github.com/cairn-ehr/drugref/issues/172) — `spl_evidence.py` at 512/500.** Seam:
   `Registry`/`load_registry` (a READ path in the SOLE WRITER); the census round's `spl_release.py` split is the
   precedent — verbatim move first, suite green, *then* behaviour. `cli.py` is **477/500**: the review round
   took the loaded-release block out to `cli_status.py` (238) rather than let issue 159's runtime column push
   it to 499. The next block added there needs the same move, not another paragraph.
3. **`5c.5` pregnancy & lactation is spiked-not-designed** — LactMed puts 1,679 moieties outside MED-RT's thin
   lactation floor, gated on a **clinician review that has not happened** (23-row worklist in the spike).
4. **The class half of 5c.3**, where every unsolved problem lives (#155, #102, the word-order gap). **Deferring
   it RAISED the drug × drug yield by 193 pairs** — a round re-adding classes must expect a FALL.

## Parallel project sequencing

DrugCentral is done: a **candidate-tier floor pinned to the 2023 release** — it does not refresh, and nothing
in that tier may auto-alert. **SPL is a fourth candidate source and DELIBERATELY NOT an arm of
`exact_ddi_pair`** — it means *a label's interactions section names both drugs*, not *an authority asserts they
interact*. FDA toxicity is cleared; class-grain content (#98) gates #112/#105.

## Open follow-ups

The full ledger lives once in [PROJECT-NOTES § "The standing open-issue ledger"](PROJECT-NOTES.md).
**#159 is CLOSED by this round; #160 by the last.** Open from the review rounds: **#176** (the watershed dates
rows by time, not writer), **#174** (`ANALYZE` skipped, WARNING discarded), **#172** (`spl_evidence.py` 512),
**#163–#166**, **#168–#171**. Standing: **#155** and **#102 re-opened**, both inherited by the deferred class
half; **#67** (salt↔base) is wanted by three sources and blocks a grain; **#158** untouched. Also #148, #149,
#151, #152, #153, #146, #128/#129, #132–#135, #124, #121/#123, #104, #94. Before production: re-run every
parser on current releases, resolve #17, and the three rule-6 deeds (#6, #25, GSRS).

## Current DSN

- Test-only DSN: `host=localhost port=5532 dbname=drugref_test user=postgres`. Set `DRUGREF_TEST_DSN` for DB
  tests; never for reviewer accounts or GUI data — pytest recreates it. **See #153 before running two sessions
  against it at once: concurrent runs drop the schema under each other and the failures look like real ones.**
- **`drugref_dur159` is THIS round's** — built from **nothing**, because a template carries nine `ingest_run`
  rows under the old meaning; **`drugref_dur159mixed`** (clone of `spl160fix` then `migrate`) is the only
  artefact showing db/053 applying over rows that predate it. Keep both; commands in the record §1.
  **`drugref_spl160fix`** (2 min 09 s) + control **`drugref_spl160`** (12 min 51 s) show #160's 630 s;
  `drugref_spl051` still holds #159's evaporated 49.85 s. `drugref_spl` is the pre-`db/051` SPL base,
  `drugref_dc049`/`drugref_dc101` DrugCentral's. **Never patch one — rebuild under a new name.**
  **NOTE: editing an APPLIED migration breaks these** (`apply_migrations` is checksum-immutable); db/053
  changed in review while still unmerged, so any database already carrying it must be rebuilt.
- Corpora: `downloads/OPENFDA/` (14 partitions, 2026-08-22) and `downloads/DAILYMED/` (6 Human Rx parts,
  2026-08-21, 17.6 GB). **`downloads/` is gitignored, so every SHA-256 is in the mining record §2** — verify
  against that table, not a manifest that vanishes with the bytes. Combined `source_checksum` `5d6a894b30ce…`,
  identical across all four SPL runs. `sections.jsonl` is scratch; `tools/spl_recovery_probe.py` rebuilds it.
