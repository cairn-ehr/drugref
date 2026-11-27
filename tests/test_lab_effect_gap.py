"""db/092 part 3: the lab-effect worklist, gap kind 21 (#294).

DB-gated. One question per (moiety, analyte) whose label readings no live moiety
ruling answers -- the review unit (design spec 6.5) -- with a frozen key and text that
says "machine-read, not confirmed" and never that a drug is safe.
"""
import pytest

from drugref import curation_lab_effect, ids, questions
from tests import class_profile_world as w
from tests import lab_effect_world as lw

KIND = "unconfirmed_label_lab_effect"


@pytest.fixture
def world(conn):
    r = w.run(conn)
    a = w.moiety(conn, r, "drugA")
    run = lw.lab_run(conn)
    lw.label(conn, run, "set-1", a)
    lw.section(conn, run, "set-1", "precautions", "k1")
    return conn, r, run, a


def _gap(conn):
    return conn.execute("SELECT moiety_uuid, analyte_code, section_rank, label_count "
                        "FROM drugref.gap_unconfirmed_label_lab_effect").fetchall()


def _rule(conn, a, **kw):
    kw = dict(analyte_code="potassium", direction="high", role="causes",
              verdict="holds", reviewed_by="Dr X", reviewed_against="label") | kw
    return curation_lab_effect.record_lab_effect_ruling(conn, subject_moiety_uuid=a, **kw)


def test_the_gap_kind_is_admitted_and_registered(conn):
    assert KIND in questions._GAP_SOURCES
    (definition,) = conn.execute(
        "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
        "WHERE conname = 'open_question_gap_kind'").fetchone()
    assert KIND in definition


def test_two_roles_read_raise_one_question(world):
    conn, _r, run, a = world
    lw.reading(conn, run, "k1", "hyperkalemia")
    lw.reading(conn, run, "k1", "serum_potassium_increased", role_read="not_read",
               char_start=100)
    assert _gap(conn) == [(a, "potassium", 4, 1)]


def test_a_not_an_effect_reading_raises_nothing(world):
    conn, _r, run, _a = world
    lw.reading(conn, run, "k1", "hyperkalemia", role_read="not_an_effect")
    assert _gap(conn) == []


@pytest.mark.parametrize("verdict", ["holds", "does_not_hold", "cannot_establish"])
def test_any_moiety_ruling_on_the_analyte_and_direction_closes_it(world, verdict):
    """Any role, any verdict: the curator reviewed the (drug, analyte, direction)."""
    conn, _r, run, a = world
    lw.reading(conn, run, "k1", "hyperkalemia")
    _rule(conn, a, verdict=verdict, role="more_dangerous", direction="high")
    assert _gap(conn) == []


def test_the_question_has_a_frozen_key_and_honest_text(world):
    conn, r, run, a = world
    lw.reading(conn, run, "k1", "hyperkalemia")
    questions.register_from_gaps(conn, r)
    key = f"LABEFFECT:{a}:potassium"
    (gap_key, quuid, text), = conn.execute(
        "SELECT gap_key, question_uuid, question_text FROM drugref.open_question "
        "WHERE gap_kind = %s", (KIND,)).fetchall()
    assert gap_key == key and quuid == ids.mint_question_uuid(KIND, key)
    for needle in ("drugA", "Potassium", "machine-read, not confirmed", "role"):
        assert needle in text
    assert "safe" not in text.lower()


def test_a_ruling_may_cite_its_own_question_only(world):
    conn, r, run, a = world
    lw.reading(conn, run, "k1", "hyperkalemia")
    questions.register_from_gaps(conn, r)
    quuid = ids.mint_question_uuid(KIND, f"LABEFFECT:{a}:potassium")
    _rule(conn, a, question_uuid=quuid)          # its own (moiety, analyte): accepted
    with pytest.raises(ValueError):
        _rule(conn, a, analyte_code="sodium", direction="low", question_uuid=quuid)


def test_a_ruling_citing_its_question_survives_the_next_ingest(world):
    """Review finding 1: the question's DELETE would cascade into the append-only
    ruling table and abort the ingest."""
    conn, r, run, a = world
    lw.reading(conn, run, "k1", "hyperkalemia")
    questions.register_from_gaps(conn, r)
    quuid = ids.mint_question_uuid(KIND, f"LABEFFECT:{a}:potassium")
    _rule(conn, a, question_uuid=quuid)
    questions.register_from_gaps(conn, r)        # the gap is closed now
    assert conn.execute("SELECT is_current FROM drugref.open_question "
                        "WHERE question_uuid = %s", (quuid,)).fetchone() == (False,)


def test_a_ruling_in_one_direction_leaves_the_other_direction_open(world):
    """Review finding 4: a ruling on potassium LOW does not review a HIGH reading."""
    conn, _r, run, a = world
    lw.reading(conn, run, "k1", "hyperkalemia")
    _rule(conn, a, direction="low", verdict="does_not_hold")
    assert [g[:2] for g in _gap(conn)] == [(a, "potassium")]
