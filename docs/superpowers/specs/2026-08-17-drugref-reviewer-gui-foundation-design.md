# Drugref reviewer GUI foundation

**Date:** 2026-08-17
**Status:** accepted for the first vertical slice

## 1. Purpose

Drugref needs a human review surface before it needs a general public drug browser.
The first user is a clinician inspecting candidate facts and gaps, recording Drugref's
judgement, attaching evidence and explanatory notes, and signing the exact assertion
they reviewed.

The application must preserve the database's existing clinical-governance model:

- ingested assertions remain reproducible source projections;
- curated assertions are append-only;
- a correction inserts a replacement and supersedes the prior assertion;
- signatures are detached, insert-only facts and may counter-sign an assertion;
- the review interface never turns an unreviewed candidate into clinical advice.

The first implementation slice proves the desktop experience and native boundary. It
is read-only and fixture-backed. Authentication, database access, annotation writes and
signing controls are visible but unavailable, so the prototype cannot imply that a
password or click has authority it does not yet possess.

## 2. Product decision

Use **Tauri 2**, with a plain Svelte and TypeScript interface and a Rust application
core.

Tauri uses each platform's system WebView rather than shipping a browser engine. That
fits the compact-installer requirement and produces native macOS, Windows and Linux
packages from one interface. Plain Svelte plus Vite is used instead of SvelteKit: this
application has no Node server or server-side rendering, and removing that layer also
removed an irrelevant cookie dependency and its advisory from the resolved tree.

The frontend is presentation and local interaction state. Rust owns every privileged
operation exposed through Tauri IPC: session persistence, key access, payload
canonicalisation and signatures. Tauri capabilities allow only commands the main
window actually uses. The foundation exposes one read-only command and no shell,
opener, filesystem, network or SQL plugin.

## 3. Deployment architecture

The installed desktop application must not connect directly to PostgreSQL with a
shared service credential. A client-side username/password screen over a shared
database login is cosmetic access control: a modified client can bypass it.

The production shape is:

```text
Svelte WebView
    -> narrow Tauri IPC
Rust desktop core
    -> encrypted device key store
    -> HTTPS
Rust review service
    -> authenticated session and authorisation
    -> PostgreSQL transaction
Drugref database
```

The review service will be a separately deployed Rust service, initially using Axum
and SQLx unless the implementation round finds a smaller maintained alternative. Its
deployment size does not affect the desktop installer. It owns authentication,
authorisation, rate limiting, account administration and database transactions.

The desktop core and service will share a small Rust domain crate for API types and
the frozen `drugref-sig-v1` canonical encoding. The Rust encoder must pass the existing
Python signing vectors byte-for-byte before it may sign a real assertion.

No offline write/synchronisation mode is included. A local read cache may be added
later, but an offline append/supersede protocol introduces conflict semantics that the
first reviewer workflow does not need.

## 4. Reviewer identity and authentication

Authentication and clinical signatures answer different questions:

- a password-backed session authorises use of the review service now;
- an Ed25519 signature proves which enrolled private key attested a clinical row.

Successful login must never be substituted for a signature, and a signing key must
never be used as the password store.

The account model must support the fields requested for the first reviewer:

- stable reviewer UUID;
- unique username;
- full name;
- qualifications as a string;
- brief biography stored as Markdown source;
- active/disabled account status;
- reviewer or administrator role;
- one or more enrolled public signing keys.

The likely schema split is:

- `reviewer_account`: stable UUID, immutable username and creation metadata;
- `reviewer_profile`: append-only profile/status revisions with `superseded_by`;
- `reviewer_password_credential`: append-only Argon2id hashes and rotation history;
- `reviewer_key`: reviewer UUID to existing `signing_key.key_fingerprint` enrolments;
- `auth_session`: revocable, expiring server sessions storing only token digests;
- `review_annotation`: append-only Markdown notes on a stable `question_uuid`.

The implementation round must settle exact constraints in a new migration. It must not
add `public_key` to the account: `signing_key` remains the authoritative registry and
already carries status and revocation history. One reviewer may rotate keys or use more
than one enrolled device.

Passwords are salted Argon2id hashes in PHC form. They are never reversibly encrypted,
logged, returned by an API or sent to the Tauri WebView after login. The service must
rate-limit attempts, use constant-shape login failures, expire sessions and let an
administrator revoke all sessions for an account.

Markdown is stored as source and rendered through a strict sanitiser with raw HTML,
scripts, remote media and active links disabled by default. Upstream names and source
text are untrusted input too and are rendered as text, never injected markup.

## 5. Private keys and sign-off

The private half of an Ed25519 key remains on the reviewer's device and never enters
Drugref infrastructure. The expected local store is Tauri Stronghold or an equally
maintained OS-backed mechanism, subject to a separate dependency and threat-model
review before addition.

The sign-off flow is:

1. The service records the new curated assertion through the append-then-supersede
   transaction and returns the stored row.
2. The desktop core rebuilds the canonical payload from the returned structured row.
3. The UI presents the human-readable decision and canonical payload metadata.
4. On confirmation, the desktop core signs locally.
5. The service independently re-derives the payload, verifies the signature against
   the enrolled public key and inserts `assertion_signature`.
6. The client reloads the database-derived verdict and displays it.

The detached design means a brief unsigned window exists between steps 1 and 5. The UI
must keep that state prominent and offer a resumable "sign pending assertion" action.
It must not claim atomic sign-off.

For the initial governance policy, one valid signature on the live assertion is signed
off. The existing detached table permits a second reviewer to counter-sign. A later
policy table may require two signatures for selected content without changing stored
signatures or invalidating historical rows.

## 6. Review workflow

The primary navigation is a work queue, not a database table browser.

1. A reviewer signs in and sees queue totals and review categories.
2. They filter by candidate kind, state, impact and source, or search names.
3. Selecting an item shows the stable subjects, upstream releases, provenance,
   expansion impact and exact review question.
4. Decision fields use database vocabularies rather than free-text substitutes.
5. Mechanism and management remain prose; evidence grade and ruling are constrained.
6. An annotation or evidence reference may be saved without manufacturing a clinical
   ruling.
7. "Record revision" previews the new immutable row and any row it supersedes.
8. "Sign decision" follows the detached flow above.
9. History shows every revision, annotation and signature verdict without hiding old
   claims.

The UI should say **record revision** or **correct decision**, not **edit row**. Ordinary
form editing exists only before submission. After submission, clinical content is
immutable.

The first two worklist categories are:

- `gap_uncurated_interaction_rule`, curated at the rule grain so one judgement can
  govern every expanded pair;
- `gap_uncurated_condition_contradiction`, curated at the stable drug-condition pair
  because both predicates can be true in different clinical states.

## 7. Foundation slice shipped by this design

`reviewer-app/` contains:

- a Tauri 2 native shell named **Drugref Reviewer**;
- a plain Svelte/TypeScript single-page interface;
- a login and reviewer-profile presentation explicitly labelled as preview-only;
- queue summary, search, type/status filters and a master-detail review layout;
- clinical decision, provenance, annotation and signing areas;
- a single read-only Tauri command returning a bundled review workspace;
- a shared JSON fixture used by Rust IPC and browser preview, avoiding two mock models;
- Rust validation and unit tests for fingerprint shape, target completeness and stable
  target uniqueness;
- a restrictive CSP and no privileged Tauri plugin;
- locked Cargo and npm dependency trees.

The fixture contains representative rows sampled from Drugref's live gap views on
2026-08-17. Its queue totals are a dated interface aid, not product constants. Every
mutating control is disabled. This slice creates no migration, user, session,
annotation, curated assertion or signature.

## 8. Verification and distribution

The foundation gate is:

- `cargo fmt --check`;
- `cargo test`;
- `npm run check` with no Svelte or accessibility diagnostics;
- `npm run build`;
- `npm audit` with no known advisories;
- `npm run tauri build -- --debug --no-bundle` for native integration;
- a visual pass at desktop and narrow widths whenever a browser surface is available.

Platform release builds must be produced on their target runners: signed/notarised DMG
or app bundle for macOS, signed MSI or NSIS installer for Windows, and AppImage plus a
native package for the supported Linux baseline. Release CI records unpacked binary
and installer sizes so compactness remains measured rather than promotional.

The resolved foundation dependencies are AGPL-compatible: the npm tree is MIT,
Apache-2.0, BSD-3-Clause or ISC; the Rust tree is permissive, Unicode, Zlib or MPL-2.0,
with compatible arms selected from multi-licensed packages. Every new plugin or server
dependency still requires the same pre-addition review.

## 9. Next implementation slices

1. Reviewer-account migration and authenticated service skeleton.
2. Live read-only queue API with pagination, filters and database-derived vocabularies.
3. Append-only annotations and evidence references.
4. Curated interaction and condition revision transactions.
5. Local Ed25519 enrolment, Rust canonical vectors, signing and verification.
6. Reviewer/key administration, revocation queues and counter-signing policy.
7. Signed cross-platform release pipeline and updater policy.

Each slice must leave a usable, accurately labelled boundary. In particular, a live
database read does not authorise writes, an authenticated write is not signed, and a
recorded signature is not necessarily valid under the key registry's current verdict.
