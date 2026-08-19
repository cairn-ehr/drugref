# drugref

drugref.org is an open, vendor-independent drug-information service: a co-equal
public good that any EHR, pharmacy system or app can consume, not a component bundled
inside any one product.

This repository contains the global drug-knowledge tier and its clinical review
system. It currently includes:

- an immortal active-moiety identity spine with append-only external-identifier
  claims;
- reproducible projections from public-domain and open sources including UNII/GSRS,
  ChEBI, MED-RT, MeSH and RxNorm;
- candidate interaction, contraindication and indication knowledge with published
  coverage gaps;
- an append-only curated overlay with detached Ed25519 signatures; and
- a Tauri/Svelte desktop reviewer, an authenticated Rust service and PostgreSQL-owned
  account, revision and signing integrity.

The public documentation at [docs.drugref.org](https://docs.drugref.org/) explains the
[architecture](https://docs.drugref.org/architecture/), [standing design
decisions](https://docs.drugref.org/decisions/) and [reviewer
workflow](https://docs.drugref.org/user-manual/). The detailed build sequence lives in
[`docs/ROADMAP.md`](docs/ROADMAP.md), while accepted design derivations remain under
[`docs/superpowers/specs/`](docs/superpowers/specs/).

The main implementation areas are:

| path | purpose |
|---|---|
| `src/drugref/` | Python ingest, curation, signing and operator CLI |
| `db/` | ordered PostgreSQL migrations and database-enforced invariants |
| `reviewer-domain/` | shared typed Rust API and validation boundary |
| `reviewer-service/` | authenticated Axum/SQLx service for clinical review |
| `reviewer-app/` | Tauri 2 + Svelte desktop reviewer |
| `docs-site/` | public MkDocs documentation |

Repository-wide coding and documentation requirements are defined in
[`CONTRIBUTING.md`](CONTRIBUTING.md). Development setup for each component is documented
in its own README and in the public [developer
guide](https://docs.drugref.org/developer/).
