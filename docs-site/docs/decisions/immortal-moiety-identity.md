# Immortal moiety identity via UUIDv5-on-UNII

**Status:** Active
**Last reviewed:** 2026-07-25
**Applies to:** Slice 1 — the active-moiety identity spine
**Full derivation:** [slice-1 moiety-spine design spec](https://github.com/cairn-ehr/drugref/blob/main/docs/superpowers/specs/2026-07-23-drugref-global-moiety-spine-design.md)

## Context

External drug identifiers churn: RxNorm concepts merge and split, and display names
differ by jurisdiction (acetaminophen vs paracetamol). Keying a moiety's identity on
any external identifier or on a name means identity breaks whenever the upstream source
changes — and independent installations would disagree about what "the same drug" is.

## Decision

Every active moiety gets its **own immortal `moiety_uuid`** — a `UUIDv5` minted
deterministically from the moiety's **UNII** at first sighting, then **pinned forever**.
Identity is never keyed on a name. Because the UUID is derived deterministically from a
public identifier, independent instances mint the same UUID for the same substance with
no central coordination.

## Consequences

- External identifiers can attach and change freely as append-only claims without ever
  re-keying the moiety.
- Independent drugref installations converge on the same identities offline.
- **Cost:** a change to the *UNII itself* is the one churn the UUID does not survive
  ([issue #3](https://github.com/cairn-ehr/drugref/issues/3) — a structural re-key by
  InChIKey is deferred). A row with a blank/absent UNII must be refused, or unrelated
  drugs collapse onto one shared UUID (closed in the foundation review).

## Related

- [Append-only claims](append-only-claims.md)
- [Principles](../principles/index.md)
