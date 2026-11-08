# SPL class mentions -- measurement record (Task 9A: census)

Date of run: 2026-11-08. Design: [spl class mentions](2026-11-08-drugref-spl-class-mentions-design.md) §7.
Rule 6: this record carries counts and class names only -- no label prose.
Sections "Precision sample" and "Gates" are Task 9B's.

## Provenance

| item | value |
|---|---|
| git head at build | `d165f39` plus the Task 9A commits (tool, scope fixes R16/R17) |
| database | `drugref_classmentions`, built from nothing (createdb, then migrate through db/066) |
| SPL run | ingest_run 11 (the rebuild below). Run 9 aborted, run 10 is the superseded first full run; run 9 is left as an unfinished row |
| SPL release tag | `openfda-2026-08-22+dailymed-2026-08-21` |
| other releases | UNII 26Feb2026, MED-RT 2026.07.06, MeSH 2026 (+ mesh-relations 2026.07.06), GSRS 2026-02-26, ONCHIGH 2026-08-12, FDA-CYP 2026-05-29, DrugCentral 11012023 |
| checksums | all 20 files (14 openFDA partitions, 6 DailyMed parts) `shasum -a 256`-verified equal to the table in the [mining record §2](2026-08-24-drugref-slice-5c3-spl-mining-measurement.md); no mismatch |
| corpus | 262,032 openFDA records, 68,550 labels carrying the section, 27,406 distinct wordings, 54,813 DailyMed documents read |

Wall times (this machine, single run each, no controls): migrate 1 s -- ingest chain 159 s --
onchigh 4 s -- fda-cyp 5 s -- drugcentral 20 s -- `ingest spl` 274 s (the rebuild, run 11; first run 228 s, the aborted attempt died after 93 s) -- census 47 s with `--openfda`.

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

## Headline (run 11)

* Drug x drug pairs **29,952 / 26,598 novel -- identical** to the 5c.3 record and unchanged by
  the class pass or by R16/R17 (floors 29,258 / 25,960 cleared).
* **249,444 mentions (distinct spans)**, as 330,597 (span, class) rows, over 23,950 of 27,406
  wordings (87.4%). 64,924 spans (26.0%) are ambiguous (fold onto >1 class).
* Statements (labelling drug x class): 14,671 over 474 classes (sum of the per-axis table).
* Advice per SPAN: **79.3% not_read**; of 51,653 strength spans 14.2% are mixed (avoid 24.0%,
  adjust_dose 43.7%, contraindicated 19.8%).
* **Zero spans read `no_meaningful_interaction`**: the R10 templates match nothing on the real
  release, so that level has no sample to clear a gate (9B: vacuous gate or withheld level).
* Quote windows cover 25.7% of spans (64,090 of 249,444).
* 11,107 moiety occurrences lie inside a class span (6,344 wordings).
* 283 wordings (783 labels) CONTAIN the long-form enzyme spelling near a role word (presence,
  not a measured miss: whether the class vocabulary also catches them is unmeasured).
* 30,632 of 330,597 rows (9.3%) are within 80 chars of "Intervention" / "Clinical Impact".

## Still for the 9B reader

* `Acetaminophen [EPC]` (empty class) remains the 9th-largest class by rows (6,588 over 1,559
  wordings): its key does not equal a registry display name, so R16(b) did not drop it, yet
  the span is a drug mention. `Enzyme`, `Calcium` and the APC axis are gone from the top 15.
* The two axes still duplicate each other where MeSH PA and MED-RT PE/TC/MoA/EPC share a name
  (Diuretics / Antacids / Analgesics / Monoamine Oxidase Inhibitors): ambiguity is real
  (26.0% of spans), and statements count each class separately.
* MED-RT PE: 1,291 classes with no direct member, 1 ever mentioned.

## Census tables (verbatim tool output of run 11)

### Totals: (span, class) rows vs distinct spans (mentions)

| span_class_rows | spans | ambiguous_spans | ambiguous_pct | wordings |
|---|---|---|---|---|
| 330,597 | 249,444 | 64,924 | 26.0 | 23,950 |

### Per axis, empty vs populated: (span, class) rows, distinct spans, wordings (`labels` = the same wordings weighted by label_count)

| axis | pop | span_class_rows | spans | wordings | labels |
|---|---|---|---|---|---|
| FDA-CYP PK | empty | 540 | 540 | 235 | 502 |
| FDA-CYP PK | populated | 7,066 | 7,066 | 1,959 | 3,612 |
| MED-RT EPC | empty | 19,044 | 19,044 | 7,861 | 19,128 |
| MED-RT EPC | populated | 124,790 | 124,790 | 19,505 | 48,384 |
| MED-RT MoA | empty | 2,507 | 2,507 | 1,335 | 2,800 |
| MED-RT MoA | populated | 29,997 | 29,997 | 11,078 | 25,671 |
| MED-RT PE | empty | 1 | 1 | 1 | 1 |
| MED-RT PE | populated | 9,527 | 9,527 | 4,415 | 11,156 |
| MED-RT TC | populated | 20,836 | 20,836 | 7,307 | 17,218 |
| MeSH PA | empty | 112 | 112 | 83 | 326 |
| MeSH PA | populated | 116,177 | 116,177 | 20,331 | 50,976 |

### Classes mentioned at least once vs classes matchable, per axis

| axis | pop | classes | mentioned |
|---|---|---|---|
| FDA-CYP PK | empty | 5 | 3 |
| FDA-CYP PK | populated | 60 | 38 |
| MED-RT EPC | empty | 272 | 27 |
| MED-RT EPC | populated | 539 | 149 |
| MED-RT MoA | empty | 242 | 4 |
| MED-RT MoA | populated | 539 | 83 |
| MED-RT PE | empty | 1,291 | 1 |
| MED-RT PE | populated | 582 | 18 |
| MED-RT TC | empty | 3 | 0 |
| MED-RT TC | populated | 63 | 23 |
| MeSH PA | empty | 67 | 8 |
| MeSH PA | populated | 501 | 144 |

### Distinct statements (labelling drug x class; label-derived, not per wording) and classes gaining at least one, per axis

| axis | statements | classes | subject_in_class |
|---|---|---|---|
| FDA-CYP PK | 747 | 41 | 31 |
| MED-RT EPC | 6,338 | 169 | 166 |
| MED-RT MoA | 1,440 | 83 | 78 |
| MED-RT PE | 374 | 15 | 7 |
| MED-RT TC | 994 | 22 | 126 |
| MeSH PA | 4,778 | 144 | 431 |

### Yield per match_form (§4.2 key form): rows, spans, ambiguous spans

| match_form | span_class_rows | spans | wordings | classes | ambiguous_spans |
|---|---|---|---|---|---|
| label_order | 5,404 | 5,404 | 1,522 | 26 | 0 |
| number | 157,493 | 146,653 | 21,540 | 325 | 51,613 |
| stored | 167,700 | 140,528 | 21,658 | 374 | 56,452 |

### Moiety spans lying inside a class span (§4.4 overlap; equal spans count)

| moiety_occurrences | wordings |
|---|---|
| 11,107 | 6,344 |

### Advice level distribution (per SPAN)

| advice_level | spans | pct | wordings | mixed |
|---|---|---|---|---|
| not_read | 197,791 | 79.29 | 23,240 | 0 |
| monitor | 24,870 | 9.97 | 8,219 | 0 |
| avoid | 18,385 | 7.37 | 6,131 | 4,418 |
| adjust_dose | 5,318 | 2.13 | 2,963 | 2,325 |
| contraindicated | 3,080 | 1.23 | 1,203 | 609 |

### Level x scope (per span)

| advice_level | advice_scope | spans |
|---|---|---|
| adjust_dose | heading_block | 339 |
| adjust_dose | sentence | 4,979 |
| avoid | heading_block | 1,762 |
| avoid | sentence | 16,623 |
| contraindicated | heading_block | 180 |
| contraindicated | sentence | 2,900 |
| monitor | heading_block | 3,961 |
| monitor | sentence | 20,909 |
| not_read | sentence | 197,791 |

### Level x axis ((span, class) rows)

| axis | advice_level | span_class_rows |
|---|---|---|
| FDA-CYP PK | adjust_dose | 505 |
| FDA-CYP PK | avoid | 2,193 |
| FDA-CYP PK | contraindicated | 235 |
| FDA-CYP PK | monitor | 430 |
| FDA-CYP PK | not_read | 4,243 |
| MED-RT EPC | adjust_dose | 3,626 |
| MED-RT EPC | avoid | 10,120 |
| MED-RT EPC | contraindicated | 2,030 |
| MED-RT EPC | monitor | 11,823 |
| MED-RT EPC | not_read | 116,235 |
| MED-RT MoA | adjust_dose | 488 |
| MED-RT MoA | avoid | 3,253 |
| MED-RT MoA | contraindicated | 935 |
| MED-RT MoA | monitor | 2,559 |
| MED-RT MoA | not_read | 25,269 |
| MED-RT PE | adjust_dose | 46 |
| MED-RT PE | avoid | 956 |
| MED-RT PE | contraindicated | 5 |
| MED-RT PE | monitor | 536 |
| MED-RT PE | not_read | 7,985 |
| MED-RT TC | adjust_dose | 144 |
| MED-RT TC | avoid | 2,136 |
| MED-RT TC | contraindicated | 186 |
| MED-RT TC | monitor | 1,929 |
| MED-RT TC | not_read | 16,441 |
| MeSH PA | adjust_dose | 1,516 |
| MeSH PA | avoid | 8,288 |
| MeSH PA | contraindicated | 1,156 |
| MeSH PA | monitor | 14,421 |
| MeSH PA | not_read | 90,908 |

### Phrase rules that fired (per span)

| rule | spans | wordings |
|---|---|---|
| (not_read) | 197,791 | 23,240 |
| monitor.monitor | 22,586 | 7,525 |
| avoid.avoid | 12,341 | 3,781 |
| avoid.not_recommended | 3,718 | 1,996 |
| contraindicated.contraindicated | 3,080 | 1,203 |
| adjust_dose.dose_adjustment | 2,903 | 1,458 |
| adjust_dose.reduce_dose | 2,415 | 1,617 |
| monitor.observe_closely | 2,284 | 1,381 |
| avoid.do_not_use | 1,931 | 752 |
| avoid.do_not_coadminister | 395 | 206 |

### Spans covered by an existing quote window, per level

| advice_level | spans | quoted |
|---|---|---|
| adjust_dose | 5,318 | 1,035 |
| avoid | 18,385 | 4,654 |
| contraindicated | 3,080 | 1,276 |
| monitor | 24,870 | 5,084 |
| not_read | 197,791 | 52,041 |

### TOP OFFENDERS: the 15 classes with most (span, class) rows (a class matching ordinary English shows up here)

| class_name | axis | pop | span_class_rows | wordings |
|---|---|---|---|---|
| Diuretics | MeSH PA | populated | 17,118 | 5,552 |
| Anticoagulants | MeSH PA | populated | 16,178 | 6,432 |
| Corticosteroid [EPC] | MED-RT EPC | populated | 9,939 | 3,129 |
| Benzodiazepine [EPC] | MED-RT EPC | populated | 9,241 | 3,272 |
| Anticholinergic [EPC] | MED-RT EPC | populated | 7,661 | 2,386 |
| Antacid [PE] | MED-RT PE | populated | 7,553 | 3,464 |
| Antacids | MeSH PA | populated | 7,553 | 3,464 |
| Tricyclic Antidepressant [EPC] | MED-RT EPC | populated | 7,343 | 4,334 |
| Acetaminophen [EPC] | MED-RT EPC | empty | 6,588 | 1,559 |
| Analgesics [TC] | MED-RT TC | populated | 5,654 | 2,730 |
| Analgesics | MeSH PA | populated | 5,654 | 2,730 |
| Monoamine Oxidase Inhibitors | MeSH PA | populated | 5,580 | 4,063 |
| Monoamine Oxidase Inhibitor [EPC] | MED-RT EPC | populated | 5,580 | 4,063 |
| Monoamine Oxidase Inhibitors [MoA] | MED-RT MoA | populated | 5,580 | 4,063 |
| Estrogens | MeSH PA | populated | 5,509 | 2,657 |

### Drug x drug pairs (unchanged by the class pass; floors 29,258 / 25,960; last recorded run 29,952 / 26,598)

| pairs | novel |
|---|---|
| 29,952 | 26,598 |

### Population (wordings and labels the class pass ran over)

| wordings | labels | wordings_with_a_class |
|---|---|---|
| 27,406 | 68,550 | 23,950 |

### Presence of the parenthetical spelling (corpus)

| figure | wordings | labels |
|---|---|---|
| wordings WITH the long-form enzyme spelling near a role word (presence) | 283 | 783 |

### Mentions near a table-row label (corpus)

| figure | span_class_rows | wordings | of all rows |
|---|---|---|---|
| (span, class) rows within 80 chars of Intervention/Clinical Impact | 30,632 | 2,804 | 330,597 |


### Notes on reading these tables

* "pop" = the class has at least one direct `class_membership` row. MED-RT PK and APC are
  excluded from matching by design (so absent); FDA-CYP is also `PK` and is matched.
* Units: "span_class_rows" = (span, class) rows; "spans" = distinct mentions. A reading
  depends only on the span, so level, rule, scope and quote-coverage tables are per span.
  "Level x axis" is per row because an axis belongs to the class.
* `labels` weights the same distinct wordings by `label_count` (the ~2.5x ratio made visible).
* Statements are label-derived (subject moiety x class), so they have no wording grain;
  `subject_in_class` = the labelling drug is itself a member of the class.
* The sentence-splitter failure signal is the boundary-drop count (64), taken from the ingest
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
* `Enzyme Inducers [MoA]` (empty, 2,218 spans) is outside the top 25 and was not read; it is the
  next candidate for the same scrutiny.

### Proposed exclusion rules (not implemented; for a controller ruling)

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

## Precision sample

TO BE FILLED BY TASK 9B.

## Gates

TO BE FILLED BY TASK 9B.
