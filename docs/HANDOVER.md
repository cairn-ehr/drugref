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

**Branch `claude/spl-ddi-design`, from `main` at `c601e39`** (PR #156 merged 2026-08-24). Migrations through
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
56.0%** — 15,345 of 27,406 — and the published 20,554 pairs came from **just 12,061 wordings**. And the orphan
half is not inferior material: **97.2%** of it names a known moiety against the keyed half's 97.8%, at
**higher** density (49.3 occurrences per wording against 44.0).

**⇒ RECOVERY SHIPS, AND IT IS BIGGER THAN DRUGCENTRAL'S WHOLE SLICE.** DailyMed's `activeIngredient` block
adds **11,064 pairs (+53.8%), 10,162 novel (91.8%)** — a *higher* novelty rate than the baseline it extends,
against the 7,501 at 91% that justified DrugCentral. Of 26,401 labels targeted, **6,539 are in DailyMed
(24.8%)** and **6,514 of those resolve (99.6%)**. The limit is the release, not the reading: DailyMed
publishes current in-use Human Rx only. ⇒ **Total: at least 31,618 pairs, 28,269 (89.4%) novel.**

**⇒ A THIRD ROUTE WAS FOUND AND REJECTED, AND ITS CALIBRATION SET IS THE KEEPER.** `openfda` is present on
100% of unkeyed records and is simply EMPTY, but `spl_product_data_elements` is populated on 99.5% of them —
one flattened uppercase string of product name, active ingredients, moieties **and excipients, undelimited**.
Measured against route 2 as ground truth: the true moiety is among the names **98.9%** of the time, but the
field averages **7.69** registry matches per label, and rank 0 is **genuinely wrong 6.2%** of the time (47.8%
before salt spellings are split out of the error — only one of those numbers supports a decision). It would
add +4,962 pairs. **The 6,317-label overlap is a permanent calibration set** for any future heuristic route.

**⇒ A PER-OCCURRENCE QUOTED WINDOW IS NOT A QUOTE — IT IS THE SECTION, REASSEMBLED.** At ~48 moiety
occurrences per wording over a mean 3,663-character section, a sentence window stores **80.4%** and ±120 chars
**89.6%**. The shipped rule is per-WORDING: ±60 chars around the FIRST occurrence of each distinct moiety, in
pair-priority order, until **25%** of the section's characters are spent — **14.7% stored on average**.

**⇒ AND THE ROUND'S OWN TALLY WAS WRONG BY 44 WHILE ITS 18 TESTS PASSED.** It counted the scan's ROWS, and
DailyMed ships successive versions of one label sharing a `set_id`. **What caught it was cross-checking the
total against an independent pass** — *a tally that only ever agrees with itself is not checked.* Now pinned
by `test_one_set_id_read_TWICE_is_one_label`.

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
LactMed alone puts 1,679 moieties outside MED-RT's thin lactation floor, and it is gated on a **clinician
review that has not happened** (a 23-row worklist ships with the spike results).

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
