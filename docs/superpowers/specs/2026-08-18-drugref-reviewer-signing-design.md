# Drugref reviewer detached signing

**Date:** 2026-08-18
**Status:** implemented

## 1. Outcome

An authenticated reviewer can create an Ed25519 key in an encrypted device-local
vault, enrol only its public key, inspect the exact canonical payload for a current
curated revision, and explicitly unlock the vault to sign it. The service independently
rebuilds and verifies the detached signature before inserting the existing
`assertion_signature` row.

Recording clinical content and signing it remain separate actions. Closing the queue,
refreshing after a revision, or restarting the app does not strand sign-off: a
database-derived **Pending signatures** view lists current GUI revisions with no
signature and resumes the same two-step flow.

## 2. Trust boundary and key custody

The WebView receives signing status, public fingerprints, pending revision metadata,
and a confirmation preview containing every canonically rendered named field in its
frozen order. This is necessary human-readable content, not the encoded payload buffer.
It never receives the private key, vault path, snapshot key, bearer token, canonical
payload bytes, or an arbitrary native signing primitive.

The native Tauri core integrates IOTA Stronghold directly instead of registering the
generic Stronghold plugin command surface. Each reviewer UUID resolves to a fixed file
set below the application-local data directory. The encrypted snapshot contains the
private key; a mode-0600 salt and public fingerprint sidecar support key derivation and
matching the device vault to the authenticated reviewer's live enrolment. No client
path is accepted.

The signing-vault passphrase is distinct from the account password, validated at
12–1,024 characters, zeroized after use, and unrecoverable by the service. Argon2id
derives a 256-bit snapshot key from a random 32-byte salt using 19 MiB, two iterations,
and one lane. Stronghold's additional password work factor is set to zero because its
input is already a high-entropy derived key; snapshot encryption and authenticated
decryption remain Stronghold-owned.

## 3. Key enrolment contract

`GET /v1/signing-keys/current` returns the authenticated reviewer's enrolled registry
rows with their current `signing_key` status. `POST /v1/signing-keys/current` accepts
one lowercase-hex 32-byte public key. The service derives its SHA-256 fingerprint,
requires the registry row to be active, and appends `reviewer_key_enrolment` using the
authenticated stable reviewer identity. Repeating enrolment of the same key by the
same reviewer is idempotent; a key already enrolled to another reviewer is refused.

Key generation, reopening, public-key extraction, and snapshot commit happen natively.
Only the public key and fingerprint cross to the authenticated service.

## 4. Detached prepare-confirm-submit protocol

The WebView identifies one current revision by `(kind, targetKey, revisionId,
keyFingerprint)`. `GET /v1/review-signature` resolves that natural key, rejects a stale
or superseded revision, reads every frozen `/v1` field from PostgreSQL, adds the active
enrolled fingerprint and a server-issued microsecond UTC signing instant, and returns
named fields plus a SHA-256 digest.

The native core validates target, revision, fingerprint, target kind, payload context,
frozen field order, and digest. It retains the query and exact canonical bytes in
native memory and returns confirmation metadata plus a copy of every already-validated
named value. The GUI shows all values in canonical order, preserves complete narrative
text, and explicitly marks SQL NULL; a heading and digest alone are not sufficient
human review. On explicit confirmation native code requires the same query and digest,
unlocks the matching local vault, signs those retained bytes, and calls `POST
/v1/review-signature`.

The service then rechecks the active enrolment and current revision, constrains the
server-issued timestamp to a five-minute challenge window with 30 seconds of future
clock skew, rebuilds the canonical bytes, compares the digest, verifies Ed25519, and
appends the detached signature. Duplicate insertion is a conflict. A successful
response is freshly loaded decision history; a network failure retains the prepared
native payload for retry, while success or logout clears it.

The canonical encoder lives in `reviewer-domain` and is pinned byte-for-byte against
the repository's committed Python signing vectors. Field order is part of the signed
contract, not presentation metadata.

## 5. Resume view and deliberate limits

`GET /v1/pending-signatures` unions current interaction and condition revisions having
no detached signature. It returns the frozen target key, current revision, decision,
names, and stored reviewer attribution needed to resume sign-off. Selecting a row
loads complete decision history before preparing a signature; a verified row leaves
the list on refresh.

This slice adds no table shape. `db/046` only replaces `db/030`'s now-obsolete catalog
claim that no enrolment protocol exists: an authenticated active reviewer session can
register and enrol its device-generated public key, while possession of that session and
the local private key remain separate requirements. The slice does not export or recover
private keys, alter key status, create release-manifest signatures, or build profile
correction, account disablement, password rotation, or all-session revocation. Browser
preview uses isolated in-memory state and never becomes a native fallback.

## 6. Verification

The gate covers canonical reference vectors, exact-field confirmation projection and validation, service unit tests, a live
PostgreSQL enrol/prepare/sign/verify/persist/resume round trip, Stronghold persistence
and wrong-passphrase rejection across a native restart, Rust formatting and clippy,
Svelte diagnostics, production frontend and native no-bundle builds, npm audit, the
full Python/PostgreSQL suite, responsive browser interaction, and `git diff --check`.
