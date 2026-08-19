# Developer guide

drugref is one repository with three execution surfaces: the Python ingest/operator
tooling, the Rust reviewer service, and the Tauri/Svelte reviewer application. They
share PostgreSQL as the integrity boundary but are built and tested independently.

## Prerequisites

- Python 3.12 or newer and [uv](https://docs.astral.sh/uv/)
- PostgreSQL 18 or newer
- a current stable Rust toolchain
- Node.js and npm for the reviewer frontend
- the [Tauri 2 platform prerequisites](https://v2.tauri.app/start/prerequisites/) for
  native application builds

Repository-wide rules—including docstrings, named behavioural constants, complete
typing and pure reusable transformations—are in
[`CONTRIBUTING.md`](https://github.com/cairn-ehr/drugref/blob/main/CONTRIBUTING.md).

## Python and PostgreSQL

Install the locked development environment, apply the ordered migrations to a database
you control, and inspect its ingest state:

```sh
uv sync
uv run drugref --dsn 'postgresql://localhost/drugref_dev' migrate
uv run drugref --dsn 'postgresql://localhost/drugref_dev' status
```

The migration command records checksums and refuses drift. Merged migration files are
immutable; schema corrections belong in a new numbered migration.

The main Python checks are:

```sh
uv run pytest
uv run ruff check .
```

Some PostgreSQL-backed tests recreate their target database. Never point the test suite
at a database holding reviewer accounts, signing keys or work you intend to keep.

## Reviewer service

The desktop app never connects directly to PostgreSQL. Run the separately authenticated
service against a dedicated, migrated database:

```sh
cd reviewer-service
DATABASE_URL='postgresql://localhost/drugref_reviewer_dev' cargo run
```

The service binds to `127.0.0.1:8787` by default. `DRUGREF_REVIEW_BIND` changes the
listen address. Production deployment belongs behind HTTPS; it owns authentication,
authorisation and database transactions, while clients hold only revocable bearer
sessions.

Run its ordinary Rust checks with an explicit manifest because this repository is not
a Cargo workspace:

```sh
cargo fmt --manifest-path reviewer-service/Cargo.toml --check
cargo test --manifest-path reviewer-domain/Cargo.toml
cargo test --manifest-path reviewer-service/Cargo.toml
cargo clippy --manifest-path reviewer-service/Cargo.toml --all-targets -- -D warnings
```

Populated-database lifecycle tests are ignored by default and require an explicit
`DRUGREF_REVIEW_TEST_DATABASE_URL`. The service
[README](https://github.com/cairn-ehr/drugref/blob/main/reviewer-service/README.md)
lists the available live checks and their data requirements.

## Reviewer application

Start the service first, then run the Tauri application:

```sh
cd reviewer-app
npm install
npm run check
npm run tauri dev
```

Debug builds use `http://127.0.0.1:8787` unless
`DRUGREF_REVIEW_SERVICE_URL` is set. Release builds require an explicit HTTPS service
URL. `npm run dev` opens a clearly labelled, representative browser preview for layout
work only; it has no authenticated native fallback and cannot be used for clinical
review.

The app's Rust tests run from its native directory:

```sh
cargo test --manifest-path reviewer-app/src-tauri/Cargo.toml
npm --prefix reviewer-app run check
npm --prefix reviewer-app run build
```

## Public documentation

The website source is under `docs-site/`. Strict mode treats broken links, missing nav
targets and orphaned pages as failures:

```sh
uv run --group docs mkdocs build --strict -f docs-site/mkdocs.yml
```

Pull requests that touch `docs-site/` run this build. A successful push to `main`
publishes the generated site to [docs.drugref.org](https://docs.drugref.org/).

## Where to go deeper

- [Architecture](../architecture/index.md) explains the trust boundaries.
- [Design decisions](../decisions/index.md) are the current public decisions and are
  updated in place.
- Accepted derivations and implementation plans remain in the repository's
  [`docs/superpowers/`](https://github.com/cairn-ehr/drugref/tree/main/docs/superpowers)
  tree.
