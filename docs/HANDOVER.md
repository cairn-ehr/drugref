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

**Branch `claude/suite-count-gate` off `main`; PR OPEN, closes [#146](https://github.com/cairn-ehr/drugref/issues/146).
NO MIGRATION** — the schema still ends at **`db/053`**. The suite total lives in PROJECT-NOTES § "How to run /
test" and **nowhere else** — and **as of this round a test enforces that**, so you no longer have to check it by
hand at session start: `uv run pytest` fails if the line and the collected suite disagree.

**⇒ JUST FINISHED — THE SUITE-COUNT GATE, CLOSING [#146](https://github.com/cairn-ehr/drugref/issues/146).**
Full account: PROJECT-NOTES § "The suite-count gate round".

**⇒ NINE DRIFTS AGAINST A COMMENT REWRITTEN THREE TIMES TO PREVENT THEM.** The suite count in PROJECT-NOTES
calls itself THE ONE HOME FOR THIS NUMBER; it went stale nine times, twice into a **commit message**, the ninth
on the very branch whose diff added the sentence *"a commit message is not a home"*. ⇒ **PROSE THAT HAS FAILED
NINE TIMES IS NOT GOING TO WORK ON THE TENTH.** `tests/test_suite_count.py` reads that line and compares it with
what `pytest` collected; `tests/suite_count.py` holds the pure halves. **The number is still stated in exactly
one place** — the new files read it and never restate it, which is what #146 asked for, because occurrence six
was created by the round that *filed* #146 writing the count into three further places.

**⇒ THREE DECISIONS ARE LOAD-BEARING, AND EACH AVOIDS A TRAP THIS REPO HAS PAID FOR.** The count is the
**pre-deselection** total (`conftest.pytest_collection_finish` + `pytest_deselected`): CI runs
`-m "not livepage"`, so a selected-item count would differ between CI and a local run and the line could only
ever have matched **one of the two** — a gate that fails in CI for a non-defect gets switched off. A **narrowed**
run (path arguments, `--ignore`, `--lf`, `--sw`) skips — and the real danger is the opposite one, a detector so
eager the gate becomes a **permanent skip**, which is 74/66/76 exactly, so a negative control asserts the bare
and the CI command lines come back **not narrowed**, and ci.yml's second step fails on any skip. The ledger of
narrowing options **refuses a name it does not recognise**: `options.get(name, neutral)` would make a renamed
pytest dest read as *"not in use"* for ever. `-k`/`-m`/`--deselect` are deliberately **not** narrowing —
deselections are counted back. **The remaining hole is chosen**: an unknown narrowing collects FEWER tests than
stated and is reported as **drift** — loud and wrong, never silent and right.

**⇒ THE GATE WAS OBSERVED FAILING BEFORE THE NUMBER WAS UPDATED** (`states 2538 tests; this run collected 2561
(+23)`), which is the only way to know it is not a third gate that never fires.

**⇒ AND IT FOUND A TEST THAT PASSED FOR A REASON IT DID NOT STATE.**
`test_registry_read.py::test_registry_is_empty_on_a_migrated_but_uningested_database` asserts a GLOBAL
precondition — no moieties registered — that it never established, and had done so since issue 120: half this
suite commits, and the assertion held only because twenty later modules `TRUNCATE` in an autouse fixture and
that file sorts after several of them. **Reproduce in 1.4 s: `uv run pytest tests/test_cli.py
tests/test_registry_read.py`.** Found because `--lf` hoists files with cached failures to the front;
**confirmed pre-existing by re-running on unmodified `main`** before anything was written. Fixed with a fixture
that TRUNCATEs **without committing** — TRUNCATE is transactional and the `conn` fixture rolls back, so no other
module's committed state is disturbed. It has to be TRUNCATE, not DELETE: the append-only floor refuses a
DELETE, and not covering TRUNCATE is the documented bypass.

**⇒ VERIFIED.** `uv run pytest` green; CI's own shape (`-q --strict-markers -p no:randomly -rs -m
"not livepage"`) green with **0 skipped**; the reordered `--lf` run green; `uv run ruff check .` clean.

## ⇒ DO THIS NEXT

**Choose one; none is blocked.**

1. **[#176](https://github.com/cairn-ehr/drugref/issues/176)** — the runtime watershed dates rows by **time**,
   not **writer**, so an older client on a migrated database publishes a confident `0.0s` for a two-second run;
   a boolean set by `open_run` replaces it. Small and self-contained.
2. **[#179](https://github.com/cairn-ehr/drugref/issues/179)** — four committed tools open their own
   `psycopg.connect` and are therefore **deaf to the server**, which is the notice-channel round's defect one
   layer out. Then **#163–#166**, **#168–#171**.
3. **[#177](https://github.com/cairn-ehr/drugref/issues/177) — seven modules over rule 4's cap**, `questions.py`
   at **797** down to `ingest/spl_match.py` at **524**, each a small self-contained round. Precedent is three
   deep (`spl_release.py`, `cli_status.py`, `registry_read.py`); the rule is always the same — find the SEAM,
   verbatim move, suite green, *then* behaviour. The ledger ratchets, so none of them may grow.
4. **`5c.5` pregnancy & lactation is spiked-not-designed** — LactMed puts 1,679 moieties outside MED-RT's thin
   lactation floor, gated on a **clinician review that has not happened** (23-row worklist).
5. **The class half of 5c.3** (#155, #102, word order); deferring it **RAISED** drug × drug yield by 193 pairs.

## Parallel project sequencing

DrugCentral is done: a **candidate-tier floor pinned to the 2023 release** — no refresh, no auto-alert. **SPL
is a fourth candidate source and DELIBERATELY NOT an arm of `exact_ddi_pair`**: it means *a label's interactions
section names both drugs*, not *an authority asserts they interact*. FDA toxicity is cleared; class-grain
content (#98) gates #112/#105.

## Open follow-ups

The full ledger lives once in [PROJECT-NOTES § "The standing open-issue ledger"](PROJECT-NOTES.md).
**#146 is CLOSED by this round; #174 and #172 by the one before; #159 and #160 by the two before that.**
Open: **#177** (seven over the cap), **#179** (tools deaf to the server), **#176** (the watershed dates rows by
time, not writer), **#163–#166**, **#168–#171**. Standing:
**#155** and **#102 re-opened**, both inherited by the deferred class half; **#67** (salt↔base) is wanted by
three sources and blocks a grain; **#158** untouched. Also #148, #149, #151, #152, #153, #128/#129, #132–#135, #124,
#121/#123, #104, #94. Before production: re-run every parser on current releases, resolve #17, and the three
rule-6 deeds (#6, #25, GSRS).

## Current DSN

- Test-only DSN: `host=localhost port=5532 dbname=drugref_test user=postgres`. Set `DRUGREF_TEST_DSN` for DB
  tests; never for reviewer accounts or GUI data — pytest recreates it. **See #153 before running two sessions
  at once: concurrent runs drop the schema under each other and the failures look real.**
- **`drugref_notice174` is the SPL/role-split one** — chain + SPL as owner, then the same SPL ingest under role
  `drugref_app` (no ownership) twice, the second with `MAINTAIN`. **It still carries `drugref_app` and its
  GRANTs**, so it is the one database where the role split can be re-run (that round's record §1);
  **`options='-c role=…'` in a DSN runs an ingest AS another role** with no authentication story.
  `drugref_dur159`/`drugref_dur159mixed` are #159's, `drugref_spl160fix` (2 min 09 s) + `drugref_spl160`
  (12 min 51 s) show #160's 630 s, `drugref_spl051`/`drugref_spl` are #159's and the pre-`db/051` base,
  `drugref_dc049`/`drugref_dc101` DrugCentral's.
  **Never patch one — rebuild under a new name**; editing an APPLIED migration breaks every database carrying
  it (`apply_migrations` is checksum-immutable).
- Corpora: `downloads/OPENFDA/` (14 partitions, 2026-08-22), `downloads/DAILYMED/` (6 Human Rx parts,
  2026-08-21, 17.6 GB). **`downloads/` is gitignored, so every SHA-256 is in the mining record §2** — verify
  against that table, not a manifest that vanishes with the bytes.
