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

**Branch `claude/spl-census-version` off `main`; PR OPEN for #188 and #170, which it closes on merge. No
migration — THE SCHEMA STILL ENDS AT `db/056`.** PR #194 is MERGED (#187 CLOSED). `main` also carries
`3d56d5f`, the new `finalcheck` skill (review-pr → fixall → drive CI green). The suite total lives in
PROJECT-NOTES § "How to run / test" and **nowhere else**; `uv run pytest` fails if the line and the collected
suite disagree.

**⇒ JUST FINISHED** (full account: PROJECT-NOTES § "The SPL census and Version round"). **#188**: each
`part-census` line carries the part's class codes, and `--json` is rewritten after every part in its final
shape. **#170**: SPL's `<versionNumber>` is one `Version` (PARSED/ABSENT/JUNK) in the new pure
`ingest/spl_version.py`, read by `spl_dailymed.version_of`, which both the ingest and the census call;
`version_was_unreadable` and `_tie_break` are gone. Also fixed: the spike `tools/spl_subject_read.py`'s
version-0 tie-break bug. `spl_dailymed.py` 508 → 446 after the split.

**⇒ VERIFIED.** `uv run pytest` with the DB: green at the PROJECT-NOTES total (+17). `ruff check .` clean.
(`ruff format` is NOT applied in this repo — most existing files fail `--check`; match the hand style.)

## ⇒ DO THIS NEXT

**Choose one; none is blocked.**

1. **The SPL follow-ups left on their own triggers**: #164 (the next migration touching that function), #169
   (the 12th `ScanResult` counter); see the ledger. #186 (persist #163's counts per run) needs a migration.
2. **`5c.5` pregnancy & lactation is spiked-not-designed**: LactMed puts 1,679 moieties outside MED-RT's thin
   lactation floor. It is gated on a **clinician review that has not happened** (23-row worklist).
3. **The class half of 5c.3** (#155, #102, word order). Deferring it **RAISED** drug × drug yield by 193 pairs.
4. **Review follow-ups** #186 (PR #185), #195 (PR #194: the same negative-count audit for the non-SPL summary types), still open.

## Parallel project sequencing

DrugCentral is done: a **candidate-tier floor pinned to the 2023 release** — no refresh, no auto-alert. **SPL is a
fourth candidate source and DELIBERATELY NOT an arm of `exact_ddi_pair`**: it means *a label's interactions section
names both drugs*, not *an authority asserts they interact*. FDA toxicity is cleared; class-grain content (#98) gates
#112/#105.

## Open follow-ups

The full ledger lives once in [PROJECT-NOTES § "The standing open-issue ledger"](PROJECT-NOTES.md). **#163, #165,
#166, #168, #171 are CLOSED (PR #185); #151 is CLOSED (PR #189).** **#177 and #89 are CLOSED (PR #193). #187 is CLOSED (PR #194). #188 and #170 close on this PR's merge.** Open: **#186** (PR #185's review), **#195** (PR #194's review), **#164**, **#169**. Standing: **#155** and **#102 re-opened**, both inherited by the deferred class
half; **#67** (salt↔base) is wanted by three sources and blocks a grain; **#158** untouched. Also #148, #149,
#152, #153, #128/#129, #132–#135, #124, #121/#123, #104, #94. Before production: re-run every parser on current
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
  `drugref_dc049`/`drugref_dc101` DrugCentral's. **All of them predate `db/054` (and `db/055`/`db/056`, which add only triggers), so every runtime they show now
  reads `unmeasured` until re-ingested — intended, not a regression.**
  **Never patch one — rebuild under a new name**; editing an APPLIED migration breaks every database carrying
  it (`apply_migrations` is checksum-immutable).
- Corpora: `downloads/OPENFDA/` (14 partitions, 2026-08-22), `downloads/DAILYMED/` (6 Human Rx parts,
  2026-08-21, 17.6 GB). **`downloads/` is gitignored, so every SHA-256 is in the mining record §2** — verify
  against that table, not a manifest that vanishes with the bytes.
