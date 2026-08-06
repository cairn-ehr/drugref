# The hybrid store: rebuildable projections vs the signable overlay

**Status:** Active
**Last reviewed:** 2026-08-06
**Applies to:** The whole architecture (slices 2–5)
**Full derivation:** [the design specs](https://github.com/cairn-ehr/drugref/tree/main/docs/superpowers/specs) and `docs/ROADMAP.md`

## Context

drugref holds two kinds of data with opposite update semantics: machine-ingested public
feeds that are regenerated wholesale each upstream release, and hand-curated knowledge
that is the durable, high-value part and must never be lost.

## Decision

Store them separately:

- **Rebuildable projections** for ingested feeds — drop-and-rebuild per source,
  version-pinned, provenance-tagged via `ingest_run`.
- **An append-only, signable overlay** for curated knowledge — *signable*, not signed: no
  signing infrastructure exists yet (no key management, no signing identity, no verification
  path), so the word here used to overstate what the schema provides. See
  [curating a drug–condition pair](curating-a-drug-condition-pair.md) §3 for why that gap
  is what shapes slice 5c.1 shipping empty.

The overlay attaches to nodes in **either** the composition tree or the classification
DAG and **inherits along the edges**, so knowledge is curated once and applies widely.

## Consequences

- A new upstream release cleanly replaces its projection without touching curation.
- Curation entered once propagates along the graph — the central curation-economy lever,
  especially for class-level interactions.
- **Cost:** the two halves must stay clearly separated, and every per-source rebuild must
  key on `ingest_run.source` so one source's rebuild never deletes another's rows.

## Related

- [Append-only claims](append-only-claims.md)
- [Curating a drug–condition pair](curating-a-drug-condition-pair.md) — the signable-not-signed
  argument, and why the overlay's first content-bearing slice ships with zero curated rows.
- [Architecture](../architecture/index.md)
- [Principles](../principles/index.md)
