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
2. **Split combinations** — on `" with "`, `" and "` and `", "`. **Measured against the July-2026 release
   (§5.3), not assumed:** `" + "` appears in **zero** of the 1,086 distinct names, while `" with "` appears
   in 208 and `" and "` in 88; multi-component names chain both plus commas ("Allantoin with sulfur, phenol,
   coal tar solution and menthol"). Each component resolves **independently** and gets its **own bridge
   row**. A combination where one component matches and another does not is recorded honestly: the matched
   components are bridged, the unmatched one is written to `local_unmatched_ingredient`. Partial knowledge is
   represented as partial, never rounded up to a match or down to a miss.
3. **Salt/hydrate strip** — applied **only if** the unstripped name misses. A small **closed** list in
   `src/drugref/data/salt_suffixes.tsv` (the same closed-asset pattern as slice 1's USAN↔INN crosswalk):
   salt formers (hydrochloride, sulfate, fumarate, decanoate, succinate, maleate, tartrate, mesilate,
   besilate, sodium, …) and hydration states (mono/di/trihydrate, anhydrous). Strips a **trailing** token
   only. **`acid` is never on the list** — "Alendronic acid", "Folic acid" and "Folinic acid" are INNs whose
   own last word it is, and stripping it would destroy real matches (§5.3).
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

**5.1a Two upstream traps the parser must handle, both measured (§5.3).**

- **The literal string `'null'` is PBS's empty-value sentinel**, used in 44 of `items.csv`'s 75 columns.
  **159 rows carry `li_drug_name = 'null'`** — every one of which has a usable `drug_name`. So the
  ingredient name is `li_drug_name`, **falling back to `drug_name`**, with `'null'` treated as absent
  everywhere. Untreated, drugref would earnestly try to bridge a drug called "null".
- **Parenthetical annotations** ("Acetone (use as additive only)", "Acetic Acid (33 per cent)") are stripped
  before matching — the same `" (…)"` annotation strip `mesh.registry_keys()` already performs.

**5.2 What is deliberately NOT done.** No fuzzy/edit-distance matching (unauditable, and a wrong drug match
is a clinical hazard, not a metrics dip). No AU→INN legacy-name aliases (frusemide→furosemide,
lignocaine→lidocaine) in this slice: they are plausible but unmeasured, and §7's output is exactly the
evidence needed to decide whether they earn their place. Measure first, then curate.

**5.3 The measurements these rules rest on** (July-2026 release, `2026-07-01-PBS-API-CSV-files.zip`,
4.4 MB, 33 CSVs — taken before the plan was written, so no rule here is assumed):

| Fact | Value |
|---|---|
| `items.csv` rows / distinct `pbs_code` / distinct `li_item_id` | 14,840 / 6,945 / **14,840** |
| Distinct `li_drug_name` | 1,086 (1,085 Title-case → folding is load-bearing) |
| `" + "` / `" with "` / `" and "` in distinct names | **0** / 208 / 88 |
| Rows with `li_drug_name = 'null'` (all have a usable `drug_name`) | 159 |
| Distinct names with a genuine trailing salt token | ~20 (hydrochloride 8, fumarate 3, decanoate 3, sulfate 3, …) |
| `benefit_type_code` spread | S 4,577 · A 4,083 · R 3,797 · U 2,383 |

Two consequences worth stating plainly. **`li_item_id` is unique per row (14,840 = 14,840)**, confirming
§4.2's keying decision against the real file rather than the dictionary. And **the salt strip is worth far
less than assumed** — only ~20 names carry a genuine trailing salt token, and two of them ("Dimethyl
fumarate", "Diroximel fumarate") are INNs *in their own right*, so stripping would turn a correct match into
a miss. That is precisely why the strip is **fallback-only** (try the unstripped name first), and
`test_pbs_parser.py` pins "Dimethyl fumarate" as the regression case for it.

Note also that many `items.csv` names are **not drugs at all** — enteral formulas ("Amino acid formula
with…"), wound dressings ("Dressing-gauze-paraffin with…") and extemporaneous chemicals ("Acetone (use as
additive only)"). These will legitimately never match a moiety, because slice 1's gate excludes foods and
excipients by design. They belong in `local_unmatched_ingredient` as *correct* output, which is why §7
reports the residual rather than treating it as failure.

## 6. Encumbrance quarantine — structural, not a promise

§1.2/§1.3 forbid ATC and AMT/SNOMED values from entering drugref. A comment saying so is worth nothing, so
the constraint is enforced in three places:

0. **The encumbered content is in separate FILES, so it is quarantined by not being opened.** Measured on
   the real release: `items.csv` (75 columns) contains **no ATC and no AMT column whatsoever** — they live
   in `atc-codes.csv`, `item-atc-relationships.csv` and `amt-items.csv` (the largest file in the ZIP at
   9.2 MB). The ingest reads **only `items.csv`**. This is a stronger boundary than the column-level one the
   design assumed, and it is why the quarantine costs nothing.
1. **The parser reads a fixed column allow-list.** `ingest/pbs.py` selects only the §4 fields into its
   `PbsItem` dataclass — belt-and-braces behind (0), so that a future release adding an ATC column to
   `items.csv` still cannot leak it.
2. **No table has anywhere to put them** — §4's schema has no ATC or AMT column, so even a buggy writer has
   no target.
3. **A test proves it after a real ingest.** Following the precedent of the MED-RT fixture-redaction test,
   an acceptance test ingests a fixture whose rows have been given **deliberately planted ATC and AMT
   columns** (`atc_code`, `amt_code` — absent upstream, added by the fixture *because* their absence is what
   is being defended) and asserts that **no planted value appears anywhere in any drugref table**. The
   licence guarantee becomes executable, and it fails loudly if a future release adds such a column and a
   future parser reads it.

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

**Node operator workflow** (documented in HANDOVER, not automated here): download the monthly ZIP into the
gitignored `downloads/`, then run the orchestrator. The URL **requires a `?variant=3` query parameter** —
without it the server returns 404 (verified):

```bash
curl -L -o downloads/pbs-2026-07.zip \
  "https://www.pbs.gov.au/publication/schedule/2026/07/2026-07-01-PBS-API-CSV-files.zip?variant=3"
```

It unpacks to `tables_as_csv/` (33 files, 4.4 MB compressed / ~50 MB raw); the ingest reads **only**
`tables_as_csv/items.csv` (8.3 MB), per §6. Files are UTF-8 **with a BOM**, so they are opened with
`encoding='utf-8-sig'` — otherwise the first column name arrives with a `﻿` prefix and every lookup of
it misses. XML and text feeds were **discontinued from 1 May 2026**, so CSV (or the keyless public API,
rate-limited to one request per twenty seconds) is the only path. Pin the data dictionary version — the
schema moved v3.5.7 → v3.6.5 → v3.7.8 within about a year.

## 9. Risks and open questions

- **The licence gate (§1.1) is the dominant risk** and is unresolved by design. It does not block node-local
  ingest; it blocks redistribution, permanently, until written confirmation arrives.
- **Match rate is unknown until measured** — that is the point of the spike, but it means this slice cannot
  promise a usable Australian product layer, only an honest assessment of how far a name bridge gets.
- **Measuring it requires a populated registry.** The dev database currently holds **0 moieties and 0 `INN`
  claims**, so a UNII ingest must run before any match rate is meaningful; against an empty registry the
  bridge correctly matches nothing. The measurement step therefore depends on the slice-1 ingest, not just
  on this slice's code.
- **Column names are now pinned against the real July-2026 release** (§5.3), so §4.2's open question is
  closed. What remains is *release drift*: re-run the fixture extractor and re-confirm §5.3's numbers when
  the monthly schedule rolls.
- **Monthly churn and schema drift** (§8) mean a pinned dictionary version and a re-run of the fixture
  extractor when the release rolls — the same standing follow-up MED-RT and MeSH carry.
