"""db/092 part 1: the lab-effect read view and its label evidence (#294).

DB-gated. lab_effect is the contract ReportTriage reads: for an analyte and a
direction, which drugs and classes matter and in which role. These tests pin its
precedence rules (design spec 6.5 and 7): a moiety ruling answers every machine
reading for its (moiety, analyte); a class ruling reaches its members, a member's own
does_not_hold carves it out; a class ruling never hides a member's label readings.
"""
import uuid

import pytest

from drugref import curation_lab_effect
from tests import class_profile_world as w
from tests import lab_effect_world as lw

_COLS = ("analyte_code, query_direction, direction, role, basis, confirmed, "
         "strongest_section, label_count, stated_as")


@pytest.fixture
def world(conn):
    r = w.run(conn)
    a = w.moiety(conn, r, "drugA")
    run = lw.lab_run(conn)
    lw.label(conn, run, "set-1", a)
    lw.section(conn, run, "set-1", "warnings", "k1")
    return conn, r, run, a


def _rows(conn, subject, cols=_COLS):
    return sorted(conn.execute(f"SELECT {cols} FROM drugref.lab_effect "
                               "WHERE subject_uuid = %s", (subject,)).fetchall(),
                  key=repr)


def _rule(conn, **kw):
    kw = dict(analyte_code="potassium", direction="high", role="causes",
              verdict="holds", reviewed_by="Dr X", reviewed_against="label") | kw
    return curation_lab_effect.record_lab_effect_ruling(conn, **kw)[0]


def test_a_label_reading_reads_back_unconfirmed(world):
    conn, _r, run, a = world
    lw.reading(conn, run, "k1", "hyperkalemia")
    assert _rows(conn, a) == [
        ("potassium", "high", "high", "causes", "label", False, "warnings", 1,
         ["hyperkalemia"])]


def test_the_strongest_section_wins_and_labels_are_counted(world):
    conn, _r, run, a = world
    lw.reading(conn, run, "k1", "hyperkalemia")
    lw.label(conn, run, "set-2", a)
    lw.section(conn, run, "set-2", "boxed_warning", "k2")
    lw.reading(conn, run, "k2", "serum_potassium_increased")
    assert _rows(conn, a) == [
        ("potassium", "high", "high", "causes", "label", False, "boxed_warning", 2,
         ["hyperkalemia", "serum_potassium_increased"])]


def test_a_not_an_effect_reading_is_evidence_only(world):
    conn, _r, run, a = world
    lw.reading(conn, run, "k1", "hyperkalemia", role_read="not_an_effect")
    assert _rows(conn, a) == []
    assert conn.execute(
        "SELECT role_read, section, analyte_code FROM drugref.lab_effect_label_evidence "
        "WHERE subject_moiety = %s", (a,)).fetchall() == [
        ("not_an_effect", "warnings", "potassium")]


def test_a_curator_role_correction_replaces_the_machine_reading(world):
    """Review focus 3: the misread role must not keep showing beside the ruling."""
    conn, _r, run, a = world
    lw.reading(conn, run, "k1", "hypokalemia")
    _rule(conn, subject_moiety_uuid=a, direction="low", role="more_dangerous")
    assert _rows(conn, a, "role, basis, confirmed") == [
        ("more_dangerous", "curated", True)]
    assert conn.execute(
        "SELECT role_read FROM drugref.lab_effect_label_evidence "
        "WHERE subject_moiety = %s", (a,)).fetchall() == [("causes",)]


def test_a_not_read_reading_is_shown_until_any_ruling_on_the_analyte(world):
    conn, _r, run, a = world
    lw.reading(conn, run, "k1", "hyperkalemia", role_read="not_read")
    assert _rows(conn, a, "role, basis") == [("not_read", "label")]
    _rule(conn, subject_moiety_uuid=a, verdict="does_not_hold")
    assert _rows(conn, a) == []


def test_a_ruling_on_another_analyte_leaves_the_reading(world):
    conn, _r, run, a = world
    lw.reading(conn, run, "k1", "hyperkalemia")
    _rule(conn, subject_moiety_uuid=a, analyte_code="sodium", direction="low")
    assert sorted(_rows(conn, a, "analyte_code, basis")) == [
        ("potassium", "label"), ("sodium", "curated")]


def test_a_class_ruling_reaches_a_subclass_member(world):
    """Review focus 2: membership below the ruled class counts."""
    conn, r, _run, a = world
    p, c = w.klass(conn, r, "ClassP"), w.klass(conn, r, "ClassC")
    w.parent(conn, r, c, p)
    w.member(conn, r, a, c)
    _rule(conn, subject_class_uuid=p)
    assert _rows(conn, p, "subject_kind, basis, confirmed, reached_via_class_uuid") == [
        ("class", "curated", True, None)]
    assert _rows(conn, a, "subject_kind, basis, confirmed, reached_via_class_uuid") == [
        ("moiety", "curated", True, p)]


def test_a_member_does_not_hold_carves_it_out_of_a_class_ruling(world):
    conn, r, _run, a = world
    p = w.klass(conn, r, "ClassP")
    w.member(conn, r, a, p)
    _rule(conn, subject_class_uuid=p)
    _rule(conn, subject_moiety_uuid=a, verdict="does_not_hold")
    assert _rows(conn, a) == []
    assert len(_rows(conn, p)) == 1


def test_a_class_ruling_never_hides_a_member_label_reading(world):
    conn, r, run, a = world
    p = w.klass(conn, r, "ClassP")
    w.member(conn, r, a, p)
    lw.reading(conn, run, "k1", "hyperkalemia")
    _rule(conn, subject_class_uuid=p, verdict="does_not_hold")
    assert _rows(conn, a, "basis") == [("label",)]


def test_an_unspecified_direction_answers_both_questions(world):
    """Review focus 4."""
    conn, _r, run, a = world
    lw.reading(conn, run, "k1", "inr_increased", direction_read="unspecified")
    assert _rows(conn, a, "query_direction, direction") == [
        ("high", "unspecified"), ("low", "unspecified")]


def test_the_view_works_with_no_published_release(world):
    """Review focus 1: current_release has no row here, and must not empty the view."""
    conn, _r, run, a = world
    assert conn.execute("SELECT count(*) FROM drugref.current_release").fetchone()[0] == 0
    lw.reading(conn, run, "k1", "hyperkalemia")
    assert _rows(conn, a, "release_tag, release_drifted") == [(None, None)]


def test_a_co_medication_is_carried(world):
    conn, r, run, a = world
    warfarin = w.moiety(conn, r, "warfarin")
    lw.reading(conn, run, "k1", "inr_increased", co_medication_moiety_uuid=warfarin)
    assert _rows(conn, a, "co_medication_kind, co_medication_uuid") == [
        ("moiety", warfarin)]


def test_a_reading_with_no_co_medication_uses_the_nil_uuid(world):
    conn, _r, run, a = world
    lw.reading(conn, run, "k1", "hyperkalemia")
    assert _rows(conn, a, "co_medication_kind, co_medication_uuid") == [
        ("none", uuid.UUID(int=0))]


def test_a_curated_row_carries_onset_and_names(world):
    conn, _r, _run, a = world
    rid = _rule(conn, subject_moiety_uuid=a, onset_min_days=7, onset_max_days=42)
    assert _rows(conn, a, "subject_name, specimen, via_pathway, curated_lab_effect_id, "
                          "onset_min_days, onset_max_days") == [
        ("drugA", "blood", False, rid, 7, 42)]


def test_an_unresolved_subject_states_nothing(world):
    conn, _r, run, _a = world
    lw.label(conn, run, "set-9", None)
    lw.section(conn, run, "set-9", "warnings", "k9")
    lw.reading(conn, run, "k9", "hyperkalemia")
    assert conn.execute("SELECT count(*) FROM drugref.lab_effect").fetchone()[0] == 0
