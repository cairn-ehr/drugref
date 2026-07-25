# Principles

The invariants everything else in drugref follows from. The [design
decisions](../decisions/index.md) are specific choices; these are the commitments those
choices serve.

## The identity & knowledge layer of medicines is a commons

Built entirely from public-domain and openly licensed sources, published under licences
that keep it open forever. The value drugref adds is **quality control — who may assert —
not access**: the data ships paywall-free under copyleft.

## Advisory, never authoritative

drugref is **advisory reference data**. It informs clinical software; no system is ever
required to route its records through it, and it never sits on Cairn's signed inter-node
wire core. A licence-encumbered source can therefore attach to one node without
compromising interoperability.

## The steward uses only the public API

drugref is stewarded alongside the [Cairn EHR](https://cairn-ehr.org) project, but Cairn
is just its first client, on the **same public-API footing as everyone else**. The
steward has no privileged access.

## Immortal identity, never keyed on a name

Every moiety owns an immortal UUID minted deterministically and pinned forever. Identity
is never derived from a display name. See [immortal moiety
identity](../decisions/immortal-moiety-identity.md).

## Append-only history — corrections supersede, never overwrite

Claims are append-only; a correction supersedes its predecessor. History is stable and
auditable. See [append-only claims](../decisions/append-only-claims.md).

## Licensing is a blocker, not a cleanup item

Every dependency and bundled source is checked for AGPL-compatibility **before** it is
added. See [licensing is a blocker](../decisions/licensing-is-a-blocker.md).

## Integrity is enforced in the database, not application code

Ingest is fit-for-purpose Python, but identity and append-only integrity live in
PostgreSQL constraints and triggers — the guarantees hold no matter what writes to the
database.

## Reproducible from source

Every fact traces to an openly licensed upstream release, and the entire database can be
rebuilt from those sources by anyone.

## Coverage gaps are published, not hidden

Where drugref lacks data — an unclassified moiety, a contraindication naming a class no
drug is filed under — the gap is published as a queryable open-question register that
shrinks visibly as coverage improves, rather than being silently absent.
