# tests/test_spl_class_schema.py
"""db/066's shape: class occurrences, the statement views, the worklist gap. DB-gated.

The two CHECK lists (match_form, advice_scope) and the level table are SECOND HOMES of
Python vocabularies, so each is pinned against the Python tuple: a value the reader can
produce and the database refuses aborts an ingest at whichever row carries it first.
"""
import uuid

import psycopg
import pytest

from drugref import curation_property, spl_evidence
from drugref.ingest import spl, spl_advice, spl_match
from tests import class_profile_world as w

KEY = "a" * 64


@pytest.fixture
def world(conn):
    """An SPL run, one wording, a class, and two subject moieties."""
    r = conn.execute(
        "INSERT INTO drugref.ingest_run (source, upstream_release, source_checksum, writer) "
        "VALUES (%s, '2026-08-22', 'deadbeef', %s) RETURNING ingest_run_id",
        (spl.SOURCE, spl.WRITER)).fetchone()[0]
    spl_evidence.write_wordings(
        conn, [spl_evidence.WordingRow(text_key=KEY, char_length=1_000, label_count=2)],
        ingest_run_id=r, source=spl.SOURCE)
    k = w.klass(conn, r, "Statins")
    return r, k, w.moiety(conn, r, "drugA"), w.moiety(conn, r, "drugB")


def _occ(conn, r, k, *, start=10, end=20, level="avoid", rule="avoid.x", mixed=False,
         scope="sentence", s0=0, s1=100, form="stored", text_key=KEY, ambiguous=False):
    conn.execute(
        "INSERT INTO drugref.spl_class_occurrence (ingest_run, source, text_key, "
        "char_start, char_end, class_uuid, match_ambiguous, match_form, advice_level, "
        "advice_mixed, advice_rule, advice_scope, scope_start, scope_end) "
        "VALUES (%s,'SPL',%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
        (r, text_key, start, end, k, ambiguous, form, level, mixed, rule, scope, s0, s1))


def _label(conn, r, m, set_id, text_key=KEY):
    spl_evidence.write_labels(
        conn, [spl_evidence.LabelRow(set_id=set_id, version="1", effective_time="20260101",
                                     product_type=None, text_key=text_key)],
        ingest_run_id=r, source=spl.SOURCE)
    conn.execute(
        "INSERT INTO drugref.spl_label_subject (ingest_run, source, set_id, version, "
        "subject_ordinal, moiety_uuid, route) VALUES (%s,'SPL',%s,'1',0,%s,'openfda_unii')",
        (r, set_id, m))


def _check_text(conn, name):
    return conn.execute("SELECT pg_get_constraintdef(oid) FROM pg_constraint "
                        "WHERE conname = %s", (name,)).fetchone()[0]


def test_advice_level_table_matches_python(conn):
    rows = conn.execute("SELECT level, strength_rank FROM drugref.spl_advice_level "
                        "ORDER BY level").fetchall()
    want = {lv: (spl_advice.STRENGTH.index(lv) + 1 if lv in spl_advice.STRENGTH else None)
            for lv in spl_advice.LEVELS}
    assert dict(rows) == {lv: rk for lv, rk in want.items()}
    assert len(rows) == len(spl_advice.LEVELS)


def test_match_form_check_matches_python(conn):
    d = _check_text(conn, "spl_class_occurrence_match_form")
    for f in spl_match.MATCH_FORMS:
        assert f"'{f}'" in d
    assert d.count("'") == 2 * len(spl_match.MATCH_FORMS)


def test_scope_check_matches_python(conn):
    d = _check_text(conn, "spl_class_occurrence_scope")
    for s in spl_advice.SCOPES:
        assert f"'{s}'" in d
    assert d.count("'") == 2 * len(spl_advice.SCOPES)


def test_not_read_requires_null_rule(conn, world):
    r, k, *_ = world
    with pytest.raises(psycopg.errors.CheckViolation):
        _occ(conn, r, k, level="not_read", rule="avoid.x")


def test_a_rule_is_required_for_a_read_level(conn, world):
    r, k, *_ = world
    with pytest.raises(psycopg.errors.CheckViolation):
        _occ(conn, r, k, level="avoid", rule=None)


def test_scope_must_contain_span(conn, world):
    r, k, *_ = world
    for s0, s1 in ((12, 100), (0, 15)):  # starts after the span / ends before it
        with pytest.raises(psycopg.errors.CheckViolation), conn.transaction():
            _occ(conn, r, k, start=10, end=20, s0=s0, s1=s1)


def test_mixed_refused_without_strength(conn, world):
    r, k, *_ = world
    with pytest.raises(psycopg.errors.CheckViolation):
        _occ(conn, r, k, level="no_meaningful_interaction", mixed=True)


def test_gap_kind_admitted(conn, world):
    r, *_ = world
    conn.execute(
        "INSERT INTO drugref.open_question (question_uuid, gap_kind, gap_key, "
        "question_text, first_derived_ingest, last_derived_ingest) "
        "VALUES (%s, 'unconfirmed_label_class_statement', 'SPLCLASS:x:y', 't', %s, %s)",
        (uuid.uuid4(), r, r))


def test_spl_entity_occurrence_comment_names_db066(conn):
    (c,) = conn.execute("SELECT obj_description("
                        "'drugref.spl_entity_occurrence'::regclass)").fetchone()
    assert "spl_class_occurrence" in c and "db/066" in c


def test_statement_views_group_by_subject_and_class(conn, world):
    r, k, a, b = world
    w.member(conn, r, a, k)
    _occ(conn, r, k, level="monitor", rule="monitor.monitor")
    _occ(conn, r, k, start=30, end=40, level="contraindicated",
         rule="contraindicated.x", mixed=True)
    _label(conn, r, a, "S1")
    _label(conn, r, a, "S2")
    ev = conn.execute("SELECT count(*), bool_and(subject_in_class) "
                      "FROM drugref.spl_class_statement_evidence").fetchone()
    assert ev == (4, True)
    (row,) = conn.execute(
        "SELECT subject_moiety, label_count, wording_count, strongest_level, "
        "level_counts, any_mixed, any_ambiguous, subject_in_class "
        "FROM drugref.spl_class_statement").fetchall()
    assert row == (a, 2, 1, "contraindicated", {"contraindicated": 2, "monitor": 2},
                   True, False, True)


def test_nmi_beats_not_read_but_loses_to_strength(conn, world):
    r, k, a, b = world
    _occ(conn, r, k, level="not_read", rule=None)
    _occ(conn, r, k, start=30, end=40, level="no_meaningful_interaction", rule="n.x")
    _label(conn, r, b, "S1")
    (lvl,) = conn.execute("SELECT strongest_level FROM drugref.spl_class_statement"
                          ).fetchone()
    assert lvl == "no_meaningful_interaction"


def test_worklist_lists_strength_rows_until_a_live_ruling_exists(conn, world):
    r, k, a, b = world
    _occ(conn, r, k)
    _label(conn, r, a, "S1")
    q = "SELECT moiety_uuid, class_uuid, strongest_level, label_count " \
        "FROM drugref.gap_unconfirmed_label_class_statement"
    assert conn.execute(q).fetchall() == [(a, k, "avoid", 1)]
    curation_property.record_property_ruling(
        conn, subject_moiety_uuid=a, relationship="interacts_with", object_kind="class",
        object_uuid=k, verdict="holds", reviewed_by="t", reviewed_against="t")
    assert conn.execute(q).fetchall() == []


def test_worklist_skips_nmi_and_not_read(conn, world):
    r, k, a, b = world
    _occ(conn, r, k, level="not_read", rule=None)
    _label(conn, r, a, "S1")
    assert conn.execute(
        "SELECT count(*) FROM drugref.gap_unconfirmed_label_class_statement"
    ).fetchone() == (0,)
