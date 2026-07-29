# A structural chemical tree is not a clinical class

**Status:** Active
**Last reviewed:** 2026-07-29
**Applies to:** Slice 5b — MeSH-keyed contraindications (`CI_ChemClass`)
**Full derivation:** the [slice-5b design spec](https://github.com/cairn-ehr/drugref/blob/main/docs/superpowers/specs/2026-07-28-drugref-slice-5b-mesh-contraindication-design.md) (§4.4, §7) and `db/014` / `db/016`

## Context

MED-RT's `CI_ChemClass` predicate says "do not co-administer this drug with *that*",
where *that* is a MeSH record. Measured against the real 2026.07.06 release, the object
is overwhelmingly an **individual substance** — Pimozide, Cisapride, Ritonavir — and
drugref resolves it to a moiety through the same UNII→CAS bridge slice 2b already uses.
That arm is exact, pairwise drug↔drug data and is ingested.

The remaining fifth of the assertions name a genuine **chemical class**: Sulfonamides,
Barbiturates, Macrolides, Penicillins. drugref already knows how to expand a rule down a
hierarchy — Plan B does exactly that for MED-RT's mechanism and physiological-effect
classes — so the obvious move is to expand these over MeSH's chemical tree too.

**That move is wrong, and measurably so.** MeSH's chemical tree is a *structural*
taxonomy: it groups molecules by what they are made of, not by what they do in a patient.
Expanding a rule on **Sulfonamides** (36 upstream rules) over it reaches 61 moieties,
including **bendroflumethiazide** and **bosentan** — the discredited sulfa
cross-reactivity inference, generated automatically and shipped as a safety assertion.
Nor can the gap be filled from MED-RT's own structural-class assertions: only **8.3%** of
these objects have any `has_SC` member.

## Decision

**The class arm is withheld, and withheld visibly.** Where the object of a
`CI_ChemClass` assertion resolves to a moiety, the pair is ingested into
`moiety_contraindication`. Where it does not — which is precisely how drugref detects
that the object is a class rather than a substance — the assertion is **not** ingested
and is instead **preserved as a curator question**: one row per object in
`ingest_unresolved_ci_object`, published through `gap_unresolved_ci_object` and the
open-question register, carrying how many upstream rules ride on it.

Withholding is the right call; withholding *silently* is not. A pharmacist rules on each
object by name, exactly as Plan B made a pharmacist rule on 14 expansion roots before
expanding over them.

## Consequences

- drugref ships **1,442** exact drug↔drug contraindication pairs — its first genuinely
  pairwise DDI content, where nothing expands because both endpoints are moieties.
- **405 upstream assertions over 103 objects** are deliberately not ingested. They are
  counted, named and queryable, not dropped.
- **Cost:** real upstream safety content is unavailable until a curator rules on it. That
  is the intended trade: a false contraindication a prescriber cannot audit is worse than
  a missing one that is on a published worklist.
- The **source-blind walk** stays latent as a result. Because no MeSH chemical class is
  registered in `substance_class`, and conditions live in their own tables with their own
  MeSH-only DAG, no rule from one authority yet expands over another's edges.

## Erratum — the spec's withheld arm is **103** objects, not 108

The slice-5b design spec records this arm as *405 assertions over **108** classes* (§4.4
and §7). **The object count is wrong; the assertion count is right.**

**The spec is the one artefact that cannot be corrected in place** — per-slice specs under
`docs/superpowers/specs/` are immutable by project rule — which is why this living record
exists. Everything else already reads **103**: the migrations, the ingest code, and the
measured tables.

The spec counted MeSH **ConceptUIs** — what MED-RT's `to_code` actually points at. A MeSH
*record* owns one or more concepts, so several concepts can name one record, and the
worklist is keyed on the **record**, because the decision a curator makes is per record:
*"should a contraindication naming this class expand over MeSH's structural tree?"* Asking
it twice about one class would be a split, not extra precision.

Exactly **five** records are each named by two withheld concepts — `D000701` (Analgesics,
Opioid), `D001569` (Benzodiazepines), `D006993` (Hypnotics and Sedatives), `D010406`
(whose two concepts are "Penicillins" and "Penicillin") and `D020902` (Hypericum) — so
108 concepts collapse to **103 curator questions**. Verified against the real releases:
`gap_unresolved_ci_object` returns **103 rows summing to 405 `ci_rule_count`**.

The **405** is unaffected, because an assertion is an assertion whichever concept names
its object.

Do not "fix" the code by keying the worklist on the concept: that is precisely the split
the record/concept distinction exists to prevent. The code itself already explains the
collapse, in `ingest/mesh_ci_run.py::_write_relations`.

## Related

- [The hybrid store](hybrid-store.md) — why an ingested projection is not the place for
  an inference drugref cannot defend.
- [Licensing is a blocker](licensing-is-a-blocker.md)
- [Roadmap](../roadmap/index.md)
