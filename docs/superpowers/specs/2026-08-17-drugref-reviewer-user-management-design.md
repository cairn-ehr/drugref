# Drugref reviewer accounts and first-run administration

**Date:** 2026-08-17
**Status:** implemented

## 1. Outcome

The reviewer application now has a real authentication boundary and an administrator
surface for creating users. Before the desktop core loads the review workspace, it asks
the review service whether any live profile has the `administrator` role. If none does,
the WebView can show only first-administrator registration. That registration creates
the stable account, initial administrator profile, Argon2id credential and session in
one PostgreSQL transaction, then continues into the application.

The clinical queue remains fixture-backed and read-only. A live account session does not
turn preview clinical controls into write paths and is not a clinical signature.

## 2. Trust boundary

The foundation's production boundary remains unchanged:

```text
Svelte WebView -> narrow Tauri commands -> Rust desktop core -> HTTPS review service
                                                          -> PostgreSQL
```

The WebView never receives a bearer token or a database credential. The desktop core
keeps the session token in native process memory and attaches it to service requests.
Release builds require an `https://` service URL; debug builds accept HTTP only on
loopback. The existing CSP still gives the WebView no network permission because HTTP
is owned by Rust.

## 3. Database model (`db/044`)

- `reviewer_account` holds the stable UUID and immutable lowercase username.
- `reviewer_profile` holds append-only name, qualifications, Markdown biography, role
  and active/disabled revisions, with exactly one live revision per account.
- `reviewer_password_credential` holds append-only Argon2id PHC strings with exactly
  one live credential. Plaintext and non-Argon2id values are not representable through
  the supported service path; the database also rejects a non-Argon2id prefix.
- `reviewer_key_enrolment` points into the existing `signing_key` registry. Ownership
  and withdrawal are append-only revisions, so a mistaken enrolment is correctable
  without deleting its history.
- `auth_session` is insert-only and stores a 32-byte SHA-256 token digest, never the
  bearer secret. `auth_session_revocation` is a separate insert-only fact for logout,
  administrative revocation and credential rotation.
- `reviewer_role_kind` is the database-owned `reviewer` / `administrator` vocabulary.

The migration deliberately seeds no user. An empty administrator set is valid and is
the only state in which bootstrap registration may succeed.

## 4. Service API

`reviewer-service/` is an Axum/SQLx service with these first endpoints:

- `GET /v1/bootstrap/status` — whether first-administrator registration is required;
- `POST /v1/bootstrap/admin` — concurrency-guarded creation of the first administrator;
- `POST /v1/sessions` — constant-shape password login and a 12-hour session;
- `POST /v1/sessions/current` — insert-only logout revocation;
- `GET /v1/users` — administrator-only account list;
- `POST /v1/users` — administrator-only account creation.

Bootstrap uses a PostgreSQL transaction advisory lock, rechecks the administrator set
under that lock, and forces the role to `administrator` regardless of the request body.
It remains closed even if an administrator is later disabled: a disabled account must
be recovered administratively, never by reopening an unauthenticated privilege grant.

Login verifies a real Argon2id sentinel for an absent username so the cheap path does
not disclose account existence. Errors have the same external wording for a missing
user, wrong password and disabled account. The service also applies a process-local
per-address attempt limit; production deployment must retain an edge rate limit too.

## 5. Desktop and GUI behavior

At startup the app has four pre-workspace states: checking, first-run registration,
sign-in and service-unavailable/retry. The workspace fixture is not requested until
registration or login succeeds. An administrator sees a **Reviewers** navigation item
with the live account list and a create-user form for identity, role, profile and
initial password. A reviewer cannot see the administration route, and the service
independently rejects a modified client that calls it.

Browser-only Vite preview retains a clearly labelled in-memory account adapter. Add
`?bootstrap` to its URL to inspect first-run registration. It writes no database and
starts with `maya.chen` / `preview` only as a browser demonstration.

## 6. Deliberate limits

This slice creates and lists users. Profile correction, disable/enable controls,
password rotation, administrator session revocation and signing-key enrolment UI are
later administration work over the history-preserving schema already provided here.
The live paginated queue API remains the next slice before any clinical write path.

## 7. Verification

The implementation gate includes database schema/trigger tests, shared-domain and
service unit tests, Tauri core tests, Svelte diagnostics, production frontend build,
npm advisory audit, full Python/PostgreSQL regression suite, and a native Tauri build.
An end-to-end local service check must observe `bootstrapRequired: true`, create exactly
one administrator, observe a second bootstrap attempt return conflict, log in with the
new credential, and create/list a reviewer through an administrator session.
