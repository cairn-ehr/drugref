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


ROLES = ("causes", "more_dangerous", "monitoring", "interferes")


def _rule_out(conn, roles=ROLES, **kw):
    for role in roles:
        _rule(conn, role=role, verdict="does_not_hold", **kw)


def _ruled_out(conn, moiety):
    return conn.execute(
        "SELECT ruled_out_roles FROM drugref.lab_effect_coverage WHERE moiety_uuid = %s "
        "AND analyte_code = 'potassium' AND direction = 'high'", (moiety,)).fetchone()[0]


def test_a_moiety_ruled_out_in_every_role_is_ruled_no_effect(world):
    conn, _r, a = world
    _rule_out(conn, subject_moiety_uuid=a)
    assert _state(conn, a) == "ruled_no_effect"
    assert _ruled_out(conn, a) == list(ROLES)


def test_ruling_out_one_role_is_not_no_effect(world):
    """Review finding 2: ruling out interference says nothing about causation."""
    conn, _r, a = world
    _labelled(conn, a)
    _rule_out(conn, roles=("interferes",), subject_moiety_uuid=a)
    assert _state(conn, a) == "examined_nothing_found"
    assert _ruled_out(conn, a) == ["interferes"]


def test_a_does_not_hold_with_a_co_medication_is_not_no_effect(world):
    """Review finding 2: "not with drug B" is not "no effect"."""
    conn, r, a = world
    b = w.moiety(conn, r, "drugB")
    _rule_out(conn, subject_moiety_uuid=a, co_medication_moiety_uuid=b)
    assert _state(conn, a) == "not_examined"
    assert _ruled_out(conn, a) == []


def test_a_class_ruled_out_in_every_role_reaches_a_member(world):
    conn, r, a = world
    p = w.klass(conn, r, "ClassP")
    w.member(conn, r, a, p)
    _rule_out(conn, subject_class_uuid=p)
    assert _state(conn, a) == "ruled_no_effect"


def test_a_label_reading_outranks_a_class_does_not_hold(world):
    conn, r, a = world
    p = w.klass(conn, r, "ClassP")
    w.member(conn, r, a, p)
    _labelled(conn, a, "hyperkalemia")
    _rule_out(conn, subject_class_uuid=p)
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
