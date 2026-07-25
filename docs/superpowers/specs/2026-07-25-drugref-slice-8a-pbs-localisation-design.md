# Design — drugref slice 8a: PBS localisation (the local tier's first attachment)

**Date:** 2026-07-25 · **Repo:** `github.com/cairn-ehr/drugref` · **Status:** design, pending implementation
plan. **Builds on:** the [slice-1 moiety-spine design](2026-07-23-drugref-global-moiety-spine-design.md)
(§5 own-immortal-UUID; the `INN` `identity_claim` this slice's bridge joins through) and the
[slice-2a MED-RT design](2026-07-23-drugref-slice-2a-medrt-classification-design.md) (the
rebuildable-projection discipline and per-source rebuild scoping this slice copies).

**Scope of change:** stand up drugref's **first local (jurisdiction-specific) tier** — a minimal Australian
**PBS** product layer that attaches to the global moiety spine — as a **node-local, separately-licensed
plug-in** that drugref **never bundles or redistributes**. Three new tables (`db/009`), a pure parser
(`ingest/pbs.py`), a single writer (`local.py`) and an orchestrator (`ingest/pbs_run.py`). This is
ROADMAP **Slice 8**'s first cut, deliberately pulled ahead of slices 3/4 to test **localisation as an
architectural pattern** while the content modelled stays deliberately thin.

**This is a spike with a measurement as its deliverable.** Its success criterion is not coverage but a
*number*: the measured moiety-match rate of a real PBS release, plus a queryable list of what did not match.
The value is proving three genuinely novel things — a name-only bridge across the missing composition-tree
levels, jurisdiction scoping in the schema, and structural quarantine of encumbered columns — at the
smallest code cost that can prove them.

**Out of scope (each tracked, none forgotten):** pricing (AEMP/DPMQ/premiums/fees), restriction *texts* and
indications/criteria, organisations/prescribers/programs beyond a code, TGA ARTG, the composition tree's
salt (slice 3) and clinical-drug (slice 4) levels, wiring unmatched components into the `open_question`
worklist, AU→INN legacy-name aliases, batch-commit ([#7](https://github.com/cairn-ehr/drugref/issues/7)),
and the HTTP API.

---

## 1. Licence gate (rule 7) — cleared for *node-local ingest*, NOT for redistribution

Rule 7 is a blocker, not a cleanup item, so this section is the spec's first and most consequential one. A
live-source investigation (July 2026) refuted **both halves** of the ROADMAP's standing assumption that
"PBS + TGA ARTG (both CC BY, redistributable)". The findings, and what each one forces:

**1.1 The PBS Schedule's open-licence status is UNCONFIRMED.** CC BY 3.0 AU is verified only for the PBS
**statistical** datasets published on `data.gov.au` by Services Australia (CKAN `license_id: cc-by`) — *not*
for the Schedule / API data mart this slice reads. The PBS website copyright page
(`https://www.pbs.gov.au/info/general/copyright`) reads **all rights reserved**, permitting reproduction
"for personal use as general reference material only", and the `data.pbs.gov.au` API pages carry no CC BY
statement.

**1.2 ATC codes are WHO-owned, NC + ND.** The PBS data dictionary sources `atc_code` from the WHO
Collaborating Centre index, whose terms state: *"Copying and distribution for commercial purposes is not
allowed. Changing or manipulating the material is not allowed."* Incompatible with AGPL redistribution.

**1.3 AMT / SNOMED CT-AU concept IDs are NCTS/SNOMED-affiliate-licensed.** Free to Australian affiliates,
but not openly licensed and not globally redistributable.

**1.4 TGA ARTG is not open either.** Its copyright page permits unaltered personal/internal reproduction and
states *"You must not use the whole or any part of the content on this website for any commercial
purposes."* So ARTG cannot supply a licence-clean ingredient identifier as a fallback.

**What the gate therefore permits, and what it forbids:**

- **PERMITTED (this slice):** drugref ships **AGPL-3.0 ingest code and schema**. An operator of an
  Australian node obtains PBS data themselves, under whatever terms bind *them*, and ingests it into *their*
  database. This is exactly the invariant CLAUDE.md rule 6 already states — *"Encumbered sources attach only
  as node-local, separately-licensed plug-ins, never bundled."*
- **FORBIDDEN (a standing gate, not a to-do):** drugref bundling, redistributing or publishing PBS data;
  and any ATC or AMT/SNOMED value entering **any** drugref table (§6 makes this structural).
- **The gate that must clear before any future redistribution:** written confirmation from the Department of
  Health (`HPP.Support@Health.gov.au`) that the Schedule/API data is CC BY. Until then, §1.1 stands.

**No `NOTICE` change is made by this slice**, because it redistributes nothing. A node-local operator's
obligations are theirs; the repo documents them (§8) rather than assuming them.

## 2. The finding that shapes the whole design: PBS carries no global chemical identifier

Verified against the PBS API V3 Data Dictionary (v3.6.5, 35 endpoints): **there is no UNII, no CAS number
and no InChIKey anywhere in the PBS data.** An item's active ingredient is identifiable only three ways:

| Route | Example fields | Licence |
|---|---|---|
| Plain drug-name text | `li_drug_name`, `drug_name`, `li_form` | open-ish (§1.1), **the only clean route** |
| ATC code | `atc_code`, `atc_description` | **WHO, NC+ND — encumbered** |
| AMT / SNOMED CT-AU concept | `amt_code`, `preferred_term` | **NCTS/SNOMED — encumbered** |

The two structured, high-precision keys are **exactly the two encumbered ones**. So the bridge from a PBS
product to a drugref moiety must be **name-based**, and the design's central question becomes how to do that
honestly rather than how to do it precisely. That is §5.

This also refutes the assumption that a local tier is mostly a schema exercise: the interesting risk is the
*join*, which is why the spike models thin content (§4) and invests in measurement (§7).

## 3. Where this sits — a rebuildable projection at the composition tree's leaf

The composition tree is **moiety → specific substance (salt) → clinical drug → product**. PBS lives at the
**product** leaf. Slices 3 and 4 do not exist yet, so this slice attaches products **directly to the
moiety**, across the gap.

That is a deliberate, reversible shortcut. `local_product_moiety` (§4) is an edge table, not a foreign key
on the product, so when slices 3/4 land the bridge's attachment point can be refined to the salt or
clinical-drug node **without re-keying any product** — the product UUID is a pure function of its PBS
identity (§4.1), not of what it points at.

**Hybrid-store placement: projection, not the signed moat.** PBS re-lists monthly (the Schedule is updated
on the first of each month, and items/prices churn), so by the project's own rule — *ingested feeds are
rebuildable projections; curated knowledge is the append-only signed overlay* — these tables are
**delete-and-rebuild per source**, `ingest_run`-tagged, and deliberately **not** behind slice 1's
append-only floor. There is no immortal identity here to protect: the table links two things that already
have immortal identities elsewhere, and a de-listed PBS item must be able to *disappear*, which an
insert-only merge can never express.

## 4. Schema (`db/009`)

Three tables. `db/009` also widens `db/005`'s `ingest_run.source` CHECK to admit `'PBS'` — the key every
per-source rebuild joins through, so it is constrained rather than free text.

```sql
drugref.local_product                 -- one row per PBS item instance
  local_product_uuid  uuid PK         -- uuid5(LOCAL_PRODUCT_NAMESPACE, 'AU:PBS:'||source_code)
  jurisdiction        text NOT NULL   -- 'AU'  (CHECK)
  source              text NOT NULL   -- 'PBS' (CHECK)
  source_code         text NOT NULL   -- the unique PBS item-instance row id (li_item_id)
  pbs_code            text            -- the recognisable PBS Item Code, an ATTRIBUTE (see 4.2)
  brand_name          text
  drug_name           text            -- li_drug_name: the licence-clean ingredient name
  form_strength       text            -- li_form / schedule_form
  program_code        text
  benefit_type_code   text            -- U/R/S/A: the restriction LEVEL only, never its text
  ingest_run          bigint NOT NULL REFERENCES drugref.ingest_run
  UNIQUE (jurisdiction, source, source_code)

drugref.local_product_moiety          -- the name-resolved bridge; fans out for combinations
  local_product_uuid  uuid NOT NULL REFERENCES drugref.local_product
  moiety_uuid         uuid NOT NULL REFERENCES drugref.substance_moiety
  component_name      text NOT NULL   -- the ingredient name that resolved
  match_method        text NOT NULL   -- 'exact' | 'salt_stripped'  (CHECK)
  ingest_run          bigint NOT NULL REFERENCES drugref.ingest_run
  PRIMARY KEY (local_product_uuid, moiety_uuid, component_name)

drugref.local_unmatched_ingredient    -- coverage made queryable, never a silent drop
  ingest_run          bigint NOT NULL REFERENCES drugref.ingest_run
  jurisdiction        text NOT NULL
  source              text NOT NULL
  source_code         text NOT NULL   -- which PBS item
  component_name      text NOT NULL   -- the ingredient name that matched no moiety
```

**4.1 Product identity is deterministic, like a class UUID and unlike a moiety UUID.**
`ids.mint_local_product_uuid(jurisdiction, source, code)` mints
`uuid5(LOCAL_PRODUCT_NAMESPACE, 'AU:PBS:<code>')` from a new per-level namespace (per `ids.py`'s existing
rule that per-level namespaces stop a moiety and a product derived from the same string colliding). It is
**re-derived on every ingest**, never pinned — which is what lets the projection be dropped and rebuilt
while every surviving product returns with the UUID it had before. Jurisdiction and source are part of the
key so a second jurisdiction's identically-numbered item can never collide.

**4.2 Why the key is the item *instance*, not `pbs_code`.** A PBS Item Code is a **prescribing rule**
(drug × form/strength × max quantity × repeats × restriction level × program), and one code covers **many
brands**. Keying on it would collapse every brand of a molecule into one row. The unique item-instance id
(`li_item_id`) preserves brand granularity; `pbs_code` is carried as a queryable attribute. The exact column
name is pinned against a real PBS API CSV during implementation, the same way the MED-RT and MeSH parsers
were pinned against real releases rather than assumed.

**4.3 `benefit_type_code` is a level, not a text.** U/R/S/A (Unrestricted / Restricted / Authority-Required
Streamlined / Authority Required) is a single closed-vocabulary code, cheap to carry and genuinely useful
for triage. Restriction *texts*, indications and criteria are option-② content and stay out of scope.

## 5. The name-bridge (the crux)

The only licence-clean join (§2), so it carries the spike. Normalisation is a **pure** pipeline in
`ingest/pbs.py` with no DB access; resolution runs against a `{name → [moiety]}` index built once per run
from the **existing** `classes.moieties_by_scheme(conn, 'INN')` — no new bridge data, exactly as MED-RT
(RxCUI) and MeSH PA (UNII/CAS) reuse slice-1 claims.

1. **Fold** — the same strip / lower-case / collapse-whitespace fold `gate._norm()` applies. Reused rather
   than re-implemented so the bridge's fold and the `INN` claim's stored fold cannot drift apart; `INN` is a
   deliberately lower-case display label (`ids._UPPERCASE_SCHEMES` excludes it), so the folds already agree.
   **Small refactor this slice makes:** `_norm` is private to `ingest/gate.py` and now has a second consumer,
   so it is promoted to a public `normalise_name()` in a shared module (`ids.py`, beside the other
   canonicalisation helpers `canonical_source` / `canonical_claim_value`, which it is a sibling of), with
   `gate` delegating. Reaching into another module's private name would leave the two folds free to diverge
   silently — the precise failure `canonical_source` exists to prevent for authority names.
2. **Split combinations** — on `" + "` and `" with "`. Each component resolves **independently** and gets
   its **own bridge row**. A combination where one component matches and another does not is recorded
   honestly: the matched components are bridged, the unmatched one is written to
   `local_unmatched_ingredient`. Partial knowledge is represented as partial, never rounded up to a match or
   down to a miss.
3. **Salt/hydrate strip** — applied **only if** the unstripped name misses. A small **closed** list in
   `src/drugref/data/salt_suffixes.tsv` (the same closed-asset pattern as slice 1's USAN↔INN crosswalk):
   common salt formers (hydrochloride, sodium, potassium, calcium, sulfate, tartrate, maleate, succinate,
   mesilate, besilate, …) and hydration states (mono/di/trihydrate, anhydrous). Strips a **trailing** token
   only.
4. **Ambiguity** — a name resolving to more than one moiety keeps **every** claimant, matching
   `moieties_by_scheme`'s existing all-claimants rule; picking one arbitrarily would drop a real link and
   answer differently run to run.

**5.1 The salt strip is an admitted stand-in for slice 3, and is labelled as such in the data.**
The correct mechanism is GSRS active-moiety→salt relationships (slice 3). Until those exist, a closed suffix
list buys most of the match rate at small cost — PBS names are salt-heavy — but it is a heuristic, so the
design refuses to let it hide:

- `match_method` records `'exact'` vs `'salt_stripped'` on **every** bridge row, so the heuristic's
  contribution is separable at query time and a consumer can ignore it entirely.
- Everything the heuristic still misses lands in `local_unmatched_ingredient`, so the residual is a
  measured number rather than an impression.
- The list is **closed and curated**, not generated: it does not grow silently, and adding to it is a
  reviewable diff.

**5.2 What is deliberately NOT done.** No fuzzy/edit-distance matching (unauditable, and a wrong drug match
is a clinical hazard, not a metrics dip). No AU→INN legacy-name aliases (frusemide→furosemide,
lignocaine→lidocaine) in this slice: they are plausible but unmeasured, and §7's output is exactly the
evidence needed to decide whether they earn their place. Measure first, then curate.

## 6. Encumbrance quarantine — structural, not a promise

§1.2/§1.3 forbid ATC and AMT/SNOMED values from entering drugref. A comment saying so is worth nothing, so
the constraint is enforced in three places:

1. **The parser reads a fixed column allow-list.** `ingest/pbs.py` selects only the §4 fields into its
   `PbsItem` dataclass. `atc_code`, `atc_description`, `amt_code`, `non_amt_code` and AMT `preferred_term`
   are never read into any field.
2. **No table has anywhere to put them** — §4's schema has no ATC or AMT column, so even a buggy writer has
   no target.
3. **A test proves it after a real ingest.** Following the precedent of the MED-RT fixture-redaction test,
   an acceptance test ingests a fixture that *does* contain populated ATC and AMT columns and asserts that
   **no such value appears anywhere in any drugref table**. The licence guarantee becomes executable, and it
   fails loudly if a future column is added carelessly.

## 7. Measurement is the deliverable

The run returns a `PbsSummary` (mirroring `MedrtSummary` / `MeshSummary`): items read, products written,
products with ≥1 bridged moiety, bridge rows by `match_method`, combination products split, distinct
unmatched component names. From these the spike reports:

- **Match rate** = products with ≥1 bridged moiety ÷ products written.
- **Heuristic contribution** = `salt_stripped` rows ÷ all bridge rows (how much is the stand-in carrying?).
- **The residual** = `local_unmatched_ingredient`, queryable and ordered by frequency — the worklist that
  tells us whether §5.2's aliases are worth building, and how much slice 3 would actually buy.

**Success = a trustworthy number plus a queryable residual, not a coverage target.** A low match rate that
is honestly measured is a successful spike; a high one obtained by fuzzy matching would not be.

## 8. Modules, tests, and how a node operator runs it

Layout mirrors the MED-RT/MeSH feeds (rule 1 pure functions; rule 4 files under ~500 lines):

- **`src/drugref/ingest/pbs.py`** — *pure*: streams the monthly PBS API CSV via `csv.DictReader`, yields
  `PbsItem`; exposes `resolve_components(name) -> list[str]` (split → strip → fold). No DB access.
- **`src/drugref/local.py`** — the **only** writer for the three tables (mirroring `classes.py`'s role):
  `upsert_product`, `clear_source_products`, `add_product_moiety`, `add_unmatched_components`.
- **`src/drugref/ingest/pbs_run.py`** — the orchestrator and sole transaction owner: open `ingest_run` →
  `clear_source_products` → read the INN index once → per item upsert + resolve + bridge-or-record →
  commit. Rollback-then-re-raise on failure, with a module logger, per the foundation-review pattern.
- **`src/drugref/data/salt_suffixes.tsv`** — the closed salt/hydrate list.

**Tests (TDD, failing first).** `tests/test_pbs_parser.py` (pure, no DB): fold, combination split,
salt-strip precedence, column allow-list. `tests/test_pbs_run.py` (DB-gated acceptance): exact match,
salt-stripped match, combination fan-out, **partial-combination honesty**, unmatched recording, idempotent
re-ingest, per-source rebuild leaves MED-RT and MeSH untouched, and the §6 encumbrance quarantine. The
fixture `tests/fixtures/pbs_items_subset.csv` is extracted from a **real** PBS API CSV by a committed,
re-runnable `tests/fixtures/make_pbs_subset.py` — the same discipline as `make_medrt_subset.py`, so the
fixture can never re-encode a wrong assumption about upstream shape.

**Node operator workflow** (documented in HANDOVER, not automated here): download the monthly
`YYYY-MM-01-PBS-API-CSV-files.zip` (~5 MB) from `pbs.gov.au/browse/publications` into the gitignored
`downloads/`, then run the orchestrator. XML and text feeds were **discontinued from 1 May 2026**, so CSV
(or the keyless public API, rate-limited to one request per twenty seconds) is the only path. Pin the data
dictionary version — the schema moved v3.5.7 → v3.6.5 → v3.7.8 within about a year.

## 9. Risks and open questions

- **The licence gate (§1.1) is the dominant risk** and is unresolved by design. It does not block node-local
  ingest; it blocks redistribution, permanently, until written confirmation arrives.
- **Match rate is unknown until measured** — that is the point of the spike, but it means this slice cannot
  promise a usable Australian product layer, only an honest assessment of how far a name bridge gets.
- **`li_item_id` and the exact column names are pinned during implementation** against a real CSV (§4.2).
- **Monthly churn and schema drift** (§8) mean a pinned dictionary version and a re-run of the fixture
  extractor when the release rolls — the same standing follow-up MED-RT and MeSH carry.
