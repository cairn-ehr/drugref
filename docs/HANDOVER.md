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
and closes #174 and #172. NO MIGRATION** — the schema still ends at **`db/053`**, so every database is valid.
The suite total lives in PROJECT-NOTES § "How to run / test" and **nowhere else**
([#146](https://github.com/cairn-ehr/drugref/issues/146)); read it there at the START of the session.

**⇒ JUST FINISHED — THE NOTICE-CHANNEL ROUND, CLOSING
[#174](https://github.com/cairn-ehr/drugref/issues/174) AND
[#172](https://github.com/cairn-ehr/drugref/issues/172).** Full account: PROJECT-NOTES § "The notice-channel
round" · [record](superpowers/specs/2026-09-02-drugref-analyze-notice-channel.md).

**⇒ POSTGRESQL SKIPS A TABLE IT WILL NOT ANALYSE, SAYS SO IN A *WARNING*, AND RETURNS SUCCESS.** `ANALYZE` on a
table the calling role does not own emits `permission denied to analyze "t", skipping it`, leaves
`reltuples = -1`, and hands back the `ANALYZE` command tag. **psycopg discards notices unless a handler is
installed, and `grep -rn add_notice_handler src/ tests/` returned nothing.** So under an ordinary
admin-migrates/app-ingests split every `ANALYZE` the COPY-cost round added did nothing, #160's **630 s** came
back, and the run **still reported success** — `reconcile`, `read_pairs` and `check_floors` count rows, and the
row counts are identical either way.

**⇒ THE PROJECT HAD ALREADY WRITTEN THE MECHANISM DOWN — AS A COMMENT, IN ONE MODULE.** `drugcentral_run.py`
has said since db/050 that "psycopg discards notices unless a handler is installed, so the ingest reported
success having silently lost its atomicity", and one round later the same discard cost the SPL ingest its whole
performance fix. ⇒ *A COMMENT IN ONE MODULE IS NOT A CHANNEL: a correct diagnosis with no mechanism attached
does not protect the next round.*

**⇒ WHAT SHIPPED.** `server_messages.py` (all eight protocol severities mapped **once**, reading the
NON-LOCALISED severity — `Diagnostic.severity` is translated, so `WARNUNG` is a real shape; unknown maps to
WARNING, never quieter) · `db.connect` installs it · `analyze.py` runs the statement inside a scoped collector
and **refuses** unless the server did the work. NOTICE sits at INFO because it was **measured**: a full fresh
migrate emits **35** notices, and a healthy ingest emits **none**.

**⇒ TWO CHECKS, AND THEY ARE NOT ONE CHECK TWICE** — each kills a mutant the other cannot see. The collected
**WARNING** is the only one that fires on a **re-ingest**; **`reltuples = -1`** is the only one that fires when
no message arrives at all — the state every connection here was in until this round. `0` is not a milder `-1`:
a table analysed while empty has statistics.

**⇒ THE COLLECTOR INSTALLS ITS OWN HANDLER RATHER THAN READING `db.connect`'s.** A guard depending on how the
connection was opened would fire on the CLI path and nowhere else — not in this suite, whose `conn` fixture
calls `psycopg.connect` directly. That is 74/66/76's *gate that never fires*, inside the fix for 174. There is a
negative control asserting a bare `psycopg.connect` hears nothing. And **a notice handler can never enforce**:
psycopg swallows whatever one raises.

**⇒ VERIFIED AT FULL SCALE ON BOTH SIDES OF THE ROLE SPLIT** (`drugref_notice174`, from nothing). Owner: chain
**132.91 s**, `ingest spl` **131.77 s**, every published figure reproduced. Split role without `MAINTAIN`:
**REFUSED at 81.76 s, exit 2**, projection untouched, run left `finished_at IS NULL` — and **it was a
RE-INGEST**, `spl_wording.reltuples` at 27,406, so only the WARNING could have fired. **The remedy the message
names was then applied and re-measured**: `GRANT MAINTAIN` → the identical command completes (150.02 s).

**⇒ #172 CLOSED AT THE SEAM IT NAMED, AND THE CAP BECAME A SWEEP.** `Registry`/`load_registry` — a READ path
inside the SOLE WRITER — moved to `registry_read.py`; `spl_evidence.py` **518 → 428**. `500` had lived in two
test files guarding three modules of forty-odd, which is how 518 happened green; `test_module_size_cap.py` now
owns it once and sweeps `src/drugref`, the seven already-over modules a **checked ledger** — **[#177](https://github.com/cairn-ehr/drugref/issues/177)**.

**⇒ AND THE SUITE COUNT WAS STALE AGAIN AT SESSION START — THE NINTH OCCURRENCE, IN THE SAME PLACE AS THE
SEVENTH.** PR #175 wrote "Suite 2467 -> 2475" into its **commit message** and left PROJECT-NOTES at 2467, on the
very branch whose diff added the sentence *"a commit message is not a home"*. Corrected before work began.
⇒ **[#146](https://github.com/cairn-ehr/drugref/issues/146) IS NOW THE ONE TO WRITE.**

## ⇒ DO THIS NEXT

**Choose one; none is blocked.**

1. **[#146](https://github.com/cairn-ehr/drugref/issues/146) — a test that reads the suite count out of
   PROJECT-NOTES and compares it to the collected total.** Nine occurrences of one failure mode against a
   comment rewritten three times to prevent it, twice now via a commit message. It is small, and it is the only
   thing that makes the prose unnecessary. Do it first unless something is on fire.
2. **[#176](https://github.com/cairn-ehr/drugref/issues/176)** — the runtime watershed dates rows by **time**,
   not **writer**, so an older client on a migrated database publishes a confident `0.0s` for a two-second run.
   A boolean set by `open_run` replaces it. Then **#163–#166**, **#168–#171** (#168 is three more homes of one
   vocabulary in `tools/`, the class this slice has now found five times).
3. **[#177](https://github.com/cairn-ehr/drugref/issues/177) — seven modules over rule 4's cap**, from
   `questions.py` at **797** down to `ingest/spl_match.py` at **524**. Each is a small self-contained round;
   the precedent is now three deep (`spl_release.py`, `cli_status.py`, `registry_read.py`) and the rule is
   always the same — find the SEAM (a read path inside a writer, a second vocabulary in one module), verbatim
   move first, suite green, *then* behaviour. `questions.py` is the largest and probably the clearest.
4. **`5c.5` pregnancy & lactation is spiked-not-designed** — LactMed puts 1,679 moieties outside MED-RT's thin
   lactation floor, gated on a **clinician review that has not happened** (23-row worklist in the spike).
5. **The class half of 5c.3**, where every unsolved problem lives (#155, #102, the word-order gap). **Deferring
   it RAISED the drug × drug yield by 193 pairs** — a round re-adding classes must expect a FALL.

## Parallel project sequencing

DrugCentral is done: a **candidate-tier floor pinned to the 2023 release** — it does not refresh, and nothing
in that tier may auto-alert. **SPL is a fourth candidate source and DELIBERATELY NOT an arm of
`exact_ddi_pair`** — it means *a label's interactions section names both drugs*, not *an authority asserts they
interact*. FDA toxicity is cleared; class-grain content (#98) gates #112/#105.

## Open follow-ups

The full ledger lives once in [PROJECT-NOTES § "The standing open-issue ledger"](PROJECT-NOTES.md).
**#174 and #172 are CLOSED by this round; #159 and #160 by the two before.** Open: **#177** (seven modules over
the cap), **#176** (the watershed dates rows by time, not writer), **#163–#166**, **#168–#171**. Standing:
**#155** and **#102 re-opened**, both inherited by the deferred class half; **#67** (salt↔base) is wanted by
three sources and blocks a grain; **#158** untouched. Also #148, #149, #151, #152, #153, #146, #128/#129,
#132–#135, #124, #121/#123, #104, #94. Before production: re-run every parser on current releases, resolve #17,
and the three rule-6 deeds (#6, #25, GSRS).

## Current DSN

- Test-only DSN: `host=localhost port=5532 dbname=drugref_test user=postgres`. Set `DRUGREF_TEST_DSN` for DB
  tests; never for reviewer accounts or GUI data — pytest recreates it. **See #153 before running two sessions
  against it at once: concurrent runs drop the schema under each other and the failures look like real ones.**
- **`drugref_notice174` is THIS round's** — built from nothing, chain + SPL as owner, then the same SPL ingest
  under role `drugref_app` (no ownership) twice, the second with `MAINTAIN` granted. **It still carries
  `drugref_app` and its GRANTs**, so it is the one database where the role split can be re-run (record §1);
  **`options='-c role=…'` in a DSN runs an ingest AS another role** with no authentication story.
  `drugref_dur159`/`drugref_dur159mixed` are #159's, `drugref_spl160fix` (2 min 09 s) + `drugref_spl160`
  (12 min 51 s) show #160's 630 s, `drugref_spl051` holds #159's evaporated 49.85 s, `drugref_spl` is the
  pre-`db/051` base, `drugref_dc049`/`drugref_dc101` DrugCentral's.
  **Never patch one — rebuild under a new name**; editing an APPLIED migration breaks every database carrying
  it (`apply_migrations` is checksum-immutable).
- Corpora: `downloads/OPENFDA/` (14 partitions, 2026-08-22), `downloads/DAILYMED/` (6 Human Rx parts,
  2026-08-21, 17.6 GB). **`downloads/` is gitignored, so every SHA-256 is in the mining record §2** — verify
  against that table, not a manifest that vanishes with the bytes; `source_checksum` `5d6a894b30ce…` is
  identical across all five SPL runs, and `tools/spl_recovery_probe.py` rebuilds the scratch `sections.jsonl`.
