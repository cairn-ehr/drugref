"""db/092 part 3: the lab-effect worklist, gap kind 21 (#294).

DB-gated. One question per (moiety, analyte) while any of its label readings is still
listed in lab_effect, i.e. no live moiety ruling answers it (design spec 6.5 as
amended by sections 12 and 13), with a frozen key and text that says "machine-read,
not confirmed" and never that a drug is safe.
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


@pytest.mark.parametrize("verdict, role, closes", [
    ("holds", "more_dangerous", True),          # a holds corrects the role
    ("does_not_hold", "causes", True),
    ("cannot_establish", "causes", True),
    ("does_not_hold", "interferes", False),     # spec 13.1: another role, no review
    ("cannot_establish", "monitoring", False)])
def test_a_moiety_ruling_closes_it_per_the_review_unit(world, verdict, role, closes):
    conn, _r, run, a = world
    lw.reading(conn, run, "k1", "hyperkalemia")
    _rule(conn, a, verdict=verdict, role=role, direction="high")
    assert (_gap(conn) == []) is closes


def test_a_co_medication_ruling_leaves_the_standalone_question_open(world):
    """Spec 13.2."""
    conn, r, run, a = world
    lw.reading(conn, run, "k1", "hyperkalemia")
    _rule(conn, a, verdict="does_not_hold",
          co_medication_moiety_uuid=w.moiety(conn, r, "drugE"))
    assert [g[:2] for g in _gap(conn)] == [(a, "potassium")]


def test_an_unspecified_reading_stays_open_until_both_directions_are_ruled(world):
    """Spec 13.3: a ruling on INR high leaves the unread reading's low half open."""
    conn, _r, run, a = world
    lw.reading(conn, run, "k1", "inr_increased", direction_read="unspecified")
    _rule(conn, a, analyte_code="inr", direction="high")
    assert [g[:2] for g in _gap(conn)] == [(a, "inr")]
    _rule(conn, a, analyte_code="inr", direction="low", verdict="does_not_hold")
    assert _gap(conn) == []


def test_an_unspecified_ruling_closes_an_unspecified_reading(world):
    conn, _r, run, a = world
    lw.reading(conn, run, "k1", "inr_increased", direction_read="unspecified")
    _rule(conn, a, analyte_code="inr", direction="unspecified")
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
    with pytest.raises(ValueError):              # another drug's question
        _rule(conn, w.moiety(conn, r, "drugB"), question_uuid=quuid)
    with pytest.raises(ValueError, match="asks about one drug"):   # a class: none
        curation_lab_effect.record_lab_effect_ruling(
            conn, subject_class_uuid=w.klass(conn, r, "ClassP"),
            analyte_code="potassium", direction="high", role="causes",
            verdict="holds", reviewed_by="Dr X", reviewed_against="label",
            question_uuid=quuid)


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
    """Spec 12.1: a ruling on potassium LOW does not review a HIGH reading."""
    conn, _r, run, a = world
    lw.reading(conn, run, "k1", "hyperkalemia")
    _rule(conn, a, direction="low", verdict="does_not_hold")
    assert [g[:2] for g in _gap(conn)] == [(a, "potassium")]
