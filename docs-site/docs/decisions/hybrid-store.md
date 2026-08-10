# The hybrid store: rebuildable projections vs the signed overlay

**Status:** Active
**Last reviewed:** 2026-08-10
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
- **An append-only, SIGNED overlay** for curated knowledge. This record read *signable*,
  not signed — "no signing infrastructure exists yet" — from 2026-08-06 until slice 5c.4
  built it (`db/030`): a key registry, curator-held Ed25519 keys over one row's canonical
  payload, an institutional key over a per-release content manifest, and `drugref verify`
  as the verification path. See
  [signing the curated overlay](signing-the-curated-overlay.md) for the two layers, and
  [curating a drug–condition pair](curating-a-drug-condition-pair.md) §3 for why the gap
  is what shaped slice 5c.1 shipping empty — the ordering that made 5c.4 land before the
  first curated row was written.

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
- [Signing the curated overlay](signing-the-curated-overlay.md) — what closed the gap this
  record used to describe: the two layers, the revocation model, and what signing does *not* fix.
- [Curating a drug–condition pair](curating-a-drug-condition-pair.md) — the signable-not-signed
  argument, and why the overlay's first content-bearing slice ships with zero curated rows.
- [Architecture](../architecture/index.md)
- [Principles](../principles/index.md)
