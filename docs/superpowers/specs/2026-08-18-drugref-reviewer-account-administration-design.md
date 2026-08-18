# Drugref reviewer account administration

**Date:** 2026-08-18
**Status:** implemented

## 1. Outcome

The administrator surface completes the account lifecycle already represented by
`db/044`: correct a reviewer's append-only profile, disable or re-enable access,
rotate a password, and revoke every live session. Stable usernames remain immutable.
No migration is required.

Account administration changes authentication and authorisation facts only. It does
not alter clinical records, signatures, signing-key history, or the device-local
private-key vault.

## 2. Service authority and concurrency

All account mutations require a currently active administrator at the authenticated
service boundary. The service serialises administration mutations with a PostgreSQL
transaction advisory lock and rechecks that authority inside the transaction.

Profile forms carry `expectedProfileRevisionId`. A stale form receives `409 Conflict`
instead of superseding a correction recorded by another administrator. The service
refuses to disable or demote the last active administrator. Bootstrap remains closed
when an administrator profile exists, even if that profile is disabled, so this guard
prevents an unrecoverable state rather than reopening unauthenticated registration.

## 3. Append-only transactions

Profile correction inserts a complete replacement `reviewer_profile` row attributed
to the acting administrator and then points the predecessor at it. It never updates
profile content in place. Disabling an account also appends administrative revocations
for all of that account's unexpired, unrevoked sessions in the same transaction.

Password rotation hashes the new password before opening the persistence transaction,
inserts a new `reviewer_password_credential`, supersedes the prior credential, and
appends `credential_rotation` revocations for every live session. A separately
confirmed session action appends `administrative` revocations without changing the
profile or password. Re-enabling an account does not revive any revoked token.

The account projection includes the current profile revision identifier and current
live-session count so the GUI can expose stale-write protection and show the effect of
revocation without inferring either from a successful response.

## 4. Desktop and WebView boundary

The Tauri core adds only typed commands for the three account-administration actions.
It continues to retain the bearer token in native memory and attaches it to service
requests. Raw passwords cross the WebView boundary only in the narrow password-rotation
command and are sent to the service for immediate Argon2id hashing; they are never
stored by the WebView, native core, or database.

The browser preview mirrors the actions in isolated memory for layout and interaction
testing. It is not a native fallback and writes no external state.

## 5. GUI behavior

Selecting a reviewer opens an edit panel with the complete current profile and status.
Profile correction is explicit and preserves the immutable username. Disabling access,
rotating a password, and revoking sessions each require a separate confirmation;
password fields are cleared after every attempt.

If an administrator changes their own profile, the application shell immediately uses
the returned projection. Self-demotion returns them to the review queue. Self-disable,
self-password rotation, or self-session revocation clears the native session and returns
to sign-in because the service has invalidated that token.

## 6. Deliberate limits

This round does not delete accounts, rename usernames, expose password hashes or session
tokens, administer another reviewer's signing keys, or add password-reset email/recovery.
Retired or compromised signing keys remain a separate trust-administration round; the
existing owned-device lost-passphrase replacement remains unchanged.

## 7. Verification

Verification covers request validation, append-only profile and credential history,
stale-profile conflicts, last-active-administrator protection, disable/re-enable login,
password rotation, reason-specific session revocation, administrator-only enforcement,
native command compilation, Svelte diagnostics/build, responsive administrator flows,
and the existing PostgreSQL and signing regression suites.
