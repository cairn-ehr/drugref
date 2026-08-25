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

**Branch `claude/spl-ddi-design`, from `main` at `c601e39`** (PR #156 merged 2026-08-24); **this round is
open as [PR #157](https://github.com/cairn-ehr/drugref/pull/157) and is not merged.** Migrations through
**`db/050`** — **this round added none.** The suite total lives in PROJECT-NOTES § "How to run / test" and
**nowhere else** ([#146](https://github.com/cairn-ehr/drugref/issues/146)); read it there at the START of the
session.

**⇒ JUST FINISHED — the slice 5c.3 DESIGN round. The spec exists; no migration, no ingest, no schema.**
[Design spec](superpowers/specs/2026-08-24-drugref-slice-5c3-spl-ddi-ingest-design.md) ·
[subject-recovery measurement](superpowers/specs/2026-08-24-drugref-slice-5c3-subject-recovery-measurement.md).
Full account and **every figure**: PROJECT-NOTES § "The 5c.3 subject-recovery round and the design spec". Do
not re-derive them from here.

**⇒ FOUR OWNER DECISIONS SCOPE THE SLICE. The implementation round must not re-open them without a reason.**

1. **[#154](https://github.com/cairn-ehr/drugref/issues/154) is ANSWERED — bundle a QUOTED WINDOW only**,
   neither reference-only nor the full prose.
2. **Drug × drug only.** The class half is deferred to its own slice.
3. **Structural subject routes only.** The rank-0 name heuristic does not ship.
4. **The quote budget is proportional** — 25% of the section's characters.

**⇒ THE COUNTERWEIGHT WAS QUOTED IN THE WRONG UNIT, AND IT WAS UNDERSTATED.** The mining round published the
loss as **41,056 labels (60%)**. Split properly it cuts both ways: **14,455 of those labels are REDUNDANT**
(another manufacturer reprinting a wording a keyed label already carries), but **in WORDINGS the loss is
56.0%** — 15,345 of 27,406 — and the published 20,554 pairs came from **just 11,939 wordings**. And the orphan
half is not inferior material: **97.2%** of it names a known moiety against the keyed half's 97.8%, at
**higher** density (49.3 occurrences per wording against 44.0).

**⇒ RECOVERY SHIPS, AND IT IS STILL BIGGER THAN DRUGCENTRAL'S WHOLE SLICE.** DailyMed's `activeIngredient`
block adds **8,704 pairs (+42.3%), 7,853 novel (90.2%)** — a *higher* novelty rate than the baseline it
extends, against the 7,501 at 91% that justified DrugCentral. Of 26,401 labels targeted, **6,539 are in
DailyMed (24.8%)**, **6,514 resolve (99.6%)** and **25 carry a UNII drugref lacks**. The limit is the
release, not the reading — now measured, not inferred: all four of the scan's drop counters are zero.
⇒ **Total: at least 29,258 pairs, 25,960 (88.7%) novel.**

**⇒ A THIRD ROUTE WAS FOUND AND REJECTED, AND ITS CALIBRATION SET IS THE KEEPER.** `openfda` is present on
100% of unkeyed records and is simply EMPTY, but `spl_product_data_elements` is populated on 99.5% — one
flattened uppercase string of product name, active ingredients, moieties **and excipients, undelimited**.
Against route 2 as ground truth the true moiety is among the names **98.9%** of the time, but the field
averages **7.69** matches per label and rank 0 is **genuinely wrong 6.2%** (47.8% before salt spellings are
split out — only one supports a decision). **The 6,317-label overlap is a permanent calibration set.** §4's
producing code was never committed, so it is owed as
[#158](https://github.com/cairn-ehr/drugref/issues/158); route 3's pair yield is withdrawn until then.

**⇒ A PER-OCCURRENCE QUOTED WINDOW IS NOT A QUOTE — IT IS THE SECTION, REASSEMBLED** (sentence **82.7%**,
±120 chars **89.0%**). The shipped rule stores **20.4%** — *not* the 14.7% first published, whose code was
never committed. Now reproducible: `tools/spl_quote_budget.py` + `probe quotes`, because a schema CHECK
rests on it. Full table: measurement record §6.

**⇒ THE ROUND GOT ITS OWN ARITHMETIC WRONG THREE TIMES AND ITS TESTS PASSED EVERY TIME.** (1) A 44-label
over-count: it tallied the scan's ROWS, and DailyMed ships successive versions of one label sharing a
`set_id`. (2) The delta's two arms used **different subject rules** — the recovered arm blended the salt UNII
in, and drugref registers a salt as its own moiety, so a salt product paired twice (56.7% of resolvable
DailyMed labels): that alone published **31,618** where the rule gives **29,258**. (3) "Wordings with a
subject" meant *any UNII* in one table and *resolves* in the next. **None was visible in any output; each was
found by re-deriving the published arithmetic from the other direction**, in the PR review, not by the tests.
⇒ *A figure that only ever agrees with itself is not checked, and a delta is only a delta while both arms
share one function.* Now: `subject_uniis` is the sole subject rule, `form_candidate_pairs` the sole pair rule
(`spl_ddi_report` calls it too), `labels_missing_from_dailymed` is counted not subtracted, **51 tests**.

## ⇒ DO THIS NEXT

**Write `db/051` and the SPL ingest**, from the design spec. It is fully specified — five tables, two views,
one gap view, the parser/orchestrator split, and the source-admission **trio** (`ingest_run` source CHECK,
writer CHECK, `ids.py` + `provenance.py`) whose failure mode is silent. Three things the spec insists on:

- **The quote budget is a CONSTRAINT** (a deferred trigger over `sum(char_end - char_start)` per wording),
  and its test must be shown it can FAIL — `db/050`'s finding was that every guard in a slice passed vacuously.
- **The ingest must scan the 14,455 redundant unkeyed labels the probe skipped.** A label's SUBJECT is its own
  even when its wording is shared, so their pairs are uncounted. ⇒ **Every pair figure is a FLOOR; the floor
  check asserts `>=`, not `==`.**
- **The matcher must be the SHIPPED resolver's rule** — exact, case-insensitive, contiguous,
  longest-match-wins. The measured yield rests on it.

**If 5c.3's implementation is not the choice, `5c.5` pregnancy & lactation is still spiked-not-designed** —
LactMed puts 1,679 moieties outside MED-RT's thin lactation floor, gated on a **clinician review that has not
happened** (a 23-row worklist ships with the spike results).

## Parallel project sequencing

DrugCentral is done and is a **candidate-tier floor pinned to the 2023 release** — it does not refresh, and
nothing in that tier may auto-alert. FDA toxicity remains cleared and unscheduled; class-grain content (#98)
still gates #112/#105.

## Open follow-ups

The full ledger lives once in [PROJECT-NOTES § "The standing open-issue ledger"](PROJECT-NOTES.md).
**[#154](https://github.com/cairn-ehr/drugref/issues/154) is now ANSWERED and closed** by the owner's quoted-
window determination. Still standing: **#155** (MED-RT's PK axis is not a drug-class vocabulary) and
**#102 re-opened in new terms** (the band is pair-scoped), both of which the deferred class half inherits;
**#67** (salt↔base equivalence) is now wanted by **three** sources and is the one blocking a grain, not a
nicety. Also: #148, #149, #151, #152, #153, #146, #128/#129 and #132–#135 (FDA-CYP residue), #124, #121/#123,
#104, #94. Before production: re-run every parser on current releases, resolve #17, and the three rule-6
deeds (#6, #25, GSRS).

## Current DSN

- Test-only DSN: `host=localhost port=5532 dbname=drugref_test user=postgres`. Set `DRUGREF_TEST_DSN` for DB
  tests; never use it for reviewer accounts or GUI service data — pytest recreates it, and see #153 before
  running two sessions against it at once.
- **`drugref_spl`** is the measurement database for both 5c.3 rounds and is the one to reuse: it is the
  **only** database holding every vocabulary at once (`TEMPLATE drugref_dc049` → `migrate` to `050` →
  `ingest fda-cyp` → `ingest onchigh`). Rebuild command: mining measurement record §2.
- **`drugref_dc049`** and **`drugref_dc101`** are the DrugCentral round's databases; `dc049` still predates
  `db/050`, so migrate it before re-measuring anything against it.
- Corpora on disk: `downloads/OPENFDA/` (14 partitions, `export_date` 2026-08-22) and `downloads/DAILYMED/`
  (6 Human Rx parts, `last-modified` 2026-08-21, 17.6 GB). **`downloads/` is gitignored, so every SHA-256 is
  recorded in the mining measurement record §2** — re-fetch and verify against that table, not against a
  manifest file that disappears with the bytes it describes.
- The probe cache (`sections.jsonl`, `texts.jsonl`, `recovered.jsonl`, `elements.jsonl`) is **scratch and is
  gone**; `tools/spl_recovery_probe.py` rebuilds it in minutes, and its `extract` stage reproduced the mining
  round's census exactly.
- The verification database and its migration state live once in PROJECT-NOTES § "How to run / test"; do not
  copy that volatile map here.
