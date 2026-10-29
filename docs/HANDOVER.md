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

**Branch `claude/tools-notice-channel` off `main`; PR OPEN against issue
[#179](https://github.com/cairn-ehr/drugref/issues/179). THE SCHEMA STILL ENDS AT `db/054`** (no migration this
round). #181 (the watershed column, #176) is MERGED. The suite total lives in PROJECT-NOTES § "How to run /
test" and **nowhere else**, and a test enforces that — `uv run pytest` fails if the line and the collected
suite disagree.

**⇒ JUST FINISHED — THE TOOLS-CHANNEL ROUND, CLOSING [#179](https://github.com/cairn-ehr/drugref/issues/179)**
(full account: PROJECT-NOTES § "The tools-channel round").

**⇒ #174'S CHANNEL WAS STILL DARK UNDER `tools/`, FOR TWO REASONS.** Three tools (`spl_recovery_probe`,
`spl_registry`, `drugcentral_ddi_spike`) opened a bare `psycopg.connect` — no notice handler. And no tool
configured logging, so the root logger had no handler and every NOTICE mapped to INFO was dropped even
through `db.connect`.

**⇒ WHAT SHIPPED.** `server_messages.configure_logging(level="info")` is the one home for the log format;
`cli.main` calls it, and so does every `tools/*.py` `__main__` block (nine). The three connects use
`db.connect`. `tests/test_tools_server_channel.py` source-scans both rules with a floor on how much the scans
must find, and tests the helper in a **subprocess** (pytest's root handlers make `basicConfig` a no-op
in-process, so an in-process test could not fail).

**⇒ VERIFIED, THEN REVIEWED.** Four agent reviewers (code, tests, comments, silent failures): no critical
finding. Fixed in the same PR — the scans now **parse** source (substrings were fooled by aliases, `from psycopg
import connect`, single-quoted guards, a call after `main()`), cover **`src/` too** (all but `db.py`),
`configure_logging` falls back to **`$DRUGREF_LOG_LEVEL`** (tools had no way to show DEBUG; `drugref
--log-level` honours it too), and four comments were corrected. `uv run pytest` green at **2706**; `ruff check
.` clean; every touched tool runs `--help`.

## ⇒ DO THIS NEXT

**Choose one; none is blocked.**

1. **[#182](https://github.com/cairn-ehr/drugref/issues/182)** — `db/054`'s no-backfill rule is prose only;
   needs a narrow trigger (`finish_run` legitimately flips `false → true`). Then **#163–#166**, **#168–#171**.
2. **[#177](https://github.com/cairn-ehr/drugref/issues/177) — seven modules over rule 4's cap**, `questions.py`
   at **797** down to `ingest/spl_match.py` at **524**, each a small self-contained round. Precedent is three
   deep (`spl_release.py`, `cli_status.py`, `registry_read.py`); the rule is always the same — find the SEAM,
   verbatim move, suite green, *then* behaviour. The ledger ratchets, so none of them may grow.
3. **`5c.5` pregnancy & lactation is spiked-not-designed** — LactMed puts 1,679 moieties outside MED-RT's thin
   lactation floor, gated on a **clinician review that has not happened** (23-row worklist).
4. **The class half of 5c.3** (#155, #102, word order); deferring it **RAISED** drug × drug yield by 193 pairs.

## Parallel project sequencing

DrugCentral is done: a **candidate-tier floor pinned to the 2023 release** — no refresh, no auto-alert. **SPL is a
fourth candidate source and DELIBERATELY NOT an arm of `exact_ddi_pair`**: it means *a label's interactions section
names both drugs*, not *an authority asserts they interact*. FDA toxicity is cleared; class-grain content (#98) gates
#112/#105.

## Open follow-ups

The full ledger lives once in [PROJECT-NOTES § "The standing open-issue ledger"](PROJECT-NOTES.md). **#176 is CLOSED
by the round before; #179 by this one.** Open: **#177** (seven over the cap), **#182** (db/054's no-backfill rule has no mechanism — filed by #181's
review), **#163–#166**, **#168–#171**. Standing: **#155** and **#102 re-opened**, both inherited by the deferred class
half; **#67** (salt↔base) is wanted by three sources and blocks a grain; **#158** untouched. Also #148, #149,
#151–#153, #128/#129, #132–#135, #124, #121/#123, #104, #94. Before production: re-run every parser on current
releases, resolve #17, the three rule-6 deeds (#6, #25, GSRS).

## Current DSN

- Test-only DSN: `host=localhost port=5532 dbname=drugref_test user=postgres`. Set `DRUGREF_TEST_DSN` for DB
  tests; never for reviewer accounts or GUI data — pytest recreates it. **See #153 before running two sessions
  at once: concurrent runs drop the schema under each other and the failures look real.**
- **`drugref176` is the watershed round's (#176)** — from nothing, migrated to `db/054`, a real `ingest unii` and a hand-built
  **old-client** row (the issue's `pg_sleep(2)`, recorded as 666 µs): the `0.0s` → `unmeasured` correction
  beside a measured row. **db/054 was edited in #181's review, so `drugref176` carries the OLD checksum and
  REFUSES to migrate — rebuild it from nothing if needed.** `drugref_test` is recreated by pytest (green here).
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
