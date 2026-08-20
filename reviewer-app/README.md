# Drugref Reviewer

Cross-platform Tauri 2 interface for human review of Drugref's curated overlay.

Accounts, sessions, the paginated clinical queue, working records and curated revision
transactions are live through `reviewer-service/`. On first run the desktop core checks
the service before loading
the workspace. If the database has no administrator, only first-administrator
registration is shown. Administrators can then list and create reviewer accounts in
the GUI. Queue search and the type, source and relationship filters execute on the
service. Reviewers can append attributed Markdown notes and citation-only evidence
references; these do not change clinical state. They can separately preview and record
an immutable interaction judgement or condition ruling with stale-form protection.
They can then create an encrypted device-local Stronghold key, enrol only its public
half, inspect the exact canonical payload, and explicitly sign. Current unsigned GUI
revisions remain available in **Pending signatures** after queue refresh or restart.
Authentication is not a clinical signature.

Signature confirmation displays every canonically rendered field in signed order,
including complete mechanism and management text, provenance, identifiers, key and
signing instant. SQL NULL is explicit. The raw length-prefixed byte buffer remains in
native memory and is still bound to the displayed digest.

The signing-vault passphrase is separate from the reviewer account password and is
never sent to the service. If it is lost, **Replace key** first retires the
authenticated public enrolment and then deletes only this reviewer's fixed local vault
files. An unused key reports zero preserved signatures; earlier signatures from a used
key remain valid under the registry's time-scoped rotation rule.

Administrators have a separate public-key trust view covering every current registry
fingerprint, reviewer enrolment, status boundary, signature count and current review
impact. Retirement is time-scoped; compromise objects to the key's entire signature
history. Current revisions with no registry-unobjected signature return to **Pending
signatures** as counter-signature tasks. Clinical rows remain served throughout.
Revision history distinguishes signed, unsigned, revoked-key and unknown-key registry
states with human-readable labels; only `drugref verify` checks Ed25519 mathematics.

The Vite browser surface retains representative data only for visual development and
labels it **Browser queue preview**. The installed Tauri app never falls back to that
data when its authenticated service request fails.

Behavioural and validation values live in `src/lib/constants.ts`; pure queue-query
and presentation transformations live in `src/lib/queue.ts` and
`src/lib/presentation.ts`; `src/lib/records.ts` and `src/lib/decisions.ts` own the
native/browser-preview working-record and clinical-revision adapters;
`src/lib/signing.ts` owns the narrow signing adapter. Components retain only lifecycle,
event, and view state.
The repository-wide documentation, constants, typing, and functional-decomposition
rules are defined in [`../CONTRIBUTING.md`](../CONTRIBUTING.md).

## Development

```sh
npm install
npm run check
npm run build
npm run tauri dev
```

Apply migrations through 048 and start `reviewer-service/` first. Debug builds use
`http://127.0.0.1:8787` by default; set `DRUGREF_REVIEW_SERVICE_URL` to override it.
Rust unit tests run from `src-tauri` with `cargo test`.
