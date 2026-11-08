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

## Precision sample

TO BE FILLED BY TASK 9B.

## Gates

TO BE FILLED BY TASK 9B.
