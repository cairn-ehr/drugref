# Public Documentation Site Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stand up a published MkDocs Material documentation site for drugref at `docs.drugref.org`, seeded with one strong page per section, deployed from this repo via GitHub Actions.

**Architecture:** A self-contained `docs-site/` MkDocs project (sibling of `site/` and `docs/`) holds all published markdown; a path-filtered GitHub Actions workflow builds it `--strict` on every PR and deploys it to GitHub Pages on push to `main`. The landing page's "Design documents" button re-points to the new site. Design decisions are captured as *living* records (revised in place; reversed decisions removed), not immutable ADRs.

**Tech Stack:** MkDocs + Material for MkDocs (built with `uv`, Python ≥ 3.12), GitHub Actions + GitHub Pages, Cloudflare DNS for the `docs.drugref.org` subdomain.

**Design spec:** [`docs/superpowers/specs/2026-07-25-drugref-public-docs-site-design.md`](../specs/2026-07-25-drugref-public-docs-site-design.md)

## Global Constraints

- **Verification gate for every task:** `uv run --group docs mkdocs build --strict -f docs-site/mkdocs.yml` exits 0 with **zero warnings**. (The `--group docs` is required: `uv run` re-syncs to the default groups first, which would otherwise drop `mkdocs-material`.) `strict: true` turns any broken internal link, missing nav target, or orphaned page into a build failure. There is no pytest here; do not touch or run the Python suite.
- **Licensing (coding rule 6):** MkDocs is BSD-2, Material for MkDocs is MIT — both AGPL-compatible build-time tooling, never bundled into shipped code/data. Documentation *prose* is licensed **CC BY-SA 4.0** (distinct from the repo's code, AGPL-3.0). Bundle no third-party prose.
- **Do not publish internal docs:** `docs/HANDOVER.md`, `docs/ROADMAP.md`, and `docs/superpowers/**` stay unpublished. Published pages *link* to specs for derivation; they never republish them.
- **Nav order is fixed:** Overview → Architecture → Design decisions → Principles → Data sources & licensing → Roadmap → Developer guide → User manual. Tasks 1–7 append nav entries in this order, so the order falls out of the task sequence.
- **Brand palette (copied verbatim from `site/index.html`):** accent green `#0e7c66`, dark `#0a5c4c`, paper `#fbfaf7`, ink `#1c2733`.
- **Frequent commits:** one commit per task, conventional-commit style, ending with the `Co-Authored-By` trailer this repo uses.
- **Files stay focused** and, where practical, under ~500 lines (coding rule 4).

---

### Task 1: Scaffold the MkDocs project + Overview home page

Stands up the whole `docs-site/` project so `mkdocs build --strict` passes, and writes the Overview page (the home page is the deliverable that needs the scaffold, so they land together).

**Files:**
- Create: `docs-site/mkdocs.yml`
- Create: `docs-site/docs/index.md` (Overview)
- Create: `docs-site/docs/CNAME`
- Create: `docs-site/docs/assets/extra.css`
- Create: `docs-site/docs/assets/drugref_logo.png` (copied from `site/assets/`)
- Modify: `pyproject.toml` (add a `docs` dependency group)
- Modify: `.gitignore` (ignore the build output `docs-site/site/`)

**Interfaces:**
- Consumes: nothing (first task).
- Produces: a working MkDocs project rooted at `docs-site/mkdocs.yml` with `docs_dir: docs`, `strict: true`, the brand theme, and a `nav:` containing exactly one entry (`Overview: index.md`). Later tasks append nav entries after the Overview line and add page files under `docs-site/docs/`.

- [ ] **Step 1: Add the `docs` dependency group to `pyproject.toml`**

Replace the `[dependency-groups]` block:

```toml
[dependency-groups]
dev = ["pytest>=8"]
docs = ["mkdocs-material>=9.5,<10"]
```

- [ ] **Step 2: Sync the docs dependencies**

Run: `uv sync --group docs`
Expected: resolves and installs `mkdocs-material` (and `mkdocs`) into `.venv`; updates `uv.lock`.

- [ ] **Step 3: Ignore the build output**

Append to `.gitignore`:

```gitignore

# MkDocs build output for the docs-site/ project (a CI artifact, never committed).
docs-site/site/
```

- [ ] **Step 4: Copy the logo asset into the docs project**

Run:
```bash
mkdir -p docs-site/docs/assets
cp site/assets/drugref_logo.png docs-site/docs/assets/drugref_logo.png
```

- [ ] **Step 5: Write `docs-site/mkdocs.yml`**

```yaml
site_name: drugref.org documentation
site_url: https://docs.drugref.org/
site_description: >-
  Design, architecture, and the standing decisions behind drugref.org — an open,
  vendor-independent drug-information commons.
repo_url: https://github.com/cairn-ehr/drugref
repo_name: cairn-ehr/drugref
edit_uri: edit/main/docs-site/docs/
copyright: >-
  Documentation © drugref.org contributors, licensed
  <a href="https://creativecommons.org/licenses/by-sa/4.0/">CC BY-SA 4.0</a> ·
  Code licensed <a href="https://www.gnu.org/licenses/agpl-3.0.html">AGPL-3.0</a>

# strict makes a broken internal link or a nav entry with no file FAIL the build,
# so the docs cannot silently rot. This is the project's "test".
strict: true

theme:
  name: material
  logo: assets/drugref_logo.png
  favicon: assets/drugref_logo.png
  palette:
    - media: "(prefers-color-scheme: light)"
      scheme: default
      primary: custom
      accent: custom
      toggle:
        icon: material/weather-night
        name: Switch to dark mode
    - media: "(prefers-color-scheme: dark)"
      scheme: slate
      primary: custom
      accent: custom
      toggle:
        icon: material/weather-sunny
        name: Switch to light mode
  features:
    - navigation.instant
    - navigation.tracking
    - navigation.sections
    - navigation.indexes
    - navigation.top
    - toc.follow
    - content.action.edit
    - search.suggest
    - search.highlight

extra_css:
  - assets/extra.css

markdown_extensions:
  - admonition
  - pymdownx.details
  - tables
  - toc:
      permalink: true
  - pymdownx.superfences:
      custom_fences:
        - name: mermaid
          class: mermaid
          format: !!python/name:pymdownx.superfences.fence_code_format

nav:
  - Overview: index.md
```

- [ ] **Step 6: Write `docs-site/docs/assets/extra.css`**

```css
/* Brand palette lifted verbatim from site/index.html so the docs read as the
   same project as the drugref.org landing page. Material picks up these CSS
   variables because both palette entries in mkdocs.yml set `primary: custom`
   and `accent: custom`. `:root > *` matches Material's documented custom-color
   selector (higher specificity than plain `:root`). */
:root > * {
  --md-primary-fg-color: #0e7c66;
  --md-primary-fg-color--light: #0e7c66;
  --md-primary-fg-color--dark: #0a5c4c;
  --md-accent-fg-color: #0a5c4c;
}

/* Light scheme: warm paper background matching the landing page. */
[data-md-color-scheme="default"] {
  --md-default-bg-color: #fbfaf7;
  --md-typeset-color: #1c2733;
}

/* Dark scheme: keep Material's slate defaults, only the green accent carries over. */
[data-md-color-scheme="slate"] {
  --md-primary-fg-color: #0e7c66;
  --md-accent-fg-color: #12a184;
}
```

- [ ] **Step 7: Write `docs-site/docs/CNAME`**

```
docs.drugref.org
```

- [ ] **Step 8: Write `docs-site/docs/index.md` (Overview)**

```markdown
# drugref.org — an open drug-information commons

drugref.org is a public-good drug-information service: a registry of active drug
**moieties**, each with an **immortal identity** and transparent, append-only links
to the world's open drug vocabularies. Any EHR, pharmacy system, prescribing tool,
or research pipeline can consume it on equal footing — free software, free data,
reproducible from source.

!!! info "Status — under active development"
    The global moiety spine, classification (MED-RT + MeSH) and the first
    interaction data are built; the public API is still to come. Follow progress
    [on GitHub](https://github.com/cairn-ehr/drugref).

## Why it exists

Reliable drug reference data — what a substance *is*, how it is classified, what it
interacts with — sits behind proprietary licences in most of the world. That locks
safe-prescribing support to whoever can pay, and locks health-IT projects into
vendors. drugref takes the opposite stance: the **identity and knowledge layer of
medicines is a commons**, built entirely from public-domain and openly licensed
sources, and published under licences that keep it open for everyone, forever.

## What it provides

- **Immortal substance identity** — every active moiety gets a stable UUID that never
  changes and is never reused, minted deterministically so independent installations
  agree without coordination.
- **Cross-vocabulary claims** — append-only, auditable links from each moiety to UNII,
  InChIKey, CAS, ChEBI, RxNorm and more. Corrections supersede; they never overwrite
  history.
- **Classification** — a source-neutral drug-class registry populated from MED-RT
  (mechanism of action, physiologic effect) and MeSH pharmacologic actions.
- **Interaction groundwork** — class-level contraindication data from MED-RT, expanded
  to candidate drug–drug pairs; the advisory foundation for interaction checking,
  clearly tiered by evidence.

## Two tiers

drugref is built in two tiers: the **global tier** (jurisdiction-independent substance
identity, chemistry, classes, interactions — being built first, in this repository)
and a later **local tier** for country-specific packaging, product, and subsidy data.

## How to read these docs

- **[Architecture](architecture/index.md)** — how drugref is built.
- **[Design decisions](decisions/index.md)** — why it is built that way (the decisions
  that currently stand).
- **[Principles](principles/index.md)** — the invariants everything else follows from.
- **[Data sources & licensing](sources/index.md)** — where the data comes from and the
  licence rules that govern it.
- **[Roadmap](roadmap/index.md)** — what is done and what is next.
```

- [ ] **Step 9: Run the strict build to verify the scaffold**

Run: `uv run --group docs mkdocs build --strict -f docs-site/mkdocs.yml`
Expected: PASS — "Documentation built" with no warnings. (If it warns that a nav file is missing, a later-task page path is wrong; only `index.md` should be referenced yet.)

- [ ] **Step 10: Commit**

```bash
git add docs-site/mkdocs.yml docs-site/docs/index.md docs-site/docs/CNAME \
        docs-site/docs/assets/extra.css docs-site/docs/assets/drugref_logo.png \
        pyproject.toml uv.lock .gitignore
git commit -m "$(cat <<'EOF'
feat(docs): scaffold the MkDocs Material site + Overview page

Self-contained docs-site/ project (brand-themed, strict build, CNAME for
docs.drugref.org) with the Overview home page. mkdocs-material added as a
uv `docs` dependency group; build output gitignored.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 2: Architecture page

**Files:**
- Create: `docs-site/docs/architecture/index.md`
- Modify: `docs-site/mkdocs.yml` (append nav entry)

**Interfaces:**
- Consumes: the scaffold + nav from Task 1.
- Produces: `architecture/index.md`, linked from nav and from `index.md`. Decision records (Task 3) link back to it.

- [ ] **Step 1: Write `docs-site/docs/architecture/index.md`**

```markdown
# Architecture

drugref is an **advisory, fit-for-purpose** service: ingest and normalisation are
written in plain Python (fast iteration over brittle upstream feeds), but **data
integrity is enforced in the database, not in application code** — constraints,
triggers, and (later) row-level security. The substrate is Python 3.12 + `uv`,
`psycopg` v3, and PostgreSQL ≥ 18.

## Two orthogonal structures

drugref models medicines as two independent graphs that a moiety sits in at once:

```mermaid
graph TD
  subgraph Composition tree — "is made of", downward
    M[Active moiety] --> S[Specific substance<br/>salt / ester / hydrate]
    S --> C[Clinical drug<br/>moiety + strength + form]
    C --> P[Product<br/>brand / pack — local tier]
  end
  subgraph Classification DAG — "is a kind of"
    K1[Class] --> K2[Class]
    M -. member of many .-> K1
  end
```

1. **Composition tree** (*is-made-of*): active moiety → specific substance → clinical
   drug → product. Product is the local tier.
2. **Classification DAG** (*is-a-kind-of*): `class ⊂ class`; a moiety belongs to many
   classes across several axes (chemical / mechanism / therapeutic) — many-to-many, a
   link, never a parent foreign key.

Curated knowledge attaches to nodes in **either** structure and **inherits along the
edges** (down the tree, up through a moiety's classes) — curate once, apply widely.

## The hybrid store

Two kinds of data with opposite update semantics live side by side:

- **Rebuildable projections** — everything ingested from a public feed. Drop-and-rebuild
  per source, version-pinned, provenance-tagged via `ingest_run`. A new upstream release
  cleanly replaces its own projection.
- **An append-only, signed overlay** — curated knowledge (the durable value-add, e.g.
  interaction severity and management). Never overwritten.

See the decision record on the [hybrid store](../decisions/hybrid-store.md).

## Immortal identity & append-only claims

Every active moiety has its own **immortal `moiety_uuid`** — a `UUIDv5` derived
deterministically from its UNII and pinned forever. External identifiers attach as
**append-only claims**; corrections *supersede* rather than overwrite. Two decision
records cover this: [immortal moiety identity](../decisions/immortal-moiety-identity.md)
and [append-only claims](../decisions/append-only-claims.md).

## Classification & membership

Drug classes come from **MED-RT** (mechanism of action, physiologic effect, therapeutic
class, and more) and **MeSH pharmacologic actions**, in a single source-neutral class
registry. A moiety joins a class through identifier claims drugref already holds
(`RXNORM_IN` for MED-RT; UNII / CAS for MeSH). Class edges are **rebuildable
projections**, deliberately outside the append-only floor.

## Where the parts live

Ingest parsers are **pure and streaming** (no database access); orchestrators
(`ingest/*_run.py`) own the transaction and are the only writers. The code lives under
`src/drugref/` (`ids`, `claims`, `classes`, `db`, and `ingest/*`). For the full
derivation of any slice, see the design specs in the
[repository](https://github.com/cairn-ehr/drugref/tree/main/docs/superpowers/specs).
```

- [ ] **Step 2: Append the Architecture nav entry in `docs-site/mkdocs.yml`**

Change:
```yaml
nav:
  - Overview: index.md
```
to:
```yaml
nav:
  - Overview: index.md
  - Architecture: architecture/index.md
```

- [ ] **Step 3: Run the strict build**

Run: `uv run --group docs mkdocs build --strict -f docs-site/mkdocs.yml`
Expected: PASS. (Links to `../decisions/*.md` do not exist yet — because `strict` would fail on a broken link, this step will FAIL until Task 3 lands. See Step 4.)

- [ ] **Step 4: Resolve the forward-link ordering**

The Architecture page links to decision records created in Task 3, and `strict` fails on links to non-existent files. **Do Task 3 immediately after writing this page, then run the strict build once at the end of Task 3 covering both.** Practically: complete Steps 1–2 here, then proceed to Task 3; the first green `--strict` build is at Task 3 Step 4. Commit both together at the end of Task 3.

> Rationale: the Architecture page and the four decision records form one mutually-linked unit. Splitting the commit would leave an intermediate state that cannot pass `--strict`. This is the one place two tasks share a commit; every other task commits independently.

---

### Task 3: Design decisions section (index + four records)

**Files:**
- Create: `docs-site/docs/decisions/index.md`
- Create: `docs-site/docs/decisions/immortal-moiety-identity.md`
- Create: `docs-site/docs/decisions/append-only-claims.md`
- Create: `docs-site/docs/decisions/hybrid-store.md`
- Create: `docs-site/docs/decisions/licensing-is-a-blocker.md`
- Modify: `docs-site/mkdocs.yml` (append the Design decisions nav block)

**Interfaces:**
- Consumes: the scaffold (Task 1) and the Architecture page (Task 2, which links here).
- Produces: four decision-record pages at stable paths that the Architecture, Principles, and Sources pages link to.

- [ ] **Step 1: Write `docs-site/docs/decisions/index.md`**

```markdown
# Design decisions

These records capture the design decisions that **currently stand** behind drugref —
what was chosen, and why.

They are shaped like ADRs (Architecture Decision Records) but are deliberately
**living, not immutable**:

- Each record describes a decision *as it stands today*.
- When a decision changes, its record is **revised in place** — not appended to.
- A decision that is **reversed is removed**, not kept as a tombstone.

There is therefore no "superseded by" chain and no status graveyard here. The full
history is never lost — it remains in the git log and in the dated design specs under
`docs/superpowers/specs/`. This section always reflects *current* truth.

## Record template

```text
# <Decision title>

**Status:** Active            (or "Under review")
**Last reviewed:** YYYY-MM-DD
**Applies to:** <slice / subsystem>
**Full derivation:** <link to the relevant design spec>

## Context      — the forces / the problem this decision answers
## Decision     — what stands today
## Consequences — trade-offs, what it enables, what it costs
## Related      — other decision records, principles, code
```

## Current decisions

- [Immortal moiety identity](immortal-moiety-identity.md) — every moiety gets its own
  UUID, never keyed on a name.
- [Append-only claims](append-only-claims.md) — corrections supersede; history is never
  overwritten.
- [The hybrid store](hybrid-store.md) — rebuildable projections beside an append-only
  signed overlay.
- [Licensing is a blocker](licensing-is-a-blocker.md) — AGPL-compatible sources only,
  checked before adding.
```

- [ ] **Step 2: Write the four decision records**

`docs-site/docs/decisions/immortal-moiety-identity.md`:
```markdown
# Immortal moiety identity via UUIDv5-on-UNII

**Status:** Active
**Last reviewed:** 2026-07-25
**Applies to:** Slice 1 — the active-moiety identity spine
**Full derivation:** [slice-1 moiety-spine design spec](https://github.com/cairn-ehr/drugref/blob/main/docs/superpowers/specs/2026-07-23-drugref-global-moiety-spine-design.md)

## Context

External drug identifiers churn: RxNorm concepts merge and split, and display names
differ by jurisdiction (acetaminophen vs paracetamol). Keying a moiety's identity on
any external identifier or on a name means identity breaks whenever the upstream source
changes — and independent installations would disagree about what "the same drug" is.

## Decision

Every active moiety gets its **own immortal `moiety_uuid`** — a `UUIDv5` minted
deterministically from the moiety's **UNII** at first sighting, then **pinned forever**.
Identity is never keyed on a name. Because the UUID is derived deterministically from a
public identifier, independent instances mint the same UUID for the same substance with
no central coordination.

## Consequences

- External identifiers can attach and change freely as append-only claims without ever
  re-keying the moiety.
- Independent drugref installations converge on the same identities offline.
- **Cost:** a change to the *UNII itself* is the one churn the UUID does not survive
  ([issue #3](https://github.com/cairn-ehr/drugref/issues/3) — a structural re-key by
  InChIKey is deferred). A row with a blank/absent UNII must be refused, or unrelated
  drugs collapse onto one shared UUID (closed in the foundation review).

## Related

- [Append-only claims](append-only-claims.md)
- [Principles](../principles/index.md)
```

`docs-site/docs/decisions/append-only-claims.md`:
```markdown
# Append-only external-identifier claims

**Status:** Active
**Last reviewed:** 2026-07-25
**Applies to:** Slice 1 + the foundation review (`db/005`)
**Full derivation:** [slice-1 moiety-spine design spec](https://github.com/cairn-ehr/drugref/blob/main/docs/superpowers/specs/2026-07-23-drugref-global-moiety-spine-design.md)

## Context

Reference data is corrected over time. A destructive `UPDATE` or `DELETE` loses
provenance and audit history, and can silently change what a downstream consumer saw
yesterday. drugref doubles as an identifier cross-walk, so its history must be stable
and inspectable.

## Decision

External-identifier claims (UNII, INN, RXNORM_IN, CAS, PUBCHEM_CID, INCHIKEY, ChEBI…)
are **append-only**. A correction **inserts a new claim** and points the old claim's
`superseded_by` at it; values are never overwritten or deleted. Database triggers
enforce this floor. Uniqueness is **partial over live claims only**, so a value that was
reverted upstream can be re-asserted later.

## Consequences

- Full, auditable history; consumers can always see what was claimed and when.
- Supersession is **one-way, same-moiety, and strictly forward** — cycles are
  unrepresentable ([issue #4](https://github.com/cairn-ehr/drugref/issues/4), closed in
  `db/005`).
- **Cost:** every read must filter `superseded_by IS NULL`. `TRUNCATE` and the
  table-owning role remain bypasses of the row-level floor
  ([issue #2](https://github.com/cairn-ehr/drugref/issues/2) — RLS + privilege
  separation later).

## Related

- [Immortal moiety identity](immortal-moiety-identity.md)
- [The hybrid store](hybrid-store.md)
- [Principles](../principles/index.md)
```

`docs-site/docs/decisions/hybrid-store.md`:
```markdown
# The hybrid store: rebuildable projections vs the signed overlay

**Status:** Active
**Last reviewed:** 2026-07-25
**Applies to:** The whole architecture (slices 2–5)
**Full derivation:** [the design specs](https://github.com/cairn-ehr/drugref/tree/main/docs/superpowers/specs) and `docs/ROADMAP.md`

## Context

drugref holds two kinds of data with opposite update semantics: machine-ingested public
feeds that are regenerated wholesale each upstream release, and hand-curated knowledge
that is the durable, high-value part and must never be lost.

## Decision

Store them separately:

- **Rebuildable projections** for ingested feeds — drop-and-rebuild per source,
  version-pinned, provenance-tagged via `ingest_run`.
- **An append-only, signed overlay** for curated knowledge.

The overlay attaches to nodes in **either** the composition tree or the classification
DAG and **inherits along the edges**, so knowledge is curated once and applies widely.

## Consequences

- A new upstream release cleanly replaces its projection without touching curation.
- Curation entered once propagates along the graph — the central curation-economy lever,
  especially for class-level interactions.
- **Cost:** the two halves must stay clearly separated, and every per-source rebuild must
  key on `ingest_run.source` so one source's rebuild never deletes another's rows.

## Related

- [Append-only claims](append-only-claims.md)
- [Architecture](../architecture/index.md)
- [Principles](../principles/index.md)
```

`docs-site/docs/decisions/licensing-is-a-blocker.md`:
```markdown
# Licensing is a blocker, not a cleanup item

**Status:** Active
**Last reviewed:** 2026-07-25
**Applies to:** Every slice — code and every bundled data source
**Full derivation:** the project's coding rules (`CLAUDE.md` rule 6) and the per-slice licence gates in each design spec

## Context

An open commons is worthless if a bundled source's licence forbids redistribution,
commercial use, or derivatives. Discovering a licence problem *after* building on a
source is expensive and can poison everything downstream of it.

## Decision

All code is **AGPL-3.0**. Every dependency and every bundled reference-data source must
be **AGPL-3.0-compatible, checked *before* adding**. Licence-encumbered sources attach
only as **node-local, separately-licensed plug-ins** — never bundled into the commons.

## Consequences

- Sources are sequenced by **licence-cleanliness, not coverage**. Public-domain and
  openly licensed sources (UNII, ChEBI, MED-RT, MeSH, RxNorm) are bundled; encumbered
  ones (ATC, SNOMED/AMT, ICD-10-AM, commercial DrugBank…) are not.
- National and proprietary data stays usable **per node**, without contaminating the
  shared commons.
- **Cost:** some high-coverage sources are simply unavailable in the bundled commons
  tier — a deliberate trade of coverage for freedom.

## Related

- [Data sources & licensing](../sources/index.md)
- [Principles](../principles/index.md)
```

- [ ] **Step 3: Append the Design decisions nav block in `docs-site/mkdocs.yml`**

Change:
```yaml
  - Architecture: architecture/index.md
```
to:
```yaml
  - Architecture: architecture/index.md
  - Design decisions:
      - decisions/index.md
      - Immortal moiety identity: decisions/immortal-moiety-identity.md
      - Append-only claims: decisions/append-only-claims.md
      - The hybrid store: decisions/hybrid-store.md
      - Licensing is a blocker: decisions/licensing-is-a-blocker.md
```

- [ ] **Step 4: Run the strict build (covers Task 2 + Task 3)**

Run: `uv run --group docs mkdocs build --strict -f docs-site/mkdocs.yml`
Expected: PASS — the Architecture page's `../decisions/*.md` links now resolve. Note the decision records link to `../principles/index.md` and `../sources/index.md`, which do **not** exist yet, so this will still FAIL. Create the two link *targets* now as minimal placeholders is **not** wanted; instead these cross-links are to pages built in Tasks 4–5. To keep `--strict` green here, temporarily the decision records must not link forward to unbuilt pages.

- [ ] **Step 5: Keep cross-links valid — build Principles and Sources before the strict gate**

The decision records link forward to `principles/index.md` (Task 4) and `sources/index.md` (Task 5). Under `--strict` those are broken until built. **Therefore run the first green strict build at the end of Task 5**, and commit Tasks 2–5 together there. Complete Steps 1–3 here, then proceed to Task 4.

> This extends the Task 2 rationale: Architecture, the four decision records, Principles, and Sources are one densely cross-linked cluster. They are written as Tasks 2→5 and share a single strict build + commit at the end of Task 5. Roadmap (Task 6) and the stubs (Task 7) link only backward, so they commit independently again.

---

### Task 4: Principles page

**Files:**
- Create: `docs-site/docs/principles/index.md`
- Modify: `docs-site/mkdocs.yml` (append nav entry)

**Interfaces:**
- Consumes: the scaffold (Task 1); linked to by the decision records (Task 3).
- Produces: `principles/index.md`.

- [ ] **Step 1: Write `docs-site/docs/principles/index.md`**

```markdown
# Principles

The invariants everything else in drugref follows from. The [design
decisions](../decisions/index.md) are specific choices; these are the commitments those
choices serve.

## The identity & knowledge layer of medicines is a commons

Built entirely from public-domain and openly licensed sources, published under licences
that keep it open forever. The value drugref adds is **quality control — who may assert —
not access**: the data ships paywall-free under copyleft.

## Advisory, never authoritative

drugref is **advisory reference data**. It informs clinical software; no system is ever
required to route its records through it, and it never sits on Cairn's signed inter-node
wire core. A licence-encumbered source can therefore attach to one node without
compromising interoperability.

## The steward uses only the public API

drugref is stewarded alongside the [Cairn EHR](https://cairn-ehr.org) project, but Cairn
is just its first client, on the **same public-API footing as everyone else**. The
steward has no privileged access.

## Immortal identity, never keyed on a name

Every moiety owns an immortal UUID minted deterministically and pinned forever. Identity
is never derived from a display name. See [immortal moiety
identity](../decisions/immortal-moiety-identity.md).

## Append-only history — corrections supersede, never overwrite

Claims are append-only; a correction supersedes its predecessor. History is stable and
auditable. See [append-only claims](../decisions/append-only-claims.md).

## Licensing is a blocker, not a cleanup item

Every dependency and bundled source is checked for AGPL-compatibility **before** it is
added. See [licensing is a blocker](../decisions/licensing-is-a-blocker.md).

## Integrity is enforced in the database, not application code

Ingest is fit-for-purpose Python, but identity and append-only integrity live in
PostgreSQL constraints and triggers — the guarantees hold no matter what writes to the
database.

## Reproducible from source

Every fact traces to an openly licensed upstream release, and the entire database can be
rebuilt from those sources by anyone.

## Coverage gaps are published, not hidden

Where drugref lacks data — an unclassified moiety, a contraindication naming a class no
drug is filed under — the gap is published as a queryable open-question register that
shrinks visibly as coverage improves, rather than being silently absent.
```

- [ ] **Step 2: Append the Principles nav entry in `docs-site/mkdocs.yml`**

Change:
```yaml
      - Licensing is a blocker: decisions/licensing-is-a-blocker.md
```
to:
```yaml
      - Licensing is a blocker: decisions/licensing-is-a-blocker.md
  - Principles: principles/index.md
```

- [ ] **Step 3: Proceed to Task 5 before the strict gate**

The decision records still link forward to `sources/index.md` (Task 5). Do not run the strict gate yet; continue to Task 5.

---

### Task 5: Data sources & licensing page

**Files:**
- Create: `docs-site/docs/sources/index.md`
- Modify: `docs-site/mkdocs.yml` (append nav entry)

**Interfaces:**
- Consumes: the scaffold (Task 1); linked to by the licensing decision record (Task 3).
- Produces: `sources/index.md`. This is the last page in the cross-linked cluster, so the first green strict build + the shared commit for Tasks 2–5 happen here.

- [ ] **Step 1: Write `docs-site/docs/sources/index.md`**

```markdown
# Data sources & licensing

Every fact in drugref traces to a public-domain or openly licensed upstream release, and
the entire database can be rebuilt from those sources by anyone. Only
**AGPL-compatible** sources are ever bundled — see the decision record on [licensing as a
blocker](../decisions/licensing-is-a-blocker.md).

## Bundled sources

| Source | What it provides | Licence |
| --- | --- | --- |
| **FDA GSRS / UNII** | The global substance-registration backbone; the identity anchor | Public domain |
| **ChEBI** | Chemical entities of biological interest; chemistry + cross-references | CC BY 4.0 |
| **MED-RT** | Mechanisms of action, physiologic effects, therapeutic classes, contraindications | Public domain (US NLM / VA) |
| **MeSH** | Pharmacologic actions and descriptors | Public domain / NLM terms |
| **RxNorm** | Normalised drug names and codes | Openly redistributable subset |

Upstream attributions are recorded in the repository's
[`NOTICE`](https://github.com/cairn-ehr/drugref/blob/main/NOTICE) file.

## The licence rule

Licensing is a hard rule, not an afterthought. Every dependency and every bundled
reference-data source must be AGPL-3.0-compatible, **checked before it is added**.
Licence-encumbered national or commercial sources — ATC, SNOMED CT / AMT, ICD-10-AM,
eTG, AMH, commercial DrugBank — attach only as **node-local, separately-licensed
plug-ins**. They never contaminate the commons.

## Notable exclusion

**ATC** (the WHO Anatomical Therapeutic Chemical classification) is **not** bundled: its
licence is NonCommercial and NoDerivatives, incompatible with an openly redistributable
commons.

## Documentation licence

The prose on this documentation site is licensed **CC BY-SA 4.0**, distinct from the
project's code, which is **AGPL-3.0**.
```

- [ ] **Step 2: Append the Data sources nav entry in `docs-site/mkdocs.yml`**

Change:
```yaml
  - Principles: principles/index.md
```
to:
```yaml
  - Principles: principles/index.md
  - Data sources & licensing: sources/index.md
```

- [ ] **Step 3: Run the strict build (first green build; covers Tasks 2–5)**

Run: `uv run --group docs mkdocs build --strict -f docs-site/mkdocs.yml`
Expected: PASS with zero warnings — every cross-link in the Architecture / decisions / Principles / Sources cluster now resolves.

- [ ] **Step 4: Commit Tasks 2–5 together**

```bash
git add docs-site/docs/architecture docs-site/docs/decisions \
        docs-site/docs/principles docs-site/docs/sources docs-site/mkdocs.yml
git commit -m "$(cat <<'EOF'
feat(docs): add Architecture, Design decisions, Principles, Sources

The core cross-linked content cluster: the architecture overview, four
living decision records (immortal identity, append-only claims, the hybrid
store, licensing-as-blocker), the principles page, and the data-sources &
licensing page. Committed together because they link to each other and a
strict build fails on any dangling link.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 6: Roadmap page

**Files:**
- Create: `docs-site/docs/roadmap/index.md`
- Modify: `docs-site/mkdocs.yml` (append nav entry)

**Interfaces:**
- Consumes: the scaffold (Task 1). Links only backward, so it builds and commits independently.
- Produces: `roadmap/index.md`.

- [ ] **Step 1: Write `docs-site/docs/roadmap/index.md`**

```markdown
# Roadmap

drugref's **global tier** is built bottom-up — substance identity → chemistry → classes
→ interactions — followed by the public API and the local (country-specific) tier. It is
an advisory reference-data service and never sits on Cairn's signed inter-node wire core.

This is a reader-friendly summary; the working roadmap lives in the
[repository](https://github.com/cairn-ehr/drugref/blob/main/docs/ROADMAP.md).

## Built so far

- **Identity spine** — active-moiety registry with immortal UUIDs and append-only
  cross-reference claims, seeded from UNII / ChEBI / INN / RxNorm.
- **Classification** — a source-neutral drug-class registry with MED-RT (mechanism of
  action, physiologic effect, therapeutic class, and more) and MeSH pharmacologic
  actions, plus moiety↔class membership.
- **First interaction data** — MED-RT mechanism/effect contraindications as a rebuildable
  projection, expanded to candidate drug–drug pairs at read time. Candidate tier only —
  nothing here auto-fires a prescriber alert.
- **Open-question registry** — coverage gaps published as a queryable register that
  shrinks as coverage improves.

## Next

- **MeSH-keyed contraindications & indications** — drug–disease contraindications and
  indications, once MeSH disease/chemical descriptors are ingested.
- **Composition tree** — specific substances (salts / esters / hydrates), then clinical
  drugs (moiety + strength + form).
- **The curated overlay (the moat)** — an append-only, signed layer adding severity,
  mechanism, management, and evidence grading on top of the candidate interaction rows.
- **Public HTTP API** — the co-equal-consumer interface; any EHR / pharmacy / app on the
  same footing.
- **Local tier** — country-specific packaging and pricing (Australia first: PBS + TGA
  ARTG), with nationally-licensed terminologies attached per node.
```

- [ ] **Step 2: Append the Roadmap nav entry in `docs-site/mkdocs.yml`**

Change:
```yaml
  - Data sources & licensing: sources/index.md
```
to:
```yaml
  - Data sources & licensing: sources/index.md
  - Roadmap: roadmap/index.md
```

- [ ] **Step 3: Run the strict build**

Run: `uv run --group docs mkdocs build --strict -f docs-site/mkdocs.yml`
Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add docs-site/docs/roadmap docs-site/mkdocs.yml
git commit -m "$(cat <<'EOF'
feat(docs): add the Roadmap page

A reader-friendly built-so-far / next summary derived from docs/ROADMAP.md.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 7: Developer guide + User manual stubs

**Files:**
- Create: `docs-site/docs/developer/index.md`
- Create: `docs-site/docs/user-manual/index.md`
- Modify: `docs-site/mkdocs.yml` (append two nav entries)

**Interfaces:**
- Consumes: the scaffold (Task 1).
- Produces: two clearly-marked "coming" pages that reserve their nav slots.

- [ ] **Step 1: Write `docs-site/docs/developer/index.md`**

```markdown
# Developer guide

!!! note "Coming soon"
    This section will cover local setup, running the ingest pipelines, the test suite,
    and the migration workflow. It is a placeholder for now — until then, the
    [repository README](https://github.com/cairn-ehr/drugref/blob/main/README.md) and the
    design specs under
    [`docs/superpowers/specs/`](https://github.com/cairn-ehr/drugref/tree/main/docs/superpowers/specs)
    are the starting points for contributors.
```

- [ ] **Step 2: Write `docs-site/docs/user-manual/index.md`**

```markdown
# User manual

!!! note "Coming soon"
    A guide for consumers of the drugref service will land alongside the **public HTTP
    API** (see the [Roadmap](../roadmap/index.md)). Until the API exists there is no
    consumer surface to document; this page reserves the place for it.
```

- [ ] **Step 3: Append the two stub nav entries in `docs-site/mkdocs.yml`**

Change:
```yaml
  - Roadmap: roadmap/index.md
```
to:
```yaml
  - Roadmap: roadmap/index.md
  - Developer guide: developer/index.md
  - User manual: user-manual/index.md
```

- [ ] **Step 4: Run the strict build**

Run: `uv run --group docs mkdocs build --strict -f docs-site/mkdocs.yml`
Expected: PASS. The full eight-section nav now resolves.

- [ ] **Step 5: Commit**

```bash
git add docs-site/docs/developer docs-site/docs/user-manual docs-site/mkdocs.yml
git commit -m "$(cat <<'EOF'
feat(docs): add Developer guide and User manual stubs

Reserve the two growth-section nav slots with clearly-marked "coming"
pages; the user manual waits on the public API.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 8: Build-and-deploy workflow

**Files:**
- Create: `.github/workflows/docs.yml`

**Interfaces:**
- Consumes: the `docs-site/` project (Tasks 1–7) and the `docs` dependency group (Task 1).
- Produces: CI that strict-builds every docs PR and deploys `main` to GitHub Pages.

- [ ] **Step 1: Write `.github/workflows/docs.yml`**

```yaml
name: docs

# Build the docs on every PR that touches them (strict — the gate), and additionally
# deploy to GitHub Pages on push to main. Path-filtered so unrelated changes don't run
# this workflow.
on:
  push:
    branches: [main]
    paths:
      - "docs-site/**"
      - ".github/workflows/docs.yml"
  pull_request:
    paths:
      - "docs-site/**"
      - ".github/workflows/docs.yml"
  workflow_dispatch:

permissions:
  contents: read

jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - name: Install uv
        uses: astral-sh/setup-uv@v5

      - name: Sync docs dependencies
        run: uv sync --group docs

      - name: Build the site (strict)
        run: uv run --group docs mkdocs build --strict -f docs-site/mkdocs.yml

      - name: Upload the Pages artifact
        if: github.ref == 'refs/heads/main'
        uses: actions/upload-pages-artifact@v3
        with:
          path: docs-site/site

  deploy:
    # Deploy only from main; PRs get the strict build gate above and stop there.
    if: github.ref == 'refs/heads/main'
    needs: build
    runs-on: ubuntu-latest
    permissions:
      pages: write
      id-token: write
    environment:
      name: github-pages
      url: ${{ steps.deployment.outputs.page_url }}
    steps:
      - name: Deploy to GitHub Pages
        id: deployment
        uses: actions/deploy-pages@v4
```

- [ ] **Step 2: Validate the workflow builds the site the same way locally**

Run: `uv run --group docs mkdocs build --strict -f docs-site/mkdocs.yml`
Expected: PASS — this is exactly the command the `build` job runs, so a local pass predicts the CI build step.

- [ ] **Step 3: Commit**

```bash
git add .github/workflows/docs.yml
git commit -m "$(cat <<'EOF'
ci(docs): strict-build docs PRs, deploy main to GitHub Pages

PRs touching docs-site/ get a strict mkdocs build (broken links fail);
pushes to main additionally publish to the github-pages environment.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
EOF
)"
```

- [ ] **Step 4: Record the manual operator steps (for the PR description / maintainer)**

These cannot be done from code — note them in the PR body:

1. Repo **Settings → Pages → Source = GitHub Actions** (enables the Actions deploy).
2. **Cloudflare DNS:** add `docs.drugref.org` → `CNAME cairn-ehr.github.io`, **DNS-only
   (grey cloud)** so GitHub terminates TLS. (Does not conflict with `docs.cairn-ehr.org`
   — GitHub routes by Host header; the shared `cairn-ehr.github.io` target is the org's
   Pages edge address, not Cairn's site.)
3. **Recommended:** add `drugref.org` to the org's verified domains to avoid
   domain-takeover warnings.

Until 1–2 are done the workflow still deploys to the default
`cairn-ehr.github.io/drugref` Pages URL; the custom domain resolves once DNS + the Pages
source are set.

---

### Task 9: Re-point the landing page "Design documents" button

**Files:**
- Modify: `site/index.html` (the button `href` at line ~182)

**Interfaces:**
- Consumes: the deployed docs site (Task 8) — logically; the link works once DNS resolves.
- Produces: the landing page pointing readers at `docs.drugref.org`.

- [ ] **Step 1: Change the button href in `site/index.html`**

Replace:
```html
        <a class="btn btn-ghost" href="https://github.com/cairn-ehr/drugref/tree/main/docs">Design documents</a>
```
with:
```html
        <a class="btn btn-ghost" href="https://docs.drugref.org">Design documents</a>
```

- [ ] **Step 2: Verify the change**

Run: `grep -n 'Design documents' site/index.html`
Expected: the line now shows `href="https://docs.drugref.org"`; the "Source code on GitHub" button (previous line) is unchanged.

- [ ] **Step 3: Commit**

```bash
git add site/index.html
git commit -m "$(cat <<'EOF'
feat(site): point "Design documents" at docs.drugref.org

The button previously linked to the raw GitHub docs/ folder (developer-only);
it now points at the published documentation site.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
EOF
)"
```

> Note: the landing page is a Cloudflare static page sourced from `site/`; this change
> goes live when `site/` is next deployed by its existing (out-of-scope) mechanism.

---

### Task 10: Pointer in the project's stable context

**Files:**
- Modify: `CLAUDE.md` (one line under session state guidance)
- Modify: `docs/HANDOVER.md` ("Repo facts" section)

**Interfaces:**
- Consumes: the whole delivered site.
- Produces: a durable pointer so future sessions know the published docs exist and how the living-decision-record norm works.

- [ ] **Step 1: Add a pointer line to `CLAUDE.md`**

In the "Starting a session" area (near the reference to `docs/HANDOVER.md` and the specs),
add one line:

```markdown
Public documentation is published from `docs-site/` (MkDocs Material) to
`docs.drugref.org`; its **Design decisions** section holds *living* records (only
decisions that currently stand — revised in place, reversed ones removed), distinct from
the immutable per-slice specs under `docs/superpowers/specs/`.
```

- [ ] **Step 2: Add a pointer to `docs/HANDOVER.md` "Repo facts"**

Under the "Repo facts" bullet list, add:

```markdown
- Public docs site: `docs-site/` (MkDocs Material) → `docs.drugref.org`, deployed by
  `.github/workflows/docs.yml`. Living decision records live in
  `docs-site/docs/decisions/`; keep them current (revise in place, remove reversed
  decisions). The internal specs/HANDOVER/ROADMAP are **not** published.
```

- [ ] **Step 3: Verify no broken references and the site still builds**

Run: `uv run --group docs mkdocs build --strict -f docs-site/mkdocs.yml`
Expected: PASS (this task doesn't change `docs-site/`, but the build confirms nothing regressed).

- [ ] **Step 4: Commit**

```bash
git add CLAUDE.md docs/HANDOVER.md
git commit -m "$(cat <<'EOF'
docs: point CLAUDE.md and HANDOVER at the published docs site

Record where the public docs live and the living-decision-record norm so
future sessions maintain them.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Self-Review

**Spec coverage** (against the design spec's §11 deliverables):

1. MkDocs project (mkdocs.yml, docs tree, CNAME, extra.css, logo) → Task 1. ✅
2. Seed content (Overview, Architecture, 4 decision records + index, Principles, Sources, Roadmap, dev/user stubs) → Tasks 1–7. ✅
3. `.github/workflows/docs.yml` (PR strict gate + main deploy) → Task 8. ✅
4. `docs` dependency group in `pyproject.toml` → Task 1 Step 1. ✅
5. `.gitignore` build-output ignore → Task 1 Step 3. ✅
6. Landing-page href change → Task 9. ✅
7. Docs-prose licence CC BY-SA 4.0 recorded (site footer via `copyright`, and the Sources page) → Task 1 Step 5 + Task 5 Step 1. ✅
8. CLAUDE.md/HANDOVER pointer → Task 10. ✅
- §5 branding (palette from `site/index.html`) → Task 1 Steps 5–6. ✅
- §6 manual operator steps documented → Task 8 Step 4. ✅
- §7 verification via `mkdocs build --strict` → Global Constraints + every task. ✅

**Placeholder scan:** No "TBD/TODO/handle appropriately". The two "coming soon" pages (Task 7) are intentional, finished stub *content*, not plan placeholders. Every config file and every page is given in full.

**Type/name consistency:** Page paths are used identically everywhere — `architecture/index.md`, `decisions/{index,immortal-moiety-identity,append-only-claims,hybrid-store,licensing-is-a-blocker}.md`, `principles/index.md`, `sources/index.md`, `roadmap/index.md`, `developer/index.md`, `user-manual/index.md`. Nav labels match the fixed order. The build command `uv run --group docs mkdocs build --strict -f docs-site/mkdocs.yml` is identical in every task and in the workflow. The `docs` dependency group name matches between `pyproject.toml` (Task 1) and `uv sync --group docs` (Task 1 Step 2, Task 8 Step 1).

**Cross-link ordering note:** Tasks 2–5 form one cross-linked cluster and share the first green `--strict` build + a single commit at Task 5 Step 4 (a `--strict` build fails on any dangling link, so an intermediate commit couldn't pass the gate). This is called out explicitly in Tasks 2, 3, and 5. Tasks 6, 7, 9, 10 link only backward and each build+commit independently.
