# Drugref reviewer signing-key trust administration

**Date:** 2026-08-19
**Status:** implemented

## 1. Outcome

An authenticated administrator can inspect every current public signing key, its stable
reviewer enrolment, status history projection, signature count and current re-review
impact. After explicit confirmation the administrator can append either a time-scoped
`retired` correction or a blanket `compromised` correction. The private half remains
device-local and no administrative action accepts a filesystem path or key material.

The existing owned-device lost-passphrase workflow remains separate: it records
`rotated`, withdraws only the authenticated reviewer's enrolment and deletes only that
device's fixed vault files. General trust administration never deletes local files.
If an administrator acted first, the owned device can still complete fixed-file cleanup
idempotently while preserving and reporting the registry's actual retired or compromised
status rather than relabelling it as rotation.

## 2. Append-only status and authority

Key status changes use the existing insert-then-supersede transaction under a
fingerprint advisory lock. The service rechecks active administrator authority inside
that same transaction, copies immutable public material from the current registry row,
and withdraws a live reviewer enrolment. Allowed GUI transitions are active to retired
or compromised, and retired to compromised. Compromise cannot be downgraded, even if a
later live row has another label, because the existing verdict rule reads the key's
whole history.

Migration `db/047` floors `signing_key_status_kind` against UPDATE and DELETE while
leaving INSERT available for a future status. It deliberately does not floor
`signature_target_kind`, whose payload context is designed to migrate to later versions.

## 3. Re-review and counter-signing policy

The pending-signature queue is defined by registry objection, not row count. A current
curated revision is pending when `curated_signature_status.unobjected_count` is zero:

- no signature rows is `unsigned`;
- one or more rows, but none from a currently unobjected registry key, is
  `needs_counter_signature`;
- one unobjected signature is sufficient to leave the queue, even if another signature
  is compromised, retired after signing, expired, or from an unknown key.

This is registry policy only. PostgreSQL does not verify Ed25519 mathematics; the
existing service still verifies every new signature before insertion, and operators use
`drugref verify` for historical cryptographic verdicts. Retirement preserves signatures
made before its boundary. Compromise objects to every signature from that fingerprint
and surfaces affected current revisions for counter-signing without withdrawing any
clinical row from consumer views.

## 4. Trust boundaries and deliberate limits

The WebView receives public fingerprints, reviewer identity, status timestamps and
aggregate counts. Bearer tokens remain native, private keys remain in Stronghold, and
canonical payload bytes remain native during sign-off. This round does not add key
recovery/export, release-manifest signing, approval quorums, automatic clinical
re-review, or issue #86's `signed_by_unknown_key` vocabulary widening.

## 5. Verification

The gate covers request-vocabulary validation, append-only status metadata, transition
and authority checks, retirement versus compromise semantics, counter-sign queue
entry/exit, native command boundaries, responsive administrator confirmation, Rust and
Svelte checks, the full PostgreSQL-backed suite, and production builds.
