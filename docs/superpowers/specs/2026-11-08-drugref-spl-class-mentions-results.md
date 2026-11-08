# SPL class mentions -- measurement record (Task 9A census, Task 9B sample and gates)

Date of run: 2026-11-08. Design: [spl class mentions](2026-11-08-drugref-spl-class-mentions-design.md) §7.
Rule 6: this record carries counts and class names only -- no label prose.
Sections "Precision sample" and "Gates" are Task 9B's.

## Provenance

| item | value |
|---|---|
| git head at build | `d165f39` plus the Task 9A commits (tool, scope fixes R16/R17); run 12 at `6e7b749` (R19) |
| database | `drugref_classmentions`, built from nothing (createdb, then migrate through db/066) |
| SPL run | **ingest_run 15** (what ships: R19 hygiene + two advice-tuning iterations + the withheld levels; per-source rebuilds on the same DB, db/066 untouched). Run 12 = R19 rebuild, run 13 = tuning iteration 1, run 14 = iteration 2. Run 11 is the R16/R17 rebuild the class hygiene sample was drawn on; run 10 the superseded first full run; run 9 aborted, left as an unfinished row |
| SPL release tag | `openfda-2026-08-22+dailymed-2026-08-21` |
| other releases | UNII 26Feb2026, MED-RT 2026.07.06, MeSH 2026 (+ mesh-relations 2026.07.06), GSRS 2026-02-26, ONCHIGH 2026-08-12, FDA-CYP 2026-05-29, DrugCentral 11012023 |
| checksums | all 20 files (14 openFDA partitions, 6 DailyMed parts) `shasum -a 256`-verified equal to the table in the [mining record §2](2026-08-24-drugref-slice-5c3-spl-mining-measurement.md); no mismatch |
| corpus | 262,032 openFDA records, 68,550 labels carrying the section, 27,406 distinct wordings, 54,813 DailyMed documents read |

Wall times (this machine, single run each, no controls): migrate 1 s -- ingest chain 159 s --
onchigh 4 s -- fda-cyp 5 s -- drugcentral 20 s -- `ingest spl` 274 s (the rebuild, run 11; first run 228 s, the aborted attempt died after 93 s; runs 12-15 about 245 s each) -- census 48-50 s with `--openfda`.

Reproduce: `uv run python -m tools.spl_class_census --dsn "$DSN" --openfda downloads/OPENFDA`
(the tables below are its output, verbatim, under added commentary).

## First run, and what changed (R16, R17, unit fix)

The first full run (run 10) had three faults, found in review, all fixed before this record's
figures were taken (run 11, same corpus, same chain; db/066 untouched):

1. **Scope fix was incomplete (R17).** The matcher tokenises on letters/digits and ignores
   punctuation, so a class name matched ACROSS a sentence end ("... Potassium. Salts ..." ->
   "Potassium Salts"), a false mention whose scope could end inside the span. The class pass
   now drops any class match whose span holds a sentence boundary (the splitter's own rule,
   `spl_advice.span_crosses_sentence_boundary`) and counts the drops; `read_advice` also
   widens its scope to contain the span on every path.
   **Sentence-splitter failure signal (design §7): 64 class spans dropped at a sentence boundary.**
2. **Class hygiene (R16).** (a) MED-RT APC is no longer matched: all 44 classes empty by
   construction, generic words ("Enzyme" 15,587 rows, "Hormone" 4,486), each duplicating a
   MeSH name. (b) 12 class match keys equal to a registry moiety display name are dropped
   (the moiety pass owns those mentions): Biguanide, Bismuth, Calcitonin, Calcium, Human C1
   Esterase Inhibitor, Interferon alfa-2b, Interferon beta, Interferon gamma, Parathyroid
   Hormone, Phenothiazine, Progesterone, Psoralen.
3. **Unit error in the first record.** The first run's "391,521 class occurrences" and its
   per-level shares counted (span, class) rows, not mentions. A mention is a distinct span;
   a span several classes fold onto has several rows. The tool now reports spans and
   ambiguous spans, and level/rule/scope distributions per SPAN.

First-run figures (superseded): 391,521 rows / 285,059 spans; 20,265 moiety spans inside a
class span; 16,792 statements.

## Run 12: the R19 class-hygiene rebuild

The class hygiene sample below (drawn on run 11) led to controller ruling R19, implemented
test-first in the class pass only and re-ingested as run 12 (same corpus, same chain):
P1 drops class keys equal to a UNII official name (2 keys: `Acetaminophen`, `Nicotinic
Acid`); P2 drops class matches on a non-drug head word (**14,098 (span, class) rows**, the
ingest summary's "class spans dropped on a non-drug head word" -- like the boundary count,
that figure is rows); P3 excludes MeSH PA `Hormones`. `drugref ingest spl` now requires
`--unii-names` (UNII_Names_*.txt). Run 11 -> run 12: 249,444 -> 227,990 spans (-21,454),
330,597 -> 304,682 rows, 14,671 -> 14,135 statements.

## Runs 13-15: advice tuning and the gates (Task 9B phase 2)

The advice hand sample (below) failed the gates on run 12; two tuning iterations followed, each
test-first and re-ingested (run 13: `4d4819d`, run 14: `9172f13`), then the two (level, scope)
pairs still under the gate were withheld (run 15: `5b133f2`, `spl_advice.WITHHELD`). Class
mentions are unchanged from run 12 (227,990 spans); only readings moved.

## Headline (run 15 -- what ships)

* Drug x drug pairs **29,952 / 26,598 novel -- identical** on every run 11-15 (floors 29,258 /
  25,960 cleared).
* **227,990 mentions (distinct spans)**, as 304,682 (span, class) rows, over 23,440 of 27,406
  wordings (85.5%). 60,464 spans (26.5%) are ambiguous (fold onto >1 class).
* Statements (labelling drug x class): 14,135 over 469 classes (sum of the per-axis table).
* Advice per SPAN: **77.3% not_read**; 51,702 strength spans -- contraindicated 5,374 (930 of
  them heading/row block), adjust_dose 25,520 (9,696 block), avoid 8,761 (all block), monitor
  12,047 (all block). **Sentence-scope avoid and monitor are withheld** (Gates). 37.8% of
  strength spans are mixed (several strength levels in the scope).
* **Zero spans read `no_meaningful_interaction`** on every run: that level ships empty and its
  gate is vacuously met.
* Quote windows cover 26.7% of spans (60,846 of 227,990).
* 10,645 moiety occurrences lie inside a class span (6,119 wordings).
* 283 wordings (783 labels) CONTAIN the long-form enzyme spelling near a role word (presence,
  not a measured miss).
* 26,410 of 304,682 rows (8.7%) are within 80 chars of "Intervention" / "Clinical Impact".
* Sentence-splitter failure signal: 64 class rows dropped at a sentence boundary.

## Still for the reader

* `Acetaminophen [EPC]` -- 6,588 run-11 spans, a drug name -- is gone (R19/P1). The registry
  holds no "acetaminophen" name, so the MOIETY pass misses those mentions too: a drug x drug
  coverage gap of the shipped ingest, outside this round (§4.4).
* The two axes still duplicate each other where MeSH PA and MED-RT PE/TC/MoA/EPC share a name
  (Diuretics / Antacids / Analgesics / Monoamine Oxidase Inhibitors): ambiguity is real
  (26.0% of spans), and statements count each class separately.
* MED-RT PE: 1,291 classes with no direct member, 1 ever mentioned.

## Census tables (verbatim tool output of run 15)

### Totals: (span, class) rows vs distinct spans (mentions)

| span_class_rows | spans | ambiguous_spans | ambiguous_pct | wordings |
|---|---|---|---|---|
| 304,682 | 227,990 | 60,464 | 26.5 | 23,440 |

### Per axis, empty vs populated: (span, class) rows, distinct spans, wordings (`labels` = the same wordings weighted by label_count)

| axis | pop | span_class_rows | spans | wordings | labels |
|---|---|---|---|---|---|
| FDA-CYP PK | empty | 540 | 540 | 235 | 502 |
| FDA-CYP PK | populated | 7,066 | 7,066 | 1,959 | 3,612 |
| MED-RT EPC | empty | 12,078 | 12,078 | 6,912 | 16,381 |
| MED-RT EPC | populated | 121,772 | 121,772 | 19,449 | 48,310 |
| MED-RT MoA | empty | 2,507 | 2,507 | 1,335 | 2,800 |
| MED-RT MoA | populated | 29,534 | 29,534 | 10,861 | 25,073 |
| MED-RT PE | empty | 1 | 1 | 1 | 1 |
| MED-RT PE | populated | 9,440 | 9,440 | 4,415 | 11,156 |
| MED-RT TC | populated | 16,757 | 16,757 | 6,550 | 15,658 |
| MeSH PA | empty | 112 | 112 | 83 | 326 |
| MeSH PA | populated | 104,875 | 104,875 | 19,935 | 50,058 |

### Classes mentioned at least once vs classes matchable, per axis

| axis | pop | classes | mentioned |
|---|---|---|---|
| FDA-CYP PK | empty | 5 | 3 |
| FDA-CYP PK | populated | 60 | 38 |
| MED-RT EPC | empty | 272 | 26 |
| MED-RT EPC | populated | 539 | 148 |
| MED-RT MoA | empty | 242 | 4 |
| MED-RT MoA | populated | 539 | 83 |
| MED-RT PE | empty | 1,291 | 1 |
| MED-RT PE | populated | 582 | 18 |
| MED-RT TC | empty | 3 | 0 |
| MED-RT TC | populated | 63 | 23 |
| MeSH PA | empty | 67 | 8 |
| MeSH PA | populated | 500 | 143 |

### Distinct statements (labelling drug x class; label-derived, not per wording) and classes gaining at least one, per axis

| axis | statements | classes | subject_in_class |
|---|---|---|---|
| FDA-CYP PK | 747 | 41 | 31 |
| MED-RT EPC | 6,123 | 166 | 163 |
| MED-RT MoA | 1,435 | 83 | 78 |
| MED-RT PE | 374 | 15 | 7 |
| MED-RT TC | 937 | 22 | 105 |
| MeSH PA | 4,519 | 142 | 396 |

### Yield per match_form (§4.2 key form): rows, spans, ambiguous spans

| match_form | span_class_rows | spans | wordings | classes | ambiguous_spans |
|---|---|---|---|---|---|
| label_order | 5,404 | 5,404 | 1,522 | 26 | 0 |
| number | 142,821 | 136,431 | 21,382 | 323 | 47,153 |
| stored | 156,457 | 129,286 | 21,052 | 370 | 56,442 |

### Moiety spans lying inside a class span (§4.4 overlap; equal spans count)

| moiety_occurrences | wordings |
|---|---|
| 10,645 | 6,119 |

### Advice level distribution (per SPAN)

| advice_level | spans | pct | wordings | mixed |
|---|---|---|---|---|
| not_read | 176,288 | 77.32 | 23,030 | 0 |
| adjust_dose | 25,520 | 11.19 | 7,083 | 12,233 |
| monitor | 12,047 | 5.28 | 4,313 | 0 |
| avoid | 8,761 | 3.84 | 2,042 | 6,067 |
| contraindicated | 5,374 | 2.36 | 2,869 | 1,231 |

### Level x scope (per span)

| advice_level | advice_scope | spans |
|---|---|---|
| adjust_dose | heading_block | 9,696 |
| adjust_dose | sentence | 15,824 |
| avoid | heading_block | 8,761 |
| contraindicated | heading_block | 930 |
| contraindicated | sentence | 4,444 |
| monitor | heading_block | 12,047 |
| not_read | sentence | 176,288 |

### Level x axis ((span, class) rows)

| axis | advice_level | span_class_rows |
|---|---|---|
| FDA-CYP PK | adjust_dose | 1,779 |
| FDA-CYP PK | avoid | 207 |
| FDA-CYP PK | contraindicated | 403 |
| FDA-CYP PK | monitor | 17 |
| FDA-CYP PK | not_read | 5,200 |
| MED-RT EPC | adjust_dose | 14,383 |
| MED-RT EPC | avoid | 6,468 |
| MED-RT EPC | contraindicated | 3,829 |
| MED-RT EPC | monitor | 6,434 |
| MED-RT EPC | not_read | 102,736 |
| MED-RT MoA | adjust_dose | 2,637 |
| MED-RT MoA | avoid | 1,004 |
| MED-RT MoA | contraindicated | 2,440 |
| MED-RT MoA | monitor | 1,812 |
| MED-RT MoA | not_read | 24,148 |
| MED-RT PE | adjust_dose | 778 |
| MED-RT PE | avoid | 312 |
| MED-RT PE | contraindicated | 149 |
| MED-RT PE | monitor | 45 |
| MED-RT PE | not_read | 8,157 |
| MED-RT TC | adjust_dose | 1,485 |
| MED-RT TC | avoid | 1,500 |
| MED-RT TC | contraindicated | 255 |
| MED-RT TC | monitor | 502 |
| MED-RT TC | not_read | 13,015 |
| MeSH PA | adjust_dose | 10,513 |
| MeSH PA | avoid | 1,740 |
| MeSH PA | contraindicated | 2,807 |
| MeSH PA | monitor | 6,165 |
| MeSH PA | not_read | 83,762 |

### Phrase rules that fired (per span)

| rule | spans | wordings |
|---|---|---|
| (not_read) | 176,288 | 23,030 |
| adjust_dose.dose_change | 14,329 | 4,541 |
| monitor.monitor | 7,992 | 3,563 |
| avoid.reserve | 4,638 | 687 |
| adjust_dose.reduce_dose | 3,849 | 1,583 |
| adjust_dose.dose_adjustment | 3,369 | 1,869 |
| contraindicated.contraindicated | 3,326 | 1,460 |
| monitor.observe | 3,209 | 705 |
| avoid.avoid | 2,625 | 1,431 |
| adjust_dose.separate_administration | 2,230 | 880 |
| contraindicated.see_contraindications | 1,992 | 1,548 |
| adjust_dose.dose_cap | 1,174 | 413 |
| avoid.not_recommended | 1,140 | 870 |
| monitor.caution | 711 | 547 |
| adjust_dose.titrate | 569 | 326 |
| avoid.do_not_use | 336 | 334 |
| monitor.observe_closely | 135 | 59 |
| contraindicated.contraindications_section | 56 | 19 |
| avoid.do_not_coadminister | 22 | 19 |

### Spans covered by an existing quote window, per level

| advice_level | spans | quoted |
|---|---|---|
| adjust_dose | 25,520 | 6,323 |
| avoid | 8,761 | 2,404 |
| contraindicated | 5,374 | 1,686 |
| monitor | 12,047 | 3,563 |
| not_read | 176,288 | 46,870 |

### TOP OFFENDERS: the 15 classes with most (span, class) rows (a class matching ordinary English shows up here)

| class_name | axis | pop | span_class_rows | wordings |
|---|---|---|---|---|
| Diuretics | MeSH PA | populated | 17,060 | 5,552 |
| Anticoagulants | MeSH PA | populated | 13,987 | 5,375 |
| Benzodiazepine [EPC] | MED-RT EPC | populated | 9,232 | 3,272 |
| Corticosteroid [EPC] | MED-RT EPC | populated | 9,232 | 3,054 |
| Antacid [PE] | MED-RT PE | populated | 7,553 | 3,464 |
| Antacids | MeSH PA | populated | 7,553 | 3,464 |
| Tricyclic Antidepressant [EPC] | MED-RT EPC | populated | 7,343 | 4,334 |
| Anticholinergic [EPC] | MED-RT EPC | populated | 7,101 | 2,188 |
| Monoamine Oxidase Inhibitors [MoA] | MED-RT MoA | populated | 5,580 | 4,063 |
| Monoamine Oxidase Inhibitors | MeSH PA | populated | 5,580 | 4,063 |
| Monoamine Oxidase Inhibitor [EPC] | MED-RT EPC | populated | 5,580 | 4,063 |
| Estrogen [EPC] | MED-RT EPC | populated | 5,507 | 2,655 |
| Estrogens | MeSH PA | populated | 5,507 | 2,655 |
| Protease Inhibitor [EPC] | MED-RT EPC | populated | 5,222 | 2,352 |
| Protease Inhibitors | MeSH PA | populated | 5,222 | 2,352 |

### Drug x drug pairs (unchanged by the class pass; floors 29,258 / 25,960; last recorded run 29,952 / 26,598)

| pairs | novel |
|---|---|
| 29,952 | 26,598 |

### Population (wordings and labels the class pass ran over)

| wordings | labels | wordings_with_a_class |
|---|---|---|
| 27,406 | 68,550 | 23,440 |

### Presence of the parenthetical spelling (corpus)

| figure | wordings | labels |
|---|---|---|
| wordings WITH the long-form spelling near a role word (presence) | 283 | 783 |

### Mentions near a table-row label (corpus)

| figure | span_class_rows | wordings | of all rows |
|---|---|---|---|
| (span, class) rows within 80 chars of Intervention/Clinical Impact | 26,410 | 2,783 | 304,682 |

### Notes on reading these tables

* "pop" = the class has at least one direct `class_membership` row. MED-RT PK and APC are
  excluded from matching by design (so absent); FDA-CYP is also `PK` and is matched.
* Units: "span_class_rows" = (span, class) rows; "spans" = distinct mentions. A reading
  depends only on the span, so level, rule, scope and quote-coverage tables are per span.
  "Level x axis" is per row because an axis belongs to the class.
* `labels` weights the same distinct wordings by `label_count` (the ~2.5x ratio made visible).
* Statements are label-derived (subject moiety x class), so they have no wording grain;
  `subject_in_class` = the labelling drug is itself a member of the class.
* The sentence-splitter failure signal is the boundary-drop count (64 rows), taken from the ingest
  summary; the census cannot recompute it because dropped spans are, by definition, not stored.

## Class hygiene sample (Task 9B phase 1)

Question asked of each sampled span: **is this span a mention of the DRUG CLASS?** Read by the
designer's clinical reader on the exact normalised openFDA text the ingest read (R18), run 11.

**Sample.** Spans of run 11 grouped by the sorted set of classes they fold onto (twin classes
such as `Antacids` / `Antacid [PE]` carry identical spans, so they are read once); the 25 largest
groups by distinct spans (38 classes); within each group the spans sorted by
(text_key, char_start, char_end) and `random.Random(20261108).sample(spans, 10)`. 250 spans.

**Verdict codes.** `Y` the class itself; `Yn` the class narrowed to a sub-group of its drugs
("oral anticoagulants", "Class III antiarrhythmics", "inhalation anesthetics", "thyroid
hormones") -- counted as yes; `E` an effect / property / dose adjective ("anticoagulant
effect of warfarin", "analgesic doses of aspirin"); `P` physiology / lab / endogenous substance
("antidiuretic hormone", "plasma hormone levels"); `D` a single drug's name; `O` other (part of
another class name, e.g. "insulin secretagogue"; a modifier that takes it outside the class,
e.g. "nondepolarizing muscle relaxants" = neuromuscular blockers, none of which is a member;
a procedure, "anesthetic induction"; an anaphor for one named drug).

"Fails" below = fewer than 8 of 10 yes. n = 10 per group, so a rate is a screen, not an estimate.

| class(es) | axis | spans | members | yes | of which Yn | dominant false positive | verdict |
|---|---|---|---|---|---|---|---|
| Diuretics | MeSH PA | 17,118 | 58 | 10/10 | 0 | -- | ok |
| Anticoagulants | MeSH PA | 16,178 | 67 | 7/10 | 3 | E: "anticoagulant effect(s)/properties" (3) | **fails** |
| Corticosteroid [EPC] | MED-RT EPC | 9,939 | 30 | 7/10 | 0 | E: "corticosteroid side effects" (2); P: 17-hydroxycorticosteroid test (1) | **fails** |
| Benzodiazepine [EPC] | MED-RT EPC | 9,241 | 14 | 10/10 | 0 | -- | ok |
| Anticholinergic [EPC] | MED-RT EPC | 7,661 | 13 | 8/10 | 0 | E: "anticholinergic (drying) effects" (2) | marginal |
| Antacids; Antacid [PE] | MeSH PA; MED-RT PE | 7,553 | 14; 6 | 10/10 | 0 | -- | ok |
| Tricyclic Antidepressant [EPC] | MED-RT EPC | 7,343 | 9 | 10/10 | 0 | -- | ok |
| Acetaminophen [EPC] | MED-RT EPC | 6,588 | **0** | **0/10** | 0 | D: the drug acetaminophen, mostly inside a product name (10) | **fails** |
| Analgesics; Analgesics [TC] | MeSH PA; MED-RT TC | 5,654 | 343; 131 | **4/10** | 3 | E: "analgesic effect of X" (4), "analgesic doses of aspirin" (2) | **fails** |
| Monoamine Oxidase Inhibitors (EPC, MeSH, MoA) | 3 axes | 5,580 | 5; 24; 31 | 10/10 | 0 | -- | ok |
| Estrogens; Estrogen [EPC] | MeSH PA; MED-RT EPC | 5,509 | 20; 3 | 9/10 | 0 | P: "estrogen reabsorption" (1) | ok |
| Protease Inhibitors (EPC, MeSH, MoA) | 3 axes | 5,223 | 9; 131; 2 | 10/10 | 0 | -- | ok |
| Insulin [EPC] | MED-RT EPC | 5,156 | 1 | 7/10 | 0 | O: "insulin secretagogue(s)" (3) | **fails** |
| Muscle Relaxant [EPC] | MED-RT EPC | 5,007 | 6 | 8/10 | 0 | O: "(non)depolarizing muscle relaxants" = neuromuscular blockers (2) | marginal |
| Hormones | MeSH PA | 4,496 | 138 | **4/10** | 4 | P: antidiuretic hormone, hormone levels, binding (5); O: "hormone replacement therapy" (1) | **fails** |
| Antiarrhythmic [EPC] | MED-RT EPC | 4,179 | 12 | 10/10 | 7 | -- | ok |
| Barbiturate [EPC] | MED-RT EPC | 4,151 | 2 | 10/10 | 0 | -- | ok |
| Anticonvulsants; Anticonvulsants [TC] | MeSH PA; MED-RT TC | 4,143 | 96; 22 | 10/10 | 0 | -- | ok |
| Calcium Channel Blocker(s) | MED-RT EPC; MeSH PA | 3,434 | 3; 95 | 10/10 | 0 | -- | ok |
| Thiazide Diuretic [EPC] | MED-RT EPC | 3,265 | 5 | 10/10 | 0 | -- | ok |
| Macrolide [EPC] | MED-RT EPC | 3,138 | 1 | 10/10 | 0 | -- | ok |
| Selective Serotonin Reuptake Inhibitors | MeSH PA | 3,095 | 29 | 10/10 | 0 | -- | ok |
| Anesthetics; Anesthetics [TC] | MeSH PA; MED-RT TC | 2,503 | 101; 45 | 8/10 | 7 | O: "anesthetic induction" (1), anaphor for one named drug (1) | marginal |
| Proton Pump Inhibitors (EPC, MeSH, MoA) | 3 axes | 2,486 | 6; 15; 17 | 10/10 | 0 | -- | ok |
| Sympathomimetics | MeSH PA | 2,473 | 89 | 10/10 | 0 | -- | ok |

Overall 212 / 250 yes (84.8%); without the two classes P1/P3 below remove, 208 / 230 (90.4%).
Members = moieties with a direct `class_membership` row (per class, in the order named).

**Findings beyond the table.**

* `Acetaminophen [EPC]` is a drug name. The registry knows the moiety only as `paracetamol`
  (display name and INN; it holds no "acetaminophen" name at all), so R16(b) cannot see it --
  and **the moiety pass does not see "acetaminophen" either**: 6,588 spans over 1,559 wordings
  are a drug x drug coverage gap of the shipped ingest (out of this round's scope, §4.4; for an
  issue). `Nicotinic Acid [EPC]` (733 spans; registry name `niacin`) is the same shape.
* `Hormones` (MeSH PA) is the twin R16(a) predicted: the APC `Hormone [APC]` it excluded (4,486
  occurrences) duplicated it, so the same spans now arrive through MeSH (4,496). Every yes in its
  sample is "thyroid hormone(s)" -- a different, narrower thing than the 138-member class.
* `Enzyme Inducers [MoA]` (empty, 2,218 spans), read after the ruling on request (10 spans,
  same seed and method): **10/10 yes** (2 narrowed: "strong CYP enzyme inducers", "UGT enzyme
  inducers"). Real class phrases, mostly the combined-oral-contraceptive back-up-contraception
  advice -- but the class has NO members, so its statements attach to a class no drug is filed
  under. Reported, not excluded. Spans: a2f619932f04:1290-1304, d65e6eaf5656:880-895,
  6ef2c80d3c90:1554-1569 (Yn), 92a7cff2d9ea:44-59, bbaaf8cc8d4d:975-990, 084cad7832ed:402-417,
  ca45304dd5ce:44-59, 2803535c10c1:1331-1346, c9c5c0ec5fd1:1496-1510, 336943fdb9cf:2884-2899 (Yn).

### Proposed exclusion rules, and the ruling (R19)

**Controller ruling R19 (binding), implemented test-first and shipped in run 12:**
P1 accepted as proposed (counted as `class_keys_shadowed_by_official_names`: 2 keys);
P2 accepted **without `activity` and `properties`** -- "drugs with anticholinergic
activity/properties" are real class phrases and dropping them would under-warn -- so the
heads are effect(s) | action(s) | side effect(s) | secretagogue(s) | "dose(s) of"
(counted as `class_spans_dropped_on_a_non_drug_head`: 14,098 rows); P3 accepted as ONE named,
documented exclusion (`spl_class_vocab.EXCLUDED_CLASSES`). The proposals as written for the
ruling follow.

All three act in the CLASS pass only (moiety pass, drug x drug figures untouched), are
mechanical (derived from data already loaded, or a fixed closed list of head nouns), and are
measured on run 11.

* **P1 -- official-name keys (generalises R16(b)).** Drop a class match KEY that folds equal to a
  UNII **official name** (`UNII_Names` TYPE `of`) of a substance whose UNII a registry moiety
  claims. Per key, as R16(b): the plural `phenothiazines` survives. Removes all **6,588**
  `Acetaminophen [EPC]` spans and the **733** `Nicotinic Acid [EPC]` spans (7,321); every other
  class it hits is already dropped by R16(b) (Calcitonin, Interferon alfa-2b/beta/gamma,
  Parathyroid Hormone, Phenothiazine, Progesterone). Restricting to `of` is deliberate: the
  common-name type `cn` would also drop the class word `sulfonamide` (1,281 spans; a real class).
* **P2 -- non-drug head noun.** Drop a class span when the next word (past at most one short
  parenthetical, e.g. "(drying)") is `effect(s)`, `activity`/`activities`, `property`/`properties`,
  `action(s)`, `side effect(s)`, `secretagogue(s)`, or `dose(s) of` -- the class word is then an
  adjective describing an effect, or part of another class name. Removes **11,209 spans**
  (15,785 rows): Analgesics 3,856 of 5,654; Anticoagulants 2,695; Anticholinergic 1,144;
  Insulin 962; Corticosteroid 707; Dopamine Agonists 462; Antiviral 429; Anticonvulsants 228;
  Anxiolytics 154; Diuretics 101; others smaller. Checked on two fresh random draws of the spans
  it removes (seeds 20261109, 20261110; 40 + 30): **no genuine class mention removed**; 2 of the
  30 are property-defined drug groups ("drugs with/having <class> activity"), up to
  ~1,343 spans of that shape if the controller prefers to keep them (exempt when preceded by
  with/having). Bare `dose(s)` is NOT a head: it removed a real one (a PPI-class span followed by
  "dose" that carries genuine dose advice for the class). On this sample P2 removes 13 false positives and no yes:
  Anticoagulants 7/8, Corticosteroid 7/8, Anticholinergic 8/8, Analgesics 4/4, Insulin 7/7.
* **P3 -- exclude MeSH PA `Hormones`** from the vocabulary, as R16(a) excluded its APC twin:
  4/10, the yes readings all name a narrower group, physiology dominant. Removes **4,496 spans**.
  (No mechanical fix inside the class: the FP is the preceding word -- thyroid, antidiuretic,
  growth -- and no "Thyroid Hormones" class exists to win the longest match.)

Not proposed: a rule for "(non)depolarizing muscle relaxants" (579 spans, 2/10 -- it would be a
hand-typed modifier list) or for Anesthetics' 2 one-offs. P1 + P2 + P3 remove about 23,000 of
249,444 spans (9%; P2 and P3 overlap slightly).

<details><summary>Per-span verdicts (text_key prefix : char_start-char_end verdict), groups in table order</summary>

```
G1: 9d94d717b41b:4415-4424 Y, db659dab3fc9:3337-3346 Y, 6c40af09dab4:390-399 Y, 9009e187fb74:4206-4214 Y, bcc92e0ef5fa:283-292 Y, 06450f57b719:74-83 Y, cfef1e3cf600:84-93 Y, 2628ad58383a:4592-4601 Y, cf8244678804:1962-1971 Y, 3450ac6e11cf:503-511 Y
G2: ffe799b5d986:494-507 E, 5352437bc5bb:6140-6154 Y, 7539e984e191:710-724 Yn, 37cd9adc22a3:5361-5374 E, e973e4eb1d92:42-55 Y, 4b55455f8b7c:230-244 Y, 63d14057bdcf:290-304 Y, 8fb0c956a898:1310-1323 E, 0459b7bbc95d:113-127 Yn, 8de2528390e5:13327-13341 Yn
G3: 9757890dc6fd:3378-3392 Y, c3af31f947fa:3038-3053 Y, 6a35e2e4a7c6:170-185 Y, 8e25e0cb86ff:975-989 Y, ad46d342ba96:101-116 Y, ec854ff3f7af:604-618 E, 05773679a7ce:3446-3460 Y, ebd12f2b7d8f:918-932 Y, bb7a5304ab32:9570-9584 P, 223c8b8ee95e:22337-22351 E
G4: 961dba518536:245-260 Y, cefdf319325e:4625-4640 Y, 6a078ef4d99e:2902-2917 Y, 890ac1d1c7f6:3371-3386 Y, af5cbc62b5ca:7578-7593 Y, fb180a773d6e:1626-1641 Y, 065583cdf1e7:140-155 Y, f8e9fba92208:2779-2794 Y, c1750f78da1e:936-950 Y, 25fab61879e2:5813-5828 Y
G5: 552b26569423:834-849 Y, 76afd29fbd9c:892-907 Y, 3c02d6a9109b:7938-7953 Y, f6234cc82caa:552-568 Y, 4dd015f2f315:399-415 Y, 65597d86f0f6:5720-5735 Y, 939780805234:8406-8421 Y, 049d1fc4d5e6:5566-5581 Y, 9109d05fef92:260-275 E, d2329785159b:2690-2705 E
G6: 60b80cc147c5:787-794 Y, 81254fe9b313:4359-4367 Y, 439b4194b639:1431-1439 Y, f9b9dc9c8aa6:7126-7134 Y, 575182d47132:538-545 Y, 6f8318b57b04:1081-1088 Y, 9d2e8c124514:1188-1195 Y, 0451274fab83:3591-3599 Y, 9c04f457c9d4:418-426 Y, d375bb2f8e06:7548-7555 Y
G7: 5c37f3c65db9:6179-6204 Y, 7fadc3b1430e:4012-4036 Y, 3e01df4b771d:480-505 Y, ff5720235890:1621-1646 Y, 529b92a1728a:2253-2277 Y, 6e41b04984c6:2033-2058 Y, 9be1499892c5:653-678 Y, 04079f12d304:1786-1811 Y, 9aa0e9633a66:1352-1377 Y, d90723151b15:482-507 Y
G8: 69dbb39bea40:5589-5602 D, 902d207b8d41:5169-5182 D, 49c6c25028e4:583-596 D, 63431765bca6:3982-3995 D, 7ee8e3352675:3432-3445 D, ae4a1f95b938:4920-4933 D, 03655febf5a3:4211-4224 D, ab9c479ab141:7559-7572 D, f462d5a23e19:7899-7912 D, d3f12ae2c267:11054-11067 D
G9: 7aa817311878:6006-6015 E, a793441d1ed4:1356-1365 E, 5266ddf97dd2:1059-1069 Yn, 6e9deab21c41:2411-2421 Y, 8e93420090ef:2847-2856 E, ce48adbd9231:9955-9964 E, 05617ce018ac:471-481 Yn, cc07858587d4:635-645 Yn, f91cbe5506a6:110-119 E, 9f49dfc441b3:7840-7849 E
G10: 7ad3694c1857:145-173 Y, a8590b6085fc:635-663 Y, 50ccb2f70ad6:1421-1449 Y, 70142635aa47:1242-1270 Y, 8eab70a77014:5703-5731 Y, d080774850c7:6159-6187 Y, 0529f9df3ded:1524-1552 Y, cdc197ba9247:4934-4962 Y, fb8e81b5c7eb:1553-1581 Y, 9dbd8ce86def:521-549 Y
G11: 7febec295a7e:378-387 Y, acc2a03cd9e5:1884-1893 Y, 578e3ba65eb1:1559-1567 P, 769dbbb05595:840-849 Y, 9616584750a3:2719-2728 Y, d591efe455c8:372-381 Y, 0573e5f788d2:4367-4375 Y, d460f3d3f7e6:1452-1460 Y, fed234552a52:760-769 Y, a2f619932f04:1975-1983 Y
G12: 85deb4118ed3:632-651 Y, b72861f077cf:3399-3418 Y, 5b9aace92396:3226-3244 Y, 7a12ebff3503:345-364 Y, 9ea7b0f97a71:9300-9319 Y, de73ba422496:345-364 Y, 05fbb5c7b54c:2298-2317 Y, dc6b0de55d15:1250-1269 Y, ade380f68868:2391-2409 Y, 21b321539ede:1998-2017 Y
G13: 823ad282b61b:661-668 Y, b76adb434de6:1762-1769 O, 5ab2d19267ec:1806-1813 O, 76ada56048d6:709-716 Y, 9c1a66501a72:1417-1424 Y, dffa08e0eb99:404-411 Y, 06bf785b2160:713-720 Y, dd2e28309a4e:1561-1568 O, ab090bc57031:9227-9234 Y, 2140c3630f12:595-602 Y
G14: 84e3fe2710f2:5359-5374 O, b8f3a8cd3ba5:3386-3402 Y, 59704fe25247:11809-11825 Y, 792aa052b9db:7815-7830 Y, 9a5e3f5d7d9e:2627-2643 Y, e650beb3037e:7577-7592 Y, 05a52e3d0b0e:3384-3400 Y, e34700e72226:4017-4033 O, ace55dd3f71d:1164-1180 Y, 1f196c7e91de:2790-2806 Y
G15: 963e4213a6b1:83-90 Yn, d17cd6627659:8779-8787 Yn, 6a473a544d5c:3371-3379 P, 884f0c3529c2:2395-2402 P, b53dccc42775:7017-7024 P, 07a3cf980800:1053-1061 Yn, fff4838c2192:7208-7215 P, c73e74a0f41e:7253-7260 P, 27071c265f6d:922-929 O, c6fb79d57275:4104-4111 Yn
G16: aa4c50f88bad:6311-6326 Y, e20ebf5807ca:10451-10466 Yn, 71bf8a74a753:593-608 Yn, 9a2618c0de8e:3953-3968 Yn, c562f55b9dfc:2287-2302 Y, 07295b9e3a4d:3956-3971 Y, d4f6d120d91d:3475-3490 Yn, 285e9e85226e:106-120 Yn, d4e5b4209f99:4192-4207 Yn, 374c80c05b68:670-685 Yn
G17: a755af9c4110:1540-1552 Y, e6b1961728f2:4927-4939 Y, 767102afb8fb:8916-8927 Y, 9aab80157fc9:8989-9000 Y, c760ad28b1d1:237-249 Y, 07ea9907db75:8806-8817 Y, dc8a8ea1eeec:2114-2126 Y, 2d84287493e4:739-751 Y, dbc1faff0125:9607-9618 Y, 3a750657d36b:8993-9004 Y
G18: a82ac70f846a:10112-10127 Y, e34797490356:1585-1600 Y, 74db3a98346a:1214-1228 Y, 99ffeec8bd34:879-894 Y, c65cb778c801:10292-10306 Y, 05468f96a995:1346-1361 Y, da73aba45205:6084-6099 Y, 2863dc72ce28:7989-8003 Y, da2b59949bcc:3539-3553 Y, 367e75d511bb:1224-1238 Y
G19: 625fd9eb2352:11652-11675 Y, 8a2122dddf78:889-912 Y, 4417a875d909:1223-1247 Y, 5b9aace92396:12000-12023 Y, 7441bade76bc:2033-2057 Y, a7ee12954f46:3114-3138 Y, 042aeec26a4d:7220-7244 Y, a6d8f9c5208d:12192-12216 Y, eae5bc3dd038:3148-3172 Y, d084f9e00c38:795-819 Y
G20: 64e21fea14e2:3365-3383 Y, 8e46cfa9a905:1474-1492 Y, 4414b1ff4a4a:1461-1479 Y, 5a4e199d2df7:2901-2919 Y, 794585cb1ae1:1012-1030 Y, b15a8bbe66b7:4172-4190 Y, 02f8f3d2592d:8335-8353 Y, afdb01b11f59:2245-2263 Y, f3b719bca1a2:138-156 Y, d31685de6e89:2868-2886 Y
G21: 6f670c054c5f:1506-1515 Y, 982ae2348663:2530-2539 Y, 4c857396a524:2641-2650 Y, 63c25031f924:1799-1809 Y, 83594f11ea5f:1520-1529 Y, baeda3950e56:1531-1541 Y, 060031ac368f:1472-1481 Y, b9d08b5a9afe:1086-1095 Y, fec2391d90d4:534-543 Y, e363d51d8a51:3129-3139 Y
G22: 7134c62096e5:1866-1905 Y, 97cc7fd6af15:1654-1693 Y, 4a94bdca8e2d:5777-5816 Y, 6682c95f8ff5:845-884 Y, 83b7265357f9:4835-4874 Y, bc9f3d6abab8:552-591 Y, 04cfa642a5df:885-924 Y, bb91f780a033:1110-1149 Y, e2557ae4447b:1245-1284 Y, 8ea8a5bae38a:849-888 Y
G23: 82be56023afa:6314-6325 Yn, b7c357033151:1858-1869 Yn, 565216c59565:2078-2089 Yn, 792b7cc34028:7403-7414 Yn, 989b65c4e1b1:5474-5485 Yn, e93d0b48d30d:769-780 Yn, 051b3a34bd65:716-727 Y, e6aa094edbba:6562-6573 Yn, aca4b62f26d1:1550-1560 O, 1db893b571fd:2565-2575 O
G24: 8a60b3c7f93f:10251-10272 Y, bea0e82bb8f9:261-283 Y, 62da78850376:172-194 Y, 7dd6ea959742:172-194 Y, 9f502eda8376:8787-8809 Y, edd4c22ed8c5:1074-1096 Y, 05cd92e7d0f8:4334-4356 Y, eacd3ed4f398:8967-8989 Y, b2647ad6ab3c:3725-3747 Y, 21684fc6c3d7:1083-1105 Y
G25: 8575cfe78dfb:2950-2966 Y, bf09b2454dee:294-310 Y, 5aad80bfb87c:24-39 Y, 7a0c5e4540f4:1010-1025 Y, a12b8c6845fd:7098-7114 Y, eaafa0ee7214:5930-5946 Y, 06abf9893633:887-903 Y, e7d2dd1d0fd6:1164-1180 Y, b591803f5837:1123-1139 Y, 2257c8bcc3e5:3655-3671 Y
```
</details>

## Precision sample (Task 9B phase 2)

**What was read.** For each sampled span, the exact normalised openFDA text the ingest read
(R18), the machine's scope (the sentence, or the heading/row block) plus the text just around
it. The hand reading is **what the label advises about THAT class mention within the
machine's scope**: contraindicated / avoid / adjust_dose / monitor / no_meaningful_interaction /
none. Advice the label gives just OUTSIDE the scope (the next sentence, a table row's
Intervention cell the reader did not reach) is flagged `ctx:<level>` and reported, not
counted as agreement or as an under-read.

**Reading conventions (for the owner's review).** "Use with caution" -> monitor. "If
coadministration cannot be avoided, monitor ..." -> avoid (implied). Separating the doses in
time ("at least 2 hours before", "separate administration") -> adjust_dose. "Reserve
concomitant prescribing ..." (where nothing else will do) and "cessation ... is preferred" ->
avoid. Advice about a NAMED member of the class ("atazanavir with
simvastatin is contraindicated" under "HMG-CoA reductase inhibitors") counts for the class
(flag `members`). A subject-in-class mention (the label naming its own drug's class) is read
as the advice on combining the labelled drug with the other drug (flag `subject`). A span that
is not a class mention at all ("monitor electrolytes", "anti-fibrinolytic agents") is read
`none` and flagged `non-mention`: it counts against the advice gates, because it is what a
class profile would show.

**Sampling.** Spans grouped by (text_key, char_start, char_end, scope, level, rule, mixed);
per stratum `random.Random(f"{seed}:{scope}:{level}").sample(sorted(pool), n)`.
Agreement = hand reading equals the machine level (not_read counts as equal to `none`);
"stronger" = machine above the label (the safe direction); "under" = machine below it.

| draw | run | seed | stratum (scope:level) | n | agree | % | stronger | under | contraindicated read lower |
|---|---|---|---|---|---|---|---|---|---|
| 1 | 12 | 20261111 | sentence:contraindicated | 100 | 80 | 80 | 20 | 0 | 0 |
| 1 | 12 | 20261111 | sentence:avoid | 50 | 30 | 60 | 20 | 0 | 0 |
| 1 | 12 | 20261111 | sentence:adjust_dose | 50 | 41 | 82 | 9 | 0 | 0 |
| 1 | 12 | 20261111 | sentence:monitor | 50 | 35 | 70 | 6 | 9 | 0 |
| 1 | 12 | 20261111 | sentence:not_read | 50 | 43 | 86 | 0 | 7 | 0 (1 ctx: a table row's Intervention) |
| 1 | 12 | 20261111 | heading_block:contraindicated | 10 | 7 | 70 | 3 | 0 | 0 |
| 1 | 12 | 20261111 | heading_block:avoid | 15 | 14 | 93 | 1 | 0 | 0 |
| 1 | 12 | 20261111 | heading_block:adjust_dose | 15 | 15 | 100 | 0 | 0 | 0 |
| 1 | 12 | 20261111 | heading_block:monitor | 15 | 10 | 67 | 0 | 5 | 0 |
| 2 | 13 | 20261113 | sentence:avoid | 50 | 41 | 82 | 9 | 0 | 0 |
| 2 | 13 | 20261113 | sentence:adjust_dose | 50 | 48 | 96 | 2 | 0 | 0 |
| 2 | 13 | 20261113 | sentence:monitor | 50 | 43 | 86 | 6 | 1 | 0 |
| 2 | 13 | 20261113 | sentence:not_read | 50 | 42 | 84 | 0 | 8 | **1** (bare CONTRAINDICATIONS cell) |
| 2 | 13 | 20261113 | heading_block:contraindicated | 15 | 12 | 80 | 3 | 0 | 0 |
| 2 | 13 | 20261113 | heading_block:avoid | 20 | 11 | 55 | 9 | 0 | 0 |
| 2 | 13 | 20261113 | heading_block:adjust_dose | 20 | 5 | 25 | 5 | 10 | 0 |
| 2 | 13 | 20261113 | heading_block:monitor | 20 | 16 | 80 | 0 | 4 | 0 |
| 3 | 14 | 20261114 | sentence:avoid | 50 | 39 | **78** | 11 | 0 | 0 |
| 3 | 14 | 20261114 | sentence:adjust_dose | 30 | 29 | 97 | 1 | 0 | 0 |
| 3 | 14 | 20261114 | sentence:monitor | 50 | 44 | **88** | 4 | 2 | 0 |
| 3 | 14 | 20261114 | sentence:not_read | 50 | 42 | 84 | 0 | 8 | 0 (1 ctx: next sentence) |
| 3 | 14 | 20261114 | heading_block:avoid | 20 | 20 | 100 | 0 | 0 | 0 |
| 3 | 14 | 20261114 | heading_block:adjust_dose | 20 | 19 | 95 | 0 | 1 | 0 |
| 3 | 14 | 20261114 | heading_block:monitor | 20 | 20 | 100 | 0 | 0 | 0 |

870 spans read in all. Draw 3 excluding the non-mention spans: sentence avoid 39/47 (83.0%),
sentence monitor 44/47 (93.6%). Under-reads in the not_read strata are all below
contraindicated (adjust_dose / avoid / monitor phrasings the rules miss); in draws 2 and 3 the
hand found 5 explicit no-interaction statements in not_read (the machine never reads NMI).

**What the misses are.** Overwhelmingly the reader taking ANOTHER drug's advice from the same
scope: highlight bullet runs and PLR table rows written without full stops, so one "sentence"
holds several items (sentence avoid draw 1: 13 of 20 misses; draw 3: 5 of 11, with 3 more
non-mentions and 3 "to avoid" purpose clauses / separations / footnotes). Iteration 1
(run 13) added sentence starts at highlight cross-references and bullets, the PLR table row
reader (a mention in a row heading reads the row's Intervention cell), the Contraindications
cross-reference, "Contraindicateda", and the dose / caution / "not generally recommended"
phrasings. Iteration 2 (run 14) made PLR cell labels sentence starts and reads a row heading
glued to the previous cell from its own row first, added "reserve concomitant prescribing" /
"cessation ... preferred" (avoid -- 4,638 spans on run 15, the opioid-benzodiazepine boxed
language), separate administration, observe/evaluate/follow patients, a mask for "dose
adjustment" as a time reference, middle-dot bullets and the cross-reference variants. Each has
a synthesised test in `tests/test_spl_advice.py`.

### Targeted contraindication hunt (not random; every run)

Every span read BELOW contraindicated whose scope holds the letters "contraindicat", or whose
next sentence / row Intervention does, listed and read (`9b-work/p2_contra_hunt*.md`).

| run | in-scope "contraindicat", read lower | next row Intervention says contraindicated, read not_read | next sentence mentions it |
|---|---|---|---|
| 12 | 1,997 | 341 | 3,236 |
| 13 | 102 | 2 | 2,083 |
| 14/15 | 32 | 0 | 2,070 |

Run 12's in-scope cases were mostly "[See ... Contraindications (4.1)]" cross-references for
MAOIs and a glued table footnote "Contraindicateda"; the 341 were PLR rows whose Intervention
cell says contraindicated (MAOIs with serotonergic drugs, chiefly). On run 14/15, of the 32
remaining in-scope cases 28 are correct or not a contraindication of the class (negated "NOT
contraindicated", "not a contraindication", a table-of-contents line, a cross-reference that
belongs to another row, the effect class "decreased blood pressure"), and **4 are genuine
misses** caused by extraction garble: "[s ee ... Contraindications (4.1)]" (2 spans, MAOIs),
"See CONTRAINDICATIONSand WARNINGS" (1, MAOIs), and a table cell "Contraindicate" (1,
"Anticoagulants and Antiplatelets" row). The "next sentence" column is outside the reader's
scope by design (mostly other drugs' contraindications); it is reported, not counted.

<details><summary>Per-span readings, draw 1 (run 12, before tuning)</summary>

| id | text_key | span | class | scope | machine | rule | hand | flag |
|---|---|---|---|---|---|---|---|---|
| sentence:contraindicated#1 | 39c8fe715e82 | 4015-4031 | Sympathomimetics | sentence | contraindicated | contraindicated.contraindicated | contraindicated |  |
| sentence:contraindicated#2 | a7f9d563fb23 | 942-966 | CYP1A2 strong inhibitor [FDA-CYP] | sentence | contraindicated | contraindicated.contraindicated | contraindicated |  |
| sentence:contraindicated#3 | b1133ee943d0 | 886-912 | CYP2D6 moderate inhibitor [FDA-CYP] | sentence | contraindicated | contraindicated.contraindicated | contraindicated |  |
| sentence:contraindicated#4 | 972ffbc7b419 | 4716-4738 | CYP3A strong inhibitor [FDA-CYP] | sentence | contraindicated | contraindicated.contraindicated | contraindicated |  |
| sentence:contraindicated#5 | 17864273e60b | 7669-7681 | Sulfonylurea [EPC] | sentence | contraindicated | contraindicated.contraindicated | none | merged-item |
| sentence:contraindicated#6 | 4a7a7cae5e9e | 9017-9026 | Macrolide [EPC] | sentence | contraindicated | contraindicated.contraindicated | avoid | other-clause |
| sentence:contraindicated#7 | 32d4ea950ba4 | 12051-12068 | Enzyme Inhibitors | sentence | contraindicated | contraindicated.contraindicated | contraindicated |  |
| sentence:contraindicated#8 | 5f350a9a9163 | 906-930 | CYP1A2 strong inhibitor [FDA-CYP] | sentence | contraindicated | contraindicated.contraindicated | contraindicated |  |
| sentence:contraindicated#9 | 23b313bde1dc | 175-203 | Monoamine Oxidase Inhibitor [EPC] | sentence | contraindicated | contraindicated.contraindicated | contraindicated |  |
| sentence:contraindicated#10 | 677823ba6c99 | 1515-1530 | P-gp inhibitor [FDA-CYP] | sentence | contraindicated | contraindicated.contraindicated | contraindicated |  |
| sentence:contraindicated#11 | 56e941599619 | 9495-9513 | Antimycobacterial [EPC] | sentence | contraindicated | contraindicated.contraindicated | contraindicated |  |
| sentence:contraindicated#12 | bd3f0b41d557 | 19880-19908 | HMG-CoA Reductase Inhibitor [EPC] | sentence | contraindicated | contraindicated.contraindicated | contraindicated | members |
| sentence:contraindicated#13 | 612b0b6cc628 | 430-458 | Monoamine Oxidase Inhibitor [EPC] | sentence | contraindicated | contraindicated.contraindicated | contraindicated |  |
| sentence:contraindicated#14 | 47460cafeb12 | 1135-1163 | Monoamine Oxidase Inhibitor [EPC] | sentence | contraindicated | contraindicated.contraindicated | contraindicated |  |
| sentence:contraindicated#15 | 02f6bb7c8d8f | 325-348 | HIV Protease Inhibitors | sentence | contraindicated | contraindicated.contraindicated | contraindicated |  |
| sentence:contraindicated#16 | b2acadd57f3a | 6353-6368 | Antiarrhythmic [EPC] | sentence | contraindicated | contraindicated.contraindicated | contraindicated | members |
| sentence:contraindicated#17 | 92d7059ecae1 | 155-183 | Monoamine Oxidase Inhibitor [EPC] | sentence | contraindicated | contraindicated.contraindicated | contraindicated |  |
| sentence:contraindicated#18 | 56e941599619 | 3630-3676 | Non-Nucleoside Reverse Transcriptase Inhibitors [MoA] | sentence | contraindicated | contraindicated.contraindicated | contraindicated |  |
| sentence:contraindicated#19 | ade380f68868 | 5237-5252 | Antiarrhythmic [EPC] | sentence | contraindicated | contraindicated.contraindicated | contraindicated |  |
| sentence:contraindicated#20 | f6ece081541e | 5156-5171 | Antiarrhythmic [EPC] | sentence | contraindicated | contraindicated.contraindicated | contraindicated |  |
| sentence:contraindicated#21 | c76b2af2cd34 | 18045-18062 | Ergot Derivative [EPC] | sentence | contraindicated | contraindicated.contraindicated | contraindicated |  |
| sentence:contraindicated#22 | b54418d2f27e | 393-416 | HIV Protease Inhibitors | sentence | contraindicated | contraindicated.contraindicated | contraindicated |  |
| sentence:contraindicated#23 | f5efe125ade8 | 1069-1097 | Monoamine Oxidase Inhibitor [EPC] | sentence | contraindicated | contraindicated.contraindicated | contraindicated |  |
| sentence:contraindicated#24 | be1a730c213b | 3810-3819 | Alkaloid [EPC] | sentence | contraindicated | contraindicated.contraindicated | contraindicated |  |
| sentence:contraindicated#25 | d0c402997b0d | 315-334 | Nitric Oxide Donors | sentence | contraindicated | contraindicated.contraindicated | contraindicated |  |
| sentence:contraindicated#26 | a1973c6df9a5 | 205-233 | Monoamine Oxidase Inhibitor [EPC] | sentence | contraindicated | contraindicated.contraindicated | contraindicated |  |
| sentence:contraindicated#27 | 6da730e2f5d8 | 316-339 | HIV Protease Inhibitors | sentence | contraindicated | contraindicated.contraindicated | contraindicated |  |
| sentence:contraindicated#28 | 7f2f1b0cb067 | 273-296 | HIV Protease Inhibitors | sentence | contraindicated | contraindicated.contraindicated | contraindicated |  |
| sentence:contraindicated#29 | 2df3abb4b8ae | 390-426 | Soluble Guanylate Cyclase Stimulator [EPC] | sentence | contraindicated | contraindicated.contraindicated | contraindicated |  |
| sentence:contraindicated#30 | fc1c8a5bbc9c | 3732-3741 | Alkaloid [EPC] | sentence | contraindicated | contraindicated.contraindicated | contraindicated |  |
| sentence:contraindicated#31 | aaecc993e996 | 319-342 | HIV Protease Inhibitors | sentence | contraindicated | contraindicated.contraindicated | contraindicated |  |
| sentence:contraindicated#32 | a59854a67371 | 7649-7661 | Sulfonylurea [EPC] | sentence | contraindicated | contraindicated.contraindicated | none |  |
| sentence:contraindicated#33 | bd3f0b41d557 | 5687-5703 | Antiviral Agent [TC] | sentence | contraindicated | contraindicated.contraindicated | contraindicated | members |
| sentence:contraindicated#34 | b2acadd57f3a | 15092-15107 | Benzodiazepine [EPC] | sentence | contraindicated | contraindicated.contraindicated | contraindicated | members |
| sentence:contraindicated#35 | f72d64716c09 | 18220-18237 | Ergot Derivative [EPC] | sentence | contraindicated | contraindicated.contraindicated | contraindicated |  |
| sentence:contraindicated#36 | b0124fb3e215 | 4005-4021 | Antiviral Agent [TC] | sentence | contraindicated | contraindicated.contraindicated | contraindicated | members |
| sentence:contraindicated#37 | e374554555e2 | 1605-1619 | Antihistamine [EPC] | sentence | contraindicated | contraindicated.contraindicated | none | other-clause |
| sentence:contraindicated#38 | eb2d77ec4e98 | 354-378 | Calcium Channel Blocker [EPC] | sentence | contraindicated | contraindicated.contraindicated | adjust_dose | merged-item |
| sentence:contraindicated#39 | 7970d33a8da7 | 3518-3532 | Antiprotozoal [EPC] | sentence | contraindicated | contraindicated.contraindicated | none |  |
| sentence:contraindicated#40 | b177899c18b5 | 14192-14211 | Protease Inhibitor [EPC] | sentence | contraindicated | contraindicated.contraindicated | none | other-clause |
| sentence:contraindicated#41 | 40429fea6caf | 719-742 | CYP3A strong inhibitor [FDA-CYP] | sentence | contraindicated | contraindicated.contraindicated | contraindicated |  |
| sentence:contraindicated#42 | c0f0c90e4af0 | 398-434 | Soluble Guanylate Cyclase Stimulator [EPC] | sentence | contraindicated | contraindicated.contraindicated | contraindicated |  |
| sentence:contraindicated#43 | c76b2af2cd34 | 5468-5484 | Antiviral Agent [TC] | sentence | contraindicated | contraindicated.contraindicated | contraindicated | members |
| sentence:contraindicated#44 | e3d17d00c2bf | 10695-10712 | Antimycobacterial [EPC] | sentence | contraindicated | contraindicated.contraindicated | contraindicated |  |
| sentence:contraindicated#45 | e6ea4b2ed612 | 1469-1481 | Barbiturate [EPC] | sentence | contraindicated | contraindicated.contraindicated | contraindicated |  |
| sentence:contraindicated#46 | 55ef8a126715 | 4121-4136 | Anticonvulsants | sentence | contraindicated | contraindicated.contraindicated | contraindicated |  |
| sentence:contraindicated#47 | b6c11fa341fc | 4573-4587 | Anticoagulants | sentence | contraindicated | contraindicated.contraindicated | adjust_dose | merged-item |
| sentence:contraindicated#48 | 3fce1fdd0850 | 14010-14027 | Ergot Derivative [EPC] | sentence | contraindicated | contraindicated.contraindicated | contraindicated |  |
| sentence:contraindicated#49 | f0915b8c4951 | 14264-14283 | Protease Inhibitor [EPC] | sentence | contraindicated | contraindicated.contraindicated | none |  |
| sentence:contraindicated#50 | 368c81300123 | 2354-2382 | Monoamine Oxidase Inhibitor [EPC] | sentence | contraindicated | contraindicated.contraindicated | contraindicated |  |
| sentence:contraindicated#51 | c789ef392403 | 25-53 | HMG-CoA Reductase Inhibitor [EPC] | sentence | contraindicated | contraindicated.contraindicated | contraindicated |  |
| sentence:contraindicated#52 | 70d123c58d34 | 3717-3765 | Non-Nucleoside Reverse Transcriptase Inhibitors [MoA] | sentence | contraindicated | contraindicated.contraindicated | none | other-clause |
| sentence:contraindicated#53 | 4d47ca089945 | 3270-3290 | Sedatives/Hypnotics [TC] | sentence | contraindicated | contraindicated.contraindicated | monitor | other-clause |
| sentence:contraindicated#54 | c76b2af2cd34 | 15067-15082 | Benzodiazepine [EPC] | sentence | contraindicated | contraindicated.contraindicated | contraindicated | members |
| sentence:contraindicated#55 | 5d12a93595af | 4246-4260 | Benzodiazepine [EPC] | sentence | contraindicated | contraindicated.contraindicated | none | merged-item |
| sentence:contraindicated#56 | c214558ce714 | 4620-4634 | Antiarrhythmic [EPC] | sentence | contraindicated | contraindicated.contraindicated | contraindicated | members |
| sentence:contraindicated#57 | 43e3b61d4462 | 307-335 | Monoamine Oxidase Inhibitor [EPC] | sentence | contraindicated | contraindicated.contraindicated | contraindicated |  |
| sentence:contraindicated#58 | 9908c6a90935 | 5632-5641 | Alkaloid [EPC] | sentence | contraindicated | contraindicated.contraindicated | contraindicated |  |
| sentence:contraindicated#59 | 274bc20cd1e5 | 1412-1441 | Carbonic Anhydrase Inhibitor [EPC] | sentence | contraindicated | contraindicated.contraindicated | monitor | merged-item |
| sentence:contraindicated#60 | e11f3d9b98a7 | 662-689 | Calcium Channel Antagonists [MoA] | sentence | contraindicated | contraindicated.contraindicated | contraindicated |  |
| sentence:contraindicated#61 | 5fc5f986b30a | 1422-1436 | Antihistamine [EPC] | sentence | contraindicated | contraindicated.contraindicated | contraindicated | members |
| sentence:contraindicated#62 | bcb4769d9eaf | 6195-6204 | Alkaloid [EPC] | sentence | contraindicated | contraindicated.contraindicated | contraindicated |  |
| sentence:contraindicated#63 | aef66295c36e | 191-219 | Monoamine Oxidase Inhibitor [EPC] | sentence | contraindicated | contraindicated.contraindicated | none | other-clause |
| sentence:contraindicated#64 | 4d716156c8cb | 4-32 | Monoamine Oxidase Inhibitor [EPC] | sentence | contraindicated | contraindicated.contraindicated | contraindicated |  |
| sentence:contraindicated#65 | 965cd41bedbf | 14382-14399 | Ergot Derivative [EPC] | sentence | contraindicated | contraindicated.contraindicated | contraindicated |  |
| sentence:contraindicated#66 | 0cc805a58796 | 4623-4638 | Sympathomimetics | sentence | contraindicated | contraindicated.contraindicated | contraindicated |  |
| sentence:contraindicated#67 | 8c2e0a0c18d0 | 16740-16759 | Sedatives/Hypnotics [TC] | sentence | contraindicated | contraindicated.contraindicated | contraindicated |  |
| sentence:contraindicated#68 | ea557af67c9f | 14463-14480 | Ergot Derivative [EPC] | sentence | contraindicated | contraindicated.contraindicated | contraindicated |  |
| sentence:contraindicated#69 | 71a8205a09cf | 9268-9287 | Hypoglycemic Agents | sentence | contraindicated | contraindicated.contraindicated | monitor | other-clause |
| sentence:contraindicated#70 | 7ebcb237ea66 | 1135-1163 | Monoamine Oxidase Inhibitor [EPC] | sentence | contraindicated | contraindicated.contraindicated | contraindicated |  |
| sentence:contraindicated#71 | 046144171043 | 14790-14807 | Ergot Derivative [EPC] | sentence | contraindicated | contraindicated.contraindicated | contraindicated |  |
| sentence:contraindicated#72 | 1e9093655907 | 5148-5163 | Antiarrhythmic [EPC] | sentence | contraindicated | contraindicated.contraindicated | contraindicated |  |
| sentence:contraindicated#73 | 08064e56e53c | 18232-18249 | Ergot Derivative [EPC] | sentence | contraindicated | contraindicated.contraindicated | contraindicated |  |
| sentence:contraindicated#74 | 4835ddc9642c | 429-457 | Monoamine Oxidase Inhibitor [EPC] | sentence | contraindicated | contraindicated.contraindicated | contraindicated |  |
| sentence:contraindicated#75 | bcc74397ab1f | 80-103 | CYP3A strong inhibitor [FDA-CYP] | sentence | contraindicated | contraindicated.contraindicated | contraindicated |  |
| sentence:contraindicated#76 | 5ca4fdd0cb3f | 1180-1227 | Non-Nucleoside Reverse Transcriptase Inhibitors [MoA] | sentence | contraindicated | contraindicated.contraindicated | contraindicated |  |
| sentence:contraindicated#77 | ab593e93bf19 | 14707-14724 | Ergot Derivative [EPC] | sentence | contraindicated | contraindicated.contraindicated | contraindicated |  |
| sentence:contraindicated#78 | ab4e9a8ff641 | 14917-14929 | Barbiturate [EPC] | sentence | contraindicated | contraindicated.contraindicated | contraindicated |  |
| sentence:contraindicated#79 | 25093a10cdff | 669-688 | Nitric Oxide Donors | sentence | contraindicated | contraindicated.contraindicated | contraindicated |  |
| sentence:contraindicated#80 | 39e1b556b9e9 | 17923-17940 | Ergot Derivative [EPC] | sentence | contraindicated | contraindicated.contraindicated | contraindicated |  |
| sentence:contraindicated#81 | 086aae80ed58 | 4918-4943 | Tricyclic Antidepressant [EPC] | sentence | contraindicated | contraindicated.contraindicated | none | other-clause |
| sentence:contraindicated#82 | f7da52d4acf0 | 14507-14524 | Ergot Derivative [EPC] | sentence | contraindicated | contraindicated.contraindicated | contraindicated |  |
| sentence:contraindicated#83 | 1f8a311d65ec | 4872-4887 | Anticonvulsants | sentence | contraindicated | contraindicated.contraindicated | contraindicated |  |
| sentence:contraindicated#84 | 0803951186b3 | 387-410 | HIV Protease Inhibitors | sentence | contraindicated | contraindicated.contraindicated | contraindicated |  |
| sentence:contraindicated#85 | 9ebcb7d0bdd1 | 320-357 | Soluble Guanylate Cyclase Stimulator [EPC] | sentence | contraindicated | contraindicated.contraindicated | contraindicated |  |
| sentence:contraindicated#86 | bdcfc934aeba | 1323-1346 | HIV Protease Inhibitors | sentence | contraindicated | contraindicated.contraindicated | contraindicated |  |
| sentence:contraindicated#87 | 787313e73e05 | 630-649 | Nitric Oxide Donors | sentence | contraindicated | contraindicated.contraindicated | contraindicated |  |
| sentence:contraindicated#88 | 5b350e6fe4c1 | 3453-3467 | Antiprotozoal [EPC] | sentence | contraindicated | contraindicated.contraindicated | none |  |
| sentence:contraindicated#89 | 2368145e91d0 | 295-316 | Plasminogen Activators [MoA] | sentence | contraindicated | contraindicated.contraindicated | none | merged-item |
| sentence:contraindicated#90 | d145433f273b | 5859-5878 | Protease Inhibitor [EPC] | sentence | contraindicated | contraindicated.contraindicated | none | other-clause |
| sentence:contraindicated#91 | f1870c6ca51d | 3519-3547 | HMG-CoA Reductase Inhibitor [EPC] | sentence | contraindicated | contraindicated.contraindicated | contraindicated |  |
| sentence:contraindicated#92 | 1e9093655907 | 15306-15333 | HMG-CoA Reductase Inhibitor [EPC] | sentence | contraindicated | contraindicated.contraindicated | contraindicated | members |
| sentence:contraindicated#93 | ee05c7b17325 | 9371-9389 | Antimycobacterial [EPC] | sentence | contraindicated | contraindicated.contraindicated | contraindicated |  |
| sentence:contraindicated#94 | 568c2caae7d5 | 2167-2188 | CYP3A strong inducer [FDA-CYP] | sentence | contraindicated | contraindicated.contraindicated | contraindicated |  |
| sentence:contraindicated#95 | c38f265d29df | 231-259 | Monoamine Oxidase Inhibitor [EPC] | sentence | contraindicated | contraindicated.contraindicated | contraindicated |  |
| sentence:contraindicated#96 | 5b350e6fe4c1 | 7092-7101 | Diuretics | sentence | contraindicated | contraindicated.contraindicated | contraindicated | members |
| sentence:contraindicated#97 | a755792ca29c | 14275-14302 | HMG-CoA Reductase Inhibitor [EPC] | sentence | contraindicated | contraindicated.contraindicated | contraindicated | members |
| sentence:contraindicated#98 | 17cdf8e28e6e | 1250-1278 | Monoamine Oxidase Inhibitor [EPC] | sentence | contraindicated | contraindicated.contraindicated | contraindicated |  |
| sentence:contraindicated#99 | bc7cbc8568bb | 16366-16385 | Protease Inhibitor [EPC] | sentence | contraindicated | contraindicated.contraindicated | none |  |
| sentence:contraindicated#100 | c214558ce714 | 623-632 | Alkaloid [EPC] | sentence | contraindicated | contraindicated.contraindicated | contraindicated |  |
| sentence:avoid#1 | db276f3ff211 | 9017-9034 | Opioid Analgesic [EPC] | sentence | avoid | avoid.avoid | avoid |  |
| sentence:avoid#2 | a01c4fe511c3 | 403-419 | Local Anesthetic [EPC] | sentence | avoid | avoid.do_not_use | none | merged-item |
| sentence:avoid#3 | 88c5cece1df2 | 10254-10282 | Monoamine Oxidase Inhibitor [EPC] | sentence | avoid | avoid.avoid | none | ctx:avoid,merged-item |
| sentence:avoid#4 | 8af20350f762 | 2676-2698 | HIV Protease Inhibitors | sentence | avoid | avoid.avoid | avoid | members |
| sentence:avoid#5 | fb9de44b1665 | 146-160 | Anticoagulants | sentence | avoid | avoid.not_recommended | none | other-clause |
| sentence:avoid#6 | ea1f2ad70c10 | 1769-1783 | Antiarrhythmic [EPC] | sentence | avoid | avoid.avoid | avoid |  |
| sentence:avoid#7 | 82616f7c5bcf | 623-671 | Serotonin and Norepinephrine Reuptake Inhibitor [EPC] | sentence | avoid | avoid.avoid | avoid |  |
| sentence:avoid#8 | 431d9e7fe800 | 5243-5259 | Muscle Relaxant [EPC] | sentence | avoid | avoid.avoid | avoid |  |
| sentence:avoid#9 | 798c8b90c22d | 610-618 | Oxytocic [EPC] | sentence | avoid | avoid.avoid | none | merged-item |
| sentence:avoid#10 | f3e49a45a840 | 13322-13331 | Analgesics | sentence | avoid | avoid.not_recommended | none |  |
| sentence:avoid#11 | 254881f5563b | 18-34 | Vasoconstrictor [EPC] | sentence | avoid | avoid.do_not_use | avoid |  |
| sentence:avoid#12 | 0377bb2b4089 | 236-258 | Proton Pump Inhibitor [EPC] | sentence | avoid | avoid.avoid | avoid |  |
| sentence:avoid#13 | 43eac69df6ab | 3149-3164 | Enzyme Inducers [MoA] | sentence | avoid | avoid.not_recommended | avoid |  |
| sentence:avoid#14 | 34c515073da8 | 8205-8216 | Anesthetics | sentence | avoid | avoid.avoid | adjust_dose |  |
| sentence:avoid#15 | cdc7c1248c52 | 930-944 | Glucocorticoids | sentence | avoid | avoid.not_recommended | none | other-clause |
| sentence:avoid#16 | f154f0978ef0 | 2011-2029 | Thiazide Diuretic [EPC] | sentence | avoid | avoid.avoid | avoid |  |
| sentence:avoid#17 | eacb57a3abd2 | 11099-11118 | Protease Inhibitor [EPC] | sentence | avoid | avoid.do_not_coadminister | adjust_dose |  |
| sentence:avoid#18 | 4e0f4719b7f6 | 1651-1670 | General Anesthetic [EPC] | sentence | avoid | avoid.avoid | avoid |  |
| sentence:avoid#19 | 958b4dbcdf09 | 11596-11643 | Non-Nucleoside Reverse Transcriptase Inhibitors [MoA] | sentence | avoid | avoid.not_recommended | none | other-clause |
| sentence:avoid#20 | 0f69279576e3 | 971-987 | Local Anesthetic [EPC] | sentence | avoid | avoid.avoid | avoid |  |
| sentence:avoid#21 | ded4f98d7140 | 2968-2986 | Protease Inhibitor [EPC] | sentence | avoid | avoid.not_recommended | adjust_dose |  |
| sentence:avoid#22 | ef090a4b70e5 | 1045-1054 | Progestin [EPC] | sentence | avoid | avoid.not_recommended | avoid |  |
| sentence:avoid#23 | 7b46af248f20 | 572-582 | Analgesics | sentence | avoid | avoid.avoid | avoid |  |
| sentence:avoid#24 | fce1aefc1709 | 2484-2503 | Adrenergic Agonists | sentence | avoid | avoid.not_recommended | avoid |  |
| sentence:avoid#25 | 907f0aac6f81 | 312-324 | Electrolyte [EPC] | sentence | avoid | avoid.avoid | avoid | subject |
| sentence:avoid#26 | 26166a968ec4 | 122-143 | CYP3A strong inducer [FDA-CYP] | sentence | avoid | avoid.avoid | avoid |  |
| sentence:avoid#27 | 96657a6897fe | 10292-10313 | CYP3A strong inducer [FDA-CYP] | sentence | avoid | avoid.do_not_use | avoid |  |
| sentence:avoid#28 | 283dd77b6f56 | 1344-1361 | Dopamine Agonists | sentence | avoid | avoid.do_not_use | none | merged-item |
| sentence:avoid#29 | 06ad82e5f938 | 1459-1481 | Proton Pump Inhibitor [EPC] | sentence | avoid | avoid.not_recommended | avoid |  |
| sentence:avoid#30 | 69633e2703a3 | 708-724 | Sympathomimetics | sentence | avoid | avoid.avoid | none | merged-item |
| sentence:avoid#31 | 0e042dcec6be | 3229-3251 | Adrenergic Antagonists | sentence | avoid | avoid.avoid | none | ctx:avoid,merged-item |
| sentence:avoid#32 | b4ca792b841f | 753-765 | Sulfonamide [EPC] | sentence | avoid | avoid.avoid | avoid |  |
| sentence:avoid#33 | 728866dbe22d | 680-695 | P-gp inhibitor [FDA-CYP] | sentence | avoid | avoid.avoid | avoid |  |
| sentence:avoid#34 | 548fca1389f6 | 1865-1880 | P-gp inhibitor [FDA-CYP] | sentence | avoid | avoid.avoid | avoid |  |
| sentence:avoid#35 | a0fd872ba64c | 925-939 | Loop Diuretic [EPC] | sentence | avoid | avoid.avoid | avoid |  |
| sentence:avoid#36 | 56e941599619 | 2550-2566 | Antiviral Agent [TC] | sentence | avoid | avoid.not_recommended | avoid | members |
| sentence:avoid#37 | 516e6de18a7e | 339-362 | CYP3A moderate inducer [FDA-CYP] | sentence | avoid | avoid.avoid | adjust_dose | ctx:avoid,merged-item |
| sentence:avoid#38 | 8d17c54b1a8e | 1261-1269 | Antacid [PE] | sentence | avoid | avoid.not_recommended | avoid |  |
| sentence:avoid#39 | c061e29d3106 | 2770-2788 | Protease Inhibitor [EPC] | sentence | avoid | avoid.avoid | avoid | members |
| sentence:avoid#40 | 0f814ea25ed6 | 3656-3673 | Local Anesthetic [EPC] | sentence | avoid | avoid.avoid | none | merged-item |
| sentence:avoid#41 | 9beae424d3b5 | 1300-1311 | Anesthetics | sentence | avoid | avoid.avoid | none | merged-item |
| sentence:avoid#42 | a4fb07fc25b0 | 103-121 | Thiazide Diuretic [EPC] | sentence | avoid | avoid.avoid | none | merged-item |
| sentence:avoid#43 | e5b60d4f1160 | 813-822 | Laxatives | sentence | avoid | avoid.do_not_use | avoid |  |
| sentence:avoid#44 | 5ddce1da5985 | 635-657 | HIV Protease Inhibitors | sentence | avoid | avoid.avoid | adjust_dose | merged-item |
| sentence:avoid#45 | d409a1917191 | 3252-3268 | Antiviral Agent [TC] | sentence | avoid | avoid.do_not_coadminister | avoid | members |
| sentence:avoid#46 | 8084b9a8c779 | 1427-1442 | P-gp inhibitor [FDA-CYP] | sentence | avoid | avoid.avoid | avoid |  |
| sentence:avoid#47 | e7bc69f21201 | 95-110 | Anticholinergic [EPC] | sentence | avoid | avoid.not_recommended | avoid |  |
| sentence:avoid#48 | 94abe89bf6bb | 551-565 | Antihistamine [EPC] | sentence | avoid | avoid.avoid | avoid |  |
| sentence:avoid#49 | 4a078d979210 | 236-243 | Insulin [EPC] | sentence | avoid | avoid.avoid | adjust_dose | merged-item |
| sentence:avoid#50 | 06988d44b7b0 | 886-900 | Anticonvulsants | sentence | avoid | avoid.avoid | adjust_dose | merged-item |
| sentence:adjust_dose#1 | 2118c22bb0e5 | 3081-3096 | Anticholinergic [EPC] | sentence | adjust_dose | adjust_dose.dose_adjustment | adjust_dose |  |
| sentence:adjust_dose#2 | 95371fa2e12b | 2879-2894 | Muscle Relaxant [EPC] | sentence | adjust_dose | adjust_dose.reduce_dose | adjust_dose |  |
| sentence:adjust_dose#3 | 40668b548e6e | 3444-3459 | Enzyme Inducers [MoA] | sentence | adjust_dose | adjust_dose.dose_adjustment | adjust_dose |  |
| sentence:adjust_dose#4 | 9321358173ad | 5874-5889 | Muscle Relaxant [EPC] | sentence | adjust_dose | adjust_dose.reduce_dose | adjust_dose |  |
| sentence:adjust_dose#5 | 7f9b1734b2d7 | 5527-5542 | Muscle Relaxant [EPC] | sentence | adjust_dose | adjust_dose.reduce_dose | adjust_dose |  |
| sentence:adjust_dose#6 | c1825ddbc29c | 890-898 | Diuretics | sentence | adjust_dose | adjust_dose.dose_adjustment | none | effect-adjective |
| sentence:adjust_dose#7 | cd16a0abdf1d | 2002-2009 | Insulin [EPC] | sentence | adjust_dose | adjust_dose.dose_adjustment | adjust_dose |  |
| sentence:adjust_dose#8 | 8afe097e3320 | 199-207 | Diuretics | sentence | adjust_dose | adjust_dose.dose_adjustment | none |  |
| sentence:adjust_dose#9 | 4c1986560a71 | 1843-1850 | Insulin [EPC] | sentence | adjust_dose | adjust_dose.dose_adjustment | adjust_dose |  |
| sentence:adjust_dose#10 | 63be5f2b5560 | 164-183 | Hypoglycemic Agents | sentence | adjust_dose | adjust_dose.dose_adjustment | adjust_dose |  |
| sentence:adjust_dose#11 | 55defe2b3faa | 1482-1504 | Proton Pump Inhibitor [EPC] | sentence | adjust_dose | adjust_dose.reduce_dose | adjust_dose |  |
| sentence:adjust_dose#12 | 939a4f1478cc | 457-465 | Estrogen [EPC] | sentence | adjust_dose | adjust_dose.dose_adjustment | monitor | merged-item |
| sentence:adjust_dose#13 | 3f72fa0edddc | 2440-2465 | Tricyclic Antidepressant [EPC] | sentence | adjust_dose | adjust_dose.dose_adjustment | adjust_dose |  |
| sentence:adjust_dose#14 | 195bf66fb552 | 2421-2439 | OATP1B3 substrate [FDA-CYP] | sentence | adjust_dose | adjust_dose.reduce_dose | adjust_dose |  |
| sentence:adjust_dose#15 | 55ee897f885f | 8019-8042 | Calcium Channel Blocker [EPC] | sentence | adjust_dose | adjust_dose.dose_adjustment | adjust_dose |  |
| sentence:adjust_dose#16 | 0287f31f105c | 5593-5611 | Thiazide Diuretic [EPC] | sentence | adjust_dose | adjust_dose.dose_adjustment | adjust_dose | subject |
| sentence:adjust_dose#17 | 18c3026834f5 | 6785-6801 | Muscle Relaxant [EPC] | sentence | adjust_dose | adjust_dose.reduce_dose | adjust_dose |  |
| sentence:adjust_dose#18 | 7e4898254c09 | 862-887 | CYP2C8 moderate inhibitor [FDA-CYP] | sentence | adjust_dose | adjust_dose.dose_adjustment | adjust_dose |  |
| sentence:adjust_dose#19 | 0225c133cf57 | 2845-2860 | Muscle Relaxant [EPC] | sentence | adjust_dose | adjust_dose.reduce_dose | adjust_dose |  |
| sentence:adjust_dose#20 | c70372c0d5f1 | 57-74 | Phosphate Binder [EPC] | sentence | adjust_dose | adjust_dose.reduce_dose | adjust_dose | other-clause |
| sentence:adjust_dose#21 | 06024622569d | 4191-4200 | Diuretics | sentence | adjust_dose | adjust_dose.dose_adjustment | none | merged-item |
| sentence:adjust_dose#22 | 31c715996975 | 80-98 | Thiazide Diuretic [EPC] | sentence | adjust_dose | adjust_dose.dose_adjustment | adjust_dose | subject |
| sentence:adjust_dose#23 | 0aae2d72d23a | 5191-5213 | CYP3A strong inhibitor [FDA-CYP] | sentence | adjust_dose | adjust_dose.dose_adjustment | none | other-clause |
| sentence:adjust_dose#24 | bdcc932950c8 | 2455-2462 | Insulin [EPC] | sentence | adjust_dose | adjust_dose.dose_adjustment | adjust_dose |  |
| sentence:adjust_dose#25 | 3497c4d8aae7 | 2647-2659 | Barbiturate [EPC] | sentence | adjust_dose | adjust_dose.dose_adjustment | adjust_dose |  |
| sentence:adjust_dose#26 | a416edaa5522 | 5435-5458 | Calcium Channel Blocker [EPC] | sentence | adjust_dose | adjust_dose.dose_adjustment | adjust_dose |  |
| sentence:adjust_dose#27 | 1b47b8cd3fdf | 213-240 | CYP2C19 moderate inhibitor [FDA-CYP] | sentence | adjust_dose | adjust_dose.dose_adjustment | adjust_dose |  |
| sentence:adjust_dose#28 | b7c2b3139027 | 4102-4120 | Thiazide Diuretic [EPC] | sentence | adjust_dose | adjust_dose.dose_adjustment | adjust_dose | subject |
| sentence:adjust_dose#29 | cacbf0586ed0 | 10794-10812 | Antimycobacterial [EPC] | sentence | adjust_dose | adjust_dose.reduce_dose | adjust_dose |  |
| sentence:adjust_dose#30 | 07ad05d897bd | 488-501 | Anticoagulants | sentence | adjust_dose | adjust_dose.dose_adjustment | adjust_dose |  |
| sentence:adjust_dose#31 | c1825ddbc29c | 4585-4603 | Thiazide Diuretic [EPC] | sentence | adjust_dose | adjust_dose.dose_adjustment | adjust_dose | subject |
| sentence:adjust_dose#32 | 11a1347ec232 | 7660-7675 | Muscle Relaxant [EPC] | sentence | adjust_dose | adjust_dose.reduce_dose | adjust_dose |  |
| sentence:adjust_dose#33 | 5b36fc7f5f70 | 6276-6291 | Muscle Relaxant [EPC] | sentence | adjust_dose | adjust_dose.reduce_dose | adjust_dose |  |
| sentence:adjust_dose#34 | 1627bcbc46c3 | 4492-4538 | Non-Nucleoside Reverse Transcriptase Inhibitors [MoA] | sentence | adjust_dose | adjust_dose.dose_adjustment | adjust_dose |  |
| sentence:adjust_dose#35 | ce6739e56c89 | 1785-1803 | Thiazide Diuretic [EPC] | sentence | adjust_dose | adjust_dose.dose_adjustment | adjust_dose |  |
| sentence:adjust_dose#36 | fcd8e572df65 | 690-705 | Benzodiazepine [EPC] | sentence | adjust_dose | adjust_dose.reduce_dose | none | ctx:adjust_dose,other-clause |
| sentence:adjust_dose#37 | 5f0479725510 | 286-293 | Insulin [EPC] | sentence | adjust_dose | adjust_dose.dose_adjustment | adjust_dose |  |
| sentence:adjust_dose#38 | 754c32068134 | 3010-3017 | Insulin [EPC] | sentence | adjust_dose | adjust_dose.dose_adjustment | adjust_dose |  |
| sentence:adjust_dose#39 | 094b33fba5cc | 459-467 | Estrogen [EPC] | sentence | adjust_dose | adjust_dose.dose_adjustment | monitor |  |
| sentence:adjust_dose#40 | ddff44a21332 | 1508-1523 | Glucocorticoids | sentence | adjust_dose | adjust_dose.dose_adjustment | adjust_dose |  |
| sentence:adjust_dose#41 | 355a965a86ac | 179-188 | Narcotics | sentence | adjust_dose | adjust_dose.dose_adjustment | none | merged-item |
| sentence:adjust_dose#42 | 837aa579a016 | 7716-7731 | Muscle Relaxant [EPC] | sentence | adjust_dose | adjust_dose.reduce_dose | adjust_dose |  |
| sentence:adjust_dose#43 | cd8524fb6e06 | 411-433 | Proton Pump Inhibitor [EPC] | sentence | adjust_dose | adjust_dose.reduce_dose | adjust_dose |  |
| sentence:adjust_dose#44 | df02b562e8de | 3809-3828 | Protease Inhibitor [EPC] | sentence | adjust_dose | adjust_dose.reduce_dose | adjust_dose |  |
| sentence:adjust_dose#45 | fc4a8f3c4961 | 465-480 | Sympathomimetics | sentence | adjust_dose | adjust_dose.dose_adjustment | adjust_dose |  |
| sentence:adjust_dose#46 | 07ea9907db75 | 457-465 | Estrogen [EPC] | sentence | adjust_dose | adjust_dose.dose_adjustment | monitor |  |
| sentence:adjust_dose#47 | ceff6e34d9ad | 514-529 | Anticholinergic [EPC] | sentence | adjust_dose | adjust_dose.dose_adjustment | adjust_dose |  |
| sentence:adjust_dose#48 | 249bad7d66f4 | 3124-3139 | Muscle Relaxant [EPC] | sentence | adjust_dose | adjust_dose.reduce_dose | adjust_dose |  |
| sentence:adjust_dose#49 | bff901c66ade | 225-232 | Insulin [EPC] | sentence | adjust_dose | adjust_dose.dose_adjustment | adjust_dose |  |
| sentence:adjust_dose#50 | 217ad6fe5238 | 3055-3070 | Muscle Relaxant [EPC] | sentence | adjust_dose | adjust_dose.reduce_dose | adjust_dose |  |
| sentence:monitor#1 | ef908bd30dce | 8472-8480 | Diuretics | sentence | monitor | monitor.monitor | monitor |  |
| sentence:monitor#2 | a964d0c04a6e | 4441-4448 | Insulin [EPC] | sentence | monitor | monitor.monitor | monitor |  |
| sentence:monitor#3 | 4b52b0d0f1ad | 1827-1866 | Selective Serotonin Reuptake Inhibitors | sentence | monitor | monitor.monitor | monitor |  |
| sentence:monitor#4 | da376da4e738 | 1989-2001 | Sulfonylurea [EPC] | sentence | monitor | monitor.monitor | adjust_dose | under-read |
| sentence:monitor#5 | 84daf3e84a25 | 11390-11404 | Anticoagulants | sentence | monitor | monitor.monitor | monitor |  |
| sentence:monitor#6 | a7db5d9a6453 | 9964-9973 | Diuretics | sentence | monitor | monitor.monitor | none | ctx:monitor,merged-item |
| sentence:monitor#7 | 024ecb87b2d0 | 1723-1762 | Selective Serotonin Reuptake Inhibitors | sentence | monitor | monitor.monitor | monitor |  |
| sentence:monitor#8 | f4078f81f740 | 912-946 | Norepinephrine Reuptake Inhibitor [EPC] | sentence | monitor | monitor.monitor | monitor |  |
| sentence:monitor#9 | b60bacf8771d | 3370-3388 | Protease Inhibitor [EPC] | sentence | monitor | monitor.monitor | adjust_dose | under-read |
| sentence:monitor#10 | 086aae80ed58 | 5913-5926 | Anticoagulants | sentence | monitor | monitor.monitor | monitor |  |
| sentence:monitor#11 | 6719c41b08d9 | 10945-10960 | Benzodiazepine [EPC] | sentence | monitor | monitor.monitor | monitor | ctx:adjust_dose |
| sentence:monitor#12 | 66035b34166a | 11538-11551 | Sulfonylurea [EPC] | sentence | monitor | monitor.monitor | none | merged-item |
| sentence:monitor#13 | 4a1046ed0ec9 | 3402-3416 | Anticoagulants | sentence | monitor | monitor.monitor | monitor |  |
| sentence:monitor#14 | a0a540a36cf3 | 4289-4296 | Insulin [EPC] | sentence | monitor | monitor.monitor | adjust_dose | under-read |
| sentence:monitor#15 | fab524e82af8 | 6236-6258 | HIV Protease Inhibitors | sentence | monitor | monitor.monitor | adjust_dose | under-read |
| sentence:monitor#16 | a6e77c4f4f4c | 8254-8268 | Benzodiazepine [EPC] | sentence | monitor | monitor.monitor | monitor |  |
| sentence:monitor#17 | ba8199f065ab | 846-885 | Selective Serotonin Reuptake Inhibitors | sentence | monitor | monitor.monitor | monitor |  |
| sentence:monitor#18 | a416edaa5522 | 3463-3477 | Anticonvulsants | sentence | monitor | monitor.monitor | monitor |  |
| sentence:monitor#19 | 081383fbb82f | 7885-7909 | Calcium Channel Blocker [EPC] | sentence | monitor | monitor.monitor | monitor | ctx:adjust_dose |
| sentence:monitor#20 | 483488f24f1c | 13904-13929 | Tricyclic Antidepressant [EPC] | sentence | monitor | monitor.monitor | monitor |  |
| sentence:monitor#21 | eb1cbf7ebd34 | 3459-3481 | HIV Protease Inhibitors | sentence | monitor | monitor.monitor | adjust_dose | under-read |
| sentence:monitor#22 | e2982acff4f3 | 2016-2024 | Diuretics | sentence | monitor | monitor.observe_closely | monitor |  |
| sentence:monitor#23 | d6de9f1ccb6e | 7779-7794 | Anticonvulsants | sentence | monitor | monitor.monitor | adjust_dose | under-read |
| sentence:monitor#24 | 59d631658dd1 | 1443-1457 | Catecholamine [EPC] | sentence | monitor | monitor.observe_closely | monitor |  |
| sentence:monitor#25 | 675e39c8775b | 1438-1452 | Catecholamine [EPC] | sentence | monitor | monitor.observe_closely | monitor |  |
| sentence:monitor#26 | 57f379717b36 | 7062-7101 | Selective Serotonin Reuptake Inhibitors | sentence | monitor | monitor.monitor | monitor |  |
| sentence:monitor#27 | 5931f34c3718 | 6825-6834 | Diuretics | sentence | monitor | monitor.monitor | none | ctx:monitor |
| sentence:monitor#28 | 12f0db6547e8 | 1577-1585 | Diuretics | sentence | monitor | monitor.observe_closely | monitor |  |
| sentence:monitor#29 | 74ca93551f8f | 3360-3373 | Anticoagulants | sentence | monitor | monitor.monitor | monitor |  |
| sentence:monitor#30 | 94f75b832e45 | 2924-2938 | Anti-coagulant [EPC] | sentence | monitor | monitor.monitor | monitor |  |
| sentence:monitor#31 | b04a31bed642 | 7739-7754 | Anticonvulsants | sentence | monitor | monitor.monitor | monitor |  |
| sentence:monitor#32 | af24bae46fb4 | 1315-1332 | Cardiac Glycoside [EPC] | sentence | monitor | monitor.monitor | none | ctx:monitor,other-clause |
| sentence:monitor#33 | 5a337672ef4a | 7281-7299 | Thiazide Diuretic [EPC] | sentence | monitor | monitor.monitor | none | ctx:monitor |
| sentence:monitor#34 | 6aeb50a0e765 | 3494-3509 | Antiarrhythmic [EPC] | sentence | monitor | monitor.monitor | monitor |  |
| sentence:monitor#35 | c4d0df8828c8 | 812-826 | Anticoagulants | sentence | monitor | monitor.monitor | monitor |  |
| sentence:monitor#36 | f642b549876f | 15980-16020 | Dihydropyridine Calcium Channel Blocker [EPC] | sentence | monitor | monitor.monitor | monitor | ctx:adjust_dose |
| sentence:monitor#37 | f0c39225284f | 1689-1697 | Diuretics | sentence | monitor | monitor.observe_closely | monitor |  |
| sentence:monitor#38 | f60c42da4e42 | 644-658 | Catecholamine [EPC] | sentence | monitor | monitor.observe_closely | monitor |  |
| sentence:monitor#39 | bf713419bba2 | 5079-5087 | Diuretics | sentence | monitor | monitor.observe_closely | monitor |  |
| sentence:monitor#40 | 9207acf69fa0 | 7584-7603 | Hypoglycemic Agents | sentence | monitor | monitor.monitor | adjust_dose | under-read |
| sentence:monitor#41 | 2b901d7070e4 | 1617-1631 | Antiarrhythmic [EPC] | sentence | monitor | monitor.monitor | adjust_dose | under-read |
| sentence:monitor#42 | 96696abab443 | 8595-8603 | Diuretics | sentence | monitor | monitor.monitor | monitor |  |
| sentence:monitor#43 | eacf527b2992 | 6451-6469 | Thiazide Diuretic [EPC] | sentence | monitor | monitor.monitor | none | ctx:monitor |
| sentence:monitor#44 | 44b2a93f84ee | 7813-7847 | Norepinephrine Reuptake Inhibitor [EPC] | sentence | monitor | monitor.monitor | monitor |  |
| sentence:monitor#45 | ccadc61c07c0 | 686-708 | Proton Pump Inhibitor [EPC] | sentence | monitor | monitor.monitor | monitor |  |
| sentence:monitor#46 | 68bfa68f5b79 | 542-557 | Anti-coagulant [EPC] | sentence | monitor | monitor.monitor | monitor | ctx:adjust_dose |
| sentence:monitor#47 | d1eb6c456b19 | 11416-11430 | Anticoagulants | sentence | monitor | monitor.monitor | monitor |  |
| sentence:monitor#48 | d5ff70ef22b6 | 7634-7668 | Norepinephrine Reuptake Inhibitor [EPC] | sentence | monitor | monitor.monitor | monitor |  |
| sentence:monitor#49 | 042aeec26a4d | 7777-7805 | HMG-CoA Reductase Inhibitor [EPC] | sentence | monitor | monitor.monitor | adjust_dose | under-read |
| sentence:monitor#50 | 6786d0700050 | 6306-6321 | Anticholinergic [EPC] | sentence | monitor | monitor.monitor | monitor |  |
| sentence:not_read#1 | 6d6697532326 | 7250-7265 | Benzodiazepine [EPC] | sentence | not_read | None | none |  |
| sentence:not_read#2 | 34fa875661d1 | 1116-1135 | Sedatives/Hypnotics [TC] | sentence | not_read | None | none |  |
| sentence:not_read#3 | 385b35f86b5b | 2241-2250 | Estrogen [EPC] | sentence | not_read | None | none |  |
| sentence:not_read#4 | 5572925e70d0 | 482-491 | Macrolide [EPC] | sentence | not_read | None | none | ctx:avoid |
| sentence:not_read#5 | f6f2f3302051 | 227-241 | Phenothiazine [EPC] | sentence | not_read | None | none | ctx:monitor |
| sentence:not_read#6 | 9e4bf17af9b6 | 797-812 | Benzodiazepine [EPC] | sentence | not_read | None | none |  |
| sentence:not_read#7 | 60c6e4327258 | 1093-1112 | Ophthalmic Solutions | sentence | not_read | None | none |  |
| sentence:not_read#8 | 8300ab7185c2 | 5407-5423 | Muscle Relaxant [EPC] | sentence | not_read | None | none | ctx:adjust_dose |
| sentence:not_read#9 | e9aef078b18b | 7898-7925 | Thyroid Stimulating Hormone [EPC] | sentence | not_read | None | none |  |
| sentence:not_read#10 | b8afa765c0a1 | 243-257 | Anticoagulants | sentence | not_read | None | none |  |
| sentence:not_read#11 | 0bd38eafcd5c | 20-29 | Diuretics | sentence | not_read | None | none |  |
| sentence:not_read#12 | 0b070ced2563 | 4440-4448 | Diuretics | sentence | not_read | None | none | ctx:monitor |
| sentence:not_read#13 | c8d79a357963 | 1014-1025 | Anesthetics | sentence | not_read | None | none |  |
| sentence:not_read#14 | 5a3b91f6d06f | 3297-3308 | Anxiolytics [TC] | sentence | not_read | None | none |  |
| sentence:not_read#15 | 7b4c8161308a | 632-660 | Monoamine Oxidase Inhibitor [EPC] | sentence | not_read | None | none | ctx:contraindicated,merged-item |
| sentence:not_read#16 | 33505e713fb4 | 1939-1948 | Diuretics | sentence | not_read | None | none | ctx:monitor |
| sentence:not_read#17 | a26c72b07014 | 5137-5148 | Anxiolytics [TC] | sentence | not_read | None | none |  |
| sentence:not_read#18 | 3c9e2b08cdf8 | 3067-3092 | Tricyclic Antidepressant [EPC] | sentence | not_read | None | adjust_dose | under-read |
| sentence:not_read#19 | de6d545f3429 | 3375-3414 | Selective Serotonin Reuptake Inhibitors | sentence | not_read | None | none | ctx:monitor |
| sentence:not_read#20 | 5edb7ee2a335 | 11807-11829 | CYP3A strong inhibitor [FDA-CYP] | sentence | not_read | None | none |  |
| sentence:not_read#21 | d11f416392c1 | 3988-4004 | P-gp inhibitor [FDA-CYP] | sentence | not_read | None | none | ctx:adjust_dose |
| sentence:not_read#22 | a642e35dc22e | 4537-4548 | Anxiolytics [TC] | sentence | not_read | None | none | ctx:avoid |
| sentence:not_read#23 | a2ee880b830f | 1039-1056 | Opioid Analgesic [EPC] | sentence | not_read | None | monitor | under-read |
| sentence:not_read#24 | 7a885dc8c7f8 | 865-880 | Corticosteroid [EPC] | sentence | not_read | None | none | ctx:adjust_dose |
| sentence:not_read#25 | f03e2df6c0eb | 1201-1215 | Anticoagulants | sentence | not_read | None | none | ctx:monitor |
| sentence:not_read#26 | 4d1c65958a38 | 2881-2888 | Insulin [EPC] | sentence | not_read | None | none | ctx:adjust_dose,other-clause |
| sentence:not_read#27 | 20003262431b | 1451-1459 | Antacid [PE] | sentence | not_read | None | none | ctx:adjust_dose |
| sentence:not_read#28 | aa966bc57b59 | 470-492 | Neuromuscular Blockade [PE] | sentence | not_read | None | none | ctx:monitor |
| sentence:not_read#29 | 67c1b2fb85a7 | 48-63 | Benzodiazepine [EPC] | sentence | not_read | None | none |  |
| sentence:not_read#30 | 6d5ec089765a | 8744-8759 | Corticosteroid [EPC] | sentence | not_read | None | none |  |
| sentence:not_read#31 | 15d384479f5a | 6779-6795 | Muscle Relaxant [EPC] | sentence | not_read | None | none | ctx:monitor |
| sentence:not_read#32 | 84d36cc129a3 | 61-68 | Insulin [EPC] | sentence | not_read | None | none |  |
| sentence:not_read#33 | fcee6f9a3f77 | 1633-1652 | Hypoglycemic Agents | sentence | not_read | None | none |  |
| sentence:not_read#34 | bd714d018c87 | 1234-1242 | Diuretics | sentence | not_read | None | none |  |
| sentence:not_read#35 | 3bef945cd7f8 | 828-843 | Aminoglycoside [EPC] | sentence | not_read | None | none | ctx:avoid |
| sentence:not_read#36 | eacf527b2992 | 332-346 | Anticoagulants | sentence | not_read | None | none |  |
| sentence:not_read#37 | c3b161417c3a | 546-560 | Antidiarrheal [EPC] | sentence | not_read | None | none |  |
| sentence:not_read#38 | 331c92237a15 | 12861-12869 | Antacid [PE] | sentence | not_read | None | avoid | under-read |
| sentence:not_read#39 | 5c4ae88d2937 | 884-898 | Anticoagulants | sentence | not_read | None | none |  |
| sentence:not_read#40 | 5bcf62697819 | 1081-1090 | Estrogen [EPC] | sentence | not_read | None | none |  |
| sentence:not_read#41 | f5616eb9d0b2 | 6888-6917 | Neuromuscular Blocking Agents | sentence | not_read | None | none | ctx:adjust_dose |
| sentence:not_read#42 | ebc5e8549994 | 229-243 | Anticoagulants | sentence | not_read | None | none |  |
| sentence:not_read#43 | d4b6007986df | 507-522 | Enzyme Inducers [MoA] | sentence | not_read | None | monitor | under-read |
| sentence:not_read#44 | a11e590c1a0e | 3260-3276 | Muscle Relaxant [EPC] | sentence | not_read | None | none |  |
| sentence:not_read#45 | 31d484429218 | 1166-1180 | Enzyme Inducers [MoA] | sentence | not_read | None | none |  |
| sentence:not_read#46 | df4759288d7a | 2684-2698 | Anticoagulants | sentence | not_read | None | monitor | under-read |
| sentence:not_read#47 | b963c550950a | 295-305 | Progestin [EPC] | sentence | not_read | None | none |  |
| sentence:not_read#48 | 255f38a4bd55 | 116-129 | Anticoagulants | sentence | not_read | None | adjust_dose | under-read |
| sentence:not_read#49 | df450adb9924 | 2151-2179 | Monoamine Oxidase Inhibitor [EPC] | sentence | not_read | None | adjust_dose | ctx:avoid,under-read |
| sentence:not_read#50 | efa50319cfdd | 1042-1069 | HMG-CoA Reductase Inhibitor [EPC] | sentence | not_read | None | none |  |
| heading_block:contraindicated#1 | e7fff3119b78 | 670-695 | CYP2D6 moderate inhibitor [FDA-CYP] | heading_block | contraindicated | contraindicated.contraindicated | none | subject |
| heading_block:contraindicated#2 | 7c55b458e68d | 356-386 | Monoamine Oxidase-A Inhibitors [MoA] | heading_block | contraindicated | contraindicated.contraindicated | contraindicated |  |
| heading_block:contraindicated#3 | 5ea487cd1a34 | 348-378 | Monoamine Oxidase-A Inhibitors [MoA] | heading_block | contraindicated | contraindicated.contraindicated | contraindicated |  |
| heading_block:contraindicated#4 | 825a202d19bd | 4660-4674 | Phenothiazine [EPC] | heading_block | contraindicated | contraindicated.contraindicated | avoid | merged-item |
| heading_block:contraindicated#5 | 825a202d19bd | 4645-4659 | Butyrophenone [EPC] | heading_block | contraindicated | contraindicated.contraindicated | avoid |  |
| heading_block:contraindicated#6 | e6d3f3b48a5a | 3704-3732 | Monoamine Oxidase Inhibitor [EPC] | heading_block | contraindicated | contraindicated.contraindicated | contraindicated |  |
| heading_block:contraindicated#7 | cfd0f517df51 | 539-582 | Nucleoside Reverse Transcriptase Inhibitors [MoA] | heading_block | contraindicated | contraindicated.contraindicated | contraindicated | members |
| heading_block:contraindicated#8 | 081383fbb82f | 3742-3751 | Alkaloid [EPC] | heading_block | contraindicated | contraindicated.contraindicated | contraindicated |  |
| heading_block:contraindicated#9 | f8e990285805 | 3671-3680 | Alkaloid [EPC] | heading_block | contraindicated | contraindicated.contraindicated | contraindicated |  |
| heading_block:contraindicated#10 | f8e990285805 | 3699-3708 | Alkaloid [EPC] | heading_block | contraindicated | contraindicated.contraindicated | contraindicated |  |
| heading_block:avoid#1 | 3f4ba630fe12 | 2328-2347 | Protease Inhibitor [EPC] | heading_block | avoid | avoid.do_not_coadminister | avoid | members |
| heading_block:avoid#2 | 06e1a72e96cf | 1744-1759 | Anticholinergic [EPC] | heading_block | avoid | avoid.not_recommended | avoid |  |
| heading_block:avoid#3 | 9aafad9b78e9 | 1066-1075 | Diuretics | heading_block | avoid | avoid.avoid | avoid |  |
| heading_block:avoid#4 | 1b5b3c79406c | 1715-1724 | Vitamin A [EPC] | heading_block | avoid | avoid.avoid | avoid |  |
| heading_block:avoid#5 | 695f64626210 | 3240-3257 | Opioid Analgesic [EPC] | heading_block | avoid | avoid.not_recommended | avoid | subject |
| heading_block:avoid#6 | a8ba332d3826 | 5215-5231 | Muscle Relaxant [EPC] | heading_block | avoid | avoid.avoid | avoid |  |
| heading_block:avoid#7 | d96d57f86530 | 600-615 | Anticholinergic [EPC] | heading_block | avoid | avoid.avoid | avoid |  |
| heading_block:avoid#8 | 4170fe5e0dc5 | 228-255 | Potassium-sparing Diuretic [EPC] | heading_block | avoid | avoid.avoid | avoid |  |
| heading_block:avoid#9 | 79d5504c83a9 | 5052-5077 | Tricyclic Antidepressant [EPC] | heading_block | avoid | avoid.avoid | avoid |  |
| heading_block:avoid#10 | bd0f4db27cd6 | 24-42 | Cardiac Glycoside [EPC] | heading_block | avoid | avoid.avoid | avoid |  |
| heading_block:avoid#11 | b2d9f0312446 | 260-286 | Potassium-sparing Diuretic [EPC] | heading_block | avoid | avoid.avoid | avoid |  |
| heading_block:avoid#12 | 8a7b1db12467 | 1711-1725 | Glucocorticoids | heading_block | avoid | avoid.avoid | adjust_dose |  |
| heading_block:avoid#13 | 4541bf571256 | 459-482 | CYP3A strong inhibitor [FDA-CYP] | heading_block | avoid | avoid.avoid | avoid |  |
| heading_block:avoid#14 | 9b14d0c278ac | 605-620 | Methylxanthine [EPC] | heading_block | avoid | avoid.avoid | avoid |  |
| heading_block:avoid#15 | cf4484e280dc | 1727-1742 | Anticholinergic [EPC] | heading_block | avoid | avoid.not_recommended | avoid |  |
| heading_block:adjust_dose#1 | aa0e256f6cbe | 790-829 | Selective Serotonin Reuptake Inhibitors | heading_block | adjust_dose | adjust_dose.dose_adjustment | adjust_dose |  |
| heading_block:adjust_dose#2 | 256f6ad06bc1 | 1336-1345 | Estrogen [EPC] | heading_block | adjust_dose | adjust_dose.dose_adjustment | adjust_dose |  |
| heading_block:adjust_dose#3 | f8fd970e1c21 | 8055-8079 | Calcium Channel Blocker [EPC] | heading_block | adjust_dose | adjust_dose.reduce_dose | adjust_dose |  |
| heading_block:adjust_dose#4 | 6b71467d0a73 | 411-441 | Cytochrome P450 1A2 Inhibitors [MoA] | heading_block | adjust_dose | adjust_dose.dose_adjustment | adjust_dose |  |
| heading_block:adjust_dose#5 | 256f6ad06bc1 | 469-499 | Cytochrome P450 1A2 Inhibitors [MoA] | heading_block | adjust_dose | adjust_dose.dose_adjustment | adjust_dose |  |
| heading_block:adjust_dose#6 | 26f4520b07fb | 891-907 | Sympathomimetics | heading_block | adjust_dose | adjust_dose.dose_adjustment | adjust_dose |  |
| heading_block:adjust_dose#7 | 242771cf79bc | 1717-1740 | Antihypertensive Agents | heading_block | adjust_dose | adjust_dose.dose_adjustment | adjust_dose |  |
| heading_block:adjust_dose#8 | d409d663c8d4 | 919-935 | Sympathomimetics | heading_block | adjust_dose | adjust_dose.dose_adjustment | adjust_dose |  |
| heading_block:adjust_dose#9 | 27071c265f6d | 811-820 | Estrogen [EPC] | heading_block | adjust_dose | adjust_dose.dose_adjustment | adjust_dose |  |
| heading_block:adjust_dose#10 | 999fd9850809 | 1828-1842 | P-gp inhibitor [FDA-CYP] | heading_block | adjust_dose | adjust_dose.dose_adjustment | adjust_dose |  |
| heading_block:adjust_dose#11 | ad19b5c4c83a | 8549-8578 | Neuromuscular Blocking Agents | heading_block | adjust_dose | adjust_dose.reduce_dose | adjust_dose |  |
| heading_block:adjust_dose#12 | 86528f150785 | 2275-2284 | Estrogen [EPC] | heading_block | adjust_dose | adjust_dose.dose_adjustment | adjust_dose |  |
| heading_block:adjust_dose#13 | f1870c6ca51d | 7998-8022 | Calcium Channel Blocker [EPC] | heading_block | adjust_dose | adjust_dose.reduce_dose | adjust_dose |  |
| heading_block:adjust_dose#14 | d19aeaa3727f | 6400-6415 | Enzyme Inducers [MoA] | heading_block | adjust_dose | adjust_dose.reduce_dose | adjust_dose |  |
| heading_block:adjust_dose#15 | f8fd970e1c21 | 16114-16138 | Calcium Channel Blocker [EPC] | heading_block | adjust_dose | adjust_dose.reduce_dose | adjust_dose |  |
| heading_block:monitor#1 | e072a029ede9 | 3942-3966 | Calcium Channel Blocker [EPC] | heading_block | monitor | monitor.monitor | monitor |  |
| heading_block:monitor#2 | c37de9e12688 | 1052-1079 | Cyclooxygenase 2 Inhibitors | heading_block | monitor | monitor.monitor | monitor |  |
| heading_block:monitor#3 | 948217a49905 | 4233-4247 | Anticoagulants | heading_block | monitor | monitor.monitor | monitor |  |
| heading_block:monitor#4 | 23902efa2e01 | 1331-1340 | Diuretics | heading_block | monitor | monitor.monitor | monitor | subject |
| heading_block:monitor#5 | f42a5825389b | 1037-1061 | Anti-inflammatory Agent [TC] | heading_block | monitor | monitor.monitor | monitor |  |
| heading_block:monitor#6 | 9cdfd43114c3 | 1398-1414 | Aminosalicylate [EPC] | heading_block | monitor | monitor.monitor | adjust_dose | under-read |
| heading_block:monitor#7 | 6b06a7b675ee | 466-475 | Diuretics | heading_block | monitor | monitor.monitor | adjust_dose | under-read |
| heading_block:monitor#8 | 5b096dd6926d | 858-872 | Anticoagulants | heading_block | monitor | monitor.monitor | monitor |  |
| heading_block:monitor#9 | 938494421560 | 1125-1134 | Vitamin A [EPC] | heading_block | monitor | monitor.monitor | monitor |  |
| heading_block:monitor#10 | 9b150bab57a3 | 1450-1474 | Anti-inflammatory Agent [TC] | heading_block | monitor | monitor.monitor | monitor |  |
| heading_block:monitor#11 | bcac9c256d61 | 7401-7427 | Tyrosine Kinase Inhibitors | heading_block | monitor | monitor.monitor | monitor |  |
| heading_block:monitor#12 | a473a1876229 | 995-1010 | Aminoglycoside [EPC] | heading_block | monitor | monitor.monitor | adjust_dose | under-read |
| heading_block:monitor#13 | 050dba7d21a4 | 870-897 | Cyclooxygenase 2 Inhibitors | heading_block | monitor | monitor.monitor | monitor |  |
| heading_block:monitor#14 | 9626cd104562 | 610-624 | Anticoagulants | heading_block | monitor | monitor.monitor | adjust_dose | under-read |
| heading_block:monitor#15 | 393d10b610d9 | 386-395 | Diuretics | heading_block | monitor | monitor.monitor | adjust_dose | under-read |

</details>

<details><summary>Per-span readings, draw 2 (run 13, after iteration 1)</summary>

| id | text_key | span | class | scope | machine | rule | hand | flag |
|---|---|---|---|---|---|---|---|---|
| sentence:avoid#1 | db0d730d51fb | 249-258 | Narcotics | sentence | avoid | avoid.avoid | avoid |  |
| sentence:avoid#2 | cee5f1f4a583 | 405-421 | Muscle Relaxant [EPC] | sentence | avoid | avoid.not_recommended | avoid |  |
| sentence:avoid#3 | 5ac5ead72aeb | 826-844 | Cardiac Glycoside [EPC] | sentence | avoid | avoid.avoid | avoid |  |
| sentence:avoid#4 | dd7bb8365db1 | 97-112 | Sympathomimetics | sentence | avoid | avoid.not_recommended | avoid |  |
| sentence:avoid#5 | 05a52e3d0b0e | 6682-6699 | Opioid Analgesic [EPC] | sentence | avoid | avoid.avoid | avoid |  |
| sentence:avoid#6 | 6e7b563e4568 | 869-890 | CYP3A strong inducer [FDA-CYP] | sentence | avoid | avoid.avoid | avoid |  |
| sentence:avoid#7 | 2d6c9316d07e | 997-1006 | Coagulants | sentence | avoid | avoid.not_recommended | avoid |  |
| sentence:avoid#8 | 099d33f83410 | 1534-1548 | Corticosteroid [EPC] | sentence | avoid | avoid.not_recommended | none | other-clause,effect-adjective |
| sentence:avoid#9 | 41b57b4f5291 | 246-254 | Narcotics | sentence | avoid | avoid.avoid | avoid |  |
| sentence:avoid#10 | db60e773171e | 714-723 | Antiviral [EPC] | sentence | avoid | avoid.avoid | none | effect-adjective |
| sentence:avoid#11 | 3ffcb6c3211c | 2498-2517 | Adrenergic Agonists | sentence | avoid | avoid.not_recommended | avoid |  |
| sentence:avoid#12 | 4379874d3aa9 | 77-86 | Vitamin A [EPC] | sentence | avoid | avoid.avoid | avoid |  |
| sentence:avoid#13 | 8754cee60b95 | 12673-12682 | Antiviral [EPC] | sentence | avoid | avoid.not_recommended | avoid |  |
| sentence:avoid#14 | f48e1cac2ffe | 25-34 | Retinoid [EPC] | sentence | avoid | avoid.avoid | avoid |  |
| sentence:avoid#15 | 78869265d094 | 902-926 | CYP3A moderate inhibitor [FDA-CYP] | sentence | avoid | avoid.not_recommended | avoid |  |
| sentence:avoid#16 | 63a7e78ce2b1 | 538-552 | Antiarrhythmic [EPC] | sentence | avoid | avoid.not_recommended | avoid |  |
| sentence:avoid#17 | 82616f7c5bcf | 574-613 | Selective Serotonin Reuptake Inhibitors | sentence | avoid | avoid.avoid | avoid |  |
| sentence:avoid#18 | f54424bc1648 | 200-228 | Monoamine Oxidase Inhibitor [EPC] | sentence | avoid | avoid.avoid | avoid |  |
| sentence:avoid#19 | fcf297d60e89 | 4235-4251 | Antiviral Agent [TC] | sentence | avoid | avoid.not_recommended | avoid | members |
| sentence:avoid#20 | c00fad2e152d | 4993-5010 | Opioid Analgesic [EPC] | sentence | avoid | avoid.avoid | avoid |  |
| sentence:avoid#21 | b4c741deff09 | 981-1003 | Proton Pump Inhibitor [EPC] | sentence | avoid | avoid.not_recommended | avoid |  |
| sentence:avoid#22 | 237c3147e760 | 562-584 | Proton Pump Inhibitor [EPC] | sentence | avoid | avoid.not_recommended | avoid |  |
| sentence:avoid#23 | 31ea068ce321 | 2438-2447 | Bile Acid [EPC] | sentence | avoid | avoid.avoid | adjust_dose |  |
| sentence:avoid#24 | 459e1eff03c5 | 907-922 | Antiarrhythmic [EPC] | sentence | avoid | avoid.not_recommended | avoid |  |
| sentence:avoid#25 | b60208f8b310 | 936-951 | Methylxanthine [EPC] | sentence | avoid | avoid.avoid | avoid |  |
| sentence:avoid#26 | 7f8ec9460ab2 | 194-203 | Irritants | sentence | avoid | avoid.avoid | avoid |  |
| sentence:avoid#27 | 05825b458964 | 1575-1585 | Analgesics | sentence | avoid | avoid.avoid | avoid |  |
| sentence:avoid#28 | 9aa36c288c2e | 748-759 | Anesthetics | sentence | avoid | avoid.do_not_use | monitor | merged-item |
| sentence:avoid#29 | 5bd41205c95c | 652-674 | HIV Protease Inhibitors | sentence | avoid | avoid.avoid | adjust_dose | merged-item |
| sentence:avoid#30 | fa8432c08569 | 988-996 | Antacid [PE] | sentence | avoid | avoid.avoid | avoid |  |
| sentence:avoid#31 | 6452ef7f1889 | 652-674 | HIV Protease Inhibitors | sentence | avoid | avoid.avoid | adjust_dose |  |
| sentence:avoid#32 | 2e951acbea29 | 4442-4457 | Vinca Alkaloid [EPC] | sentence | avoid | avoid.not_recommended | avoid |  |
| sentence:avoid#33 | 9af80747a262 | 1148-1184 | Nonsteroidal Anti-inflammatory Drug [EPC] | sentence | avoid | avoid.avoid | avoid |  |
| sentence:avoid#34 | 366a7d7043ce | 1060-1082 | Proton Pump Inhibitor [EPC] | sentence | avoid | avoid.not_recommended | avoid |  |
| sentence:avoid#35 | 63ca13ba0ceb | 372-387 | P-gp inhibitor [FDA-CYP] | sentence | avoid | avoid.avoid | avoid |  |
| sentence:avoid#36 | 7bef89439af1 | 1857-1880 | CYP3A moderate inducer [FDA-CYP] | sentence | avoid | avoid.avoid | avoid |  |
| sentence:avoid#37 | 0adfb95fcbd4 | 9366-9383 | Opioid Analgesic [EPC] | sentence | avoid | avoid.avoid | avoid |  |
| sentence:avoid#38 | 0cd995154e30 | 907-922 | Antiarrhythmic [EPC] | sentence | avoid | avoid.not_recommended | avoid |  |
| sentence:avoid#39 | c4e77de9233c | 35-50 | Antiarrhythmic [EPC] | sentence | avoid | avoid.avoid | avoid |  |
| sentence:avoid#40 | ce64c817fed7 | 6298-6307 | Analgesics | sentence | avoid | avoid.avoid | avoid | subject |
| sentence:avoid#41 | 5fbc86239153 | 5188-5205 | Opioid Analgesic [EPC] | sentence | avoid | avoid.avoid | avoid |  |
| sentence:avoid#42 | 0609b7b6ad12 | 9795-9827 | Vasopressin Receptor Antagonist [EPC] | sentence | avoid | avoid.not_recommended | avoid |  |
| sentence:avoid#43 | 7809273f6a81 | 566-574 | Narcotics | sentence | avoid | avoid.avoid | avoid |  |
| sentence:avoid#44 | c3e8f4c9d553 | 1829-1854 | Tricyclic Antidepressant [EPC] | sentence | avoid | avoid.avoid | avoid |  |
| sentence:avoid#45 | 4b200cf7718d | 2614-2626 | Electrolyte [EPC] | sentence | avoid | avoid.avoid | none |  |
| sentence:avoid#46 | a5b9713f545b | 5354-5370 | Muscle Relaxant [EPC] | sentence | avoid | avoid.avoid | none | ctx:adjust_dose,merged-item |
| sentence:avoid#47 | 9075ec9a81f7 | 1069-1092 | CYP2C19 strong inducer [FDA-CYP] | sentence | avoid | avoid.avoid | avoid |  |
| sentence:avoid#48 | 13fda517da75 | 1435-1457 | Proton Pump Inhibitor [EPC] | sentence | avoid | avoid.avoid | avoid |  |
| sentence:avoid#49 | d17d6761b54c | 2039-2058 | Adrenergic Agonists | sentence | avoid | avoid.not_recommended | avoid |  |
| sentence:avoid#50 | a253e1cb4ec7 | 286-294 | Antacid [PE] | sentence | avoid | avoid.avoid | none | merged-item |
| sentence:adjust_dose#1 | f92f49bf81b4 | 773-800 | Potassium-sparing Diuretic [EPC] | sentence | adjust_dose | adjust_dose.dose_change | none | ctx:monitor,other-clause |
| sentence:adjust_dose#2 | 96d265cc3133 | 112-137 | CYP3A moderate inhibitor [FDA-CYP] | sentence | adjust_dose | adjust_dose.dose_cap | adjust_dose |  |
| sentence:adjust_dose#3 | adf27fc2834e | 379-392 | Anticoagulants | sentence | adjust_dose | adjust_dose.dose_change | adjust_dose |  |
| sentence:adjust_dose#4 | 59b143b6ae49 | 606-613 | Insulin [EPC] | sentence | adjust_dose | adjust_dose.dose_adjustment | adjust_dose |  |
| sentence:adjust_dose#5 | 990d626f9195 | 5465-5480 | Muscle Relaxant [EPC] | sentence | adjust_dose | adjust_dose.reduce_dose | adjust_dose |  |
| sentence:adjust_dose#6 | 65be8dece6f2 | 3214-3225 | Anesthetics | sentence | adjust_dose | adjust_dose.titrate | adjust_dose |  |
| sentence:adjust_dose#7 | 0917b0cc329f | 7790-7805 | Muscle Relaxant [EPC] | sentence | adjust_dose | adjust_dose.reduce_dose | adjust_dose |  |
| sentence:adjust_dose#8 | 5b0f56cb2425 | 712-726 | Benzodiazepine [EPC] | sentence | adjust_dose | adjust_dose.dose_change | adjust_dose |  |
| sentence:adjust_dose#9 | 9f88c6d2c50b | 2346-2370 | Tricyclic Antidepressant [EPC] | sentence | adjust_dose | adjust_dose.dose_change | adjust_dose |  |
| sentence:adjust_dose#10 | 20ea88247749 | 6920-6959 | Selective Serotonin Reuptake Inhibitors | sentence | adjust_dose | adjust_dose.dose_change | adjust_dose |  |
| sentence:adjust_dose#11 | 52a528bc179a | 5278-5286 | Diuretics | sentence | adjust_dose | adjust_dose.dose_change | adjust_dose |  |
| sentence:adjust_dose#12 | 1c09ca8bf6fe | 7238-7253 | Muscle Relaxant [EPC] | sentence | adjust_dose | adjust_dose.reduce_dose | adjust_dose |  |
| sentence:adjust_dose#13 | 0584f1bd1cb8 | 694-717 | Atypical Antipsychotic [EPC] | sentence | adjust_dose | adjust_dose.dose_change | adjust_dose |  |
| sentence:adjust_dose#14 | 014f4f90a56d | 9712-9724 | Sulfonylurea [EPC] | sentence | adjust_dose | adjust_dose.dose_change | adjust_dose |  |
| sentence:adjust_dose#15 | 10e17074a550 | 7913-7921 | Diuretics | sentence | adjust_dose | adjust_dose.dose_change | adjust_dose |  |
| sentence:adjust_dose#16 | 718d678f18ad | 4586-4604 | Protease Inhibitor [EPC] | sentence | adjust_dose | adjust_dose.dose_adjustment | adjust_dose |  |
| sentence:adjust_dose#17 | 449d9f5afc75 | 2046-2065 | Protease Inhibitor [EPC] | sentence | adjust_dose | adjust_dose.dose_cap | adjust_dose |  |
| sentence:adjust_dose#18 | 294a5d829f30 | 3048-3065 | Local Anesthetic [EPC] | sentence | adjust_dose | adjust_dose.dose_change | adjust_dose |  |
| sentence:adjust_dose#19 | 03cd9133e9d7 | 1775-1782 | Insulin [EPC] | sentence | adjust_dose | adjust_dose.dose_change | adjust_dose |  |
| sentence:adjust_dose#20 | 87020d368fd9 | 7765-7773 | Diuretics | sentence | adjust_dose | adjust_dose.dose_change | adjust_dose |  |
| sentence:adjust_dose#21 | 48ec859ddfda | 4035-4043 | Diuretics | sentence | adjust_dose | adjust_dose.dose_change | adjust_dose |  |
| sentence:adjust_dose#22 | ee4931c865c9 | 2829-2844 | Muscle Relaxant [EPC] | sentence | adjust_dose | adjust_dose.reduce_dose | adjust_dose |  |
| sentence:adjust_dose#23 | b782ff67bf4c | 2012-2024 | Barbiturate [EPC] | sentence | adjust_dose | adjust_dose.dose_change | adjust_dose |  |
| sentence:adjust_dose#24 | 23ee06c0dba7 | 1641-1648 | Insulin [EPC] | sentence | adjust_dose | adjust_dose.dose_change | adjust_dose |  |
| sentence:adjust_dose#25 | 8e25e0cb86ff | 2608-2620 | Barbiturate [EPC] | sentence | adjust_dose | adjust_dose.dose_change | adjust_dose |  |
| sentence:adjust_dose#26 | b6c20b913b8c | 544-559 | Benzodiazepine [EPC] | sentence | adjust_dose | adjust_dose.dose_change | adjust_dose |  |
| sentence:adjust_dose#27 | bab5b4bdd0e0 | 605-612 | Insulin [EPC] | sentence | adjust_dose | adjust_dose.dose_adjustment | adjust_dose |  |
| sentence:adjust_dose#28 | 701ef1a0d2d4 | 2481-2496 | Anticholinergic [EPC] | sentence | adjust_dose | adjust_dose.dose_adjustment | adjust_dose |  |
| sentence:adjust_dose#29 | cd61685fd7bc | 10006-10019 | Anticoagulants | sentence | adjust_dose | adjust_dose.dose_change | adjust_dose |  |
| sentence:adjust_dose#30 | d24ff277ae0f | 2654-2669 | Corticosteroid [EPC] | sentence | adjust_dose | adjust_dose.dose_change | adjust_dose |  |
| sentence:adjust_dose#31 | 44c04c78fc1b | 1111-1119 | Diuretics | sentence | adjust_dose | adjust_dose.dose_change | adjust_dose |  |
| sentence:adjust_dose#32 | 3bbfd98766be | 2319-2343 | CYP2D6 strong inhibitor [FDA-CYP] | sentence | adjust_dose | adjust_dose.titrate | adjust_dose |  |
| sentence:adjust_dose#33 | 88ec021fb678 | 1894-1901 | Insulin [EPC] | sentence | adjust_dose | adjust_dose.dose_change | adjust_dose |  |
| sentence:adjust_dose#34 | 1b25c173f0c4 | 2842-2856 | P-gp inhibitor [FDA-CYP] | sentence | adjust_dose | adjust_dose.dose_change | adjust_dose |  |
| sentence:adjust_dose#35 | f02dd13e663b | 8229-8237 | Diuretics | sentence | adjust_dose | adjust_dose.dose_change | adjust_dose |  |
| sentence:adjust_dose#36 | 4b755d2c62af | 535-549 | Anticoagulants | sentence | adjust_dose | adjust_dose.dose_change | adjust_dose |  |
| sentence:adjust_dose#37 | 44352357bb60 | 2842-2866 | Tricyclic Antidepressant [EPC] | sentence | adjust_dose | adjust_dose.dose_change | adjust_dose |  |
| sentence:adjust_dose#38 | c4bec44b2a59 | 126-150 | CYP1A2 strong inhibitor [FDA-CYP] | sentence | adjust_dose | adjust_dose.dose_change | adjust_dose |  |
| sentence:adjust_dose#39 | 9c84a62aff92 | 178-187 | Narcotics | sentence | adjust_dose | adjust_dose.dose_adjustment | none | merged-item |
| sentence:adjust_dose#40 | a6124b5ebd7b | 5087-5112 | Tricyclic Antidepressant [EPC] | sentence | adjust_dose | adjust_dose.dose_adjustment | adjust_dose |  |
| sentence:adjust_dose#41 | 22abf6a7aaa5 | 6179-6194 | Muscle Relaxant [EPC] | sentence | adjust_dose | adjust_dose.reduce_dose | adjust_dose |  |
| sentence:adjust_dose#42 | 46b3ffe50a27 | 7187-7195 | Diuretics | sentence | adjust_dose | adjust_dose.dose_change | adjust_dose |  |
| sentence:adjust_dose#43 | 867f43e8ad97 | 1767-1774 | Insulin [EPC] | sentence | adjust_dose | adjust_dose.dose_change | adjust_dose |  |
| sentence:adjust_dose#44 | 80ce9f9bf0cd | 661-673 | Barbiturate [EPC] | sentence | adjust_dose | adjust_dose.dose_change | adjust_dose |  |
| sentence:adjust_dose#45 | 958de6a88f99 | 4427-4448 | CYP3A strong inducer [FDA-CYP] | sentence | adjust_dose | adjust_dose.dose_change | adjust_dose |  |
| sentence:adjust_dose#46 | 3235e411c636 | 7907-7915 | Diuretics | sentence | adjust_dose | adjust_dose.dose_change | adjust_dose |  |
| sentence:adjust_dose#47 | f11beb0abdac | 12876-12890 | Benzodiazepine [EPC] | sentence | adjust_dose | adjust_dose.dose_change | adjust_dose |  |
| sentence:adjust_dose#48 | d6de9f1ccb6e | 4211-4229 | Analgesics, Opioid | sentence | adjust_dose | adjust_dose.dose_change | adjust_dose |  |
| sentence:adjust_dose#49 | 6784345ccace | 564-582 | Thiazide Diuretic [EPC] | sentence | adjust_dose | adjust_dose.dose_change | adjust_dose |  |
| sentence:adjust_dose#50 | 1b47b8cd3fdf | 2452-2479 | CYP2C19 moderate inhibitor [FDA-CYP] | sentence | adjust_dose | adjust_dose.dose_adjustment | adjust_dose |  |
| sentence:monitor#1 | 7528e945a5db | 950-958 | Diuretics | sentence | monitor | monitor.monitor | monitor |  |
| sentence:monitor#2 | 8de2528390e5 | 16610-16650 | Dihydropyridine Calcium Channel Blocker [EPC] | sentence | monitor | monitor.monitor | monitor |  |
| sentence:monitor#3 | 395a27791f88 | 2172-2183 | Barbiturate [EPC] | sentence | monitor | monitor.monitor | monitor |  |
| sentence:monitor#4 | e9a96dba349e | 4471-4493 | Neuromuscular Blockade [PE] | sentence | monitor | monitor.monitor | monitor |  |
| sentence:monitor#5 | c9fdf54fc689 | 2452-2466 | Anticoagulants | sentence | monitor | monitor.caution | monitor |  |
| sentence:monitor#6 | 6ea952c44375 | 2923-2937 | Anti-coagulant [EPC] | sentence | monitor | monitor.monitor | monitor |  |
| sentence:monitor#7 | f276c818ee7b | 2551-2576 | Tricyclic Antidepressant [EPC] | sentence | monitor | monitor.caution | monitor |  |
| sentence:monitor#8 | 281a46e94bd2 | 721-733 | Electrolyte [EPC] | sentence | monitor | monitor.monitor | none | non-mention |
| sentence:monitor#9 | d29a194c2928 | 6494-6521 | Calcium Channel Antagonists [MoA] | sentence | monitor | monitor.caution | monitor |  |
| sentence:monitor#10 | eefb2054845b | 6052-6067 | Anticholinergic [EPC] | sentence | monitor | monitor.monitor | monitor |  |
| sentence:monitor#11 | 3d2a7e758d7f | 712-734 | Proton Pump Inhibitor [EPC] | sentence | monitor | monitor.monitor | monitor |  |
| sentence:monitor#12 | c9118e3fb7d5 | 511-518 | Insulin [EPC] | sentence | monitor | monitor.monitor | monitor |  |
| sentence:monitor#13 | 7dc18931da1c | 867-906 | Selective Serotonin Reuptake Inhibitors | sentence | monitor | monitor.monitor | monitor |  |
| sentence:monitor#14 | 53502d40ec5b | 3803-3831 | HMG-CoA Reductase Inhibitor [EPC] | sentence | monitor | monitor.caution | monitor | subject |
| sentence:monitor#15 | 01be4dae93a4 | 1235-1263 | Carbonic Anhydrase Inhibitor [EPC] | sentence | monitor | monitor.monitor | monitor |  |
| sentence:monitor#16 | f37446fb3545 | 5462-5476 | Phenothiazine [EPC] | sentence | monitor | monitor.caution | monitor |  |
| sentence:monitor#17 | 778a24298e3f | 832-846 | Anticoagulants | sentence | monitor | monitor.monitor | monitor |  |
| sentence:monitor#18 | 95d177361f32 | 5554-5568 | Phenothiazine [EPC] | sentence | monitor | monitor.caution | monitor |  |
| sentence:monitor#19 | 5046c988bda8 | 4232-4244 | Electrolyte [EPC] | sentence | monitor | monitor.monitor | none | non-mention |
| sentence:monitor#20 | 5a4e199d2df7 | 766-780 | Anticoagulants | sentence | monitor | monitor.monitor | monitor |  |
| sentence:monitor#21 | 734933e81a8f | 1234-1262 | Carbonic Anhydrase Inhibitor [EPC] | sentence | monitor | monitor.monitor | monitor |  |
| sentence:monitor#22 | 341e9f28e938 | 2314-2328 | Anticoagulants | sentence | monitor | monitor.monitor | monitor |  |
| sentence:monitor#23 | 619464851a8a | 3013-3028 | Anticholinergic [EPC] | sentence | monitor | monitor.monitor | monitor |  |
| sentence:monitor#24 | e79239f6a137 | 1301-1316 | Corticosteroid [EPC] | sentence | monitor | monitor.observe_closely | monitor |  |
| sentence:monitor#25 | f7d1e2d5d74a | 1922-1929 | Insulin [EPC] | sentence | monitor | monitor.monitor | none | other-clause |
| sentence:monitor#26 | 6cb4a98ab1ee | 2011-2023 | Electrolyte [EPC] | sentence | monitor | monitor.monitor | none | non-mention |
| sentence:monitor#27 | 49d9da828cd6 | 762-786 | Anti-inflammatory Agent [TC] | sentence | monitor | monitor.caution | monitor |  |
| sentence:monitor#28 | b9263a351efb | 3554-3566 | Vasodilator [EPC] | sentence | monitor | monitor.caution | monitor |  |
| sentence:monitor#29 | d0e37b9a24c7 | 1098-1112 | Anticoagulants | sentence | monitor | monitor.monitor | monitor |  |
| sentence:monitor#30 | 23b313bde1dc | 6947-6962 | Anticholinergic [EPC] | sentence | monitor | monitor.monitor | monitor |  |
| sentence:monitor#31 | 0008c36a5bdf | 736-761 | Tricyclic Antidepressant [EPC] | sentence | monitor | monitor.monitor | monitor |  |
| sentence:monitor#32 | e00843c6d7b5 | 1068-1090 | Proton Pump Inhibitor [EPC] | sentence | monitor | monitor.monitor | monitor |  |
| sentence:monitor#33 | 754ada692b3b | 2629-2643 | Anticoagulants | sentence | monitor | monitor.caution | monitor |  |
| sentence:monitor#34 | e1b22adbcc4d | 7173-7188 | Antiarrhythmic [EPC] | sentence | monitor | monitor.caution | monitor |  |
| sentence:monitor#35 | e9aef078b18b | 850-889 | Selective Serotonin Reuptake Inhibitors | sentence | monitor | monitor.monitor | monitor |  |
| sentence:monitor#36 | 39d71757dbfd | 788-796 | Estrogen [EPC] | sentence | monitor | monitor.caution | monitor |  |
| sentence:monitor#37 | 79d67ef49afe | 1765-1773 | Diuretics | sentence | monitor | monitor.observe_closely | monitor |  |
| sentence:monitor#38 | 81d8e200f59f | 2287-2306 | Adrenergic Agonists | sentence | monitor | monitor.caution | monitor | subject |
| sentence:monitor#39 | bf2c8f60d85d | 4369-4384 | Corticosteroid [EPC] | sentence | monitor | monitor.caution | monitor |  |
| sentence:monitor#40 | 8fee2e9accb5 | 448-459 | Electrolyte [EPC] | sentence | monitor | monitor.caution | none | non-mention |
| sentence:monitor#41 | b3014ec3cc6d | 1606-1614 | Steroid [EPC] | sentence | monitor | monitor.caution | adjust_dose | under-read |
| sentence:monitor#42 | 7bcaad9b0e56 | 1931-1945 | Anticoagulants | sentence | monitor | monitor.monitor | monitor |  |
| sentence:monitor#43 | 7ddf64055b6d | 4070-4085 | Anticholinergic [EPC] | sentence | monitor | monitor.monitor | monitor |  |
| sentence:monitor#44 | ab593e93bf19 | 12199-12223 | Calcium Channel Blocker [EPC] | sentence | monitor | monitor.monitor | monitor |  |
| sentence:monitor#45 | 6795b3f9146a | 1434-1463 | Carbonic Anhydrase Inhibitor [EPC] | sentence | monitor | monitor.monitor | monitor |  |
| sentence:monitor#46 | 410e9d07c320 | 182-191 | Diuretics | sentence | monitor | monitor.observe_closely | monitor |  |
| sentence:monitor#47 | f112b2a8ff8a | 171-186 | Corticosteroid [EPC] | sentence | monitor | monitor.observe_closely | monitor |  |
| sentence:monitor#48 | 2321af7edfbf | 879-918 | Selective Serotonin Reuptake Inhibitors | sentence | monitor | monitor.monitor | monitor |  |
| sentence:monitor#49 | a68cec58e79a | 3543-3552 | Diuretics | sentence | monitor | monitor.monitor | none | ctx:adjust_dose,merged-item |
| sentence:monitor#50 | 99dc89ec1e4d | 4793-4815 | Neuromuscular Blockade [PE] | sentence | monitor | monitor.monitor | monitor |  |
| heading_block:avoid#1 | 8e10b97e5087 | 6602-6630 | Monoamine Oxidase Inhibitor [EPC] | heading_block | avoid | avoid.do_not_use | avoid |  |
| heading_block:avoid#2 | ec8f34feb140 | 5103-5118 | Benzodiazepine [EPC] | heading_block | avoid | avoid.not_recommended | avoid | merged-item |
| heading_block:avoid#3 | d6a55fcc4695 | 2482-2498 | Muscle Relaxant [EPC] | heading_block | avoid | avoid.avoid | adjust_dose | merged-item |
| heading_block:avoid#4 | 30046c6631d5 | 5566-5582 | Muscle Relaxant [EPC] | heading_block | avoid | avoid.avoid | adjust_dose |  |
| heading_block:avoid#5 | 5292cb5f9a0c | 7118-7143 | Skeletal Muscle Relaxant [EPC] | heading_block | avoid | avoid.not_recommended | adjust_dose |  |
| heading_block:avoid#6 | b7663018198f | 5967-5975 | Antacid [PE] | heading_block | avoid | avoid.not_recommended | avoid |  |
| heading_block:avoid#7 | 29ea62f3bc2b | 6906-6923 | Opioid Analgesic [EPC] | heading_block | avoid | avoid.do_not_use | avoid | merged-item |
| heading_block:avoid#8 | 71f252d0d82d | 6793-6821 | Monoamine Oxidase Inhibitor [EPC] | heading_block | avoid | avoid.do_not_use | avoid |  |
| heading_block:avoid#9 | 631fbd5644d8 | 7440-7455 | Corticosteroid [EPC] | heading_block | avoid | avoid.not_recommended | monitor | other-clause |
| heading_block:avoid#10 | eb0b55360eff | 934-955 | CYP3A strong inducer [FDA-CYP] | heading_block | avoid | avoid.avoid | avoid |  |
| heading_block:avoid#11 | 37e87f8288d4 | 2236-2260 | Bile Acid Binding Resin [EPC] | heading_block | avoid | avoid.avoid | adjust_dose |  |
| heading_block:avoid#12 | 10f003b0bc4d | 7275-7303 | Monoamine Oxidase Inhibitor [EPC] | heading_block | avoid | avoid.do_not_use | avoid |  |
| heading_block:avoid#13 | 54570163c372 | 7026-7034 | Antacid [PE] | heading_block | avoid | avoid.not_recommended | avoid |  |
| heading_block:avoid#14 | 4e75aaf57244 | 5330-5346 | Muscle Relaxant [EPC] | heading_block | avoid | avoid.avoid | adjust_dose |  |
| heading_block:avoid#15 | d88fb4cdaa54 | 5239-5267 | Monoamine Oxidase Inhibitor [EPC] | heading_block | avoid | avoid.not_recommended | avoid |  |
| heading_block:avoid#16 | d55110bdd4cb | 7669-7685 | Muscle Relaxant [EPC] | heading_block | avoid | avoid.avoid | adjust_dose |  |
| heading_block:avoid#17 | 9a42ffa66d51 | 1190-1201 | Anesthetics | heading_block | avoid | avoid.avoid | avoid |  |
| heading_block:avoid#18 | c86bd9d02a4c | 223-238 | Benzodiazepine [EPC] | heading_block | avoid | avoid.avoid | adjust_dose | other-clause |
| heading_block:avoid#19 | 31daf210b838 | 228-255 | Potassium-sparing Diuretic [EPC] | heading_block | avoid | avoid.avoid | avoid |  |
| heading_block:avoid#20 | ec6dc95a30c8 | 5219-5244 | Skeletal Muscle Relaxant [EPC] | heading_block | avoid | avoid.avoid | adjust_dose |  |
| heading_block:adjust_dose#1 | 0c44d8cc2adf | 5758-5777 | Sedatives/Hypnotics [TC] | heading_block | adjust_dose | adjust_dose.dose_change | avoid | under-read |
| heading_block:adjust_dose#2 | abcf1f3ad0c5 | 2400-2416 | Muscle Relaxant [EPC] | heading_block | adjust_dose | adjust_dose.dose_adjustment | monitor |  |
| heading_block:adjust_dose#3 | 0063b72aa256 | 3018-3033 | Benzodiazepine [EPC] | heading_block | adjust_dose | adjust_dose.dose_change | avoid | under-read |
| heading_block:adjust_dose#4 | 47a0b3cc554e | 5612-5631 | General Anesthetic [EPC] | heading_block | adjust_dose | adjust_dose.dose_change | avoid | under-read |
| heading_block:adjust_dose#5 | f63ee6874894 | 1075-1094 | Sedatives/Hypnotics [TC] | heading_block | adjust_dose | adjust_dose.dose_change | avoid | under-read |
| heading_block:adjust_dose#6 | 698486d5b5e7 | 1352-1371 | Sedatives/Hypnotics [TC] | heading_block | adjust_dose | adjust_dose.dose_change | avoid | under-read |
| heading_block:adjust_dose#7 | fea2207679ab | 3221-3240 | General Anesthetic [EPC] | heading_block | adjust_dose | adjust_dose.dose_change | avoid | under-read |
| heading_block:adjust_dose#8 | 692fd6bd0f2d | 6201-6210 | Diuretics | heading_block | adjust_dose | adjust_dose.reduce_dose | adjust_dose | merged-item |
| heading_block:adjust_dose#9 | 05a52e3d0b0e | 3335-3354 | Sedatives/Hypnotics [TC] | heading_block | adjust_dose | adjust_dose.dose_change | avoid | under-read |
| heading_block:adjust_dose#10 | 130e9f3f2fdd | 5015-5034 | General Anesthetic [EPC] | heading_block | adjust_dose | adjust_dose.dose_change | avoid | under-read |
| heading_block:adjust_dose#11 | a777973f6bc6 | 6347-6361 | Anticoagulants | heading_block | adjust_dose | adjust_dose.titrate | monitor |  |
| heading_block:adjust_dose#12 | 29b717fb4048 | 3031-3050 | Protease Inhibitor [EPC] | heading_block | adjust_dose | adjust_dose.dose_change | adjust_dose |  |
| heading_block:adjust_dose#13 | 7e961b07e13c | 5842-5867 | Tricyclic Antidepressant [EPC] | heading_block | adjust_dose | adjust_dose.dose_adjustment | monitor |  |
| heading_block:adjust_dose#14 | 5b0f56cb2425 | 1379-1398 | General Anesthetic [EPC] | heading_block | adjust_dose | adjust_dose.dose_change | avoid | under-read |
| heading_block:adjust_dose#15 | 500ade6d3190 | 3177-3211 | Central Nervous System Depressant [EPC] | heading_block | adjust_dose | adjust_dose.reduce_dose | adjust_dose |  |
| heading_block:adjust_dose#16 | 7f56306c12b7 | 4462-4487 | Tricyclic Antidepressant [EPC] | heading_block | adjust_dose | adjust_dose.dose_adjustment | monitor |  |
| heading_block:adjust_dose#17 | 3f7953b5af4e | 1153-1160 | Insulin [EPC] | heading_block | adjust_dose | adjust_dose.dose_adjustment | adjust_dose |  |
| heading_block:adjust_dose#18 | 531dcadd73c5 | 1546-1562 | Azole Antifungal [EPC] | heading_block | adjust_dose | adjust_dose.dose_change | adjust_dose |  |
| heading_block:adjust_dose#19 | 8aaf61b252a4 | 3280-3295 | Benzodiazepine [EPC] | heading_block | adjust_dose | adjust_dose.dose_change | avoid | under-read |
| heading_block:adjust_dose#20 | a571775d2279 | 4260-4276 | Muscle Relaxant [EPC] | heading_block | adjust_dose | adjust_dose.dose_adjustment | monitor |  |
| heading_block:monitor#1 | 20557edb2f59 | 335-349 | Anticoagulants | heading_block | monitor | monitor.monitor | monitor |  |
| heading_block:monitor#2 | 6e9edd5d2d96 | 3526-3541 | Anticholinergic [EPC] | heading_block | monitor | monitor.monitor | monitor |  |
| heading_block:monitor#3 | 2be5babbf418 | 6658-6684 | Tyrosine Kinase Inhibitors | heading_block | monitor | monitor.monitor | monitor |  |
| heading_block:monitor#4 | d8f1747cdd60 | 2064-2072 | Diuretics | heading_block | monitor | monitor.monitor | monitor |  |
| heading_block:monitor#5 | 050dba7d21a4 | 870-897 | Cyclooxygenase 2 Inhibitors | heading_block | monitor | monitor.monitor | monitor |  |
| heading_block:monitor#6 | d004ac973457 | 3874-3889 | Anticholinergic [EPC] | heading_block | monitor | monitor.monitor | monitor |  |
| heading_block:monitor#7 | 880b27082842 | 6783-6792 | Diuretics | heading_block | monitor | monitor.monitor | monitor |  |
| heading_block:monitor#8 | d369fa0aa329 | 6937-6952 | Corticosteroid [EPC] | heading_block | monitor | monitor.monitor | monitor |  |
| heading_block:monitor#9 | 676ea6c9dd93 | 336-350 | Anticoagulants | heading_block | monitor | monitor.monitor | monitor |  |
| heading_block:monitor#10 | 22a72a5ee6fb | 1056-1071 | Aminoglycoside [EPC] | heading_block | monitor | monitor.monitor | adjust_dose | under-read |
| heading_block:monitor#11 | fee9303365d3 | 1595-1622 | Serotonin Uptake Inhibitors [MoA] | heading_block | monitor | monitor.monitor | monitor |  |
| heading_block:monitor#12 | e6aa812d830f | 257-266 | Diuretics | heading_block | monitor | monitor.monitor | adjust_dose | under-read |
| heading_block:monitor#13 | aee9d0801d37 | 1051-1066 | Aminoglycoside [EPC] | heading_block | monitor | monitor.monitor | adjust_dose | under-read |
| heading_block:monitor#14 | df80e73d733f | 2186-2194 | Diuretics | heading_block | monitor | monitor.monitor | monitor |  |
| heading_block:monitor#15 | 8de2d868ec6f | 4403-4410 | Insulin [EPC] | heading_block | monitor | monitor.monitor | monitor |  |
| heading_block:monitor#16 | 6a7d14b17167 | 337-351 | Anticoagulants | heading_block | monitor | monitor.monitor | monitor |  |
| heading_block:monitor#17 | 44897f8e1f7d | 4486-4505 | Protease Inhibitor [EPC] | heading_block | monitor | monitor.monitor | adjust_dose | under-read,merged-item |
| heading_block:monitor#18 | cbb848ee855c | 1992-2006 | Anticoagulants | heading_block | monitor | monitor.monitor | monitor |  |
| heading_block:monitor#19 | a0a540a36cf3 | 2095-2110 | Anticholinergic [EPC] | heading_block | monitor | monitor.monitor | monitor |  |
| heading_block:monitor#20 | 850d61ddfe17 | 2974-2990 | MTOR Inhibitors | heading_block | monitor | monitor.monitor | monitor |  |
| heading_block:contraindicated#1 | b1b1b077e67a | 372-400 | Monoamine Oxidase Inhibitor [EPC] | heading_block | contraindicated | contraindicated.see_contraindications | contraindicated |  |
| heading_block:contraindicated#2 | 8e58405f044c | 3728-3737 | Alkaloid [EPC] | heading_block | contraindicated | contraindicated.contraindicated | contraindicated |  |
| heading_block:contraindicated#3 | 3eb2d509c1b6 | 402-418 | Anticholinergic [EPC] | heading_block | contraindicated | contraindicated.see_contraindications | none | other-clause |
| heading_block:contraindicated#4 | 398e6517f1c7 | 1701-1710 | Macrolide [EPC] | heading_block | contraindicated | contraindicated.contraindicated | contraindicated |  |
| heading_block:contraindicated#5 | 85738a3f1979 | 1769-1792 | HIV Protease Inhibitors | heading_block | contraindicated | contraindicated.contraindicated | contraindicated |  |
| heading_block:contraindicated#6 | ff1dac28ea9c | 355-383 | Monoamine Oxidase Inhibitor [EPC] | heading_block | contraindicated | contraindicated.see_contraindications | contraindicated |  |
| heading_block:contraindicated#7 | cc1b1749e335 | 1939-1963 | Anti-inflammatory Agent [TC] | heading_block | contraindicated | contraindicated.see_contraindications | monitor |  |
| heading_block:contraindicated#8 | 39e12e596d91 | 235-263 | Monoamine Oxidase Inhibitor [EPC] | heading_block | contraindicated | contraindicated.contraindicated | contraindicated |  |
| heading_block:contraindicated#9 | a0e3dc0b0544 | 2295-2304 | Macrolide [EPC] | heading_block | contraindicated | contraindicated.contraindicated | contraindicated |  |
| heading_block:contraindicated#10 | e42182f8ff92 | 356-386 | Monoamine Oxidase-A Inhibitors [MoA] | heading_block | contraindicated | contraindicated.contraindicated | contraindicated |  |
| heading_block:contraindicated#11 | 086a147976e8 | 518-546 | Monoamine Oxidase Inhibitor [EPC] | heading_block | contraindicated | contraindicated.contraindicated | contraindicated |  |
| heading_block:contraindicated#12 | ad48e2b5fe02 | 2406-2429 | HIV Protease Inhibitors | heading_block | contraindicated | contraindicated.contraindicated | contraindicated |  |
| heading_block:contraindicated#13 | f5d5ceb0e00c | 195-223 | Monoamine Oxidase Inhibitor [EPC] | heading_block | contraindicated | contraindicated.contraindicated | contraindicated |  |
| heading_block:contraindicated#14 | 489f387bac3d | 405-421 | Anticholinergic [EPC] | heading_block | contraindicated | contraindicated.see_contraindications | none |  |
| heading_block:contraindicated#15 | cde4e51f7110 | 211-239 | Monoamine Oxidase Inhibitor [EPC] | heading_block | contraindicated | contraindicated.contraindicated | contraindicated |  |
| sentence:not_read#1 | 681b5f9ec6c4 | 741-759 | Thiazide Diuretic [EPC] | sentence | not_read | None | none |  |
| sentence:not_read#2 | 1760897a1e63 | 829-847 | Psychotropic Drugs | sentence | not_read | None | none |  |
| sentence:not_read#3 | 10636013b9b8 | 180-188 | Antacid [PE] | sentence | not_read | None | none |  |
| sentence:not_read#4 | eb2300dfc00d | 3214-3231 | Dopamine Agonists | sentence | not_read | None | none |  |
| sentence:not_read#5 | f0b45e824642 | 2666-2689 | Antihypertensive Agents | sentence | not_read | None | none |  |
| sentence:not_read#6 | 07a3fc7edb32 | 2267-2279 | Barbiturate [EPC] | sentence | not_read | None | none |  |
| sentence:not_read#7 | a9ff2d3c98da | 2439-2447 | Oxytocic [EPC] | sentence | not_read | None | none | ctx:monitor |
| sentence:not_read#8 | 0c8294f1cf4d | 5174-5188 | Glucocorticoids | sentence | not_read | None | none |  |
| sentence:not_read#9 | 3edc3ca6061e | 4187-4204 | Dopamine Agonists | sentence | not_read | None | none |  |
| sentence:not_read#10 | 8f9ad5c7d8aa | 2071-2086 | Glucocorticoids | sentence | not_read | None | none |  |
| sentence:not_read#11 | 2214dc8c121b | 1240-1269 | Carbonic Anhydrase Inhibitor [EPC] | sentence | not_read | None | none | ctx:avoid |
| sentence:not_read#12 | 047752c9217b | 267-287 | Cholinergic Agonists | sentence | not_read | None | none |  |
| sentence:not_read#13 | 18b1f8b3453a | 536-551 | Benzodiazepine [EPC] | sentence | not_read | None | avoid | under-read |
| sentence:not_read#14 | e9e987811d54 | 4948-4956 | Antacid [PE] | sentence | not_read | None | no_meaningful_interaction |  |
| sentence:not_read#15 | fc1dd3bcb2c7 | 2530-2545 | Sympathomimetics | sentence | not_read | None | none |  |
| sentence:not_read#16 | e63e477ef7aa | 534-542 | Estrogen [EPC] | sentence | not_read | None | none |  |
| sentence:not_read#17 | d21d15174423 | 3830-3842 | Electrolyte [EPC] | sentence | not_read | None | none |  |
| sentence:not_read#18 | 313b265c895c | 1177-1186 | Progestin [EPC] | sentence | not_read | None | none |  |
| sentence:not_read#19 | 20de0027d71c | 7302-7317 | Antiarrhythmic [EPC] | sentence | not_read | None | contraindicated | under-read |
| sentence:not_read#20 | 5e33c8b98dd0 | 706-731 | CYP2C9 moderate inhibitor [FDA-CYP] | sentence | not_read | None | none |  |
| sentence:not_read#21 | a6eceeb844f2 | 1753-1776 | Thiazide-like Diuretic [EPC] | sentence | not_read | None | adjust_dose | under-read,subject |
| sentence:not_read#22 | a23dbc5e2ea3 | 2391-2402 | Electrolyte [EPC] | sentence | not_read | None | none |  |
| sentence:not_read#23 | ce2bc3db0f2b | 6769-6778 | Estrogen [EPC] | sentence | not_read | None | none |  |
| sentence:not_read#24 | 94a9c46f0fbf | 929-954 | Tricyclic Antidepressant [EPC] | sentence | not_read | None | none |  |
| sentence:not_read#25 | 95682622d6c6 | 112-120 | Diuretics | sentence | not_read | None | none |  |
| sentence:not_read#26 | 634c4cc4522d | 600-615 | Anticholinergic [EPC] | sentence | not_read | None | none |  |
| sentence:not_read#27 | 753ef62d253c | 2104-2125 | Antineoplastic Agent [TC] | sentence | not_read | None | none |  |
| sentence:not_read#28 | a0927687ddc2 | 8195-8203 | Antacid [PE] | sentence | not_read | None | adjust_dose | under-read |
| sentence:not_read#29 | 7c4112afff87 | 1717-1732 | Aminoglycoside [EPC] | sentence | not_read | None | adjust_dose | under-read |
| sentence:not_read#30 | 49e3dcb0cc23 | 3533-3548 | Corticosteroid [EPC] | sentence | not_read | None | none |  |
| sentence:not_read#31 | 59afe6c3cf85 | 20-29 | Diuretics | sentence | not_read | None | none |  |
| sentence:not_read#32 | e1e1085b085a | 12323-12338 | Corticosteroid [EPC] | sentence | not_read | None | none |  |
| sentence:not_read#33 | bde1b78e73f0 | 1754-1782 | DOPA Decarboxylase Inhibitors [MoA] | sentence | not_read | None | none |  |
| sentence:not_read#34 | 37f0867bab2f | 6249-6264 | Anticonvulsants | sentence | not_read | None | none |  |
| sentence:not_read#35 | 7907a5111a65 | 3960-3976 | Antiviral Agent [TC] | sentence | not_read | None | none |  |
| sentence:not_read#36 | d796d63661c5 | 1913-1960 | Serotonin and Noradrenaline Reuptake Inhibitors | sentence | not_read | None | none |  |
| sentence:not_read#37 | aed57a5758d1 | 2823-2838 | Benzodiazepine [EPC] | sentence | not_read | None | none | ctx:avoid |
| sentence:not_read#38 | 22153728ec1c | 908-922 | Anticoagulants | sentence | not_read | None | none |  |
| sentence:not_read#39 | 1b61e1b104f1 | 4678-4690 | Electrolyte [EPC] | sentence | not_read | None | none |  |
| sentence:not_read#40 | b01641e6c8f3 | 2968-2980 | Sulfonylurea [EPC] | sentence | not_read | None | none |  |
| sentence:not_read#41 | 8d04c005b02c | 8401-8416 | Anticholinergic [EPC] | sentence | not_read | None | none | ctx:monitor |
| sentence:not_read#42 | cb34c4c45139 | 43-72 | Serotonin Reuptake Inhibitor [EPC] | sentence | not_read | None | none |  |
| sentence:not_read#43 | b1bb887e603b | 167-182 | P-gp inhibitor [FDA-CYP] | sentence | not_read | None | none | ctx:adjust_dose |
| sentence:not_read#44 | 808fcd893ddb | 2276-2283 | Antacid [PE] | sentence | not_read | None | no_meaningful_interaction |  |
| sentence:not_read#45 | a7e148e03fd1 | 418-426 | Antacid [PE] | sentence | not_read | None | none |  |
| sentence:not_read#46 | 9da9c8a35c26 | 67-83 | Vasoconstriction [PE] | sentence | not_read | None | none |  |
| sentence:not_read#47 | 467f20e390d0 | 5918-5946 | Monoamine Oxidase Inhibitor [EPC] | sentence | not_read | None | none | ctx:avoid |
| sentence:not_read#48 | 9b3ac7e48c13 | 2018-2037 | Protease Inhibitor [EPC] | sentence | not_read | None | none |  |
| sentence:not_read#49 | ffe0890137ee | 1630-1639 | Diuretics | sentence | not_read | None | no_meaningful_interaction |  |
| sentence:not_read#50 | e8b39c2847ac | 8947-8958 | Barbiturate [EPC] | sentence | not_read | None | none | ctx:monitor |

</details>

<details><summary>Per-span readings, draw 3 (run 14, after iteration 2; the gating draw)</summary>

| id | text_key | span | class | scope | machine | rule | hand | flag |
|---|---|---|---|---|---|---|---|---|
| sentence:avoid#1 | cdee904f6fd6 | 340-355 | P-gp inhibitor [FDA-CYP] | sentence | avoid | avoid.avoid | avoid |  |
| sentence:avoid#2 | ab14d7bd6cbc | 1458-1473 | P-gp inhibitor [FDA-CYP] | sentence | avoid | avoid.avoid | avoid |  |
| sentence:avoid#3 | 5f7fe3c2078a | 1287-1312 | Tricyclic Antidepressant [EPC] | sentence | avoid | avoid.avoid | avoid |  |
| sentence:avoid#4 | addef9b0cfff | 913-927 | Anticoagulants | sentence | avoid | avoid.avoid | avoid |  |
| sentence:avoid#5 | c8676e5f7602 | 2718-2736 | Protease Inhibitor [EPC] | sentence | avoid | avoid.not_recommended | avoid | members |
| sentence:avoid#6 | 0081c12758bc | 17714-17761 | Non-Nucleoside Reverse Transcriptase Inhibitors [MoA] | sentence | avoid | avoid.not_recommended | none | other-clause |
| sentence:avoid#7 | fc4ed097d0c2 | 707-719 | Barbiturate [EPC] | sentence | avoid | avoid.avoid | avoid |  |
| sentence:avoid#8 | 718d678f18ad | 4406-4452 | Non-Nucleoside Reverse Transcriptase Inhibitors [MoA] | sentence | avoid | avoid.avoid | avoid |  |
| sentence:avoid#9 | 71541f9a93f8 | 1102-1116 | Anticoagulants | sentence | avoid | avoid.avoid | none | ctx:monitor,merged-item |
| sentence:avoid#10 | 94abe89bf6bb | 1523-1538 | Anticholinergic [EPC] | sentence | avoid | avoid.avoid | monitor |  |
| sentence:avoid#11 | 90302ff30713 | 1709-1717 | Antacid [PE] | sentence | avoid | avoid.avoid | avoid |  |
| sentence:avoid#12 | 5b3ed616c0ad | 1124-1146 | Proton Pump Inhibitor [EPC] | sentence | avoid | avoid.not_recommended | avoid |  |
| sentence:avoid#13 | cd4d1e5c72ea | 7069-7077 | Antacid [PE] | sentence | avoid | avoid.not_recommended | avoid |  |
| sentence:avoid#14 | ca5179c37960 | 4805-4822 | Phosphate Binder [EPC] | sentence | avoid | avoid.avoid | none | ctx:adjust_dose,merged-item |
| sentence:avoid#15 | e62949037b20 | 262-278 | Muscle Relaxant [EPC] | sentence | avoid | avoid.avoid | avoid |  |
| sentence:avoid#16 | b45ceca12022 | 812-827 | Antiarrhythmic [EPC] | sentence | avoid | avoid.avoid | avoid |  |
| sentence:avoid#17 | ed86f261eb6f | 1288-1313 | Tricyclic Antidepressant [EPC] | sentence | avoid | avoid.avoid | avoid |  |
| sentence:avoid#18 | ad7428174b26 | 664-675 | Electrolyte [EPC] | sentence | avoid | avoid.avoid | none | non-mention |
| sentence:avoid#19 | b35a3d76149d | 214-236 | Proton Pump Inhibitor [EPC] | sentence | avoid | avoid.avoid | avoid |  |
| sentence:avoid#20 | aad7ffa60b35 | 2191-2210 | Adrenergic Agonists | sentence | avoid | avoid.not_recommended | avoid |  |
| sentence:avoid#21 | f9eb5f3c5c2a | 1086-1094 | Antacid [PE] | sentence | avoid | avoid.avoid | avoid |  |
| sentence:avoid#22 | d6de9f1ccb6e | 4898-4908 | Anesthetics | sentence | avoid | avoid.not_recommended | avoid | members |
| sentence:avoid#23 | c6940e46ed56 | 261-280 | General Anesthetic [EPC] | sentence | avoid | avoid.avoid | avoid |  |
| sentence:avoid#24 | ff24cd44692d | 20-43 | CYP3A strong inhibitor [FDA-CYP] | sentence | avoid | avoid.avoid | avoid |  |
| sentence:avoid#25 | ad7644430cd7 | 508-516 | Antacid [PE] | sentence | avoid | avoid.avoid | avoid |  |
| sentence:avoid#26 | d0d5a791f39b | 146-155 | Diuretics | sentence | avoid | avoid.avoid | avoid |  |
| sentence:avoid#27 | 026e237aa542 | 4013-4028 | Benzodiazepine [EPC] | sentence | avoid | avoid.not_recommended | none | merged-item |
| sentence:avoid#28 | 63ef7c183cbb | 2455-2474 | Adrenergic Agonists | sentence | avoid | avoid.not_recommended | avoid |  |
| sentence:avoid#29 | 7b1de57808ad | 396-410 | Antiarrhythmic [EPC] | sentence | avoid | avoid.not_recommended | avoid |  |
| sentence:avoid#30 | d7f5869e2d9b | 508-517 | Antiviral [EPC] | sentence | avoid | avoid.avoid | none | effect-adjective |
| sentence:avoid#31 | 43e0df52a3f5 | 522-545 | HIV Protease Inhibitors | sentence | avoid | avoid.avoid | avoid |  |
| sentence:avoid#32 | 34fa875661d1 | 5117-5134 | Opioid Analgesic [EPC] | sentence | avoid | avoid.avoid | avoid |  |
| sentence:avoid#33 | 9a447c4cdbfe | 169-178 | Vitamin A [EPC] | sentence | avoid | avoid.avoid | avoid |  |
| sentence:avoid#34 | 15184c790a88 | 1253-1277 | Bile Acid Binding Resin [EPC] | sentence | avoid | avoid.avoid | adjust_dose |  |
| sentence:avoid#35 | 01d711664f9d | 5870-5898 | Monoamine Oxidase Inhibitor [EPC] | sentence | avoid | avoid.avoid | avoid |  |
| sentence:avoid#36 | 4daaa7e80fc3 | 1264-1289 | Tricyclic Antidepressant [EPC] | sentence | avoid | avoid.avoid | avoid |  |
| sentence:avoid#37 | 3f442df6e578 | 259-281 | Proton Pump Inhibitor [EPC] | sentence | avoid | avoid.avoid | avoid |  |
| sentence:avoid#38 | 9630633e5016 | 8710-8727 | Opioid Analgesic [EPC] | sentence | avoid | avoid.avoid | avoid |  |
| sentence:avoid#39 | c60b25c0cdf9 | 412-431 | Fibrinolytic Agents | sentence | avoid | avoid.avoid | none | non-mention |
| sentence:avoid#40 | 80987130389c | 814-832 | Cardiac Glycoside [EPC] | sentence | avoid | avoid.avoid | avoid |  |
| sentence:avoid#41 | bb90b93f9d7f | 7148-7156 | Antacid [PE] | sentence | avoid | avoid.not_recommended | avoid |  |
| sentence:avoid#42 | e3e65de9ad83 | 187-206 | Sedatives/Hypnotics [TC] | sentence | avoid | avoid.avoid | avoid |  |
| sentence:avoid#43 | 2e74224d8be7 | 32-54 | CYP1A2 weak inhibitor [FDA-CYP] | sentence | avoid | avoid.avoid | avoid |  |
| sentence:avoid#44 | 05fbb5c7b54c | 775-798 | HIV Protease Inhibitors | sentence | avoid | avoid.avoid | adjust_dose | merged-item |
| sentence:avoid#45 | 58fb18775493 | 519-542 | HIV Protease Inhibitors | sentence | avoid | avoid.avoid | avoid |  |
| sentence:avoid#46 | 9f68a2c07aeb | 3884-3912 | Monoamine Oxidase Inhibitor [EPC] | sentence | avoid | avoid.avoid | avoid |  |
| sentence:avoid#47 | bcfa131f2742 | 1652-1677 | CYP2C9 moderate inhibitor [FDA-CYP] | sentence | avoid | avoid.avoid | avoid |  |
| sentence:avoid#48 | 24b16ab6302b | 4708-4716 | Antacid [PE] | sentence | avoid | avoid.avoid | none |  |
| sentence:avoid#49 | 223e78db2296 | 6549-6566 | Opioid Analgesic [EPC] | sentence | avoid | avoid.avoid | avoid |  |
| sentence:avoid#50 | 13727ac7c311 | 1949-1958 | Vitamin D [EPC] | sentence | avoid | avoid.avoid | avoid |  |
| sentence:monitor#1 | d94faa7ee14d | 2218-2236 | Thiazide Diuretic [EPC] | sentence | monitor | monitor.monitor | adjust_dose | under-read |
| sentence:monitor#2 | f2375fc35866 | 1417-1426 | Diuretics | sentence | monitor | monitor.observe_closely | monitor |  |
| sentence:monitor#3 | 0529f9df3ded | 349-374 | Tricyclic Antidepressant [EPC] | sentence | monitor | monitor.monitor | monitor |  |
| sentence:monitor#4 | 7ead49c67e2d | 7561-7576 | Corticosteroid [EPC] | sentence | monitor | monitor.monitor | monitor |  |
| sentence:monitor#5 | 2899160b2fb5 | 14595-14618 | HIV Protease Inhibitors | sentence | monitor | monitor.monitor | monitor |  |
| sentence:monitor#6 | 9125ccaf39e1 | 1649-1657 | Diuretics | sentence | monitor | monitor.observe_closely | monitor |  |
| sentence:monitor#7 | f6f704a4be8b | 6145-6160 | Benzodiazepine [EPC] | sentence | monitor | monitor.caution | monitor |  |
| sentence:monitor#8 | 7e2b6c925e99 | 1691-1727 | Nonsteroidal Anti-inflammatory Drug [EPC] | sentence | monitor | monitor.monitor | monitor |  |
| sentence:monitor#9 | 155899e828bb | 945-959 | Anticoagulants | sentence | monitor | monitor.monitor | monitor |  |
| sentence:monitor#10 | f00908d343b0 | 11420-11434 | Anticoagulants | sentence | monitor | monitor.monitor | monitor |  |
| sentence:monitor#11 | 3ddfb6cb79d4 | 919-953 | Norepinephrine Reuptake Inhibitor [EPC] | sentence | monitor | monitor.monitor | monitor |  |
| sentence:monitor#12 | 8406d1aa54a7 | 3706-3721 | Benzodiazepine [EPC] | sentence | monitor | monitor.caution | monitor |  |
| sentence:monitor#13 | e9e4b58f24c4 | 2685-2699 | Anticoagulants | sentence | monitor | monitor.caution | monitor |  |
| sentence:monitor#14 | 7e961b07e13c | 7986-8001 | Benzodiazepine [EPC] | sentence | monitor | monitor.caution | monitor |  |
| sentence:monitor#15 | 2899160b2fb5 | 14054-14076 | Proton Pump Inhibitor [EPC] | sentence | monitor | monitor.monitor | none | merged-item |
| sentence:monitor#16 | 14aca808861f | 462-470 | Estrogen [EPC] | sentence | monitor | monitor.monitor | monitor |  |
| sentence:monitor#17 | becf772ba441 | 933-967 | Norepinephrine Reuptake Inhibitor [EPC] | sentence | monitor | monitor.monitor | monitor |  |
| sentence:monitor#18 | 84d36cc129a3 | 964-972 | Androgen [EPC] | sentence | monitor | monitor.monitor | monitor | subject |
| sentence:monitor#19 | 5dd5497a468f | 4395-4410 | Corticosteroid [EPC] | sentence | monitor | monitor.caution | monitor |  |
| sentence:monitor#20 | a1413d8cc83e | 3437-3460 | HIV Protease Inhibitors | sentence | monitor | monitor.monitor | monitor |  |
| sentence:monitor#21 | 3cd9c929dafb | 352-377 | Tricyclic Antidepressant [EPC] | sentence | monitor | monitor.monitor | monitor |  |
| sentence:monitor#22 | dc7456985db1 | 380-398 | Cardiac Glycoside [EPC] | sentence | monitor | monitor.caution | monitor |  |
| sentence:monitor#23 | 53faf49740ae | 4788-4812 | Anti-inflammatory Agent [TC] | sentence | monitor | monitor.observe_closely | monitor |  |
| sentence:monitor#24 | b9b9372d8aa7 | 2646-2673 | Potassium-sparing Diuretic [EPC] | sentence | monitor | monitor.caution | none | non-mention |
| sentence:monitor#25 | 5999f48a262b | 881-920 | Selective Serotonin Reuptake Inhibitors | sentence | monitor | monitor.monitor | monitor |  |
| sentence:monitor#26 | b185aa070cba | 915-954 | Selective Serotonin Reuptake Inhibitors | sentence | monitor | monitor.monitor | monitor |  |
| sentence:monitor#27 | ccf95f41af03 | 5593-5607 | Anticonvulsants | sentence | monitor | monitor.monitor | monitor |  |
| sentence:monitor#28 | 6105fa4d2ff0 | 2290-2314 | Anti-inflammatory Agent [TC] | sentence | monitor | monitor.observe_closely | monitor |  |
| sentence:monitor#29 | 81c91e21dbea | 9022-9047 | Tricyclic Antidepressant [EPC] | sentence | monitor | monitor.caution | monitor |  |
| sentence:monitor#30 | fad8d47007ca | 13158-13172 | Anticoagulants | sentence | monitor | monitor.monitor | adjust_dose | under-read |
| sentence:monitor#31 | 8055c063a408 | 3264-3276 | Electrolyte [EPC] | sentence | monitor | monitor.monitor | none | non-mention |
| sentence:monitor#32 | 74ce0c1d55d7 | 5771-5786 | Anticholinergic [EPC] | sentence | monitor | monitor.monitor | monitor |  |
| sentence:monitor#33 | 475b278ba2ac | 2908-2933 | Tricyclic Antidepressant [EPC] | sentence | monitor | monitor.caution | monitor |  |
| sentence:monitor#34 | dc93c8e38d33 | 630-654 | Calcium Channel Blocker [EPC] | sentence | monitor | monitor.monitor | monitor |  |
| sentence:monitor#35 | dabb6c660111 | 3019-3033 | Anticoagulants | sentence | monitor | monitor.monitor | monitor |  |
| sentence:monitor#36 | fe51a5a4635c | 6377-6392 | Corticosteroid [EPC] | sentence | monitor | monitor.monitor | monitor |  |
| sentence:monitor#37 | 485b1d6f4d38 | 1070-1085 | Corticosteroid [EPC] | sentence | monitor | monitor.monitor | monitor |  |
| sentence:monitor#38 | 7db961793673 | 1907-1915 | Diuretics | sentence | monitor | monitor.observe_closely | monitor |  |
| sentence:monitor#39 | cc606e22cfcb | 58-67 | Diuretics | sentence | monitor | monitor.caution | monitor |  |
| sentence:monitor#40 | 21fba46bbd75 | 4884-4898 | Phenothiazine [EPC] | sentence | monitor | monitor.caution | monitor |  |
| sentence:monitor#41 | 336316b8884c | 1051-1069 | Cardiac Glycoside [EPC] | sentence | monitor | monitor.caution | monitor |  |
| sentence:monitor#42 | bd487494715e | 4050-4058 | Estrogen [EPC] | sentence | monitor | monitor.monitor | monitor |  |
| sentence:monitor#43 | f2f254993e2f | 5605-5615 | Macrolide [EPC] | sentence | monitor | monitor.caution | monitor |  |
| sentence:monitor#44 | 03cb496f00c6 | 428-447 | Ophthalmic Solutions | sentence | monitor | monitor.caution | none | non-mention |
| sentence:monitor#45 | 3b293805d7db | 3279-3293 | Anticoagulants | sentence | monitor | monitor.monitor | monitor |  |
| sentence:monitor#46 | 5d34ca7caed0 | 404-429 | CYP3A moderate inhibitor [FDA-CYP] | sentence | monitor | monitor.monitor | monitor |  |
| sentence:monitor#47 | 65bac9768588 | 846-873 | Potassium-sparing Diuretic [EPC] | sentence | monitor | monitor.caution | monitor |  |
| sentence:monitor#48 | b98051dbfc33 | 11464-11478 | Anticoagulants | sentence | monitor | monitor.monitor | monitor |  |
| sentence:monitor#49 | 3cb6b8289f9d | 7393-7408 | Anticonvulsants | sentence | monitor | monitor.caution | monitor |  |
| sentence:monitor#50 | 84de0d642583 | 849-888 | Selective Serotonin Reuptake Inhibitors | sentence | monitor | monitor.monitor | monitor |  |
| sentence:adjust_dose#1 | 395f4721fe2b | 395-408 | Anticoagulants | sentence | adjust_dose | adjust_dose.dose_change | adjust_dose |  |
| sentence:adjust_dose#2 | 368c81300123 | 1022-1045 | CYP2D6 strong inhibitor [FDA-CYP] | sentence | adjust_dose | adjust_dose.titrate | adjust_dose |  |
| sentence:adjust_dose#3 | 11bf802d3313 | 482-496 | Corticosteroid [EPC] | sentence | adjust_dose | adjust_dose.separate_administration | adjust_dose |  |
| sentence:adjust_dose#4 | aae1bcda3f8f | 4674-4693 | Ion Exchange Resins | sentence | adjust_dose | adjust_dose.separate_administration | adjust_dose |  |
| sentence:adjust_dose#5 | 404921a5a33d | 3038-3046 | Antacid [PE] | sentence | adjust_dose | adjust_dose.separate_administration | adjust_dose |  |
| sentence:adjust_dose#6 | 066c3f9ca903 | 6042-6057 | Muscle Relaxant [EPC] | sentence | adjust_dose | adjust_dose.reduce_dose | adjust_dose |  |
| sentence:adjust_dose#7 | ec739d3ec9bf | 4382-4395 | Sulfonylurea [EPC] | sentence | adjust_dose | adjust_dose.dose_adjustment | none | merged-item |
| sentence:adjust_dose#8 | 58fb18775493 | 3460-3478 | Protease Inhibitor [EPC] | sentence | adjust_dose | adjust_dose.dose_cap | adjust_dose |  |
| sentence:adjust_dose#9 | 5715ad981896 | 4614-4630 | Iron Supplement [EPC] | sentence | adjust_dose | adjust_dose.separate_administration | adjust_dose |  |
| sentence:adjust_dose#10 | fd11357f19ce | 2157-2172 | Corticosteroid [EPC] | sentence | adjust_dose | adjust_dose.dose_change | adjust_dose |  |
| sentence:adjust_dose#11 | 7d8d0eeef438 | 4222-4229 | Insulin [EPC] | sentence | adjust_dose | adjust_dose.dose_adjustment | adjust_dose |  |
| sentence:adjust_dose#12 | fca7ec0df533 | 5447-5468 | Bile Acid Sequestrant [EPC] | sentence | adjust_dose | adjust_dose.separate_administration | adjust_dose |  |
| sentence:adjust_dose#13 | 916c93c45950 | 6274-6282 | Diuretics | sentence | adjust_dose | adjust_dose.dose_change | adjust_dose |  |
| sentence:adjust_dose#14 | 27b7a015a21d | 363-375 | Barbiturate [EPC] | sentence | adjust_dose | adjust_dose.dose_change | adjust_dose |  |
| sentence:adjust_dose#15 | d5c60d087089 | 17884-17892 | Narcotics | sentence | adjust_dose | adjust_dose.dose_change | adjust_dose |  |
| sentence:adjust_dose#16 | 7cc5d14d08cc | 350-364 | Anticoagulants | sentence | adjust_dose | adjust_dose.dose_change | adjust_dose |  |
| sentence:adjust_dose#17 | 3258b11c800a | 1080-1096 | Sympathomimetics | sentence | adjust_dose | adjust_dose.dose_adjustment | adjust_dose |  |
| sentence:adjust_dose#18 | 74e1228d8c50 | 544-559 | Benzodiazepine [EPC] | sentence | adjust_dose | adjust_dose.dose_change | adjust_dose |  |
| sentence:adjust_dose#19 | 654ac0e51687 | 4657-4665 | Antacid [PE] | sentence | adjust_dose | adjust_dose.separate_administration | adjust_dose |  |
| sentence:adjust_dose#20 | e1571192e490 | 4052-4071 | Ion Exchange Resins | sentence | adjust_dose | adjust_dose.separate_administration | adjust_dose |  |
| sentence:adjust_dose#21 | 6deb2cd11d33 | 888-897 | Diuretics | sentence | adjust_dose | adjust_dose.dose_change | adjust_dose |  |
| sentence:adjust_dose#22 | 4ce126567e67 | 1893-1900 | Insulin [EPC] | sentence | adjust_dose | adjust_dose.dose_change | adjust_dose |  |
| sentence:adjust_dose#23 | 0f7c4d2aedee | 6420-6427 | Insulin [EPC] | sentence | adjust_dose | adjust_dose.dose_adjustment | adjust_dose |  |
| sentence:adjust_dose#24 | d78eb784906f | 3545-3556 | Anesthetics | sentence | adjust_dose | adjust_dose.titrate | adjust_dose |  |
| sentence:adjust_dose#25 | 710138f23bd2 | 4222-4229 | Insulin [EPC] | sentence | adjust_dose | adjust_dose.dose_adjustment | adjust_dose |  |
| sentence:adjust_dose#26 | 4d1913dbeb21 | 4655-4674 | Ion Exchange Resins | sentence | adjust_dose | adjust_dose.separate_administration | adjust_dose |  |
| sentence:adjust_dose#27 | 35fa3b2de216 | 586-600 | Corticosteroid [EPC] | sentence | adjust_dose | adjust_dose.separate_administration | adjust_dose |  |
| sentence:adjust_dose#28 | 2f7c129b53a6 | 1403-1410 | Insulin [EPC] | sentence | adjust_dose | adjust_dose.dose_change | adjust_dose |  |
| sentence:adjust_dose#29 | 3845184e4820 | 20180-20208 | HMG-CoA Reductase Inhibitor [EPC] | sentence | adjust_dose | adjust_dose.dose_change | adjust_dose |  |
| sentence:adjust_dose#30 | 94312a465947 | 1672-1679 | Insulin [EPC] | sentence | adjust_dose | adjust_dose.dose_change | adjust_dose |  |
| heading_block:avoid#1 | 3f007d051df4 | 2989-3004 | Benzodiazepine [EPC] | heading_block | avoid | avoid.reserve | avoid |  |
| heading_block:avoid#2 | e7fff3119b78 | 2516-2531 | BCRP substrate [FDA-CYP] | heading_block | avoid | avoid.avoid | avoid |  |
| heading_block:avoid#3 | 8ee2fa5e93ce | 640-655 | Anticholinergic [EPC] | heading_block | avoid | avoid.not_recommended | avoid |  |
| heading_block:avoid#4 | affcd3a026b3 | 3132-3148 | Muscle Relaxant [EPC] | heading_block | avoid | avoid.reserve | avoid |  |
| heading_block:avoid#5 | ce568a0fcc6a | 1327-1346 | Sedatives/Hypnotics [TC] | heading_block | avoid | avoid.reserve | avoid |  |
| heading_block:avoid#6 | cefdf319325e | 3140-3155 | Benzodiazepine [EPC] | heading_block | avoid | avoid.reserve | avoid |  |
| heading_block:avoid#7 | b45507617090 | 163-171 | Antacid [PE] | heading_block | avoid | avoid.avoid | avoid |  |
| heading_block:avoid#8 | 145e2c6821af | 307-341 | Central Nervous System Depressant [EPC] | heading_block | avoid | avoid.reserve | avoid |  |
| heading_block:avoid#9 | d0736339cf92 | 245-260 | Benzodiazepine [EPC] | heading_block | avoid | avoid.reserve | avoid |  |
| heading_block:avoid#10 | 36071f0799d0 | 1161-1180 | Sedatives/Hypnotics [TC] | heading_block | avoid | avoid.reserve | avoid |  |
| heading_block:avoid#11 | 8dba4c126e66 | 4634-4649 | Benzodiazepine [EPC] | heading_block | avoid | avoid.reserve | avoid |  |
| heading_block:avoid#12 | dc6743d0b1da | 6742-6770 | Monoamine Oxidase Inhibitor [EPC] | heading_block | avoid | avoid.not_recommended | avoid |  |
| heading_block:avoid#13 | edd7acbfd412 | 165-180 | Benzodiazepine [EPC] | heading_block | avoid | avoid.reserve | avoid |  |
| heading_block:avoid#14 | 5f41e559ceac | 2736-2751 | Benzodiazepine [EPC] | heading_block | avoid | avoid.reserve | avoid |  |
| heading_block:avoid#15 | a6bab208807d | 5900-5928 | Monoamine Oxidase Inhibitor [EPC] | heading_block | avoid | avoid.do_not_use | avoid |  |
| heading_block:avoid#16 | 7a15786104b4 | 3971-3999 | Monoamine Oxidase Inhibitor [EPC] | heading_block | avoid | avoid.not_recommended | avoid |  |
| heading_block:avoid#17 | e68af9c0f030 | 5804-5820 | Muscle Relaxant [EPC] | heading_block | avoid | avoid.reserve | avoid |  |
| heading_block:avoid#18 | 45646b8be92a | 24-41 | Local Anesthetic [EPC] | heading_block | avoid | avoid.avoid | avoid |  |
| heading_block:avoid#19 | 6d09dce82fc7 | 2938-2955 | Opioid Analgesic [EPC] | heading_block | avoid | avoid.avoid | avoid |  |
| heading_block:avoid#20 | 43bff8ca79ba | 4354-4371 | Opioid Analgesic [EPC] | heading_block | avoid | avoid.avoid | avoid |  |
| heading_block:adjust_dose#1 | d2b01991648d | 1576-1583 | Insulin [EPC] | heading_block | adjust_dose | adjust_dose.dose_change | adjust_dose |  |
| heading_block:adjust_dose#2 | 46083ac103b0 | 1576-1583 | Insulin [EPC] | heading_block | adjust_dose | adjust_dose.dose_change | adjust_dose |  |
| heading_block:adjust_dose#3 | 5a718ce6e716 | 1761-1771 | Macrolide [EPC] | heading_block | adjust_dose | adjust_dose.dose_cap | adjust_dose |  |
| heading_block:adjust_dose#4 | 467f20e390d0 | 4327-4342 | Benzodiazepine [EPC] | heading_block | adjust_dose | adjust_dose.dose_adjustment | avoid | under-read |
| heading_block:adjust_dose#5 | 0818538d0431 | 5320-5336 | Muscle Relaxant [EPC] | heading_block | adjust_dose | adjust_dose.reduce_dose | adjust_dose |  |
| heading_block:adjust_dose#6 | 890ac1d1c7f6 | 5958-5967 | Diuretics | heading_block | adjust_dose | adjust_dose.dose_change | adjust_dose |  |
| heading_block:adjust_dose#7 | 727288804105 | 7766-7775 | Diuretics | heading_block | adjust_dose | adjust_dose.dose_change | adjust_dose |  |
| heading_block:adjust_dose#8 | 83594f11ea5f | 3701-3723 | Bile Acid Sequestrant [EPC] | heading_block | adjust_dose | adjust_dose.separate_administration | adjust_dose |  |
| heading_block:adjust_dose#9 | 6e540cd8db20 | 4346-4361 | Anticonvulsants | heading_block | adjust_dose | adjust_dose.dose_adjustment | adjust_dose |  |
| heading_block:adjust_dose#10 | ecc2a36e2443 | 7649-7674 | Skeletal Muscle Relaxant [EPC] | heading_block | adjust_dose | adjust_dose.reduce_dose | adjust_dose |  |
| heading_block:adjust_dose#11 | 739d4f89334f | 1197-1206 | Estrogen [EPC] | heading_block | adjust_dose | adjust_dose.dose_change | adjust_dose |  |
| heading_block:adjust_dose#12 | 8bb92cad21bc | 24-48 | CYP2D6 strong inhibitor [FDA-CYP] | heading_block | adjust_dose | adjust_dose.dose_cap | adjust_dose |  |
| heading_block:adjust_dose#13 | aad9079874ff | 1722-1741 | Protease Inhibitor [EPC] | heading_block | adjust_dose | adjust_dose.dose_cap | adjust_dose |  |
| heading_block:adjust_dose#14 | 1a256c850c81 | 7006-7022 | Muscle Relaxant [EPC] | heading_block | adjust_dose | adjust_dose.reduce_dose | adjust_dose |  |
| heading_block:adjust_dose#15 | 16f60836ce1e | 7631-7640 | Diuretics | heading_block | adjust_dose | adjust_dose.dose_change | adjust_dose |  |
| heading_block:adjust_dose#16 | 00b49019e5d2 | 1553-1565 | Sulfonylurea [EPC] | heading_block | adjust_dose | adjust_dose.dose_change | adjust_dose |  |
| heading_block:adjust_dose#17 | e6ea8fd59c31 | 7195-7211 | Muscle Relaxant [EPC] | heading_block | adjust_dose | adjust_dose.reduce_dose | adjust_dose |  |
| heading_block:adjust_dose#18 | 97f5e872da2f | 340-348 | Diuretics | heading_block | adjust_dose | adjust_dose.dose_change | adjust_dose |  |
| heading_block:adjust_dose#19 | 0810fc19c452 | 5545-5561 | Muscle Relaxant [EPC] | heading_block | adjust_dose | adjust_dose.reduce_dose | adjust_dose |  |
| heading_block:adjust_dose#20 | 7ff2bd9f9b23 | 1290-1299 | Macrolide [EPC] | heading_block | adjust_dose | adjust_dose.dose_change | adjust_dose |  |
| heading_block:monitor#1 | 632f8f346956 | 2076-2089 | Anticoagulants | heading_block | monitor | monitor.monitor | monitor |  |
| heading_block:monitor#2 | 762c55006f31 | 237-266 | Carbonic Anhydrase Inhibitor [EPC] | heading_block | monitor | monitor.monitor | monitor |  |
| heading_block:monitor#3 | 60582e508882 | 1807-1820 | Anticoagulants | heading_block | monitor | monitor.monitor | monitor |  |
| heading_block:monitor#4 | ca637f7535f9 | 1853-1869 | Muscle Relaxant [EPC] | heading_block | monitor | monitor.observe | monitor |  |
| heading_block:monitor#5 | 9ae9199c1e1f | 1526-1565 | Selective Serotonin Reuptake Inhibitors | heading_block | monitor | monitor.observe | monitor |  |
| heading_block:monitor#6 | 727ecb331692 | 4025-4049 | Calcium Channel Blocker [EPC] | heading_block | monitor | monitor.monitor | monitor |  |
| heading_block:monitor#7 | 161b722d74e3 | 6143-6168 | Tricyclic Antidepressant [EPC] | heading_block | monitor | monitor.observe | monitor |  |
| heading_block:monitor#8 | 24eeef89a61e | 3310-3326 | Anticholinergic [EPC] | heading_block | monitor | monitor.monitor | monitor |  |
| heading_block:monitor#9 | be231956cbcc | 1138-1145 | Insulin [EPC] | heading_block | monitor | monitor.monitor | monitor |  |
| heading_block:monitor#10 | 888fb65f29e3 | 4445-4452 | Insulin [EPC] | heading_block | monitor | monitor.monitor | monitor |  |
| heading_block:monitor#11 | 5b7e15b3fb53 | 3658-3673 | Anticholinergic [EPC] | heading_block | monitor | monitor.monitor | monitor |  |
| heading_block:monitor#12 | 19d6e27bef5a | 8410-8425 | Corticosteroid [EPC] | heading_block | monitor | monitor.monitor | monitor |  |
| heading_block:monitor#13 | a793367e93a4 | 204-233 | Carbonic Anhydrase Inhibitor [EPC] | heading_block | monitor | monitor.monitor | monitor |  |
| heading_block:monitor#14 | 95f861b1da69 | 5803-5818 | Anticholinergic [EPC] | heading_block | monitor | monitor.monitor | monitor |  |
| heading_block:monitor#15 | 2474cb974c1a | 2770-2795 | Tricyclic Antidepressant [EPC] | heading_block | monitor | monitor.caution | monitor |  |
| heading_block:monitor#16 | 6c2afed6f4a4 | 235-264 | Carbonic Anhydrase Inhibitor [EPC] | heading_block | monitor | monitor.monitor | monitor |  |
| heading_block:monitor#17 | 54bf54c81d53 | 4258-4287 | Neuromuscular Blocking Agents | heading_block | monitor | monitor.monitor | monitor |  |
| heading_block:monitor#18 | e5a238aef3f0 | 1549-1585 | Nonsteroidal Anti-inflammatory Drug [EPC] | heading_block | monitor | monitor.monitor | monitor |  |
| heading_block:monitor#19 | 050fe8eacea4 | 2096-2104 | Diuretics | heading_block | monitor | monitor.monitor | monitor |  |
| heading_block:monitor#20 | f7140b021b2d | 2516-2552 | Nonsteroidal Anti-inflammatory Drug [EPC] | heading_block | monitor | monitor.caution | monitor |  |
| sentence:not_read#1 | f7d1d710f7fb | 4339-4348 | Diuretics | sentence | not_read | None | monitor | under-read |
| sentence:not_read#2 | f2c0f08bda41 | 3380-3404 | Anti-inflammatory Agent [TC] | sentence | not_read | None | none |  |
| sentence:not_read#3 | 93ed0dfaa858 | 251-265 | Anticoagulants | sentence | not_read | None | none |  |
| sentence:not_read#4 | 531835cd0e93 | 4668-4678 | Anesthetics | sentence | not_read | None | monitor | under-read |
| sentence:not_read#5 | 3258b11c800a | 550-567 | Opioid Analgesic [EPC] | sentence | not_read | None | none |  |
| sentence:not_read#6 | ffaf06599f70 | 4269-4290 | Neprilysin Inhibitor [EPC] | sentence | not_read | None | none |  |
| sentence:not_read#7 | 5239bdd0e038 | 25567-25578 | Cannabinoid [EPC] | sentence | not_read | None | none | non-mention |
| sentence:not_read#8 | 88a75d2011e0 | 7071-7118 | Non-Nucleoside Reverse Transcriptase Inhibitors [MoA] | sentence | not_read | None | none |  |
| sentence:not_read#9 | 1a21847bddde | 2584-2593 | Progestin [EPC] | sentence | not_read | None | none |  |
| sentence:not_read#10 | e34700e72226 | 111-127 | Muscle Relaxant [EPC] | sentence | not_read | None | none |  |
| sentence:not_read#11 | fd0e2f0f4ae5 | 16610-16618 | Estrogen [EPC] | sentence | not_read | None | none | ctx:monitor |
| sentence:not_read#12 | 3f3eea91b815 | 86-101 | Sympathomimetics | sentence | not_read | None | none | ctx:contraindicated |
| sentence:not_read#13 | 620bd6cb327a | 186-196 | Analgesics | sentence | not_read | None | none |  |
| sentence:not_read#14 | ebd12f2b7d8f | 3122-3137 | Corticosteroid [EPC] | sentence | not_read | None | none |  |
| sentence:not_read#15 | 8721e2ac6081 | 2800-2815 | Anticholinergic [EPC] | sentence | not_read | None | none | ctx:adjust_dose |
| sentence:not_read#16 | bbf62fa3bb10 | 5166-5182 | Muscle Relaxant [EPC] | sentence | not_read | None | none |  |
| sentence:not_read#17 | 822ead9fc9e7 | 5944-5953 | Diuretics | sentence | not_read | None | avoid | under-read |
| sentence:not_read#18 | f8b898f66c70 | 1348-1363 | Enzyme Inducers [MoA] | sentence | not_read | None | none |  |
| sentence:not_read#19 | ea1acf62917f | 4585-4602 | Azole Antifungal [EPC] | sentence | not_read | None | monitor | under-read |
| sentence:not_read#20 | 67274d2345b2 | 3466-3481 | Antiarrhythmic [EPC] | sentence | not_read | None | none | ctx:adjust_dose |
| sentence:not_read#21 | a12b8c6845fd | 2208-2223 | Glucocorticoids | sentence | not_read | None | none |  |
| sentence:not_read#22 | d4cea41b7b01 | 9295-9306 | Barbiturate [EPC] | sentence | not_read | None | none | ctx:monitor |
| sentence:not_read#23 | bd29dbd2e8dd | 3906-3953 | Non-Nucleoside Reverse Transcriptase Inhibitors [MoA] | sentence | not_read | None | none | ctx:adjust_dose |
| sentence:not_read#24 | 1c820593a8a7 | 560-574 | Phenothiazine [EPC] | sentence | not_read | None | none |  |
| sentence:not_read#25 | db74fa4a1e21 | 153-181 | Monoamine Oxidase Inhibitor [EPC] | sentence | not_read | None | none | ctx:avoid |
| sentence:not_read#26 | 6410b04d383a | 671-685 | Antiarrhythmic [EPC] | sentence | not_read | None | none |  |
| sentence:not_read#27 | ae2dbed94ccd | 93-102 | Estrogen [EPC] | sentence | not_read | None | no_meaningful_interaction |  |
| sentence:not_read#28 | c0119106fae7 | 1718-1733 | Benzodiazepine [EPC] | sentence | not_read | None | none |  |
| sentence:not_read#29 | d0762cad0547 | 1314-1337 | HIV Protease Inhibitors | sentence | not_read | None | no_meaningful_interaction |  |
| sentence:not_read#30 | 3b27d9468de0 | 1717-1741 | Calcium Channel Blocker [EPC] | sentence | not_read | None | none |  |
| sentence:not_read#31 | 4d1a205d1eb8 | 1002-1016 | Phenothiazine [EPC] | sentence | not_read | None | none |  |
| sentence:not_read#32 | 9f2bd578e117 | 8434-8462 | Phosphodiesterase Inhibitors | sentence | not_read | None | none |  |
| sentence:not_read#33 | f3d3fd6bc85c | 5208-5225 | Mineralocorticoids | sentence | not_read | None | none |  |
| sentence:not_read#34 | d3e0b3a51be0 | 431-444 | Catecholamine [EPC] | sentence | not_read | None | none |  |
| sentence:not_read#35 | 9beae424d3b5 | 1240-1255 | Anticonvulsants | sentence | not_read | None | none |  |
| sentence:not_read#36 | 9ace5ef5e0e0 | 5108-5122 | Corticosteroid [EPC] | sentence | not_read | None | avoid | under-read |
| sentence:not_read#37 | 95e01599d0e4 | 3251-3266 | Anticholinergic [EPC] | sentence | not_read | None | none | ctx:monitor |
| sentence:not_read#38 | d1cb6175f769 | 4014-4029 | Muscle Relaxant [EPC] | sentence | not_read | None | none |  |
| sentence:not_read#39 | 9667f5c3c65c | 5770-5785 | Anticholinergic [EPC] | sentence | not_read | None | none | ctx:monitor |
| sentence:not_read#40 | c570d3fee17a | 17351-17368 | Azole Antifungal [EPC] | sentence | not_read | None | avoid | under-read |
| sentence:not_read#41 | 63f854b49e02 | 1349-1362 | Sympatholytics | sentence | not_read | None | none |  |
| sentence:not_read#42 | 48941e70254a | 4169-4192 | Antihypertensive Agents | sentence | not_read | None | none |  |
| sentence:not_read#43 | 7c4072a15c04 | 2863-2879 | Muscle Relaxant [EPC] | sentence | not_read | None | none | ctx:avoid |
| sentence:not_read#44 | 8fc1d8eac8c1 | 4019-4034 | Corticosteroid [EPC] | sentence | not_read | None | none |  |
| sentence:not_read#45 | ac6921fc2a73 | 2512-2520 | Estrogen [EPC] | sentence | not_read | None | none |  |
| sentence:not_read#46 | 8e57fe91d88e | 3787-3805 | Protease Inhibitor [EPC] | sentence | not_read | None | none |  |
| sentence:not_read#47 | 3e2413b551cf | 2211-2219 | Steroid [EPC] | sentence | not_read | None | none |  |
| sentence:not_read#48 | a3aa27e54d46 | 834-846 | Barbiturate [EPC] | sentence | not_read | None | none |  |
| sentence:not_read#49 | 241f1f8f87a0 | 3341-3355 | Corticosteroid [EPC] | sentence | not_read | None | none |  |
| sentence:not_read#50 | be862d9f067e | 193-218 | Tricyclic Antidepressant [EPC] | sentence | not_read | None | none |  |

</details>

Flags: `members` advice about a named class member; `subject` subject-in-class; `merged-item`
the machine's level came from another item of a merged bullet/table run; `other-clause` from
another drug's clause; `non-mention` / `effect-adjective` the span is not a class mention;
`under-read` machine below the label; `ctx:<level>` advice just outside the scope.

## Gates

Owner's gates (design 7), applied to the final draw (run 14 readings; run 15 ships them with
the withheld pairs mapped to not_read):

| gate | result | verdict |
|---|---|---|
| `no_meaningful_interaction`: zero wrong readings | 0 spans read NMI on every run | **met (vacuous)**; the level ships empty |
| zero contraindicated read lower -- random strata | 0 in draw 3 (the one in draw 2 was fixed by iteration 2) | met |
| zero contraindicated read lower -- targeted hunt | **4 garbled-text spans** still read not_read on run 14/15 | **NOT met** (see below) |
| avoid >= 90%, sentence | 39/50 = 78% (83% excluding non-mentions) | **failed -> withheld** |
| avoid >= 90%, heading block | 20/20 = 100% | met, ships |
| adjust_dose >= 90%, sentence | 29/30 = 97% (draw 2: 48/50 = 96%) | met, ships |
| adjust_dose >= 90%, heading block | 19/20 = 95% | met, ships |
| monitor >= 90%, sentence | 44/50 = 88% (93.6% excluding non-mentions) | **failed -> withheld** |
| monitor >= 90%, heading block | 20/20 = 100% | met, ships |

**Withheld (run 15): sentence-scope avoid and sentence-scope monitor**, mapped to not_read in
`read_advice` (`spl_advice.WITHHELD`, tested), after the two permitted tuning iterations.
Reason: the misses are another drug's advice in a merged bullet run or table row, which a
phrase reader cannot attribute, plus non-mention spans. What ships: contraindicated (sentence
4,444, block 930), adjust_dose (sentence 15,824, block 9,696), avoid and monitor from heading /
row blocks only (8,761 and 12,047). Heading-block strata are n = 20: one miss is 5 points.

**Open, for a ruling.** (1) The 4 garbled contraindication spans fail the zero-tolerance gate
read strictly; fixing them needs a third tuning iteration (three regex variants: a split
"s ee", "CONTRAINDICATIONS" glued to the next word, the truncated "Contraindicate"), which the
brief does not permit without a ruling. (2) A hygiene ruling would lift sentence monitor over
the gate (93.6% without the non-mentions): `Electrolyte [EPC]` matching the lab analyte
("monitor electrolytes"), the dosage-form class `Ophthalmic Solutions`, and class words inside
"non-potassium-sparing" / "anti-fibrinolytic". Sentence avoid stays under the gate either way.

