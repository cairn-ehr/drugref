# drugref — slice 5c.3: SPL drug × drug interaction evidence (`db/051`)

**Design spec, 2026-08-24.** Rests on two measurement rounds, both of which
changed it:

- [SPL mining measurement](2026-08-24-drugref-slice-5c3-spl-mining-measurement.md)
  — the corpus, the yield, the class vocabulary, the potency band.
- [Subject recovery measurement](2026-08-24-drugref-slice-5c3-subject-recovery-measurement.md)
  — how the subject drug is resolved, and what may be stored of the prose.

**Every figure in this document comes from one of those two.** Nothing here is
re-derived, and where this design and a measurement disagree, the measurement
wins.

---

## 1. Scope

**One grain: drug × drug.** The slice ingests SPL section `34073-7` DRUG
INTERACTIONS and publishes, for each label whose subject drug resolves, the
**known moieties its section names** — as evidence, with offsets and a citation.

**Three things it deliberately does not do**, each settled by measurement rather
than by preference:

- **No relation extraction.** Deciding that a sentence means *contraindicated*
  is a clinical reading. The standing rule is *ingest preserves evidence;
  curation creates clinical judgement*, and this slice holds that line: it
  records that two drugs are named together in an interactions section, never
  what the label says about them.
- **No class grain** (owner's call, 2026-08-24). The class half is where every
  unsolved problem lives: **32.3% of class occurrences name an EMPTY class**,
  MED-RT's PK axis is **97.2% empty** and is not a drug-class vocabulary
  ([#155](https://github.com/cairn-ehr/drugref/issues/155)), and `Diuretics`
  (MeSH) and `Diuretic [APC]` (MED-RT) fold to one string with no cross-source
  class identity. It becomes its own slice, with its own measurement.
- **No potency band.** The band is **pair-scoped, not class-scoped** — FDA's
  footnote 20 bands ciprofloxacin *moderate* and names tizanidine as the
  substrate against which it behaves *strong* — and reading a band off prose is
  relation extraction by another name. The band belongs on a curated assertion.
  This slice's contribution to [#102](https://github.com/cairn-ehr/drugref/issues/102)
  is the measurement that retired its options 1 and 2, not a column.

**What justifies the slice: at least 29,258 distinct candidate pairs, 25,960
(88.7%) novel.** DrugCentral's entire slice was justified on 7,501 at 91% new.

---

## 2. Rule 6 — what may be stored, decided per column

**The determination is [#154](https://github.com/cairn-ehr/drugref/issues/154),
answered by the owner on 2026-08-24: bundle a quoted window only.** The two
publishers of this corpus take opposite positions — NLM disclaims (*"cannot
guarantee the copyright status for any item"*) over labeling *"submitted to the
FDA by companies"*, the DIRIL shape exactly, while **openFDA dedicates the same
bytes CC0 1.0** — so the unit of clearance is the column, as it was for DIRIL.

| what is stored | rule-6 standing |
|---|---|
| entity occurrences, character offsets | **clear under either reading** — facts are not copyrightable |
| `set_id`, `version`, `effective_time` citations | **clear** — a citation is not a copy, and `db/045` already admits citation-only **SPL** references |
| **bounded quoted windows** | **admitted under the owner's determination**, and bounded by §5.4's budget |
| the section text in full | **NOT STORED**, under either reading |

**The budget is a schema constraint, never a convention**, and §5.4 says why:
measured, an unbounded per-occurrence window stores **82.7%** of a section, so a
rule that is merely intended would make "a quoted window" and "the prose" the
same act.

---

## 3. The shape

### 3.1 What was rejected, and why it is worth recording

- **Folding SPL into `exact_ddi_pair`.** Rejected: that view means *an authority
  asserted this pair interacts*. SPL, read without relation extraction, means
  *a label's interactions section names both drugs*. Those are different claims
  and merging them would make the stronger one unfalsifiable.
- **A per-occurrence quoted window.** Rejected on measurement (§5.4): it
  reproduces the section.
- **The rank-0 name heuristic as a subject route.** Rejected (owner's call,
  2026-08-24): **6.2% genuinely wrong** subjects. Recorded, with its calibration
  set, in the recovery measurement §4. Its pair yield is withdrawn pending
  [#158](https://github.com/cairn-ehr/drugref/issues/158) and was never the
  reason for the rejection.
- **Discarding unresolved labels.** Rejected: 19,862 labels are absent from
  today's DailyMed release and may be in tomorrow's. They are recorded as a
  population, per the standing rule that *absence is a population, not a bug*.

### 3.2 What is built

`db/051_spl_ddi_evidence.sql` — one migration, five tables, two views, one gap
view. Plus the parser/orchestrator pair and the source-admission **trio**.

---

## 4. `db/051_spl_ddi_evidence.sql`

### 4.1 Source admission — three edits, one commit

`db/049`'s comment names this failure mode and it is copied here deliberately,
because the failure is **silent**: `ids.canonical_source` folds the source to a
spelling the CHECK does not admit, and a per-source rebuild then deletes nothing
and reports success.

- `drugref.ingest_run` `source` CHECK gains `'SPL'`;
- `drugref.ingest_run` `writer` CHECK gains `'spl_run'`;
- `src/drugref/ids.py` gains `"SPL": "SPL"` and
  `src/drugref/provenance.py` gains `'spl_run'`.

**COPY the live CHECK from the catalog and extend it — never retype it from this
document.** `db/039`'s comment records a stale list that would have DROPPED
`'DRUGREF'`.

### 4.2 `drugref.spl_label` — one row per label, no prose

Identity and provenance for every section-carrying label, **including the ones
whose subject did not resolve**, because that is the recovery register.

| column | note |
|---|---|
| `ingest_run` | FK, as every projection |
| `source` | CHECK `= 'SPL'` |
| `set_id`, `version`, `effective_time` | the citation, and the join key to both corpora |
| `product_type` | nullable — **absence is a population**: it is populated on only 86,574 of 262,032 records |
| `text_key` | FK → `spl_wording`, the wording this label carries |
| PK | `(ingest_run, source, set_id, version)` |

**A label is keyed on `(set_id, version)`, not `set_id` alone.** A revised label
is a new document making its own statement; collapsing versions would silently
prefer whichever the reader happened to ingest last.

### 4.3 `drugref.spl_label_subject` — the route column, on `db/049`'s terms

The subject drug, and **how it resolved or why it did not**. The design is
`drugcentral_ddi_assertion`'s, because the problem is the same one.

| column | note |
|---|---|
| `moiety_uuid` | **NULLABLE** — an unresolved label stays, with a route saying why |
| `route` | CHECK — see below |

Route vocabulary, and the measured population of each:

| route | resolves? | labels | mechanism |
|---|---|---|---|
| `openfda_unii` | yes | 27,494 | openFDA's own `openfda.unii` |
| `dailymed_active_moiety` | yes | 6,498 | SPL `<activeMoiety>` under an **active** ingredient |
| `dailymed_active_substance` | yes | 16 | the salt only — [#67](https://github.com/cairn-ehr/drugref/issues/67), counted apart so it cannot hide |
| `absent_from_dailymed` | no | 19,862 | the label is not in the current Human Rx release |
| `unresolved` | no | 14,680 | present, read, and still unkeyable — including **200 labels carrying a UNII drugref does not hold** |

**The 14,680 includes 14,455 labels the MEASUREMENT never scanned** — unkeyed
labels sharing a wording with a keyed one, skipped as a probe optimisation
because they cannot rescue a *wording*. **The ingest must scan them**: a
label's subject is its own, and one sharing another's wording may be a
different drug. Their pairs are uncounted, which is why §7's yield is a floor.

**Two CHECKs, on `db/049`'s exact pattern**, so the malformed states are
*unrepresentable* rather than merely discouraged:

```sql
CONSTRAINT spl_label_subject_route CHECK (route IN (
    'openfda_unii', 'dailymed_active_moiety', 'dailymed_active_substance',
    'absent_from_dailymed', 'unresolved')),
CONSTRAINT spl_label_subject_complete CHECK (
    (route IN ('openfda_unii', 'dailymed_active_moiety',
               'dailymed_active_substance')) = (moiety_uuid IS NOT NULL))
```

**The route CHECK is the vocabulary's SECOND home**, admitted deliberately on
the same terms `drugcentral_ddi_assertion_route_1` lives under, and pinned by a
test that reads the Python vocabulary and the catalog CHECK and compares them.

**A label may carry more than one subject** — combination products are ordinary
— so this is a separate table, not columns on `spl_label`.

### 4.4 `drugref.spl_wording` — the statement, identified but not stored

| column | note |
|---|---|
| `text_key` | **PK.** SHA-256 of the whitespace-normalised section text |
| `char_length` | the denominator the quote budget is enforced against |
| `label_count` | how many labels carry this wording |

**No prose column, in any form.** The wording is *identified* here and quoted
only through §4.5.

**Why a wording table at all**: the corpus is 68,550 labels carrying **27,406
distinct wordings** — 2.50 labels per wording. Storing occurrences per label
would multiply every downstream count by that factor, which is the exact error
the 2026-08-13 evaluation made and the mining measurement was written to
prevent.

### 4.5 `drugref.spl_wording_quote` — the bounded window

| column | note |
|---|---|
| `text_key` | FK → `spl_wording` |
| `ordinal` | window order within the wording |
| `char_start`, `char_end` | the window's span in the normalised text |
| `quote_text` | **the only prose drugref stores** |

**The rule** (measured; owner's call, 2026-08-24): **±60 characters around the
FIRST occurrence of each distinct moiety, kept in DOCUMENT order, until 25% of
`char_length` is spent.**

Document order, not "pair priority": priority order would make the stored bytes
depend on which pairs the registry happens to resolve, and a licensing
constraint whose result moves with the vocabulary is not a constraint.

Measured over all 26,721 wordings naming a moiety: **20.4% of a section stored
on average**, median 22.7%, **5.1 merged windows per wording**, covering 71.6%
of the distinct moieties named. The alternatives and why they lose are in the
recovery measurement §6 — briefly, the containing sentence stores **82.7%** and
±120 characters **89.0%**, which is the section, reassembled.

**The budget is enforced, not intended.** A deferred constraint trigger
re-computes `sum(char_end - char_start)` per `text_key` at commit and refuses a
wording whose windows exceed `ceil(0.25 * char_length)`. This is `db/050`'s
lesson taken before the review round instead of during it: the failure mode is
silent, additive, and visible only in aggregate — exactly the shape that
survives a suite.

**The 28.4% of moieties with no window lose only the window.** Their occurrence,
offsets and citation are stored regardless, because §2 clears those.

### 4.6 `drugref.spl_entity_occurrence` — the derived facts

| column | note |
|---|---|
| `text_key` | FK → `spl_wording` |
| `char_start`, `char_end` | the matched span |
| `moiety_uuid` | FK → `substance_moiety` |
| `match_ambiguous` | boolean — the span folded onto more than one registry entry |

**`match_ambiguous` exists because ambiguity is unresolved, never "pick the
first"** — FDA-CYP's rule. **24 folded keys carry more than one registry name,
covering 55 of 19,438 (0.28%)**, mostly stereoisomers whose punctuation suffix
the fold strips (`carvone, (+)-`). The direction matters for DDI specifically:
S- and R-warfarin take different CYP pathways. Every colliding entry gets a row
and the flag is set; nothing downstream may silently choose.

**Class occurrences are NOT stored** (§1). Storing them would require answering
[#155](https://github.com/cairn-ehr/drugref/issues/155) first.

---

## 5. The read path

### 5.1 `drugref.spl_ddi_pair` — orientation-normalised candidate pairs

Joins `spl_label_subject` (resolved routes only) to `spl_entity_occurrence`
through `spl_label.text_key`, emitting `(moiety_lo, moiety_hi)` with the
citation and the wording key.

- **Self-pairs excluded in the view**, not refused at insert. A label routinely
  names its own drug, and that is a correct reading of the source, not a
  malformed row — `db/049`'s asymmetry with `moiety_contraindication_not_self`,
  for the same reason. The orchestrator counts them in their own bucket so the
  number cannot become nonzero unnoticed.
- **Orientation-normalised**, so the count is directly comparable with
  DrugCentral's.

### 5.2 `drugref.spl_ddi_pair` is NOT merged into `exact_ddi_pair`

Stated as its own section because the temptation is real and the reason is the
whole design: `exact_ddi_pair` means *an authority asserted these two drugs
interact*. SPL evidence means *a label's interactions section names both*. The
second is weaker, and a read path that cannot tell them apart makes the first
unfalsifiable.

Consumers wanting both take the union explicitly, and see which source said
what.

### 5.3 `drugref.gap_unresolved_spl_subject` — the recovery register

Every `spl_label` whose subject did not resolve, with its route, its `set_id`
and its wording key. **34,542 rows on today's release** — of which 19,862 carry
`absent_from_dailymed` — and it is the artifact that lets a future recovery
route run against a stored list rather than a re-read of 1.73 GB.

---

## 6. Code

Per the architecture invariant — *parsers are pure/streaming with no DB access;
orchestrators own the transaction and are the only writers*:

| module | job |
|---|---|
| `ingest/spl.py` | pure: openFDA record → section, identity, subject UNIIs |
| `ingest/spl_dailymed.py` | pure: SPL XML → active-ingredient/moiety UNIIs |
| `ingest/spl_match.py` | pure: wording → entity occurrences (the shipped resolver's rule) |
| `ingest/spl_quote.py` | pure: occurrences + text → the bounded window set |
| | *(the measurement's rules live in `tools/spl_quote_budget.py`)* |
| `ingest/spl_run.py` | the orchestrator: owns the transaction, sole writer |
| `cli_spl.py` | `drugref ingest spl` |

**`ingest/spl_quote.py` is pure and separately testable on purpose.** It is the
one module implementing a licensing determination, and a determination that can
only be tested through a database is a determination nobody re-checks.

**The matcher must be the SHIPPED resolver's rule** — exact, case-insensitive,
contiguous, longest-match-wins, `fold`-normalised — not a more generous variant.
The measured 29,258 pairs rest on that rule, and a matcher that skips words
produces spans it cannot quote back to a reader.

**The negative vocabulary, not a stop-list.** The nine measured terms in
`tools/spl_suppress_terms.txt` become seed data: `prothrombin time`, `serotonin
syndrome`, `lead to` and the rest, each carrying its measured distribution. A
stop-list would delete `lead` everywhere — including where a label means the
element, which is a real moiety with a real interaction through chelation — and
would still miss `serotonin`, which is not a dictionary word. **Deriving the
list systematically from next-word distributions is in scope for this slice**;
nine terms is a starting point that was measured, not a finished list.

---

## 7. Tests and measurement

- **TDD throughout**, failing test first.
- **The route vocabulary test**: reads the Python vocabulary and the catalog
  CHECK and asserts they match. Second homes are admitted only when pinned.
- **The quote-budget test must be shown it can fail** — construct a wording
  whose windows exceed the budget and assert the trigger refuses it. `db/050`'s
  finding was that every guard in a slice passed vacuously; a budget nobody
  demonstrated rejecting anything is that finding waiting to recur.
- **Per-source rebuild safety**: `DELETE FROM ... WHERE source = 'SPL'` then
  re-ingest reproduces every count exactly.
- **Floor checks on the orchestrator's own tally**, `db/050`'s pattern: the
  ingest asserts it published a non-trivial number of labels, subjects,
  occurrences and pairs, and fails loudly rather than reporting success over an
  empty read.
- **Measured on a fresh database from the real releases**, with the counts that
  must not move recorded: `substance_moiety` 19,438, `ddi_candidate_pair`
  21,877, `exact_ddi_pair` 8,943 — this slice adds no class rule and no gap
  kind those depend on, so none of them has licence to move.

**The figures this ingest must reproduce**, from the two measurements:

| | expected |
|---|---|
| section-carrying labels | 68,550 |
| distinct wordings | 27,406 |
| labels with a resolved subject | 34,008 (27,494 + 6,514) |
| unresolved, recorded | 34,542 |
| **distinct candidate pairs** | **≥ 29,258** |
| **novel against everything held** | **≥ 25,960 (88.7%)** |

**The pair figures are a FLOOR, not a target**, and an ingest reproducing more
is not failing its check. Two reasons, both pushing the same way: the
measurement scanned only orphan-wording labels, so the 14,455 redundant unkeyed
labels contributed no subject and therefore no pairs; and 200 labels carrying a
UNII drugref does not hold were filed as keyed by the probe's classifiers, which
excluded their wordings from the recoverable half. The ingest scans everything
and may find more. **The floor check asserts `>=`**, and the orchestrator prints
the actual figure so the difference is visible rather than absorbed.

**⇒ ONE SUBJECT PER LABEL PER ROUTE, AND THE SALT IS NOT A SECOND SUBJECT.** The
route table above is exclusive by construction, and the ingest must key subjects
the same way `subject_uniis` does: the moiety where a moiety UNII resolves, the
salt only where none does. The measurement's first reading blended the two and
published 31,618 pairs where the exclusive rule gives 29,258 — because drugref
registers a salt as its own moiety, so blending doubles a salt product's pairs.
An ingest built to this table cannot reach a floor derived from the blended
rule, which is why the floor above is the corrected figure.

---

## 8. What this slice does not answer

- **The class grain** — `#155`, empty classes, cross-source class identity.
- **The potency band** — `#102`, now known to be pair-scoped; it belongs on a
  curated assertion, and 25.4% of wordings carry the question.
- **The word-order gap** — labels write *"strong CYP1A2 inhibitors"*, drugref
  stores *"CYP1A2 strong inhibitor"*, and the difference is 2,212 matches
  against ~15,708. A class-grain problem, deferred with the class grain.
- **Salt-grain resolution** — `#67`, now wanted by three sources.
- **What a label MEANS by naming two drugs together.** That is curation, and
  keeping it out of ingest is the point.
