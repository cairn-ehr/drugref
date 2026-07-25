# Design — drugref public documentation site (`docs.drugref.org`)

**Date:** 2026-07-25 · **Repo:** `github.com/cairn-ehr/drugref` · **Status:** design, pending
implementation plan. **Builds on:** the landing page ([`site/index.html`](../../../site/index.html)) whose
"Design documents" button this replaces, and the existing internal docs it deliberately does **not**
publish (`docs/HANDOVER.md`, `docs/ROADMAP.md`, `docs/superpowers/specs/`).

**The problem.** The landing page's **"Design documents"** button points at the raw GitHub `docs/` folder
([`site/index.html`:182](../../../site/index.html#L182)). For anyone who is not already a contributing
developer that is a dead end — a tree of dense, per-slice engineering specs plus two files that announce
themselves as "disposable working scaffolding." drugref's intended audience is broader: prospective
adopters and integrators (EHR / pharmacy / research), the health-IT and open-data community, and future
contributors who need an on-ramp before the specs. None of them are served today.

**What we are building.** A published documentation site at **`docs.drugref.org`**, built with **MkDocs
Material** (the same engine as [`docs.cairn-ehr.org`](https://docs.cairn-ehr.org), our sibling project),
that explains *what drugref is, why it is built the way it is, and what decisions currently stand*. It has
a **Design decisions** section modelled on ADRs but **living, not immutable**: each record describes a
decision that *currently stands*; when a decision changes the record is revised in place; a reversed
decision is simply removed. The site is authored for readers, distilling the engineering specs rather than
exposing them raw, and links back to the specs for full derivation.

**Scope of change (this first pass).** Stand up the MkDocs project, its information architecture and nav,
a GitHub Actions build-and-deploy pipeline to GitHub Pages at `docs.drugref.org`, brand it to match the
landing page, re-point the landing page button, and **write one strong seed page per section** (Overview,
Architecture, four exemplar decision records, Principles, Data sources & licensing, Roadmap, plus two
clearly-marked "coming" stubs). This establishes every template; remaining decision records and the deeper
developer/user content are backfilled iteratively in later sessions.

**Out of scope (each a later concern):** a full backfill of every standing decision to date (only four
exemplar records now — §4); the developer guide and user manual *content* (their nav slots and stub pages
are created, but the substance waits — the user manual in particular waits on the public API, ROADMAP
Slice 6); any change to how the **landing page** itself is hosted (it stays a Cloudflare static page
sourced from [`site/`](../../../site)); and any change to the internal `HANDOVER.md` / `ROADMAP.md` /
`superpowers/specs/` working artifacts beyond linking to them.

---

## 1. Licence gate (coding rule 6 — cleared before adding anything)

Two things get "added" here — build tooling and prose — and both are clean:

1. **Build tooling.** [MkDocs](https://www.mkdocs.org/) is **BSD-2-Clause**; [Material for
   MkDocs](https://squidfunk.github.io/mkdocs-material/) (community edition) is **MIT**. Both are
   AGPL-compatible. More to the point they are **build-time developer tooling** — they generate static
   HTML in CI and are never bundled into the shipped service code or the reference data. They carry the
   same standing as `ruff` or `pytest`, not the standing of a bundled data source.
2. **Documentation prose.** The written content of the site is licensed **CC BY-SA 4.0** — copyleft
   (derivatives shared alike, aligned with drugref's paywall-free-copyleft philosophy) and the conventional
   choice for prose, distinct from the repo's **AGPL-3.0** which governs code. This split is stated on the
   site (footer) and recorded in the repo. No third-party prose is copied in, so there is nothing to
   attribute upstream; `NOTICE` is unchanged.

Nothing here bundles or redistributes an encumbered reference-data source, so the hard part of rule 6 does
not engage.

## 2. Why a separate `docs-site/` tree, not the existing `docs/`

The repo already has a `docs/` directory, and it is **not** publishable as-is: it holds `HANDOVER.md` and
`ROADMAP.md` (both self-described disposable scaffolding) and the dense per-slice engineering specs under
`docs/superpowers/specs/`. Pointing MkDocs' `docs_dir` at it would publish all of that to the world.

So the published site gets its **own self-contained subtree** at the repo root, a sibling of the existing
`site/` (Cloudflare landing page) and `docs/` (internal working docs):

```
docs-site/
  mkdocs.yml                     # config, theme, nav, strict mode
  docs/                          # published markdown (MkDocs default docs_dir)
    index.md                     # Overview
    architecture/index.md
    decisions/
      index.md                   # explains the living-record model + template
      immortal-moiety-identity.md
      append-only-claims.md
      hybrid-store.md
      licensing-is-a-blocker.md
    principles/index.md
    sources/index.md             # data sources & licensing
    roadmap/index.md
    developer/index.md           # "coming" stub
    user-manual/index.md         # "coming" stub
    assets/
      drugref_logo.png           # copied from site/assets (logo + favicon)
      extra.css                  # brand palette to match the landing page
    CNAME                        # single line: docs.drugref.org
  overrides/                     # optional Material theme overrides (partials)
```

`docs_dir` is `docs-site/docs` (the doubled name is seen only by contributors and is unambiguous). The
build output (`docs-site/site/` by default) is **gitignored** — it is a CI artifact, never committed. The
three trees now have one clear job each: `site/` → the Cloudflare landing page; `docs-site/` → the
GitHub Pages documentation site; `docs/` → internal working artifacts, unpublished.

## 3. Information architecture (the sidebar)

Sized to what drugref is today, with named slots for what is coming — Approach B from brainstorming (a lean
living-docs IA, not a 1:1 copy of Cairn's larger section set, and not a decisions-only minimum):

| Nav section | Page(s) | Audience & purpose |
|---|---|---|
| **Overview** | `index.md` | Everyone. What drugref is, why it exists — distilled from the README and landing page. The site's front door. |
| **Architecture** | `architecture/index.md` | Technical adopters, contributors. The data model in the large: the two orthogonal structures (composition tree + classification DAG), the hybrid store, immortal identity + append-only claims. The "what we're building" surface. |
| **Design decisions** | `decisions/` (index + records) | Contributors, evaluators. The living decision records — §4. |
| **Principles** | `principles/index.md` | Everyone, esp. policy/community. The invariants and philosophy: advisory-not-authoritative, steward-uses-the-public-API, paywall-free copyleft, immortal identity, integrity-enforced-in-the-DB. |
| **Data sources & licensing** | `sources/index.md` | Adopters, licensing-conscious readers. The provenance table (UNII, ChEBI, MED-RT, MeSH, RxNorm) and the licence posture (AGPL-compatible only; encumbered sources attach node-locally). High adopter value. |
| **Roadmap** | `roadmap/index.md` | Everyone. A reader-friendly slice sequence (done / next), curated from `docs/ROADMAP.md` — not the raw file. |
| **Developer guide** | `developer/index.md` | Contributors. **Stub now** — reserves the slot; will cover local setup, ingest, testing, migrations. |
| **User manual** | `user-manual/index.md` | Consumers of the service. **Stub now** — reserves the slot; waits on the public API (Slice 6). |

Nav order in `mkdocs.yml` follows the table. Each section is a directory with an `index.md` so the section
label is itself a landing page (Material's "section index pages" feature).

## 4. The living decision record

drugref's design decisions live today only inside the engineering specs, entangled with implementation
detail. The **Design decisions** section lifts each *standing* decision into a short, readable,
independently-citable page.

**This is ADR-shaped but explicitly not an immutable ADR log.** Classic ADRs are numbered, append-only,
and keep superseded records forever with "Superseded by" links. drugref's are **living**: the section
documents *only decisions that currently stand*; a changed decision is edited in place; a reversed decision
is deleted. There is therefore no status graveyard, no supersession chain, and no requirement that a record
never change. (The full history is not lost — it remains in git and in the dated engineering specs; the
published record just always reflects *current* truth.)

**Template** (`decisions/index.md` states it and the norm above):

```markdown
# <Decision title>

**Status:** Active            <!-- or "Under review" -->
**Last reviewed:** 2026-07-25
**Applies to:** <slice / subsystem>
**Full derivation:** <link to the relevant docs/superpowers/specs/ page>

## Context      — the forces / the problem this decision answers
## Decision     — what stands today
## Consequences — trade-offs, what it enables, what it costs
## Related      — other decision records, principles, code
```

Records stay short and delegate depth to the linked spec rather than duplicating it.

**Four exemplar records seeded now** (chosen to cover identity, provenance, storage model, and governance —
the four corners of drugref's design, so the template is proven across genuinely different decision kinds):

1. **Immortal moiety identity via UUIDv5-on-UNII** — every active moiety gets a stable UUID minted
   deterministically from its UNII and pinned forever; identity is never keyed on a name. *Derivation:*
   slice-1 moiety-spine spec.
2. **Append-only claims with supersession** — external-identifier claims are append-only; corrections
   supersede, values are never destructively overwritten. *Derivation:* slice-1 spec + the foundation
   review (`db/005`).
3. **Hybrid store: rebuildable projections vs the append-only signed overlay** — ingested feeds are
   drop-and-rebuild projections; curated knowledge is an append-only signed overlay. *Derivation:* the
   architecture-in-one-breath note carried across specs; ROADMAP §"data model in the large".
4. **Licensing is a blocker, not a cleanup item** — all code AGPL-3.0; every dependency and bundled data
   source must be AGPL-compatible, checked before adding; encumbered sources attach only as node-local
   plug-ins. *Derivation:* CLAUDE.md rule 6; the per-slice licence gates.

## 5. Branding — visual continuity with the landing page

The docs should read as the same project as `drugref.org`. Material is themed to the landing page's palette
(defined in [`site/index.html`](../../../site/index.html)): accent green `#0e7c66` / `#0a5c4c`, paper
background `#fbfaf7`, ink `#1c2733`. Delivered via a Material `palette` block plus a small
`docs/assets/extra.css` for the exact tones. The existing `drugref_logo.png` (copied from
`site/assets/`) is the site logo and favicon. A light/dark toggle is enabled (Material default), which the
single-file landing page does not have — acceptable divergence; the accent and logo carry the identity.

## 6. Build & deploy

**Dependencies.** The repo standardizes on `uv` + `pyproject.toml`. A **`docs` dependency group** is added
to `pyproject.toml` pinning `mkdocs-material` (which pulls `mkdocs`). The build is `uv sync --group docs &&
uv run mkdocs build --strict`. One toolchain, no separate pip requirements file, and the docs deps stay out
of the default install so they never touch the runtime/test environment.

**Pipeline** (`.github/workflows/docs.yml`), path-filtered to `docs-site/**` and the workflow file itself:

- **Pull requests** → build with `--strict` only, **no deploy**. This is the docs' test gate (§7): a broken
  internal link, a nav entry pointing at a missing file, or any Material warning fails the check before
  merge. Mirrors how `ci.yml` gates the Python suite.
- **Push to `main`** (and `workflow_dispatch`) → build, then publish via the official Pages actions
  (`actions/configure-pages` → `actions/upload-pages-artifact` → `actions/deploy-pages`) with the
  `pages: write` / `id-token: write` permissions and a `github-pages` environment. This is a **separate
  GitHub Pages site from the Cloudflare landing page** — no conflict, because the landing page is not served
  from this repo's Pages (it is a Cloudflare static page sourced from `site/`).

**Custom domain.** A `CNAME` file containing `docs.drugref.org` lives in the source dir so MkDocs copies it
verbatim into every build; `mkdocs.yml` sets `site_url: https://docs.drugref.org/` (needed for correct
canonical URLs, the sitemap, and Material search/nav).

**Two required manual operator steps** (plus one recommended — outside what code or CI can do; documented
in the plan, flagged for the maintainer):

1. Repo **Settings → Pages → Source = GitHub Actions** (one-time; enables the Actions-based deploy).
2. **Cloudflare DNS**: add `docs.drugref.org` → `CNAME cairn-ehr.github.io`, **DNS-only (grey cloud)** so
   GitHub terminates TLS for the subdomain. (The apex `drugref.org` Cloudflare config is untouched.)

   **This does not conflict with `docs.cairn-ehr.org`.** `cairn-ehr.github.io` is not "the Cairn docs
   site" — it is the org's single Pages edge address that *every* project site in the `cairn-ehr` org
   CNAMEs to (a DNS record cannot target a specific repo — there is no path in DNS). GitHub routes to the
   correct repo **by Host header**: a request for `docs.drugref.org` is served by whichever repo has
   *claimed that exact host* as its Pages custom domain (here, `cairn-ehr/drugref` via the `CNAME` file in
   §6), and `docs.cairn-ehr.org` is served by whatever repo claimed *it*. The only hard rule is that one
   exact hostname is claimed by exactly one repo — and `docs.drugref.org` and `docs.cairn-ehr.org` are
   distinct hosts on distinct apexes, so there is no collision.
3. **Recommended (not required):** add `drugref.org` to the org's **verified domains** (org / repo Pages
   settings) to prevent domain-takeover warnings. It does not affect serving and does not touch Cairn.

Until both are done the site still builds and the workflow still deploys to the default
`cairn-ehr.github.io/drugref` Pages URL; the custom domain simply resolves once DNS + the Pages source are
set. So the two manual steps are not a hard blocker on landing the code.

## 7. Verification

A documentation site's "tests" are a clean strict build and resolvable links:

- **`uv run mkdocs build --strict` passes with zero warnings** — the completion gate. `strict: true` in
  `mkdocs.yml` turns any broken internal link, missing-nav-target, or orphaned page into a build failure.
- **CI enforces it on every docs PR** (the PR job above), exactly as `ci.yml` enforces the Python suite —
  so the docs cannot silently rot on merge.
- **No pytest involvement.** The docs subtree is independent of the Python test suite; `uv run pytest` is
  unaffected and its count does not change.
- **Manual smoke check** after first deploy: the eight nav sections render, the logo/palette match the
  landing page, and the landing page "Design documents" button reaches the live site.

There is no TDD "failing test first" in the code sense here (there is no unit under test), but the
`--strict` build is written into CI *before* content exists, so the gate is real from the first page.

## 8. Landing-page wiring

A single change to [`site/index.html`](../../../site/index.html): the **"Design documents"** button
([line 182](../../../site/index.html#L182)) `href` moves from
`https://github.com/cairn-ehr/drugref/tree/main/docs` to **`https://docs.drugref.org`**. The "Source code
on GitHub" button ([line 181](../../../site/index.html#L181)) is unchanged. Because the landing page is a
Cloudflare static page sourced from `site/`, this change goes live when `site/` is next deployed by its
existing (out-of-scope) mechanism — the docs site does not depend on it, nor vice versa.

## 9. Relationship to the existing docs (what stays, what is not published)

- **`docs/HANDOVER.md`, `docs/ROADMAP.md`** — remain internal working scaffolding, **not published**. The
  public **Roadmap** page is a curated, reader-facing derivative; `ROADMAP.md` stays the working ledger.
- **`docs/superpowers/specs/`** — remain the deep engineering artifacts and the canonical *what/why* (per
  CLAUDE.md). Decision records and architecture pages **link** to them for derivation; they are not
  republished.
- **A short pointer** to the published-docs location and the living-decision-record norm belongs in the
  project's stable context so future sessions maintain it — added to CLAUDE.md/HANDOVER as a small follow-up
  in the plan, not a core deliverable of this spec.

## 10. Open points (non-blocking)

- **DNS + Pages source** are manual operator steps (§6). Tracked in the plan; the code lands and deploys to
  the default Pages URL regardless.
- **Backfill of the remaining standing decisions** (candidate-tier interactions, the open-question
  registry, integrity-in-the-DB, advisory-never-on-the-wire-core, source-neutral class registry, …) is
  iterative follow-up work, not this pass. The four seed records prove the template; the rest are added as
  sessions touch them.
- **Developer guide / User manual content** — stubs now; the user manual in particular is gated on the
  public API (Slice 6). File as follow-ups when their upstream work lands.
- **Search / analytics / versioning** (e.g. `mike` for versioned docs) — not needed at this size; revisit
  if the docs grow a release cadence.

## 11. Summary of deliverables

1. `docs-site/` MkDocs project — `mkdocs.yml` (theme, palette, nav, `site_url`, `strict: true`), `docs/`
   source tree (§2), `CNAME`, `extra.css`, logo asset, optional `overrides/`.
2. Seed content — Overview, Architecture, four decision records + decisions index, Principles, Data sources
   & licensing, Roadmap, and the developer/user-manual stubs (§3–§4).
3. `.github/workflows/docs.yml` — PR strict-build gate + `main` build-and-deploy to Pages (§6).
4. `pyproject.toml` — a `docs` dependency group pinning `mkdocs-material` (§6).
5. `.gitignore` — ignore `docs-site/site/` (the build output).
6. `site/index.html` — the one-line "Design documents" href change (§8).
7. Docs-prose licence recorded as **CC BY-SA 4.0** (site footer + repo), distinct from code AGPL-3.0 (§1).
8. A small CLAUDE.md/HANDOVER pointer to the published docs + the living-record norm (§9, follow-up).
