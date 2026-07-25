# Licensing is a blocker, not a cleanup item

**Status:** Active
**Last reviewed:** 2026-07-25
**Applies to:** Every slice — code and every bundled data source
**Full derivation:** the project's coding rules (`CLAUDE.md` rule 6) and the per-slice licence gates in each design spec

## Context

An open commons is worthless if a bundled source's licence forbids redistribution,
commercial use, or derivatives. Discovering a licence problem *after* building on a
source is expensive and can poison everything downstream of it.

## Decision

All code is **AGPL-3.0**. Every dependency and every bundled reference-data source must
be **AGPL-3.0-compatible, checked *before* adding**. Licence-encumbered sources attach
only as **node-local, separately-licensed plug-ins** — never bundled into the commons.

## Consequences

- Sources are sequenced by **licence-cleanliness, not coverage**. Public-domain and
  openly licensed sources (UNII, ChEBI, MED-RT, MeSH, RxNorm) are bundled; encumbered
  ones (ATC, SNOMED/AMT, ICD-10-AM, commercial DrugBank…) are not.
- National and proprietary data stays usable **per node**, without contaminating the
  shared commons.
- **Cost:** some high-coverage sources are simply unavailable in the bundled commons
  tier — a deliberate trade of coverage for freedom.

## Related

- [Data sources & licensing](../sources/index.md)
- [Principles](../principles/index.md)
