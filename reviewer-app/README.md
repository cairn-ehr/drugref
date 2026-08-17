# Drugref Reviewer

Cross-platform Tauri 2 interface for human review of Drugref's curated overlay.

Accounts and sessions are live through `reviewer-service/`; the clinical queue remains
an intentionally read-only fixture sampled from Drugref's gap views. On first run the
desktop core checks the service before loading the workspace. If the database has no
administrator, only first-administrator registration is shown. Administrators can then
list and create reviewer accounts in the GUI. No preview clinical control can write
clinical data, and authentication is not a clinical signature.

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
