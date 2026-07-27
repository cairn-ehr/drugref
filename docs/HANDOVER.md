# HANDOVER — drugref

> **Disposable working scaffolding, NOT a source of truth.** The canonical *what/why* lives in the design
> specs under [`docs/superpowers/specs/`](superpowers/specs/). If this file disagrees with a spec, the spec
> wins. Regenerate it at the end of every working session (nextsession rule 9).

## What drugref is

**drugref.org v2** — an open, co-equal **public-good drug-information service** (any EHR / pharmacy / app can
consume it; Cairn is its first client on the same public-API footing). Two tiers: a **global tier**
(jurisdiction-independent — substance identity, chemistry, classes, interactions) built first, and a
**local tier** (country-specific packaging/pricing; Australia/PBS first). Designed to co-reside in a Cairn
deployment's PostgreSQL **or** run standalone, but **advisory reference data — never on Cairn's signed
inter-node wire core**.

## ⇒ NEXT

**Merged to `main`:** slice 1 (identity spine, PR #1) · slice 2a (MED-RT classification, #9) · slice 2a.1
(source-neutral class registry, #10) · slice 2b (MeSH PA) · slice 5a (MED-RT CI_MoA/CI_PE) · the
foundation review · Plan A (open-question registry) · slice 8a (PBS localisation, #28).

**⇒ DONE ON THIS BRANCH (`feat/plan-b-descendant-expansion`): Plan B — DAG-descendant expansion.**
**384 tests green** (347 → 384). Detail below. Note [#15](https://github.com/cairn-ehr/drugref/issues/15)
was closed as COMPLETED when the *measurement* landed in the additive-effect spec; the code it asked for is
what this branch built.

**⇒ Next candidates:**

- **Slice 3 — composition tree (salts/esters/hydrates via GSRS).** Doubly motivated: slice 8a's salt-strip
  heuristic contributes 0.0% of bridge rows at the ceiling, and GSRS salt relationships are the real fix.
- **Slice 5b — MeSH-keyed CI/indications.** The largest single block of unlocked content (~31k assertions).
  Adding a CI predicate is now **one `ci_axis` INSERT** (`relationship`, `membership_relationship`,
  `expands_descendants`) plus the `source`/vocabulary CHECKs — neither read-path view needs an edit. See
  "Slice 5b" below for the measured inventory and the one genuinely new piece (M-code → descriptor).
- **Plan C — the accumulation model.** Gated on 5b (§12-H): curating before 5b risks paying for what the
  release already supplies.

## Plan B — DAG-descendant expansion (this branch)

Design: §3.2 / §7.1 / §11 step 2 of the
[additive-effect & open-question spec](superpowers/specs/2026-07-25-drugref-additive-effect-and-open-question-design.md).
Plan: [Plan B plan](superpowers/plans/2026-07-27-plan-b-descendant-expansion.md) — read it for the full
evidence table.

**The defect.** `ddi_candidate_pair` joined **direct** `class_membership` only, so a contraindication naming
a broad class returned nothing for a drug filed solely under a descendant. `db/004` called that "the
conservative default"; for a contraindication it reads backwards — **fewer rows is the harm direction**.
Over 739 rules: `CI_MoA` 21.9% hidden, `CI_PE` **85.2%** hidden. MED-RT files membership at the most
*specific* node while writing rules against the *parent*, so direct-only is systematically mismatched with
how the source is authored.

**What `db/010` ships:**

1. **`ddi_candidate_pair` now descends the class DAG**, and gained `member_class` (the class the partner is
   actually filed under) + `is_direct`. **`WHERE is_direct` reproduces the pre-Plan-B row set exactly**, so
   a precision-sensitive consumer opts out explicitly — and a consumer who *forgets* the filter gets more
   rows, which is the safe direction to fail in. Recursion is `UNION` over `(root, class)`, not paths:
   cycle-safe (db/002 forbids only self-parenting) and linear in a multi-parent DAG. Same idiom as
   `db/008`, deliberately — one recursion pattern in the codebase, not two.
2. **`class_expansion_policy`** — the deny-list, as **data** a pharmacist can read and diff. Keyed
   `(source, source_code)`, **not** `class_uuid`: a migration runs before any class exists (no FK possible),
   and storing a derived UUID would put `ids.mint_class_uuid` in a second place. **Curator policy, not a
   projection — no ingest clears it.**
3. **`ci_axis.expands_descendants`** — per-predicate, defaulting true. Slice 5b's predicates sit over a
   MeSH vocabulary with a different tree shape, so *whether* a predicate expands is declared beside *what*
   it expands over.
4. **`gap_unreviewed_expansion_root`** + a fourth `gap_kind` — the review gate, so the list cannot rot
   silently. **`expansion_policy_unresolved`** is the other half: a decision whose class upstream re-keyed.

**The deny-list: 11 denied, 3 explicitly allowed.** The `>20 descendant classes` heuristic finds **exactly
14** CI object classes in the 2026.07.06 release — **all PE, not one MoA**, confirming §3.2 on real data.
But size is only how they were *discovered*; the criterion is qualitative ("does it name an effect a
prescriber can act on, or only the organ system?"). Ten are `<system> Activity Alteration` buckets. The
other four name a direction *and* a function, so three are **explicitly allowed** — `Vasoconstriction`
(54→119), `Decreased Immunologically Active Molecule Activity` (35→327), `Increased Sympathetic Activity`
(16→16, all 21 children empty). The fourth, **`Increased Immunologic Activity`, is denied on its SUBTREE
rather than its name**: `Acquired Immunity [PE]` (1,109 drugs — in effect every vaccine) sits beneath it,
fanning 33 direct members out to 1,313.

**`allow` is not the same as absent.** Absent means *unreviewed*, which expands **and** raises a question.

**The deny-list filters the RULE'S OBJECT CLASS — it is not a traversal barrier.** `Decreased Coagulation
Activity` is a *descendant* of the denied `Hematologic Activity Alteration`, so a barrier reading would
leave the coagulation rules unexpanded, deleting the single most important case Plan B exists to fix.
Pinned by `test_a_descendant_of_a_denied_root_still_expands` — do not delete that test.

**Measured, terminology level** (distinct subject/partner/via-class/predicate, self-pairs removed):

| policy | pairs |
|---|---|
| direct only (before) | 20,462 |
| full expansion, no deny-list | 58,288 |
| **shipped (11 deny / 3 allow)** | **29,687** |

So the deny-list keeps ~24% of the recall gain and removes ~76% of the fan-out.

**Verified end-to-end against the real release** (UNII 26Feb2026 + MED-RT 2026.07.06, live PG18):
`expansion_policy_unresolved` **empty** (all 14 NUI literals resolve) · `gap_unreviewed_expansion_root`
**empty** · pairs **6,395 vs 4,363 direct (+46.6%)** — lower than the terminology figure because the moiety
gate binds, the familiar pattern · and the headline case confirmed: a rule on `Decreased Coagulation
Activity [PE]` now reaches **warfarin**, via `Decreased Coagulation Factor Activity [PE]`, plus 54 other
partners across `Decreased Platelet Aggregation`, `Decreased Prothrombin Activity`, `Increased
Fibrinolysis` and `Increased Thrombolysis`. A filtered pair lookup on the full DAG costs ~25 ms.

**Filed, not fixed: [#31](https://github.com/cairn-ehr/drugref/issues/31)** — a rule on a *denied* root with
no direct members yields no pair and no gap view reports it (`Endocrine Activity Alteration`,
`Cardiovascular Activity Alteration`: 1 rule each against the gated registry). **Pre-existing, not a
regression** — those rules returned nothing before Plan B too; Plan B shrank the affected set to these two.
`db/010` narrows `gap_unpopulated_contraindication`'s `COMMENT ON` to say so rather than deleting the
now-mostly-stale caveat.

## Current state, by layer

**Slice 1 — the identity spine.** Schema `drugref` (`ingest_run`, `substance_moiety`, `identity_claim`) +
an append-only row-level floor. Own immortal `moiety_uuid` (`UUIDv5` on UNII at first sighting, then
**pinned forever**; namespace `d07651ee-311d-552b-a97b-591219eb3ad3`), never keyed on a name. External IDs
are **append-only claims** (UNII, INN, RXNORM_IN, CAS, PUBCHEM_CID, INCHIKEY, CHEBI), so drugref doubles as
a public cross-walk. Membership gate = **has-INN** (UNII `INN_ID`) **or** a small closed legacy allow-list.
International-by-construction seeding: UNII (public domain) backbone, INN display anchor, ChEBI (CC BY 4.0)
chemistry, **RxNorm demoted to a claim**, a closed hand-curated USAN↔INN crosswalk.
**Floor scope:** row-level UPDATE/DELETE only — `TRUNCATE` and the table-owning role remain bypasses
([#2](https://github.com/cairn-ehr/drugref/issues/2)).

**Slice 2a / 2a.1 — the classification DAG.** `substance_class`, `class_parent`, `class_membership` seeded
from **MED-RT**: 3,634 classes, 3,961 edges (440 multi-parent), 27,540 memberships over 6,012 ingredients.
Class identity is immortal *by determinism* — `class_uuid = UUIDv5(CLASS_NAMESPACE, SOURCE + ":" + code)`,
so a rebuild re-derives it and no pin table is needed. Edges are **rebuildable projections**, deliberately
outside slice 1's floor. Membership joins via the `RXNORM_IN` claims slice 1 already records — no new
bridge data. 2a.1 (`db/003`) generalised the registry off its one authority (`source_code`/`published_code`,
per-`(source, source_code)` uniqueness); **existing MED-RT class UUIDs are unchanged, pinned by frozen
literals** — the derivation is the join key of both edge tables, so a drift would orphan every edge with no
error anywhere. The stored `source` and the UUID key derive from one canonicalisation
(`ids.canonical_source`); extend that **and** `db/003`'s CHECK together when an authority lands.

**Licence scoping is structural**, not a matter of intent: only MED-RT concepts are *defined* in the
release (SNOMED/MeSH appear solely as edge endpoints), so requiring both endpoints of every edge to be an
ingested class is what keeps unlicensed content out.

**Slice 2b — MeSH PA.** 568 PA class descriptors, their tree-number DAG and memberships, on the **same
three tables** (no schema change). `ingest/mesh.py` is a pure streaming (`iterparse`) parser;
`ingest/mesh_run.py` holds the **two-key bridge** — UNII-primary → CAS-fallback against slice-1
`identity_claim` rows, **no new external source**. 10,505 member substances, **73% joinable**; unmatched
counted, split no-key vs key-not-in-registry.

**Slice 5a — the first interaction data.** `db/004` `class_contraindication` (rebuildable projection) +
read-time pair expansion. `db/006` replaced the comment-enforced CHECK↔CASE coupling with a **`ci_axis`
table the vocabulary is a foreign key into**, put `source` in the PK, and moved the clinical contract into
`COMMENT ON`. **Candidate tier only** — MED-RT does not track label updates, so nothing here auto-alerts.

**Plan A — the open-question registry** (`db/007`, `db/008`). Coverage gaps are published as a **queryable
register** rather than hidden. **The hybrid split is the design:** `open_question` is a rebuildable
projection re-derived every ingest; curator intent (`question_state`), tier watermarks
(`question_source_check`) and findings (`question_evidence`) are **append-only**, keyed off an immortal
`question_uuid` external tooling can cite — so a rebuild can never erase a `withdrawn`. **Populated is per
axis** (joins `ci_axis`). **Watermark, not closure:** only `withdrawn` is terminal. **A closed gap carrying
curator work is retired, not deleted** (`is_current`) — the curated tables cascade from `open_question`
*and* refuse `DELETE`, so deleting one aborts the whole ingest. Every orchestrator rebuilds the register as
its last step before commit.

**Slice 8a — PBS localisation, the local tier's first attachment.** `db/009` (three tables, a rebuildable
projection with **no** append-only floor, because a de-listed PBS item must be able to disappear);
`ingest/pbs.py` (pure parser), `local.py` (single writer), `ingest/pbs_run.py` (orchestrator), bridging PBS
products to the global spine **by name alone** — the only licence-clean join, since PBS carries no
UNII/CAS/InChIKey. `local_product_uuid` is a pure function of `(jurisdiction, source, source_code)`.

Measured against the real July-2026 release (14,840 items): **92.4%** name-bridge ceiling against all UNII
substance names but only **84.6%** against today's INN-gated registry — **the moiety gate, not the bridge,
is the binding constraint** ([#26](https://github.com/cairn-ehr/drugref/issues/26)), the same pattern
already measured for MED-RT and MeSH, now on a third independent axis. The salt-strip heuristic contributes
1.1% of bridge rows gated and **0.0% at the ceiling** — reported as near-worthless rather than left to
imply otherwise; slice 3 is the real fix. The residual is otherwise AU/INN-vs-USAN spelling divergence
(paracetamol, cefalexin, ciclosporin — the deferred alias list **has now earned its place**, and the closed
USAN↔INN crosswalk is its home) and non-drugs the gate correctly excludes.

**Licence posture — read before extending slice 8a.** Node-local plug-in only: drugref ships AGPL-3.0
ingest code and schema, **never a PBS release**, with one stated exception — `tests/fixtures/
pbs_items_subset.csv` commits 11 real rows and is the thing that goes if
[#25](https://github.com/cairn-ehr/drugref/issues/25) lands negative. ATC (WHO, NC+ND) and AMT/SNOMED CT-AU
are quarantined **structurally**: `items.csv` has no such column, the parser reads a fixed allow-list, no
table has anywhere to put them, and a test proves it by ingesting a fixture with **planted**
`atc_code`/`amt_code` columns and asserting neither value reaches any drugref table (matched by
**substring**, so a canary concatenated into a longer value is caught too).

## Slice 5b — the task, measured

The rest of MED-RT's interaction/indication content, all `RxNorm → MeSH` and therefore blocked **only**
because drugref has not ingested MeSH disease/chemical descriptors (2b ingested the **PA** subset only).
In the 2026.07.06 release: **`CI_with`** 11,524 assertions / 3,720 subjects · **`CI_ChemClass`** 1,939 / 565
· **`may_treat`/`may_prevent`/`may_diagnose`** ~18,144 (a public-domain, drugref-owned MeDIC alternative) ·
**`induces`** 170.

**In order:** (1) ingest MeSH disease + chemical descriptors — **licence already cleared**; the one
genuinely new piece is that MED-RT endpoints are MeSH **M-codes** (`M0006033`), so it needs **M-code →
descriptor** resolution, where 2b keyed on descriptor UI / tree numbers (the accessory crosswalk resolves
50.8%). (2) Extend `medrt.py` to emit these predicates once the object resolves — the loop already sees and
drops them. (3) **Design storage in a 5b spec first:** `CI_with`'s object is a *disease*, not a
`substance_class`, so it likely wants its own `drug_disease_*` table rather than overloading
`object_class_uuid`; indications are a separate relation again. (4) Same posture as 5a: rebuildable
projection, candidate tier, unmatched counted never dropped.

Reuse from 5a: the `interactions.py` writer pattern, `unmatched_ci_rxcuis` counting, per-source rebuild.
**Only the MeSH object side is new.** Also unblocked: MED-RT **`has_SC`** — 3,632 assertions, of which
**248 target MED-RT itself** and never needed the bridge at all.

## Three things the MED-RT documentation got wrong (verified against the real release)

Each would be a silent, plausible bug invisible to a hand-written fixture: **`Parent Of` runs parent →
child**, not the reverse; **`[HC]` concepts are the 26 alphabetical navigation bins** (`"A
[Preparations]"`), 18,450 of 21,058 class→ingredient edges; and **EPC membership is licence-clean and
hierarchical**, not routed through SNOMED/MeSH. The fixture is therefore extracted from the real release by
a committed, re-runnable extractor, so it can never re-encode a wrong assumption about upstream shape.
Likewise for MeSH: **Descriptors DO carry UNIIs** in `RegistryNumber` (aspirin D001241 = `R16CO5Y76E`), and
a record may carry several — so key extraction is set-valued.

## Architecture in one breath

- **Hybrid store** mirroring a Cairn node: **rebuildable projections** for ingested feeds (drop-and-rebuild,
  version-pinned, provenance-tagged via `ingest_run`) + an **append-only, signed overlay** for curated
  knowledge (the DDI moat — slice 5c, not built). `class_expansion_policy` is a third, small category:
  curator *policy*, edited in place, cleared by nothing.
- **Two orthogonal structures**: a **composition tree** (moiety → salt → clinical drug → product) and a
  **classification DAG** (class ⊂ class; moiety ∈ many classes). The curated overlay attaches to either and
  **inherits along the edges** — the key curation-economy lever. Plan B is the first read path that
  actually walks those edges.
- **Substrate**: Python 3.12 + `uv`, `psycopg` v3, PostgreSQL ≥ 18. Advisory tier, but **integrity is
  enforced in the DB, not app code**.

## How to run / test

```bash
uv sync
uv run pytest                      # unit tests run anywhere; DB-gated tests SKIP without a DSN
# 384 tests, of which ~230 are DB-gated -- a run without this DSN passes while
# exercising none of the schema, floor, views or orchestrators:
DRUGREF_TEST_DSN='host=localhost port=5532 dbname=drugref_test user=postgres' uv run pytest
ruff check .
```

CI (`.github/workflows/ci.yml`) runs the suite against a PostgreSQL 18 service container, and `conftest`
**fails rather than skips** when `CI` is set — so the DB layer can never go green by being skipped.

- **Schema:** `db/001` identity spine · `002` classification · `003` registry generalised · `004`
  contraindication projection · `005` supersession/floor hardening · `006` `ci_axis` + view contract · `007`
  question registry · `008` gap views · `009` local (PBS) tier · `010` descendant expansion.
  **Read the LATEST file that touches an object for its actual shape** — 002 still shows superseded
  MED-RT-specific columns, 004's relationship CHECK is replaced by 006's FK, and 006's `ddi_candidate_pair`
  is replaced by 010's.
- **Migrations are immutable once applied.** `apply_migrations` records each file's checksum in
  `drugref.schema_migration` and raises if an applied file changed — so altering the schema, *including
  re-issuing a `COMMENT ON`*, means a new `db/NNN_*.sql`.
- **Code:** `src/drugref/{ids,claims,classes,db,interactions,local,questions}.py` +
  `src/drugref/ingest/{unii,gate,run,chebi,medrt,medrt_run,mesh,mesh_run,pbs,pbs_run}.py`; seed data under
  `src/drugref/data/`; fixtures under `tests/fixtures/`.
- Current dev DSN (Postgres.app, PG18): `host=localhost port=5532 dbname=drugref_test user=postgres`.
- **Upstream feed files are NOT committed** (`downloads/` is gitignored):
  - **MED-RT** — [NCI EVS](https://evs.nci.nih.gov/ftp1/MED-RT/) (`Core_MEDRT_*_XML.zip`); regenerate the
    fixture with `python tests/fixtures/make_medrt_subset.py <xml> > tests/fixtures/medrt_subset.xml`
    (regeneration must keep the endpoint redaction — a test enforces it).
  - **MeSH** — [NLM](https://nlmpubs.nlm.nih.gov/projects/mesh/MESH_FILES/xmlmesh/): `desc2026.gz`,
    `supp2026.gz`, `pa2026.xml`. NLM throttles per connection hard; a segmented byte-range fetch beats it
    ~18×. Regenerate with `python tests/fixtures/make_mesh_subset.py downloads tests/fixtures/`.
  - **PBS** — the `?variant=3` query parameter is **required** or the server 404s; files are UTF-8 **with a
    BOM**, so open with `encoding='utf-8-sig'`:
    ```bash
    curl -L -o downloads/pbs-2026-07.zip \
      "https://www.pbs.gov.au/publication/schedule/2026/07/2026-07-01-PBS-API-CSV-files.zip?variant=3"
    ```
    Ingest reads **only** `items.csv`, per the licence quarantine. Regenerate with
    `python tests/fixtures/make_pbs_subset.py downloads/tables_as_csv/items.csv > tests/fixtures/pbs_items_subset.csv`.

## Open follow-ups (all filed as GitHub issues)

**Floor & identity**
- [#2](https://github.com/cairn-ehr/drugref/issues/2) **Floor hardening** — close the `TRUNCATE` +
  owner-role bypass via RLS + privilege separation. **Note the test-suite coupling:** `grep -l TRUNCATE
  tests/*.py` finds **seven** modules, each truncating in an autouse fixture because their orchestrators
  commit internally and escape the `conn` fixture's rollback. Those fixtures depend on precisely the bypass
  this closes, so hardening must land with a replacement isolation strategy.
- [#3](https://github.com/cairn-ehr/drugref/issues/3) **UNII-change immortality** — structural re-key by
  InChIKey, deferred.
- [#17](https://github.com/cairn-ehr/drugref/issues/17) **Remaining no-silent-drop gaps** — MeSH PA records
  with no `DescriptorUI`; the legacy allow-list still keyed on a display name rather than a UNII.

**Ingest correctness (all found by measuring the real releases)**
- [#27](https://github.com/cairn-ehr/drugref/issues/27) **`ingest/unii.py` reads a non-existent `PT`
  column** — the real release uses `Display Name`, so every moiety gets an empty `display_name` and it does
  not raise. Visible in Plan B's own verification output. **Cheap and high value — good next task.**
- [#26](https://github.com/cairn-ehr/drugref/issues/26) **UNII gate excludes common drugs** — `INN_ID` is
  empty for amoxicillin, morphine, codeine, doxycycline, tacrolimus, dasatinib. The binding constraint
  behind every coverage number in this file.
- [#5](https://github.com/cairn-ehr/drugref/issues/5) INN sourced from UNII PT, not an authoritative WHO
  list.
- [#7](https://github.com/cairn-ehr/drugref/issues/7) / [#29](https://github.com/cairn-ehr/drugref/issues/29)
  **Row-at-a-time ingest** — MED-RT (~31k round trips, plus `ElementTree.parse` holding 45 MB) and PBS
  (~28k). `executemany`/`COPY` + batch commits + `iterparse`.
- [#16](https://github.com/cairn-ehr/drugref/issues/16) **A crashed ingest leaves no trace** — the
  `ingest_run` row is written inside the run's own transaction, so a failure rolls it back. Needs a
  connection-ownership decision, and a CLI.
- [#30](https://github.com/cairn-ehr/drugref/issues/30) `strip_salt` drops only one trailing token —
  measure before building; slice 3 may supersede it.

**Interaction model**
- [#31](https://github.com/cairn-ehr/drugref/issues/31) **Denied-root rules with no direct members yield no
  pair, unreported** — Plan B's residue, pre-existing rather than a regression (above).
- [#19](https://github.com/cairn-ehr/drugref/issues/19) **41 of 739 CI rules are structurally dead** — the
  object class has no member anywhere in its subtree. The highest-value curation worklist available:
  upstream vouching that the answer matters. Largely an **indexing loss, not a knowledge gap** — openFDA
  labels carry the statements (§3.5), which is why the cost ladder puts `openFDA-SPL` above `literature`.
- [#20](https://github.com/cairn-ehr/drugref/issues/20) **n-ary interactions** — decide before 5c builds on
  the pairwise shape. MED-RT does not assert the triple-whammy even pairwise.
- [#8](https://github.com/cairn-ehr/drugref/issues/8) **Class-level `has_*` assertions unused** (~756
  edges) — the other half of making the DAG carry knowledge, now that Plan B walks it.

**Licence deeds (blockers before production, per rule 6)**
- [#6](https://github.com/cairn-ehr/drugref/issues/6) Re-confirm the MED-RT deed against the live NLM
  source-release doc (the distribution ships no licence file; NLM's doc was HTTP 502 at design time).
- [#25](https://github.com/cairn-ehr/drugref/issues/25) PBS redistribution — blocks bundling, not
  node-local ingest; needs written Dept-of-Health confirmation.

**Verify-before-production, generally:** re-run each parser against a full current release and re-confirm
the aggregate numbers; the parsers are validated against committed fixtures extracted from real releases,
which is not the same thing.

## Repo facts

- GitHub `cairn-ehr/drugref` · default branch `main` · **AGPL-3.0** · attribution in `NOTICE` (unchanged by
  Plan B — no new source).
- Coding rules live in CLAUDE.md and the `nextsession` skill.
- Public docs site: `docs-site/` (MkDocs Material) → `docs.drugref.org`, deployed by
  `.github/workflows/docs.yml`. Its **Design decisions** section holds *living* records (revised in place,
  reversed ones removed), distinct from the immutable per-slice specs. Specs/HANDOVER/ROADMAP are **not**
  published.
