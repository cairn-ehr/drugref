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

**Branch `claude/spl-copy-cost`, from `main` at `1272d02`**; **this round is open as
[PR #173](https://github.com/cairn-ehr/drugref/pull/173) and is not merged.** Migrations through **`db/052`** —
this round added **NO migration**. The suite total lives in PROJECT-NOTES § "How to run / test" and **nowhere
else** ([#146](https://github.com/cairn-ehr/drugref/issues/146)); read it there at the START of the session.

**⇒ JUST FINISHED — THE COPY-COST ROUND, CLOSING [#160](https://github.com/cairn-ehr/drugref/issues/160).**
Full account: PROJECT-NOTES § "The COPY-cost round" ·
[measurement record](superpowers/specs/2026-09-01-drugref-spl-copy-fk-plan.md). **The whole SPL ingest went
from 12 min 51 s to 2 min 09 s**, with every published count, both checksums and all five routes identical.

**⇒ THE RE-VERIFICATION THE LAST ROUND OWED IS DONE AND CLEAN.** The census round's end-to-end run predated its
own review's fixes to the reader. Re-run on `drugref_spl160`: **every figure reproduced exactly**.

**⇒ AND THAT RUN CARRIED #160'S CONTROL, WHICH NOBODY HAD LOOKED FOR.** 73,867 subject rows took **630 s**
while **1,297,944** occurrence rows took **35 s** — same transaction, same writer, same client. *17.6× more
rows in 18× less time.* Two of the issue's three candidate causes die on that one line: not row volume, not
`COPY`. **All three turned out wrong**, and no amount of ablating them would have reached the fourth.

**⇒ THE CAUSE CAME FROM A STACK SAMPLE, NOT THE HYPOTHESIS LIST.** `sample <backend> 8`: **6,748 of 6,748
samples** inside `RI_FKey_check_ins`. A foreign-key check is a QUERY, and the planner may use any parent index
whose leading columns its equality quals cover. On a freshly `COPY`d parent (`relpages = 0`) the primary key
and `spl_label_by_wording` **cost an identical 8.44**, and the tie landed on the loose one — matching all
68,550 parent rows, once per child row. Two `ANALYZE`s costing **112 ms** bought **365×** (493,539 ms → 1,352
ms, one variable, full scale).

**⇒ THE RULE, AND IT IS NOT "ANALYZE AT THE END": analyse a bulk-loaded table BEFORE loading anything that
references it.** The RI plan is cached at first use, *inside* the load, so the existing `analyze_source_tables`
at the end of the run cannot repair it. Censused: of all **138** foreign keys in the schema, **exactly one**
parent offers a loose plan, and it is this one — pinned by a test, as is the cause; **both mutants were run and
killed.**

**⇒ AND THE REFUTATION THAT CLOSED THE RIGHT DOOR FOR A ROUND.** `analyze_source_tables`'s docstring ruled the
foreign key out *"because PostgreSQL's RI triggers use a plan pinned to the parent's primary key"*. The 175 ms
measurement was real; **the reason was invented**, and it is the half that got quoted forward. Pinned is not
pinned *to the primary key*. ⇒ *A refutation is a measurement plus an explanation, and only the explanation is
load-bearing once it is quoted forward.* ⇒ *Where a cost sits in one statement, SAMPLE THE PROCESS before
designing an experiment about it.*

**⇒ THE INGEST'S DURATION HAD FIVE HOMES AND THIS ROUND MADE FOUR OF THEM FALSE** — two docstrings, ROADMAP
twice, PROJECT-NOTES twice. All corrected. Two of them were **already** wrong: they described the *scan* (~50 s)
using the *whole ingest's* figure.

## ⇒ DO THIS NEXT

**Choose one; none is blocked.**

1. **[#159](https://github.com/cairn-ehr/drugref/issues/159) — `finished_at − started_at` is not a duration for
   ANY feed.** Now the most valuable of the small ones: this round cut the real SPL runtime by 6× and the
   column still reports 49.9 s for it, so the one number an operator would size a rebuild from is wrong in a
   new way. One line to change and a decision about a column already on disk for nine feeds.
2. **#163–#166 and #168–#171**, the two review rounds' deferrals. **#168 is three more homes of one vocabulary
   in `tools/`** — the same defect class this slice has now found five times.
3. **[#172](https://github.com/cairn-ehr/drugref/issues/172) — `spl_evidence.py` is at 494/500** after this
   round. `cli.py` at exactly 500 is #130; this is the same shape one edit earlier, and the census round's
   `spl_release.py` split is the worked precedent (verbatim move first, suite green, *then* behaviour).
4. **`5c.5` pregnancy & lactation is still spiked-not-designed** — LactMed puts 1,679 moieties outside MED-RT's
   thin lactation floor, gated on a **clinician review that has not happened** (23-row worklist in the spike).
5. **The class half of 5c.3**, which is where every unsolved problem lives (#155, #102, the word-order gap).
   **Deferring the class half RAISED the drug × drug yield by 193 pairs**, so a round re-adding classes must
   expect it to FALL and must not read that as a regression.

## Parallel project sequencing

DrugCentral is done and is a **candidate-tier floor pinned to the 2023 release** — it does not refresh, and
nothing in that tier may auto-alert. **SPL is a fourth candidate source and DELIBERATELY NOT an arm of
`exact_ddi_pair`**: it means *a label's interactions section names both drugs*, not *an authority asserts they
interact*. FDA toxicity remains cleared and unscheduled; class-grain content (#98) still gates #112/#105.

## Open follow-ups

The full ledger lives once in [PROJECT-NOTES § "The standing open-issue ledger"](PROJECT-NOTES.md).
**#160 is CLOSED by this round; #162 was closed by the last.** New from this round: **#172**
(`spl_evidence.py` at 494/500). Still open from the two review rounds: **#163–#166** and **#168–#171**, plus
**#159** (the last of the two performance findings). Still standing: **#155** (MED-RT's PK axis is not a
drug-class vocabulary) and **#102 re-opened** (the band is pair-scoped), both inherited by the deferred class
half; **#67** (salt↔base equivalence) is wanted by **three** sources and blocks a grain; **#158** (route 3's
calibration set) is untouched. Also: #148, #149, #151, #152, #153, #146, #128/#129 and #132–#135 (FDA-CYP
residue), #124, #121/#123, #104, #94. Before production: re-run every parser on current releases, resolve #17,
and the three rule-6 deeds (#6, #25, GSRS).

## Current DSN

- Test-only DSN: `host=localhost port=5532 dbname=drugref_test user=postgres`. Set `DRUGREF_TEST_DSN` for DB
  tests; never use it for reviewer accounts or GUI service data — pytest recreates it, and see #153 before
  running two sessions against it at once.
- **`drugref_spl160fix` is THIS round's verification database and the one to measure against**: `TEMPLATE
  drugref_spl` → `migrate` → `ingest spl`, **2 min 09 s**, reproducing every published figure. Full command:
  the round's measurement record §1. **`drugref_spl160` is its BEFORE control** — the same build with the same
  code minus the two `ANALYZE`s, 12 min 51 s; keep both, they are the only pair that shows the 630 s.
  **`drugref_spl162`** (census round) and **`drugref_spl051`** (ingest round) are still on disk.
  **Never patch a verification database — rebuild it under a new name.**
- **`drugref_spl`** is the pre-`db/051` database every SPL round templates from.
  **`drugref_dc049`**/**`drugref_dc101`** are DrugCentral's; `dc049` predates `db/050`.
- Corpora on disk: `downloads/OPENFDA/` (14 partitions, `export_date` 2026-08-22) and `downloads/DAILYMED/`
  (6 Human Rx parts, `last-modified` 2026-08-21, 17.6 GB). **`downloads/` is gitignored, so every SHA-256 is
  recorded in the mining measurement record §2** — re-fetch and verify against that table, not against a
  manifest file that disappears with the bytes it describes. The combined `source_checksum` over all twenty
  files is `5d6a894b30ce…`, recorded identically by all three runs above.
- The probe cache (`sections.jsonl`) is **scratch and gone**; `tools/spl_recovery_probe.py` rebuilds it in
  minutes. The verification-database map and migration state live once in PROJECT-NOTES § "How to run / test".
