# Drugref reviewer signing-key replacement

**Date:** 2026-08-18
**Status:** implemented

## 1. Outcome

An authenticated reviewer who has lost a device-vault passphrase can replace that
device key without recovering or exporting private material. Replacement withdraws
the reviewer's current enrolment, records the registry key as rotated, deletes the
three fixed local vault files, and returns the number of existing detached signatures
preserved by the operation.

An unused key follows the same audited path with a preserved-signature count of zero.
It causes no clinical re-review and the reviewer can immediately create a new local
key and passphrase.

## 2. Append-only registry correction

`signing_key` and `reviewer_key_enrolment` deliberately forbid hard deletion. The
authenticated `DELETE /v1/signing-keys/current` operation therefore performs the safe
resource-level equivalent in one PostgreSQL transaction:

1. lock the fingerprint;
2. require a live active key enrolled to the authenticated reviewer;
3. count every `assertion_signature` naming the fingerprint;
4. insert a `rotated` registry correction and supersede the active row; and
5. insert an unenrolled account correction and supersede the live enrolment.

The service takes the holder, algorithm, and public bytes from the current registry
row rather than from the client. A request from another reviewer is refused. Repeating
an already-completed replacement returns the preserved-signature count so native file
cleanup can be retried safely.

Rotation is time-scoped by the existing signing vocabulary. Signatures made before
the rotation remain valid; a prepared but not submitted challenge is refused because
submission rechecks the active enrolment.

## 3. Device deletion boundary

The WebView supplies neither a fingerprint nor a filesystem path. The native Tauri
command resolves both from the authenticated reviewer UUID and the existing public
fingerprint sidecar. It commits the service-side rotation first, then deletes only the
fixed `.hold`, `.salt`, and `.fingerprint` files. The fingerprint is deleted last so a
retry can identify a service operation that committed before a local filesystem
failure. Pending in-memory signature bytes are cleared.

No passphrase is required: replacement is specifically the recovery path when that
passphrase is lost. The UI uses an explicit second confirmation, distinguishes an
unused key from one with preserved signatures, and never describes the append-only
registry history as physically erased.

## 4. Verification

The gate covers fingerprint validation, authenticated ownership, unused-key
replacement, idempotent retry, signed-key rotation with preserved signature count,
fixed-path native deletion with unrelated-file retention, Stronghold reopen tests,
Svelte diagnostics, Rust formatting and linting, production builds, and the full
repository suite.
