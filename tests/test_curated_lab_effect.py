"""curated_lab_effect (db/090) and its writer, curation_lab_effect (#294).

DB-gated. One test per clause of the table's contract: round trip, idempotence,
supersession, the role in the key, the co-medication encoding, the CHECKs, the
append-only floor, the ruling-time reach snapshot, and registration as a signed,
released kind. Design spec 2026-11-27-drugref-lab-effects-design.md section 6.5.
"""
import uuid

import psycopg
import pytest

from drugref import curation_lab_effect, releases, signing_contexts
from tests import class_profile_world as w

NIL = uuid.UUID(int=0)


@pytest.fixture
def world(conn):
    r = w.run(conn)
    return conn, r, w.moiety(conn, r, "drugA"), w.klass(conn, r, "ClassP")


def _rule(conn, *, verdict="holds", role="causes", analyte_code="potassium",
          direction="high", **kw):
    return curation_lab_effect.record_lab_effect_ruling(
        conn, analyte_code=analyte_code, direction=direction, role=role,
        verdict=verdict, reviewed_by="Dr X", reviewed_against="label 2026", **kw)


def _live(conn, subject):
    return conn.execute(
        "SELECT curated_lab_effect_id, superseded_by FROM drugref.curated_lab_effect "
        "WHERE subject_uuid = %s ORDER BY 1", (subject,)).fetchall()


def test_a_moiety_holds_ruling_round_trips(world):
    conn, _r, a, _p = world
    rid, written = _rule(conn, subject_moiety_uuid=a)
    assert written is True
    assert conn.execute(
        "SELECT subject_kind, subject_uuid, analyte_code, direction, role, verdict, "
        "co_medication_kind, co_medication_uuid FROM drugref.curated_lab_effect "
        "WHERE curated_lab_effect_id = %s", (rid,)).fetchone() == (
        "moiety", a, "potassium", "high", "causes", "holds", "none", NIL)


def test_re_recording_the_same_ruling_writes_nothing(world):
    conn, _r, a, _p = world
    rid, _ = _rule(conn, subject_moiety_uuid=a)
    assert _rule(conn, subject_moiety_uuid=a) == (rid, False)
    assert len(_live(conn, a)) == 1


@pytest.mark.parametrize("change", [
    {"verdict": "does_not_hold"}, {"onset_min_days": 3}, {"reviewed_against_": "new"}])
def test_a_changed_ruling_supersedes(world, change):
    conn, _r, a, _p = world
    old, _ = _rule(conn, subject_moiety_uuid=a)
    if "reviewed_against_" in change:
        new, _ = curation_lab_effect.record_lab_effect_ruling(
            conn, subject_moiety_uuid=a, analyte_code="potassium", direction="high",
            role="causes", verdict="holds", reviewed_by="Dr X", reviewed_against="new")
    else:
        new, _ = _rule(conn, subject_moiety_uuid=a, **change)
    assert _live(conn, a) == [(old, new), (new, None)]


def test_two_roles_for_one_analyte_are_two_live_facts(world):
    """Clozapine: causes AND monitoring for neutrophils. The role is in the key."""
    conn, _r, a, _p = world
    _rule(conn, subject_moiety_uuid=a, role="causes")
    _rule(conn, subject_moiety_uuid=a, role="monitoring")
    assert [s for (_i, s) in _live(conn, a)] == [None, None]


def test_a_co_medication_is_part_of_the_key(world):
    conn, r, a, _p = world
    warfarin = w.moiety(conn, r, "warfarin")
    rid, _ = _rule(conn, subject_moiety_uuid=a, analyte_code="inr",
                   co_medication_moiety_uuid=warfarin)
    _rule(conn, subject_moiety_uuid=a, analyte_code="inr")
    assert conn.execute(
        "SELECT co_medication_kind, co_medication_uuid FROM drugref.curated_lab_effect "
        "WHERE curated_lab_effect_id = %s", (rid,)).fetchone() == ("moiety", warfarin)
    assert [s for (_i, s) in _live(conn, a)] == [None, None]


@pytest.mark.parametrize("bad", [
    "two_co_medications", "class_cannot_establish", "method_not_interferes",
    "onset_reversed", "onset_negative", "role_not_read"])
def test_the_checks_refuse(world, bad):
    conn, r, a, p = world
    w.member(conn, r, a, p)          # so a class ruling reaches someone
    kw = {"two_co_medications": dict(subject_moiety_uuid=a,
                                     co_medication_moiety_uuid=w.moiety(conn, r, "b"),
                                     co_medication_class_uuid=p),
          "class_cannot_establish": dict(subject_class_uuid=p, verdict="cannot_establish"),
          "method_not_interferes": dict(subject_moiety_uuid=a, method="immunoassay"),
          "onset_reversed": dict(subject_moiety_uuid=a, onset_min_days=30,
                                 onset_max_days=7),
          "onset_negative": dict(subject_moiety_uuid=a, onset_min_days=-1),
          "role_not_read": dict(subject_moiety_uuid=a, role="not_read")}[bad]
    with pytest.raises(psycopg.errors.CheckViolation):
        _rule(conn, **kw)


def test_an_interference_ruling_may_name_its_method(world):
    conn, _r, a, _p = world
    _rule(conn, subject_moiety_uuid=a, role="interferes", direction="unspecified",
          method="copper reduction")   # control for method_not_interferes


def test_an_unknown_analyte_is_refused(world):
    conn, _r, a, _p = world
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        _rule(conn, subject_moiety_uuid=a, analyte_code="biotin_level")


@pytest.mark.parametrize("statement", [
    "UPDATE drugref.curated_lab_effect SET verdict = 'does_not_hold'",
    "DELETE FROM drugref.curated_lab_effect"])
def test_the_floor(world, statement):
    conn, _r, a, _p = world
    _rule(conn, subject_moiety_uuid=a)
    with pytest.raises(psycopg.errors.RaiseException):
        conn.execute(statement)


def _reach(conn, rid):
    row = conn.execute("SELECT members, captured FROM drugref.curated_lab_effect_reach "
                       "WHERE curated_lab_effect_id = %s", (rid,)).fetchone()
    return row and (sorted(row[0]), row[1])


def test_a_class_ruling_snapshots_a_subclass_member(world):
    conn, r, a, p = world
    c = w.klass(conn, r, "ClassC")
    w.parent(conn, r, c, p)
    w.member(conn, r, a, c)          # a is in the child class only
    rid, _ = _rule(conn, subject_class_uuid=p)
    assert _reach(conn, rid) == ([a], "at_ruling")


def test_a_class_ruling_that_reaches_no_drug_is_refused(world):
    """A ruling on a class with no members (or a MED-RT PK class, which
    lab_effect_class_member excludes) would state nothing about any drug."""
    conn, _r, _a, p = world
    with pytest.raises(ValueError, match="reaches no drug"):
        _rule(conn, subject_class_uuid=p)
    assert _live(conn, p) == []


def test_a_moiety_ruling_has_no_reach(world):
    conn, _r, a, _p = world
    rid, _ = _rule(conn, subject_moiety_uuid=a)
    assert _reach(conn, rid) is None


def test_a_corrected_class_ruling_snapshots_again_under_its_new_id(world):
    conn, r, a, p = world
    w.member(conn, r, a, p)
    old, _ = _rule(conn, subject_class_uuid=p)
    b = w.moiety(conn, r, "drugB")
    w.member(conn, r, b, p)
    new, _ = _rule(conn, subject_class_uuid=p, onset_max_days=14)
    assert _reach(conn, old) == ([a], "at_ruling")
    assert _reach(conn, new) == (sorted([a, b]), "at_ruling")


def test_the_reach_is_insert_only(world):
    conn, r, a, p = world
    w.member(conn, r, a, p)
    _rule(conn, subject_class_uuid=p)
    with pytest.raises(psycopg.errors.RaiseException, match="insert-only"):
        conn.execute("UPDATE drugref.curated_lab_effect_reach SET members = '{}'")


def test_a_ruling_is_a_signed_released_kind():
    assert "curated_lab_effect" in releases._CURATED_KINDS
    assert signing_contexts.NATURAL_KEY_COLUMNS["curated_lab_effect/v1"] == (
        "subject_kind", "subject_uuid", "analyte_code", "direction", "role",
        "co_medication_kind", "co_medication_uuid")


def _raw_live(conn, a, *, direction="high", role="causes", co=None):
    """A live row INSERTed directly, bypassing the writer's supersede."""
    conn.execute(
        "INSERT INTO drugref.curated_lab_effect (subject_moiety_uuid, analyte_code, "
        "direction, role, co_medication_moiety_uuid, verdict, source, reviewed_by, "
        "reviewed_against) VALUES (%s, 'potassium', %s, %s, %s, 'holds', "
        "'DRUGREF', 'Dr X', 'label')", (a, direction, role, co))


def test_the_single_live_guard_refuses_two_live_rows_on_one_key(world):
    """The guard is DEFERRED and the conn fixture never commits: fire it by hand."""
    conn, _r, a, _p = world
    _raw_live(conn, a)
    _raw_live(conn, a)
    with pytest.raises(psycopg.errors.RaiseException, match="live rows"):
        conn.execute("SET CONSTRAINTS ALL IMMEDIATE")


def test_the_single_live_guard_keys_on_role_direction_and_co_medication(world):
    """Rows differing only in role, direction or co-medication survive the deferred
    check, so those columns really are in the guard's key (the two key tests above
    go through the writer, whose rows the deferred guard never sees)."""
    conn, r, a, _p = world
    _raw_live(conn, a)
    _raw_live(conn, a, role="monitoring")
    _raw_live(conn, a, direction="low")
    _raw_live(conn, a, co=w.moiety(conn, r, "warfarin"))
    conn.execute("SET CONSTRAINTS ALL IMMEDIATE")      # no raise


def test_the_guard_key_is_the_signed_natural_key(conn):
    (args,) = conn.execute(
        "SELECT tgargs FROM pg_trigger WHERE tgname = 'curated_lab_effect_single_live'"
    ).fetchone()
    cols = tuple(bytes(args).decode().rstrip("\x00").split("\x00"))
    assert cols[-7:] == signing_contexts.NATURAL_KEY_COLUMNS["curated_lab_effect/v1"]


@pytest.mark.parametrize("subject", ["class", "co_medication_class"])
def test_re_recording_a_class_or_co_medication_ruling_writes_nothing(world, subject):
    """The writer recomputes db/090's generated key columns in Python to find the live
    row; a drift there would make every re-run a new live row."""
    conn, r, a, p = world
    w.member(conn, r, a, p)
    kw = ({"subject_class_uuid": p} if subject == "class" else
          {"subject_moiety_uuid": a, "co_medication_class_uuid": p})
    rid, _ = _rule(conn, **kw)
    assert _rule(conn, **kw) == (rid, False)
    new, written = _rule(conn, onset_max_days=9, **kw)
    assert written and conn.execute(
        "SELECT superseded_by FROM drugref.curated_lab_effect "
        "WHERE curated_lab_effect_id = %s", (rid,)).fetchone() == (new,)
