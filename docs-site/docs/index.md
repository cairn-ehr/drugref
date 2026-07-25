# drugref.org — an open drug-information commons

drugref.org is a public-good drug-information service: a registry of active drug
**moieties**, each with an **immortal identity** and transparent, append-only links
to the world's open drug vocabularies. Any EHR, pharmacy system, prescribing tool,
or research pipeline can consume it on equal footing — free software, free data,
reproducible from source.

!!! info "Status — under active development"
    The global moiety spine, classification (MED-RT + MeSH) and the first
    interaction data are built; the public API is still to come. Follow progress
    [on GitHub](https://github.com/cairn-ehr/drugref).

## Why it exists

Reliable drug reference data — what a substance *is*, how it is classified, what it
interacts with — sits behind proprietary licences in most of the world. That locks
safe-prescribing support to whoever can pay, and locks health-IT projects into
vendors. drugref takes the opposite stance: the **identity and knowledge layer of
medicines is a commons**, built entirely from public-domain and openly licensed
sources, and published under licences that keep it open for everyone, forever.

## What it provides

- **Immortal substance identity** — every active moiety gets a stable UUID that never
  changes and is never reused, minted deterministically so independent installations
  agree without coordination.
- **Cross-vocabulary claims** — append-only, auditable links from each moiety to UNII,
  InChIKey, CAS, ChEBI, RxNorm and more. Corrections supersede; they never overwrite
  history.
- **Classification** — a source-neutral drug-class registry populated from MED-RT
  (mechanism of action, physiologic effect) and MeSH pharmacologic actions.
- **Interaction groundwork** — class-level contraindication data from MED-RT, expanded
  to candidate drug–drug pairs; the advisory foundation for interaction checking,
  clearly tiered by evidence.

## Two tiers

drugref is built in two tiers: the **global tier** (jurisdiction-independent substance
identity, chemistry, classes, interactions — being built first, in this repository)
and a later **local tier** for country-specific packaging, product, and subsidy data.

## How to read these docs

- [Architecture](architecture/index.md) — how drugref is built.
- [Design decisions](decisions/index.md) — why it is built that way (the decisions that
  currently stand).
- [Principles](principles/index.md) — the invariants everything else follows from.
- [Data sources & licensing](sources/index.md) — where the data comes from and the
  licence rules that govern it.
- [Roadmap](roadmap/index.md) — what is done and what is next.
