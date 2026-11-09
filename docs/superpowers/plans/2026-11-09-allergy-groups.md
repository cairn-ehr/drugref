# Allergy groups Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give cairn-ehr one read, `drugref.allergy_check(prescribed, allergen)`. It answers `match` / `no-match` /
`unplaced`, with the path, for a prescribed moiety against a recorded allergen (a moiety or an allergy group).
The read is backed by a MeSH-seeded penicillin allergy group behind a reviewed allow-list.

**Architecture:** A pure parser (`ingest/allergy_groups.py`) reads `desc2026` + `supp2026` + the allow-list
TOML. An orchestrator (`ingest/allergy_group_run.py`, the only writer) bridges MeSH records to moieties with
the existing two-key UNII/CAS rule and rebuilds three tables, all keyed by its own source
`DRUGREF-ALLERGY`. A SQL function in a migration does the fold and the match, so cairn (Rust, same Postgres)
and drugref's Python wrapper share one implementation. **No row is written to `class_membership` or
`class_parent`**, so no existing class walk can see the groups.

**Tech Stack:** Python 3 / psycopg 3 / PostgreSQL, pytest (DB-gated via `DRUGREF_TEST_DSN`), `tomllib`, the
existing `drugref.ingest.mesh` streaming reader.

**Spec:** [`docs/superpowers/specs/2026-11-08-drugref-allergy-groups-design.md`](../specs/2026-11-08-drugref-allergy-groups-design.md)

## Global Constraints

- AGPL-3.0; **no new dependency, no new source**. MeSH is already attributed in `NOTICE`.
- Never edit an applied migration. New files are `db/067_allergy_groups.sql` (tables + vocab) and
  `db/068_allergy_check.sql` (fold view + read function). Before writing, check that `origin/main` still ends
  at `066`; renumber if it has moved.
- **Copy every widened CHECK list from the live catalog** (`db.constraint_definition`), then add one value.
  Never retype it (db/039's and db/051's comments record why).
- Source label `DRUGREF-ALLERGY`, writer `allergy_group_run`. Add `"DRUGREF-ALLERGY": "DRUGREF-ALLERGY"` to
  `ids._SOURCE_CANONICAL` and `allergy_group_run` to `provenance.WRITERS` **in the same commit** as the CHECK
  widening.
- `substance_class.concept_type` gains `'AG'` (allergy group). Group identity:
  `ids.mint_class_uuid("DRUGREF-ALLERGY", <root DescriptorUI>)`, with `source_code` = `published_code` = the
  root UI. The name is `"<MeSH name> (allergy group)"`, e.g. `"Penicillins (allergy group)"`.
- `review_status` vocabulary: exactly `'structural-seed'`, `'reviewed'`.
- The fold follows **every** `substance_composition` component, whatever `is_active_component` says, plus the
  moiety itself (owner). It does **not** reuse `moiety_active_in_composite`.
- Outcomes: exactly `'match'`, `'no-match'`, `'unplaced'`. Match kinds: `'same-drug'`, `'same-group'`.
  Unplaced reasons: `'unknown-prescribed'`, `'unknown-allergen'`, `'allergen-not-a-group'`,
  `'prescribed-not-in-mesh'`, `'allergen-in-no-group'`.
- Parsers pure/streaming; the orchestrator owns the transaction and commits once; files stay under ~500 lines
  (`test_module_size_cap`); inline docs a junior can follow; TDD.
- Test moieties carry **real UNIIs** (spec §6 / the fixture rule): penicillin g `Q42T66VG0C`, procaine
  `4Z8Y51M438`, penicillin g procaine `17R794ESYN`, clemizole penicillin `5UL276H6TF`, amoxicillin
  `804826J2HU`, amoxicillin anhydrous `9EM05410Q9`, ampicillin sodium `JFN36L5S8K`, flucloxacillin
  `43B2M34G2V`, temocillin `03QB156W6I`, sulbactam `S4TF6I2330`, piperacillin anhydrous `9I628532GX`,
  perindopril `Y5GMK36KGY`, cephalexin `OBN7UDS42Y`.

## Review Focus

1. **Excluded descriptor reached another way.** Sulbactam (`D013407`) or tazobactam (`D000078142`) reached
   through a combination descriptor (`D000077725` Piperacillin/Tazobactam) or a supplementary record mapped
   to it, so the inhibitor's own moiety becomes a member. Expected: neither moiety is ever a member. Test in
   Task 2 and Task 3.
2. **A self-match on the root's own tree number.** `mesh_concepts.is_descendant_tree` is *strictly* below. The
   root descriptor "Penicillins" itself (and anything bridging to it) must count as at-or-below. Test in
   Task 2.
3. **The `'AG'` class row leaks into a gap view or class reader** (e.g. a "class with no members" view now
   lists Penicillins (allergy group)). Expected: every `gap_%` view, `ci_class_subtree` and
   `class_grain_counts` byte-identical before/after. Test in Task 3. If it fires, fix it with a filter in
   `db/068`, never by editing the view's original migration.
4. **Re-running either MeSH writer wipes the other** (#39). Expected: `mesh_run` then `allergy_group_run` then
   `mesh_run` leaves both sets of rows. Test in Task 3.
5. **An allergen moiety that is itself only a salt partner** (procaine against penicillin g procaine) must
   answer `match`/`same-drug` via the procaine component, **and** must not make penicillin g procaine's
   *other* component (penicillin g) count as "the allergen". The path names the matched components on both
   sides. Test in Task 4.

---

### Task 1: Schema and vocabulary (`db/067`)

**Files:**
- Create: `db/067_allergy_groups.sql`
- Modify: `src/drugref/ids.py` (`_SOURCE_CANONICAL`), `src/drugref/provenance.py` (`WRITERS`)
- Test: `tests/test_allergy_schema.py`

**Interfaces:**
- Produces tables (all with `ingest_run bigint NOT NULL REFERENCES drugref.ingest_run`):
  - `drugref.allergy_group(class_uuid uuid PK REFERENCES substance_class, mesh_root_ui text NOT NULL,
    review_status text NOT NULL CHECK (…'structural-seed','reviewed'), reviewed_by text, reviewed_against text, ingest_run)`
  - `drugref.allergy_group_member(class_uuid uuid REFERENCES allergy_group, moiety_uuid uuid REFERENCES
    substance_moiety, via text NOT NULL, reason text, ingest_run, PRIMARY KEY (class_uuid, moiety_uuid, via))`.
    `via` is `'descriptor:<UI>'` | `'scr:<UI>'` | `'include:<UNII>'`. Index on `moiety_uuid`.
  - `drugref.allergy_mesh_placement(moiety_uuid uuid PK REFERENCES substance_moiety, ingest_run)`
- Widened CHECKs: `ingest_run_source` + `'DRUGREF-ALLERGY'`, `ingest_run_writer` + `'allergy_group_run'`,
  `substance_class_source` + `'DRUGREF-ALLERGY'`, `substance_class_concept_type` + `'AG'`.
- `COMMENT ON` each table: why it exists, that it is delete-and-rebuild per source, and (on
  `allergy_group_member`) that it is deliberately **not** `class_membership`.

- [ ] **Step 1: Write the failing tests.** `test_allergy_schema.py` (DB-gated):
  - `test_tables_exist`: `db.missing_relations(conn, "drugref.allergy_group", "drugref.allergy_group_member", "drugref.allergy_mesh_placement") == ()`.
  - `test_checks_admit_new_values`: insert an `ingest_run` with source `'DRUGREF-ALLERGY'`, writer
    `'allergy_group_run'`, then a `substance_class` row with concept_type `'AG'`. Both succeed.
  - `test_checks_keep_old_values`: for each widened constraint, every value in the pre-migration list (read
    from `db/051` / `db/039` text or restated) is still admitted.
  - `test_review_status_closed`: inserting `review_status='draft'` raises `psycopg.errors.CheckViolation`.
  - `test_source_canonical_pair`: `ids.canonical_source("drugref-allergy") == "DRUGREF-ALLERGY"`, and
    `"allergy_group_run" in provenance.WRITERS`.
- [ ] **Step 2: Run** `uv run pytest tests/test_allergy_schema.py -v`. Expected: FAIL (relations missing).
- [ ] **Step 3: Write `db/067_allergy_groups.sql`** and the two Python vocab entries. Read each CHECK's
  current definition with `db.constraint_definition` against a freshly migrated DB and paste it into the file.
- [ ] **Step 4: Run** the same command. Expected: PASS. Also `uv run pytest tests/test_migration_guard.py
  tests/test_ids.py tests/test_provenance.py -q`. Expected: PASS.
- [ ] **Step 5: Commit** `feat: db/067 allergy-group tables and DRUGREF-ALLERGY vocabulary`.

### Task 2: Allow-list, fixtures, pure parser

**Files:**
- Create: `src/drugref/data/allergy_groups.toml`, `src/drugref/ingest/allergy_groups.py`,
  `tests/fixtures/make_allergy_subset.py`, `tests/fixtures/allergy_desc_subset.xml`,
  `tests/fixtures/allergy_supp_subset.xml`
- Test: `tests/test_allergy_groups_parser.py`

**Interfaces:**
- Consumes: `mesh.iter_records`, `mesh._texts`/`_findtext` (promote to public `texts`/`findtext` if used
  outside `mesh.py`; update callers), `mesh.registry_keys`, `mesh.MemberKeys`.
- Produces:
  - `@dataclass(frozen=True) AllowEntry(ui_or_unii: str, reason: str)`
  - `@dataclass(frozen=True) GroupSpec(root_ui: str, review_status: str, reviewed_by: str | None,
    reviewed_against: str | None, exclude: tuple[AllowEntry, ...], include: tuple[AllowEntry, ...])`
  - `load_allow_list(path: StrPath) -> tuple[GroupSpec, ...]`. It raises `ValueError` on an unknown
    `review_status`, a duplicate root, or a malformed UI/UNII (`D\d+` / `mesh._UNII_RE`).
  - `@dataclass(frozen=True) GroupMemberKeys(root_ui: str, via: str, keys: MemberKeys)`
  - `@dataclass(frozen=True) ParsedAllergyGroups(root_names: dict[str, str], members:
    tuple[GroupMemberKeys, ...], placement_keys: MemberKeys, scr_records: int, desc_records: int)`
  - `parse(*, desc_path, supp_path, groups: tuple[GroupSpec, ...]) -> ParsedAllergyGroups`
- TOML shape (this round's content is exactly spec §4.1):

```toml
[[group]]
root = "D010406"              # Penicillins
review_status = "structural-seed"
reviewed_by = "…"             # who seeded it
reviewed_against = "MeSH 2026 desc/supp; spec 2026-11-08 §4.1"
exclude = [
  { ui = "D013407", reason = "sulbactam: beta-lactamase inhibitor, not a penicillin-allergy trigger alone" },
  { ui = "D000078142", reason = "tazobactam: as sulbactam" },
  { ui = "D010397", reason = "penicillanic acid: not a drug" },
]
include = [
  { unii = "9I628532GX", reason = "piperacillin anhydrous: MED-RT has_SC files it under Penicillins; MeSH carries no bridge key for this form" },
  { unii = "4OUL81K2RT", reason = "carbenicillin indanyl sodium: as above" },
]
```

- Membership rule (spec §4.2), stated once in `parse`'s docstring. A descriptor is **in** a group if any of
  its tree numbers equals or `is_descendant_tree` one of the root's, **and** none of its tree numbers equals
  or is below an excluded UI's tree numbers. An SCR is in the group if any `HeadingMappedTo` UI (`*`
  stripped) is in. Two passes: pass 1 reads `desc` whole into `{ui: (name, trees, keys)}` (roots may come
  later in UI order), pass 2 streams `supp`. `placement_keys` is the union of every descriptor's and SCR's
  keys.

- [ ] **Step 1: Write `make_allergy_subset.py`** on `make_mesh_subset.py`'s pattern (real values copied, only
  ConceptUI synthetic, header says "Do not hand-edit"). It extracts descriptors `D010406 D005436 D000658
  D013407 D000078142 D010397 D000077725 D020913 D002511 D002506 D011343` and SCR `C031367` (temocillin), plus
  one SCR mapped to Sulbactam if the release has one, and one mapped outside the tree. It emits
  `HeadingMappedToList`. Run it against `downloads/mesh/`, commit the two XML files.
- [ ] **Step 2: Write the failing tests** in `test_allergy_groups_parser.py` (pure, no DB):
  - `test_load_allow_list_shipped`: the shipped TOML loads, with one group, root `D010406`, three excludes,
    two includes.
  - `test_load_allow_list_rejects` (parametrised): unknown status, duplicate root, `ui="X1"`,
    `unii="short"` → `ValueError`.
  - `test_flucloxacillin_in_via_descriptor`: some member has `via == "descriptor:D005436"` and
    `"43B2M34G2V" in keys.unii`.
  - `test_root_itself_counts`: some member has `via == "descriptor:D010406"` (Review Focus 2).
  - `test_temocillin_in_via_scr`: `via == "scr:C031367"` with `"03QB156W6I"`.
  - `test_excluded_never_member`: no member's keys contain `S4TF6I2330` (sulbactam) or tazobactam's
    `SE10G96M8W`, and no `via` names `D013407`/`D000078142`/`D010397` (Review Focus 1).
  - `test_outside_tree_not_member`: no member via `D020913` (perindopril), `D002506` (cephalexin) or
    `D011343` (procaine).
  - `test_placement_covers_everything`: `placement_keys.unii` ⊇ {perindopril, cephalexin, procaine,
    temocillin UNIIs}.
- [ ] **Step 3: Run** `uv run pytest tests/test_allergy_groups_parser.py -v`. Expected: FAIL (module missing).
- [ ] **Step 4: Implement** `allergy_groups.py` and the TOML.
- [ ] **Step 5: Run** the same command, plus `uv run pytest tests/test_mesh_parser.py tests/test_mesh_run.py -q`
  if you promoted helpers. Expected: PASS.
- [ ] **Step 6: Commit** `feat: allergy-group allow-list and pure MeSH group parser`.

### Task 3: Orchestrator (`allergy_group_run`)

**Files:**
- Create: `src/drugref/ingest/allergy_group_run.py`
- Modify: `src/drugref/ingest/mesh_run.py` (rename `_resolve_moieties` → `resolve_moieties`, update its
  callers; behaviour unchanged), `tests/test_source_clear_contract.py` (add the new tuple)
- Test: `tests/test_allergy_group_run.py`

**Interfaces:**
- Consumes: Task 2's `load_allow_list`, `parse`; `mesh_run.resolve_moieties(keys, unii_index, cas_index) ->
  list[uuid.UUID]`; `classes.upsert_class`, `classes.moieties_by_scheme`; `provenance.open_run`/`finish_run`;
  `db.clear_source_tables`; `checksum`.
- Produces:
  - `SOURCE = "DRUGREF-ALLERGY"`, `WRITER = "allergy_group_run"`,
    `ALLERGY_TABLES = ("allergy_group_member", "allergy_group", "allergy_mesh_placement")` (children first)
  - `@dataclass(frozen=True) AllergyGroupSummary(groups: int, members: int, placed_moieties: int,
    members_no_key: int, members_key_not_in_registry: int, includes_unresolved: tuple[str, ...])`
  - `ingest_allergy_groups(conn, *, desc_path, supp_path, allow_list_path, upstream_release: str) ->
    AllergyGroupSummary`. It commits once and rolls back + re-raises on error, mirroring `ingest_mesh`.
- Order: open run → `upsert_class` each group (`ClassConcept(nui=root, code=root, name=f"{mesh name}
  (allergy group)", concept_type="AG")`) → `clear_source_tables(ALLERGY_TABLES)` → insert `allergy_group` →
  members (bridged; includes resolved by UNII index only, with the TOML reason in `reason`) → placement (every
  moiety any `placement_keys` value resolves to, **plus** every member) → finish → commit. An include whose
  UNII resolves to no moiety goes in `includes_unresolved` (reported, not fatal).

- [ ] **Step 1: Write the failing tests** (DB-gated; autouse TRUNCATE like `test_mesh_run.py`, adding the
  three new tables). Seed moieties with `claims.upsert_moiety`/`add_claim` using the Global Constraints UNIIs
  (only the ones the fixture needs), then run on Task 2's fixtures and the shipped TOML:
  - `test_penicillin_group_registered`: one `substance_class` row, source `'DRUGREF-ALLERGY'`, concept_type
    `'AG'`, name `'Penicillins (allergy group)'`, and `class_uuid == <pinned literal of
    mint_class_uuid("DRUGREF-ALLERGY","D010406")>` (compute once, paste as a literal, like
    `D000894_UUID`).
  - `test_members`: flucloxacillin, temocillin, both amoxicillins and piperacillin anhydrous (`via
    'include:9I628532GX'`) are members. Sulbactam, perindopril, cephalexin and procaine are not.
  - `test_placement`: perindopril, cephalexin and procaine are placed. A seeded moiety with a made-up-but-valid
    UNII absent from MeSH is not.
  - `test_unresolved_include_reported`: with a temp TOML including an unseeded UNII,
    `summary.includes_unresolved == ("<that UNII>",)`.
  - `test_rebuild_idempotent`: two runs → same member/placement rows, and `classes_added`-style newness only
    on the first run.
  - `test_coexists_with_mesh_run`: `mesh_run.ingest_mesh` (existing fixtures) → allergy run → `ingest_mesh`
    again. Both row sets are still present (Review Focus 4).
  - `test_existing_readers_unchanged`: build a world with `tests/class_profile_world.py` helpers, snapshot
    `SELECT * FROM drugref.<v>` ordered for every view in schema `drugref` named `gap_%`, plus
    `ci_class_subtree`, plus `class_grain_read.class_grain_counts(conn)`. Run the allergy ingest, snapshot
    again, assert equal, and assert `class_membership`/`class_parent` row counts are unchanged (Review
    Focus 3).
  - In `test_source_clear_contract.py`: add
    `"allergy_group_run.ALLERGY_TABLES": (allergy_group_run.ALLERGY_TABLES, ("allergy_group_member", "allergy_group", "allergy_mesh_placement"))`.
- [ ] **Step 2: Run** `uv run pytest tests/test_allergy_group_run.py tests/test_source_clear_contract.py -v`.
  Expected: FAIL.
- [ ] **Step 3: Implement** the orchestrator and the `resolve_moieties` rename.
- [ ] **Step 4: Run** the same, plus `uv run pytest tests/test_mesh_run.py -q`. Expected: PASS.
- [ ] **Step 5: Commit** `feat: allergy_group_run -- MeSH-seeded allergy groups, rebuilt per source`.

### Task 4: The read (`db/068`, `allergy_read.py`)

**Files:**
- Create: `db/068_allergy_check.sql`, `src/drugref/allergy_read.py`
- Test: `tests/test_allergy_read.py`

**Interfaces:**
- Produces (SQL, `LANGUAGE sql STABLE`, `COMMENT ON` each):
  - view `drugref.moiety_allergy_fold(moiety_uuid uuid, component_moiety uuid)`: one self row per
    `substance_moiety`, plus one row per `substance_composition` component of any **unsuperseded** UNII claim
    of the moiety (every component, any `is_active_component`).
  - function `drugref.allergy_check(p_prescribed uuid, p_allergen uuid) RETURNS TABLE (outcome text,
    match_kind text, reason text, group_class_uuid uuid, group_name text, review_status text,
    allergen_component uuid, prescribed_component uuid)`
- Semantics (spec §4.3), evaluated in this order:
  1. Prescribed not in `substance_moiety` → one row `('unplaced', NULL, 'unknown-prescribed', …NULL)`.
  2. Allergen in `substance_class`: if it is not in `allergy_group` → `('unplaced', …, 'allergen-not-a-group')`.
     Otherwise one `('match','same-group')` row per prescribed fold component that is a member. With none,
     `'no-match'` if any prescribed fold component is placed, else `('unplaced', …, 'prescribed-not-in-mesh')`.
  3. Allergen in neither table → `('unplaced', …, 'unknown-allergen')`.
  4. Allergen is a moiety. `'same-drug'` rows for every shared fold component, and `'same-group'` rows for
     every group holding both an allergen component and a prescribed component. With no rows:
     `'unplaced'/'allergen-in-no-group'` if no allergen component is a member of any group; else
     `'unplaced'/'prescribed-not-in-mesh'` if no prescribed component is placed; else `'no-match'`.
  "Placed" = in `allergy_mesh_placement` (the ingest already adds members to it).
- Python: `@dataclass(frozen=True) AllergyMatch(kind, group_class_uuid, group_name, review_status,
  allergen_component, prescribed_component)`; `@dataclass(frozen=True) AllergyAnswer(outcome: str, reason:
  str | None, matches: tuple[AllergyMatch, ...])`; `check(conn, prescribed: uuid.UUID, allergen: uuid.UUID) ->
  AllergyAnswer`, a thin wrapper that adds no logic of its own. The module docstring says why the fold is not
  `moiety_active_in_composite`.

- [ ] **Step 1: Write the failing tests** (DB-gated, on Task 3's seeded world plus `substance_composition`
  rows copied from `drugref_profiles`: `17R794ESYN`→{penicillin g, procaine}, `5UL276H6TF`→{penicillin g
  (FALSE), clemizole}, `JFN36L5S8K`→{ampicillin}; seed the clemizole and ampicillin moieties too). `P` = the
  penicillin group uuid:
  - `test_case_a_perindopril`: `check(perindopril, P).outcome == "no-match"`.
  - `test_case_b_amoxicillin`: both amoxicillin moieties against `P` → `match`, `same-group`.
  - `test_case_c_cephalexin_known_gap`: `check(cephalexin, P).outcome == "no-match"`, with a comment pointing
    at spec §2 non-goal.
  - `test_drug_allergen_expands`: `check(flucloxacillin, amoxicillin)` → `match`, kind `same-group`,
    `group_name == "Penicillins (allergy group)"`.
  - `test_same_drug`: `check(amoxicillin, amoxicillin)` includes kind `same-drug`.
  - `test_fold_ignores_active_flag`: `check(clemizole_penicillin, P)` → `match`, `prescribed_component ==
    penicillin_g`.
  - `test_salt`: `check(ampicillin_sodium, P)` → `match`.
  - `test_procaine_partner` (Review Focus 5): `check(penicillin_g_procaine, procaine)` → `match`. Every match
    row has `allergen_component == procaine`, and there is a `same-drug` row with `prescribed_component ==
    procaine`.
  - `test_unplaced_reasons` (parametrised): unknown prescribed uuid → `unknown-prescribed`. Unknown allergen →
    `unknown-allergen`. A non-AG class uuid → `allergen-not-a-group`. The not-in-MeSH moiety against `P` →
    `prescribed-not-in-mesh`. Perindopril against a perindopril allergen → `match` `same-drug`, while
    cephalexin against a perindopril allergen → `allergen-in-no-group`.
- [ ] **Step 2: Run** `uv run pytest tests/test_allergy_read.py -v`. Expected: FAIL.
- [ ] **Step 3: Write `db/068_allergy_check.sql`** and `allergy_read.py`.
- [ ] **Step 4: Run** the same command. Expected: PASS.
- [ ] **Step 5: Commit** `feat: drugref.allergy_check -- fold every component, three outcomes, with path`.

### Task 5: CLI

**Files:**
- Create: `src/drugref/cli_allergy.py`
- Modify: `src/drugref/cli.py` (wire it in, as `cli_fda_cyp.add_parser(sources)` and
  `cli_class_profile.register(commands)` are)
- Test: `tests/test_cli_allergy.py`

**Interfaces:**
- `drugref ingest allergy-groups --release R --desc PATH --supp PATH [--allow-list PATH]` (the allow-list
  defaults to the packaged TOML). It prints the `AllergyGroupSummary` fields one per line, and exits 0, or 1
  on error. It is **not** a chain step this round: run it after the chain, like `drugref curate` (#104). Say
  so in `--help`.
- `drugref allergy check PRESCRIBED_UUID ALLERGEN_UUID`. It prints `outcome` (and `reason`), then one line per
  match: `<kind>  <group_name> [<review_status>]  allergen:<name> -> prescribed:<name>`. Exit 0 for `match`
  or `no-match`, 3 for `unplaced`, so a script can't mistake "not checked" for "no".

- [ ] **Step 1: Write the failing tests** in the style of `test_cli_class_profile.py`: ingest prints
  `members=` and `placed_moieties=`; check on the case-b pair prints `match` and `Penicillins (allergy group)
  [structural-seed]`; an unknown uuid exits 3.
- [ ] **Step 2: Run** `uv run pytest tests/test_cli_allergy.py -v`. Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** the same command. Expected: PASS.
- [ ] **Step 5: Commit** `feat: drugref ingest allergy-groups / drugref allergy check`.

### Task 6: Measure on a real database, record results

**Files:**
- Create: `tools/allergy_group_measure.py`,
  `docs/superpowers/specs/2026-11-09-drugref-allergy-groups-results.md`
- Test: `tests/test_allergy_measure.py` (one smoke test on the Task 3 world: the tool runs and prints its
  section headers)

**Interfaces:** `uv run python -m tools.allergy_group_measure --dsn DSN --medrt PATH` prints:
- member count;
- placed moieties out of all moieties, and out of *prescribable* ones (those with an `RXNORM_IN` claim);
- every `-cillin`-named moiety not reached via `allergy_check(…, P)`, with its unplaced reason;
- the MED-RT `has_SC` → `Penicillins` RxNorm ingredients, each resolved by `RXNORM_IN` and checked with
  `allergy_check`, naming each miss.

It exits 0 when it runs, whatever the counts (a measurement, not a gate, per spec §6).

- [ ] **Step 1: Write the smoke test; run it; it FAILs.**
- [ ] **Step 2: Implement the tool. Run the test. It PASSes.**
- [ ] **Step 3:** Migrate `drugref_profiles` to `db/068` (`db.apply_migrations`) and run `drugref ingest
  allergy-groups --release 2026 --desc downloads/mesh/desc2026.gz --supp downloads/mesh/supp2026.gz`, then the
  tool. Expected: about 80 members (78 + 2 includes), about 9,930 placed, has_SC misses 0. Any figure that
  differs from spec §6's pre-build table is **explained in the results record**, not smoothed over.
- [ ] **Step 4:** Run the three acceptance cases with `drugref allergy check` on the real database and paste
  the output into the results record.
- [ ] **Step 5: Commit** `docs: allergy-groups results record (real MeSH 2026 build)`.

### Task 7: Docs and hand-over

**Files:** `docs-site/docs/decisions/allergy-groups-allow-list.md` (+ its nav entry in `docs-site/mkdocs.yml`),
`docs/PROJECT-NOTES.md`, `docs/ROADMAP.md`, `docs/HANDOVER.md`.

- [ ] **Step 1:** Write the decision record covering four points: only allow-listed roots are groups (the sulfa
  trap); exclusions and includes need a reason; the fold covers every component (the owner's call and the
  clemizole case); and `no-match` is not "safe" for cross-reactivity (case c). Link the withheld-chemical-class
  record.
- [ ] **Step 2:** PROJECT-NOTES gets the schema/code-map entries, the new DSN state of `drugref_profiles`, and
  **the suite count** (`tests/test_suite_count.py` gates it). ROADMAP gets the slice entry plus follow-ups:
  case (c) cross-reactivity at the R1 side-chain grain, the reviewer for the allow-list, and MED-RT `has_SC`
  ingestion. HANDOVER: follow its own header's bound.
- [ ] **Step 3: Run** `uv run pytest -q` (full suite, `DRUGREF_TEST_DSN` set) and `ruff check .`. Expected:
  all pass. That includes `test_suite_count`, `test_module_size_cap` and `test_lint_bounds`.
- [ ] **Step 4: Commit** `docs: allergy groups -- decision record, notes, roadmap, handover`. Push, then open
  a PR to `main` that names the cairn-ehr allergy slice it unblocks.
