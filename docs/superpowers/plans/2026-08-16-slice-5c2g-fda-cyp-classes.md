# FDA-CYP Potency Classes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Land FDA's CYP/transporter examples table as 65 source-defined PK classes with `has_PK` membership, so slice 5c.3 can point an SPL rule at an exact potency band instead of MED-RT's undifferentiated inhibitor class.

**Architecture:** A pure streaming parser (`ingest/fda_cyp.py`, no DB access) turns the page's 245×11 matrix into `(substance, pathway, role, potency)` tuples; an orchestrator (`ingest/fda_cyp_run.py`) owns the transaction and is the only writer. Classes are minted from the parsed vocabulary; membership is written only for tuples that are unqualified, resolved and in the closed pathway vocabulary. Everything else — 29 qualified cells, 20 unresolved names, 9 regimens, 5 non-drug entries — lands in a rebuildable `fda_cyp_assertion` projection and raises an open question.

**Tech Stack:** Python 3.13 (`uv`), psycopg 3, Postgres 18, pytest. **No new dependency** — the parser uses `re` + `html` from the standard library. Adding an HTML parser would need a rule-6 licence check; the table is regular enough not to need one, and §8's closed vocabulary is what makes a regex parse safe rather than reckless.

**Spec:** [`docs/superpowers/specs/2026-08-16-drugref-slice-5c2g-fda-cyp-classes-design.md`](../specs/2026-08-16-drugref-slice-5c2g-fda-cyp-classes-design.md) — read it alongside this plan; every task cites the section it implements.

## Global Constraints

Every task's requirements implicitly include all of these.

- **TDD, without exception.** Write the failing test, run it, watch it fail *for the stated reason*, then implement. A test that passes before the implementation is a test that pins nothing.
- **All tests must pass before every commit.** `DRUGREF_TEST_DSN='host=localhost port=5532 dbname=drugref_test user=postgres' uv run pytest`. Never `-k`, never `--deselect`: a skip is not a pass, and a deselected failure is not a pass either.
- **`ruff check .` clean before every commit.** The bare form is correct (issue 66); `pyproject`'s `extend-exclude` drops `downloads/` and `docs-site/site`.
- **Do not run `ruff format`.** 163 of 168 files would be reformatted — this repo is not ruff-formatted, and running it would bury the change.
- **Inline documentation understandable by a junior contributor is mandatory** (CLAUDE.md rule 3). Match the surrounding density: this codebase explains *why*, and names the failure a decision prevents.
- **Keep files under ~500 lines.** Issue 89 already tracks four files over it; do not add a fifth.
- **`db/029`–`db/038` are FROZEN.** Never edit an applied migration. `db/039` is this slice's only migration; a correction to it after it merges needs `db/040`.
- **The commit-msg guard must be installed:** `git config core.hooksPath .githooks`. It refuses a body where a GitHub closing keyword sits beside an issue reference. **Write "issue 128", not "#128", near words like *closes* or *fixes*.**
- **Branch:** `slice/5c2g-fda-cyp-classes` (already created; two design commits on it).
- **Source page:** `downloads/FDA/fda_cyp_2026-05-29.html`, already retrieved and verified. `downloads/` is gitignored. SHA-256 **`7400dc898509e83d888ecd713897e59f3dc9d1c5f6cbd2f62a5d6ff8377ffa73`** — matches the spike's manifest, independently reproduced.
- **The standing rule** (PROJECT-NOTES § Standing rules): *ingest what is unambiguous; set aside for clinician review what is not; err on the side of caution.* When a task's instructions and this rule appear to conflict, the rule wins and the conflict is a plan bug — report it.

## File Structure

| file | responsibility |
|---|---|
| `db/039_fda_cyp_classes.sql` | **create** — widen 2 source CHECKs + the writer CHECK, `fda_cyp_assertion`, `gap_fda_cyp_unadjudicated`, one new `open_question` gap kind |
| `src/drugref/ids.py` | **modify** — one `_SOURCE_CANONICAL` entry (a pair with db/039, per db/003's own comment) |
| `src/drugref/provenance.py` | **modify** — `WRITERS` gains `fda_cyp_run` |
| `src/drugref/ingest/fda_cyp.py` | **create** — the pure parser: table location, structural assertions, name/footnote split, cell grammar, closed vocabulary, release identity. No DB, no network. ~380 lines |
| `src/drugref/ingest/fda_cyp_run.py` | **create** — the orchestrator: resolve, disposition, clear, write, rebuild questions, finish. The only writer. ~300 lines |
| `src/drugref/questions.py` | **modify** — one `_GAP_SOURCES` entry |
| `src/drugref/cli_chain.py` | **modify** — `drugref ingest fda-cyp` subcommand |
| `tests/fixtures/fda_cyp_table.html` | **create** — extracted verbatim from the live page, carrying every trap |
| `tests/test_fda_cyp_parser.py` | **create** — pure, no DB |
| `tests/test_fda_cyp_run.py` | **create** — DB-gated |

---

### Task 1: `db/039` — schema, and the three-place source vocabulary

Implements spec §6 and §4.1. Schema only: nothing parses or writes yet, so the later tasks' tests are not also blocked on schema — db/031's precedent.

**Files:**
- Create: `db/039_fda_cyp_classes.sql`
- Modify: `src/drugref/ids.py` (the `_SOURCE_CANONICAL` dict, ~line 49–68)
- Modify: `src/drugref/provenance.py:27-28` (`WRITERS`)
- Test: `tests/test_fda_cyp_schema.py` (create)

**Interfaces:**
- Consumes: nothing.
- Produces: `drugref.fda_cyp_assertion` (table), `drugref.gap_fda_cyp_unadjudicated` (view), `open_question.gap_kind = 'fda_cyp_unadjudicated'`, `ids.canonical_source('FDA-CYP') == 'FDA-CYP'`, `'fda_cyp_run' in provenance.WRITERS`.

- [ ] **Step 1: Read the live CHECK definitions before editing anything**

Copying them verbatim from the catalog rather than retyping from memory is db/031's stated discipline — retyping is how a value goes silently missing, and both CHECKs gate every ingest in the project.

```bash
psql "host=localhost port=5532 dbname=drugref_test user=postgres" -qAt -c \
  "SELECT conname, pg_get_constraintdef(oid) FROM pg_constraint
   WHERE conname IN ('ingest_run_source','ingest_run_writer','substance_class_source')
   ORDER BY conname;"
```

Expected today: `ingest_run_source` lists `UNII, CHEBI, MED-RT, MeSH, PBS, DRUGREF, GSRS, ONCHIGH`; `ingest_run_writer` lists nine writers plus `onchigh_run`; `substance_class_source` lists `MED-RT, MeSH`. **Use what the command prints, not what this plan says** — if they differ, the plan is stale and that is the finding.

- [ ] **Step 2: Write the failing test**

```python
# tests/test_fda_cyp_schema.py
"""db/039's shape: the three-place source vocabulary, the projection, the gap view.

WHY A SCHEMA TEST AT ALL, when later tasks exercise the same objects: a new
source spelling is not a one-line change (issue 101 recorded the lesson for
DRUGCENTRAL and it applies unchanged here). It must land in the database CHECK,
in ids._SOURCE_CANONICAL and in provenance.WRITERS *in the same migration*, and
the failure mode when it does not is silent -- a per-source rebuild deletes
nothing and reports success. These tests are the guard against that silence.
"""
import pytest

from drugref import db, ids, provenance


def test_fda_cyp_is_a_canonical_source_spelling():
    """Listed EXPLICITLY, though the upper-case fall-through would also produce it.

    ids.py's own docstring warns by name against leaning on that fall-through:
    'openFDA-SPL' and 'MeDIC' fold to spellings a mixed-case CHECK would never
    match. 'FDA-CYP' survives by luck, exactly as 'GSRS' and 'DRUGREF' do, and
    the entry records that the luck was CHECKED rather than assumed.
    """
    assert ids.canonical_source("FDA-CYP") == "FDA-CYP"
    assert ids.canonical_source("fda-cyp") == "FDA-CYP"
    assert ids.canonical_source("  FDA-CYP  ") == "FDA-CYP"


def test_fda_cyp_run_is_a_declared_writer():
    """provenance.WRITERS and db/039's CHECK are a PAIR (db/020's source-trio lesson)."""
    assert "fda_cyp_run" in provenance.WRITERS


@pytest.mark.usefixtures("conn")
def test_ingest_run_admits_the_fda_cyp_source_and_writer(conn):
    conn.execute(
        "INSERT INTO drugref.ingest_run (source, upstream_release, source_checksum, writer) "
        "VALUES ('FDA-CYP', '2026-05-29T14:00', 'deadbeef', 'fda_cyp_run')")


@pytest.mark.usefixtures("conn")
def test_substance_class_admits_the_fda_cyp_source(conn):
    """db/003 created this CHECK with a comment instructing exactly this edit:
    'Extend it and _SOURCE_CANONICAL together when a source lands.' FDA-CYP is
    the first source to land since, so this is that instruction being followed.
    """
    live = db.constraint_definition(conn, "substance_class", "substance_class_source")
    assert "'FDA-CYP'" in live
    assert "'MED-RT'" in live and "'MeSH'" in live, "widening must not drop a value"


@pytest.mark.usefixtures("conn")
def test_the_assertion_projection_and_gap_view_exist(conn):
    assert db.missing_relations(conn, "fda_cyp_assertion", "gap_fda_cyp_unadjudicated") == ()


@pytest.mark.usefixtures("conn")
def test_disposition_is_a_closed_set_of_exactly_five_values(conn):
    """Five, not nine -- spec section 7.1. Only combination_regimen and
    non_drug_entity name a CATEGORY, because only those two are asserted by FDA
    rather than inferred by drugref from a string prefix.
    """
    live = db.constraint_definition(conn, "fda_cyp_assertion", "fda_cyp_assertion_disposition")
    for value in ("member", "withheld_qualified", "unresolved_substance",
                  "combination_regimen", "non_drug_entity"):
        assert f"'{value}'" in live
    for inferred in ("enantiomer", "synonym", "metabolite", "group_term"):
        assert inferred not in live, (
            f"{inferred!r} is a cause drugref would be INFERRING from a name, which is "
            "issue 122's manufactured-cause defect. Spec section 7.1.")


@pytest.mark.usefixtures("conn")
def test_the_new_gap_kind_is_admitted(conn):
    live = db.constraint_definition(conn, "open_question", "open_question_gap_kind")
    assert "'fda_cyp_unadjudicated'" in live
```

- [ ] **Step 3: Run it and watch every DB-gated test fail**

```bash
DRUGREF_TEST_DSN='host=localhost port=5532 dbname=drugref_test user=postgres' \
  uv run pytest tests/test_fda_cyp_schema.py -v
```

Expected: the two pure tests fail on `_SOURCE_CANONICAL` / `WRITERS`; the DB-gated ones fail on the missing relation or the un-widened CHECK. **If `test_the_assertion_projection_and_gap_view_exist` passes here, stop** — something already created those objects and this plan's assumptions are wrong.

- [ ] **Step 4: Write `db/039_fda_cyp_classes.sql`**

Follow db/031's house style exactly: every widening is `DROP CONSTRAINT IF EXISTS` + `ADD`, and every additive object is `IF NOT EXISTS` or guarded on the constraint's *text* (not its name), so a hand-run `psql -f` replay is a no-op rather than an error or a table rescan.

```sql
-- db/039_fda_cyp_classes.sql
-- Slice 5c.2g: admit FDA's CYP/transporter examples table as a CLASSIFICATION
-- source -- the potency vocabulary MED-RT cannot express and SPL mining needs.
--
-- Spec: docs/superpowers/specs/2026-08-16-drugref-slice-5c2g-fda-cyp-classes-design.md
--
-- SCHEMA ONLY. No parser, no orchestrator, no data lands here. This slice adds
-- classification MEMBERSHIP and nothing else: it touches no curated_* table, no
-- class_contraindication, no read path, and creates NO DDI pair. FDA describes
-- its table as an optional, non-exhaustive interpretive guide; joining its
-- inhibitor and substrate columns would manufacture ~800 pairs no source asserts
-- (spec section 9).

-- ============================================================================
-- 1. The three-place source vocabulary
-- ============================================================================
-- Copied VERBATIM from the live catalog before adding one value each -- db/031's
-- discipline, because retyping either list from memory is how a value goes
-- silently missing, and both CHECKs gate every ingest in the project.
ALTER TABLE drugref.ingest_run DROP CONSTRAINT IF EXISTS ingest_run_source;
ALTER TABLE drugref.ingest_run ADD CONSTRAINT ingest_run_source
    CHECK (source IN ('UNII', 'CHEBI', 'MED-RT', 'MeSH', 'PBS', 'DRUGREF', 'GSRS',
                      'ONCHIGH', 'FDA-CYP'));

ALTER TABLE drugref.ingest_run DROP CONSTRAINT IF EXISTS ingest_run_writer;
ALTER TABLE drugref.ingest_run ADD CONSTRAINT ingest_run_writer
    CHECK (writer IN ('unii_run', 'chebi', 'medrt_run', 'mesh_run', 'mesh_rel_run',
                      'pbs_run', 'curation', 'unattributed', 'gsrs_run',
                      'onchigh_run', 'fda_cyp_run'));

-- db/003 created this CHECK with ('MED-RT', 'MeSH') and a comment ending
-- "Extend it and _SOURCE_CANONICAL together when a source lands." FDA-CYP is the
-- first class-defining source to land since, so this is that instruction being
-- followed rather than rediscovered. src/drugref/ids.py gains its entry in the
-- same commit; the two are a pair, and a test asserts both.
ALTER TABLE drugref.substance_class DROP CONSTRAINT IF EXISTS substance_class_source;
ALTER TABLE drugref.substance_class ADD CONSTRAINT substance_class_source
    CHECK (source IN ('MED-RT', 'MeSH', 'FDA-CYP'));

-- NOTE what is deliberately NOT widened: substance_class_concept_type already
-- admits 'PK' and class_membership_relationship already admits 'has_PK', both
-- since db/003. This slice reuses those vocabularies rather than adding to them,
-- which is the whole argument for projecting FDA's roles as PK classes instead
-- of inventing a mechanism.

-- ============================================================================
-- 2. fda_cyp_assertion -- every parsed tuple, including the ones NOT promoted
-- ============================================================================
-- A rebuildable projection keyed by ingest_run.source, in the shape
-- ingest_unresolved_onc_endpoint (db/031) established: a row here is a WORKLIST
-- ENTRY, not an error and not a drop.
--
-- It holds every tuple the parser produced -- members and withheld alike --
-- because the withheld ones are the point. 29 of 337 cells carry a footnote, and
-- two of those footnotes NEGATE the row they sit on: bupropion's row asserts
-- '2B6 sensitive substrate' while footnote 2 says "Bupropion itself is not a
-- sensitive substrate", and rolapitant's asserts P-gp/BCRP inhibition while
-- footnote 17 denies it for the IV route. Promoting those to membership would
-- make drugref assert the opposite of its cited source; deciding they are
-- negated is a clinical reading of prose. Storing the row with its footnote and
-- withholding the membership is the only option that neither asserts nor
-- discards (spec sections 3 and 5).
CREATE TABLE IF NOT EXISTS drugref.fda_cyp_assertion (
    ingest_run       bigint NOT NULL REFERENCES drugref.ingest_run(ingest_run_id),
    source           text   NOT NULL
        CONSTRAINT fda_cyp_assertion_source CHECK (source = 'FDA-CYP'),
    -- The row's 1-based position in FDA's table. FDA publishes no row id, and the
    -- substance name is NOT unique (aprepitant occupies two rows), so this is the
    -- only stable within-release handle back to the exact upstream line.
    row_ordinal      integer NOT NULL,
    -- The substance name AS FDA PRINTS IT, footnote markers and all. The raw fact,
    -- never a guess at what it should have been -- ingest_unresolved_onc_endpoint's
    -- identifier_value has the same contract.
    raw_substance    text   NOT NULL,
    resolved_moiety_uuid uuid REFERENCES drugref.substance_moiety(moiety_uuid),
    -- FDA's own column heading ('CYP Mod INH'), carried so a curator can find the
    -- exact cell, and because it is half of the role cross-check (spec section 8).
    column_heading   text   NOT NULL,
    raw_cell         text   NOT NULL,
    system           text   NOT NULL
        CONSTRAINT fda_cyp_assertion_system CHECK (system IN ('CYP', 'transporter')),
    pathway          text   NOT NULL,
    role             text   NOT NULL
        CONSTRAINT fda_cyp_assertion_role
        CHECK (role IN ('inhibitor', 'inducer', 'substrate')),
    -- NULL for transporters, which FDA gives no potency vocabulary at all. A
    -- nullable column here is the honest representation of "this axis has no
    -- band", not a missing value.
    potency          text
        CONSTRAINT fda_cyp_assertion_potency
        CHECK (potency IS NULL OR potency IN ('strong', 'moderate', 'weak',
                                              'sensitive', 'moderate sensitive')),
    class_uuid       uuid REFERENCES drugref.substance_class(class_uuid),
    footnote_markers text,
    footnote_text    text,
    -- CURATOR EVIDENCE, NEVER COVERAGE. What a stated prefix rule found in the
    -- registry near an unresolved name, so a curator need not redo the search.
    -- A row carrying one is EXACTLY as unresolved as one carrying none, and NO
    -- COUNT MAY EVER BE QUOTED AGAINST IT. The DrugCentral evaluation already
    -- paid for this lesson: its prefix heuristic "matched" glycerol to
    -- glycerol 1,3-dimethacrylate, a different substance, and its own note says
    -- "treat it as the shape of the problem, not a count to quote."
    registry_near_name text,
    -- FIVE VALUES, NOT NINE -- spec section 7.1, and the reason is the standing
    -- rule (PROJECT-NOTES): a disposition records what was OBSERVED, never what
    -- the round suspects it MEANS. The resolution residue splits into six
    -- recognisable categories, and only these two name one, because only these
    -- two are asserted by FDA: combination_regimen from the regimen string FDA
    -- wrote, non_drug_entity from FDA's own pinned five-substance sentence.
    -- Calling R-venlafaxine an "enantiomer of a held racemate" would be a
    -- chemical relationship inferred from a string prefix -- issue 122's
    -- manufactured-cause defect. Those four collapse to unresolved_substance.
    disposition      text   NOT NULL
        CONSTRAINT fda_cyp_assertion_disposition
        CHECK (disposition IN ('member', 'withheld_qualified', 'unresolved_substance',
                               'combination_regimen', 'non_drug_entity')),
    PRIMARY KEY (ingest_run, row_ordinal, column_heading, pathway)
);

COMMENT ON TABLE drugref.fda_cyp_assertion IS
    'Every (substance x pathway x role x potency) tuple parsed from FDA''s '
    'CYP/transporter examples table, INCLUDING the ones deliberately not promoted '
    'to class_membership. A rebuildable projection keyed by ingest_run.source. '
    'Rows with disposition <> ''member'' are a WORKLIST, not errors and not drops: '
    'ingest preserves evidence, curation creates clinical judgement.';

COMMENT ON COLUMN drugref.fda_cyp_assertion.registry_near_name IS
    'Curator evidence, NEVER coverage. A row carrying a near name is exactly as '
    'unresolved as one without, and no coverage figure may be computed from this '
    'column. See db/039''s header and the DrugCentral glycerol precedent.';

CREATE INDEX IF NOT EXISTS fda_cyp_assertion_by_disposition
    ON drugref.fda_cyp_assertion (disposition);

-- ============================================================================
-- 3. The gap view
-- ============================================================================
-- Grouped on (source, raw_substance, column_heading, pathway) -- dropping ONLY
-- ingest_run, exactly as db/016 and db/031 dropped it -- so one view row is one
-- independently-answerable fact and the view's grain matches the grain the
-- gap_key built from it uses. db/017's lesson, restated because it has bitten
-- twice: grouping coarser folds two independent facts onto one immortal
-- question_uuid; grouping finer mints two questions for one fact.
--
-- 'member' rows are excluded: a membership drugref already wrote asks nobody
-- anything.
CREATE OR REPLACE VIEW drugref.gap_fda_cyp_unadjudicated AS
SELECT a.source,
       a.raw_substance,
       a.column_heading,
       a.pathway,
       max(a.disposition)        AS disposition,
       max(a.raw_cell)           AS raw_cell,
       max(a.footnote_text)      AS footnote_text,
       max(a.registry_near_name) AS registry_near_name,
       max(r.upstream_release)   AS upstream_release
FROM   drugref.fda_cyp_assertion a
JOIN   drugref.ingest_run r ON r.ingest_run_id = a.ingest_run
WHERE  a.disposition <> 'member'
GROUP  BY a.source, a.raw_substance, a.column_heading, a.pathway;

COMMENT ON VIEW drugref.gap_fda_cyp_unadjudicated IS
    'FDA-CYP tuples awaiting a human: a footnote nobody has adjudicated, a name '
    'drugref did not resolve, a regimen, or a non-drug entity. One row per '
    '(source, raw_substance, column_heading, pathway) -- the grain a gap_key built '
    'from this view must also use. ABSENCE OF A ROW IS NOT COVERAGE.';

-- ============================================================================
-- 4. The sixteenth question kind
-- ============================================================================
-- Guarded on the constraint's TEXT rather than its name, so a replay against an
-- already-widened database skips the drop/add entirely instead of rescanning --
-- the idiom db/016, db/019, db/022, db/028, db/029 and db/031 all reuse.
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint
                   WHERE  conname  = 'open_question_gap_kind'
                   AND    conrelid = 'drugref.open_question'::regclass
                   AND    pg_get_constraintdef(oid) LIKE '%fda_cyp_unadjudicated%') THEN
        ALTER TABLE drugref.open_question DROP CONSTRAINT IF EXISTS open_question_gap_kind;
        ALTER TABLE drugref.open_question ADD CONSTRAINT open_question_gap_kind
            CHECK (gap_kind IN (
                -- COPIED VERBATIM from the live catalog, then extended by one.
                -- Retyping this list from memory would silently drop a kind and
                -- orphan every question already minted under it.
                'PLACEHOLDER_REPLACE_WITH_LIVE_VALUES',
                'fda_cyp_unadjudicated'));
    END IF;
END $$;
```

> **The `PLACEHOLDER_REPLACE_WITH_LIVE_VALUES` line is deliberate and must not survive.** Replace it with the fifteen existing values printed by the command below, in the order printed. It is written this way because hand-copying a fifteen-value list into a plan is exactly how a value goes missing, and a placeholder that fails loudly beats a list that is quietly wrong:
> ```bash
> psql "host=localhost port=5532 dbname=drugref_test user=postgres" -qAt -c \
>   "SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conname='open_question_gap_kind';"
> ```

- [ ] **Step 5: Add the `ids` entry and the writer**

In `src/drugref/ids.py`, inside `_SOURCE_CANONICAL`, after the `GSRS` entry:

```python
    # Slice 5c.2g. 'FDA-CYP' survives the upper-case fall-through unchanged, as
    # 'GSRS' and 'DRUGREF' do -- and is listed for the same reason: the entry
    # records that the luck was CHECKED rather than assumed. db/039 widens both
    # substance_class's and ingest_run's source CHECKs to match; they are a trio.
    "FDA-CYP": "FDA-CYP",
```

In `src/drugref/provenance.py`, extend `WRITERS`:

```python
WRITERS = ("unii_run", "chebi", "medrt_run", "mesh_run", "mesh_rel_run", "pbs_run",
           "curation", "unattributed", "gsrs_run", "onchigh_run", "fda_cyp_run")
```

`tests/test_provenance.py:142` asserts `WRITERS` equals a literal tuple — **update that assertion too**, or the suite fails. It is a deliberate second statement, not duplication: it is the test that makes `WRITERS` and the CHECK a pair.

- [ ] **Step 6: Apply and verify**

```bash
DRUGREF_TEST_DSN='host=localhost port=5532 dbname=drugref_test user=postgres' \
  uv run pytest tests/test_fda_cyp_schema.py tests/test_provenance.py -v
```

Expected: all pass. Then the whole suite, then `ruff check .`.

- [ ] **Step 7: Commit**

```bash
git add db/039_fda_cyp_classes.sql src/drugref/ids.py src/drugref/provenance.py \
        tests/test_fda_cyp_schema.py tests/test_provenance.py
git commit -m "feat(db/039): admit FDA-CYP as a classification source

The three-place vocabulary a new source needs -- ingest_run's source and
writer CHECKs, substance_class's source CHECK, ids._SOURCE_CANONICAL and
provenance.WRITERS -- landed together, which issue 101 recorded as the
non-obvious part: a missing spelling makes a per-source rebuild delete
nothing and report success.

fda_cyp_assertion holds every parsed tuple including the ones NOT promoted
to membership, because those are the point: two of FDA's footnotes negate
the row they sit on. disposition has five values rather than nine -- only
combination_regimen and non_drug_entity name a category, because only
those two are asserted by FDA rather than inferred by drugref.

Schema only. Nothing parses or writes yet."
```

---

### Task 2: The fixture, and the parser's structural assertions

Implements spec §2.1 and the count assertions in §8. **The fixture is built first because every later parser task tests against it.**

**Files:**
- Create: `tests/fixtures/fda_cyp_table.html`
- Create: `src/drugref/ingest/fda_cyp.py`
- Create: `tests/test_fda_cyp_parser.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `fda_cyp.DATA_TABLE_INDEX: int`, `fda_cyp.EXPECTED_COLUMNS: int`, `fda_cyp.ROLE_COLUMNS: dict[int, tuple[str, str, str | None]]`, `fda_cyp.extract_rows(html: str) -> list[list[str]]` (returns the 245 data rows, each of 11 cleaned cells, header excluded), `fda_cyp.FdaCypParseError(Exception)`.

- [ ] **Step 1: Build the fixture from the real page**

**Extracted verbatim, never hand-written** — the repo's standing rule, whose cost is on record: the last hand-written fixture invented an `INN_ID`, a CAS and a UNII. Run this from the repo root:

```bash
uv run python - <<'PY'
import re, pathlib
src = pathlib.Path("downloads/FDA/fda_cyp_2026-05-29.html").read_text(encoding="utf-8")
table = re.findall(r"<table.*?</table>", src, re.S)[0]
rows = re.findall(r"<tr.*?</tr>", table, re.S)
header, data = rows[0], rows[1:]

# Every trap the design was derived from, by the substance each row names.
# A fixture of clean rows would pass a parser carrying all four garbage classes.
WANTED = [
    "ciprofloxacin", "rifampin", "teriflunomide",      # closed-vocabulary rejections
    "bupropion", "rolapitant",                          # footnotes that NEGATE (spec s3)
    "ritonavir", "conivaptan", "cenobamate",            # footnote list / cell / letter
    "atazanavir and ritonavir",                         # combination regimen
    "Sofosbuvir and Velpatasvir and Voxilaprevir",      # combination, capitalised
    "curcumin", "diosmin", "grapefruit juice",          # non-drugs (2 of which RESOLVE)
    "St. John", "tobacco (smoking)",
    "R-venlafaxine", "S-venlafaxine", "S-mephenytoin",  # enantiomers (issue 128)
    "oseltamivir carboxylate", "oral contraceptives",   # metabolite, group term
    "glyburide", "peginterferon alpha-2a",              # apparent synonyms
    "aprepitant",                                       # the one substance on two rows
    "abiraterone", "acyclovir", "adagrasib", "adefovir", "alprazolam",  # clean controls
]
def names(row):
    cell = re.findall(r"<t[hd].*?</t[hd]>", row, re.S)[0]
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", cell)).strip()

kept = [r for r in data if any(w.lower() in names(r).lower() for w in WANTED)]
out = "<table>\n" + header + "\n" + "\n".join(kept) + "\n</table>\n"
pathlib.Path("tests/fixtures").mkdir(parents=True, exist_ok=True)
pathlib.Path("tests/fixtures/fda_cyp_table.html").write_text(out, encoding="utf-8")
print(f"fixture rows: {len(kept)}")
for r in kept: print("   ", names(r))
PY
```

**Read the printed list.** Every trap named in the design must appear. If `ritonavir` prints as `ritonavir 14, 15,` — good, that is the point. If any `WANTED` entry produced no row, the page changed and that is a finding to report, not to work around.

- [ ] **Step 2: Write the failing structural test**

```python
# tests/test_fda_cyp_parser.py
"""The FDA-CYP parser: pure, no DB, no network.

THE FIXTURE IS EXTRACTED VERBATIM from the live page and carries every trap the
design was derived from, because a fixture of clean rows would pass a parser
that mints four garbage classes. Do not hand-edit it; rebuild it with the
snippet in the plan's Task 2 if the page is re-fetched.
"""
import pathlib

import pytest

from drugref.ingest import fda_cyp

FIXTURE = pathlib.Path(__file__).parent / "fixtures" / "fda_cyp_table.html"


@pytest.fixture(scope="module")
def fixture_html():
    return FIXTURE.read_text(encoding="utf-8")


def test_every_row_has_exactly_eleven_cells(fixture_html):
    """The real page measures Counter({11: 245}) -- EXACT, not typical.

    So a ragged row is a structural change to the source, not a parse variation,
    and it must stop the ingest rather than be absorbed. This is one of the two
    integrity gates that make a regex parse of HTML defensible (spec section 8).
    """
    rows = fda_cyp.extract_rows(fixture_html)
    assert rows, "fixture yielded no data rows"
    for row in rows:
        assert len(row) == fda_cyp.EXPECTED_COLUMNS, f"ragged row: {row[0]!r}"


def test_a_ragged_row_raises_rather_than_being_absorbed(fixture_html):
    broken = fixture_html.replace("</tr>\n</table>", "<td>extra</td></tr>\n</table>")
    with pytest.raises(fda_cyp.FdaCypParseError, match="cells"):
        fda_cyp.extract_rows(broken)


def test_the_header_row_is_not_returned_as_data(fixture_html):
    rows = fda_cyp.extract_rows(fixture_html)
    assert not any(row[0] == "Drug or Other Substance" for row in rows)


def test_the_ten_role_columns_are_pinned_to_their_meanings():
    """Each column IS a (system, role, potency) tuple -- the table is a MATRIX,
    not a list of facts, and this mapping is the whole reason a cell can be read.
    """
    assert fda_cyp.ROLE_COLUMNS[1] == ("CYP", "inhibitor", "strong")
    assert fda_cyp.ROLE_COLUMNS[8] == ("CYP", "substrate", "moderate sensitive")
    assert fda_cyp.ROLE_COLUMNS[9] == ("transporter", "inhibitor", None)
    assert fda_cyp.ROLE_COLUMNS[10] == ("transporter", "substrate", None)
    assert len(fda_cyp.ROLE_COLUMNS) == 10
```

- [ ] **Step 3: Run and verify it fails**

```bash
uv run pytest tests/test_fda_cyp_parser.py -v
```
Expected: `ModuleNotFoundError: No module named 'drugref.ingest.fda_cyp'`.

- [ ] **Step 4: Implement the structural half of the parser**

```python
# src/drugref/ingest/fda_cyp.py
"""Parse FDA's CYP/transporter examples table. PURE: no DB, no network.

The architecture invariant every parser here follows -- parsers are pure and
streaming, orchestrators own the transaction and are the only writers.

WHAT THE SOURCE IS. One HTML page carrying six tables; the first is the data.
It is a MATRIX, not a list of facts: 245 data rows x 11 columns, where the first
column names the substance and EACH OF THE OTHER TEN IS a (system, role,
potency) tuple. The cell holds the pathway list. So one cell such as
'P-gp; BCRP inhibitor' in the TRNSP INH column is two facts, and the whole table
is 337 non-empty cells expanding to 415 tuples over 65 classes.

WHY A REGEX PARSE IS DEFENSIBLE HERE, when it usually is not. Two reasons, and
neither is "the HTML looked simple":

1. Adding an HTML-parser dependency needs a rule-6 licence check before it can
   be added, not after (CLAUDE.md rule 6). The table does not need one.
2. The parse is guarded on both sides. The row and cell COUNTS are asserted
   (245 x 11 exactly), and the pathway vocabulary is CLOSED -- an unrecognised
   token aborts the ingest. A lenient parse of the real page produces 69 classes
   instead of 65 while reporting zero errors, and four of them are garbage minted
   with real immortal UUIDs ('cyp:1a2 20', 'transporter:oatp1b1 inhibitor').
   Those four are what this module's strictness is for.
"""
import html
import re

# The data is the FIRST table on the page; tables 2-6 are the potency legends
# (definitions of strong/moderate/weak), which drugref does not ingest -- it
# stores the class, not the pharmacokinetics.
DATA_TABLE_INDEX = 0

# One substance column + ten role columns. ASSERTED, not assumed: the real page
# measures Counter({11: 245}), so a ragged row is a source change.
EXPECTED_COLUMNS = 11

# Each role column IS a (system, role, potency). Transporters get None: FDA
# publishes no potency vocabulary for them at all, which is why potency is
# nullable in db/039 rather than defaulted -- "this axis has no band" is a fact,
# not a missing value.
ROLE_COLUMNS: dict[int, tuple[str, str, str | None]] = {
    1:  ("CYP", "inhibitor", "strong"),
    2:  ("CYP", "inhibitor", "moderate"),
    3:  ("CYP", "inhibitor", "weak"),
    4:  ("CYP", "inducer", "strong"),
    5:  ("CYP", "inducer", "moderate"),
    6:  ("CYP", "inducer", "weak"),
    7:  ("CYP", "substrate", "sensitive"),
    8:  ("CYP", "substrate", "moderate sensitive"),
    9:  ("transporter", "inhibitor", None),
    10: ("transporter", "substrate", None),
}

_TABLE = re.compile(r"<table.*?</table>", re.S)
_ROW = re.compile(r"<tr.*?</tr>", re.S)
_CELL = re.compile(r"<t[hd].*?</t[hd]>", re.S)
_TAG = re.compile(r"<[^>]+>")
_SPACE = re.compile(r"\s+")


class FdaCypParseError(Exception):
    """The source did not have the shape this parser asserts.

    RAISED, NEVER LOGGED AND SKIPPED. Every condition that reaches this class is
    a change in the source's structure or vocabulary, and absorbing one silently
    is how four garbage classes get minted with immortal UUIDs while the run
    reports success.
    """


def _clean(fragment: str) -> str:
    """One HTML cell to its visible text: tags out, entities decoded, spaces collapsed.

    Collapsing internal whitespace matters rather than being tidy -- FDA's cells
    carry newlines and non-breaking spaces from the CMS, and '1A2  weak' must read
    identically to '1A2 weak' or the role cross-check fires on a formatting
    difference.
    """
    return _SPACE.sub(" ", html.unescape(_TAG.sub(" ", fragment))).strip()


def extract_rows(page: str) -> list[list[str]]:
    """The data table's 245 rows, each of 11 cleaned cells. Header excluded.

    Raises FdaCypParseError if the page has no table, or if any row does not
    carry exactly EXPECTED_COLUMNS cells.
    """
    tables = _TABLE.findall(page)
    if len(tables) <= DATA_TABLE_INDEX:
        raise FdaCypParseError(
            f"page carries {len(tables)} table(s); the data table is index "
            f"{DATA_TABLE_INDEX}. The page structure changed.")
    rows = _ROW.findall(tables[DATA_TABLE_INDEX])
    if not rows:
        raise FdaCypParseError("the data table carries no rows")

    parsed: list[list[str]] = []
    for ordinal, row in enumerate(rows[1:], start=1):  # rows[0] is the header
        cells = [_clean(cell) for cell in _CELL.findall(row)]
        if len(cells) != EXPECTED_COLUMNS:
            raise FdaCypParseError(
                f"row {ordinal} ({cells[0] if cells else '?'!r}) has {len(cells)} "
                f"cells, expected {EXPECTED_COLUMNS}. The table's shape changed.")
        parsed.append(cells)
    return parsed
```

- [ ] **Step 5: Run and verify it passes**

```bash
uv run pytest tests/test_fda_cyp_parser.py -v
```
Expected: 4 passed.

- [ ] **Step 6: Commit**

```bash
git add src/drugref/ingest/fda_cyp.py tests/test_fda_cyp_parser.py tests/fixtures/fda_cyp_table.html
git commit -m "feat(fda-cyp): the parser's structural gate, and a fixture of traps

The table is a matrix, not a list of facts: each of the ten role columns IS
a (system, role, potency) and the cell holds the pathway list.

Row and cell counts are asserted rather than assumed. The real page measures
exactly 11 cells in each of 245 rows, so a ragged row is a change to the
source and stops the ingest.

The fixture is extracted verbatim and carries every trap the design was
derived from -- a fixture of clean rows would pass a parser that mints four
garbage classes while reporting success."
```

---

### Task 3: Substance names and footnote markers

Implements spec §2.3. **The `ritonavir 14, 15,` case is the reason this is its own task.**

**Files:**
- Modify: `src/drugref/ingest/fda_cyp.py`
- Modify: `tests/test_fda_cyp_parser.py`

**Interfaces:**
- Consumes: `fda_cyp.extract_rows`.
- Produces: `fda_cyp.split_footnotes(text: str) -> tuple[str, str | None]` returning `(clean_text, markers_or_None)`, where `markers` is a comma-joined string such as `"14, 15"`.

- [ ] **Step 1: Write the failing tests**

```python
def test_a_single_trailing_footnote_is_split_off():
    assert fda_cyp.split_footnotes("adefovir 1") == ("adefovir", "1")


def test_a_COMMA_SEPARATED_footnote_list_is_split_off():
    """THE LOAD-BEARING CASE. FDA prints 'ritonavir 14, 15, 16' -- three markers,
    comma-separated.

    A stripper that handles 'adefovir 1' but not this leaves the substance named
    with its markers attached, which resolves to nothing -- so one of the most
    important CYP3A inhibitors in medicine drops out of the ingest SILENTLY and
    the run still reports success.

    THE DESIGN ROUND FIRST WROTE THIS STRING DOWN AS 'ritonavir 14, 15,' -- a
    string that appears nowhere on FDA's page. It was its own probe stripper's
    output: the regex ate the trailing ' 16' and left the comma, and the result
    was recorded as a measurement of the source. A partially-working parser hands
    you a plausible string, and a plausible string gets quoted.
    """
    assert fda_cyp.split_footnotes("ritonavir 14, 15, 16") == ("ritonavir", "14, 15, 16")
    # And the trailing-comma form the probe produced must ALSO split cleanly, so a
    # re-fetch that really does end in a comma is not a new bug.
    assert fda_cyp.split_footnotes("ritonavir 14, 15,") == ("ritonavir", "14, 15")


def test_a_LETTER_marker_is_a_second_namespace():
    """Footnotes are numbered AND lettered: cenobamate's cell ends 'inducer b'."""
    assert fda_cyp.split_footnotes("CYP3A moderate inducer b") == ("CYP3A moderate inducer", "b")


def test_an_unfootnoted_name_is_returned_unchanged_with_no_markers():
    assert fda_cyp.split_footnotes("abiraterone") == ("abiraterone", None)


def test_a_number_that_is_part_of_the_name_is_not_eaten():
    """The stripper must not treat a pathway digit as a footnote.

    'peginterferon alpha-2a' and 'MATE2-K' both end in alphanumerics that are
    NAME, not marker. The rule is a marker is a whitespace-separated bare integer
    or single lower-case letter -- '2a' and '2-K' are neither.
    """
    assert fda_cyp.split_footnotes("peginterferon alpha-2a") == ("peginterferon alpha-2a", None)
    assert fda_cyp.split_footnotes("MATE2-K substrate") == ("MATE2-K substrate", None)


def test_the_fixture_yields_ritonavir_not_ritonavir_14_15(fixture_html):
    """End to end over the real bytes: the substance is named 'ritonavir'."""
    rows = fda_cyp.extract_rows(fixture_html)
    names = {fda_cyp.split_footnotes(row[0])[0] for row in rows}
    assert "ritonavir" in names
    assert not any(name.startswith("ritonavir 14") for name in names)
```

- [ ] **Step 2: Run and verify failure**

```bash
uv run pytest tests/test_fda_cyp_parser.py -v -k footnote
```
Expected: `AttributeError: module 'drugref.ingest.fda_cyp' has no attribute 'split_footnotes'`.

- [ ] **Step 3: Implement**

```python
# A footnote marker is a whitespace-separated BARE INTEGER or SINGLE LOWER-CASE
# LETTER, optionally repeated and comma-separated, at the very end of the text.
#
# THE PRECISION IS THE POINT, in both directions:
#  * too loose eats real name characters -- 'peginterferon alpha-2a' ends in '2a'
#    and 'MATE2-K substrate' contains '2-K', neither of which is a marker;
#  * too tight misses 'ritonavir 14, 15,' -- markers can be a comma-separated
#    list WITH A TRAILING COMMA, and missing it drops ritonavir from the ingest
#    silently while the run reports success.
_FOOTNOTE_TAIL = re.compile(r"((?:\s+(?:\d+|[a-z])\s*,?)+)\s*$")
_MARKER = re.compile(r"\d+|[a-z]")


def split_footnotes(text: str) -> tuple[str, str | None]:
    """Split trailing footnote markers off a substance name or cell body.

    Returns (text_without_markers, "14, 15") or (text, None).

    Markers appear in THREE positions on this page and this handles the trailing
    one; a marker attached to an individual pathway MID-cell (ciprofloxacin's
    '1A2 20 ; 3A moderate inhibitor') is handled by the cell parser, which calls
    this per list item.
    """
    match = _FOOTNOTE_TAIL.search(text)
    if not match:
        return text, None
    markers = _MARKER.findall(match.group(1))
    if not markers:
        return text, None
    return text[:match.start()].strip(), ", ".join(markers)
```

- [ ] **Step 4: Run and verify it passes**

```bash
uv run pytest tests/test_fda_cyp_parser.py -v
```
Expected: 10 passed.

- [ ] **Step 5: Commit**

```bash
git add src/drugref/ingest/fda_cyp.py tests/test_fda_cyp_parser.py
git commit -m "feat(fda-cyp): footnote markers, including the comma-separated list

FDA prints 'ritonavir 14, 15,' -- several markers, comma-separated, with a
trailing comma. A stripper handling 'adefovir 1' but not this leaves the
substance named 'ritonavir 14, 15,', which resolves to nothing, so one of the
most important CYP3A inhibitors in medicine drops out silently while the run
reports success.

The rule is deliberately precise in both directions: a marker is a bare
integer or single lower-case letter, so 'peginterferon alpha-2a' and
'MATE2-K' keep their own characters."
```

---

### Task 4: The cell grammar, the closed vocabulary, and the role cross-check

Implements spec §2.2 and §8 — the heart of the parser.

**Files:**
- Modify: `src/drugref/ingest/fda_cyp.py`
- Modify: `tests/test_fda_cyp_parser.py`

**Interfaces:**
- Consumes: `split_footnotes`, `ROLE_COLUMNS`.
- Produces: `fda_cyp.PATHWAYS: frozenset[str]`; `fda_cyp.CypTuple` (frozen dataclass: `row_ordinal: int`, `raw_substance: str`, `substance: str`, `column_heading: str`, `raw_cell: str`, `system: str`, `pathway: str`, `role: str`, `potency: str | None`, `footnote_markers: str | None`); `fda_cyp.parse_cell(raw_cell, column_index, column_heading) -> list[tuple[str, str | None]]` returning `(pathway, markers)` pairs; `fda_cyp.parse_table(page) -> list[CypTuple]`.

- [ ] **Step 1: Write the failing tests**

```python
def test_a_simple_cell_yields_one_pathway():
    assert fda_cyp.parse_cell("2D6 moderate inhibitor", 2, "CYP Mod INH") == [("2D6", None)]


def test_a_semicolon_list_yields_several_pathways():
    assert fda_cyp.parse_cell("P-gp; BCRP inhibitor", 9, "TRNSP INH") == [
        ("P-gp", None), ("BCRP", None)]


def test_the_word_and_is_also_a_separator():
    assert fda_cyp.parse_cell("3A and 2C19 weak inhibitor", 3, "CYP WK INH") == [
        ("3A", None), ("2C19", None)]


def test_a_comma_is_also_a_separator():
    assert fda_cyp.parse_cell("1A2, 2B6 weak inducer", 6, "CYP WK IND") == [
        ("1A2", None), ("2B6", None)]


def test_a_cyp_prefix_is_normalised_away():
    """The page writes bare '3A' and prefixed 'CYP3A' for the same pathway."""
    assert fda_cyp.parse_cell("CYP3A moderate inducer", 5, "CYP Mod IND") == [("3A", None)]


def test_a_trailing_noun_is_not_a_pathway():
    assert fda_cyp.parse_cell("BCRP and P-gp transporters", 9, "TRNSP INH") == [
        ("BCRP", None), ("P-gp", None)]


def test_a_footnote_attached_to_ONE_pathway_is_kept_with_it():
    """ciprofloxacin: '1A2 20 ; 3A moderate inhibitor'. Marker 20 belongs to 1A2
    ALONE, mid-cell -- a trailing-only stripper turns '1A2 20' into a pathway and
    mints the garbage class 'cyp:1a2 20:inhibitor:moderate'.
    """
    assert fda_cyp.parse_cell("1A2 20 ; 3A moderate inhibitor", 2, "CYP Mod INH") == [
        ("1A2", "20"), ("3A", None)]


def test_a_footnote_on_SEVERAL_pathways_is_kept_with_each():
    """rifampin: 'OATP1B1 13 ; OATP1B3 13 inhibitor'."""
    assert fda_cyp.parse_cell("OATP1B1 13 ; OATP1B3 13 inhibitor", 9, "TRNSP INH") == [
        ("OATP1B1", "13"), ("OATP1B3", "13")]


def test_the_role_word_may_repeat_per_list_item():
    """teriflunomide: 'BCRP; OATP1B1 inhibitor; OAT3 inhibitor'. The trailing role
    phrase covers only the items that do not state their own -- reading it as part
    of the pathway mints 'transporter:oatp1b1 inhibitor'.
    """
    assert fda_cyp.parse_cell("BCRP; OATP1B1 inhibitor; OAT3 inhibitor", 9, "TRNSP INH") == [
        ("BCRP", None), ("OATP1B1", None), ("OAT3", None)]


def test_an_unknown_pathway_token_ABORTS():
    """The closed vocabulary, and the reason this parser exists in this shape.

    A lenient parse of the real page produces 69 classes instead of 65 while
    reporting zero errors. Four are garbage, minted with real immortal UUIDs.
    """
    with pytest.raises(fda_cyp.FdaCypParseError, match="pathway"):
        fda_cyp.parse_cell("CYP9Z9 moderate inhibitor", 2, "CYP Mod INH")


def test_a_cell_whose_role_disagrees_with_its_COLUMN_aborts():
    """The page states role and potency TWICE -- in the column heading and in the
    cell -- so they can be cross-checked for free. A disagreement means the
    table's shape changed under an unchanged checksum, which must stop the ingest
    rather than be resolved by preferring one of them.
    """
    with pytest.raises(fda_cyp.FdaCypParseError, match="disagree"):
        fda_cyp.parse_cell("2D6 strong inhibitor", 2, "CYP Mod INH")  # column says moderate


def test_moderately_sensitive_matches_the_columns_moderate_sensitive():
    """The legend's word is not always the cell's: 'moderately sensitive
    substrate' against the column's 'Mod SENS SUB'. Not a disagreement.
    """
    assert fda_cyp.parse_cell("2C8 and 3A moderately sensitive substrate", 8,
                              "CYP Mod SENS SUB") == [("2C8", None), ("3A", None)]


def test_OATP1B_is_its_own_pathway_and_is_never_expanded():
    """FDA writes the coarser 'OATP1B' where other rows say OATP1B1/OATP1B3.
    Expanding it would manufacture a specificity FDA declined to state.
    """
    assert fda_cyp.parse_cell("OATP1B transporter inhibitor", 9, "TRNSP INH") == [
        ("OATP1B", None)]
    assert "OATP1B" in fda_cyp.PATHWAYS


def test_parse_table_over_the_fixture_produces_no_garbage_pathway(fixture_html):
    """Every pathway in every tuple is in the closed vocabulary -- the property
    the four garbage classes violated.
    """
    for tup in fda_cyp.parse_table(fixture_html):
        assert tup.pathway in fda_cyp.PATHWAYS


def test_parse_table_carries_the_row_ordinal_because_names_repeat(fixture_html):
    """aprepitant occupies TWO rows, so the substance name is not a row key."""
    tuples = fda_cyp.parse_table(fixture_html)
    ordinals = {t.row_ordinal for t in tuples if t.substance == "aprepitant"}
    assert len(ordinals) == 2
```

- [ ] **Step 2: Run and verify failure**

```bash
uv run pytest tests/test_fda_cyp_parser.py -v -k "cell or parse_table or OATP1B"
```
Expected: `AttributeError: ... has no attribute 'parse_cell'`.

- [ ] **Step 3: Implement**

```python
import dataclasses

# THE CLOSED PATHWAY VOCABULARY. An unrecognised token aborts the ingest.
#
# This is not defensiveness; it is the finding that justified the whole module.
# A lenient parse of the real page -- one that strips trailing footnotes and
# accepts whatever remains -- produces 69 classes instead of 65 while reporting
# ZERO errors, and four are garbage minted with real immortal UUIDs:
#   cyp:1a2 20:inhibitor:moderate            (ciprofloxacin, mid-cell footnote)
#   transporter:oatp1b1 13:inhibitor         (rifampin, footnote on both pathways)
#   transporter:oatp1b3 13:inhibitor
#   transporter:oatp1b1 inhibitor:inhibitor  (teriflunomide, per-item role phrase)
#
# OATP1B is listed SEPARATELY from OATP1B1 and OATP1B3 and is never expanded into
# them: FDA writes the coarser name on some rows, and expanding it would
# manufacture a specificity FDA declined to state.
PATHWAYS = frozenset({
    "1A2", "2B6", "2C8", "2C9", "2C19", "2D6", "3A",
    "P-gp", "BCRP", "OATP1B1", "OATP1B3", "OATP1B",
    "OAT1", "OAT3", "OCT2", "MATE1", "MATE2-K",
})

# Case-folded lookup, so 'p-gp' and 'P-gp' are one pathway while the CANONICAL
# spelling (which reaches source_code and class_name) stays FDA's own.
_PATHWAY_BY_FOLD = {p.upper(): p for p in PATHWAYS}

# The role phrase that closes a cell (or a list item). 'moderately sensitive' and
# 'moderate sensitive' are the SAME band under two spellings -- the legend says
# one, some cells say the other.
_ROLE_PHRASE = re.compile(
    r"\b(strong|moderate|moderately|weak|sensitive|"
    r"moderate sensitive|moderately sensitive)?\s*"
    r"(inhibitors?|inducers?|substrates?)\s*$", re.I)

# Separators. THREE spellings of one concept: ';', ',' and the word 'and'.
_SEPARATOR = re.compile(r";|,|\band\b", re.I)

# Nouns FDA appends to a pathway list ('BCRP and P-gp transporters'). Not pathways.
_TRAILING_NOUN = re.compile(r"\b(transporters?|enzymes?)\b", re.I)


@dataclasses.dataclass(frozen=True)
class CypTuple:
    """One (substance x pathway x role x potency) fact, before any DB contact.

    row_ordinal is carried because THE SUBSTANCE NAME IS NOT A KEY: aprepitant
    occupies two rows, and FDA publishes no row identifier, so the 1-based
    position is the only stable within-release handle back to the exact line.

    raw_substance keeps FDA's printed form INCLUDING markers ('ritonavir 14, 15,')
    while `substance` is the cleaned name -- the raw fact and the derived one are
    both stored, never one in place of the other.
    """
    row_ordinal: int
    raw_substance: str
    substance: str
    column_heading: str
    raw_cell: str
    system: str
    pathway: str
    role: str
    potency: str | None
    footnote_markers: str | None


def _normalise_potency(word: str | None) -> str | None:
    if word is None:
        return None
    folded = word.lower()
    return "moderate" if folded == "moderately" else folded


def parse_cell(raw_cell: str, column_index: int,
               column_heading: str) -> list[tuple[str, str | None]]:
    """One cell to its (pathway, footnote_markers) pairs.

    THE GRAMMAR, derived from the real bytes rather than assumed: a cell is a
    list of `pathway [footnote] [role phrase]` items separated by ';', ',' or the
    word 'and', closed by a trailing role phrase that applies to every item which
    did not state its own.

    Raises FdaCypParseError on an unknown pathway token, or when the cell's own
    role/potency disagrees with the column it sits in.
    """
    system, column_role, column_potency = ROLE_COLUMNS[column_index]
    body, _ = split_footnotes(raw_cell)

    match = _ROLE_PHRASE.search(body)
    if not match:
        raise FdaCypParseError(
            f"cell {raw_cell!r} in column {column_heading!r} states no role phrase")

    # THE CROSS-CHECK. The page states role and potency twice, so verifying them
    # against each other costs nothing and catches a source whose shape changed
    # under an unchanged checksum. Preferring one over the other would hide it.
    cell_role = match.group(2).lower().rstrip("s")
    cell_potency = _normalise_potency(match.group(1))
    if cell_role != column_role:
        raise FdaCypParseError(
            f"cell {raw_cell!r} says role {cell_role!r} but column "
            f"{column_heading!r} says {column_role!r} -- they disagree")
    if cell_potency is not None and column_potency is not None:
        # 'moderate sensitive' is spelled 'moderately sensitive' in some cells and
        # reaches here as bare 'moderate' once the role word is split off.
        if not column_potency.startswith(cell_potency):
            raise FdaCypParseError(
                f"cell {raw_cell!r} says potency {cell_potency!r} but column "
                f"{column_heading!r} says {column_potency!r} -- they disagree")

    listed = _TRAILING_NOUN.sub("", body[:match.start()]).strip().rstrip(",")
    pairs: list[tuple[str, str | None]] = []
    for item in (part.strip() for part in _SEPARATOR.split(listed)):
        if not item:
            continue
        # Each item may carry its OWN role phrase (teriflunomide) and its OWN
        # footnote (ciprofloxacin, rifampin). Strip both, per item.
        item = _ROLE_PHRASE.sub("", item).strip()
        token, markers = split_footnotes(item)
        token = re.sub(r"^CYP", "", token, flags=re.I).strip()
        if not token:
            continue
        canonical = _PATHWAY_BY_FOLD.get(token.upper())
        if canonical is None:
            raise FdaCypParseError(
                f"unknown pathway {token!r} in cell {raw_cell!r} "
                f"(column {column_heading!r}). The closed vocabulary is "
                f"{sorted(PATHWAYS)}. Widen it deliberately or fix the parse -- "
                "accepting it would mint a class with an immortal UUID.")
        pairs.append((canonical, markers))
    return pairs


def parse_table(page: str) -> list[CypTuple]:
    """The whole table to its tuples, in row then column order."""
    headings = _column_headings(page)
    tuples: list[CypTuple] = []
    for ordinal, row in enumerate(extract_rows(page), start=1):
        raw_substance = row[0]
        substance, _ = split_footnotes(raw_substance)
        for index in sorted(ROLE_COLUMNS):
            raw_cell = row[index]
            if not raw_cell:
                continue
            system, role, potency = ROLE_COLUMNS[index]
            heading = headings[index]
            for pathway, cell_markers in parse_cell(raw_cell, index, heading):
                # A row-level marker qualifies EVERY cell in that row; a cell- or
                # item-level one qualifies only where it sits. Both are kept.
                _, row_markers = split_footnotes(raw_substance)
                markers = ", ".join(m for m in (row_markers, cell_markers) if m) or None
                tuples.append(CypTuple(
                    row_ordinal=ordinal, raw_substance=raw_substance,
                    substance=substance, column_heading=heading, raw_cell=raw_cell,
                    system=system, pathway=pathway, role=role, potency=potency,
                    footnote_markers=markers))
    return tuples


def _column_headings(page: str) -> list[str]:
    """FDA's own column headings, read from the header row rather than restated.

    Restating them here would be a second copy of a vocabulary the page already
    publishes -- the 'written down twice' hazard this project keeps paying for.
    """
    tables = _TABLE.findall(page)
    header = _ROW.findall(tables[DATA_TABLE_INDEX])[0]
    return [_clean(cell) for cell in _CELL.findall(header)]
```

- [ ] **Step 4: Run and verify it passes**

```bash
uv run pytest tests/test_fda_cyp_parser.py -v
```
Expected: 24 passed.

- [ ] **Step 5: Verify against the FULL page, not just the fixture**

The fixture is a subset; the counts in the spec are the whole table's. This is the check that the grammar generalises:

```bash
uv run python -c "
from drugref.ingest import fda_cyp
import pathlib, collections
page = pathlib.Path('downloads/FDA/fda_cyp_2026-05-29.html').read_text(encoding='utf-8')
tuples = fda_cyp.parse_table(page)
classes = {(t.system, t.pathway, t.role, t.potency) for t in tuples}
print('tuples :', len(tuples))
print('classes:', len(classes))
print('rows   :', len({t.row_ordinal for t in tuples}))
print('qualified tuples:', sum(1 for t in tuples if t.footnote_markers))
"
```

Expected: **415 tuples, 65 classes.** If either differs, stop and reconcile against spec §2.1 before continuing — the numbers are the design's evidence, and a silent divergence here is the failure this whole task exists to prevent.

- [ ] **Step 6: Commit**

```bash
git add src/drugref/ingest/fda_cyp.py tests/test_fda_cyp_parser.py
git commit -m "feat(fda-cyp): the cell grammar and the closed pathway vocabulary

A cell is a list of 'pathway [footnote] [role]' items separated by ';', ','
or 'and', closed by a trailing role phrase covering the items that state
none of their own. All three separators, per-item roles and per-item
footnotes were found in the real bytes.

An unknown pathway token aborts. A lenient parse produces 69 classes rather
than 65 while reporting zero errors, and four are garbage minted with real
immortal UUIDs -- ciprofloxacin's mid-cell footnote, rifampin's on both
pathways, teriflunomide's repeated role word.

The page states role and potency twice, in the column heading and the cell,
so they are cross-checked. A disagreement means the shape changed under an
unchanged checksum and stops the ingest rather than being resolved by
preferring one."
```

---

### Task 5: Release identity from `dateModified`

Implements spec §13 — the correction to the spike.

**Files:**
- Modify: `src/drugref/ingest/fda_cyp.py`
- Modify: `tests/test_fda_cyp_parser.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `fda_cyp.parse_release(page: str) -> str` returning `'2026-05-29T14:00'`.

- [ ] **Step 1: Write the failing tests**

```python
def test_the_release_is_read_from_the_pages_own_dateModified():
    """The spike said the HTML carries no release identifier. It carries one.

    Fetch time records when drugref LOOKED; dateModified records when FDA CHANGED
    the content, and only the second distinguishes a re-fetch of unchanged
    material from a genuine revision.
    """
    page = '<script>{"dateModified": "Fri, 05/29/2026 - 14:00"}</script>'
    assert fda_cyp.parse_release(page) == "2026-05-29T14:00"


def test_the_meta_tag_is_an_accepted_second_spelling():
    page = '<meta property="article:modified_time" content="Fri, 05/29/2026 - 14:00" />'
    assert fda_cyp.parse_release(page) == "2026-05-29T14:00"


def test_a_page_without_a_modified_date_FAILS_and_names_the_field():
    """It does NOT silently substitute fetch time. That would put a value with a
    different meaning in the same column, and this project has already lost
    rounds to one field carrying two meanings.
    """
    with pytest.raises(fda_cyp.FdaCypParseError, match="dateModified"):
        fda_cyp.parse_release("<html><body>no date here</body></html>")


def test_the_real_page_reports_the_expected_release():
    page = pathlib.Path("downloads/FDA/fda_cyp_2026-05-29.html").read_text(encoding="utf-8")
    assert fda_cyp.parse_release(page) == "2026-05-29T14:00"
```

> The last test reads a gitignored file. Guard it exactly as the repo guards other release-dependent tests — `@pytest.mark.skipif(not pathlib.Path(...).exists(), reason="live page not downloaded")` — so a fresh clone does not fail on a file it cannot have.

- [ ] **Step 2: Run and verify failure**

```bash
uv run pytest tests/test_fda_cyp_parser.py -v -k release
```
Expected: `AttributeError: ... has no attribute 'parse_release'`.

- [ ] **Step 3: Implement**

```python
# FDA's CMS prints the modification stamp in three places with one format:
# JSON-LD "dateModified", og:updated_time and article:modified_time.
# Accepting all three is not redundancy -- it is not knowing which the CMS will
# keep, and they are read in this order.
_MODIFIED = re.compile(
    r'"dateModified"\s*:\s*"([^"]+)"'
    r'|(?:article:modified_time|og:updated_time)"\s+content="([^"]+)"')

# 'Fri, 05/29/2026 - 14:00'
_STAMP = re.compile(r"(\d{2})/(\d{2})/(\d{4})\s*-\s*(\d{2}:\d{2})")


def parse_release(page: str) -> str:
    """The page's own modification stamp, as '2026-05-29T14:00'.

    WHY NOT FETCH TIME, which the source spike proposed: fetch time records when
    drugref looked, and dateModified records when FDA changed the content. Only
    the second can tell a re-fetch of unchanged material from a genuine revision
    -- which is the question check_release_agreement and every per-source rebuild
    actually ask.

    RAISES rather than falling back. Substituting fetch time would put a value
    with a DIFFERENT MEANING into upstream_release, and one field carrying two
    meanings is a defect this project has already paid for more than once. If FDA
    stops publishing the field, that is a decision for a human, not a default.
    """
    for match in _MODIFIED.finditer(page):
        raw = match.group(1) or match.group(2)
        stamp = _STAMP.search(raw or "")
        if stamp:
            month, day, year, clock = stamp.groups()
            return f"{year}-{month}-{day}T{clock}"
    raise FdaCypParseError(
        "the page carries no dateModified / article:modified_time / "
        "og:updated_time stamp, so its release identity is unknown. Fetch time is "
        "NOT a substitute: it records when drugref looked, not when FDA changed "
        "the content. Decide deliberately before ingesting this page.")
```

- [ ] **Step 4: Run and verify it passes, then commit**

```bash
uv run pytest tests/test_fda_cyp_parser.py -v
git add src/drugref/ingest/fda_cyp.py tests/test_fda_cyp_parser.py
git commit -m "feat(fda-cyp): release identity from the page's own dateModified

The source spike said the HTML carries no release identifier. It carries one,
in JSON-LD and two meta tags: 'Fri, 05/29/2026 - 14:00'.

Fetch time records when drugref looked; dateModified records when FDA changed
the content, and only the second distinguishes a re-fetch of unchanged
material from a revision.

A page without the field FAILS and names it rather than substituting fetch
time -- that would put a value with a different meaning in the same column."
```

---

### Task 6: The orchestrator — resolution, dispositions, and the writes

Implements spec §4.3, §5, §7, §7.1 and §11's rebuild safety. This is the only writer.

**Files:**
- Create: `src/drugref/ingest/fda_cyp_run.py`
- Create: `tests/test_fda_cyp_run.py`

**Interfaces:**
- Consumes: `fda_cyp.parse_table`, `fda_cyp.parse_release`, `classes.upsert_class`, `classes.add_membership`, `classes.moieties_by_display_name`, `classes.clear_source_edges`, `provenance.open_run`, `provenance.finish_run`, `db.clear_source_tables`, `ingest.checksum.checksum`.
- Produces: `fda_cyp_run.SOURCE = 'FDA-CYP'`, `fda_cyp_run.WRITER = 'fda_cyp_run'`, `fda_cyp_run.NON_DRUG_ENTITIES: frozenset[str]`, `fda_cyp_run.FdaCypSummary` (frozen dataclass), `fda_cyp_run.ingest_fda_cyp(conn, *, page_path, upstream_release=None) -> FdaCypSummary`, `fda_cyp_run.source_code(system, pathway, role, potency) -> str`, `fda_cyp_run.class_name(system, pathway, role, potency) -> str`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_fda_cyp_run.py
"""The FDA-CYP orchestrator: DB-gated.

Every test here pins a DECISION from the design, not an implementation detail.
"""
import pathlib

import pytest

from drugref.ingest import fda_cyp_run

FIXTURE = pathlib.Path(__file__).parent / "fixtures" / "fda_cyp_table.html"


def test_the_source_code_is_deterministic_and_lower_case():
    assert fda_cyp_run.source_code("CYP", "3A", "inhibitor", "strong") == "cyp:3a:inhibitor:strong"
    assert fda_cyp_run.source_code("transporter", "P-gp", "substrate", None) == "transporter:pgp:substrate"
    assert fda_cyp_run.source_code("transporter", "MATE2-K", "inhibitor", None) == "transporter:mate2k:inhibitor"


def test_the_class_name_is_source_tagged():
    """So no consumer or UI can mistake it for one of MED-RT's [MoA] classes.
    MED-RT's bracketed suffix is PUBLISHED BY MED-RT; this one is drugref's own
    label and says so.
    """
    assert fda_cyp_run.class_name("CYP", "3A", "inhibitor", "strong") == \
        "CYP3A strong inhibitor [FDA-CYP]"
    assert fda_cyp_run.class_name("transporter", "P-gp", "substrate", None) == \
        "P-gp substrate [FDA-CYP]"


@pytest.mark.usefixtures("conn")
def test_a_qualified_cell_writes_NO_membership(conn):
    """THE SECTION 3 CASE, pinned directly.

    bupropion's row asserts '2B6 sensitive substrate' while its footnote 2 says
    "Bupropion itself is not a sensitive substrate." Promoting it would make
    drugref assert the OPPOSITE of its cited source.
    """
    fda_cyp_run.ingest_fda_cyp(conn, page_path=FIXTURE, upstream_release="2026-05-29T14:00")
    membership = conn.execute(
        "SELECT count(*) FROM drugref.class_membership m "
        "JOIN drugref.substance_class c ON c.class_uuid = m.class_uuid "
        "JOIN drugref.substance_moiety s ON s.moiety_uuid = m.moiety_uuid "
        "WHERE c.source = 'FDA-CYP' AND lower(s.display_name) = 'bupropion'").fetchone()[0]
    assert membership == 0, "a footnoted cell must not become a membership"

    withheld = conn.execute(
        "SELECT count(*) FROM drugref.fda_cyp_assertion "
        "WHERE lower(raw_substance) LIKE 'bupropion%' "
        "  AND disposition = 'withheld_qualified'").fetchone()[0]
    assert withheld > 0, "and it must still be recorded, with its footnote"


@pytest.mark.usefixtures("conn")
def test_every_withheld_row_carries_its_footnote_text(conn):
    """Withholding without the reason would be a drop wearing a disposition."""
    fda_cyp_run.ingest_fda_cyp(conn, page_path=FIXTURE, upstream_release="2026-05-29T14:00")
    missing = conn.execute(
        "SELECT count(*) FROM drugref.fda_cyp_assertion "
        "WHERE disposition = 'withheld_qualified' "
        "  AND (footnote_text IS NULL OR footnote_markers IS NULL)").fetchone()[0]
    assert missing == 0


@pytest.mark.usefixtures("conn")
def test_S_mephenytoin_is_unresolved_and_NOT_mapped_to_mephenytoin(conn):
    """Issue 128. S-mephenytoin is the reference CYP2C19 probe substrate, and it
    is the ENANTIOMER that makes it one. Mapping it to the racemate asserts a
    stereochemistry claim FDA did not make, in the direction that ADDS membership.
    """
    fda_cyp_run.ingest_fda_cyp(conn, page_path=FIXTURE, upstream_release="2026-05-29T14:00")
    row = conn.execute(
        "SELECT disposition, resolved_moiety_uuid FROM drugref.fda_cyp_assertion "
        "WHERE raw_substance ILIKE 'S-mephenytoin%' LIMIT 1").fetchone()
    assert row is not None
    assert row[0] == "unresolved_substance"
    assert row[1] is None


@pytest.mark.usefixtures("conn")
def test_the_disposition_never_names_a_cause_drugref_inferred(conn):
    """Spec section 7.1 and the standing rule. Six recognisable categories exist
    in the residue; only the two FDA asserts are stored. Calling R-venlafaxine an
    'enantiomer' would be a chemical relationship inferred from a string prefix --
    issue 122's manufactured-cause defect.
    """
    fda_cyp_run.ingest_fda_cyp(conn, page_path=FIXTURE, upstream_release="2026-05-29T14:00")
    live = {row[0] for row in conn.execute(
        "SELECT DISTINCT disposition FROM drugref.fda_cyp_assertion").fetchall()}
    assert live <= {"member", "withheld_qualified", "unresolved_substance",
                    "combination_regimen", "non_drug_entity"}


@pytest.mark.usefixtures("conn")
def test_curcumin_resolves_as_a_moiety_AND_is_still_a_non_drug_entity(conn):
    """The independence in section 7, and it inverts the obvious assumption:
    curcumin and diosmin are two of FDA's five declared non-drugs and they DO
    resolve. So the non-drug list must be FDA's own pinned five, read from its
    prose, never inferred from a resolution failure.
    """
    fda_cyp_run.ingest_fda_cyp(conn, page_path=FIXTURE, upstream_release="2026-05-29T14:00")
    row = conn.execute(
        "SELECT disposition FROM drugref.fda_cyp_assertion "
        "WHERE lower(raw_substance) = 'curcumin' LIMIT 1").fetchone()
    assert row[0] == "non_drug_entity"
    assert "curcumin" in fda_cyp_run.NON_DRUG_ENTITIES


@pytest.mark.usefixtures("conn")
def test_a_combination_regimen_is_never_exploded_into_its_components(conn):
    """FDA reports the role FOR THE REGIMEN. Assigning it to atazanavir or to
    ritonavir individually is an inference FDA did not make.
    """
    fda_cyp_run.ingest_fda_cyp(conn, page_path=FIXTURE, upstream_release="2026-05-29T14:00")
    rows = conn.execute(
        "SELECT disposition, resolved_moiety_uuid FROM drugref.fda_cyp_assertion "
        "WHERE raw_substance ILIKE 'atazanavir and ritonavir%'").fetchall()
    assert rows
    for disposition, moiety in rows:
        assert disposition == "combination_regimen"
        assert moiety is None


@pytest.mark.usefixtures("conn")
def test_a_row_with_a_near_name_is_counted_as_unresolved(conn):
    """registry_near_name is EVIDENCE, never coverage. This test exists because a
    nullable text column beside an unresolved row is precisely the shape a later
    reader will be tempted to count.
    """
    fda_cyp_run.ingest_fda_cyp(conn, page_path=FIXTURE, upstream_release="2026-05-29T14:00")
    contradictions = conn.execute(
        "SELECT count(*) FROM drugref.fda_cyp_assertion "
        "WHERE registry_near_name IS NOT NULL AND disposition = 'member'").fetchone()[0]
    assert contradictions == 0


@pytest.mark.usefixtures("conn")
def test_all_classes_are_minted_even_when_every_member_is_withheld(conn):
    """Spec section 4.2. A class whose only members are withheld still exists, so
    a withheld row can name the class it WOULD have joined, and a zero-member
    class is distinguishable from a band FDA never defined.
    """
    summary = fda_cyp_run.ingest_fda_cyp(conn, page_path=FIXTURE,
                                         upstream_release="2026-05-29T14:00")
    minted = conn.execute(
        "SELECT count(*) FROM drugref.substance_class WHERE source = 'FDA-CYP'").fetchone()[0]
    assert minted == summary.classes_minted
    orphaned = conn.execute(
        "SELECT count(*) FROM drugref.fda_cyp_assertion "
        "WHERE disposition = 'withheld_qualified' AND class_uuid IS NULL").fetchone()[0]
    assert orphaned == 0, "a withheld row must still name the class it would have joined"


@pytest.mark.usefixtures("conn")
def test_no_class_parent_edge_is_written(conn):
    """FDA publishes no hierarchy; inventing one and inheriting advice along it
    is the rejected alternative in section 4.2.
    """
    fda_cyp_run.ingest_fda_cyp(conn, page_path=FIXTURE, upstream_release="2026-05-29T14:00")
    edges = conn.execute(
        "SELECT count(*) FROM drugref.class_parent p "
        "JOIN drugref.substance_class c ON c.class_uuid = p.child_class_uuid "
        "WHERE c.source = 'FDA-CYP'").fetchone()[0]
    assert edges == 0


@pytest.mark.usefixtures("conn")
def test_a_second_run_rebuilds_rather_than_duplicating(conn):
    first = fda_cyp_run.ingest_fda_cyp(conn, page_path=FIXTURE, upstream_release="2026-05-29T14:00")
    second = fda_cyp_run.ingest_fda_cyp(conn, page_path=FIXTURE, upstream_release="2026-05-29T14:00")
    assert first.memberships_written == second.memberships_written
    rows = conn.execute("SELECT count(*) FROM drugref.fda_cyp_assertion").fetchone()[0]
    assert rows == second.assertions_written


@pytest.mark.usefixtures("conn")
def test_clearing_FDA_CYP_touches_no_other_sources_classes(conn):
    """Per-source rebuild safety, pinned rather than argued. class_membership has
    no source column of its own, so the clear is scoped through ingest_run.
    """
    before = conn.execute(
        "SELECT count(*) FROM drugref.substance_class WHERE source <> 'FDA-CYP'").fetchone()[0]
    fda_cyp_run.ingest_fda_cyp(conn, page_path=FIXTURE, upstream_release="2026-05-29T14:00")
    fda_cyp_run.ingest_fda_cyp(conn, page_path=FIXTURE, upstream_release="2026-05-29T14:00")
    after = conn.execute(
        "SELECT count(*) FROM drugref.substance_class WHERE source <> 'FDA-CYP'").fetchone()[0]
    assert before == after


@pytest.mark.usefixtures("conn")
def test_this_slice_creates_no_interaction_content(conn):
    """Section 9's refusal, checked rather than trusted: 20 strong CYP3A
    inhibitors x 40 sensitive CYP3A substrates would be 800 pairs no source
    asserts.
    """
    before = conn.execute("SELECT count(*) FROM drugref.ddi_candidate_pair").fetchone()[0]
    fda_cyp_run.ingest_fda_cyp(conn, page_path=FIXTURE, upstream_release="2026-05-29T14:00")
    after = conn.execute("SELECT count(*) FROM drugref.ddi_candidate_pair").fetchone()[0]
    assert before == after
    assert conn.execute(
        "SELECT count(*) FROM drugref.class_contraindication "
        "WHERE source = 'FDA-CYP'").fetchone()[0] == 0
```

- [ ] **Step 2: Run and verify failure**

```bash
DRUGREF_TEST_DSN='host=localhost port=5532 dbname=drugref_test user=postgres' \
  uv run pytest tests/test_fda_cyp_run.py -v
```
Expected: `ModuleNotFoundError: No module named 'drugref.ingest.fda_cyp_run'`.

- [ ] **Step 3: Implement the orchestrator**

Follow `gsrs_run.py`'s ordering exactly — parse and checksum **before** opening the run, so a crash leaves no half-written run row; then clear, insert, rebuild questions, finish.

```python
# src/drugref/ingest/fda_cyp_run.py
"""Orchestrate one FDA-CYP ingest: parse -> resolve -> clear -> write -> rebuild.

The ONLY writer of drugref's FDA-CYP rows, per the architecture invariant:
parsers are pure, orchestrators own the transaction.

ORDER MATTERS, as for every other feed here:
  1. parse and checksum BEFORE opening the run, so a crash during the parse
     leaves no half-written run row;
  2. clear this source's old rows, so a re-ingest REPLACES rather than accumulates;
  3. write classes, then memberships, then the assertion projection;
  4. rebuild the question register, finish, commit.

WHAT THIS MODULE REFUSES TO DO, and it is most of the design:

* It writes NO class_contraindication and NO DDI pair. FDA calls its table an
  optional, non-exhaustive interpretive guide; joining the inhibitor and
  substrate columns would manufacture ~800 pairs no source asserts.
* It promotes NO footnoted cell to membership. Two of FDA's footnotes NEGATE the
  row they sit on, and deciding which do is a clinical reading of prose.
* It bridges NO name. Six recognisable categories sit in the resolution residue
  and every one of them is a different job -- see the standing rule in
  PROJECT-NOTES, and issue 128 for the enantiomers specifically.
"""
import dataclasses
import logging
import pathlib

import psycopg

from drugref import classes, db, ids, provenance, questions
from drugref.ingest import fda_cyp
from drugref.ingest.checksum import checksum

SOURCE = "FDA-CYP"
# WHICH orchestrator this is, as distinct from SOURCE, the authority it reads
# (db/025). Declared in provenance.WRITERS and db/039's CHECK -- a pair.
WRITER = "fda_cyp_run"

# The axis FDA's roles sit on. Both values predate this slice (db/003), which is
# the whole argument for projecting the roles as PK classes rather than inventing
# a mechanism for them.
CONCEPT_TYPE = "PK"
RELATIONSHIP = "has_PK"

# The projection this source owns, cleared per-source on every re-ingest.
FDA_CYP_TABLES = ("fda_cyp_assertion",)

# FDA'S OWN FIVE, quoted from the page's prose rather than inferred:
#   "Table 1 also includes five other substances that interact with CYP enzymes
#    and transporter systems (i.e., St. John's wort (a dietary supplement),
#    curcumin (a supplement), diosmin (a supplement), tobacco (smoking) and
#    grapefruit juice (a food))."
#
# READ THAT LIST CAREFULLY BEFORE CHANGING IT: curcumin and diosmin RESOLVE as
# ordinary drugref moieties. Non-drug and unresolvable are INDEPENDENT properties,
# so this list can never be derived from a resolution failure -- it can only be
# FDA's own statement. Matched case-insensitively against the footnote-stripped
# name; the apostrophe in "St. John's wort" is U+2019 as FDA prints it.
NON_DRUG_ENTITIES = frozenset({
    "st. john’s wort", "curcumin", "diosmin", "tobacco (smoking)", "grapefruit juice",
})

log = logging.getLogger(__name__)


@dataclasses.dataclass(frozen=True)
class FdaCypSummary:
    """What one FDA-CYP run did -- returned so a caller or test can assert on it.

    EVERY FIELD IS NAMED FOR WHAT IT ACTUALLY COUNTS. `assertions_written` is
    every tuple parsed; `memberships_written` is the subset promoted, and the two
    differ by exactly the withheld and unresolved populations. A summary line the
    CLI prints is the number a reader quotes later, so a name that overstates its
    scope is a wrong number with a plausible source.
    """
    upstream_release: str
    classes_minted: int
    memberships_written: int
    assertions_written: int
    withheld_qualified: int
    unresolved_substances: int
    combination_regimens: int
    non_drug_entities: int
    questions_registered: int


def source_code(system: str, pathway: str, role: str, potency: str | None) -> str:
    """The deterministic key a class_uuid is minted from.

    EXPLICITLY A DRUGREF NORMALISATION KEY, NOT AN FDA IDENTIFIER -- FDA publishes
    no code for these classes. The live URL, dateModified, checksum and raw column
    heading carry the provenance, which is why substance_class.published_code is
    left NULL rather than filled with something invented.

    Punctuation is folded out of the pathway ('P-gp' -> 'pgp', 'MATE2-K' ->
    'mate2k') so the key is stable against a spelling change upstream that does
    not change the concept.
    """
    prefix = "cyp" if system == "CYP" else "transporter"
    token = pathway.lower().replace("-", "").replace(" ", "")
    parts = [prefix, token, role]
    if potency:
        parts.append(potency.replace(" ", "-"))
    return ":".join(parts)


def class_name(system: str, pathway: str, role: str, potency: str | None) -> str:
    """The cached display name, SOURCE-TAGGED.

    '[FDA-CYP]' rather than MED-RT's '[MoA]' shape, so no consumer or UI can
    mistake one for the other. MED-RT's bracketed suffix is published BY MED-RT;
    this one is drugref's own label, and saying so is the difference between a
    label and a claim about what FDA published.
    """
    stem = f"CYP{pathway}" if system == "CYP" else pathway
    band = f"{potency} " if potency else ""
    return f"{stem} {band}{role} [FDA-CYP]"
```

Then `ingest_fda_cyp` itself. Structure it as: parse → build the case-folded name index → classify each tuple's disposition → open run → clear → write → `questions.register_from_gaps` → `finish_run`. Two rules the tests above enforce:

- **Resolution is exact and case-insensitive, and ambiguity is unresolved.** Build the fold from `classes.moieties_by_display_name(conn)`. Today no display name collides under `lower()` and no FDA name matches more than one moiety — both measured — but the registry grows, so a name resolving to several moieties must land as `unresolved_substance`, never "pick the first".
- **Disposition is decided in this order**, because the categories overlap: `non_drug_entity` (grapefruit juice is *both* non-drug and footnoted) → `combination_regimen` → `unresolved_substance` → `withheld_qualified` → `member`. Pin the order with a test on grapefruit juice.

- [ ] **Step 4: Run the tests, then the whole suite**

```bash
DRUGREF_TEST_DSN='host=localhost port=5532 dbname=drugref_test user=postgres' \
  uv run pytest tests/test_fda_cyp_run.py -v
DRUGREF_TEST_DSN='host=localhost port=5532 dbname=drugref_test user=postgres' uv run pytest
ruff check .
```

- [ ] **Step 5: Commit**

```bash
git add src/drugref/ingest/fda_cyp_run.py tests/test_fda_cyp_run.py
git commit -m "feat(fda-cyp): the orchestrator, and everything it refuses to write

Classification membership and nothing else: no class_contraindication, no DDI
pair, no curated row, no read-path change.

A footnoted cell writes no membership -- bupropion's row asserts '2B6
sensitive substrate' while its own footnote says it is not one. It is
recorded with the footnote instead, and raises a question.

No name is bridged. Resolution is exact and case-insensitive, and a name
matching several moieties is unresolved rather than resolved to the first.
S-mephenytoin stays unresolved: issue 128.

Disposition order is non-drug, then regimen, then unresolved, then withheld,
because the categories overlap -- grapefruit juice is both a non-drug and
footnoted."
```

---

### Task 7: Questions, and the gap-view wiring

Implements spec §6.3–6.4 and §5's "raises a question".

**Files:**
- Modify: `src/drugref/questions.py` (the `_GAP_SOURCES` dict)
- Modify: `tests/test_fda_cyp_run.py`

**Interfaces:**
- Consumes: `drugref.gap_fda_cyp_unadjudicated`.
- Produces: `_GAP_SOURCES['fda_cyp_unadjudicated']`.

- [ ] **Step 1: Write the failing test**

```python
@pytest.mark.usefixtures("conn")
def test_every_unadjudicated_tuple_raises_exactly_one_question(conn):
    """The view's grain IS the gap_key's grain (#41). Grouping coarser folds two
    independent facts onto one immortal question_uuid; finer mints two questions
    for one fact.
    """
    fda_cyp_run.ingest_fda_cyp(conn, page_path=FIXTURE, upstream_release="2026-05-29T14:00")
    gaps = conn.execute(
        "SELECT count(*) FROM drugref.gap_fda_cyp_unadjudicated").fetchone()[0]
    questions_ = conn.execute(
        "SELECT count(*) FROM drugref.open_question "
        "WHERE gap_kind = 'fda_cyp_unadjudicated'").fetchone()[0]
    assert gaps > 0
    assert questions_ == gaps


@pytest.mark.usefixtures("conn")
def test_a_members_row_raises_no_question(conn):
    """A membership drugref already wrote asks nobody anything."""
    fda_cyp_run.ingest_fda_cyp(conn, page_path=FIXTURE, upstream_release="2026-05-29T14:00")
    leaked = conn.execute(
        "SELECT count(*) FROM drugref.gap_fda_cyp_unadjudicated "
        "WHERE disposition = 'member'").fetchone()[0]
    assert leaked == 0


@pytest.mark.usefixtures("conn")
def test_the_question_text_states_the_actual_reason(conn):
    """Four dispositions reach this view and they are four different questions.
    A single text asserting one reason would be #122's defect again -- a message
    asserting a cause it has not confirmed.
    """
    fda_cyp_run.ingest_fda_cyp(conn, page_path=FIXTURE, upstream_release="2026-05-29T14:00")
    texts = [row[0] for row in conn.execute(
        "SELECT question_text FROM drugref.open_question "
        "WHERE gap_kind = 'fda_cyp_unadjudicated'").fetchall()]
    assert any("footnote" in t.lower() for t in texts)
    assert any("regimen" in t.lower() for t in texts)
```

- [ ] **Step 2: Run, verify failure, implement the `_GAP_SOURCES` entry**

Add to `_GAP_SOURCES` in `src/drugref/questions.py`, following the shape of the existing entries. `key_sql` must use the view's exact grain:

```python
    # Slice 5c.2g. FOUR dispositions reach this view and they are four different
    # questions, so the text branches on `disposition` with a CASE rather than
    # asserting one reason for all of them -- #122's lesson: a message may not
    # state a cause it has not confirmed.
    "fda_cyp_unadjudicated": {
        "view": "gap_fda_cyp_unadjudicated",
        "key_sql": "'FDACYP:' || raw_substance || '|' || column_heading || '|' || pathway",
        "text_sql": (
            "CASE disposition "
            "WHEN 'withheld_qualified' THEN "
            "  'Does FDA''s footnote on ' || raw_substance || ' (' || column_heading || "
            "  ', ' || pathway || ') narrow or NEGATE the membership its row states? "
            "Drugref withheld the membership rather than assert either way. "
            "FDA''s note: ' || COALESCE(footnote_text, '(not captured)') "
            "WHEN 'unresolved_substance' THEN "
            "  'Which drugref moiety, if any, is FDA''s ' || raw_substance || '? "
            "No moiety''s display name matches it.' || "
            "  COALESCE(' A near name in the registry is ' || registry_near_name || "
            "  ', which is EVIDENCE for a curator, not a resolution.', '') "
            "WHEN 'combination_regimen' THEN "
            "  'FDA reports this role for the REGIMEN ' || raw_substance || '. "
            "Which component, if any, carries it? Drugref does not assign a "
            "regimen''s role to a component.' "
            "ELSE "
            "  'FDA lists ' || raw_substance || ' as one of five substances that are "
            "not drugs. Should drugref carry it at all, and under what identity?' "
            "END"),
    },
```

- [ ] **Step 3: Run tests, whole suite, `ruff check .`, commit**

```bash
git add src/drugref/questions.py tests/test_fda_cyp_run.py
git commit -m "feat(fda-cyp): the sixteenth question kind

Four dispositions reach the gap view and they are four different questions,
so the text branches on disposition rather than asserting one reason for all
of them -- issue 122's lesson, that a message may not state a cause it has
not confirmed.

The near-name hint is worded as evidence for a curator, not a resolution,
because that is the only thing it is."
```

---

### Task 8: The CLI subcommand

**Files:**
- Modify: `src/drugref/cli_chain.py`
- Modify: `tests/test_fda_cyp_run.py`

- [ ] **Step 1: Read how a sibling subcommand is wired**

```bash
grep -n "gsrs\|onchigh" src/drugref/cli_chain.py src/drugref/cli.py | head -30
```

Follow whichever module actually owns `ingest` subparsers — do not guess from this plan.

- [ ] **Step 2: Write the failing test, implement, verify**

The subcommand is `drugref ingest fda-cyp --release <upstream_release> --page <path>`, with `--release` **optional**: when omitted it comes from `fda_cyp.parse_release`, and when supplied it must *match* what the page says, or the ingest fails. That check is the point — a release passed on the command line that disagrees with the page's own stamp means someone is ingesting bytes they think are a different release.

- [ ] **Step 3: Commit**

---

### Task 9: The measured round

Implements spec §11. **Nothing here changes code** — it produces the numbers the round is judged on.

- [ ] **Step 1: Build the scratch database**

```bash
psql "host=localhost port=5532 dbname=postgres user=postgres" -c \
  "CREATE DATABASE drugref_5c2g TEMPLATE drugref_db038;"
uv run drugref --dsn "host=localhost port=5532 dbname=drugref_5c2g user=postgres" migrate
```

- [ ] **Step 2: Record the before-counts**

```bash
psql "host=localhost port=5532 dbname=drugref_5c2g user=postgres" -qAt -c "
SELECT 'substance_moiety', count(*) FROM drugref.substance_moiety
UNION ALL SELECT 'ddi_candidate_pair', count(*) FROM drugref.ddi_candidate_pair
UNION ALL SELECT 'gap_uncurated_interaction_rule', count(*) FROM drugref.gap_uncurated_interaction_rule
UNION ALL SELECT 'gap_uncurated_condition_contradiction', count(*) FROM drugref.gap_uncurated_condition_contradiction
UNION ALL SELECT 'substance_class', count(*) FROM drugref.substance_class;"
```

Expected: 19,438 · 21,664 · 595 · 168 · (record the class count).

- [ ] **Step 3: Ingest and time it**

```bash
time uv run drugref --dsn "host=localhost port=5532 dbname=drugref_5c2g user=postgres" \
  ingest fda-cyp --page downloads/FDA/fda_cyp_2026-05-29.html
```

- [ ] **Step 4: Record the after-counts and the disposition breakdown**

Re-run Step 2's query — **every one of the first four must be unchanged.** Then:

```bash
psql "host=localhost port=5532 dbname=drugref_5c2g user=postgres" -qAt -c "
SELECT disposition, count(*) FROM drugref.fda_cyp_assertion GROUP BY 1 ORDER BY 2 DESC;
SELECT count(*) FROM drugref.substance_class WHERE source = 'FDA-CYP';
SELECT count(*) FROM drugref.class_membership m JOIN drugref.substance_class c
  ON c.class_uuid = m.class_uuid WHERE c.source = 'FDA-CYP';
SELECT count(*) FROM drugref.open_question WHERE gap_kind = 'fda_cyp_unadjudicated';"
```

**Expected: 65 classes.** Memberships written is **measured, not predicted** — spec §11 explains why: the withheld and unresolved exclusions overlap, and deriving a figure instead of measuring it is how a fabricated number enters the record.

- [ ] **Step 5: Verify the rebuild is idempotent**

Run the same ingest a second time. Every count above must be identical, and `substance_class` for other sources unchanged.

- [ ] **Step 6: Write the results into the three documents**

- **PROJECT-NOTES**: a new `## Slice 5c.2g` section — the measured table, the traps (the four garbage classes, `ritonavir 14, 15,`, the two negating footnotes, curcumin/diosmin resolving), and the standing notes. **Update the suite-count line in § "How to run / test"** — it is that number's ONE home and the fifth occurrence was a near miss.
- **ROADMAP** § 5c.2g: flip ⏳ IN PROGRESS to ✅ DONE with the measured counts.
- **HANDOVER**: regenerate, within the bound its own header states.

- [ ] **Step 7: Final verification, then commit**

```bash
DRUGREF_TEST_DSN='host=localhost port=5532 dbname=drugref_test user=postgres' uv run pytest
ruff check .
wc -l src/drugref/ingest/fda_cyp.py src/drugref/ingest/fda_cyp_run.py docs/HANDOVER.md
```

All green; both modules under ~500 lines; HANDOVER within its bound.

- [ ] **Step 8: Push and open the PR**

Link issue 101's sibling context and reference issue 128 as filed-not-fixed. **Write "issue 128", not "#128", near any closing word** — the commit guard cannot see PR descriptions, which GitHub also parses (issue 124), and that surface has never been measured.

---

## Self-Review

**Spec coverage.** §1 scope → Tasks 1–9; §2.1 matrix → Task 2; §2.2 grammar → Task 4; §2.3 footnotes → Task 3; §3 negating footnotes → Task 6 (`test_a_qualified_cell_writes_NO_membership`); §4.1 identity → Task 1; §4.2 vocabulary + all-classes-minted + no parents → Tasks 4, 6; §4.3 membership → Task 6; §5 withholding → Tasks 6, 7; §6 migration → Task 1; §7/§7.1 residue + dispositions + near-name → Task 6; §7.2 enantiomers → Task 6 (`test_S_mephenytoin…`), issue 128; §8 failure behaviour → Task 4; §9 refusals → Task 6 (`test_this_slice_creates_no_interaction_content`); §10 fixture + tests → Tasks 2–7; §11 measurement → Task 9; §12 rule 6 → discharged, no code; §13 release identity → Task 5; §14 risks → Tasks 4 (count assertions), 9 (drift).

**Placeholders.** One deliberate and clearly marked: `PLACEHOLDER_REPLACE_WITH_LIVE_VALUES` in Task 1's `open_question_gap_kind` CHECK, with the command that produces the real values and an explicit instruction that it must not survive. It is written that way because hand-copying a fifteen-value list into a plan is how a value goes missing, and a placeholder that fails loudly beats a list that is quietly wrong. Task 6 Step 3 and Task 8 Step 2 describe `ingest_fda_cyp`'s body and the CLI wiring in prose rather than full code — both are deliberate: the first because its two load-bearing rules (resolution ambiguity, disposition order) are stated exactly and the rest is the `gsrs_run` shape the plan points at, the second because the plan must not guess which module owns the subparsers.

**Type consistency.** `split_footnotes` returns `tuple[str, str | None]` in Tasks 3, 4. `parse_cell` returns `list[tuple[str, str | None]]` in Task 4 throughout. `CypTuple`'s field names match their `fda_cyp_assertion` columns. `FdaCypSummary.classes_minted` / `.memberships_written` / `.assertions_written` are used consistently in Tasks 6 and 9. `SOURCE`/`WRITER` match db/039's CHECK values and `provenance.WRITERS`.
