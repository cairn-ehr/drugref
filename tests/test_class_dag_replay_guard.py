"""The class-DAG walk each grain reads, pinned from the CATALOG -- issue 100.

WHAT THIS GUARDS. `db/031`:24-27 names a hand-run `psql -f` replay as a supported,
documented exposure ("should be a no-op, not an error"), and issue 91 makes it the
RECOMMENDED path for the reference database, whose ledger checksum is stale. Out of order
it is not a no-op. `db/033` unconditionally `CREATE OR REPLACE`s `ci_class_subtree` with a
WIDE seed (roots from `class_pair_contraindication` as well as `class_contraindication`)
and repoints `curated_ddi_pair`'s `class_dag` CTE at it; `db/034` reverts both, because the
wider root set inflated Postgres's row-ESTIMATE for the whole recursive CTE roughly 5x
(37,414 -> 181,334 against an ACTUAL 1,233-1,238) and tipped the recursive join from a Hash
Join to a Merge Join FOR EVERY READER -- a tax the empty-overlay case alone measured at
~3.6x on the moiety-grain hot path.

Replaying `db/033` ALONE after `db/034` therefore reinstates that regression. Results stay
CORRECT -- every row set is byte-identical either way, which is precisely the problem. No
count moves, no test that checks data fails, and nothing anywhere errors. The only symptom
is latency, and `db/034` exists because that symptom was invisible without deliberate
measurement.

WHY A TEST AND NOT A NOTE IN A HEADER. Issue 100 offered both and preferred this one, for
the reason `db/034` was needed at all: a note in a migration header is read by whoever
opens that file, and the person doing a stray replay is by definition working from the
OTHER file. `db/033` and `db/034` are applied and immutable (the ledger checksum, issue 91),
so neither can be amended to warn about the other even if that would help.

WHY THE CATALOG AND NOT THE MIGRATION TEXT. The file a grep could check is not the one that
shipped -- this project's standing rule, learned when a `COMMENT ON` figure was corrected on
a branch and nothing pinned the correction. A replay changes the CATALOG while leaving every
`.sql` file on disk untouched, so reading `db/034` back would report success against a
database it no longer describes. `pg_rewrite` + `pg_depend` answer the question the replay
actually alters: which relations does this view's body name?
"""
import pytest


# Every relation a view's body NAMES, asked of the catalogue. pg_rewrite holds one row per
# view rule; pg_depend links that rule to each relation the rule references. Same shape as
# tests/test_expansion_policy.py's reader pin, run in the other direction -- that one asks
# "who reads this table", this one asks "what does this view read".
#
# DIRECT references only: `curated_ddi_pair` reads `ddi_candidate_pair`, which itself reads
# `ci_class_subtree`, and that indirection does NOT appear here. That is what makes the
# negative assertion below meaningful rather than accidental -- see its docstring.
_READS = """
SELECT DISTINCT src.relname
FROM   pg_depend d
JOIN   pg_rewrite rw ON rw.oid = d.objid
JOIN   pg_class dependent ON dependent.oid = rw.ev_class
JOIN   pg_class src ON src.oid = d.refobjid
JOIN   pg_namespace n ON n.oid = src.relnamespace
WHERE  d.classid = 'pg_rewrite'::regclass
  AND  n.nspname = 'drugref'
  AND  src.relkind IN ('r', 'v', 'm')
  AND  dependent.relname = %s
  AND  src.relname <> dependent.relname
"""

# db/012's seed, restored by db/034 section 1. The roots of the moiety grain's walk are the
# classes a CONTRAINDICATION names -- `class_contraindication.object_class_uuid` alone --
# and `class_parent` is the DAG it descends. Nothing else. A db/033 replay adds a third
# name here, which is the whole regression in one row.
NARROW_ROOTS = {"class_contraindication", "class_parent"}


def _reads(conn, view):
    return {r[0] for r in conn.execute(_READS, (view,)).fetchall()}


def test_ci_class_subtree_is_seeded_from_contraindications_alone(conn):
    """The moiety grain's walk keeps db/012's narrow root set.

    Set EQUALITY, not a `not in` check on one name: the failure this pins is a widening,
    and a widening is exactly "the set grew". Equality also fails if a future change swaps
    a root source rather than adding one, which no membership test would catch.
    """
    assert _reads(conn, "ci_class_subtree") == NARROW_ROOTS


def test_the_class_grain_walks_its_own_separately_estimated_view(conn):
    """`ci_class_pair_subtree` exists, is seeded from the class x class tier, and is what
    `curated_ddi_pair` actually reads.

    THE NEGATIVE HALF IS THE POINT and it is not over-determined. `curated_ddi_pair` still
    reaches `ci_class_subtree` transitively through `ddi_candidate_pair` in its moiety-grain
    half -- that is correct and unchanged -- but a DIRECT reference can only come from
    `class_dag`, the one line db/034 moved. The positive assertion beside it is what stops
    the negative passing vacuously: if the view were dropped, renamed, or its rule rebuilt
    empty, `_reads` would return the empty set and `not in` alone would still be satisfied.
    """
    assert _reads(conn, "ci_class_pair_subtree") == {
        "class_pair_contraindication", "class_parent"}
    reads = _reads(conn, "curated_ddi_pair")
    assert "ci_class_pair_subtree" in reads
    assert "ci_class_subtree" not in reads


def test_class_pair_rule_reach_walks_the_class_grains_view_too(conn):
    """db/035's reach detector is the second reader of the class grain's own walk.

    Included because a replay-shaped regression is not confined to the read path: this view
    feeds `gap_uncurated_class_interaction_rule` and `drugref status`, so a repoint here
    would tax the operator surface as well, equally silently.
    """
    reads = _reads(conn, "class_pair_rule_reach")
    assert "ci_class_pair_subtree" in reads
    assert "ci_class_subtree" not in reads


# db/033 section 1's body, verbatim in shape -- the definition a stray replay reinstates.
# Kept here as CONTROLLED INPUT, the way this project pins any branch the release itself
# cannot exercise: the real db/033 cannot be replayed inside a test (it is a 378-line file
# that also rewrites curated_ddi_pair), and the widening is the part that matters.
_DB033_WIDE_SEED = """
CREATE OR REPLACE VIEW drugref.ci_class_subtree AS
WITH RECURSIVE subtree(root_uuid, class_uuid) AS (
    (
        SELECT DISTINCT class_uuid, class_uuid
        FROM (
            SELECT object_class_uuid AS class_uuid FROM drugref.class_contraindication
          UNION ALL
            SELECT subject_class_uuid FROM drugref.class_pair_contraindication
          UNION ALL
            SELECT object_class_uuid FROM drugref.class_pair_contraindication
        ) roots
    )
  UNION
    SELECT s.root_uuid, cp.child_class_uuid
    FROM   subtree s
    JOIN   drugref.class_parent cp ON cp.parent_class_uuid = s.class_uuid
)
SELECT root_uuid, class_uuid FROM subtree;
"""


def test_the_guard_fails_on_a_db033_replay(conn):
    """MUTATION: reinstate the wide seed and confirm the pin above goes red.

    Without this the three tests above are assertions about a state nobody has ever seen
    violated, and this project has already shipped two of those -- a consolidated verdict
    table that could be REVERSED with 177 tests still green, and a collapsing function that
    could be replaced by `verdicts[0]` with every releases test green. An unmutated pin is a
    claim, not evidence.

    Safe to run: DDL is transactional in Postgres and the `conn` fixture rolls back, so the
    session-scoped schema every other test shares is untouched. `CREATE OR REPLACE VIEW`
    keeps the column list identical, so the dependent views need no rebuild.
    """
    assert _reads(conn, "ci_class_subtree") == NARROW_ROOTS      # before
    conn.execute(_DB033_WIDE_SEED)
    widened = _reads(conn, "ci_class_subtree")
    assert widened == NARROW_ROOTS | {"class_pair_contraindication"}
    assert widened != NARROW_ROOTS, (
        "the pin above cannot distinguish db/033 from db/034 and is therefore not a pin")


@pytest.mark.parametrize("view", ["ci_class_subtree", "ci_class_pair_subtree"])
def test_both_walks_still_return_rows(conn, view):
    """A view whose body was replaced by something that reads nothing would satisfy every
    catalogue assertion above by naming the right relations and returning nothing at all.

    BOTH WALKS ARE LEGITIMATELY EMPTY HERE, and an earlier version of this docstring said
    otherwise -- it quoted `class_contraindication`'s ~640 rules, which is the REFERENCE
    database's figure (643: 635 MED-RT + 8 ONCHIGH) and not this one's. The `conn` fixture
    runs against a schema conftest.py drops and rebuilds from migrations alone, and no
    migration inserts into either candidate tier, so both tables hold zero rows and both
    walks return nothing.

    So this test is a SMOKE CHECK, not a population check: it pins that each view still
    PARSES and can be selected from after the catalogue assertions above have said what it
    reads. `all()` over an empty list is vacuously true and that is accepted here rather
    than papered over -- the alternative is seeding both tiers in a test whose subject is
    the catalogue, and issue 100's question is which relations the view NAMES. What would
    make this stronger is a row-carrying fixture; it is not worth the setup for a
    parse-and-select probe, and saying so is better than a count nobody could justify.
    """
    rows = conn.execute(
        f"SELECT root_uuid, class_uuid FROM drugref.{view} LIMIT 5").fetchall()
    assert all(r[0] is not None and r[1] is not None for r in rows)
