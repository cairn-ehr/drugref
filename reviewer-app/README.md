# Drugref Reviewer

Cross-platform Tauri 2 interface for human review of Drugref's curated overlay.

This first vertical slice is intentionally read-only. It presents the login, reviewer
profile, review queue and record-detail experience against a bundled fixture sampled
from Drugref's gap views. Authentication, PostgreSQL access, annotations and signing
will arrive behind an authenticated service boundary; no preview control can write
clinical data.

## Development

```sh
npm install
npm run check
npm run tauri dev
```

Rust unit tests run from `src-tauri` with `cargo test`.
