"""db/065: class_property is filter-pushable (#202), and class_subclass_only_property
gives what a subclass-only drug inherits in SQL (#209). DB-gated."""
import json

import pytest

from drugref import curation_property
from tests import class_profile_world as w


@pytest.fixture
def world(conn):
    return conn, w.run(conn)


def _relation_conditions(plan, relation):
    """Every scan node over `relation` in an EXPLAIN (FORMAT JSON) tree, as the text of
    its conditions (index, recheck and filter), so a test can ask what reached it."""
    out = []

    def walk(node):
        if node.get("Relation Name") == relation:
            out.append(" ".join(str(node.get(k, "")) for k in
                                ("Index Cond", "Recheck Cond", "Filter")))
        for child in node.get("Plans", ()):
            walk(child)
    walk(plan[0]["Plan"])
    return out


def test_a_single_class_read_filters_the_membership_scan(world):
    """#202: before db/065 the view's CTEs were each referenced more than once, so
    Postgres materialised them and applied `class_uuid = X` only at the end: every
    class's members were counted and joined for a one-class read. Now the filter must
    reach EVERY scan of class_membership (the members, the count, the self-partner)."""
    conn, r = world
    k = w.klass(conn, r, "K")
    m = w.moiety(conn, r, "m")
    w.member(conn, r, m, k)
    (plan,) = conn.execute(
        "EXPLAIN (FORMAT JSON) SELECT * FROM drugref.class_property "
        "WHERE class_uuid = %s", (k,)).fetchone()
    plan = plan if isinstance(plan, list) else json.loads(plan)
    scans = _relation_conditions(plan, "class_membership")
    assert scans, "class_membership is not read at all?"
    assert all(str(k) in s for s in scans), scans


def _sub(conn, k):
    return sorted((str(r[0]), r[1], r[2]) for r in conn.execute(
        "SELECT moiety_uuid, object_uuid, standing "
        "FROM drugref.class_subclass_only_property WHERE class_uuid = %s", (k,)))


def test_a_subclass_only_drug_inherits_asserted_minus_exceptions_and_itself(world):
    """#209, the same rule as class_profile_read's: K asserts "interacts with x" and
    "interacts with y"; d and x sit only in subclass S. d inherits x and has a
    confirmed exception on y; x inherits y and nothing about itself."""
    conn, r = world
    k, s = w.klass(conn, r, "K"), w.klass(conn, r, "S")
    w.parent(conn, r, s, k)
    x, y, d = (w.moiety(conn, r, n) for n in ("x", "y", "d"))
    for m in (x, d):
        w.member(conn, r, m, s)
    w.class_ci(conn, r, x, k)
    w.class_ci(conn, r, y, k)
    curation_property.record_property_ruling(
        conn, subject_moiety_uuid=d, relationship="interacts_with", object_kind="moiety",
        object_uuid=y, verdict="does_not_hold", reviewed_by="t", reviewed_against="t")
    assert _sub(conn, k) == sorted([
        (str(d), x, "inherits"), (str(d), y, "confirmed_exception"),
        (str(x), y, "inherits")])
    (via,) = {v for v, in conn.execute(
        "SELECT via_class_uuid FROM drugref.class_subclass_only_property "
        "WHERE class_uuid = %s", (k,))}
    assert via == s


def test_an_observed_property_does_not_pass_down(world):
    """Observed is a count over K's own direct members: never inherited."""
    conn, r = world
    k, s = w.klass(conn, r, "K"), w.klass(conn, r, "S")
    w.parent(conn, r, s, k)
    cond = w.condition(conn, r, "Asthma")
    for n in ("a", "b"):
        m = w.moiety(conn, r, n)
        w.member(conn, r, m, k)
        w.contraindication(conn, r, m, cond)
    d = w.moiety(conn, r, "d")
    w.member(conn, r, d, s)
    assert conn.execute("SELECT is_observed FROM drugref.class_property "
                        "WHERE class_uuid = %s", (k,)).fetchone() == (True,)
    assert _sub(conn, k) == []


def test_the_view_comment_carries_the_contract(conn):
    c = conn.execute("SELECT obj_description('drugref.class_subclass_only_property'"
                     "::regclass)").fetchone()[0]
    assert "ASSERTED" in c and "confirmed_exception" in c and "NOT" in c
