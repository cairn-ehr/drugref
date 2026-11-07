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

**Branch `claude/class-profiles`; PR pending (to `main`). Schema now ends at `db/061`** (`058`–`061`, class
profiles). The suite total lives in PROJECT-NOTES § "How to run / test" and **nowhere else**; `uv run pytest`
fails if the line and the collected suite disagree.

**⇒ JUST FINISHED: class profiles, built and measured** on `drugref_profiles`. What a class has (asserted or
observed), each member's standing, peculiarities, `drugref profile class|drug|rule`, and gap kind 19
`unconfirmed_class_property`. **"Not confirmed" is never "safe".** Figures: the
[results record](superpowers/specs/2026-11-07-drugref-class-profiles-results.md); account: PROJECT-NOTES §
"The class-profiles round". **Two findings filed, not tuned:** #202 (single-class reads cost the whole view —
the profile CLI is too slow for interactive use) and #203 (the worklist's composition).

**⇒ VERIFIED.** `uv run pytest` with the DB: green at the PROJECT-NOTES total. `ruff check src tests` clean.
(`ruff format` is NOT applied in this repo — most existing files fail `--check`; match the hand style.)
**Do not add `-p no:cacheprovider` to a full run**: `test_suite_count.py` then fails two tests that are not real.

## ⇒ DO THIS NEXT

1. **Get the PR reviewed and merged**, then the owner's **clinical read of results §5** (beta blockers, ACE
   inhibitors, SSRIs). Nothing in this round drew a clinical conclusion.
2. **Owner design calls on #203** before curators work the new worklist: exclude MED-RT's "Unknown ..."
   catch-all classes the way PK is excluded? Treat near-universal properties (`CI_with Drug Hypersensitivity`)
   differently on the worklist? Whatever is chosen, "not confirmed" stays "not confirmed".
3. **#202** before the read path gets a consumer beyond the CLI (an API, Cairn): a filter-pushable
   `class_property` and a join-based `asserted_by`. A NEW migration — `db/060` is immutable once merged.
4. **SPL class-mention extraction** (the old class half of 5c.3: #102, #155, word order) is the next project
   and FEEDS class profiles: today no source states a condition about a class. Expect drug × drug yield to
   fall when classes are re-added (ROADMAP § 5c.3) — not a regression.
5. Still open on their own triggers: #164, #169 (SPL); `5c.5` pregnancy/lactation is gated on a clinician
   review that has not happened (23-row worklist).

## Open follow-ups

The full ledger lives once in [PROJECT-NOTES § "The standing open-issue ledger"](PROJECT-NOTES.md). **New:
#202, #203.** #130 and #133 were CLOSED 2026-11-07; #149 CLOSED (PR #201). Open: **#164**, **#169**. Standing:
**#155** and **#102**, both inherited by SPL class-mention extraction; **#67** (salt↔base) is wanted by three
sources and blocks a grain; **#158** untouched. Also #148, #152, #153, #128/#129, #132, #134, #135, #124,
#121/#123, #104, #94. Before production: re-run every parser on current releases, resolve #17, the three
rule-6 deeds (#6, #25, GSRS).

## Current DSN

- Test-only DSN: `host=localhost port=5532 dbname=drugref_test user=postgres`. Set `DRUGREF_TEST_DSN` for DB
  tests; never for reviewer accounts or GUI data — pytest recreates it. **See #153 before running two sessions
  at once: concurrent runs drop the schema under each other and the failures look real.**
- **`drugref_profiles` is the class-profiles round's** — built from nothing at `db/061` (chain + `onchigh` +
  `fda-cyp` + `drugcentral`, no SPL), `curated_property_ruling` empty. **Its builds and figures are in the
  [results record](superpowers/specs/2026-11-07-drugref-class-profiles-results.md)**; re-measure #202/#203 there.
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
