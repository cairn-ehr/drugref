# Drugref Reviewer

Cross-platform Tauri 2 interface for human review of Drugref's curated overlay.

Accounts, sessions and the paginated clinical queue are live through
`reviewer-service/`. On first run the desktop core checks the service before loading
the workspace. If the database has no administrator, only first-administrator
registration is shown. Administrators can then list and create reviewer accounts in
the GUI. Queue search and the type, source and relationship filters execute on the
service; no clinical control can write data, and authentication is not a clinical
signature.

The Vite browser surface retains representative data only for visual development and
labels it **Browser queue preview**. The installed Tauri app never falls back to that
data when its authenticated service request fails.

Behavioural and validation values live in `src/lib/constants.ts`; pure queue-query
and presentation transformations live in `src/lib/queue.ts` and
`src/lib/presentation.ts`. Components retain only lifecycle, event, and view state.
The repository-wide documentation, constants, typing, and functional-decomposition
rules are defined in [`../CONTRIBUTING.md`](../CONTRIBUTING.md).

## Development

```sh
npm install
npm run check
npm run build
npm run tauri dev
```

Apply migration 044 and start `reviewer-service/` first. Debug builds use
`http://127.0.0.1:8787` by default; set `DRUGREF_REVIEW_SERVICE_URL` to override it.
Rust unit tests run from `src-tauri` with `cargo test`.
