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
    """#202: before db/065 several of the view's CTEs were referenced more than once,
    so Postgres materialised them and applied `class_uuid = X` only at the end: every
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
    """#209, spec §4's rule (formerly done in Python by class_profile_read): K asserts "interacts with x" and
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
    assert "ASSERTED" in c and "confirmed_exception" in c
    assert "NOT" in c and "inherit" in c.lower()


def test_a_drug_in_two_subclasses_gets_one_route_per_via_class(world):
    """The view COMMENT and SubclassOnlyDrug promise one row per (drug, via): collapsed
    routes would show the second as "inherits nothing", which reads as safe."""
    from drugref import class_profile_read as cpr
    conn, r = world
    k, s1, s2 = (w.klass(conn, r, n) for n in ("K", "S1", "S2"))
    for s in (s1, s2):
        w.parent(conn, r, s, k)
    x, d = w.moiety(conn, r, "x"), w.moiety(conn, r, "d")
    for s in (s1, s2):
        w.member(conn, r, d, s)
    w.class_ci(conn, r, x, k)
    rows = sorted((str(v), o, st) for v, o, st in conn.execute(
        "SELECT via_class_uuid, object_uuid, standing "
        "FROM drugref.class_subclass_only_property "
        "WHERE class_uuid = %s AND moiety_uuid = %s", (k, d)))
    assert rows == sorted([(str(s1), x, "inherits"), (str(s2), x, "inherits")])
    routes = cpr.class_profile(conn, k).subclass_only
    assert sorted(e.via for e in routes) == ["S1", "S2"]
    assert all(len(e.inherits) == 1 for e in routes)


def test_an_ancestors_assertion_reaches_a_grandchild_route(world):
    """P > K > S1 > G, x asserted on P only, e a member of G only. Read for K, e
    inherits x, and `via` is G (e's direct class), not the intermediate S1."""
    conn, r = world
    p, k, s1, g = (w.klass(conn, r, n) for n in ("P", "K", "S1", "G"))
    w.parent(conn, r, k, p)
    w.parent(conn, r, s1, k)
    w.parent(conn, r, g, s1)
    x, e = w.moiety(conn, r, "x"), w.moiety(conn, r, "e")
    w.member(conn, r, e, g)
    w.class_ci(conn, r, x, p)
    assert list(conn.execute(
        "SELECT moiety_uuid, via_class_uuid, object_uuid, standing "
        "FROM drugref.class_subclass_only_property WHERE class_uuid = %s", (k,))) == [
        (e, g, x, "inherits")]


def _db064_class_property_sql():
    """db/064's class_property body, verbatim. Applied migrations are immutable, so
    the file text is a stable reference to compare db/065's rewrite against."""
    from pathlib import Path
    text = (Path(__file__).parent.parent / "db" /
            "064_class_property_eligible_members.sql").read_text()
    head = "CREATE OR REPLACE VIEW drugref.class_property AS"
    body = text[text.index(head) + len(head):]
    return body[:body.index(";\n\nCOMMENT ON VIEW drugref.class_property")]  # a ";" also sits in a comment


def test_the_rewrite_returns_exactly_db064s_rows(world):
    """db/065 changes shape, not rows. One world exercising what the new GROUP BY
    merges: a property asserted through two (via, source) pairs AND carried by
    members (carriers, members, observed must not fan out), a self-partner, a
    member-less class, a PK ancestor, and an observed-only property."""
    conn, r = world
    onc = w.run(conn, source="ONCHIGH")
    pk = w.klass(conn, r, "Clearance", concept_type="PK")
    p, c, empty, o = (w.klass(conn, r, n) for n in ("P", "C", "Empty", "O"))
    w.parent(conn, r, p, pk)
    w.parent(conn, r, c, p)
    w.parent(conn, r, empty, c)
    x, a, b, d = (w.moiety(conn, r, n) for n in ("x", "a", "b", "d"))
    for m in (x, a, b, d):
        w.member(conn, r, m, c)               # x is a self-partner of C
    for m in (a, b, d):
        w.moiety_ci(conn, r, m, x)            # 3 of the 3 eligible carry it
    for klass in (p, c):
        w.class_ci(conn, r, x, klass)
        w.class_ci(conn, onc, x, klass, source="ONCHIGH")
    w.class_ci(conn, r, a, pk, source="ONCHIGH")
    cond = w.condition(conn, r, "Asthma")
    for n in ("o1", "o2"):
        m = w.moiety(conn, r, n)
        w.member(conn, r, m, o)
        w.contraindication(conn, r, m, cond)
    conn.execute(f"CREATE TEMP VIEW cp064 AS {_db064_class_property_sql()}")
    assert conn.execute("SELECT count(*) FROM drugref.class_property").fetchone()[0] > 3
    for left, right in (("drugref.class_property", "cp064"),
                        ("cp064", "drugref.class_property")):
        diff = conn.execute(f"SELECT * FROM {left} EXCEPT ALL "
                            f"SELECT * FROM {right}").fetchall()
        assert diff == [], (left, diff)
