# Design — drugref global tier, slice 5b.2: MeSH-keyed indications (`may_treat` / `may_prevent` / `may_diagnose`) and `induces`

**Status:** design agreed 2026-07-30. Implements the other half of MED-RT's MeSH-keyed
content, over the condition registry slice 5b built.

**Predecessors this depends on and does not change:**
[slice 5b](2026-07-28-drugref-slice-5b-mesh-contraindication-design.md) (the condition
registry, `mesh_concepts` ConceptUI→record resolution, the descendant closure),
[Plan A](2026-07-25-drugref-additive-effect-and-open-question-design.md) (the
open-question register), `db/013`–`db/018`.

**One-line summary:** ingest 18,314 public-domain drug→condition assertions —
therapeutic indications and drug-induced states — as two new rebuildable projections
over the *existing* condition registry; store **only what the release asserts**, and
generalise at read time in the one direction that is sound, with every derived row
labelled as a generalisation.

---

## 1. Licence gate (rule 6 — cleared before any code)

**No new source.** Both authorities are already ingested and cleared: MED-RT (VA
federal work, public domain, UMLS restriction level 0 — slice 2a) states the
assertions, MeSH (NLM terms: attribution, no-endorsement, version-currency; no NC, no
ND — slice 2b) defines their objects. This slice reads *more predicates out of files
drugref already downloads*, and adds no namespace: every object is a MeSH ConceptUI,
exactly as `CI_with`'s is.

`NOTICE` is therefore **unchanged**. Note what slice 5b had to correct there and why it
does not recur: 5b widened the *scope* statements because it began reading MeSH concept
names out of the MED-RT file. That is already stated; 5b.2 reads the same shape of
endpoint from the same file.

What this yields is worth naming: an openly-licensed drug–disease **indication**
dataset drugref owns outright, which is what `source_tier`'s `MeDIC` row (rank 3, "CC0
drug-disease indications/contraindications seed") was a placeholder for.

## 2. Where this sits

The subject is a moiety; the object is a `condition` — the **same third endpoint type**
5b established, on the same tables, with no schema change to them beyond one cached
column (§5.5). Nothing here is a `substance_class` and nothing is a member of anything.

Both new relations are **rebuildable projections** and **candidate tier**, exactly as
`moiety_condition_contraindication` is: MED-RT does not track label updates, so rows
feed review and must never auto-alert. The curated, signed overlay remains slice 5c.

## 3. Ground truth — measured against the real releases

Every figure below is from **MED-RT 2026.07.06 + MeSH desc2026/supp2026**, read with
drugref's own parsing code. Extraction scripts are in the session scratchpad; the
figures are reproduced end-to-end during implementation (§10).

### 3.1 The predicates, and where their endpoints actually point

| predicate | total | **RxNorm → MeSH** | MED-RT → MeSH | distinct subjects | objects (concept) | objects (record) |
|---|---:|---:|---:|---:|---:|---:|
| `may_treat` | 15,419 | **15,319** | 100 | 4,589 | 1,401 | 1,351 |
| `may_prevent` | 2,760 | **2,670** | 90 | 1,853 | 345 | 337 |
| `may_diagnose` | 158 | **155** | 3 | 119 | 61 | 61 |
| `induces` | 170 | **170** | 0 | 122 | 50 | 50 |

**18,314 assertions in scope** (18,144 therapeutic + 170 `induces`) over **1,469
distinct object records**, of which **1,215 are already registered by 5b**. The
therapeutic objects are **1,453 distinct records**, and the four predicates share
**5,082 distinct subject RxCUIs**.

**19 assertions collapse, and the cause is 5b's**: drugref keys a condition on the MeSH
**record** while MED-RT points at a **ConceptUI**, so several concepts resolve to one
condition — the collapse that turned 5b's predicted 9,482 rows into 9,471. Distinct
`(subject, object record)` pairs are therefore **18,295**, not 18,314: `may_treat`
15,319 → **15,302**, `may_prevent` 2,670 → **2,668**, `may_diagnose` and `induces`
unchanged. Those are the **pre-gate ceilings** §10 measures the stored row counts
against — 18,125 therapeutic pairs and 170 induced-state pairs, before the moiety gate
removes subjects no moiety carries.

**Every object ConceptUI resolves.** Across all five MeSH-keyed predicates, 1,851 of
1,853 concepts resolve to a record (99.89%) — and the 2 that do not are `CI_with`'s
already-known withdrawn pair. So the indication half has **no** analogue of 5b's
unresolved-code gap in this release, and the counter still exists (§7) because that is
a fact about a release, not a guarantee.

**193 assertions run MED-RT → MeSH**: a pharmacologic *class* may_treat a condition.
Refused and counted, exactly as `non_mesh_ci_objects` is — see §6.3.

### 3.2 The finding that shapes the whole slice: expansion is unsound here

`CI_with` expands **down** the condition DAG, and `db/014` gives the argument: a
patient coded *Temporal Lobe Epilepsy* **is** a patient with epilepsy, so a rule on
Epilepsy holds. The walk is **subsumption on the patient's state**, and for a
contraindication fewer rows is the harm direction.

Applied to an indication the same walk means something else entirely — **distribution
over the object's subclasses** — and the release shows what it would produce:

| predicate | direct rows | if expanded down the DAG | multiplier |
|---|---:|---:|---:|
| `may_treat` | 15,302 | 199,546 | **13.0×** |
| `may_prevent` | 2,668 | 109,042 | **40.9×** |
| `may_diagnose` | 155 | 11,618 | **75.0×** |
| `induces` | 170 | 1,313 | 7.7× |

The worked cases are the argument, not the multipliers. **One** `may_treat` rule on
*Neoplasms* (D009369, 702 descendants) would manufacture 702 therapeutic claims —
"treats Adenocarcinoma", "treats Astrocytoma", "treats Basal Cell Carcinoma"; *Infections*
(D007239) 785; *Cardiovascular Diseases* 470. MED-RT asserted none of them. **For an
indication, MORE rows is the harm direction** — the exact inverse of Plan B's premise,
and a deny-list would not fix it, because the unsoundness is in the walk itself rather
than in which roots are too abstract.

### 3.3 But the value *is* in the DAG — read the other way

Of the 5,963 conditions the registry holds after this slice:

- **1,453** carry a direct therapeutic indication;
- **3,655** carry none, but have an **ancestor** that does;
- 855 have no indication at or above them (§5.6).

So a patient coded at a finer granularity than MED-RT's — the common case, 3,655
against 1,453 — gets nothing from the stored rows alone. The sound walk is **UP** from
the patient's condition: "phenytoin is indicated for *Epilepsy*, a more general form of
this diagnosis" is a true, weaker statement, and it is the only statement the release
supports. That is the read path in §5.4, and the derived rows are labelled so a
consumer cannot mistake the weaker claim for the stronger one.

### 3.4 The object of an indication is not always a disease

Assertions by object tree (record grain; a multi-tree object counts once per tree):

| predicate | C diseases | F psych | G phenomena | B organisms | D chemicals | other |
|---|---:|---:|---:|---:|---:|---:|
| `may_treat` | 14,611 | 1,184 | 359 | 34 | 14 | 74 |
| `may_prevent` | 2,449 | 116 | 142 | 159 | 3 | 16 |
| `may_diagnose` | 151 | 8 | — | — | — | — |
| `induces` | 169 | 17 | 8 | — | — | — |

Two groups need a stated decision, and both are **ingested**:

- **B-tree organisms** (159 `may_prevent` assertions: *Influenza A virus* 76,
  *Streptococcus pneumoniae* 24, *Neisseria meningitidis* serogroups, *Measles virus*) —
  these are the **vaccines**, and withholding them would delete the most useful
  prevention content in the release. MED-RT names the organism where a clinician would
  name the infection; that is an upstream idiom, not an error.
- **D-tree chemicals**, 17 assertions over 13 records (`may_treat`: *LDL Cholesterol* 2,
  *Antioxidants* 2, *Prostate-Specific Antigen* 2, *Analgesics*, *Antiemetics*,
  *Antiparkinson Agents*, …; `may_prevent`: *von Willebrand Factor* 2, *Radioactive
  Tracers*) — 0.09% of `may_treat`. Some are defensible treatment targets ("a statin
  may_treat LDL cholesterol"), some are upstream quirks ("may_treat Analgesics").
  Ingested, **counted**, and left scopeable: `db/013` stores `tree_numbers` precisely so
  "the leading letter distinguishes a disease (C) from a physiological state (G) from a
  procedure (E)", and 5b already registered 18 such `CI_with` objects. Withholding 17
  rows behind a new worklist kind would cost more than it buys.

### 3.5 `SCRClass` — the documentation is wrong again

The seventh gap kind (§5.6) needs to tell a rare disease from a chemical among
supplementary records, which bear no tree numbers. MeSH publishes `SCRClass` as an
attribute of every `<SupplementalRecord>` (`DescriptorRecord` carries `DescriptorClass`
instead, never this). The documented vocabulary is four values; **supp2026 publishes
six**:

| SCRClass | records in supp2026 | documented meaning |
|---|---:|---|
| 1 | 249,245 | chemical |
| 4 | 65,236 | organism |
| **3** | **6,542** | **rare disease** |
| 5 | 1,763 | *not asserted by drugref* |
| 2 | 1,236 | protocol |
| 6 | 23 | *not asserted by drugref* |

drugref stores the published value and **claims no meaning for 5 and 6** — inventing
one is how a plausible bug gets shipped. Only `3` is load-bearing, and only in one
place (§5.6). Of the 34 SCRs the registry will hold, 29 are class 3 and 5 are class 1.

## 4. What is added, in one breath

Two relations, one vocabulary table, one cached column, one read-path function, one
reach view, one gap view, one gap kind, one `reason` value — and **no new mechanism**:
concept resolution, the closure, the DAG, the moiety indexes, the worklist posture and
the register are all 5b's, unchanged.

## 5. Schema (`db/019_mesh_indications.sql`)

### 5.1 Two relations, because the unfiltered read of a table must be one true sentence

`moiety_condition_indication` holds `may_treat` / `may_prevent` / `may_diagnose`;
`moiety_induced_condition` holds `induces` **alone**.

The test applied is not "are the endpoints the same kind of thing" (they are: moiety →
condition) but **what does a row of this table say if nobody filters it**. "This drug is
used for this condition" is true of all three therapeutic predicates. "This drug can
*cause* this condition" is a different sentence, and a consumer who forgets a
`relationship` filter on a shared table would read *"carbamazepine treats
agranulocytosis"* off an `induces` row. `db/010` chose `is_direct` so that a forgetful
consumer errs toward recall — the safe direction; here the safe direction is the other
one, so the split is structural rather than a `WHERE` clause.

```sql
CREATE TABLE drugref.moiety_condition_indication (
    subject_moiety_uuid   uuid   NOT NULL REFERENCES drugref.substance_moiety(moiety_uuid),
    object_condition_uuid uuid   NOT NULL REFERENCES drugref.condition(condition_uuid),
    relationship          text   NOT NULL
        REFERENCES drugref.condition_indication_axis(relationship),
    source                text   NOT NULL
        CONSTRAINT moiety_condition_indication_source CHECK (source IN ('MED-RT')),
    ingest_run            bigint NOT NULL REFERENCES drugref.ingest_run(ingest_run_id),
    PRIMARY KEY (subject_moiety_uuid, object_condition_uuid, relationship, source)
);
CREATE INDEX moiety_condition_indication_by_condition
    ON drugref.moiety_condition_indication (object_condition_uuid);
```

`source` is **in the key** and **CHECK-constrained**, for `db/006` finding 2's reason
restated by `db/014`: without it a second authority's independent assertion is swallowed
by `ON CONFLICT DO NOTHING` and then deleted by the next MED-RT rebuild; and an
unconstrained `source` once let `'MEDRT'` insert cleanly and match nothing forever
(`db/012` finding 3).

`moiety_induced_condition` is the same shape with `relationship text NOT NULL CHECK
(relationship IN ('induces'))` — **a CHECK, not an FK**, on `db/014`'s own asymmetry
argument: an FK exists to keep a predicate list in step with a *second* list held
elsewhere (a view's `CASE`, a walk's gate). Nothing walks this table, so there is no
second list, and an FK would copy the form of that fix while its cause is absent.

### 5.2 The vocabulary — `generalises_to_descendants`, and why not `expands`

```sql
CREATE TABLE drugref.condition_indication_axis (
    relationship             text    PRIMARY KEY,
    generalises_to_descendants boolean NOT NULL      -- NO DEFAULT, deliberately
);
INSERT INTO drugref.condition_indication_axis (relationship, generalises_to_descendants)
VALUES ('may_treat', true), ('may_prevent', true), ('may_diagnose', true)
ON CONFLICT (relationship) DO NOTHING;
```

**No DEFAULT**, the discipline `db/014` implemented for `condition_ci_axis` after
`db/012` finding 5 found a comment claiming it while a DEFAULT quietly answered the
question: a predicate added later — 5c's, or a second authority's — **must state its
own answer**.

The column is deliberately **not** called `expands_descendants`, because it licenses
something weaker. `condition_ci_axis.expands_descendants = true` says *the rule fires
for the descendant*. This says *a rule on an ancestor may be offered for this
condition, labelled as a generalisation*. Same graph, different claim; naming them
alike would invite a future reader to unify two things §3.2 says must not be unified.

`induces` has **no row here** and appears in no walk: what a drug causes in a general
state is not a claim about a subtype, and 170 rows do not need a mechanism.

### 5.3 One quantity, stated once — `condition_indication_reach`

```sql
CREATE VIEW drugref.condition_indication_reach AS ...
-- (condition_uuid, direct_indication_rules, generalised_indication_rules)
-- one row per registry condition; 0 where nothing reaches it
```

Built by walking the DAG **down** from every condition a **therapeutic** indication
names, gated by `generalises_to_descendants`, aggregated per condition, and LEFT-joined
from `condition` so an unreached condition is present with zeroes rather than absent.
`induces` is **excluded** — it holds no axis row, licenses no walk, and a state a drug
causes is not a claim about that state's subtypes. It exists because the gap view
(§5.6) needs "does anything reach this condition" and `db/018`'s round taught what
happens when that quantity is stated twice: `populated` and `reachable` were
near-identical CTEs, only one learned that a rule's own subject is not a partner, and a
whole class of dead rules was reported by *nothing*. **One view, and the gap view is a
filter on one of its columns** — `= 0` — so the partition is true by construction.

### 5.4 The read path — one function, and no expanded view

```sql
CREATE FUNCTION drugref.indications_for_condition(patient_condition uuid)
RETURNS TABLE (subject_moiety   uuid, object_condition uuid,
               member_condition uuid, is_direct        boolean,
               relationship     text, source           text)
```

An **ancestor walk** from the patient's condition, `db/018`'s `#45` shape, joined to
`condition_indication_axis` with the same gate the reach view applies: `WHERE
a.generalises_to_descendants OR ci.object_condition_uuid = patient_condition`. Column
shape is identical to `contraindications_for_condition` so a consumer sees one shape
for both halves.

**There is deliberately no `condition_indication_expanded` view.** 5b needs both a view
and a function because rows are stored expandable and whole-set access is a real use;
here nothing is stored expanded, so the base table *is* whole-set access and a second
walk would buy nothing while creating the disagreement `db/006` warns about. The one
equivalence that must hold — the function against `condition_indication_reach`'s counts —
is pinned by test and re-checked on the real release (§9, §10).

`is_direct = false` **means a weaker claim**, not a wider one, and that is the whole
contract of this slice. The `COMMENT ON FUNCTION` says so in those terms, names
`object_condition` as the condition the assertion was actually written against, and
states that a consumer must render such a row as *"indicated for <ancestor>, a more
general form of this diagnosis"* and never as an indication for the coded diagnosis.

### 5.5 One cached column on `condition`

```sql
ALTER TABLE drugref.condition ADD COLUMN scr_class text;   -- NULL for descriptors
```

Stored **as published**, with no CHECK — the same treatment `tree_numbers` gets, and for
the same reason: it is opaque source data, and §3.5 measured that the published
vocabulary is already wider than the documented one. A future value therefore cannot
abort an ingest. The protection against silent drift is a **count**, not a constraint:
the run summary reports registered conditions per `scr_class`, so a renumbering shows up
as a number that moved — the posture `skipped_predicates` and `skipped_concept_types`
already take.

### 5.6 The seventh gap kind — `condition_without_indication`

`gap_condition_without_indication`: a registry condition that **is a disease** and that
`condition_indication_reach` shows no therapeutic indication reaching, directly or from
above. Measured: **66 rows** — 55 carrying a C (Diseases) or F (Psychiatry) tree number,
plus 11 tree-less `SCRClass = 3` rare diseases (*Short QT Syndrome*, *succinic
semialdehyde dehydrogenase deficiency*, *Familial medullary thyroid carcinoma*, …).

**Scope is a decision, and the numbers are why.** 855 registry conditions are unreached,
but 789 of them are not gaps: 669 are **surgical procedures** (E tree — *Abdominoplasty*,
*Ablation Techniques*), and the rest are chemicals, organisms, foods (*Beer*, *Cheese*),
demographics (*Adolescent*, *Aged*) and specialties (*Pediatrics*). "Nothing is indicated
for Abdominoplasty" is a category error, and 789 of them on the worklist would mint
externally-citable `question_uuid`s for noise while burying the 66 real gaps. The
exclusions are stated with their counts in the view's `COMMENT ON`, never silently
applied — `is_current` retirement already exists for a gap that genuinely closes.

Tree-less records are excluded on a **different stated ground**: a record with no DAG
position cannot be assessed at all, so "nothing above it" is vacuously true. The
`SCRClass = 3` carve-out recovers exactly the 11 for which the vacuous answer is also
the clinically right one — a rare disease with no recorded indication is the most
valuable row on this list — and drops *aliskiren* and *formaldehyde-serum albumin*,
which are class 1 chemicals that the tree-blind version would have published as
diseases nothing treats.

Wiring, per `questions.py`'s `_GAP_SOURCES` contract:

- `gap_key` = `'CONDITION:' || condition_uuid` — the registered-object form (`MOIETY:`,
  `CLASS:`), not `{NAMESPACE}:{code}`, because unlike `unresolved_ci_object` the subject
  **is** registered and has a drugref UUID to cite.
- `question_text` names the condition and its MeSH code so the row is usable as a
  literature search on its own.
- `open_question.gap_kind`'s CHECK is widened to seven values, following `db/016`'s and
  `db/018`'s idempotent re-issue pattern.
- `source_tier` is **unchanged**: `openFDA-SPL` (rank 2) and `MeDIC` (rank 3) already
  describe the sources that answer this, and `question_worklist` orders generically.

## 6. Ingest

### 6.1 One orchestrator owns the condition registry

**Structural, not stylistic.** `condition` and `condition_parent` are rebuilt per
`ingest_run.source`, and both halves of the MeSH-keyed content run under `MED-RT`
(MED-RT asserts; MeSH defines). Two orchestrators would therefore each clear the
other's DAG edges — `#39` one layer deeper, and **unfixable by a discriminator**: a
`(child, parent)` edge is derived by *both* closures, so it cannot be split by a `reason`
column the way `ingest_unmatched_ingredient` was. One writer is the only shape that
works.

So slice 5b's orchestrator is refactored, not duplicated: one run opens the `ingest_run`,
resolves concepts once, takes the closure over **all** referenced objects, upserts
conditions, clears this source's projections, writes the DAG, builds the moiety indexes
once, then runs **two relation passes** (contraindications, indications), persists the
worklists and rebuilds the register last, before one commit. `mesh_ci_run.py` is already
458 lines against CLAUDE.md rule 4's ~500, so the shared steps and each relation pass
become their own modules; a new `indications.py` is the **only** writer of the two new
tables, mirroring `interactions.py`.

The run summary reports the registry figures **once** (they are one fact about one
closure) and each half's tallies under its own names — reporting `conditions_registered`
twice is precisely the "one quantity stated twice" trap.

### 6.2 Parser (`ingest/medrt.py`)

Adds `INDICATION_RELATIONSHIPS = {may_treat, may_prevent, may_diagnose}` and
`INDUCES_RELATIONSHIP = induces`, both scoped to `RxNorm → MeSH` exactly as
`MESH_CI_RELATIONSHIPS` is, yielding the existing `MeshObjectAssertion` shape (rxcui,
raw ConceptUI, relationship). The namespace scoping is what keeps SNOMED out and is not
relaxed. Four names leave `skipped_predicates`, which a test pins — that tuple exists so
an upstream *rename* of something drugref ingests cannot look identical to a deliberate
skip.

### 6.3 The 193 class-subject assertions

`may_treat` 100, `may_prevent` 90, `may_diagnose` 3 run **MED-RT → MeSH**: the subject is
a pharmacologic class, not an ingredient. Refused and **counted**, the posture
`non_mesh_ci_objects` takes, with the counter named for what it counts and its docstring
stating that it increments for *any* endpoint pair other than `RxNorm → MeSH` while the
release contains only this one shape. Filed against
[#8](https://github.com/cairn-ehr/drugref/issues/8) (class-level `has_*` assertions
unused), whose DAG-inheritance question this is a second instance of — ingesting them
would need a third relation and a second expansion question, in a slice whose premise is
that it adds no mechanism.

## 7. No silent drops — every loss counted

Per run, for the indication half:

| number | measured | persisted? |
|---|---:|---|
| unmatched subject RxCUIs (no moiety carries them) | run-dependent | yes — `ingest_unmatched_ingredient`, `reason = 'indication'` |
| class-subject assertions refused | 193 | count only |
| object ConceptUIs MeSH no longer defines | 0 | count only |
| assertions whose object is a D-tree chemical | 17 | count only (§3.4) |
| registered conditions per `scr_class` | 29 × `3`, 5 × `1` | count only (§5.5) |

`reason = 'indication'` is a **fourth bucket, never a shared one** — `db/018`'s invariant
is exactly one writer per `(source, reason)`, and the CHECK is widened in `db/019`. One
orchestrator owning two buckets is fine; two writers sharing one is what `#39` was.

## 8. Clinical-safety posture

- **Candidate tier.** MED-RT does not track label updates. Rows feed review; nothing
  auto-alerts. `COMMENT ON` carries this on both tables.
- **An indication is not a recommendation.** MED-RT asserts that a drug *may* treat a
  condition, never that it is appropriate for a given patient, first-line, dosed how, or
  safe in combination. The `COMMENT ON` says so.
- **A generalised row is a weaker claim.** §5.4. The label is the safety mechanism.
- **`induces` is neither an indication nor a contraindication** and its own table is what
  keeps it from being read as either. It is a statement that the drug *causes* the state
  — *Unconsciousness* (32, anaesthetics), *Mydriasis* (14), *Diarrhea* (8) — which is
  sometimes the therapeutic point and sometimes the adverse effect, and MED-RT does not
  say which.

## 9. Testing (TDD — failing test first)

- **Parser**: the four predicates parsed at `RxNorm → MeSH`; a class-subject row refused
  and counted; the four names absent from `skipped_predicates`; `SCRClass` read from a
  supplementary record and absent on a descriptor.
- **Schema**: `condition_indication_axis` refuses an insert with no
  `generalises_to_descendants`; the relation's FK refuses an undeclared predicate; the
  induces CHECK refuses `may_treat`; `source` CHECK refuses `'MEDRT'`; the PK collapses a
  duplicate assertion; `scr_class` accepts NULL for a descriptor.
- **Read path**: direct and generalised rows with correct `is_direct`; a predicate flipped
  to `generalises_to_descendants = false` returns direct rows only, by `UPDATE` and no
  view edit; termination under a DAG cycle; **the function's row count equals
  `condition_indication_reach`'s counts** for every condition in the fixture — the pin
  that makes two statements of one rule safe.
- **Gap view**: a C-tree disease with no reach appears; one with a reaching ancestor does
  not; a procedure (E tree) never appears; a tree-less `SCRClass = 3` record appears and
  a `SCRClass = 1` record does not; the view's grain is the `gap_key`'s grain (`#41`'s
  test, restated for this kind).
- **Orchestrator**: both halves under one `ingest_run`; a re-run is idempotent and moves
  no count; the closure covers indication objects; unmatched indication subjects land
  under `reason = 'indication'` and a later `medrt_run` leaves them standing; the seventh
  gap kind reaches `open_question` and `question_worklist`.
- **Clear contract**: `tests/test_source_clear_contract.py` restates the two new tables
  independently, so a dropped table fails (`#43`'s rule).
- **Fixtures**: `make_medrt_subset.py` extended to carry indication assertions, keeping
  the endpoint redaction a test enforces; then `make_mesh_ci_subset.py` **re-run after
  it**, since its wanted set is read out of `medrt_subset.xml` — the ordering that exists
  because the first hand-picked version described a world disjoint from the MED-RT
  fixture's while both files looked healthy alone.

## 10. Verification before the claim (rule: measure the real release)

Re-run the whole chain on a scratch database — UNII 26Feb2026 → MED-RT 2026.07.06 →
MeSH pa/desc/supp 2026 → this slice — and record the measured table. Three things must
hold, and one must move:

- **must not move**: every slice-5b headline figure — `condition_parent` 7,157 edges over
  the CI closure, `moiety_condition_contraindication` **9,471**, `moiety_contraindication`
  **1,442**, 103 withheld objects summing to 405 rules, `ddi_candidate_pair` **21,664**;
- **must move, to exactly this**: `condition` **5,203 → 5,963**;
- **must be measured, against the §3.1 ceilings**: the two relations' row counts after the
  moiety gate — at most **18,125** and **170** distinct pairs — plus the unmatched
  indication subjects, and the gap view against its predicted **66**;
- **must be checked on the release, not only on fixtures**: `indications_for_condition`
  against `condition_indication_reach` for every registry condition, zero difference in
  either direction — the check `#45` ran for the contraindication pair.

Any disagreement between a figure here and the run is **the spec being wrong**, as it was
five times in 5b, and is corrected in the docs-site *Design decisions* section, because
this file is immutable once merged.

## 11. Design tensions recorded

- **A. Generalisation is offered at all.** Rejected alternative: store nothing derived and
  publish no walk. It is the safest possible reading and leaves 3,655 of 5,963 conditions
  answerless. Resolution: offer it through **one labelled function**, never stored, never
  unlabelled, per-predicate revocable by one `UPDATE`.
- **B. A shared table with a discriminator would be less DDL.** Rejected: §5.1 — the
  unfiltered read must be one true sentence, and here the forgetful direction is unsafe.
- **C. The D-tree objects are ingested.** 17 assertions that are arguably category errors
  upstream. Rejected alternative: a new worklist kind for them. Resolution: ingest, count,
  and leave `tree_numbers` to scope — the cost of the alternative exceeds 17 rows.
- **D. `induces` is ingested now rather than deferred to an adverse-effect slice.** It is
  170 licence-clean public-domain rows already in a file drugref parses; deferring them
  costs a second pass over the same predicate list later. Its own table keeps it from
  contaminating either neighbour.
- **E. The gap view is scoped, and scoping is a judgement.** 789 rows excluded. Resolution:
  every exclusion is stated **with its count** in the view's `COMMENT ON`, so the judgement
  is auditable and reversible in one migration rather than invisible.
- **F. `scr_class` has no CHECK.** Consistent with `tree_numbers`, and §3.5 is the reason:
  the published vocabulary is already wider than the documented one, so a CHECK would abort
  ingests on an upstream addition. Drift is caught by a reported count instead.

## 12. Explicitly out of scope

- **`has_SC`** (3,632 assertions, **248 targeting MED-RT itself**) — still unbuilt, and
  still the thing that would end the source-blind-walk latency along with
  `CI_ChemClass`'s class arm.
- **`CI_ChemClass`'s class arm** — 405 assertions over 103 objects, withheld by 5b pending
  a curator ruling. Untouched here.
- **`site_of_metabolism`** (43, `RxNorm → MED-RT`), **`has_active_metabolites`** (8),
  **`effect_may_be_inhibited_by`** (2) — measured and named here so they are known rather
  than merely unread; they are pharmacokinetic content for a later slice.
- **The 193 class-subject indications** — §6.3, filed against `#8`.
- **A read path that ranks or prefers among indications.** MED-RT asserts no line of
  therapy, no strength of evidence and no ordering; inventing one is slice 5c's curated
  work, not a projection's.
- **`#48`** stays unreachable: it needs a predicate with no direct member and no
  expansion, and an indication always reaches its own condition, so the dead-rule shape
  does not transfer to this graph.
