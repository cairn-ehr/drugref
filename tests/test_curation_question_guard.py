"""A curated row may only cite the question minted from its OWN key (#207).

DB-gated: the expected gap_key is computed by the gap kind's own frozen `key_sql`
(questions._GAP_SOURCES), so these tests run it the way register_from_gaps does.

Why it matters: a wrong question_uuid ties the row to an unrelated question, and the
retention guard in register_from_gaps then keeps that unrelated question alive for as
long as the row exists (the FK cascades into an append-only table).
"""
import uuid

import pytest

from drugref import curation, curation_property, ids
from tests import class_profile_world as w


def _question(conn, kind, key) -> uuid.UUID:
    """Register one question exactly as register_from_gaps would mint it."""
    q = ids.mint_question_uuid(kind, key)
    r = w.run(conn)
    conn.execute(
        "INSERT INTO drugref.open_question (question_uuid, gap_kind, gap_key, "
        "question_text, first_derived_ingest, last_derived_ingest) "
        "VALUES (%s, %s, %s, 'q', %s, %s)", (q, kind, key, r, r))
    return q


@pytest.fixture
def world(conn):
    r = w.run(conn)
    return (conn, w.moiety(conn, r, "drugA"), w.moiety(conn, r, "drugB"),
            w.condition(conn, r, "Epilepsy"), w.klass(conn, r, "ClassA"))


def _rule(conn, **kw):
    return curation_property.record_property_ruling(
        conn, verdict="holds", reviewed_by="Dr X", reviewed_against="test", **kw)


def _rulings(conn):
    return conn.execute(
        "SELECT count(*) FROM drugref.curated_property_ruling").fetchone()[0]


def test_a_property_ruling_citing_its_own_question_is_accepted(world):
    conn, a, _b, cond, _k = world
    q = _question(conn, "unconfirmed_class_property",
                  f"CLASSPROP:{a}:CI_with:condition:{cond}")
    _rule(conn, subject_moiety_uuid=a, relationship="CI_with",
          object_kind="condition", object_uuid=cond, question_uuid=q)
    assert conn.execute("SELECT question_uuid FROM drugref.curated_property_ruling "
                        "WHERE subject_uuid = %s", (a,)).fetchall() == [(q,)]


def test_a_property_ruling_citing_another_question_is_refused(world):
    conn, a, b, cond, _k = world
    before = _rulings(conn)
    # The right kind, but another drug's question.
    q = _question(conn, "unconfirmed_class_property",
                  f"CLASSPROP:{b}:CI_with:condition:{cond}")
    with pytest.raises(ValueError, match="does not ask about"):
        _rule(conn, subject_moiety_uuid=a, relationship="CI_with",
              object_kind="condition", object_uuid=cond, question_uuid=q)
    # And a question of another kind entirely.
    other = _question(conn, "unclassified_moiety", f"MOIETY:{a}")
    with pytest.raises(ValueError, match="does not ask about"):
        _rule(conn, subject_moiety_uuid=a, relationship="CI_with",
              object_kind="condition", object_uuid=cond, question_uuid=other)
    assert _rulings(conn) == before


@pytest.mark.parametrize("asked_of_first", [True, False])
@pytest.mark.parametrize("recorded_from_first", [True, False])
def test_a_drug_pair_ruling_accepts_the_question_from_either_side(
        world, asked_of_first, recorded_from_first):
    """An interaction is symmetric, and the worklist may ask it of either drug."""
    conn, a, b, _c, _k = world
    asked, partner = (a, b) if asked_of_first else (b, a)
    q = _question(conn, "unconfirmed_class_property",
                  f"CLASSPROP:{asked}:interacts_with:moiety:{partner}")
    subject, obj = (a, b) if recorded_from_first else (b, a)
    _rule(conn, subject_moiety_uuid=subject, relationship="interacts_with",
          object_kind="moiety", object_uuid=obj, question_uuid=q)


def test_a_class_subject_ruling_answers_no_worklist_question(world):
    """Gap 19 asks about one MEMBER; no question is minted from a class's key."""
    conn, a, _b, cond, k = world
    q = _question(conn, "unconfirmed_class_property",
                  f"CLASSPROP:{a}:CI_with:condition:{cond}")
    with pytest.raises(ValueError, match="answers no worklist question"):
        _rule(conn, subject_class_uuid=k, relationship="CI_with",
              object_kind="condition", object_uuid=cond, question_uuid=q)


def test_a_question_uuid_given_as_text_is_judged_by_value(world):
    """A str uuid (any case) is the same question; it must not read as 'about
    something else'."""
    conn, a, _b, cond, _k = world
    q = _question(conn, "unconfirmed_class_property",
                  f"CLASSPROP:{a}:CI_with:condition:{cond}")
    _rule(conn, subject_moiety_uuid=a, relationship="CI_with",
          object_kind="condition", object_uuid=cond, question_uuid=str(q).upper())


def test_a_real_gap_19_question_is_citable_from_the_partners_side(conn):
    """End to end: register_from_gaps mints the drug-pair question for the member
    that is not confirmed; the ruling recorded from the PARTNER's side may cite it."""
    from drugref import questions
    r = w.run(conn)
    c = w.klass(conn, r, "CPair")
    x, *others = (w.moiety(conn, r, n) for n in ("px", "py", "pz", "pv", "pu"))
    for m in (x, *others):
        w.member(conn, r, m, c)
    for m in others[:3]:
        w.moiety_ci(conn, r, m, x)
    questions.register_from_gaps(conn, r)
    (q,) = conn.execute(
        "SELECT question_uuid FROM drugref.open_question "
        "WHERE gap_kind = 'unconfirmed_class_property' AND gap_key LIKE %s",
        (f"CLASSPROP:{others[3]}:%",)).fetchone()
    _rule(conn, subject_moiety_uuid=x, relationship="interacts_with",
          object_kind="moiety", object_uuid=others[3], question_uuid=q)


@pytest.mark.parametrize("write", [
    lambda conn, q: curation.record_interaction_judgement(
        conn, uuid.uuid4(), uuid.uuid4(), "CI_MoA", False, question_uuid=q,
        reviewed_by="t", reviewed_against="t"),
    lambda conn, q: curation.record_condition_ruling(
        conn, uuid.uuid4(), uuid.uuid4(), "spurious", question_uuid=q,
        reviewed_by="t", reviewed_against="t"),
    lambda conn, q: curation.record_class_interaction_judgement(
        conn, uuid.uuid4(), uuid.uuid4(), "CI_PE", False, question_uuid=q,
        reviewed_by="t", reviewed_against="t"),
], ids=["interaction", "condition", "class_interaction"])
def test_every_curation_py_writer_refuses_a_question_about_something_else(world, write):
    """Refused before the INSERT: the subjects here do not even exist. (The fourth
    writer, record_property_ruling, is test_a_property_ruling_citing_another_...)"""
    conn, a, _b, _c, _k = world
    q = _question(conn, "unclassified_moiety", f"MOIETY:{a}")
    with pytest.raises(ValueError, match="does not ask about"):
        write(conn, q)


def test_the_class_interaction_writer_accepts_its_own_question(conn):
    r = w.run(conn)
    s, o = w.klass(conn, r, "SubjectC"), w.klass(conn, r, "ObjectC")
    q = _question(conn, "uncurated_class_interaction_rule",
                  f"CLASS:{s}/CLASS:{o}/CI_AXIS:CI_PE")
    curation.record_class_interaction_judgement(
        conn, s, o, "CI_PE", False, question_uuid=q, reviewed_by="t",
        reviewed_against="t")
