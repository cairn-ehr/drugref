"""Gap kind unconfirmed_label_class_statement (Task 8). DB-gated.

A machine-read label statement about a (drug, class) pair becomes a curator question;
only a curator's `holds` ruling promotes it (to class_asserted_property).
"""
import pytest

from drugref import spl_evidence
from drugref import citation_audit, curation_property, ids, questions
from tests.test_spl_class_schema import KEY, _label, _occ, world  # noqa: F401  (fixture)

KIND = "unconfirmed_label_class_statement"


def _registered(conn, r, a, k):
    """Run the registry for real and return the pair's question row (or None)."""
    questions.register_from_gaps(conn, r)
    return conn.execute(
        "SELECT question_uuid, question_text, gap_key FROM drugref.open_question "
        "WHERE gap_kind = %s AND gap_key = %s", (KIND, f"SPLCLASS:{a}:{k}")).fetchone()


def _rule(conn, a, k, verdict="holds", question_uuid=None):
    return curation_property.record_property_ruling(
        conn, subject_moiety_uuid=a, relationship="interacts_with", object_kind="class",
        object_uuid=k, verdict=verdict, reviewed_by="t", reviewed_against="t",
        question_uuid=question_uuid)


def test_label_class_question_registered_after_register_from_gaps(conn, world):  # noqa: F811
    r, k, a, _b = world
    _occ(conn, r, k)
    _label(conn, r, a, "S1")
    q, text, _key = _registered(conn, r, a, k)
    assert q == ids.mint_question_uuid(KIND, f"SPLCLASS:{a}:{k}")
    assert "1 of 1 label(s)" in text and "avoid" in text
    assert "machine-read, not confirmed" in text
    assert text.endswith("Answering does not change prescribing alerts.")
    assert "safe" not in text.lower()


def test_question_text_counts_labels_at_the_strongest_level(conn, world):  # noqa: F811
    """3 labels, only 1 saying the strongest level: the text must say '1 of 3'."""
    r, k, a, _b = world
    k2, k3 = "b" * 64, "c" * 64
    spl_evidence.write_wordings(
        conn, [spl_evidence.WordingRow(text_key=t, char_length=1_000, label_count=1)
               for t in (k2, k3)], ingest_run_id=r, source="SPL")
    _occ(conn, r, k, level="avoid")
    _occ(conn, r, k, level="not_read", rule=None, text_key=k2)
    _occ(conn, r, k, level="monitor", rule="monitor.x", text_key=k3)
    for sid, key in (("S1", KEY), ("S2", k2), ("S3", k3)):
        _label(conn, r, a, sid, text_key=key)
    text = _registered(conn, r, a, k)[1]
    assert "1 of 3 label(s)" in text and "say avoid" in text


def test_ruling_may_cite_label_class_question(conn, world):  # noqa: F811
    r, k, a, _b = world
    _occ(conn, r, k)
    _label(conn, r, a, "S1")
    q = _registered(conn, r, a, k)[0]
    assert _rule(conn, a, k, question_uuid=q)[1] is True


def test_ruling_refuses_label_question_for_other_class(conn, world):  # noqa: F811
    from tests import class_profile_world as w
    r, k, a, _b = world
    other = w.klass(conn, r, "Fibrates")
    _occ(conn, r, k)
    _label(conn, r, a, "S1")
    q = _registered(conn, r, a, k)[0]
    with pytest.raises(ValueError, match="does not ask about this row"):
        _rule(conn, a, other, question_uuid=q)


def test_holds_ruling_promotes_and_leaves_worklist(conn, world):  # noqa: F811
    r, k, a, b = world
    _occ(conn, r, k)
    _label(conn, r, a, "S1")
    gap = "SELECT count(*) FROM drugref.gap_unconfirmed_label_class_statement"
    # A drug -> class rule is asserted on the OBJECT class: (class, interacts_with, drug).
    prop = ("SELECT count(*) FROM drugref.class_asserted_property WHERE class_uuid = %s "
            "AND relationship = 'interacts_with' AND object_kind = 'moiety' "
            "AND object_uuid = %s")
    # Machine-read alone promotes nothing.
    assert conn.execute(gap).fetchone() == (1,)
    assert conn.execute(prop, (k, a)).fetchone() == (0,)
    _rule(conn, a, k, "holds")
    assert conn.execute(gap).fetchone() == (0,)
    assert conn.execute(prop, (k, a)).fetchone() == (1,)


def test_citation_census_accepts_label_question(conn, world):  # noqa: F811
    r, k, a, _b = world
    _occ(conn, r, k)
    _label(conn, r, a, "S1")
    _rule(conn, a, k, question_uuid=_registered(conn, r, a, k)[0])
    assert citation_audit.miscited(conn) == []


def test_worklist_orders_label_statements_strongest_level_first(conn, world):  # noqa: F811
    """#217 (design §6.4): a curator meets contraindicated before monitor.

    question_uuid is a hash, so without priority_rank the two pairs come back in an
    order unrelated to strength. The query restates the view's full ORDER BY (db/008:
    it is not guaranteed through a wrapping query); the viewdef check below pins that
    the view itself still sorts by the rank, after the tier and the kind.
    """
    from tests import class_profile_world as w
    r, k, a, b = world
    k2 = "b" * 64
    spl_evidence.write_wordings(
        conn, [spl_evidence.WordingRow(text_key=k2, char_length=1_000, label_count=1)],
        ingest_run_id=r, source="SPL")
    weak = w.klass(conn, r, "Fibrates")
    _occ(conn, r, k, level="contraindicated", rule="contraindicated.x")
    _occ(conn, r, weak, level="monitor", rule="monitor.x", text_key=k2)
    _label(conn, r, a, "S1")
    _label(conn, r, b, "S2", text_key=k2)
    questions.register_from_gaps(conn, r)
    rows = conn.execute(
        "SELECT gap_key, priority_rank FROM drugref.question_worklist WHERE gap_kind = %s "
        "ORDER BY cheapest_unchecked_rank NULLS LAST, gap_kind, priority_rank NULLS LAST, "
        "question_uuid", (KIND,)).fetchall()
    assert rows == [(f"SPLCLASS:{a}:{k}", 1), (f"SPLCLASS:{b}:{weak}", 4)]
    (viewdef,) = conn.execute(
        "SELECT pg_get_viewdef('drugref.question_worklist'::regclass)").fetchone()
    # Postgres renders the view's ORDER BY without NULLS LAST (the ASC default).
    assert viewdef.rstrip().endswith("q.gap_kind, q.priority_rank, q.question_uuid;")
    # A kind without rank_sql stores NULL, never a rank borrowed from another kind.
    ranked, unranked_kind_rows = conn.execute(
        "SELECT count(*) FILTER (WHERE priority_rank IS NOT NULL), count(*) "
        "FROM drugref.open_question WHERE gap_kind <> %s", (KIND,)).fetchone()
    assert ranked == 0 and unranked_kind_rows > 0


def test_priority_rank_follows_the_reading_on_re_derivation(conn, world):  # noqa: F811
    """The rank is derived like the text: a re-run refreshes it, never keeps a stale one."""
    r, k, a, _b = world
    _occ(conn, r, k, level="monitor", rule="monitor.x")
    _label(conn, r, a, "S1")
    rank = ("SELECT priority_rank FROM drugref.open_question WHERE gap_kind = %s "
            "AND gap_key = %s")
    key = f"SPLCLASS:{a}:{k}"
    questions.register_from_gaps(conn, r)
    assert conn.execute(rank, (KIND, key)).fetchone() == (4,)
    conn.execute("UPDATE drugref.spl_class_occurrence SET advice_level = 'avoid', "
                 "advice_rule = 'avoid.x' WHERE class_uuid = %s", (k,))
    questions.register_from_gaps(conn, r)
    assert conn.execute(rank, (KIND, key)).fetchone() == (2,)
