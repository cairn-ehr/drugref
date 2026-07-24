# Design — drugref global tier, slice 5a: MED-RT mechanism/effect contraindications (the first interaction projection)

**Date:** 2026-07-25 · **Repo:** `github.com/cairn-ehr/drugref` · **Status:** design, pending
implementation plan. **Builds on:** the
[slice-2a MED-RT design](2026-07-23-drugref-slice-2a-medrt-classification-design.md) (the class registry +
membership join this slice reuses **unchanged**) and the
[slice-1 moiety-spine design](2026-07-23-drugref-global-moiety-spine-design.md) (§5 own-immortal-UUID; the
`RXNORM_IN` `identity_claim` crosswalk the subject side joins through).

**Scope of change:** ingest MED-RT's **`CI_MoA`** and **`CI_PE`** associations — *"contraindicated
mechanism of action / physiological effect of a **co-administered ingredient**"* (MED-RT's own words, §4) —
as drugref's **first drug–drug interaction data**. These are **class-level DDI rules**: "drug *X* is
contraindicated with any co-administered drug acting on class *C*." They are stored in **one new table**
(`drugref.class_contraindication`, `db/004`) and populated **from the MED-RT file drugref already parses**,
joining through machinery that already exists — **no new external source, no new join, no new UUID
minting**. This is the smallest possible first cut of ROADMAP **Slice 5** (the interaction overlay): its
*ingested-projection* half, seeded from public-domain regulatory-derived content, deliberately ahead of the
curated/signed half.

**Why only `CI_MoA` + `CI_PE` (and not the rest of MED-RT's CI/indication content):** both of their
endpoints are things drugref **already holds** — the subject is an RxNorm ingredient (slice-1 `RXNORM_IN`
claim) and the object is a MED-RT **MoA**/**PE** class (slice-2a `substance_class`). So this slice needs
**zero new ingest** on either side. `CI_with` and `CI_ChemClass` (contraindications whose object is a
**MeSH disease / chemical** descriptor) and `may_treat`/`may_prevent`/`induces` (drug–disease indications
and drug-induced states, also MeSH-keyed) all require ingesting **MeSH disease/chemical descriptors** first
— a larger, still-clean follow-up (Slice 5b), scoped out here (§11).

**Out of scope (each a later concern):** `CI_with` / `CI_ChemClass` / `may_treat` / `may_prevent` /
`may_diagnose` / `induces` (all MeSH-keyed → Slice 5b); severity, mechanism prose, and management
recommendations (drugref's own **curated/signed** overlay — the append-only half of Slice 5, later); SPL/
DailyMed-mined interactions (a separate ingest); the HTTP API; and any auto-firing prescriber alert (§7 — a
Slice-5a projection row is a *candidate*, never a rendered alert).

---

## 1. Licence gate (rule 7 — cleared before any bundling)

**No new source is introduced, so the gate is already cleared.** MED-RT was licence-verified
AGPL-bundleable in the slice-2a gate (§1): a work of the U.S. Department of Veterans Affairs, distributed
via NCI EVS, **public domain in the US, UMLS restriction level 0** — no NonCommercial, no NoDerivatives.
The `NOTICE` entry for MED-RT already ships. Two operational notes:

1. **Both endpoints of every ingested edge are already drugref-licensed content.** The subject is an
   `RxNorm`-namespace code (public-domain RxNorm, already a slice-1 claim); the object is a
   `MED-RT`-namespace MoA/PE class (already in `substance_class`). Unlike slice 2a — where an edge could
   reach out into un-ingested SNOMED/MeSH and the parser had to refuse it — **a `CI_MoA`/`CI_PE` edge cannot
   introduce any new namespace.** The both-endpoints-must-be-ingested discipline
   ([`medrt.py`](../../../src/drugref/ingest/medrt.py) docstring) holds trivially here.
2. **No `NOTICE` change is required.** This slice redistributes no term MED-RT did not already contribute.

## 2. Where this sits — the interaction domain, which is neither classification nor membership

drugref has two orthogonal structures (composition tree; classification DAG). **Interactions are a third
kind of statement that rides on top of both** and is explicitly *not* either:

- It is **not classification** — a `CI_MoA` edge does not say drug *X* *is* an MoA class; it says *X* is
  contraindicated *with drugs that are*.
- It is **not membership** — and the codebase already says so, twice: [`db/002`:76](../../../db/002_schema_classes.sql)
  ("Indication/contraindication relations … are NOT membership — they are curated-overlay data for a later
  slice") and [`medrt.py`:226](../../../src/drugref/ingest/medrt.py) (which parses `CI_with`/`CI_MoA` today
  and deliberately drops them). **Slice 5a is that later slice.** Overloading `class_membership.relationship`
  with `CI_MoA` would conflate "is-a-kind-of" with "is-contraindicated-with" and silently corrupt every
  membership query and every class-inheritance walk. So this slice adds **its own table** (§5).

The payoff of the class-level shape is the same lever that justified building the class DAG in 2a/2b: one
stored `X CI_MoA C` row **expands at read time**, over the `class_membership` + `class_parent` tables that
already exist, into "X contraindicated with every drug that `has_MoA` C (or any descendant of C)." **Curate/
ingest once at the class node; apply to many pairs** — without materialising the pair explosion (§5.3).

## 3. Hybrid-store placement — a rebuildable projection, *not* the signed moat

ROADMAP Slice 5's headline calls the interaction overlay "append-only, **signed** … the moat." That
describes drugref's **own hand-curated** rules. MED-RT's `CI_MoA`/`CI_PE` content is **ingested from an
upstream authority**, so by the project's own hybrid-store rule (*ingested feeds = rebuildable projections;
curated knowledge = append-only signed overlay*) it belongs on the **projection** side, exactly like
`class_membership`:

- `class_contraindication` is a **rebuildable projection**, keyed by `ingest_run` provenance. Re-ingesting a
  newer MED-RT release **`DELETE`s this source's prior rows and re-inserts** (via the existing
  `clear_source_edges`-style scoping), so a contraindication MED-RT *retracts* upstream disappears here too.
- It is **not** under slice-1's append-only floor — a no-`DELETE` trigger would make the rebuild impossible,
  and there is no immortal identity to protect (the *identity* protected elsewhere — moiety UUID, class UUID
  — is untouched; this table only links two existing immortal IDs).

**The moat is the layer above this**, added later (Slice 5c): drugref's own append-only, signed review
decisions — severity, management, "confirmed / rejected / refined" verdicts — that *reference*
`class_contraindication` candidate rows and the SPL-mined ones. Slice 5a delivers the **candidate substrate**
that the moat curates; it is not itself the moat. The "quality-control gate" the moat exists for (§7)
operates on this substrate; it is not a licence or access gate.

## 4. Ground truth — measured against the real 2026.07.06 release, not the documentation

Established by parsing the real **`Core_MEDRT_2026.07.06_XML.xml`** (the same 45 MB file slice 2a ingests),
and by reading the shipped **MED-RT Documentation** for the operative predicate definitions. Pinned here
because the semantics — *drug–drug*, via a *co-administered* ingredient — are the whole basis of the slice
and must not be taken on memory of NDF-RT.

### 4.1 The predicates and their official definitions

Verbatim from the MED-RT Documentation:

- **`CI_MoA`** — "contraindicated **mechanism of action** of a **co-administered ingredient**" · CTY target
  **MoA** · inverse `[Inv] CI_MoA`.
- **`CI_PE`** — "contraindicated **physiological effect** of a **co-administered ingredient**" · CTY target
  **PE** · inverse `[Inv] CI_PE`.

Both are therefore **drug–drug**: the subject drug is contraindicated *in combination with* another drug
characterised by that MoA/PE. (For contrast, and to justify their exclusion here: `CI_with` = "therapeutic
or **co-morbid** contraindication" → a drug–**disease** statement, MeSH-keyed; `CI_ChemClass` = "chemical
structural class of a co-administered ingredient" → drug–drug but MeSH-keyed. Both are Slice 5b.)

### 4.2 What the release actually contains (measured)

| predicate | subject → object | assertions | distinct subject drugs | object namespace | both endpoints already ingested? |
|---|---|---|---|---|---|
| `CI_MoA` | RxNorm → MED-RT (MoA) | **462** | **420** | MED-RT | ✅ yes (RxNorm claim + MoA class) |
| `CI_PE`  | RxNorm → MED-RT (PE)  | **277** | **233** | MED-RT | ✅ yes (RxNorm claim + PE class) |

≈ **739 class-level DDI rules** across ~650 subject drugs, every endpoint already resident in drugref. The
object classes are a subset of the MoA/PE classes slice 2a already registered (e.g. subjects contraindicated
with *Immunologic Adjuvants [MoA]*, *Increased Immunologic Activity [PE]*). The subject side joins to
moieties through the **same `RXNORM_IN` index slice-2a membership already uses** — join yield is therefore
bounded by the moiety gate, exactly as measured for membership in 2a, not by anything new here.

### 4.3 The currency caveat — pinned, because it sets the clinical posture (§7)

The MED-RT Documentation states, of these very relationships, that they are established when an ingredient
first enters RxNorm and that **"Subsequent updates … based on labeling updates are not routinely
incorporated due to scope and resource constraints."** So this content is a **structural seed, not a current
evidence feed.** It is expert-derived and worth ingesting, but it is a **candidate tier** — cross-checked
against live SPL and human-reviewed before any high-intrusiveness use — never an auto-firing alert on its
own (§7). This is not a defect to hide; it is a property to record on every row's provenance.

## 5. Schema (`db/004_schema_interactions.sql`)

### 5.1 The new table

One new table, styled after `class_membership` (rebuildable projection; `ingest_run` provenance; no
append-only floor), but in the **interaction** domain and therefore separate from it:

```sql
-- db/004_schema_interactions.sql
-- drugref global tier, slice 5a: ingested drug<->class CONTRAINDICATIONS.
-- A rebuildable projection, like class_membership (db/002) -- NOT the append-only
-- signed overlay (that is the curated moat, a later slice). Re-ingesting a MED-RT
-- release DELETEs this source's rows and re-inserts, so a retracted upstream
-- contraindication disappears here too.

CREATE TABLE IF NOT EXISTS drugref.class_contraindication (
    -- The drug the statement is ABOUT: contraindicated when co-administered with a
    -- drug of object_class_uuid. A moiety, joined from the MED-RT RxNorm subject
    -- through the same RXNORM_IN claim slice 2a membership uses.
    subject_moiety_uuid uuid   NOT NULL REFERENCES drugref.substance_moiety(moiety_uuid),
    -- The MoA/PE class of the CO-ADMINISTERED drug. Already in substance_class (2a).
    object_class_uuid   uuid   NOT NULL REFERENCES drugref.substance_class(class_uuid),
    relationship        text   NOT NULL,
    source              text   NOT NULL,
    ingest_run          bigint NOT NULL REFERENCES drugref.ingest_run(ingest_run_id),
    PRIMARY KEY (subject_moiety_uuid, object_class_uuid, relationship),
    -- Only the two co-administered-ingredient predicates whose object is a MED-RT
    -- class drugref already ingests. CI_with / CI_ChemClass (MeSH-keyed) are 5b.
    CONSTRAINT class_contraindication_relationship
        CHECK (relationship IN ('CI_MoA', 'CI_PE')),
    -- Symmetric with substance_class.source; folded by ids.canonical_source.
    CONSTRAINT class_contraindication_source
        CHECK (source IN ('MED-RT'))
);

-- Read path: "who is contraindicated with drugs of this class" -- the object side
-- drives pair expansion (§5.3), so it is the indexed direction.
CREATE INDEX IF NOT EXISTS class_contraindication_by_object
    ON drugref.class_contraindication (object_class_uuid);
```

Notes on shape, each deliberate:

- **Subject is a moiety, object is a class** (not moiety↔moiety): the class-level form is what MED-RT
  asserts and what makes the row a *rule* rather than one pair. Pair-level moiety↔moiety rows are **not
  stored** (§5.3).
- **No `severity` / `mechanism` / `management` / `evidence` columns.** MED-RT supplies none of them (§4.3),
  and the project's convention is to not build empty columns a source cannot fill. Those dimensions belong
  to the **curated overlay** (Slice 5c), which will reference these rows — see tension (C). This keeps 5a a
  faithful projection and defers the richer ROADMAP-§8 `interaction_assertion` schema until there is
  reviewed content whose columns justify it.
- **`source` CHECK admits only `MED-RT` today**, widened per source exactly as `substance_class.source` was
  in `db/003`.

### 5.2 Why a separate migration (`db/004`), not an edit to `db/002`/`db/003`

Same reason recorded in `db/003`: the existing files use `CREATE TABLE IF NOT EXISTS` / guarded `ALTER`s and
are replayed whole every `apply_migrations`. A brand-new table is a clean additive `db/004`, itself guarded
(`CREATE TABLE IF NOT EXISTS`, `CREATE INDEX IF NOT EXISTS`) so replay stays idempotent.

### 5.3 Pair expansion is a read-time join, never stored rows

The concrete "drug A ✕ drug B" pairs are derived on demand by joining a contraindication to the members of
its object class — reusing `class_membership` (and, for sub-class inheritance, `class_parent`) built in
2a/2b. A view captures the direct (non-inherited) form:

```sql
CREATE OR REPLACE VIEW drugref.ddi_candidate_pair AS
SELECT ci.subject_moiety_uuid AS moiety_a,
       m.moiety_uuid          AS moiety_b,      -- the co-administered drug
       ci.relationship,
       ci.object_class_uuid   AS via_class,
       ci.source, ci.ingest_run
FROM   drugref.class_contraindication ci
JOIN   drugref.class_membership m
       ON m.class_uuid = ci.object_class_uuid
      AND m.relationship = CASE ci.relationship
                              WHEN 'CI_MoA' THEN 'has_MoA'
                              WHEN 'CI_PE'  THEN 'has_PE'  END
WHERE  m.moiety_uuid <> ci.subject_moiety_uuid;   -- never self-pair
```

**Sub-class inheritance (optional, and a known over-trigger risk).** A drug filed under a *specific* MoA
subclass is still contraindicated when the CI targets a broad ancestor of that subclass. Catching those
means walking `class_parent` **downward** from the CI target to its descendants before joining membership
(a recursive CTE). Tier 5a ships the **direct-membership** view above as the conservative default; the
descendant-expansion variant is offered as a second view and its blast radius (a CI on a broad MoA fans out
to every member of every descendant) is exactly why the reviewed overlay (§7), not this projection, decides
what actually alerts.

## 6. Ingest (extend the MED-RT parser drugref already runs — one file read)

- **Parser ([`medrt.py`](../../../src/drugref/ingest/medrt.py) — pure, no DB).** The association loop
  already visits `CI_MoA`/`CI_PE` and drops them at line 226. Extend it to **emit** them: add
  `CI_RELATIONSHIPS = frozenset({"CI_MoA", "CI_PE"})`, and in the loop, when `name in CI_RELATIONSHIPS`
  with `from_ns == RxNorm`, `to_ns == MED-RT`, and `to_code in nui_by_code`, yield a new record
  `ContraindicationAssertion(rxcui=from_code, class_nui=nui_by_code[to_code], relationship=name)`. Add a
  `contraindications: list[ContraindicationAssertion]` field to `ParsedMedrt`. This is the change
  `medrt.py`:226 already anticipates ("curated-overlay data for a later slice"). No second parse of the
  45 MB file — it rides the existing `medrt.parse`.
- **Writer (small, in a new `drugref/ingest/medrt_interactions.py` or a writer in `classes.py`'s sibling).**
  A thin `add_contraindication(conn, subject_moiety_uuid, object_class_uuid, relationship, source, run_id)`
  mirroring `add_membership`, plus reuse of the existing `clear_source_edges` scoping extended to also clear
  `class_contraindication` for the source. Keeping the interaction *writer* in its own module preserves the
  domain separation even though the *parse* and the *run* are shared.
- **Orchestrator ([`medrt_run.py`](../../../src/drugref/ingest/medrt_run.py)).** After memberships (step 4),
  add **step 5 — contraindications**, reusing the **same** `moieties_by_rxcui` index (subject side) and the
  **same** `uuid_by_nui` map (object side) already built in the function. Clear this source's prior
  contraindication rows in the same rebuild point as the edge clear (step 2). Extend `MedrtSummary` with
  `contraindications` (rows written) and `unmatched_ci_rxcuis` (subject RxCUIs not on a gated moiety —
  counted, never silently dropped, exactly as `unmatched_rxcuis` is for membership).
  - **The interaction domain shares MED-RT's run on purpose.** One file, one release, one checksum, one
    `ingest_run` = correct shared provenance. The *table* is separate (domain); the *run* is shared (source).

Pure function first; the DB-touching orchestrator stays the thin shell — the slice-2a/2b posture.

## 7. Clinical-safety posture (the reason 5a is a projection, not an alert)

A `class_contraindication` row is a **candidate**, and the design must make it impossible to mistake it for
a decision:

- **Candidate tier only.** Per §4.3, MED-RT does not track label updates. A 5a row must carry its
  `ingest_run`/`source` provenance to the consumer and must **not** be rendered as a high-intrusiveness
  alert on its own. It feeds review and cross-checking (against SPL mining, MeDIC, the ONC high-priority
  list), and the **reviewed overlay** decides what alerts.
- **Over-trigger is structural, not incidental.** "Contraindicated with any drug of MoA C" inherited down a
  broad class can implicate large drug sets (§5.3). Specificity comes from the review layer and from
  severity/management the overlay adds — never from MED-RT, which supplies none.
- **No severity ⇒ no severity is invented.** 5a stores the *fact of a MED-RT contraindication assertion*,
  not a graded risk. Any grade is a later, attributed, curated judgement.

This is the same three-tier release philosophy the project already holds (raw → normalized candidate →
clinically reviewed): **5a populates the candidate tier of the interaction domain.**

## 8. Testing (TDD, failing-test-first — mirrors slice-2a/2b §7)

Unit (Python, no DB), against the fixture extended in §9:

- Parser emits a `ContraindicationAssertion` for a `CI_MoA` edge (RxNorm → MED-RT MoA class) and for a
  `CI_PE` edge; emits **none** for a `CI_MoA` whose object class is *not* ingested (endpoint scoping holds),
  and **none** for the classification/`may_treat`/`CI_with` associations (they stay out of this table).
- The subject/object direction is correct: subject = `from_code` (the drug), object = `to_code` (the
  co-administered drug's class) — the reverse would invert the clinical meaning and no hand fixture would
  catch it, so it is pinned against a known real example.

Integration (DB-gated), against the fixture + the slice-1 seed + slice-2a classes:

- A `CI_MoA` assertion whose subject RxCUI is a gated-in moiety and whose object MoA class exists writes one
  `class_contraindication` row with `relationship='CI_MoA'`, `source='MED-RT'`; a subject RxCUI **not** in
  the registry writes no row and **increments `unmatched_ci_rxcuis`**.
- `ddi_candidate_pair` expands a stored `X CI_MoA C` row to `(X, Y)` for a `Y` that `has_MoA C`, and
  **excludes the self-pair** where the subject itself is a member of `C`.
- Idempotent re-ingest: `class_contraindication` **rebuilt not duplicated**; a re-run leaves
  `class_membership`/`class_parent` intact (the contraindication clear is scoped to its own table + source).

## 9. Fixture (extend `tests/fixtures/make_medrt_subset.py`)

The MED-RT fixture generator already exists and is re-runnable. Extend its selection to include **one
subject ingredient carrying a `CI_MoA` edge to an already-selected MoA class**, **one carrying a `CI_PE`
edge to a selected PE class**, and **one `CI_MoA` edge whose object class is deliberately not selected**
(to prove endpoint scoping drops it). No redaction concerns beyond slice 2a's existing ones — the endpoints
are RxNorm and MED-RT, both licensed; the generator continues to emit MED-RT content only. A shape test pins
the fixture's CI edge set so a hand regeneration cannot silently drift.

## 10. Design tensions recorded (resolved)

- **(A) New table, or reuse `class_membership`?** → **New table** (`class_contraindication`). The codebase
  already declares `CI_*` is "NOT membership" ([`db/002`:76](../../../db/002_schema_classes.sql)); reusing
  the membership table would corrupt classification queries and the inheritance walk. The two share a
  *shape*, not a *meaning*.
- **(B) Is this the "signed, append-only moat" ROADMAP Slice 5 names?** → **No — it is the rebuildable
  *projection* half.** MED-RT content is ingested, so per the hybrid-store rule it is a drop-and-rebuild
  projection, not the append-only overlay. The moat is drugref's own reviewed judgements layered on top
  (Slice 5c), referencing these candidate rows. 5a builds the substrate the moat curates.
- **(C) Store severity/mechanism/management now (the ROADMAP-§8 `interaction_assertion` schema)?** → **No.**
  MED-RT supplies none of them; empty columns would misrepresent the projection as richer than it is. The
  richer curated schema arrives with the reviewed overlay, which has content to fill it. 5a's table is
  intentionally minimal and forward-compatible (the overlay references it by its PK).
- **(D) Materialise moiety↔moiety pairs, or expand at read time?** → **Read time** (`ddi_candidate_pair`
  view over `class_membership`). Storing pairs would explode ~739 class rules into a large, redundant, and
  rebuild-fragile pair set; the class-level row + existing membership/DAG tables already encode it losslessly
  and are the project's stated curation-economy lever.
- **(E) Include `CI_ChemClass` (also drug–drug) now?** → **No.** Its object is a **MeSH** chemical
  descriptor drugref has not ingested; admitting it would reach a namespace this slice cannot resolve, and
  it belongs with the MeSH-keyed CI/indication content in Slice 5b.
- **(F) Silent drop of subjects not in the registry?** → **No.** Counted as `unmatched_ci_rxcuis`
  (distinct RxCUI), the same no-silent-exclude posture as slice-1's gate and slice-2a's `unmatched_rxcuis`.
- **(G) Separate interaction ingest run, re-reading the file?** → **No.** Ride the existing `medrt.parse`
  and `medrt_run` (one 45 MB read, one `ingest_run`, one checksum = correct shared provenance); keep only
  the interaction *writer* in its own module for domain separation.

## 11. Explicitly out of scope for slice 5a

- **`CI_with` (drug–disease contraindication, ~11.5k) and `CI_ChemClass` (drug–drug by chemical class,
  ~1.9k)** — object is a MeSH descriptor; require **MeSH disease/chemical descriptor ingest** first → Slice
  5b (the natural companion, same MeSH licence already accepted in slice 2b).
- **`may_treat` / `may_prevent` / `may_diagnose` (indications, ~18k) and `induces` (drug-induced states,
  ~170)** — MeSH-keyed drug–disease content; a public-domain, drugref-owned alternative to MeDIC for the
  drug–disease axis → also Slice 5b, distinct from the DDI concern here.
- **Severity / mechanism / management / evidence grading and human review** — the curated, append-only,
  signed overlay (Slice 5c); 5a is its candidate substrate.
- **SPL/DailyMed-mined interactions (ONSIDES-method)** — a separate ingest, different source and licence
  posture; complements this MED-RT seed, does not belong in it.
- **Auto-firing prescriber alerts / the HTTP API** — no interaction data is *served* or *alerted* by this
  slice (§7); it lands the projection the later tiers build on.
```
