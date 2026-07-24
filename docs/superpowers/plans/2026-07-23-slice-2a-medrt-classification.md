# Slice 2a — MED-RT Classification DAG + Membership — AS BUILT

> **Status: delivered.** This began as a forward implementation plan. Reading the real MED-RT release
> overturned several assumptions in it (below), so rather than leave a plan that contradicts the shipped
> code it has been replaced by this as-built record. The **canonical design is the
> [slice-2a design spec](../specs/2026-07-23-drugref-slice-2a-medrt-classification-design.md)**; the code
> and its tests are the detail.

**Goal:** a classification layer over the active-moiety spine — class registry, subclass DAG, and
many-to-many moiety↔class membership, seeded from MED-RT.

**Outcome:** 91 tests green. Against the full `Core_MEDRT_2026.07.06_XML` release: **3,634 classes,
3,961 DAG edges (440 multi-parent), 27,540 memberships across 6,012 ingredients**, parsed in ~4s.

## What shipped

| File | Responsibility |
|---|---|
| `db/002_schema_classes.sql` | `substance_class`, `class_parent`, `class_membership` + CHECK constraints. No append-only floor, by design. |
| `src/drugref/ids.py` | `CLASS_NAMESPACE` + `mint_class_uuid(nui)` — deterministic UUIDv5 on the MED-RT NUI. |
| `src/drugref/ingest/medrt.py` | Pure Apelon-DTS XML parser, scoped to licensed namespaces. |
| `src/drugref/classes.py` | The only module writing the class tables (mirrors `claims.py`'s single-writer role). |
| `src/drugref/ingest/medrt_run.py` | Orchestrator: classes → clear prior edges → rebuild DAG → join membership. |
| `tests/fixtures/make_medrt_subset.py` | Re-runnable extractor that regenerates the fixture from a real release. |
| `tests/fixtures/medrt_subset.xml` | The fixture, **extracted from real data** (49 classes, 39 DAG edges). |
| `tests/test_{ids,schema_classes,medrt_parser,medrt_run}.py` | 56 new tests. |

## Design decisions worth re-reading before changing anything

- **Class identity is immortal by determinism, not by a floor.** `class_uuid = UUIDv5(CLASS_NAMESPACE,
  "MEDRT:"+NUI)`. A rebuild re-derives identical UUIDs, so no pin table is needed.
- **Class edges are rebuildable projections** and sit deliberately *outside* slice 1's append-only floor.
  `clear_source_edges()` DELETEs on purpose: a parent removed upstream must be removed here, which an
  insert-only merge cannot express. Adding a no-DELETE trigger to these tables breaks re-ingest.
- **Membership needs no new bridge data** — it joins through the `RXNORM_IN` claims slice 1 already
  records, because MED-RT's ingredient concepts are keyed on RxCUI.
- **Licence scoping is structural.** Only MED-RT concepts are *defined* in the release; SNOMED and MeSH
  appear solely as association endpoints. Requiring both endpoints of every edge to be an ingested class is
  therefore the mechanism that keeps unlicensed content out — not a review convention.
- **Unmatched RxCUIs are counted, never dropped silently**, matching the slice-1 gate's posture.

## What the real release corrected (and why the fixture is generated)

The MED-RT documentation alone produced three wrong conclusions. Each would have been a silent, plausible
bug that a hand-written fixture would have happily confirmed:

1. **`Parent Of` runs parent → child**, not child → parent. The original plan had it reversed, which would
   have inverted the entire DAG. Verified two independent ways: the MoA root appears as `from_code` 9× and
   as `to_code` never (a root has no parent), and `"A [Preparations]"` is the *from* of paracetamol.
2. **`[HC]` concepts are the 26 alphabetical navigation bins**, not classifications — 18,450 of the 21,058
   class→ingredient edges. Ingesting them would file nearly every drug under a letter.
3. **EPC membership is licence-clean**, expressed as a `Parent Of` from the EPC class to the ingredient
   (2,608 links) — *not* routed through SNOMED/MeSH mappings as the plan had assumed. EPC was consequently
   brought **into** scope (with `APC` as its hierarchy ancestors), normalised to `has_EPC`.

Also corrected mid-flight: `has_TC` exists (it had been wrongly dropped) and there is no `has_EPC`
association type at all. Because assumptions about upstream shape proved unreliable, the fixture is
**extracted from the real release** by a committed script rather than written by hand.

## Follow-ups (see HANDOVER for the live list)

- Re-confirm the MED-RT licence deed against the live NLM source doc (HTTP 502 at design time; the
  distribution ships no licence file).
- Batch-commit + `iterparse` for production ingest — the parser currently holds the whole 45 MB XML in
  memory and the orchestrator writes in one transaction.
- Use the class-level `MED-RT → MED-RT` `has_*` assertions to let curated knowledge inherit along the DAG.
- Re-validate when the next MED-RT release rolls (regenerate the fixture, re-run the suite).
