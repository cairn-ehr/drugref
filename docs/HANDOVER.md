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

**Branch `claude/fda-cyp-split` off `main`; PR OPEN for the second module of #177 (#177 stays open). No
migration — THE SCHEMA STILL ENDS AT `db/056`.** PR #189 (the questions.py split, #151) is MERGED. The suite
total lives in PROJECT-NOTES § "How to run / test" and **nowhere else**; `uv run pytest` fails if the line and
the collected suite disagree.

**⇒ JUST FINISHED — THE fda_cyp.py SPLIT** (full account: PROJECT-NOTES § "The fda_cyp.py split").

**⇒ WHAT SHIPPED.** `ingest/fda_cyp.py` 707 → 395, off #177's ledger. Its CELL grammar (`ROLE_COLUMNS`, the
marker grammar, the closed pathway vocabulary, `parse_cell` + helpers) moved **verbatim** (checked by sorted-line
diff) into `ingest/fda_cyp_cell.py` (348). The PAGE half (table, headings, `CypTuple`, `parse_table`,
`parse_release`, `parse_footnotes`) stayed. **`FdaCypParseError` now lives in `fda_cyp_cell`** to avoid an
import cycle. `fda_cyp` imports it, so `fda_cyp.FdaCypParseError` is unchanged for every caller. One new test
pins that both modules use the same class.

**⇒ VERIFIED.** `uv run pytest` with the DB: 2753 passed; `ruff check .` clean.

## ⇒ DO THIS NEXT

**Choose one; none is blocked.**

1. **[#177](https://github.com/cairn-ehr/drugref/issues/177) — four modules over rule 4's cap**:
   `ingest/fda_cyp_run.py` 622, `signing.py` 605, `curation.py` 555, `release_verification.py` 540. Each is a
   small self-contained round, and six splits have used the same rule: find the SEAM (a read path inside a
   writer, or a second vocabulary), move it verbatim, get the suite green, *then* change behaviour. The ledger
   ratchets, so none of them may grow. **Trap:** a module that leaves the ledger also loses its ledger test
   case, and each new file adds a `test_tools_server_channel` sweep case, so update the count line.
2. **The SPL follow-ups left on their own triggers**: #164, #169, #170 (see the ledger). #170 has no trigger,
   only cost.
3. **`5c.5` pregnancy & lactation is spiked-not-designed**: LactMed puts 1,679 moieties outside MED-RT's thin
   lactation floor. It is gated on a **clinician review that has not happened** (23-row worklist).
4. **The class half of 5c.3** (#155, #102, word order). Deferring it **RAISED** drug × drug yield by 193 pairs.

## Parallel project sequencing

DrugCentral is done: a **candidate-tier floor pinned to the 2023 release** — no refresh, no auto-alert. **SPL is a
fourth candidate source and DELIBERATELY NOT an arm of `exact_ddi_pair`**: it means *a label's interactions section
names both drugs*, not *an authority asserts they interact*. FDA toxicity is cleared; class-grain content (#98) gates
#112/#105.

## Open follow-ups

The full ledger lives once in [PROJECT-NOTES § "The standing open-issue ledger"](PROJECT-NOTES.md). **#163, #165,
#166, #168, #171 are CLOSED (PR #185); #151 is CLOSED (PR #189).** Open: **#177** (four over the cap), **#186**,
**#187**, **#188** (PR #185's review), **#164**, **#169**, **#170**. Standing: **#155** and **#102 re-opened**, both inherited by the deferred class
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
