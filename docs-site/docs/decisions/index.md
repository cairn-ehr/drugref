# Design decisions

These records capture the design decisions that **currently stand** behind drugref —
what was chosen, and why.

They are shaped like ADRs (Architecture Decision Records) but are deliberately
**living, not immutable**:

- Each record describes a decision *as it stands today*.
- When a decision changes, its record is **revised in place** — not appended to.
- A decision that is **reversed is removed**, not kept as a tombstone.

There is therefore no "superseded by" chain and no status graveyard here. The full
history is never lost — it remains in the git log and in the dated design specs under
`docs/superpowers/specs/`. This section always reflects *current* truth.

## Record template

```text
# <Decision title>

**Status:** Active            (or "Under review")
**Last reviewed:** YYYY-MM-DD
**Applies to:** <slice / subsystem>
**Full derivation:** <link to the relevant design spec>

## Context      — the forces / the problem this decision answers
## Decision     — what stands today
## Consequences — trade-offs, what it enables, what it costs
## Related      — other decision records, principles, code
```

## Current decisions

- [Immortal moiety identity](immortal-moiety-identity.md) — every moiety gets its own
  UUID, never keyed on a name.
- [Append-only claims](append-only-claims.md) — corrections supersede; history is never
  overwritten.
- [The hybrid store](hybrid-store.md) — rebuildable projections beside an append-only,
  signed overlay.
- [Licensing is a blocker](licensing-is-a-blocker.md) — AGPL-compatible sources only,
  checked before adding.
- [A structural chemical tree is not a clinical class](withheld-chemical-class-contraindications.md)
  — why `CI_ChemClass`'s class arm is withheld as a curator question rather than expanded
  (carries the 103-vs-108 erratum to the slice-5b spec).
- [An indication does not expand down the disease tree](indications-do-not-expand.md)
  — why therapeutic rules are stored unexpanded and generalised *upward* at read time,
  labelled (carries the pre-gate erratum to the slice-5b.2 spec).
- [A curated correction needs a deferred check, not a unique index](correcting-a-curated-assertion.md)
  — how single-live is enforced on an append-only curated table, and why retirement needs
  an explicit value rather than a supersession (carries the §5.0 erratum to the
  additive-effect spec).
- [The expansion policy is append-only, and `withdrawn` is a decision](expansion-policy-is-append-only.md)
  — why the table that gates contraindication recall stopped being edited in place, and what a
  reader of it must do differently (carries the standing correction to `db/010`'s storage-tier prose).
- [GSRS relationship direction runs target → record](gsrs-relationship-direction.md)
  — the undocumented upstream convention that makes a naive read of the composition graph
  fully populated and entirely reversed, and why `ACTIVE MOIETY` is a discriminator rather
  than an edge (carries the activity-split erratum to the slice-3 spec).
- [Curating a drug–condition pair](curating-a-drug-condition-pair.md) — why the drug–drug
  overlay keys on the RULE while the drug–condition overlay keys on the PAIR without
  `relationship`, why a curated row references its candidate by natural key and never by
  foreign key, and why the tier's first content-bearing slice shipped empty (§3, now
  delivered by the record below).
- [Signing the curated overlay](signing-the-curated-overlay.md) — the two layers (curator
  non-repudiation and release integrity), why curator-held keys are the only ones that prove
  anything, why a compromised key invalidates blanket while a rotated one does not, why a
  signature is never an admission gate — and, plainly, what signing does **not** fix.
- [The ONC high-priority floor is facts, not text](the-onc-high-priority-floor.md) — why the
  ONC high-priority drug–drug interaction list clears rule 6 on the facts-are-not-copyrightable
  argument (Feist) rather than on its public-funding history alone, and the re-encoding
  discipline — no verbatim paper text, every `mechanism`/`management` string drugref's own —
  that keeps the argument true.
