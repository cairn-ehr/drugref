# CLAUDE.md — stable project context for coding agents

Auto-loaded at session start. Keep this file short and **stable**: invariant rules and commands only.
Session state lives in `docs/HANDOVER.md` (**volatile**, line-bounded, regenerated each session); the stable
working notes — traps, state by layer, how to run/test, schema and code map — live in `docs/PROJECT-NOTES.md`
(**edited in place, no line bound**); slice sequencing in `docs/ROADMAP.md` (**no line bound**); the canonical
what/why in the design specs under `docs/superpowers/specs/` (if anything here disagrees with a spec, the
spec wins).

## What this repo is

**drugref.org v2, global tier** — an open, vendor-independent drug-information service (AGPL-3.0).
A registry of active drug moieties with immortal UUIDv5 identities and append-only external-identifier
claims, seeded reproducibly from public-domain/open sources (UNII/GSRS, ChEBI, MED-RT, MeSH, RxNorm).
Advisory reference data — never on Cairn's signed inter-node wire core.

## Starting a session

Read `docs/HANDOVER.md` first and follow it (the `nextsession` skill does this). Before starting work,
verify HANDOVER.md, PROJECT-NOTES.md and ROADMAP.md reflect the current state; update them if stale. When
done, update all three, then commit, push, and open a PR to `main` linking any relevant issue.

**Only HANDOVER.md is bounded.** THE NUMBER IS STATED IN ITS OWN HEADER AND NOWHERE ELSE — including here.
It was written down in three files at once (here twice, the `nextsession` skill, and HANDOVER's header),
two of them disagreed, and the file exceeded both; a bound is a vocabulary like any other, and this repo
has lost four rounds to one rule kept in two places. Read the number off the file it governs.

PROJECT-NOTES.md and ROADMAP.md are edited in place and grow: #63 measured that the old < 500-line bound
forced a compression pass every round, which turned every edit into an ~80% rewrite and made `git log -p`
useless on the two files whose job is carrying state between sessions.

Public documentation is published from `docs-site/` (MkDocs Material) to
`docs.drugref.org`; its **Design decisions** section holds *living* records (only
decisions that currently stand — revised in place, reversed ones removed), distinct from
the immutable per-slice specs under `docs/superpowers/specs/`.

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
