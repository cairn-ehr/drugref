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

**Branch `claude/spl-class-mentions` (from `main` after PR #212 MERGED, closing #210). PRs #208, #211, #212
MERGED. Schema still ends at `db/065`.** The suite total lives in PROJECT-NOTES § "How to run / test" and
**nowhere else**; `uv run pytest` fails if the line and the collected suite disagree.

**⇒ LAST ROUND: #210.** `citation_audit.miscited(conn)` / `uv run python -m tools.citation_census --dsn ...`
(exit 1 on a live finding). 0 findings on `drugref_profiles`; 2 on `drugref_reviewer_dev` (GUI test data).

**⇒ THIS ROUND: SPL class-mention extraction** (item 6 below) — opens with its own brainstorm/design round.
`ruff format` is NOT applied in this repo (most files fail `--check`); match the hand style. **Do not add
`-p no:cacheprovider` to a full run**: `test_suite_count.py` then fails two tests that are not real.

## ⇒ DO THIS NEXT

1. The owner's **clinical read of the class-profiles results §5**
   (beta blockers, ACE inhibitors, SSRIs); nothing so far drew a clinical conclusion. **After that read**,
   write the docs-site *Design decisions* record for class profiles (living decisions only).
2. **Owner design call on #204**: interaction rulings never reach the prescribing-alert path. **Curators must
   not work `interacts_with` questions until it is decided.**
3. **Owner design calls on #203** before curators work the rest of the worklist: exclude MED-RT's "Unknown ..."
   catch-all classes the way PK is excluded? Treat near-universal properties (`CI_with Drug Hypersensitivity`)
   differently? Whatever is chosen, "not confirmed" stays "not confirmed".
4. **Owner question**: should an ASSERTED property passing down to a subclass honour a
   `class_expansion_policy` deny? Today it does not (db/060 and db/065 read no policy).
5. **Owner question (from #207)**: may a `curated_interaction` cite the gap-19 `interacts_with class` question
   about the same pair? Today the guard says no (only its `uncurated_interaction_rule` question).
6. **SPL class-mention extraction** (the old class half of 5c.3: #102, #155, word order) is the next project
   and FEEDS class profiles. Still open on their own triggers: #164, #169 (SPL); `5c.5` pregnancy/lactation is
   gated on a clinician review that has not happened.

## Open follow-ups

The full ledger lives once in [PROJECT-NOTES § "The standing open-issue ledger"](PROJECT-NOTES.md). **Open
from class profiles: #203, #204; #210 CLOSED (PR #212).** #130 and #133 were CLOSED 2026-11-07; #149 CLOSED (PR #201). Open: **#164**,
**#169**. Standing: **#155** and **#102**, both inherited by SPL class-mention extraction; **#67** (salt↔base)
is wanted by three sources and blocks a grain; **#158** untouched. Also #148, #152, #153, #128/#129, #132,
#134, #135, #124, #121/#123, #104, #94. Before production: re-run every parser on current releases, resolve
#17, the three rule-6 deeds (#6, #25, GSRS).

## Current DSN

- Test-only DSN: `host=localhost port=5532 dbname=drugref_test user=postgres`. Set `DRUGREF_TEST_DSN` for DB
  tests; never for reviewer accounts or GUI data — pytest recreates it. **See #153 before running two sessions
  at once: concurrent runs drop the schema under each other and the failures look real.**
- **`drugref_profiles` is the class-profiles round's** — built from nothing at `db/061` (chain + `onchigh` +
  `fda-cyp` + `drugcentral`, no SPL), migrated to `db/065`, `curated_property_ruling` empty. **Its builds and figures are in the
  [results record](superpowers/specs/2026-11-07-drugref-class-profiles-results.md)**; re-measure #203 there (#202's figures: its §6 `db/065` addendum).
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
  reads `unmeasured` until re-ingested — intended, not a regression.** They also predate `db/057`: none has the `spl_run_census` table, so `status` exits 2 on
  each until migrated (and there is no backfill: those counts were never stored).
  **Never patch one — rebuild under a new name**; editing an APPLIED migration breaks every database carrying
  it (`apply_migrations` is checksum-immutable).
- Corpora: `downloads/OPENFDA/` (14 partitions, 2026-08-22), `downloads/DAILYMED/` (6 Human Rx parts,
  2026-08-21, 17.6 GB). **`downloads/` is gitignored, so every SHA-256 is in the mining record §2** — verify
  against that table, not a manifest that vanishes with the bytes.
