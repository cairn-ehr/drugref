"""Gap kind 19: unconfirmed_class_property (db/061). DB-gated.

A class property observed in >= 3/4 of a class's members but not confirmed for one
member is a question about THAT DRUG, not about the class.
"""
import pytest

from drugref import curation_property, ids, questions
from tests import class_profile_world as w


def _world(conn, n=4):
    """Class of n members; all but the last carry CI_with condition c (>= 75%)."""
    r = w.run(conn)
    k = w.klass(conn, r, "K")
    c = w.condition(conn, r, "Cond")
    ms = [w.moiety(conn, r, f"m{i}") for i in range(n)]
    for m in ms:
        w.member(conn, r, m, k)
    for m in ms[:-1]:
        w.contraindication(conn, r, m, c)
    return r, k, c, ms


def _gap(conn):
    return conn.execute(
        "SELECT moiety_uuid, relationship, object_kind, object_uuid, raising_classes "
        "FROM drugref.gap_unconfirmed_class_property").fetchall()


def _rule(conn, m, c, verdict):
    curation_property.record_property_ruling(
        conn, subject_moiety_uuid=m, relationship="CI_with", object_kind="condition",
        object_uuid=c, verdict=verdict, reviewed_by="t", reviewed_against="t")


def test_the_gap_kind_is_admitted_and_registered(conn):
    assert "unconfirmed_class_property" in questions._GAP_SOURCES
    (definition,) = conn.execute(
        "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
        "WHERE conname = 'open_question_gap_kind'").fetchone()
    assert "unconfirmed_class_property" in definition
    assert conn.execute(
        "SELECT count(*) FROM drugref.gap_unconfirmed_class_property").fetchone() == (0,)


def test_a_not_confirmed_member_raises_one_question_with_a_frozen_key(conn):
    r, k, c, ms = _world(conn)
    m = ms[-1]
    questions.register_from_gaps(conn, r)
    key = f"CLASSPROP:{m}:CI_with:condition:{c}"
    rows = conn.execute(
        "SELECT gap_key, question_uuid, question_text FROM drugref.open_question "
        "WHERE gap_kind = 'unconfirmed_class_property'").fetchall()
    assert len(rows) == 1
    assert rows[0][0] == key
    assert rows[0][1] == ids.mint_question_uuid("unconfirmed_class_property", key)
    text = rows[0][2]
    # The alert sentence is on EVERY question: no alert-path view reads
    # curated_property_ruling (#204), so it is true of condition answers too.
    for needle in ("m3", "CI_with", "Cond", "K", "holds", "does not hold",
                   "cannot be established",
                   "Answering does not change prescribing alerts."):
        assert needle in text
    assert "safe" not in text.lower()


def test_two_classes_raise_one_question(conn):
    r, k, c, ms = _world(conn)
    k2 = w.klass(conn, r, "K2")
    for m in ms:
        w.member(conn, r, m, k2)
    (row,) = _gap(conn)
    assert row[0] == ms[-1]
    assert row[4] == sorted([k, k2])


@pytest.mark.parametrize("verdict", ["holds", "does_not_hold", "cannot_establish"])
def test_any_live_answer_closes_the_question(conn, verdict):
    r, k, c, ms = _world(conn)
    assert len(_gap(conn)) == 1
    _rule(conn, ms[-1], c, verdict)
    assert _gap(conn) == []
    if verdict == "cannot_establish":
        # Off the worklist, but still NOT confirmed (and never "safe").
        assert conn.execute(
            "SELECT standing FROM drugref.class_property_member "
            "WHERE moiety_uuid = %s", (ms[-1],)).fetchone() == ("not_confirmed",)


def test_inherited_and_carried_members_raise_nothing(conn):
    r, k, c, ms = _world(conn)
    _rule(conn, ms[-1], c, "holds")           # now every member carries
    assert _gap(conn) == []
    # An asserted property is 'inherits' for a member with no record of its own.
    r2 = w.run(conn)
    k3 = w.klass(conn, r2, "K3")
    c3 = w.condition(conn, r2, "Cond3")
    m = w.moiety(conn, r2, "solo")
    w.member(conn, r2, m, k3)
    curation_property.record_property_ruling(
        conn, subject_class_uuid=k3, relationship="CI_with", object_kind="condition",
        object_uuid=c3, verdict="holds", reviewed_by="t", reviewed_against="t")
    assert _gap(conn) == []


def test_a_ruling_from_the_partners_side_closes_the_question(conn):
    """Interactions are symmetric (member_ruling_oriented): a ruling recorded as
    (Y, interacts_with, moiety, D) answers D's question about Y too."""
    r = w.run(conn)
    k = w.klass(conn, r, "K")
    y = w.moiety(conn, r, "partner")
    ms = [w.moiety(conn, r, f"m{i}") for i in range(4)]
    for m in ms:
        w.member(conn, r, m, k)
    for m in ms[:-1]:
        w.moiety_ci(conn, r, m, y)
    assert [g[0] for g in _gap(conn)] == [ms[-1]]
    curation_property.record_property_ruling(
        conn, subject_moiety_uuid=y, relationship="interacts_with", object_kind="moiety",
        object_uuid=ms[-1], verdict="cannot_establish", reviewed_by="t",
        reviewed_against="t")
    assert _gap(conn) == []
