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

**Branch `claude/watershed-column` off `main`; PR OPEN against issue
[#176](https://github.com/cairn-ehr/drugref/issues/176). THE SCHEMA NOW ENDS AT `db/054`.** The suite total
lives in PROJECT-NOTES § "How to run / test" and **nowhere else**, and a test enforces that — `uv run pytest`
fails if the line and the collected suite disagree, which is how this round found its own drift twice rather
than a reviewer finding it a round later.

**⇒ JUST FINISHED — THE WATERSHED COLUMN, CLOSING [#176](https://github.com/cairn-ehr/drugref/issues/176).**
Full account: PROJECT-NOTES § "The watershed round".

**⇒ THE GUARD BUILT TO STOP A WRONG NUMBER STOOD BESIDE THE WRONG NUMBER SAYING NOTHING.** `db/053` changed what
`ingest_run`'s two stamps MEAN and gave a reader nothing to tell the two meanings apart except a clock:
`format_run_duration` compared `started_at` against **when db/053 was applied here**. That asks **WHEN** a row
was written; the question is **WHICH CODE** wrote it, and nothing on the row recorded it. `db/054` adds
`duration_measured boolean NOT NULL DEFAULT false`, set by `provenance.open_run` and by nothing else.

**⇒ BOTH DIRECTIONS REPRODUCED ON A REAL DATABASE (`drugref176`).** An older client's INSERT takes db/053's
`clock_timestamp()` DEFAULT and its old `finish_run` writes `now()`: **two seconds of `pg_sleep` recorded
666 µs**, cleared the CHECK and the watershed alike, and printed **`0.0s`** — issue #159's own failure mode, one
round after it was fixed. Now `unmeasured`, beside a genuine `unii_run` row at 42.8 ms flagged measured. The
mirror case is the one no reader could see: `open_run` **backdates** `started_at` over the pre-open parse, so a
correct new row could land before the watershed and be refused; its test reads the ledger to prove the row
really straddles `applied_at`, or it would pass anywhere and prove nothing.

**⇒ NO ROW WAS BACKFILLED, AND THAT IS THE ROUND'S REAL DECISION** (yours, with both options on the table).
Backfilling from the watershed would have kept today's printed runtimes by **storing** the very inference the
column removes. **Computed wrongly, a wrong answer can be corrected next round; written into a column, it
becomes a fact nobody can tell from a measured one.** Cost is bounded and self-healing: each writer's next
ingest records a real duration. Every existing verification database therefore reads `unmeasured` until
re-ingested — that is the intended state, not a regression.

**⇒ THREE THINGS FOUND ON THE WAY PAST.** `db.migration_applied_at` went with its only caller (43 lines, three
tests). `migration_guard.py`'s docstring hand-listed **"ALL FIVE CALLERS"** and there were already **six** —
replaced by a `grep` that cannot go stale, in the module whose whole subject is diagnoses that assert what they
have not confirmed. And the script written to prove the three re-issued catalog comments verbatim first
reported all three **identical**, because it scanned to the first `;` and db/053's comment holds one inside its
string literal: **a reading identical on both versions is not evidence.**

**⇒ EVERY NEW GATE WAS OBSERVED FAILING.** The db/054 guard, with its wrapper removed, produced exactly the raw
psycopg traceback it prevents; the explanatory line, with its condition mutated to `if loaded:`, failed the
assertion that the two renderings differ. The guard's `consequence` was reworded **after reading the real CLI
output** — `guard_message` appends "until it is fixed".

**⇒ VERIFIED.** `uv run pytest` green at **2571**; CI's own shape (`-q --strict-markers -p no:randomly -rs -m
"not livepage"`) green with **0 skipped, 1 deselected**; `uv run ruff check .` clean; end-to-end on
`drugref176` (migrate, real `ingest unii`, the issue's reproduction, the guard's sentence).

## ⇒ DO THIS NEXT

**Choose one; none is blocked.**

1. **[#179](https://github.com/cairn-ehr/drugref/issues/179)** — four committed tools open their own
   `psycopg.connect` and are therefore **deaf to the server**, which is the notice-channel round's defect one
   layer out; and no tool configures logging, so every NOTICE mapped to INFO is dropped under `tools/`. Wants a
   source-scan test in the shape of `tests/test_spl_tools_smoke.py`'s. Then **#163–#166**, **#168–#171**.
2. **[#177](https://github.com/cairn-ehr/drugref/issues/177) — seven modules over rule 4's cap**, `questions.py`
   at **797** down to `ingest/spl_match.py` at **524**, each a small self-contained round. Precedent is three
   deep (`spl_release.py`, `cli_status.py`, `registry_read.py`); the rule is always the same — find the SEAM,
   verbatim move, suite green, *then* behaviour. The ledger ratchets, so none of them may grow.
3. **`5c.5` pregnancy & lactation is spiked-not-designed** — LactMed puts 1,679 moieties outside MED-RT's thin
   lactation floor, gated on a **clinician review that has not happened** (23-row worklist).
4. **The class half of 5c.3** (#155, #102, word order); deferring it **RAISED** drug × drug yield by 193 pairs.

## Parallel project sequencing

DrugCentral is done: a **candidate-tier floor pinned to the 2023 release** — no refresh, no auto-alert. **SPL
is a fourth candidate source and DELIBERATELY NOT an arm of `exact_ddi_pair`**: it means *a label's interactions
section names both drugs*, not *an authority asserts they interact*. FDA toxicity is cleared; class-grain
content (#98) gates #112/#105.

## Open follow-ups

The full ledger lives once in [PROJECT-NOTES § "The standing open-issue ledger"](PROJECT-NOTES.md).
**#176 is CLOSED by this round; #146 by the one before; #174 and #172 by the one before that.**
Open: **#177** (seven over the cap), **#179** (tools deaf to the server), **#163–#166**, **#168–#171**.
Standing: **#155** and **#102 re-opened**, both inherited by the deferred class half; **#67** (salt↔base) is
wanted by three sources and blocks a grain; **#158** untouched. Also #148, #149, #151, #152, #153, #128/#129,
#132–#135, #124, #121/#123, #104, #94. Before production: re-run every parser on current releases, resolve #17,
and the three rule-6 deeds (#6, #25, GSRS).

## Current DSN

- Test-only DSN: `host=localhost port=5532 dbname=drugref_test user=postgres`. Set `DRUGREF_TEST_DSN` for DB
  tests; never for reviewer accounts or GUI data — pytest recreates it. **See #153 before running two sessions
  at once: concurrent runs drop the schema under each other and the failures look real.**
- **`drugref176` is THIS round's** — built from nothing, migrated to `db/054`, one real `ingest unii` and one
  hand-built **old-client** row (the issue's `pg_sleep(2)` reproduction, recorded as 666 µs). It is the only
  artefact showing the `0.0s`→`unmeasured` correction side by side with a measured row.
- **`drugref_notice174` is the SPL/role-split one** — chain + SPL as owner, then the same SPL ingest under role
  `drugref_app` (no ownership) twice, the second with `MAINTAIN`. **It still carries `drugref_app` and its
  GRANTs**, so it is the one database where the role split can be re-run (that round's record §1);
  **`options='-c role=…'` in a DSN runs an ingest AS another role** with no authentication story.
  `drugref_dur159`/`drugref_dur159mixed` are #159's, `drugref_spl160fix` (2 min 09 s) + `drugref_spl160`
  (12 min 51 s) show #160's 630 s, `drugref_spl051`/`drugref_spl` are #159's and the pre-`db/051` base,
  `drugref_dc049`/`drugref_dc101` DrugCentral's. **All of them predate `db/054`, so every runtime they show now
  reads `unmeasured` until re-ingested — intended, not a regression.**
  **Never patch one — rebuild under a new name**; editing an APPLIED migration breaks every database carrying
  it (`apply_migrations` is checksum-immutable).
- Corpora: `downloads/OPENFDA/` (14 partitions, 2026-08-22), `downloads/DAILYMED/` (6 Human Rx parts,
  2026-08-21, 17.6 GB). **`downloads/` is gitignored, so every SHA-256 is in the mining record §2** — verify
  against that table, not a manifest that vanishes with the bytes.
