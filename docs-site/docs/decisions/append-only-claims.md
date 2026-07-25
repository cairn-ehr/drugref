# Append-only external-identifier claims

**Status:** Active
**Last reviewed:** 2026-07-25
**Applies to:** Slice 1 + the foundation review (`db/005`)
**Full derivation:** [slice-1 moiety-spine design spec](https://github.com/cairn-ehr/drugref/blob/main/docs/superpowers/specs/2026-07-23-drugref-global-moiety-spine-design.md)

## Context

Reference data is corrected over time. A destructive `UPDATE` or `DELETE` loses
provenance and audit history, and can silently change what a downstream consumer saw
yesterday. drugref doubles as an identifier cross-walk, so its history must be stable
and inspectable.

## Decision

External-identifier claims (UNII, INN, RXNORM_IN, CAS, PUBCHEM_CID, INCHIKEY, ChEBI…)
are **append-only**. A correction **inserts a new claim** and points the old claim's
`superseded_by` at it; values are never overwritten or deleted. Database triggers
enforce this floor. Uniqueness is **partial over live claims only**, so a value that was
reverted upstream can be re-asserted later.

## Consequences

- Full, auditable history; consumers can always see what was claimed and when.
- Supersession is **one-way, same-moiety, and strictly forward** — cycles are
  unrepresentable ([issue #4](https://github.com/cairn-ehr/drugref/issues/4), closed in
  `db/005`).
- **Cost:** every read must filter `superseded_by IS NULL`. `TRUNCATE` and the
  table-owning role remain bypasses of the row-level floor
  ([issue #2](https://github.com/cairn-ehr/drugref/issues/2) — RLS + privilege
  separation later).

## Related

- [Immortal moiety identity](immortal-moiety-identity.md)
- [The hybrid store](hybrid-store.md)
- [Principles](../principles/index.md)
