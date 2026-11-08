"""The miscited-question census (#210): curated rows written before #207 may cite a
question about something else, and nothing refused them at the time.

DB-gated. "Written before #207" is simulated by switching the guard off for the write
(monkeypatching questions.require_question_for to a no-op), which is exactly the
state the writers were in before PR #208 added it.
"""
import contextlib

import pytest

from drugref import citation_audit, curation, curation_property, ids, questions
from tests import class_profile_world as w
from tools import citation_census


def _question(conn, run, kind, key):
    """Register one question exactly as register_from_gaps would mint it."""
    q = ids.mint_question_uuid(kind, key)
    conn.execute(
        "INSERT INTO drugref.open_question (question_uuid, gap_kind, gap_key, "
        "question_text, first_derived_ingest, last_derived_ingest) "
        "VALUES (%s, %s, %s, 'q', %s, %s)", (q, kind, key, run, run))
    return q


@pytest.fixture
def world(conn, monkeypatch):
    """A tiny registry, an unrelated question to miscite, and the guard switched off."""
    r = w.run(conn)
    a, b = w.moiety(conn, r, "drugA"), w.moiety(conn, r, "drugB")
    k1, k2 = w.klass(conn, r, "ClassA"), w.klass(conn, r, "ClassB")
    cond = w.condition(conn, r, "Epilepsy")
    unrelated = _question(conn, r, "unclassified_moiety", f"MOIETY:{b}")
    monkeypatch.setattr(questions, "require_question_for", lambda *a, **k: None)
    return conn, r, a, b, k1, k2, cond, unrelated


def _who(rows):
    return sorted(row["table"] for row in rows)


def test_rows_citing_a_question_about_something_else_are_reported(world):
    """One miscited row in each of the four curated tables that carry a question_uuid."""
    conn, _r, a, b, k1, k2, cond, unrelated = world
    curation.record_interaction_judgement(
        conn, a, k1, "CI_MoA", False, question_uuid=unrelated, reviewed_by="t",
        reviewed_against="t")
    curation.record_condition_ruling(
        conn, a, cond, "spurious", question_uuid=unrelated, reviewed_by="t",
        reviewed_against="t")
    curation.record_class_interaction_judgement(
        conn, k1, k2, "CI_MoA", False, question_uuid=unrelated, reviewed_by="t",
        reviewed_against="t")
    curation_property.record_property_ruling(
        conn, subject_moiety_uuid=a, relationship="CI_with", object_kind="condition",
        object_uuid=cond, verdict="holds", question_uuid=unrelated, reviewed_by="t",
        reviewed_against="t")

    found = citation_audit.miscited(conn)
    assert _who(found) == ["curated_class_interaction", "curated_condition",
                           "curated_interaction", "curated_property_ruling"]
    # Each report names the question it cites, so a curator can see what it is about.
    assert {(f["question_uuid"], f["cited_gap_kind"], f["cited_gap_key"], f["live"])
            for f in found} == {(unrelated, "unclassified_moiety", f"MOIETY:{b}", True)}


def test_rows_citing_their_own_question_or_none_are_not_reported(world):
    conn, r, a, b, k1, k2, cond, _unrelated = world
    # The writer stores a drug pair smaller-uuid first, so asking about the LARGER
    # drug makes the stored subject the partner: the census must accept the reverse.
    lo, hi = sorted((a, b))
    own = _question(conn, r, "unconfirmed_class_property",
                    f"CLASSPROP:{hi}:interacts_with:moiety:{lo}")
    curation_property.record_property_ruling(
        conn, subject_moiety_uuid=hi, relationship="interacts_with", object_kind="moiety",
        object_uuid=lo, verdict="holds", question_uuid=own, reviewed_by="t",
        reviewed_against="t")
    curation.record_condition_ruling(
        conn, a, cond, "spurious", reviewed_by="t", reviewed_against="t")
    assert citation_audit.miscited(conn) == []


def test_each_curation_table_citing_its_own_question_is_not_reported(world):
    """Pins each table's key-column order in citation_audit._TABLES: a mis-ordered
    mapping would report these correctly cited rows. Keys are spelled out by hand on
    purpose, to pin the frozen key_sql formats from the outside."""
    conn, r, a, _b, k1, k2, cond, _unrelated = world
    curation.record_interaction_judgement(
        conn, a, k1, "CI_MoA", False, reviewed_by="t", reviewed_against="t",
        question_uuid=_question(conn, r, "uncurated_interaction_rule",
                                f"MOIETY:{a}/CLASS:{k1}/CI_AXIS:CI_MoA"))
    curation.record_condition_ruling(
        conn, a, cond, "spurious", reviewed_by="t", reviewed_against="t",
        question_uuid=_question(conn, r, "uncurated_condition_contradiction",
                                f"MOIETY:{a}/CONDITION:{cond}"))
    curation.record_class_interaction_judgement(
        conn, k1, k2, "CI_MoA", False, reviewed_by="t", reviewed_against="t",
        question_uuid=_question(conn, r, "uncurated_class_interaction_rule",
                                f"CLASS:{k1}/CLASS:{k2}/CI_AXIS:CI_MoA"))
    assert citation_audit.miscited(conn) == []


def test_a_superseded_row_is_still_reported(world):
    """Superseded rows keep their question alive too (the retention guard reads every
    row, not only the live one), so the census must not stop at the live row."""
    conn, _r, a, _b, _k1, _k2, cond, unrelated = world
    curation.record_condition_ruling(
        conn, a, cond, "spurious", question_uuid=unrelated, reviewed_by="t",
        reviewed_against="t")
    curation.record_condition_ruling(
        conn, a, cond, "spurious", reviewed_by="t2", reviewed_against="t")
    (found,) = citation_audit.miscited(conn)
    assert (found["table"], found["live"]) == ("curated_condition", False)


def test_a_class_subject_property_ruling_citing_anything_is_reported(world):
    """Gap 19 asks about a MEMBER, never a class: a class ruling can cite nothing."""
    conn, r, a, _b, k1, _k2, cond, _unrelated = world
    q = _question(conn, r, "unconfirmed_class_property",
                  f"CLASSPROP:{a}:CI_with:condition:{cond}")
    curation_property.record_property_ruling(
        conn, subject_class_uuid=k1, relationship="CI_with", object_kind="condition",
        object_uuid=cond, verdict="holds", question_uuid=q, reviewed_by="t",
        reviewed_against="t")
    assert _who(citation_audit.miscited(conn)) == ["curated_property_ruling"]


def test_census_tool_exits_1_only_on_a_live_finding(world, monkeypatch, capsys):
    """A superseded miscite can never be cleared (append-only, and it still pins its
    question), so it is printed but does not fail the run; a live one does."""
    conn, _r, a, _b, _k1, _k2, cond, unrelated = world
    monkeypatch.setattr(citation_census.db, "connect",
                        lambda dsn: contextlib.nullcontext(conn))
    assert citation_census.main(["--dsn", "x"]) == 0

    curation.record_condition_ruling(
        conn, a, cond, "spurious", question_uuid=unrelated, reviewed_by="t",
        reviewed_against="t")
    assert citation_census.main(["--dsn", "x"]) == 1
    assert "curated_condition #" in capsys.readouterr().out

    curation.record_condition_ruling(
        conn, a, cond, "spurious", reviewed_by="t2", reviewed_against="t")
    assert citation_census.main(["--dsn", "x"]) == 0
    out = capsys.readouterr().out
    assert "(superseded)" in out and "1 miscited row(s), 0 live" in out


def test_census_tool_help_has_a_description(capsys):
    with pytest.raises(SystemExit):
        citation_census.main(["--help"])
    assert "Report curated rows that cite a question" in capsys.readouterr().out
