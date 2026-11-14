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

**Branch `claude/spl-census-wide` (from `main` after PR #234, MERGED). PR open to `main` for #215 + #228 + #229.
Schema now ends at `db/072`.** The suite total lives in PROJECT-NOTES § "How to run / test" and **nowhere else**;
`uv run pytest` fails if the line and the collected suite disagree.

**⇒ THIS ROUND (built, tested, docs done): #215 + #228 + #229.** `db/072` adds the four class-pass drop counts and
the seven official-name adoption counts to `spl_run_census` (NULL = not measured: every pre-072 row; with no
UNII_Names file, the seven plus R19/P1's drop, i.e. the CHECK groups are 3 + 8). `spl_census.CENSUS_COLUMNS` =
`SplSummary` field names; `write_run_census(..., summary=)` inserts by name, passing None through unchanged.
`source_checksum` now covers UNII_Names and the two shipped SPL lists (`spl_run.checksummed_inputs`), so every
new SPL run hashes differently from older ones on the same corpora (intended). **No ingest was re-run.** Filed
**#235**: `drugref status` does not print the new columns. PROJECT-NOTES § "The SPL census widening".
`ruff format` is NOT applied in this repo; match the hand style. **Do not add `-p no:cacheprovider` to a full
run**: `test_suite_count.py` then fails two tests that are not real.

**⇒ STILL OWED FROM #231/#232 (owner):** the exclusion list applies to search (*glucose* does not find dextrose);
from #227: glance at the exclusions and the 12 shadowed names.

**⇒ CARRY TO cairn-ehr's allergy slice (do not edit that repo from here):** `no-match` is NOT "safe" for
cross-reactivity (cephalosporins, or any other beta-lactam, e.g. carbapenems). Case (c), cefalexin for a penicillin allergy, answers `no-match` (no cross-reactivity rule
exists yet). `unplaced` means "not checked" (30% of prescribable moieties). The group is `structural-seed`, not
clinically reviewed, and the read says so.

## ⇒ DO THIS NEXT

0. **Allergy groups (owner):** (a) who reviews the allow-list so `structural-seed` can come off; (b) when does
   case (c) come, and at what grain (R1 side-chain groups vs blanket); (c) **#223**: the registry's `RXNORM_IN`
   lacks MED-RT's RxCUIs for ampicillin / amoxicillin / nafcillin sodium / Penicillin, which blocks any MED-RT
   `has_SC` join.
1. **Owner's clinical read of the class-mentions round's precision sample** (results record, "Precision sample" and "Gates").
   Trade-offs to decide:
   - Sentence-scope avoid / monitor are withheld by EXACT agreement. Most disagreements were the machine reading
     STRONGER than the label (the safe direction). Should the gate count safe-direction disagreement, and
     re-admit those levels?
   - Contraindicated reads stronger than the label in 22 of 100 (safe direction; zero read lower). Acceptable?
   - **#220**: withheld readings are stored as `not_read`, so no curator ever sees them. Store the level, and
     put withheld strength readings on the worklist? (Also decide **#221** and **#222**.)
   - The reader's two conventions: "Reserve concomitant prescribing ..." reads `avoid` (4,638 spans);
     "separate administration" reads `adjust_dose`. Keep, or change?
   - Wilson lower bounds are below 90% at n = 50 (results record gate table): is a larger sample wanted?
2. The still-owed **clinical read of the class-profiles results §5** (beta blockers, ACE inhibitors, SSRIs);
   **then** write its docs-site *Design decisions* record (living decisions only).
3. **Owner design call on #204**: interaction rulings never reach the prescribing-alert path. **Curators must
   not work `interacts_with` questions until it is decided.**
4. **Owner design calls on #203** (MED-RT "Unknown ..." catch-all classes; near-universal properties such as
   `CI_with Drug Hypersensitivity`). Whatever is chosen, "not confirmed" stays "not confirmed".
5. **Owner questions**: should an ASSERTED property passing down to a subclass honour a `class_expansion_policy`
   deny (today not: db/060, db/065)? May a `curated_interaction` cite the gap-19 `interacts_with class` question
   about the same pair (today the guard says no; from #207)?
6. **No owner call needed:** **#235** (print db/072's columns in `drugref status`; small), then **#226** (measure
   FDA-CYP / onchigh unresolved names a UNII official name would fix; retire `gate.py`'s hand crosswalk;
   `moiety_name` is a ready lookup), **#214** (class-hygiene candidates: `Electrolyte [EPC]`, `Ophthalmic
   Solutions`, the effect classes, `Bile Acid [EPC]`, "consider alternative therapy" as an avoid phrasing) and
   **#217** (order the label class-statement worklist strongest level first).
7. **#155** (PK-axis classes): this round answers it by exclusion (the MED-RT PK and APC axes are never matched).
   Owner to close it or say what remains.
8. Still open on their own triggers: #164, #169 (SPL); `5c.5` pregnancy/lactation is gated on a clinician review
   that has not happened.

## Open follow-ups

The full ledger lives once in [PROJECT-NOTES § "The standing open-issue ledger"](PROJECT-NOTES.md). **#231 CLOSED** (PR
#233); **#216, #218 CLOSED** (PR #234). **#215, #228, #229 close on this branch's PR** (§ "The SPL census widening"); it
files **#235**. **Open from #227: #226.** **Open from allergy groups: #223, #224.** **Open from class profiles: #203,
#204. From class mentions: #214, #217, #220–#222.** Open: **#164**, **#169**. Standing: **#155** and **#102** (remaining
work is #214); **#67** (salt↔base) is wanted by three sources and blocks a grain; **#158** untouched. Also #148, #152,
#153, #128/#129, #132, #134, #135, #124, #121/#123, #104, #94. Before production: re-run every parser on current
releases, resolve #17, the three rule-6 deeds (#6, #25, GSRS).

## Current DSN

- Test-only DSN: `host=localhost port=5532 dbname=drugref_test user=postgres`. Set `DRUGREF_TEST_DSN` for DB
  tests; never for reviewer accounts or GUI data — pytest recreates it. **See #153 before running two sessions
  at once: concurrent runs drop the schema under each other and the failures look real.**
- **This round ran no ingest and migrated no named database** (db/072 reached only the test DB). Test DB this
  branch: `drugref_test_census` (create it with `createdb` first; pytest recreates the schema, not the database).
  Every named database below lacks db/072 until migrated; its pre-072 census rows then read NULL (not measured).
- **`drugref_synonyms` is #231's BAN/AAN round's**: `createdb -T drugref_names`, migrated to `db/071`, one `ingest
  mesh-synonyms` (18,692 stored); never patch it.
- **`drugref_names` is #232's**: `createdb -T drugref_usnames`, `db/070`, one `ingest moiety-names` (1,630 names).
- **`drugref_usnames` is #213's** — `createdb -T drugref_allergy` (`db/069`), then two full SPL ingests: run 11
  (adoption off, reproduces run 17) and **run 12 (the shipped measurement)**; never patch it.
- **`drugref_allergy` is the allergy round's** — `createdb -T drugref_profiles`, migrated to `db/069`, allergy groups ingested
  (MeSH 2026); never patch it. Its test DB: `drugref_test_allergy`. `drugref_profiles` untouched at `db/065`.
- **`drugref_classmentions` is the previous round's** — built from nothing at `db/066` (chain + `onchigh` + `fda-cyp` +
  `drugcentral` + `ingest spl`). **SPL run 17 is the shipped measurement**; it also carries an aborted run 9 and
  superseded runs 10-16. **Never patch it**; census: `uv run python -m tools.spl_class_census --dsn ... --openfda downloads/OPENFDA`.
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
