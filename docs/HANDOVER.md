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

**Branch `claude/notice-handler` off `main`; [PR #178](https://github.com/cairn-ehr/drugref/pull/178) is OPEN
and closes #174 and #172. NO MIGRATION** — the schema still ends at **`db/053`**. The suite total lives in
PROJECT-NOTES § "How to run / test" and **nowhere else** ([#146](https://github.com/cairn-ehr/drugref/issues/146));
read it there at the START of the session.

**⇒ JUST FINISHED — THE NOTICE-CHANNEL ROUND + ITS REVIEW, CLOSING [#174](https://github.com/cairn-ehr/drugref/issues/174)
AND [#172](https://github.com/cairn-ehr/drugref/issues/172).** Full account: PROJECT-NOTES § "The notice-channel
round" · [record](superpowers/specs/2026-09-02-drugref-analyze-notice-channel.md), whose §6 is the review.

**⇒ POSTGRESQL SKIPS A TABLE IT WILL NOT ANALYSE, SAYS SO IN A *WARNING*, AND RETURNS SUCCESS** — leaving
`reltuples = -1` and handing back the `ANALYZE` tag. **psycopg discards notices unless a handler is installed,
and `grep -rn add_notice_handler src/ tests/` returned nothing** — so under an admin-migrates / app-ingests
split every `ANALYZE` #160 added did nothing, its **630 s** came back, and the run still reported success. The
project had written that mechanism down **as a comment, in one module** (`drugcentral_run.py`, db/050). ⇒ *A
COMMENT IN ONE MODULE IS NOT A CHANNEL.*

**⇒ WHAT SHIPPED.** `server_messages.py` (all eight protocol severities mapped **once**, reading the
NON-LOCALISED severity — `Diagnostic.severity` is translated, so `WARNUNG` is a real shape; unknown maps to
WARNING, never quieter) · `db.connect` installs it · `analyze.py` runs the statement inside its OWN scoped
collector (a guard reading `db.connect`'s would fire on the CLI path and nowhere else — 74/66/76's *gate that
never fires*; there is a negative control) and **refuses** unless the server did the work. NOTICE sits at INFO
because it was **measured**: a fresh migrate emits **35** notices, a healthy ingest **none**.

**⇒ THREE CHECKS, AND NO TWO ARE ONE CHECK TWICE** — the collected **WARNING** carries the only DIAGNOSIS;
**`reltuples = -1`** needs neither a message nor a counter (`0` is not a milder `-1`); the **`analyze_count`
delta** is the only one that fires on a **re-ingest** with no message. ⇒ **THE THIRD IS THE REVIEW ROUND, WHICH
FOUND 174 INSIDE THE FIX FOR 174.** `client_min_messages`
decides what the server SENDS, is `PGC_USERSET`, and reaches drugref from `ALTER ROLE`/`ALTER DATABASE`/
`postgresql.conf`/a pooler/a DSN `options=`. Above `warning` no WARNING arrives, `reltuples` is blind on every
run after the first, so **the guard was a no-op on every database past its first ingest**. Reproduced, then
closed with `pg_stat_all_tables.analyze_count`. ⇒ **`stats_fetch_consistency` DEFAULTS TO `cache`**, pinning a
stats row at its FIRST read for the transaction — a naive before/after delta is always zero and refuses every
HEALTHY run; `SELECT pg_stat_clear_snapshot()` before each read, and deleting it fails 8 tests. **No check may
be silently unavailable**: with `track_counts` off AND the channel quiet the guard refuses BEFORE the statement.
A quiet channel *alone* is not refused — the counter still proves the work.

**⇒ ALSO FROM THAT REVIEW.** Both notice handlers go through `read_diagnostic_safely` — psycopg swallows what a
handler raises and `read_diagnostic` is duck-typed, so an unreadable diagnostic left the collector's list EMPTY,
indistinguishable from silence. `serious_messages` asks the SQLSTATE (class `00` = success, the skipped ANALYZE
`01000`) when the severity is unclassifiable, so a German `HINWEIS` no longer aborts an ingest; `SEVERITY_LEVEL`
is a `MappingProxyType`. The size-cap ledger's values were never read — the seven LARGEST modules were exempt
GROWING — and now ratchet. `test_spl_tools_smoke.py`'s scan could match nothing and pass, and did; four tools
stay deaf ([#179](https://github.com/cairn-ehr/drugref/issues/179)).

**⇒ VERIFIED AT FULL SCALE ON BOTH SIDES OF THE ROLE SPLIT** (`drugref_notice174`, from nothing). Owner: chain
**132.91 s**, `ingest spl` **131.77 s**, every figure reproduced. Split role without `MAINTAIN`: **REFUSED at
81.76 s, exit 2**, projection untouched, `finished_at IS NULL` — and a **RE-INGEST** (`reltuples` 27,406), so
only the WARNING could have fired; `GRANT MAINTAIN` → the identical command completes (150.02 s). **Those runs
measured the TWO-check guard**; the third adds three catalogue reads per `ANALYZE`, not re-measured at scale.

**⇒ #172 CLOSED AT THE SEAM IT NAMED, AND THE CAP BECAME A SWEEP.** `Registry`/`load_registry` — a READ path
inside the SOLE WRITER — moved to `registry_read.py`; `spl_evidence.py` **518 → 428** (430 after the review
round's docstring corrections). `500` had lived in two
test files guarding three modules of 76; `test_module_size_cap.py` owns it once, sweeps `src/drugref`, ratchets
the seven already over — **[#177](https://github.com/cairn-ehr/drugref/issues/177)**.

**⇒ AND THE SUITE COUNT WAS STALE AT SESSION START — THE NINTH OCCURRENCE, IN THE SAME PLACE AS THE SEVENTH:**
PR #175 wrote "2467 -> 2475" into its **commit message** and left PROJECT-NOTES at 2467, on the very branch
whose diff added *"a commit message is not a home"*. ⇒ **[#146](https://github.com/cairn-ehr/drugref/issues/146)
IS NOW THE ONE TO WRITE.**

## ⇒ DO THIS NEXT

**Choose one; none is blocked.**

1. **[#146](https://github.com/cairn-ehr/drugref/issues/146) — a test that reads the suite count out of
   PROJECT-NOTES and compares it to the collected total.** Nine occurrences against a comment rewritten three
   times to prevent them. Small, and the only thing that makes the prose unnecessary. **Do it first.**
2. **[#176](https://github.com/cairn-ehr/drugref/issues/176)** — the runtime watershed dates rows by **time**,
   not **writer**, so an older client on a migrated database publishes a confident `0.0s` for a two-second run;
   a boolean set by `open_run` replaces it. Then **#179**, **#163–#166**, **#168–#171**.
3. **[#177](https://github.com/cairn-ehr/drugref/issues/177) — seven modules over rule 4's cap**, `questions.py`
   at **797** down to `ingest/spl_match.py` at **524**, each a small self-contained round. Precedent is three
   deep (`spl_release.py`, `cli_status.py`, `registry_read.py`); the rule is always the same — find the SEAM,
   verbatim move, suite green, *then* behaviour. The ledger now ratchets, so none of them may grow.
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
**#174 and #172 are CLOSED by this round; #159 and #160 by the two before.** Open: **#177** (seven over the
cap), **#179** (tools deaf to the server), **#176** (the watershed dates rows by time, not writer),
**#163–#166**, **#168–#171**. Standing:
**#155** and **#102 re-opened**, both inherited by the deferred class half; **#67** (salt↔base) is wanted by
three sources and blocks a grain; **#158** untouched. Also #148, #149, #151, #152, #153, #146, #128/#129, #132–#135, #124,
#121/#123, #104, #94. Before production: re-run every parser on current releases, resolve #17, and the three
rule-6 deeds (#6, #25, GSRS).

## Current DSN

- Test-only DSN: `host=localhost port=5532 dbname=drugref_test user=postgres`. Set `DRUGREF_TEST_DSN` for DB
  tests; never for reviewer accounts or GUI data — pytest recreates it. **See #153 before running two sessions
  at once: concurrent runs drop the schema under each other and the failures look real.**
- **`drugref_notice174` is THIS round's** — chain + SPL as owner, then the same SPL ingest under role
  `drugref_app` (no ownership) twice, the second with `MAINTAIN`. **It still carries `drugref_app` and its
  GRANTs**, so it is the one database where the role split can be re-run (record §1); **`options='-c role=…'`
  in a DSN runs an ingest AS another role** with no authentication story.
  `drugref_dur159`/`drugref_dur159mixed` are #159's, `drugref_spl160fix` (2 min 09 s) + `drugref_spl160`
  (12 min 51 s) show #160's 630 s, `drugref_spl051`/`drugref_spl` are #159's and the pre-`db/051` base,
  `drugref_dc049`/`drugref_dc101` DrugCentral's.
  **Never patch one — rebuild under a new name**; editing an APPLIED migration breaks every database carrying
  it (`apply_migrations` is checksum-immutable).
- Corpora: `downloads/OPENFDA/` (14 partitions, 2026-08-22), `downloads/DAILYMED/` (6 Human Rx parts,
  2026-08-21, 17.6 GB). **`downloads/` is gitignored, so every SHA-256 is in the mining record §2** — verify
  against that table, not a manifest that vanishes with the bytes.
