# GSRS relationship direction runs target → record

**Status:** Active
**Last reviewed:** 2026-08-05
**Applies to:** Slice 3 — the composition tree (`substance_composition`, `db/028`)
**Full derivation:** the [slice-3 design spec](https://github.com/cairn-ehr/drugref/blob/main/docs/superpowers/specs/2026-08-05-drugref-slice-3-composition-tree-design.md) (§3.2) and `src/drugref/ingest/gsrs.py`

## Context

GSRS stores a relationship of type `A->B` on a record `X` pointing at a record `Y`,
and **`X` plays role `B` while `Y` plays role `A`**. The stored relationship is the
*inbound* edge, not the outbound one.

This is not documented upstream, and reading the type left-to-right produces a
plausible, fully-populated, entirely wrong graph. In the 2026-02-26 public dump the
naive reading yields a single "salt" with **124 parent bases**. Under the correct
reading the same data says Maleic Acid is the parent of **124 salts** — which is
what a common counterion should look like. Tartaric Acid follows with 123 and
citric acid with 117.

**Nothing about the wrongness is visible in an aggregate count.** The table is the
same size either way; only the direction of every edge in it is reversed. That is
why this record exists, and why the convention is pinned by tests rather than by
prose alone.

Two independent checks pin the convention, and both are kept as tests:

- **Mirror agreement.** Most edges are stored from both ends. Normalised under this
  convention the two encodings agree on **15,039** edges (of 15,109 and 15,150).
  Inverted, they would agree on essentially none.
- **Functional cardinality.** Every solvate has exactly **one** anhydrous parent.
  Inverted, the relation is many-to-many and meaningless.

The 70 edges stored only as `SALT/SOLVATE->PARENT` and the 111 stored only as
`PARENT->SALT/SOLVATE` are upstream asymmetry: counted, not repaired.

This is the same class of erratum as MED-RT's `Parent Of`, which runs parent →
child rather than the reverse.

## Decision

**The convention lives in exactly one pure function**, `normalise_relationship` in
`src/drugref/ingest/gsrs.py`, expressed as a four-entry table rather than as four
if-branches. Every edge in the projection passes through it, so the direction is
stated once and cannot drift between call sites.

`ACTIVE MOIETY` is deliberately **not** an edge in this graph. It is the ion level:
71% of its 33,647 edges are self-references, and every magnesium form — including
drugref's own moiety — points at `MAGNESIUM CATION`. Treated as an equivalence join
it would assert that levomefolate magnesium is interchangeable with magnesium
sulfate. It reaches the projection **only** as `is_active_component`, a
discriminator *inside* a composition.

## A second finding, recorded with it

The **public GSRS API strips `relationships` entirely**. A record whose `ACTIVE
MOIETY` edge is present in the dump returns zero relationships from
`/api/v1/substances/{uuid}`. Any tool reading GSRS relationships must use the dump;
the API is not a fallback for it.

## Consequences — what the assembled pipeline measured

Slice 3 ran end to end against the real releases (UNII 26Feb2026 → MED-RT
2026.07.06 → MeSH 2026 → GSRS 2026-02-26) on 2026-08-05:

| quantity | measured |
|---|---:|
| `substance_composition` rows | **8,671** (7,962 `SALT_SOLVATE` + 709 `SOLVATE_ANHYDROUS`) |
| composites | **7,377** (4,425 of them are *not* drugref moieties) |
| component moieties | **4,433** (22.8% of the registry gain ≥1 child) |
| `is_active_component` TRUE | **5,011** |
| `is_active_component` FALSE | **992** |
| `is_active_component` NULL (unruled) | **2,668** |
| gap kind 12 (`gap_unruled_composition_activity`) | **2,245** composites |

**The pre-existing figures did not move**, which is the regression signal that
matters: `ddi_candidate_pair` **21,664** and `substance_moiety` **19,438** are
byte-identical to the pre-slice run, and `open_question` grew by exactly the 2,245
new gap-kind-12 rows (18,834 → **21,079**). Slice 3 wires nothing into the
contraindication hot path.

## Erratum — the activity split was predicted from the wrong scope

The design measurements predicted **TRUE 5,029 / FALSE 1,001 / NULL 2,641** and
**2,226** gap-kind-12 composites. The assembled pipeline measured **5,011 / 992 /
2,668** and **2,245**. The *edge set* is identical — 8,671 rows, 7,377 composites,
4,433 components, all exactly as predicted — so nothing about the direction
convention or the parser is in question. Only the activity split moved.

**The cause is a scope difference, reproduced exactly.** The prediction scripts built
a global `unii → active moieties` map and looked up the composite for every edge.
The shipped orchestrator only lets a ruling come from the composite's **own record**,
because that is the record the mirror-merge is keyed on. The two readings differ on
precisely the **27** in-registry edges that GSRS stores *only* on the component's
record: 18 the global map would call TRUE and 9 it would call FALSE are recorded
NULL instead, which leaves 19 further composites with no ruling at all.

Re-running both readings over the dump reproduces both figures to the row, so this
is a difference of method and not a release change. It also runs one way only: the
global reading never downgrades a ruling to NULL, it only adds rulings. The shipped
behaviour is therefore the **conservative** one — it under-claims activity and
over-reports the gap — which is the safe direction for a projection whose NULL means
"nobody ruled". Whether the composite's own `ACTIVE MOIETY` declaration ought to rule
on an edge that arrived from the other end is a real question, deliberately left open
rather than changed during a verification run; it is worth an issue of its own.

**Published figures are the measured ones.** The predicted split is recorded here
only so a reader who finds it in the design spec knows it was superseded, and by
what. Per-slice specs under `docs/superpowers/specs/` are immutable once merged,
which is why the correction lives in this record.

## What this slice does not do

It does **not** resolve
[issue 33](https://github.com/cairn-ehr/drugref/issues/33) or
[issue 30](https://github.com/cairn-ehr/drugref/issues/30), and any roadmap
annotation saying otherwise is withdrawn. Issue 33's own proposed fix was refuted by
the release: **nothing in GSRS points at `DE08037SAB`** — 0 inbound references across
173,080 records. A composition hop recovers **94 of 706** unmatched MeSH UNII keys and
**68 of 1,977** CAS keys, and the magnesium flagship is not among them. Issue 30's
yield is unmeasured here, because the verification database carries no PBS release.

## Related

- [A structural chemical tree is not a clinical class](withheld-chemical-class-contraindications.md)
  — the same refusal to turn a structural relationship into a clinical inference.
- [Immortal moiety identity](immortal-moiety-identity.md) — why the composite side of
  a composition row is a bare UNII and not a second registry.
- [Roadmap](../roadmap/index.md)
