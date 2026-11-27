"""db/092 part 2: lab_effect_coverage -- "no statement" vs "no effect" (#294).

DB-gated. One test per state (design spec section 8), plus the precedence between
them. The whole point: only ruled_no_effect may ever read as "no effect";
examined_nothing_found and not_examined are unavailable context, never reassurance.
"""
import pytest

from drugref import curation_lab_effect
from tests import class_profile_world as w
from tests import lab_effect_world as lw


@pytest.fixture
def world(conn):
    r = w.run(conn)
    return conn, r, w.moiety(conn, r, "drugA")


def _state(conn, moiety, analyte="potassium", direction="high"):
    return conn.execute(
        "SELECT state FROM drugref.lab_effect_coverage WHERE moiety_uuid = %s "
        "AND analyte_code = %s AND direction = %s",
        (moiety, analyte, direction)).fetchone()[0]


def _rule(conn, **kw):
    kw = dict(analyte_code="potassium", direction="high", role="causes",
              verdict="holds", reviewed_by="Dr X", reviewed_against="label") | kw
    curation_lab_effect.record_lab_effect_ruling(conn, **kw)


def _labelled(conn, moiety, term=None):
    run = lw.lab_run(conn)
    lw.label(conn, run, f"set-{moiety}", moiety)
    lw.section(conn, run, f"set-{moiety}", "warnings", f"k-{moiety}")
    if term:
        lw.reading(conn, run, f"k-{moiety}", term)


def test_a_drug_with_no_label_is_not_examined_for_every_analyte(world):
    conn, _r, a = world
    rows = conn.execute("SELECT state, label_count FROM drugref.lab_effect_coverage "
                        "WHERE moiety_uuid = %s", (a,)).fetchall()
    assert len(rows) == 14 and set(rows) == {("not_examined", 0)}


def test_a_label_reading_is_stated(world):
    conn, _r, a = world
    _labelled(conn, a, "hyperkalemia")
    assert _state(conn, a) == "stated"


def test_a_label_read_with_nothing_for_an_analyte_is_examined_nothing_found(world):
    conn, _r, a = world
    _labelled(conn, a, "hyperkalemia")
    assert _state(conn, a, "sodium", "low") == "examined_nothing_found"
    assert conn.execute("SELECT label_count FROM drugref.lab_effect_coverage "
                        "WHERE moiety_uuid = %s AND analyte_code = 'sodium' "
                        "AND direction = 'low'", (a,)).fetchone() == (1,)


def test_a_moiety_does_not_hold_is_ruled_no_effect(world):
    conn, _r, a = world
    _rule(conn, subject_moiety_uuid=a, verdict="does_not_hold")
    assert _state(conn, a) == "ruled_no_effect"


def test_a_class_does_not_hold_reaches_a_member_with_no_reading(world):
    conn, r, a = world
    p = w.klass(conn, r, "ClassP")
    w.member(conn, r, a, p)
    _rule(conn, subject_class_uuid=p, verdict="does_not_hold")
    assert _state(conn, a) == "ruled_no_effect"


def test_a_label_reading_outranks_a_class_does_not_hold(world):
    conn, r, a = world
    p = w.klass(conn, r, "ClassP")
    w.member(conn, r, a, p)
    _labelled(conn, a, "hyperkalemia")
    _rule(conn, subject_class_uuid=p, verdict="does_not_hold")
    assert _state(conn, a) == "stated"


def test_cannot_establish_is_its_own_state(world):
    conn, _r, a = world
    _labelled(conn, a)
    _rule(conn, subject_moiety_uuid=a, verdict="cannot_establish")
    assert _state(conn, a) == "cannot_establish"


def test_the_other_direction_is_not_stated_by_a_ruling(world):
    conn, _r, a = world
    _rule(conn, subject_moiety_uuid=a)
    assert (_state(conn, a), _state(conn, a, direction="low")) == (
        "stated", "not_examined")


def test_an_unspecified_reading_states_both_directions(world):
    conn, _r, a = world
    run = lw.lab_run(conn)
    lw.label(conn, run, "s", a)
    lw.section(conn, run, "s", "warnings", "k")
    lw.reading(conn, run, "k", "inr_increased", direction_read="unspecified")
    assert (_state(conn, a, "inr", "high"), _state(conn, a, "inr", "low")) == (
        "stated", "stated")
