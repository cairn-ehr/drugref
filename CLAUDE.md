# CLAUDE.md — stable project context for coding agents

Auto-loaded at session start. Keep this file short and **stable**: invariant rules and commands only.
Session state lives in `docs/HANDOVER.md`; slice sequencing in `docs/ROADMAP.md`; the canonical
what/why in the design specs under `docs/superpowers/specs/` (if anything here disagrees with a spec,
the spec wins).

## What this repo is

**drugref.org v2, global tier** — an open, vendor-independent drug-information service (AGPL-3.0).
A registry of active drug moieties with immortal UUIDv5 identities and append-only external-identifier
claims, seeded reproducibly from public-domain/open sources (UNII/GSRS, ChEBI, MED-RT, MeSH, RxNorm).
Advisory reference data — never on Cairn's signed inter-node wire core.

## Starting a session

Read `docs/HANDOVER.md` first and follow it (the `nextsession` skill does this). Before starting work,
verify HANDOVER.md and ROADMAP.md reflect the current state; update them if stale. When done, update
both (concise, < 500 lines), then commit, push, and open a PR to `main` linking any relevant issue.

## Commands

- Install/sync: `uv sync`
- Tests: `uv run pytest` — DB-gated tests need `DRUGREF_TEST_DSN` set (current DSN: see
  `docs/HANDOVER.md`). All tests must pass before committing unless explicitly waived.
- Lint: `ruff check .` / `ruff format .`
- Migrations apply via `db.apply_migrations` (ledger-backed: applied files are immutable —
  never edit an applied migration; add a new numbered file instead).

## Coding rules (non-negotiable)

1. Prefer pure functions in small reusable modules over complex code.
2. TDD: write the failing test first, then the code.
3. Inline documentation understandable by junior contributors is mandatory.
4. Keep code files under ~500 lines where feasible; refactor when they grow past that.
5. No technical debt: fix errors when found, or lodge a GitHub issue.
6. **Licensing is a blocker, not a cleanup item**: all code is AGPL-3.0; every dependency and every
   bundled reference-data source must be AGPL-3.0-compatible — check BEFORE adding. Encumbered sources
   attach only as node-local, separately-licensed plug-ins, never bundled.

## Architecture invariants

- `moiety_uuid` is immortal; claims are append-only (supersession, never DELETE/UPDATE of values).
- Identity UUIDs are UUIDv5 minted from canonicalised claim values (`ids.canonical_claim_value`).
- Ingest parsers are pure/streaming (no DB access); orchestrators (`ingest/*_run.py`) own the
  transaction and are the only writers.
- Per-source rebuilds are safe: projections keyed by `ingest_run.source` are delete-and-rebuild.
