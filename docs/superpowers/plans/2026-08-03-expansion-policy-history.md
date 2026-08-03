# Expansion-policy history (#35) — IMPLEMENTATION PLAN

> **For agentic workers:** REQUIRED SUB-SKILL: use `superpowers:subagent-driven-development` (recommended) or
> `superpowers:executing-plans` to implement this task-by-task. Steps use checkbox (`- [ ]`) syntax.
>
> **Status: forward plan.** The canonical design is
> [the expansion-policy history spec](../specs/2026-08-03-drugref-expansion-policy-history-design.md); this
> file only orders the build into TDD-sized tasks. **If the two disagree, the spec wins. If a measurement here
> contradicts the spec, stop and update the spec first**, then continue — a plan is a claim about the code,
> and the ingest-operability round shipped three plan defects by correcting only the code.

**Goal:** put `drugref.class_expansion_policy` — the curator-policy table that gates recall — on Plan C's
append-only overlay floor, so a revised deny/allow supersedes rather than overwrites, a `DELETE` is refused,
and a judgement can be **withdrawn** back to unreviewed.

**Architecture:** one migration (`db/027`) that ALTERs the table in place (surrogate `policy_id`,
`superseded_by`, the two generic Plan C triggers, a partial `live_key` index, `decision` widened with
`'withdrawn'`), one new view `class_expansion_policy_current` that all **four** readers go through, and two
writer functions in `interactions.py`.

**Tech Stack:** PostgreSQL ≥ 18, Python 3.12, `psycopg` v3, `uv`, pytest, ruff, MkDocs Material.

## Global Constraints

- **TDD**: the failing test comes first, always. Every task ends with the full suite green.
- **Tests**: `DRUGREF_TEST_DSN='host=localhost port=5532 dbname=drugref_test user=postgres' uv run pytest`.
  Baseline entering this plan: **788 passed**.
- **Lint**: `ruff check src tests` — **not** `ruff check .`, which walks `downloads/` and hangs.
- **Migrations are immutable once applied — immutability starts at MERGE.** `db/027` is unmerged throughout
  this plan, so it MAY be edited between tasks; conftest's `_migrated` fixture drops and recreates the schema.
  Never edit `db/010`, `db/012` or `db/018`.
- **No new source, no new dependency**, so no licence check is triggered (rule 6) and `NOTICE` is unchanged.
- **Do not touch `ci_class_subtree`.** The deny-list filters the rule's object class, never the walk;
  `test_a_descendant_of_a_denied_root_still_expands` must stay green and unedited.
- **Comments are mandatory and are for a junior contributor** (rule 3). Match the density of the surrounding
  migrations — they explain *why*, not *what*.
- **Do not update `CLAUDE.md`.**

## File map

| File | Responsibility |
|---|---|
| `db/027_expansion_policy_history.sql` | **create** — the whole schema change: floor, `withdrawn`, view, four reader re-issues |
| `src/drugref/interactions.py` | **modify** — add `NoLiveDecisionError`, `record_expansion_decision`, `withdraw_expansion_decision` |
| `tests/test_expansion_policy.py` | **modify** — floor + withdrawal tests; repair the two tests db/027 invalidates |
| `tests/test_expansion_policy_writer.py` | **create** — the writer's own tests |
| `tests/test_db.py` | **modify** — one line: the new view joins the schema-object list |
| `docs-site/docs/decisions/expansion-policy-is-append-only.md` | **create** — the standing correction to db/010's now-false prose |
| `docs-site/mkdocs.yml` | **modify** — nav entry for that page |
| `docs/HANDOVER.md`, `docs/ROADMAP.md` | **modify** — session wrap-up |

---

## Task 1: The append-only floor

**Files:**
- Create: `db/027_expansion_policy_history.sql`
- Modify: `tests/test_expansion_policy.py`
- Test: `tests/test_expansion_policy.py`

**Interfaces:**
- Consumes: `drugref.forbid_overlay_rewrite(pk_col, natural_key…)` and
  `drugref.forbid_multiple_live_assertions(natural_key…)`, both from `db/020` (the latter rewritten by
  `db/023`). Neither is modified.
- Produces: `class_expansion_policy.policy_id` (bigint identity PK) and
  `class_expansion_policy.superseded_by` (bigint, nullable, self-FK), relied on by every later task.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_expansion_policy.py`. Note `_seed_row` inserts a row this test owns rather than touching
a seeded one, because the `conn` fixture rolls back but a mistake against a seeded row would be permanent.

```python
# ---- the append-only floor (db/027, #35) ------------------------------------
#
# The table gates recall: one UPDATE of `decision` removes thousands of candidate
# pairs with no audit row and nothing reporting it. Since db/027 it carries Plan C's
# overlay floor, so a revision is an INSERT that supersedes -- history survives, and
# what drugref believed when a pair was withheld stays answerable.


def _own_row(conn, code, decision="deny", rationale="seeded by a test"):
    """Insert a policy row this test owns, and return its policy_id.

    Never revise a SEEDED row here: the conn fixture rolls back, but a row committed
    by accident could not be deleted afterwards -- that is the point of the floor.
    """
    return conn.execute(
        "INSERT INTO drugref.class_expansion_policy (source, source_code, decision, "
        "class_name, rationale, reviewed_by, reviewed_against) "
        "VALUES ('MED-RT', %s, %s, 'Test Bucket [PE]', %s, 'test', '2026.07.06') "
        "RETURNING policy_id", (code, decision, rationale)).fetchone()[0]


def test_a_policy_decision_cannot_be_deleted(conn):
    """#35's second asymmetry. Every other clinically-consequential curated table
    refuses DELETE; this one gated recall with no floor at all."""
    _own_row(conn, "N0000100001")
    with pytest.raises(psycopg.errors.RaiseException, match="append-only"):
        conn.execute("DELETE FROM drugref.class_expansion_policy "
                     "WHERE source_code = 'N0000100001'")


def test_a_decision_cannot_be_revised_in_place(conn):
    """#35's first asymmetry, and the whole point of the round: flipping `decision`
    used to overwrite the rationale that justified the previous judgement."""
    _own_row(conn, "N0000100002", decision="deny")
    with pytest.raises(psycopg.errors.RaiseException, match="only superseded_by may change"):
        conn.execute("UPDATE drugref.class_expansion_policy SET decision = 'allow' "
                     "WHERE source_code = 'N0000100002'")


def test_supersession_is_one_way_and_set_once(conn):
    """Un-setting would resurrect a corrected-away judgement as live; re-pointing
    would rewrite history a consumer may already have acted on."""
    old = _own_row(conn, "N0000100003")
    new = _own_row(conn, "N0000100003", decision="allow")
    conn.execute("UPDATE drugref.class_expansion_policy SET superseded_by = %s "
                 "WHERE policy_id = %s", (new, old))
    with pytest.raises(psycopg.errors.RaiseException, match="one-way"):
        conn.execute("UPDATE drugref.class_expansion_policy SET superseded_by = NULL "
                     "WHERE policy_id = %s", (old,))


def test_a_correction_must_point_at_a_later_row(conn):
    """The chain strictly increases, so it can never close into a cycle -- which would
    make BOTH judgements vanish from every live read at once, silently."""
    first = _own_row(conn, "N0000100004")
    second = _own_row(conn, "N0000100004", decision="allow")
    with pytest.raises(psycopg.errors.RaiseException, match="LATER row"):
        conn.execute("UPDATE drugref.class_expansion_policy SET superseded_by = %s "
                     "WHERE policy_id = %s", (first, second))


def test_a_correction_must_keep_the_same_class(conn):
    """A correction replaces a judgement about THIS class, not a different one.
    Pointing across classes is a merge, and there are no merge semantics here."""
    old = _own_row(conn, "N0000100005")
    other = _own_row(conn, "N0000100006")
    with pytest.raises(psycopg.errors.RaiseException, match="same source_code"):
        conn.execute("UPDATE drugref.class_expansion_policy SET superseded_by = %s "
                     "WHERE policy_id = %s", (other, old))


def test_two_live_decisions_for_one_class_are_refused_at_commit(conn):
    """The natural key stopped being unique in db/027 -- history rows carry it by
    definition -- so 'at most one LIVE row per class' is a DEFERRED trigger instead.

    SET CONSTRAINTS ALL IMMEDIATE forces the check that would otherwise fire at COMMIT,
    which the conn fixture never reaches: A TEST THAT NEVER COMMITS PROVES NOTHING.
    Note that statement switches the mode for the REST of this transaction.
    """
    _own_row(conn, "N0000100007")
    _own_row(conn, "N0000100007", decision="allow")   # nothing superseded
    with pytest.raises(psycopg.errors.RaiseException, match="live rows for natural key"):
        conn.execute("SET CONSTRAINTS ALL IMMEDIATE")


def test_the_live_key_index_exists(conn):
    """db/023 measured that this partial index is what keeps the single-live trigger
    linear rather than quadratic (2,000 rows: 5,773 ms -> 42 ms). NOTHING BUT THE
    TRIGGER READS IT, so it looks unused to a catalog sweep and is asserted by name."""
    assert conn.execute(
        "SELECT count(*) FROM pg_indexes WHERE schemaname = 'drugref' "
        "AND indexname = 'class_expansion_policy_live_key'").fetchone()[0] == 1
```

- [ ] **Step 2: Run the tests and confirm they fail**

```bash
DRUGREF_TEST_DSN='host=localhost port=5532 dbname=drugref_test user=postgres' \
  uv run pytest tests/test_expansion_policy.py -k "delete or revised or supersession or later_row or same_class or two_live or live_key_index" -v
```

Expected: all seven FAIL — the DELETE and UPDATE simply succeed (no trigger), `policy_id`/`superseded_by`
do not exist (`UndefinedColumn`), and the index is absent.

- [ ] **Step 3: Write `db/027_expansion_policy_history.sql` — the floor**

```sql
-- db/027 -- expansion-policy history (#35)
--
-- drugref.class_expansion_policy is the one curated table still edited in place, and
-- it GATES RECALL: a single `UPDATE ... SET decision = 'deny'` removes thousands of
-- candidate pairs with no audit row and nothing reporting it. db/010 reasoned its tier
-- out explicitly and was right AT THE TIME --
--
--   "NOT the append-only signed overlay either (that tier arrives with Plan C). This
--    is small, low-cardinality policy data in the same class as ci_axis and
--    source_tier: edited in place, reviewed by diff."
--
-- -- and Plan C has since landed. That parenthesis is now the argument FOR this
-- change. db/010 is applied and immutable, so the standing correction to its prose
-- lives in docs-site/docs/decisions/expansion-policy-is-append-only.md.
--
-- WHAT THIS MIGRATION DOES NOT DO: it does not touch ci_class_subtree. The deny-list
-- filters the class a rule NAMES, never the walk -- `Decreased Coagulation Activity`
-- is a descendant of a denied root and must still expand, which is how a rule reaches
-- warfarin, apixaban and aspirin (test_a_descendant_of_a_denied_root_still_expands).

-- ---- 1. the surrogate key -----------------------------------------------------
--
-- THE NATURAL KEY HAS TO STOP BEING UNIQUE, and this is the part most likely to be
-- "simplified" back. Correction-by-overlay means INSERTING the new judgement and THEN
-- pointing the old row at it, so both rows carry the same (source, source_code) --
-- a primary key on those columns rejects the only sequence that can express a
-- correction, and in-place mutation becomes the only possible implementation.
-- db/001 shipped exactly that defect on identity_claim and db/005 had to repair it;
-- db/020 records the same reasoning for additive_effect.
--
-- "At most one LIVE row per class" is not lost -- it moves to the deferred trigger in
-- section 3, which is the only shape that can express it (see
-- docs-site/docs/decisions/correcting-a-curated-assertion.md).
ALTER TABLE drugref.class_expansion_policy
    DROP CONSTRAINT IF EXISTS class_expansion_policy_pkey;

ALTER TABLE drugref.class_expansion_policy
    ADD COLUMN IF NOT EXISTS policy_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY;

-- One-way, set once, always a LATER row on the SAME class. The seeded rows acquire
-- NULL and are therefore live: nobody has revised or withdrawn anything.
ALTER TABLE drugref.class_expansion_policy
    ADD COLUMN IF NOT EXISTS superseded_by bigint
        REFERENCES drugref.class_expansion_policy(policy_id);

COMMENT ON COLUMN drugref.class_expansion_policy.policy_id IS
    'Surrogate key. The natural key (source, source_code) is deliberately NOT unique: '
    'a correction preserves it, so history rows share it. Do not "restore" a UNIQUE '
    'constraint there -- it would forbid every correction.';
COMMENT ON COLUMN drugref.class_expansion_policy.superseded_by IS
    'One-way, set once, always a LATER row on the SAME class. A superseded judgement '
    'is history and is never deleted: what drugref believed, and when, stays '
    'answerable -- which matters most for exactly the decisions that withheld pairs.';

-- ---- 2. the floor, REUSED rather than copied ----------------------------------
--
-- Both functions are db/020's, generic over the natural key (db/023 rewrote the
-- second one as equality predicates so an index can serve it). This table attaches to
-- them with no new PL/pgSQL, which is the point: one rule in five places is one rule
-- that will drift, and this project has spent four rounds proving it (#31, #40, #43,
-- db/018's two CTEs).
DROP TRIGGER IF EXISTS class_expansion_policy_append_only
    ON drugref.class_expansion_policy;
CREATE TRIGGER class_expansion_policy_append_only
    BEFORE UPDATE OR DELETE ON drugref.class_expansion_policy
    FOR EACH ROW EXECUTE FUNCTION drugref.forbid_overlay_rewrite(
        'policy_id', 'source', 'source_code');

-- DEFERRED, because a correction is momentarily TWO live rows -- between the INSERT
-- and the UPDATE that supersedes -- and an immediate check would reject the only
-- sequence that can express one.
DROP TRIGGER IF EXISTS class_expansion_policy_single_live
    ON drugref.class_expansion_policy;
CREATE CONSTRAINT TRIGGER class_expansion_policy_single_live
    AFTER INSERT OR UPDATE ON drugref.class_expansion_policy
    DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW EXECUTE FUNCTION drugref.forbid_multiple_live_assertions(
        'source', 'source_code');

-- PARTIAL and NOT UNIQUE, matching the trigger's predicate exactly -- uniqueness over
-- live rows is precisely what this design cannot use. db/023 measured that without
-- this index the trigger is a sequential scan per row and therefore quadratic.
-- Nothing but the trigger reads it, so a test asserts it by name.
CREATE INDEX IF NOT EXISTS class_expansion_policy_live_key
    ON drugref.class_expansion_policy (source, source_code)
    WHERE superseded_by IS NULL;
```

- [ ] **Step 4: Repair the two existing tests db/027 invalidates**

Both are real breakages, not test noise — they are the round working as intended.

In `tests/test_expansion_policy.py`, change `_decisions` to read live rows only. Without this it collapses a
supersession chain into one arbitrary value:

```python
def _decisions(conn) -> dict[str, str]:
    """The MED-RT decisions that are LIVE. Since db/027 the table holds history too,
    and a dict over every row would silently keep whichever one came last."""
    return dict(conn.execute(
        "SELECT source_code, decision FROM drugref.class_expansion_policy "
        "WHERE source = 'MED-RT' AND superseded_by IS NULL").fetchall())
```

Then rewrite the deploy test's revision and restore, which currently `UPDATE`s in place. Replace the body of
`test_a_second_apply_does_not_stomp_a_locally_revised_decision` from `revised = ...` to the end of `finally:`:

```python
    revised = "N0000009908"                    # Vasoconstriction, seeded as `allow`

    def _revise(decision, rationale):
        """Express an operator's revision the only way db/027 allows: insert, then
        point whatever was live at the new row. Task 3 replaces this with
        interactions.record_expansion_decision -- the point of having a writer."""
        new_id = conn.execute(
            "INSERT INTO drugref.class_expansion_policy (source, source_code, "
            "decision, class_name, rationale, reviewed_by, reviewed_against) VALUES "
            "('MED-RT', %s, %s, 'Vasoconstriction [PE]', %s, 'test', '2026.07.06') "
            "RETURNING policy_id", (revised, decision, rationale)).fetchone()[0]
        conn.execute(
            "UPDATE drugref.class_expansion_policy SET superseded_by = %s "
            "WHERE source = 'MED-RT' AND source_code = %s AND superseded_by IS NULL "
            "AND policy_id <> %s", (new_id, revised, new_id))
        conn.commit()

    _revise("deny", "an operator disagrees with the seed")
    try:
        with psycopg.connect(_migrated) as c:
            db.apply_migrations(c)
        assert _decisions(conn)[revised] == "deny", "a deploy overwrote curator judgement"
    finally:
        _revise("allow", "restoring the seeded judgement after the test")
```

Two changes of substance, both worth stating in the docstring you edit alongside it: the `before`/`after`
row-count assertion goes, because a supersession legitimately adds rows and the assertion it was standing in
for (*a re-seed would reinstate drugref's opinion*) is exactly what `_decisions(conn)[revised] == "deny"`
already tests; and the restore is a third row rather than a rollback, because **nothing can be deleted now**.

Finally, rename `test_a_third_decision_value_is_refused` — after this round a third value *exists*, so the
name would be a false claim that passes:

```python
def test_an_unrecognised_decision_value_is_refused(conn):
    """The views branch on these literals. A row spelled 'denied' or 'no' would read as
    neither deny nor allow and silently expand a bucket somebody meant to stop.
    THREE values are legal since db/027 -- `withdrawn` joined them (#35) -- and this
    test is about the closed vocabulary, not about how many members it has."""
```

(Keep its body unchanged: `'maybe'` must still raise `CheckViolation`.)

- [ ] **Step 5: Run the full suite**

```bash
DRUGREF_TEST_DSN='host=localhost port=5532 dbname=drugref_test user=postgres' uv run pytest -q
```

Expected: **795 passed** (788 + 7 new). If `test_a_second_apply_does_not_stomp_a_locally_revised_decision`
fails, check that the restore ran — a committed revision persists for the whole session.

- [ ] **Step 6: Commit**

```bash
git add db/027_expansion_policy_history.sql tests/test_expansion_policy.py
git commit -m "feat(db): the expansion-policy table becomes append-only (#35)

A table that gates recall had no write protection at all: one UPDATE removed
thousands of candidate pairs with no audit row. It now carries Plan C's overlay
floor -- surrogate key, one-way supersession, at most one live row per class
checked at COMMIT, over the partial index db/023 measured is what keeps that
check linear. Both trigger functions are reused, not copied.

The natural key stops being unique deliberately: a correction preserves it, so
history rows share it. Two existing tests broke and both breakages ARE the
round -- the deploy test revised a decision with a raw UPDATE, which is now
refused, and _decisions() would have collapsed a supersession chain."
```

---

## Task 2: `withdrawn`, one view, four readers

**Files:**
- Modify: `db/027_expansion_policy_history.sql` (append sections 3–4; still unmerged, so editing is allowed)
- Modify: `tests/test_expansion_policy.py`, `tests/test_db.py`

**Interfaces:**
- Consumes: `policy_id` / `superseded_by` from Task 1.
- Produces: view `drugref.class_expansion_policy_current` with columns `policy_id, source, source_code,
  decision, class_name, rationale, reviewed_by, reviewed_against, reviewed_at` — the only thing any reader
  may join to.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_expansion_policy.py`:

```python
# ---- withdrawal (db/027, #35) -----------------------------------------------
#
# ABSENT IS NOT `allow`. Absent means UNREVIEWED, which expands AND raises a question;
# `allow` means a curator looked and said expand. An append-only table can never
# return a class to absent, so `withdrawn` is what says "no current judgement".
#
# This is not a nicety: medrt_run already tells an operator to "re-key or WITHDRAW"
# a decision whose class a release no longer defines, and since Task 1 the DELETE that
# used to mean is refused.


def test_withdrawn_is_a_legal_decision(conn):
    """The third value. `deny` and `allow` are judgements; `withdrawn` is the absence
    of one, recorded rather than deleted so its rationale survives."""
    _own_row(conn, "N0000100010", decision="withdrawn")
    assert conn.execute(
        "SELECT decision FROM drugref.class_expansion_policy "
        "WHERE source_code = 'N0000100010'").fetchone()[0] == "withdrawn"


def test_a_withdrawn_decision_does_not_bind(conn):
    """`class_expansion_policy_current` is what every reader goes through, and it is
    BINDING rather than merely live: a withdrawn row is unsuperseded and still absent
    here, which is what returns the class to the worklist."""
    _own_row(conn, "N0000100011", decision="withdrawn")
    assert conn.execute(
        "SELECT count(*) FROM drugref.class_expansion_policy_current "
        "WHERE source_code = 'N0000100011'").fetchone()[0] == 0


def test_a_superseded_decision_does_not_bind(conn):
    """The other half: history must not be read as policy."""
    old = _own_row(conn, "N0000100012", decision="deny")
    new = _own_row(conn, "N0000100012", decision="allow")
    conn.execute("UPDATE drugref.class_expansion_policy SET superseded_by = %s "
                 "WHERE policy_id = %s", (new, old))
    assert conn.execute(
        "SELECT decision FROM drugref.class_expansion_policy_current "
        "WHERE source_code = 'N0000100012'").fetchall() == [("allow",)]


def test_withdrawing_a_decision_returns_the_class_to_the_review_worklist(conn):
    """THE POINT OF THE VALUE, and the one behaviour a release cannot exercise -- no
    withdrawn row exists in any release-derived database -- so it is pinned here on
    controlled input, the same treatment #42's tie-break and #53's cap exemption got.

    Vasoconstriction is seeded `allow` and is one of the fourteen roots the >20
    discovery heuristic finds, so withdrawing it must put it back on the worklist.
    Asserted through expansion_policy_unresolved's sibling condition -- membership of
    class_expansion_policy_current -- because whether the ROOT itself appears in
    gap_unreviewed_expansion_root depends on which classes the shared test database
    happens to hold, and a test whose outcome depends on module ordering is worse
    than no test.
    """
    seeded = "N0000009908"
    assert conn.execute(
        "SELECT decision FROM drugref.class_expansion_policy_current "
        "WHERE source_code = %s", (seeded,)).fetchone() == ("allow",)
    live = conn.execute(
        "SELECT policy_id FROM drugref.class_expansion_policy "
        "WHERE source_code = %s AND superseded_by IS NULL", (seeded,)).fetchone()[0]
    new = conn.execute(
        "INSERT INTO drugref.class_expansion_policy (source, source_code, decision, "
        "class_name, rationale, reviewed_by, reviewed_against) VALUES "
        "('MED-RT', %s, 'withdrawn', 'Vasoconstriction [PE]', 'stale', 'test', 'r') "
        "RETURNING policy_id", (seeded,)).fetchone()[0]
    conn.execute("UPDATE drugref.class_expansion_policy SET superseded_by = %s "
                 "WHERE policy_id = %s", (new, live))
    # Indistinguishable from never having been reviewed -- which is the definition.
    assert conn.execute(
        "SELECT count(*) FROM drugref.class_expansion_policy_current "
        "WHERE source_code = %s", (seeded,)).fetchone()[0] == 0


def test_a_withdrawn_decision_is_not_reported_as_unresolved(conn):
    """expansion_policy_unresolved exists to say "this deny matches no class, re-key or
    withdraw it". Once withdrawn, there is nothing left to re-key -- so the view must
    stop reporting it, or following its own advice would not clear it."""
    live = _own_row(conn, "N0000999998", decision="deny")   # names no real class
    assert "N0000999998" in {r[0] for r in conn.execute(
        "SELECT source_code FROM drugref.expansion_policy_unresolved").fetchall()}
    new = _own_row(conn, "N0000999998", decision="withdrawn")
    conn.execute("UPDATE drugref.class_expansion_policy SET superseded_by = %s "
                 "WHERE policy_id = %s", (new, live))
    assert "N0000999998" not in {r[0] for r in conn.execute(
        "SELECT source_code FROM drugref.expansion_policy_unresolved").fetchall()}
```

And in `tests/test_db.py`, extend the db/010 entry of the schema-object list (around line 77):

```python
        # Plan B, db/010: descendant expansion. The policy table is CURATOR DATA --
        # no ingest clears it -- plus the two views that stop it rotting silently:
        # one for a large root nobody has ruled on, one for a ruling whose class the
        # release no longer defines. db/027 (#35) made the table append-only and
        # added the view every reader of it must go through.
        "class_expansion_policy", "class_expansion_policy_current",
        "gap_unreviewed_expansion_root", "expansion_policy_unresolved",
```

- [ ] **Step 2: Run the tests and confirm they fail**

```bash
DRUGREF_TEST_DSN='host=localhost port=5532 dbname=drugref_test user=postgres' \
  uv run pytest tests/test_expansion_policy.py tests/test_db.py -k "withdraw or bind or unresolved or objects" -v
```

Expected: FAIL — `'withdrawn'` violates the CHECK, and `class_expansion_policy_current` does not exist.

- [ ] **Step 3: Append sections 3–4 to `db/027_expansion_policy_history.sql`**

```sql
-- ---- 3. the third decision value ----------------------------------------------
--
-- SUPERSESSION ALONE CAN NEVER WITHDRAW ANYTHING. A correction must point at a later
-- row carrying the SAME natural key, so every correction leaves another live row
-- standing -- the finding that gave additive_effect its `accumulates`,
-- interaction_group_member its `satisfies_role`, and (db/023) interaction_group_
-- assertion its `applies`. This table has the same hole, and here it BITES: absent
-- means UNREVIEWED, which expands AND raises a question, so a class that has ever
-- been ruled on could never go back on the worklist.
--
-- medrt_run already logs "Re-key or WITHDRAW them in drugref.class_expansion_policy"
-- when a release stops defining a ruled-on class. Before this round, withdraw meant
-- DELETE. Since section 2, DELETE raises -- so without this value the schema would
-- ship a warning advising an impossible action.
--
-- A THIRD VALUE RATHER THAN A BOOLEAN, deliberately. `decision` is already the ruling
-- vocabulary and all four readers branch on it; a boolean beside it would admit two
-- encodings of one state ((deny, false) and (allow, false)) and let a consumer read
-- `decision` alone and be confidently wrong -- the footgun slice 5b.2 split a table to
-- avoid. A reader that has never heard of `withdrawn` reads it as NOT-deny and
-- expands, which for a contraindication is the safe direction.
--
-- `withdrawn` IS NOT `allow`. It means NO CURRENT JUDGEMENT.
ALTER TABLE drugref.class_expansion_policy
    DROP CONSTRAINT IF EXISTS class_expansion_policy_decision;
ALTER TABLE drugref.class_expansion_policy
    ADD CONSTRAINT class_expansion_policy_decision
    CHECK (decision IN ('deny', 'allow', 'withdrawn'));

COMMENT ON COLUMN drugref.class_expansion_policy.decision IS
    '`deny` expands to DIRECT members only; `allow` expands over the full subtree; '
    '`withdrawn` (db/027) means the judgement no longer stands, which returns the '
    'class to gap_unreviewed_expansion_root. WITHDRAWN IS NOT ALLOW -- it is the '
    'absence of a judgement, recorded rather than deleted so its rationale survives.';

-- ---- 4. one view, four readers -------------------------------------------------
--
-- There are FOUR readers of this table and every one asks the same question: WHAT
-- BINDS NOW. Writing `superseded_by IS NULL AND decision <> 'withdrawn'` four times is
-- the bet this project has lost four times already (#31's reach measure stated twice
-- where only one copy learned a correction; #40's two MeSH readers; #43's two
-- checksums; db/018's two near-identical CTEs). So it is stated once.
--
-- NAMED `_current`, NOT `_live`, BECAUSE LIVE AND BINDING ARE DIFFERENT QUESTIONS. A
-- withdrawn row is live (nothing superseded it) and does not bind. The writer in
-- interactions.py needs the LIVE row including a withdrawn one, to supersede it --
-- that is a different question asked in exactly one place, not a fifth copy of this.
--
-- It also keeps ddi_candidate_pair's LEFT JOIN one-to-one: the single-live trigger
-- allows one live row per class, and this view drops the withdrawn ones, so a history
-- row can never multiply a pair.
CREATE OR REPLACE VIEW drugref.class_expansion_policy_current AS
SELECT policy_id, source, source_code, decision, class_name, rationale,
       reviewed_by, reviewed_against, reviewed_at
FROM   drugref.class_expansion_policy
WHERE  superseded_by IS NULL
AND    decision <> 'withdrawn';

COMMENT ON VIEW drugref.class_expansion_policy_current IS
    'The expansion decisions that currently BIND: not superseded, and not withdrawn. '
    'EVERY READER OF class_expansion_policy MUST GO THROUGH THIS VIEW -- the base '
    'table holds history since db/027, and history read as policy is a deny that '
    'stopped being true. A withdrawn decision is deliberately indistinguishable from '
    'no decision here, which is what returns its class to the review worklist.';
```

- [ ] **Step 4: Re-issue the four readers, in the same file**

`CREATE OR REPLACE` rather than `DROP`: no column list changes in any of the four, so replacing in place keeps
grants and dependent objects intact. **The only edit in each is the relation the policy is read from.**

```sql
-- 4a. expansion_policy_unresolved (db/010) -- a withdrawn decision binds nothing, so
--     there is nothing left to re-key and reporting it would be noise.
CREATE OR REPLACE VIEW drugref.expansion_policy_unresolved AS
SELECT p.source, p.source_code, p.decision, p.class_name, p.reviewed_against
FROM   drugref.class_expansion_policy_current p
WHERE  NOT EXISTS (SELECT 1 FROM drugref.substance_class sc
                   WHERE  sc.source      = p.source
                   AND    sc.source_code = p.source_code);

-- 4b. gap_unreviewed_expansion_root (db/012) -- and THIS is where withdrawal pays:
--     the class becomes invisible here again, so the question re-raises.
CREATE OR REPLACE VIEW drugref.gap_unreviewed_expansion_root AS
WITH sized AS (
    SELECT root_uuid, count(*) - 1 AS descendant_class_count
    FROM   drugref.ci_class_subtree
    GROUP  BY root_uuid
)
SELECT sc.class_uuid,
       sc.class_name,
       sc.concept_type,
       z.descendant_class_count,
       count(*)                AS ci_rule_count,
       max(r.upstream_release) AS upstream_release
FROM   drugref.class_contraindication ci
JOIN   drugref.ci_axis         a  ON a.relationship  = ci.relationship
JOIN   drugref.substance_class sc ON sc.class_uuid   = ci.object_class_uuid
JOIN   sized                   z  ON z.root_uuid    = ci.object_class_uuid
JOIN   drugref.ingest_run      r  ON r.ingest_run_id = ci.ingest_run
WHERE  z.descendant_class_count > 20
AND    a.expands_descendants
       -- Either decision counts as reviewed. `allow` and `deny` differ for the pair
       -- set and agree here, because this view asks only whether a human has looked
       -- -- and since db/027, whether they still stand by it.
AND    NOT EXISTS (SELECT 1 FROM drugref.class_expansion_policy_current p
                   WHERE  p.source      = sc.source
                   AND    p.source_code = sc.source_code)
GROUP  BY sc.class_uuid, sc.class_name, sc.concept_type, z.descendant_class_count;

-- 4c. ddi_candidate_pair (db/012) -- a withdrawn deny stops denying, and the class
--     expands again. COALESCE already treats a missing row as 'allow', so a withdrawn
--     row disappearing from the view is exactly the right behaviour with no change to
--     the predicate.
CREATE OR REPLACE VIEW drugref.ddi_candidate_pair AS
SELECT DISTINCT ON (ci.subject_moiety_uuid, ci.object_class_uuid, ci.relationship,
                    ci.source, m.moiety_uuid)
       ci.subject_moiety_uuid AS subject_moiety,
       m.moiety_uuid          AS partner_moiety,
       ci.relationship,
       ci.object_class_uuid   AS via_class,
       m.class_uuid           AS member_class,
       (m.class_uuid = ci.object_class_uuid) AS is_direct,
       ci.source,
       ci.ingest_run,
       r.upstream_release,
       r.finished_at          AS ingested_at
FROM   drugref.class_contraindication ci
JOIN   drugref.ci_axis a
       ON a.relationship = ci.relationship
JOIN   drugref.ci_class_subtree s
       ON s.root_uuid = ci.object_class_uuid
JOIN   drugref.class_membership m
       ON m.class_uuid   = s.class_uuid
      AND m.relationship = a.membership_relationship
JOIN   drugref.ingest_run r
       ON r.ingest_run_id = ci.ingest_run
JOIN   drugref.substance_class oc
       ON oc.class_uuid = ci.object_class_uuid
LEFT   JOIN drugref.class_expansion_policy_current p
       ON p.source = oc.source AND p.source_code = oc.source_code
WHERE  m.moiety_uuid <> ci.subject_moiety_uuid
AND    (m.class_uuid = ci.object_class_uuid
        OR (a.expands_descendants AND COALESCE(p.decision, 'allow') <> 'deny'))
ORDER  BY ci.subject_moiety_uuid, ci.object_class_uuid, ci.relationship, ci.source,
          m.moiety_uuid, (m.class_uuid = ci.object_class_uuid) DESC, m.class_uuid;

-- 4d. gap_dead_by_expansion_policy (db/018) -- the FOURTH reader, which the issue text
--     does not mention. A withdrawn deny stops killing its rules, so the question
--     retires, which is correct: the rule now reaches its subtree.
CREATE OR REPLACE VIEW drugref.gap_dead_by_expansion_policy AS
SELECT rr.object_class_uuid    AS class_uuid,
       sc.class_name,
       sc.concept_type,
       count(*)                AS ci_rule_count,
       max(rr.subtree_partner_count) AS subtree_partner_count,
       max(r.upstream_release) AS upstream_release
FROM   drugref.ci_rule_partner_reach rr
JOIN   drugref.substance_class sc ON sc.class_uuid   = rr.object_class_uuid
JOIN   drugref.ingest_run      r  ON r.ingest_run_id = rr.ingest_run
JOIN   drugref.class_expansion_policy_current p
       ON p.source = sc.source AND p.source_code = sc.source_code
      AND p.decision = 'deny'
WHERE  rr.expands_descendants
AND    rr.direct_partner_count = 0
AND    rr.subtree_partner_count > 0
GROUP  BY rr.object_class_uuid, sc.class_name, sc.concept_type;
```

> **Do not re-issue the `COMMENT ON VIEW` text for 4b/4c/4d.** Those comments are unchanged and still true;
> re-stating them here would be a second copy to drift. Only `expansion_policy_unresolved` and the two new
> objects get comments in this migration.

- [ ] **Step 5: Run the full suite**

```bash
DRUGREF_TEST_DSN='host=localhost port=5532 dbname=drugref_test user=postgres' uv run pytest -q
```

Expected: **800 passed**. Existing `ddi_candidate_pair` and gap-view tests must be **untouched and green** —
if any fails, the reader re-issue changed behaviour it should not have; diff your view body against db/012 /
db/018 line by line before changing a test.

- [ ] **Step 6: Commit**

```bash
git add db/027_expansion_policy_history.sql tests/test_expansion_policy.py tests/test_db.py
git commit -m "feat(db): a judgement can be withdrawn, and four readers go through one view (#35)

Supersession preserves the natural key, so nothing could be RETIRED -- Plan C's
finding, and here it bites: absent means UNREVIEWED, so a class that had ever
been ruled on could never return to the worklist. medrt_run already tells an
operator to 're-key or withdraw' a stale decision and Task 1 made DELETE raise,
so the value is required, not decorative.

A third decision value rather than a boolean: one column, one truth, and a
reader that has never heard of it treats it as not-deny and expands.

There are FOUR readers, not the three #35 names -- gap_dead_by_expansion_policy
joins on decision = 'deny' -- and all four ask what binds NOW, so all four go
through class_expansion_policy_current. Named _current, not _live: a withdrawn
row is live and does not bind."
```

---

## Task 3: The writer

**Files:**
- Modify: `src/drugref/interactions.py`
- Create: `tests/test_expansion_policy_writer.py`
- Modify: `tests/test_expansion_policy.py` (the deploy test switches to the writer)

**Interfaces:**
- Consumes: the schema from Tasks 1–2.
- Produces:
  - `interactions.NoLiveDecisionError(LookupError)`
  - `interactions.record_expansion_decision(conn, source, source_code, decision, class_name, rationale,
    reviewed_by, reviewed_against) -> int` (returns the new `policy_id`)
  - `interactions.withdraw_expansion_decision(conn, source, source_code, rationale, reviewed_by,
    reviewed_against) -> int`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_expansion_policy_writer.py`:

```python
# tests/test_expansion_policy_writer.py
"""The writer for class_expansion_policy decisions (db/027, #35).

Correction-by-overlay is INSERT-then-point-the-old-row-at-it, and the ordering is the
part that is easy to get wrong: get it backwards and the failure arrives at COMMIT,
long after the call that caused it. That is why there is a function rather than a
paragraph of documentation telling each curator to write it themselves.

The vocabulary is deliberately NOT restated in Python. `decision` has one home -- the
CHECK in db/027 -- because a second list is a second thing to disagree with the first
(db/006's lesson). A typo therefore raises CheckViolation from the database.
"""
import pytest
import psycopg

from drugref import interactions

CODE = "N0000200001"


def _live(conn, code=CODE):
    """(decision, rationale) of the row that currently BINDS, or None."""
    return conn.execute(
        "SELECT decision, rationale FROM drugref.class_expansion_policy_current "
        "WHERE source = 'MED-RT' AND source_code = %s", (code,)).fetchone()


def test_recording_a_decision_makes_it_bind(conn):
    interactions.record_expansion_decision(
        conn, "MED-RT", CODE, "deny", "Test Bucket [PE]",
        "abstract organ-system bucket", "test", "2026.07.06")
    assert _live(conn) == ("deny", "abstract organ-system bucket")


def test_revising_a_decision_supersedes_rather_than_overwrites(conn):
    """#35 in one test: the previous rationale must still be answerable afterwards."""
    first = interactions.record_expansion_decision(
        conn, "MED-RT", CODE, "deny", "Test Bucket [PE]", "too abstract",
        "test", "2026.07.06")
    second = interactions.record_expansion_decision(
        conn, "MED-RT", CODE, "allow", "Test Bucket [PE]", "gained a real effect",
        "test", "2026.07.06")
    conn.execute("SET CONSTRAINTS ALL IMMEDIATE")     # a test that never commits proves nothing
    assert _live(conn) == ("allow", "gained a real effect")
    assert conn.execute(
        "SELECT superseded_by, rationale FROM drugref.class_expansion_policy "
        "WHERE policy_id = %s", (first,)).fetchone() == (second, "too abstract")


def test_withdrawing_carries_the_class_name_forward(conn):
    """A withdrawal must not be able to introduce a name nobody reviewed, so it is
    copied from the row being withdrawn rather than asked of the caller."""
    interactions.record_expansion_decision(
        conn, "MED-RT", CODE, "deny", "Test Bucket [PE]", "too abstract",
        "test", "2026.07.06")
    withdrawn = interactions.withdraw_expansion_decision(
        conn, "MED-RT", CODE, "the 2026.07.06 measurement no longer holds",
        "test", "2026.08.06")
    assert _live(conn) is None, "a withdrawn decision must not bind"
    assert conn.execute(
        "SELECT decision, class_name FROM drugref.class_expansion_policy "
        "WHERE policy_id = %s", (withdrawn,)).fetchone() == ("withdrawn", "Test Bucket [PE]")


def test_withdrawing_a_decision_nobody_made_is_an_error(conn):
    """Not a no-op: it means the caller believes a judgement exists that does not, and
    silently doing nothing would leave them believing it."""
    with pytest.raises(interactions.NoLiveDecisionError, match="N0000200099"):
        interactions.withdraw_expansion_decision(
            conn, "MED-RT", "N0000200099", "x", "test", "2026.07.06")


def test_a_class_can_be_ruled_on_again_after_a_withdrawal(conn):
    """Withdrawal returns the class to unreviewed; it does not close it for ever."""
    interactions.record_expansion_decision(
        conn, "MED-RT", CODE, "deny", "Test Bucket [PE]", "too abstract",
        "test", "2026.07.06")
    interactions.withdraw_expansion_decision(
        conn, "MED-RT", CODE, "stale", "test", "2026.08.06")
    interactions.record_expansion_decision(
        conn, "MED-RT", CODE, "allow", "Test Bucket [PE]", "re-reviewed",
        "test", "2026.08.06")
    conn.execute("SET CONSTRAINTS ALL IMMEDIATE")
    assert _live(conn) == ("allow", "re-reviewed")


def test_an_unrecognised_decision_reaches_the_database_constraint(conn):
    """The vocabulary lives in the CHECK and nowhere else."""
    with pytest.raises(psycopg.errors.CheckViolation):
        interactions.record_expansion_decision(
            conn, "MED-RT", CODE, "maybe", "Test Bucket [PE]", "x",
            "test", "2026.07.06")
```

- [ ] **Step 2: Run the tests and confirm they fail**

```bash
DRUGREF_TEST_DSN='host=localhost port=5532 dbname=drugref_test user=postgres' \
  uv run pytest tests/test_expansion_policy_writer.py -v
```

Expected: FAIL — `AttributeError: module 'drugref.interactions' has no attribute
'record_expansion_decision'`.

- [ ] **Step 3: Implement in `src/drugref/interactions.py`**

Add after `unresolved_expansion_policy`. Also extend the module docstring to say it now writes curator policy
as well as projections — the file currently claims "REBUILDABLE PROJECTION" discipline throughout, which stops
being the whole truth here.

```python
class NoLiveDecisionError(LookupError):
    """Raised when a withdrawal names a class carrying no live expansion decision."""


def record_expansion_decision(conn: psycopg.Connection, source: str, source_code: str,
                              decision: str, class_name: str, rationale: str,
                              reviewed_by: str, reviewed_against: str) -> int:
    """Record (or revise) whether a contraindicated class expands over its subtree.

    Returns the new `policy_id`. THE ONLY WAY TO REVISE A DECISION: since db/027 the
    table is append-only, so a revision INSERTs the new judgement and then points
    whatever was live at it. The previous rationale survives as history, which is the
    whole of #35 -- "what did we last say about this class, against which release, and
    why did we change our mind" has to be answerable from the database.

    ORDER MATTERS AND IS EASY TO GET WRONG: `superseded_by` must reference a row that
    already exists, and getting it backwards fails at COMMIT rather than here. That is
    why this is a function and not a paragraph of documentation.

    `decision` is passed straight through to the CHECK in db/027, which is the one
    place the vocabulary lives -- restating it here would be a second list to disagree
    with the first (db/006). An unrecognised value therefore raises CheckViolation.

    NOTE the caller owns the transaction, as everywhere else in this module: nothing
    here commits. The single-live check is DEFERRED, so a mistake surfaces at the
    caller's COMMIT.
    """
    new_id = conn.execute(
        "INSERT INTO drugref.class_expansion_policy "
        "(source, source_code, decision, class_name, rationale, reviewed_by, "
        "reviewed_against) VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING policy_id",
        (source, source_code, decision, class_name, rationale, reviewed_by,
         reviewed_against)).fetchone()[0]
    # Point whatever was live at the new row -- including a `withdrawn` one, which is
    # live but does not bind. `policy_id <> new_id` keeps the row we just wrote out of
    # its own supersession.
    conn.execute(
        "UPDATE drugref.class_expansion_policy SET superseded_by = %s "
        "WHERE source = %s AND source_code = %s AND superseded_by IS NULL "
        "AND policy_id <> %s", (new_id, source, source_code, new_id))
    return new_id


def withdraw_expansion_decision(conn: psycopg.Connection, source: str, source_code: str,
                                rationale: str, reviewed_by: str,
                                reviewed_against: str) -> int:
    """Retract the live decision for a class, returning it to the review worklist.

    WITHDRAWN IS NOT `allow`. Absent means UNREVIEWED -- it expands AND raises a
    question on gap_unreviewed_expansion_root -- and an append-only table can never
    return a class to absent, so `withdrawn` is what says "no current judgement". Use
    it when a rationale has gone stale (it rested on a release measurement that no
    longer holds), or when a release stops defining the class at all: that is the case
    medrt_run's "re-key or withdraw" warning is about.

    `class_name` is carried forward from the row being withdrawn rather than asked of
    the caller, so a withdrawal cannot introduce a name nobody reviewed.

    Raises NoLiveDecisionError if no decision is live: withdrawing one nobody made
    means the caller believes something false, and silently doing nothing would leave
    them believing it.
    """
    row = conn.execute(
        "SELECT class_name FROM drugref.class_expansion_policy "
        "WHERE source = %s AND source_code = %s AND superseded_by IS NULL",
        (source, source_code)).fetchone()
    if row is None:
        raise NoLiveDecisionError(
            f"no live expansion decision for {source} {source_code}: "
            "nothing to withdraw")
    return record_expansion_decision(conn, source, source_code, "withdrawn", row[0],
                                     rationale, reviewed_by, reviewed_against)
```

- [ ] **Step 4: Switch the deploy test to the writer**

In `tests/test_expansion_policy.py`, replace the local `_revise` helper written in Task 1 with the real thing,
and drop the now-stale comment that pointed forward to this task:

```python
    def _revise(decision, rationale):
        """Express an operator's revision the only way db/027 allows -- insert, then
        supersede -- through the writer that owns that ordering."""
        interactions.record_expansion_decision(
            conn, "MED-RT", revised, decision, "Vasoconstriction [PE]", rationale,
            "test", "2026.07.06")
        conn.commit()
```

Add `from drugref import db, ids, interactions` to that module's imports (it currently imports `db, ids`).

- [ ] **Step 5: Run the full suite and lint**

```bash
DRUGREF_TEST_DSN='host=localhost port=5532 dbname=drugref_test user=postgres' uv run pytest -q
ruff check src tests
```

Expected: **806 passed**, ruff clean. Confirm `interactions.py` is still under 500 lines: `wc -l
src/drugref/interactions.py` (191 + ~70).

- [ ] **Step 6: Commit**

```bash
git add src/drugref/interactions.py tests/test_expansion_policy_writer.py tests/test_expansion_policy.py
git commit -m "feat(interactions): a writer for expansion decisions (#35)

Correction-by-overlay is insert-then-supersede and the ordering fails at COMMIT
rather than at the call, so it is a function rather than a paragraph telling
each curator to write it themselves. withdraw_expansion_decision carries
class_name forward from the row it retracts, so a withdrawal cannot introduce a
name nobody reviewed, and raises rather than no-oping when nothing is live.

The decision vocabulary is NOT restated in Python: the CHECK is its one home,
and a second list is a second thing to disagree with the first (db/006).

The supersede UPDATE is written locally rather than shared with
accumulation._supersede -- promoting a private helper and refactoring four
merged Plan C call sites would widen this round's blast radius. Filed instead."
```

---

## Task 4: Re-measure against the real releases

Nothing here changes code. **Reasoning that a schema change cannot move rows is exactly what this project's
rules forbid**, and the ingest-operability round found three defects this way.

- [ ] **Step 1: Build a fresh measurement database**

`drugref_ops` stays as the pre-round baseline (as `drugref_planc` was for Plan C), and its ledger already
holds a drifted `db/025`, so `apply_migrations` refuses there anyway. Note the `psql` on PATH is the
Postgres.app **v16** client against a v18 server — plain SQL works, `\l` does not.

```bash
PSQL=/Applications/Postgres.app/Contents/Versions/16/bin/psql
DSN='host=localhost port=5532 dbname=drugref_policy user=postgres'
"$PSQL" -h localhost -p 5532 -U postgres -d postgres -c "CREATE DATABASE drugref_policy"
uv run drugref --dsn "$DSN" migrate
uv run drugref --dsn "$DSN" ingest chain --downloads downloads \
  --unii-release 26Feb2026 --medrt-release 2026.07.06 \
  --mesh-release 2026 --mesh-relations-release 2026.07.06
```

Expected: ~110 s. The one manual step the chain does not do is unzipping `Core_MEDRT_XML.zip`, and
`downloads/MEDRT/Core_MEDRT_2026.07.06_XML.xml` is already unzipped.

- [ ] **Step 2: Confirm nothing moved**

```bash
"$PSQL" "$DSN" -Atc "
SELECT 'ddi_candidate_pair', count(*) FROM drugref.ddi_candidate_pair
UNION ALL SELECT 'gap_dead_by_expansion_policy', count(*) FROM drugref.gap_dead_by_expansion_policy
UNION ALL SELECT 'gap_unreviewed_expansion_root', count(*) FROM drugref.gap_unreviewed_expansion_root
UNION ALL SELECT 'open_question', count(*) FROM drugref.open_question
UNION ALL SELECT 'policy_rows', count(*) FROM drugref.class_expansion_policy
UNION ALL SELECT 'policy_binding', count(*) FROM drugref.class_expansion_policy_current
UNION ALL SELECT 'expansion_policy_unresolved', count(*) FROM drugref.expansion_policy_unresolved"
```

Required: `ddi_candidate_pair` **21664** · `gap_dead_by_expansion_policy` **1** ·
`gap_unreviewed_expansion_root` **0** · `open_question` **18834** · `policy_rows` **14** ·
`policy_binding` **14** (nothing superseded or withdrawn on a fresh install).

**If any figure differs, STOP.** Do not adjust the plan or the spec to match — find the cause first. A moved
`ddi_candidate_pair` means the reader re-issue changed the join, which is the one regression this round had to
avoid.

- [ ] **Step 3: Confirm the hot path did not get slower**

```bash
"$PSQL" "$DSN" -c "EXPLAIN ANALYZE SELECT * FROM drugref.ddi_candidate_pair
                   WHERE subject_moiety = (SELECT subject_moiety FROM drugref.ddi_candidate_pair LIMIT 1)"
```

Expected: the same order as the **3.1 ms** HANDOVER records. The view now reads a view rather than a table;
if this has become materially slower, say so in the PR rather than quietly accepting it.

- [ ] **Step 4: Record the figures**

Paste the actual output into the PR body and into HANDOVER (Task 5). Quote measurements, never predictions.

---

## Task 5: Docs, the filed follow-up, and the PR

- [ ] **Step 1: Write the standing correction**

Create `docs-site/docs/decisions/expansion-policy-is-append-only.md`. db/010's prose ("NOT the append-only
signed overlay either … edited in place, reviewed by diff") is now false and that migration is applied and
immutable, so this is where the correction lives — the *Design decisions* section holds living records, and
this is exactly the case it exists for. Match the house style of
`docs-site/docs/decisions/correcting-a-curated-assertion.md`: what was decided, what changed, why, and what a
consumer must do differently. Cover:

- the three states (`absent` / `allow` / `deny`) and why `withdrawn` had to become a fourth;
- that `withdrawn` is **not** `allow`;
- that every reader must go through `class_expansion_policy_current`;
- that the natural key is deliberately not unique.

Add the nav entry to `docs-site/mkdocs.yml` under `Design decisions`, after the
`correcting-a-curated-assertion.md` line:

```yaml
      - The expansion policy is append-only: decisions/expansion-policy-is-append-only.md
```

Then verify: `uv run mkdocs build --strict -f docs-site/mkdocs.yml` (or the command the repo already uses).

- [ ] **Step 2: File the follow-up for the duplicated supersede**

Rule 5 — record it rather than leave it implicit:

```bash
gh issue create --title "Two copies of the insert-then-supersede rule: accumulation._supersede and interactions.record_expansion_decision" --body "$(cat <<'EOF'
Plan C's `accumulation._supersede` and db/027's `interactions.record_expansion_decision`
now each carry the overlay tier's correction rule: INSERT the new row, then point
whatever was live at it. The rule is identical; the copies are not shared.

Sharing was **considered and deliberately deferred** during #35 (see the spec, §5):
promoting a private helper to public API and refactoring four merged Plan C call sites
would have widened the blast radius of a round about a different table.

This project has spent four rounds fixing one rule kept in two places (#31, #40, #43,
db/018's two CTEs), so the duplication is filed rather than left implicit. Promote to a
shared primitive -- `db.supersede()` beside `db.clear_source_tables`, or a small
overlay module -- if a third owner appears.

Filed, not fixed.
EOF
)"
```

Note the body says **"Filed, not fixed"** in prose that cannot be parsed as a closing keyword — HANDOVER
records the sweep-closed-but-unfixed pattern happening three times (#31, #35, #40), each time because a
commit or PR body saying *filed, not fixed* still named the number.

- [ ] **Step 3: Update HANDOVER and ROADMAP**

Per rule 9: concise, under 500 lines, focused on what remains. Specifically:

- HANDOVER **⇒ NEXT**: add this round to the merged list; drop #35 from the "Next candidates" bullet that
  currently reads *"#35 is still open and Plan C did NOT close it"*.
- HANDOVER: a short **traps** section for this round — `withdrawn` ≠ `allow`; `_current` is binding, not
  merely live; four readers, one view; the natural key is deliberately not unique; db/010's prose is
  corrected in docs-site, not in the migration.
- HANDOVER **Schema** list: add `027`.
- HANDOVER: add `drugref_policy` to the database list and note what it holds.
- ROADMAP **Cross-cutting hardening**: a new ✅ DONE entry, with the measured figures from Task 4.
- Both: update the test count to the final number.
- The open-follow-ups section: #35 moves from open to closed-by-this-round; add the new issue from Step 2.

- [ ] **Step 4: Full verification before any claim of done**

```bash
DRUGREF_TEST_DSN='host=localhost port=5532 dbname=drugref_test user=postgres' uv run pytest -q
ruff check src tests
uv run mkdocs build --strict -f docs-site/mkdocs.yml
```

All three must be clean. **Do not claim green without the DSN set** — the DB-gated majority skips silently
without it, exercising none of the schema, floor or views this round is made of.

- [ ] **Step 5: Commit, push, open the PR**

```bash
git add docs-site docs/HANDOVER.md docs/ROADMAP.md
git commit -m "docs: record the expansion-policy history round (#35)"
git push -u origin fix/expansion-policy-history
gh pr create --base main --title "Expansion-policy history: the table that gates recall becomes append-only (#35)" --body "…"
```

The PR body must **close #35** (`Closes #35`), link the new follow-up issue **without** a closing keyword,
quote the Task 4 measurements verbatim, and name the two existing tests this round had to change and why —
a reviewer seeing a modified test wants to know it was the round working, not the round being accommodated.

---

## Self-review

**Spec coverage.** §1 → Tasks 1–2 · §2 (withdrawal) → Task 2 · §3.1/3.2 (surrogate key, floor, index) →
Task 1 · §3.3 (provenance unchanged) → no task needed, and that is the point: `reviewed_*` is untouched by
every task above · §3.4 (ALTER in place) → Task 1 Step 3 · §4 (one view, four readers) → Task 2 Steps 3–4 ·
§5 (writer, local supersede, vocabulary not restated) → Task 3, with the filed issue in Task 5 Step 2 ·
§6 (both halves of verification) → Task 4 (release) and Tasks 1–3 (controlled input) · §7 (traps) → Task 5
Step 3.

**Type consistency.** `policy_id` is `bigint` everywhere; `record_expansion_decision` returns it and
`withdraw_expansion_decision` returns the same through delegation. `NoLiveDecisionError` is named identically
in the implementation and the test. `class_expansion_policy_current` is spelled the same in the migration, the
four readers, both test modules and `test_db.py`.

**Known ordering hazard.** Task 1 leaves `tests/test_expansion_policy.py` importing nothing new, but Task 3
Step 4 adds `interactions` to its imports. If Task 3 is skipped, that import is never added and the local
`_revise` helper from Task 1 remains correct — the tasks degrade safely if run out of order, but they are
written to run 1 → 5.
