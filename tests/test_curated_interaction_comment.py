# tests/test_curated_interaction_comment.py
"""`curated_interaction`'s CATALOG COMMENT carries a measured figure, so it is data.

WHY THIS FILE EXISTS. db/029 shipped the stale `~739` twice -- once in a `--` comment
Postgres strips, and once inside `COMMENT ON TABLE drugref.curated_interaction`, which
lands in the catalog permanently for any consumer running `\\d+`. 739 is the RAW MED-RT
terminology-level CI_MoA/CI_PE count BEFORE the moiety gate; the gated figure is 635, of
which 595 reach the worklist (the other 40 pair with nobody in ddi_candidate_pair and are
already covered by gap_unpopulated_contraindication and gap_dead_by_expansion_policy).

Slice 5c.1's final whole-branch review caught the figure and corrected the migration while
it was still unapplied outside its branch -- and NOTHING PINNED THE CORRECTION. It was the
one fix of that round's five with no test, so the post-merge round of 2026-08-08 had to
read the live catalog by hand, and the state files claimed a test coverage that did not
exist. Its own file rather than an addition to test_curated_overlay.py, on the precedent of
tests/test_live_key_index_guard.py: a check plus the guard that proves it fires is one
subject, and the overlay suite is at CLAUDE.md's ~500-line mark.

ASSERTED AGAINST THE CATALOG, NEVER THE MIGRATION TEXT. db/029 is merged and therefore
frozen, so a correction arrives as a new db/NNN whose COMMENT replaces this one; the file
a grep could check is no longer the file that shipped.
"""


def _stale_population_figures(comment):
    """PURE predicate: every way the comment can misstate the curatable population.

    Returns the defects found, empty when the comment is current. Pure, and separate from
    the reader below, so the guard test can drive it with a MUTATED comment and prove the
    check actually fires -- without a second copy of the rule to disagree with this one.
    """
    if not comment:
        return ["no COMMENT ON TABLE at all"]
    defects = []
    if "739" in comment:
        defects.append("carries the stale pre-gate ~739")
    if "635" not in comment:
        defects.append("does not state the 635 gated rules")
    if "595" not in comment:
        defects.append("does not state the 595 that reach the worklist")
    return defects


def _table_comment(conn):
    """The live catalog comment, read the way a consumer's `\\d+` reads it."""
    return conn.execute(
        "SELECT obj_description('drugref.curated_interaction'::regclass, 'pg_class')"
    ).fetchone()[0]


def test_the_catalog_states_the_gated_curatable_population(conn):
    """The gate itself: what the merged db/029 actually put in the catalog.

    A future `COMMENT ON` rewrite is exactly where the design spec's approximate prose
    gets restated -- which is where the ~739 came from in the first place.
    """
    assert _stale_population_figures(_table_comment(conn)) == []


def test_the_population_check_rejects_the_comment_that_actually_shipped(conn):
    """THE GUARD'S OWN GUARD. A check only ever asserted against a passing database is a
    check nobody has seen fail. Postgres COMMENT is transactional and the `conn` fixture
    rolls back, so the real catalog can be mutated back to the shape that shipped and the
    same reader re-run against it.

    The mutation is not invented: it is the surviving text from `drugref_5c1`, the
    pre-merge verification database (PROJECT-NOTES § Repo facts), trimmed to the clause
    that carries the figure.
    """
    conn.execute(
        "COMMENT ON TABLE drugref.curated_interaction IS "
        "'Keyed on the RULE, not the pair: 21,664 pairs is not a curatable "
        "population while ~739 rules is.'")
    assert _stale_population_figures(_table_comment(conn)) == [
        "carries the stale pre-gate ~739",
        "does not state the 635 gated rules",
        "does not state the 595 that reach the worklist",
    ]
