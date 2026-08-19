# Architecture

drugref is an **advisory, fit-for-purpose** service: ingest and normalisation are
written in plain Python (fast iteration over brittle upstream feeds), but **data
integrity is enforced in the database, not in application code** — constraints,
triggers, and (later) row-level security. The substrate is Python 3.12 + `uv`,
`psycopg` v3, and PostgreSQL ≥ 18.

## Two orthogonal structures

drugref models medicines as two independent graphs that a moiety sits in at once:

```text
Composition tree — is made of, downward

Active moiety ──> Specific substance ──> Clinical drug ──> Product
                  salt/ester/hydrate     strength + form    local tier

Classification DAG — is a kind of

Class ──> Class
  ^
  └──── an active moiety can be a member of many classes
```

1. **Composition tree** (*is-made-of*): active moiety → specific substance → clinical
   drug → product. Product is the local tier.
2. **Classification DAG** (*is-a-kind-of*): `class ⊂ class`; a moiety belongs to many
   classes across several axes (chemical / mechanism / therapeutic) — many-to-many, a
   link, never a parent foreign key.

Curated knowledge attaches to nodes in **either** structure and **inherits along the
edges** (down the tree, up through a moiety's classes) — curate once, apply widely.

## The hybrid store

Two kinds of data with opposite update semantics live side by side:

- **Rebuildable projections** — everything ingested from a public feed. Drop-and-rebuild
  per source, version-pinned, provenance-tagged via `ingest_run`. A new upstream release
  cleanly replaces its own projection.
- **An append-only, signed overlay** — curated knowledge (the durable value-add, e.g.
  interaction severity and management). Never overwritten; corrections supersede.
  Signatures are **detached**, in their own insert-only table rather than a column, so a
  row can be signed at any time — including long after it was written — and can carry
  more than one signature when a second reviewer counter-signs.

See the decision records on the [hybrid store](../decisions/hybrid-store.md) and
[signing the curated overlay](../decisions/signing-the-curated-overlay.md).

## The clinical-review boundary

The reviewer is a desktop client, not a database administration tool. Privileged
operations cross four explicit boundaries:

```text
Svelte WebView
    │ typed Tauri commands
    ▼
Rust desktop core ───── local encryption ─────> Stronghold private key
    │ HTTPS + bearer session
    ▼
Rust reviewer service
    │ authorised transactions; public keys and detached signatures only
    ▼
PostgreSQL
```

- **Svelte** renders typed queue, history and confirmation data. It never receives a
  bearer token, database credential, private key or raw canonical signing bytes.
- **The Tauri core** owns sessions, device-local Stronghold keys, canonical payload
  construction and local Ed25519 signing.
- **The reviewer service** authenticates and authorises requests, independently
  rebuilds every submitted signing payload and owns the append-then-supersede
  transactions.
- **PostgreSQL** enforces append-only account, clinical revision, key-status and
  signature history. Ingested candidates remain distinct from curated judgements.

Authentication and clinical attestation are deliberately separate. A valid session
authorises an action now; a detached signature proves that an enrolled private key
attested to exact content. Neither substitutes for the other.

See the [reviewer manual](../user-manual/index.md) for the human workflow and the
[reviewer foundation design](https://github.com/cairn-ehr/drugref/blob/main/docs/superpowers/specs/2026-08-17-drugref-reviewer-gui-foundation-design.md)
for the full threat-boundary derivation.

## Immortal identity & append-only claims

Every active moiety has its own **immortal `moiety_uuid`** — a `UUIDv5` derived
deterministically from its UNII and pinned forever. External identifiers attach as
**append-only claims**; corrections *supersede* rather than overwrite. Two decision
records cover this: [immortal moiety identity](../decisions/immortal-moiety-identity.md)
and [append-only claims](../decisions/append-only-claims.md).

## Classification & membership

Drug classes come from **MED-RT** (mechanism of action, physiologic effect, therapeutic
class, and more) and **MeSH pharmacologic actions**, in a single source-neutral class
registry. A moiety joins a class through identifier claims drugref already holds
(`RXNORM_IN` for MED-RT; UNII / CAS for MeSH). Class edges are **rebuildable
projections**, deliberately outside the append-only floor.

## Where the parts live

Ingest parsers are **pure and streaming** (no database access); orchestrators
(`ingest/*_run.py`) own the transaction and are the only feed writers. Operator code
lives under `src/drugref/`; ordered invariants under `db/`; shared reviewer API types
under `reviewer-domain/`; the service under `reviewer-service/`; and the native client
under `reviewer-app/`. For the full derivation of any slice, see the design specs in
the [repository](https://github.com/cairn-ehr/drugref/tree/main/docs/superpowers/specs).
