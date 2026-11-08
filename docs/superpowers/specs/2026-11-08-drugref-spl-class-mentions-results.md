# SPL class mentions -- measurement record (Task 9A: census)

Date of run: 2026-11-08. Design: [spl class mentions](2026-11-08-drugref-spl-class-mentions-design.md) §7.
Rule 6: this record carries counts and class names only -- no label prose.
Sections "Precision sample" and "Gates" are Task 9B's.

## Provenance

| item | value |
|---|---|
| git head at build | `d165f39` plus the uncommitted Task 9A tool (and one scope fix, see "A defect the real release found") |
| database | `drugref_classmentions`, built from nothing (createdb, then migrate through db/066) |
| SPL run | ingest_run 10 (run 9 is the first SPL attempt, which aborted, see below; it is left as an unfinished row) |
| SPL release tag | `openfda-2026-08-22+dailymed-2026-08-21` |
| other releases | UNII 26Feb2026, MED-RT 2026.07.06, MeSH 2026 (+ mesh-relations 2026.07.06), GSRS 2026-02-26, ONCHIGH 2026-08-12, FDA-CYP 2026-05-29, DrugCentral 11012023 |
| checksums | all 20 files (14 openFDA partitions, 6 DailyMed parts) `shasum -a 256`-verified equal to the table in the [mining record §2](2026-08-24-drugref-slice-5c3-spl-mining-measurement.md); no mismatch |
| corpus | 262,032 openFDA records, 68,550 labels carrying the section, 27,406 distinct wordings, 54,813 DailyMed documents read |

Wall times (this machine, single run each, no controls): migrate 1 s -- ingest chain 159 s --
onchigh 4 s -- fda-cyp 5 s -- drugcentral 20 s -- `ingest spl` 228 s (the passing run; the first
attempt died after 93 s) -- census 47 s with `--openfda`.

Reproduce: `uv run python -m tools.spl_class_census --dsn "$DSN" --openfda downloads/OPENFDA`
(the tables below are its output, verbatim, under added commentary).

## A defect the real release found (fixed in this round)

The first `ingest spl` aborted at the class write: db/066's CHECK
`spl_class_occurrence_scope_contains_span` refused a row whose sentence scope ended 4
characters before the mention ended. A class name (MED-RT EPC "Potassium Salt", 15-char
mention) contained a `". X"` sentence boundary inside the mention, so the sentence ended
mid-name. No fixture contained a class name spanning a boundary. Fix in
`spl_advice.read_advice`: the not_read fallback scope is widened to cover the mention and a
heading block that ends before the mention is not used; test
`test_scope_always_contains_a_mention_that_holds_a_sentence_boundary`. This changes no
strength/NMI rule.

## Headline

* Drug x drug pairs **29,952 / 26,598 novel -- identical** to the 5c.3 results record (floors
  29,258 / 25,960 both cleared). The class pass changed no drug x drug figure.
* 391,521 class occurrences over 24,841 of 27,406 wordings (90.6%); 20,265 moiety occurrences lie
  inside a class span (the run summary and the census agree).
* 16,792 distinct (labelling drug x class) statements over 502 classes.
* Advice: **79.7% not_read**; of the 79,648 strength readings, 13.7% are mixed (avoid 22.9%,
  adjust_dose 44.1%, contraindicated 18.3%). **No occurrence was read `no_meaningful_interaction`**
  (the R10 template set matched nothing on the real release): that level has zero sample to
  clear its gate, so Task 9B's gate for it is vacuous or the level is withheld.
* Quote windows cover 25.2% of occurrences (98,494 of 391,521).

## ALARMS for the 9B reader (not fixed in 9A)

* **Ordinary-English / non-class names dominate the counts**: `Enzyme [APC]` 15,587 occurrences
  in 7,951 wordings (APC axis; 20 classes yield 1,808 statements and **none** has the labelling
  drug as a member); `Calcium [EPC]` 9,935 / 2,350 (the word, not necessarily the class);
  `Acetaminophen [EPC]` 6,588 / 1,559 (an *empty* EPC named like the drug). Full top-15 in the
  tables. These are the #155-shape risk; whether they are wrong needs 9B's read.
* **Two axes duplicate each other**: `Diuretics` (MeSH PA) and `Diuretic [APC]` have identical
  counts (17,118), likewise `Antacids` / `Antacid [PE]`, `Analgesics` MeSH / `[TC]`. One span
  matches two classes, which is why `number` and `stored` carry 87,130 / 105,861 ambiguous
  occurrences (about half of all). Occurrences are therefore NOT distinct spans.
* **MED-RT PE has 1,291 classes with no direct member, of which 1 is ever mentioned** -- fine,
  but the empty-class share of the vocabulary is large (PE) while empty-class *mentions* are
  concentrated in APC (50,416) and `Acetaminophen`.

## Census tables (verbatim tool output)

## SPL run 10

### Class occurrences per axis, empty vs populated (per wording; `labels` = the same wordings weighted by label_count)

| axis | pop | occurrences | wordings | labels |
|---|---|---|---|---|
| FDA-CYP PK | empty | 540 | 235 | 502 |
| FDA-CYP PK | populated | 7,076 | 1,967 | 3,627 |
| MED-RT APC | empty | 50,416 | 14,318 | 34,205 |
| MED-RT EPC | empty | 19,066 | 7,864 | 19,131 |
| MED-RT EPC | populated | 135,853 | 19,811 | 49,010 |
| MED-RT MoA | empty | 2,507 | 1,335 | 2,800 |
| MED-RT MoA | populated | 29,998 | 11,078 | 25,671 |
| MED-RT PE | empty | 1 | 1 | 1 |
| MED-RT PE | populated | 9,527 | 4,415 | 11,156 |
| MED-RT TC | populated | 20,836 | 7,307 | 17,218 |
| MeSH PA | empty | 112 | 83 | 326 |
| MeSH PA | populated | 115,589 | 20,280 | 50,904 |

### Classes mentioned at least once vs classes matchable, per axis

| axis | pop | classes | mentioned |
|---|---|---|---|
| FDA-CYP PK | empty | 5 | 3 |
| FDA-CYP PK | populated | 60 | 38 |
| MED-RT APC | empty | 44 | 22 |
| MED-RT EPC | empty | 272 | 28 |
| MED-RT EPC | populated | 539 | 157 |
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
| FDA-CYP PK | 748 | 41 | 31 |
| MED-RT APC | 1,808 | 20 | 0 |
| MED-RT EPC | 6,679 | 177 | 171 |
| MED-RT MoA | 1,440 | 83 | 78 |
| MED-RT PE | 374 | 15 | 7 |
| MED-RT TC | 994 | 22 | 126 |
| MeSH PA | 4,749 | 144 | 427 |

### Yield per match_form (§4.2 key form)

| match_form | occurrences | wordings | classes | ambiguous |
|---|---|---|---|---|
| label_order | 5,404 | 1,522 | 26 | 0 |
| number | 184,548 | 22,672 | 346 | 87,130 |
| stored | 201,569 | 22,503 | 402 | 105,861 |

### Moiety spans lying inside a class span (§4.4 overlap; equal spans count)

| moiety_occurrences | wordings |
|---|---|
| 20,265 | 8,353 |

### Advice level distribution (per occurrence; wordings = distinct wordings)

| advice_level | occurrences | pct | wordings | mixed |
|---|---|---|---|---|
| not_read | 311,873 | 79.66 | 24,254 | 0 |
| monitor | 37,476 | 9.57 | 8,592 | 0 |
| avoid | 30,393 | 7.76 | 6,908 | 6,967 |
| adjust_dose | 6,991 | 1.79 | 3,091 | 3,081 |
| contraindicated | 4,788 | 1.22 | 1,262 | 874 |

### Level x scope

| advice_level | advice_scope | occurrences |
|---|---|---|
| adjust_dose | heading_block | 439 |
| adjust_dose | sentence | 6,552 |
| avoid | heading_block | 2,912 |
| avoid | sentence | 27,481 |
| contraindicated | heading_block | 235 |
| contraindicated | sentence | 4,553 |
| monitor | heading_block | 6,755 |
| monitor | sentence | 30,721 |
| not_read | sentence | 311,873 |

### Level x axis

| axis | advice_level | occurrences |
|---|---|---|
| FDA-CYP PK | adjust_dose | 505 |
| FDA-CYP PK | avoid | 2,193 |
| FDA-CYP PK | contraindicated | 235 |
| FDA-CYP PK | monitor | 430 |
| FDA-CYP PK | not_read | 4,253 |
| MED-RT APC | adjust_dose | 555 |
| MED-RT APC | avoid | 2,337 |
| MED-RT APC | contraindicated | 235 |
| MED-RT APC | monitor | 5,023 |
| MED-RT APC | not_read | 42,266 |
| MED-RT EPC | adjust_dose | 3,740 |
| MED-RT EPC | avoid | 11,265 |
| MED-RT EPC | contraindicated | 2,036 |
| MED-RT EPC | monitor | 12,604 |
| MED-RT EPC | not_read | 125,274 |
| MED-RT MoA | adjust_dose | 488 |
| MED-RT MoA | avoid | 3,253 |
| MED-RT MoA | contraindicated | 935 |
| MED-RT MoA | monitor | 2,559 |
| MED-RT MoA | not_read | 25,270 |
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
| MeSH PA | adjust_dose | 1,513 |
| MeSH PA | avoid | 8,253 |
| MeSH PA | contraindicated | 1,156 |
| MeSH PA | monitor | 14,395 |
| MeSH PA | not_read | 90,384 |

### Phrase rules that fired

| rule | occurrences | wordings |
|---|---|---|
| (not_read) | 311,873 | 24,254 |
| monitor.monitor | 33,364 | 7,907 |
| avoid.avoid | 19,239 | 4,186 |
| avoid.not_recommended | 6,683 | 2,270 |
| contraindicated.contraindicated | 4,788 | 1,262 |
| monitor.observe_closely | 4,112 | 1,381 |
| adjust_dose.dose_adjustment | 4,081 | 1,586 |
| avoid.do_not_use | 3,214 | 761 |
| adjust_dose.reduce_dose | 2,910 | 1,624 |
| avoid.do_not_coadminister | 1,257 | 470 |

### Occurrences covered by an existing quote window, per level

| advice_level | occurrences | quoted |
|---|---|---|
| adjust_dose | 6,991 | 1,535 |
| avoid | 30,393 | 7,856 |
| contraindicated | 4,788 | 1,726 |
| monitor | 37,476 | 7,277 |
| not_read | 311,873 | 80,100 |

### TOP OFFENDERS: the 15 classes with most occurrences (a class matching ordinary English shows up here)

| class_name | axis | pop | occurrences | wordings |
|---|---|---|---|---|
| Diuretics | MeSH PA | populated | 17,118 | 5,552 |
| Diuretic [APC] | MED-RT APC | empty | 17,118 | 5,552 |
| Anticoagulants | MeSH PA | populated | 16,178 | 6,432 |
| Enzyme [APC] | MED-RT APC | empty | 15,587 | 7,951 |
| Corticosteroid [EPC] | MED-RT EPC | populated | 9,939 | 3,129 |
| Calcium [EPC] | MED-RT EPC | populated | 9,935 | 2,350 |
| Benzodiazepine [EPC] | MED-RT EPC | populated | 9,241 | 3,272 |
| Anticholinergic [EPC] | MED-RT EPC | populated | 7,661 | 2,386 |
| Antacids | MeSH PA | populated | 7,553 | 3,464 |
| Antacid [PE] | MED-RT PE | populated | 7,553 | 3,464 |
| Tricyclic Antidepressant [EPC] | MED-RT EPC | populated | 7,343 | 4,334 |
| Acetaminophen [EPC] | MED-RT EPC | empty | 6,588 | 1,559 |
| Analgesics [TC] | MED-RT TC | populated | 5,654 | 2,730 |
| Analgesics | MeSH PA | populated | 5,654 | 2,730 |
| Monoamine Oxidase Inhibitor [EPC] | MED-RT EPC | populated | 5,580 | 4,063 |

### Drug x drug pairs (unchanged by the class pass; floors 29,258 / 25,960; last recorded run 29,952 / 26,598)

| pairs | novel |
|---|---|
| 29,952 | 26,598 |

### Population (wordings and labels the class pass ran over)

| wordings | labels | wordings_with_a_class |
|---|---|---|
| 27,406 | 68,550 | 24,841 |

### Parenthetical-form misses (corpus)

| figure | wordings | labels |
|---|---|---|
| wordings with the parenthetical spelling near a role word | 283 | 783 |

### Mentions near a table-row label (corpus)

| figure | occurrences | wordings | of occurrences |
|---|---|---|---|
| class occurrences within 80 chars of Intervention/Clinical Impact | 36,969 | 2,807 | 391,521 |


### Notes on reading these tables

* "pop" = the class has at least one direct `class_membership` row. MED-RT PK is excluded from
  matching by design (so it is absent); FDA-CYP is also `PK` and is matched.
* `labels` weights the same distinct wordings by `label_count`; it is shown only to make the
  ~2.5x label/wording ratio visible. Every other count is per wording or per occurrence.
* Statements are label-derived (subject moiety x class), so they have no wording grain.
* `subject_in_class` is the share where the labelling drug is itself a member of the named
  class. For APC it is 0 of 1,808.
* The sentence-splitter failure rate (§7) is not separately measurable: the splitter has no
  failure signal. The one failure the release exposed is the section above (a boundary inside
  a mention); the `heading_block` scope share (level x scope table) is the nearest proxy.
* The two corpus-derived figures were computed on the same 68,550-label corpus the database
  was built from: 283 wordings (783 labels) carry the long-form enzyme spelling
  (`cytochrome P450 <n> (CYP...`) with an inhibit/induc/substrate word within 80 characters;
  36,969 of 391,521 occurrences (9.4%), in 2,807 wordings, sit within 80 characters (either
  side) of "Intervention" / "Clinical Impact", i.e. in interaction-table rows.

## Precision sample

TO BE FILLED BY TASK 9B.

## Gates

TO BE FILLED BY TASK 9B.
