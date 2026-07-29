# Design — drugref global tier, slice 5b: MeSH-keyed contraindications (drug↔condition and drug↔drug)

**Date:** 2026-07-28 · **Repo:** `github.com/cairn-ehr/drugref` · **Status:** design, pending implementation
plan. **Builds on:** the
[slice-5a contraindication design](2026-07-25-drugref-slice-5a-medrt-contraindication-design.md) (the
projection/candidate-tier posture and the `ci_axis` discipline this slice mirrors), the
[slice-2b MeSH PA design](2026-07-24-drugref-slice-2b-mesh-pa-design.md) (the streaming MeSH parser and the
two-key UNII→CAS bridge this slice reuses), the
[additive-effect & open-question design](2026-07-25-drugref-additive-effect-and-open-question-design.md)
(Plan A's register, Plan B's descendant expansion), and the
[slice-1 moiety-spine design](2026-07-23-drugref-global-moiety-spine-design.md) (§5 own-immortal-UUID; the
`RXNORM_IN` claim the subject side joins through).

**Scope of change:** ingest MED-RT's **`CI_with`** (11,524 assertions — *"contraindicated with"* a disease
or physiological state) and the **moiety-resolvable arm of `CI_ChemClass`** (1,443 assertions — *"do not
co-administer with this specific drug"*), by first building the **MeSH condition registry** that MED-RT's
MeSH-namespace endpoints resolve into. Two new relations, one new vocabulary, `db/013`.

**Out of scope, each for a stated reason (§11):** indications (`may_treat` / `may_prevent` /
`may_diagnose` / `induces`, ~18k) → **slice 5b.2**, a different clinical contract; `has_SC` → later;
**`CI_ChemClass`'s class arm** (405 assertions) → **counted as a gap, deliberately not ingested** (§7);
severity/mechanism/management prose → the curated overlay, slice 5c; the HTTP API; any auto-firing alert.

---

## 1. Licence gate (rule 6 — cleared before any bundling)

**No new source is introduced.** Both files this slice reads are already cleared and already downloaded by
existing ingest paths:

- **MED-RT** — U.S. Department of Veterans Affairs, distributed via NCI EVS, **public domain in the US,
  UMLS restriction level 0**. Cleared in the slice-2a gate; `NOTICE` entry ships.
- **MeSH** — NLM, cleared in the slice-2b gate: attribution + no-endorsement + version-currency, **no NC,
  no ND**. `NOTICE` entry ships.

Slice 5b reads **more of the same two files**: MeSH *disease and chemical* descriptors where 2b read only
the **PA** subset. Same distribution, same terms, same attribution. **No `NOTICE` change is required.**

Two operational notes, both checked rather than assumed:

1. **The NDF-RT accessory crosswalk is NOT used.** `Core_MEDRT_Accessory_Files.zip` ships
   `NDFRT-NUI_MeSH-CUI_crosswalk_file_*.txt`, which HANDOVER proposed as the M-code resolution route. It is
   both **worse and wrong-shaped** — it resolves 85.0% of this slice's object codes against the MeSH
   release's 99.88% (§4.2), and it yields a *name*, which ROADMAP principle 2 forbids as a key. Declining it
   also means no third artefact enters the licence surface.
2. **Endpoint scoping still holds.** MED-RT *defines* only MED-RT-namespace concepts (3,695 in the
   2026.07.06 release); every MeSH endpoint appears as a bare code. So MeSH content can enter through
   exactly one door — a resolved endpoint — and it enters carrying MeSH's own licence, which is cleared.
   The `medrt.py` both-endpoints-must-be-ingested discipline is *extended*, not relaxed: a `CI_with` whose
   object namespace is not `MeSH` is refused and counted (§7).

## 2. Where this sits — a third endpoint type, and why it is not a class

drugref holds two orthogonal structures: a **composition tree** and a **classification DAG**. Slice 5a
added interactions as statements riding on top of both. Slice 5b introduces the first object that is
**neither a substance nor a class of substances**: a *condition* — the patient state a drug must not be
given in.

This is why conditions get their own tables rather than a new `concept_type` on `substance_class`
(alternative considered and rejected, §10 tension A). Three concrete consequences:

- `class_membership` (*moiety ∈ class*) is **meaningless** for a condition. Nothing is a member of
  pregnancy. Reusing `substance_class` would put a table with a membership relation next to objects that
  can never have one.
- `substance_class`'s axis vocabulary (`MoA`/`PE`/`TC`/`PK`/`EPC`/`APC`/`PA`) is **entirely
  pharmacological**. Filing *Coronary Artery Bypass* under it requires either a lie or a seventh axis that
  means "not actually a substance class".
- Every existing `COMMENT ON`, `CHECK` and consumer of `substance_class` currently means "a class of
  substances", and that is load-bearing for the licence-scoping argument in `medrt.py`.

**`CI_ChemClass`'s moiety arm is different again**: both endpoints are moieties, so it is the first
genuinely **pairwise** DDI content drugref holds — not a class-level rule awaiting read-time expansion.

## 3. Hybrid-store placement — rebuildable projections, *not* the signed moat

Every table in this slice is a **rebuildable projection**, exactly as `class_contraindication` and
`class_membership` are: re-ingest deletes this source's rows and re-inserts. A contraindication retracted
upstream must be able to disappear, which an insert-only merge could never express. None of it sits under
slice 1's append-only floor.

**Candidate tier, as 5a.** MED-RT does not track label updates, so nothing here may auto-alert. The
`COMMENT ON` for both new relations carries that contract into the catalog, per `db/006`'s finding that a
clinical contract living in `--` comments is a contract Postgres discards.

## 4. Ground truth — measured against the real releases, not the documentation

Everything below was measured against **MED-RT 2026.07.06**, **MeSH 2026** (`desc2026` + `supp2026`) and
**UNII 26Feb2026**, using drugref's own parser and gate rather than a re-implementation. The measurement
scripts corrected the design three times; each correction is recorded here because a hand-written fixture
would have concealed all three.

### 4.1 The predicates, and where their endpoints actually point

| predicate | assertions | subjects | objects | object namespace |
|---|---:|---:|---:|---|
| `CI_with` | 11,524 | 3,720 (RxNorm) | 708 | MeSH (+2 MED-RT, §7) |
| `CI_ChemClass` | 1,939 | 565 (RxNorm) | 360 | MeSH |

### 4.2 M-code resolution — the one genuinely new mechanism

**MED-RT's MeSH `to_code` is a MeSH *ConceptUI*, not a DescriptorUI.** Every MeSH record owns one or more
Concepts, exactly one preferred. So resolution is a plain lookup over files drugref already ingests:

Measured across **all seven** MeSH-keyed MED-RT predicates (2,474 distinct object codes), because the
mechanism is shared with slice 5b.2 and is worth establishing once:

| route | resolved | yields |
|---|---:|---|
| `desc2026` ConceptUI | 2,385 / 2,474 = **96.4%** | DescriptorUI |
| \+ `supp2026` ConceptUI | **2,471 / 2,474 = 99.88%** | SupplementalRecordUI |
| *(NDF-RT accessory crosswalk, rejected)* | 2,103 / 2,474 = 85.0% | a **name** |

**Scoped to this slice's two predicates: 1,051 of 1,053 objects resolve = 99.81%** (993 descriptors,
58 SCRs). The 2 that resolve nowhere are `CI_with` objects carrying **5** assertions between them;
`CI_ChemClass` has none.

Two shapes occur — legacy `M0000006` (8 chars) and modern `M000595362` (10) — and **both are ConceptUIs**;
nothing keys off the length. **81 of this slice's 1,051 resolved objects are NOT their record's preferred
concept**, and a subordinate concept may be *narrower* than the record it belongs to. This slice records
which (`is_preferred_concept`) rather than flattening the distinction away (§10 tension C).

### 4.3 `CI_with`'s object is NOT "a disease" — the finding that named the table

| object kind (MeSH tree branch) | assertions | examples |
|---|---:|---|
| Diseases (C) | 10,091 | Epilepsy, Virus Diseases |
| **Physiological states (G)** | **786** | **Pregnancy, Lactation** |
| Procedures (E) | 105 | Coronary Artery Bypass, Injections Spinal |
| Psychiatric (F) | 52 | Schizophrenia, Depressive Disorder |
| Chemicals (D) | 45 | Heparin, live vaccines |
| Check tags (no tree number) | — | Female |

**Pregnancy and lactation are the single most clinically consequential contraindication axis in the
release**, and a `drug_disease_contraindication` table would have filed them as a category error. The table
is `moiety_condition_contraindication`, and `condition` stores MeSH `tree_numbers` so a consumer can tell
these apart without drugref inventing a taxonomy of its own.

### 4.4 `CI_ChemClass` is overwhelmingly drug↔**drug**, not drug↔class

Its highest-frequency objects are **individual substances**: Pimozide (74 rules), Cisapride (71),
Ritonavir (52), Dihydroergotamine (47), Rifampin (37). Resolving each object through slice 2b's two-key
(UNII→CAS) bridge against the gated registry:

| arm | objects | assertions | of which both ends resolve |
|---|---:|---:|---:|
| object bridges to a **moiety** | 252 | 1,534 (79.1%) | **1,443** = 74.4% |
| object is a genuine **class** | 108 | **405** (20.9%) | — (not ingested, §7) |

The 1,534 → 1,443 step is the *subject* side: 89.9% of `CI_ChemClass` subjects join the gated registry.

Only **8.3%** of `CI_ChemClass` objects have any `has_SC` member, so the class arm cannot be populated from
`has_SC` either — see §7 for why it is counted rather than expanded.

### 4.5 Yield, applying drugref's own moiety gate

| | measured |
|---|---:|
| admitted moieties (19,438) carrying an `RXNORM_IN` claim | 8,861 |
| `CI_with` subject join | 2,900 / 3,720 = **78.0%** |
| **`moiety_condition_contraindication` rows** | **9,482** over 2,900 moieties / 667 conditions |
| **`moiety_contraindication` rows** | **1,443** |
| `condition` registry (descendant closure, §5.1) | **5,190** = 16.7% of MeSH |
| `condition_parent` edges | **7,157** (1,690 multi-parent) |

## 5. Schema (`db/013_mesh_conditions.sql`)

### 5.1 The condition registry — and why it holds the descendant *closure*

```sql
drugref.condition
    condition_uuid  uuid PRIMARY KEY   -- UUIDv5(CONDITION_NAMESPACE, source || ':' || source_code)
    source          text NOT NULL      -- CHECK ('MeSH'), as db/003 constrains class sources
    source_code     text NOT NULL      -- DescriptorUI (D004827) or SupplementalRecordUI (C536778)
    name            text NOT NULL
    record_kind     text NOT NULL      -- CHECK ('DESCRIPTOR', 'SCR')
    tree_numbers    text[] NOT NULL DEFAULT '{}'
    ingest_run      bigint NOT NULL REFERENCES drugref.ingest_run
    UNIQUE (source, source_code)

drugref.condition_parent
    child_condition_uuid  uuid NOT NULL REFERENCES drugref.condition
    parent_condition_uuid uuid NOT NULL REFERENCES drugref.condition
    PRIMARY KEY (child_condition_uuid, parent_condition_uuid)
    CHECK (child_condition_uuid <> parent_condition_uuid)
```

`condition_uuid` is **immortal by determinism**, exactly as `class_uuid` is: derived from
`(source, source_code)`, so a rebuild re-derives it and no pin table is needed. It is externally citable,
so the derivation is frozen and pinned by a literal in tests.

**The registry is the descendant CLOSURE of the referenced conditions, not the referenced set.** This is
the correction that saves the whole read path: expansion exists so a rule on *Epilepsy* fires for a patient
coded *Temporal Lobe Epilepsy* — and that descendant is **not itself a `CI_with` object**, so a registry
scoped to referenced objects would contain nothing to expand into and the feature would be inert while
appearing to work. Measured: 664 referenced descriptors → **5,190** in closure (+4,526), 26 descendants
reachable under *Epilepsy* alone.

`tree_numbers` is stored because it is **source data, not derived**: it is the input the DAG is built from,
and it is what distinguishes a disease from a physiological state from a procedure. SCRs carry none.

`condition_parent` is derived from **tree-number nesting**, precisely as slice 2b derived the PA DAG — one
established idiom, not a second one.

### 5.2 The condition-contraindication vocabulary

```sql
drugref.condition_ci_axis
    relationship        text PRIMARY KEY
    expands_descendants boolean NOT NULL      -- NO DEFAULT: see below
INSERT VALUES ('CI_with', true);
```

**No `DEFAULT`, deliberately.** ROADMAP's standing instruction for this slice is to *decide*
`expands_descendants` per predicate rather than inherit `ci_axis`'s recall-safe `true`, because MeSH's tree
is a different shape from MED-RT's. `db/012` finding 5 recorded that `ci_axis`'s comment claimed a
force-a-declaration discipline it did not implement; this table implements it. `CI_with` is declared
**`true`** on Plan B's argument: for a contraindication, fewer rows is the harm direction.

A separate table from `ci_axis` because the two map to different things — `ci_axis.membership_relationship`
names a `class_membership` axis, which has no analogue here (§2).

### 5.3 The two relations

```sql
drugref.moiety_condition_contraindication
    subject_moiety_uuid   uuid NOT NULL REFERENCES drugref.substance_moiety
    object_condition_uuid uuid NOT NULL REFERENCES drugref.condition
    relationship          text NOT NULL REFERENCES drugref.condition_ci_axis
    source                text NOT NULL
    ingest_run            bigint NOT NULL REFERENCES drugref.ingest_run
    PRIMARY KEY (subject_moiety_uuid, object_condition_uuid, relationship, source)

drugref.moiety_contraindication
    subject_moiety_uuid uuid NOT NULL REFERENCES drugref.substance_moiety
    object_moiety_uuid  uuid NOT NULL REFERENCES drugref.substance_moiety
    relationship        text NOT NULL          -- CHECK ('CI_ChemClass'); see below
    source              text NOT NULL
    ingest_run          bigint NOT NULL REFERENCES drugref.ingest_run
    PRIMARY KEY (subject_moiety_uuid, object_moiety_uuid, relationship, source)
    CHECK (subject_moiety_uuid <> object_moiety_uuid)
```

**`source` is in both primary keys**, per `db/006` finding 2: without it a second authority asserting the
same statement is swallowed by `ON CONFLICT DO NOTHING`, and the next routine rebuild — which deletes by
`ingest_run` — takes the shared row away with it, destroying the other source's independent assertion.
Slice 5c plans exactly that second source.

**`moiety_contraindication.relationship` is a `CHECK`, not an FK into an axis table.** `db/006` replaced a
`CHECK` with an FK because the predicate list was duplicated in a `CASE` *inside a view* — two lists in two
places, where widening only one silently produced rows that expanded to nothing. Here both endpoints are
moieties: there is no DAG, no expansion, no membership axis and therefore **no second list to keep in step
with**. An FK would copy the form of `db/006`'s fix while its cause is absent.

**Directionality is the clinical content.** `CI_ChemClass` states which drug the assertion is *about*;
reversing subject and object changes the meaning. Columns are named for their roles and the contract goes
in `COMMENT ON`, per `db/006` finding 3.

### 5.4 The read path — one recursive walk, mirroring `db/012`

```sql
CREATE VIEW drugref.condition_subtree AS      -- root INCLUDED in its own subtree
WITH RECURSIVE subtree(root_uuid, condition_uuid) AS (
    SELECT DISTINCT ci.object_condition_uuid, ci.object_condition_uuid
    FROM   drugref.moiety_condition_contraindication ci
  UNION
    SELECT s.root_uuid, cp.child_condition_uuid
    FROM   subtree s
    JOIN   drugref.condition_parent cp ON cp.parent_condition_uuid = s.condition_uuid
)
SELECT root_uuid, condition_uuid FROM subtree;

CREATE VIEW drugref.condition_contraindication_expanded AS ...
    -- subject_moiety · object_condition (what the rule NAMES)
    -- member_condition (the condition actually matched) · is_direct · relationship · source
```

`UNION` over `(root, condition)` rather than over paths: cycle-safe and linear in a multi-parent DAG (1,690
conditions have several parents). **Scoped to contraindicated conditions**, so the walk does not compute
5,190 subtrees nothing asks about. `WHERE is_direct` reproduces the unexpanded row set exactly, so a
precision-sensitive consumer opts out explicitly and a consumer who *forgets* errs toward recall — Plan B's
contract, unchanged.

Expansion is gated on `condition_ci_axis.expands_descendants`, joined in the view, so switching a predicate
off is one `UPDATE` and needs no view edit.

### 5.5 The counted gap

```sql
drugref.ingest_unresolved_ci_object
    ingest_run bigint NOT NULL REFERENCES drugref.ingest_run
    source, relationship, object_source, object_code, object_name text NOT NULL
    assertion_count int NOT NULL
    PRIMARY KEY (ingest_run, source, relationship, object_source, object_code)
```

Plus `gap_unresolved_ci_object` and a **fifth `gap_kind`** on `open_question`
(`'unresolved_ci_object'`, `gap_key` = `MESH:D013449`). One row per **object**, not per assertion, because
the question a curator answers is per class.

## 6. Ingest

| module | ~lines | role |
|---|---:|---|
| `ingest/mesh_concepts.py` | 150 | **new, pure & streaming**: `resolve(desc, supp, wanted) -> {m_code: MeshRecord}` carrying `record_ui`, `record_kind`, `name`, `tree_numbers`, `uniis`, `cas`, `is_preferred_concept`. Separate from `mesh.py` (296 lines, rule 4) because it answers a different question. |
| `conditions.py` | 110 | **new**: the ONLY writer of `condition` / `condition_parent`, mirroring `classes.py`. |
| `ingest/mesh_ci_run.py` | 230 | **new orchestrator**: owns the transaction; the only writer path; rebuilds the question register as its last step before commit, as every orchestrator does. |
| `ingest/medrt.py` | +40 | admit `CI_with` / `CI_ChemClass` as `MeshObjectAssertion(rxcui, mesh_code, relationship)`, endpoint-scoped to `to_namespace = 'MeSH'`. |
| `interactions.py` | 71 → 160 | the new `add_*` / `clear_source_*` writers. |
| `ids.py` | +25 | `CONDITION_NAMESPACE` + `mint_condition_uuid`. |
| `questions.py` | +30 | the fifth gap kind. |

Parsers stay **pure** — no DB, no network, no UUID minting — as `medrt.py` and `mesh.py` are today. The
orchestrator owns the transaction and is the only writer, per the architecture invariant.

**Two passes over MeSH, by necessity**: pass 1 resolves the referenced M-codes; pass 2 collects the
descendant closure, which is only knowable once the referenced tree positions are known. Both stream via
`iterparse`; neither holds the release in memory.

## 7. No silent drops — four distinct losses, each counted

| loss | measured | disposition |
|---|---:|---|
| `CI_with` subject RxCUI not in the registry | 2,041 assertions / 820 subjects | `ingest_unmatched_ingredient` (exists) |
| **`CI_ChemClass` class arm** | **405** assertions / **108** classes | `ingest_unresolved_ci_object` + gap view + question |
| object M-code unresolvable in MeSH | 2 objects / 5 assertions | counted in the run summary |
| `CI_with` → MED-RT `Current Non-smoker [EXT]` | 2 assertions | counted as skipped — `EXT` is deliberately not an ingested concept type |

**Why the class arm is counted rather than expanded — the sulfonamide case.** Expanding a `CI_ChemClass`
rule over MeSH's *structural* chemical tree would yield ≈8,000 pairs from those 405 assertions, and the
largest contributor is `Sulfonamides` (D013449, 36 rules), which reaches **61** moieties including
**bendroflumethiazide** and **bosentan**. That is the discredited sulfa cross-reactivity inference,
generated automatically and shipped as a safety assertion. `D015363` behaves the same way, pulling
aripiprazole and carteolol in beside ciprofloxacin. MeSH's chemical tree is a *structural* taxonomy and does
not mean what a clinical class means.

Plan B's precedent governs: it made a pharmacist rule on 14 expansion roots before expanding over them. The
class arm gets the same treatment — the content is **preserved and published as a question**, and a curator
decides. `allow` is not the same as absent; absent means unreviewed.

## 8. Clinical-safety posture

Unchanged from 5a and restated in `COMMENT ON` for both relations: **candidate tier**. MED-RT does not track
label updates; rows feed review and must not auto-alert. Two additions specific to this slice:

1. **A condition-contraindication is not an absolute contraindication.** MED-RT asserts the association, not
   its severity or whether a benefit-risk judgement may override it. The curated overlay (5c) adds those
   dimensions; until then a consumer must not render "contraindicated in pregnancy" as a hard stop.
2. **Descendant expansion widens recall, not certainty.** A rule matched via `is_direct = false` was written
   against an ancestor of the patient's coded condition. That is the intended behaviour, and it is
   *visible* — which is why `member_condition` and `is_direct` are columns rather than an internal detail.

## 9. Testing (TDD, failing test first)

Fixtures are **extracted from the real releases by a committed script** (`tests/fixtures/
make_mesh_ci_subset.py`), never hand-written — the standing rule since the `PT` column incident, where the
last hand-written fixture concealed a defect that would have shipped an entirely unlabelled registry.

Behaviours to pin:

1. M-code → record resolution: a descriptor hit, an **SCR fallback** hit, and a **subordinate
   (non-preferred) concept** recorded as such.
2. `condition_uuid` frozen against a **literal**, as class UUIDs are — it is immortal and externally cited,
   and it is the join key of `condition_parent`, so a drift would orphan every edge with no error anywhere.
3. **The descendant closure**: a rule on *Epilepsy* reaches *Drug Resistant Epilepsy*; `WHERE is_direct`
   returns only the direct row. A registry scoped to referenced conditions fails this test — which is the
   point of having it.
4. **The class arm is counted, not ingested**: a `CI_ChemClass` naming `Sulfonamides` produces a gap row and
   **zero** contraindication rows. This is the guard against the §7 hazard and must not be deleted.
5. Endpoint scoping: a `CI_with` whose object namespace is not `MeSH` yields no row and is counted.
6. Directionality: subject and object are not interchangeable in either relation.
7. Per-source rebuild replaces cleanly and does not disturb another source's rows.
8. The multi-parent DAG terminates, and a condition with several parents is reachable from each.

## 10. Design tensions recorded

**A. Conditions as their own tables vs a `concept_type` on `substance_class`** — *resolved: own tables.*
Reuse would have inherited Plan B's expansion machinery and `ci_class_subtree` for free. Rejected because
`substance_class` would stop meaning "a class of substances" (§2), and that meaning is load-bearing for the
licence-scoping argument in `medrt.py`. The cost is a second recursive view — but it walks a *different*
DAG, so it is not the duplication `db/012` removed (which was three copies of **one** walk).

**B. The class arm** — *resolved: counted, not ingested* (§7). Recorded because it is a deliberate recall
sacrifice: 396 assertions and ~7,988 potential pairs are withheld pending curator review.

**C. Subordinate concepts** — *resolved: recorded, not flattened.* 81 of this slice's 1,051 resolved objects
are not their record's preferred concept and may be narrower than it. Storing the condition at record grain
loses that nuance; `is_preferred_concept` makes the loss visible and measurable rather than silent. Whether
subordinate concepts deserve their own registry grain is deferred, with data now available to decide it.

**D. `expands_descendants` for `CI_with`** — *resolved: `true`, declared explicitly.* Plan B's argument
applies unchanged. Recorded because MeSH's tree is broader than MED-RT's and this is the first time the
question has been answered for a non-MED-RT vocabulary.

**E. The source-blind walk stays latent.** `db/012` warned that `class_parent` and `class_membership` carry
no `source` column, so a transitive walk crosses vocabularies — and ROADMAP states "slice 5b ends that".
**With this scope it does not:** 5b registers no MeSH chemical classes in `substance_class` (the class arm
is deferred) and conditions live in their own tables with their own MeSH-only DAG. The hazard becomes live
when `has_SC` or the class arm lands. ROADMAP is corrected rather than left as a false reassurance.

## 11. Explicitly out of scope for slice 5b

| deferred | size | why |
|---|---:|---|
| `may_treat` / `may_prevent` / `may_diagnose` / `induces` | ~18.3k | **Slice 5b.2.** Indications are a different clinical contract from contraindications and a prescriber reads them differently; one table serving both would serve neither. They reuse this slice's condition registry unchanged, which is most of the work. Note their objects include **organisms** (Ebolavirus, *Pseudomonas aeruginosa* — antimicrobial spectrum), so 5b.2 has its own object-kind question to answer. |
| `has_SC` | 3,632 | Structural-class *membership*, not an interaction. Needs the class arm's question answered first. |
| `CI_ChemClass` class arm | 405 | §7 — counted, awaiting curator review. |
| class-level indications (EPC subjects) | 699 | `may_treat`/`may_prevent`/`has_SC` with a MED-RT **EPC** subject rather than an RxNorm one — a class-level statement, issue #8's territory. |
| severity / mechanism / management | — | The curated signed overlay, slice 5c. |
