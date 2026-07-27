# tests/test_questions.py
"""Deriving the register from the gap views, and the curator API over it.

The load-bearing test in this file is test_curator_state_survives_a_rebuild. Every
other property here would hold just as well under the design that put `state` on
open_question -- on a FRESH database. That design fails only on the second ingest of
a long-lived one, which is exactly the shape of bug that reaches production.
"""
import uuid

import pytest

from drugref import ids, questions


@pytest.fixture(autouse=True)
def _isolate(conn):
    """The gap views read the WHOLE registry, so any moiety or class another module
    committed shows up as a gap here and makes these counts non-deterministic. The
    orchestrator tests (test_medrt_run, test_ingest_run) commit internally, so the
    conn fixture's rollback cannot isolate against them -- truncate first, exactly as
    those modules do for the same reason."""
    conn.execute("TRUNCATE drugref.class_contraindication, drugref.class_membership, "
                 "drugref.class_parent, drugref.substance_class, drugref.identity_claim, "
                 "drugref.substance_moiety, drugref.ingest_run RESTART IDENTITY CASCADE")
    conn.commit()
    yield


def _run(conn, source="MED-RT"):
    return conn.execute(
        "INSERT INTO drugref.ingest_run (source, upstream_release, source_checksum) "
        "VALUES (%s, 'test', 'deadbeef') RETURNING ingest_run_id", (source,)).fetchone()[0]


def _moiety(conn, run_id, name="orphanium"):
    m = uuid.uuid4()
    conn.execute("INSERT INTO drugref.substance_moiety "
                 "(moiety_uuid, display_name, first_seen_ingest) VALUES (%s, %s, %s)",
                 (m, name, run_id))
    return m


def _empty_ci_class(conn, run_id, code="N0000000001"):
    cu = ids.mint_class_uuid("MED-RT", code)
    conn.execute(
        "INSERT INTO drugref.substance_class (class_uuid, source, source_code, "
        "published_code, class_name, concept_type, first_seen_ingest) "
        "VALUES (%s, 'MED-RT', %s, %s, 'Renal Arterial Vasoconstriction [PE]', 'PE', %s)",
        (cu, code, code, run_id))
    conn.execute(
        "INSERT INTO drugref.class_contraindication (subject_moiety_uuid, "
        "object_class_uuid, relationship, source, ingest_run) "
        "VALUES (%s, %s, 'CI_PE', 'MED-RT', %s)", (_moiety(conn, run_id, "subj"), cu, run_id))
    return cu


# ---- derivation --------------------------------------------------------------


def test_an_unclassified_moiety_becomes_a_question(conn):
    run_id = _run(conn)
    m = _moiety(conn, run_id)
    questions.register_from_gaps(conn, run_id)

    row = conn.execute(
        "SELECT question_uuid, gap_kind, gap_key FROM drugref.open_question").fetchone()
    assert row == (ids.mint_question_uuid("unclassified_moiety", f"MOIETY:{m}"),
                   "unclassified_moiety", f"MOIETY:{m}")


def test_an_unpopulated_contraindication_becomes_a_question(conn):
    run_id = _run(conn)
    cu = _empty_ci_class(conn, run_id)
    questions.register_from_gaps(conn, run_id)

    row = conn.execute(
        "SELECT gap_key, question_text FROM drugref.open_question "
        "WHERE gap_kind = 'unpopulated_contraindication'").fetchone()
    assert row[0] == f"CLASS:{cu}"
    # The text is what a literature search or a label probe is run against, so the
    # class must be named in it rather than referenced only by UUID.
    assert "Renal Arterial Vasoconstriction [PE]" in row[1]


def test_registration_is_idempotent(conn):
    """The derived half is rebuilt every ingest; running it twice must not duplicate,
    or the register would grow without bound while describing the same gaps."""
    run_id = _run(conn)
    _moiety(conn, run_id)
    questions.register_from_gaps(conn, run_id)
    questions.register_from_gaps(conn, run_id)
    assert conn.execute("SELECT count(*) FROM drugref.open_question").fetchone()[0] == 1


def test_a_rebuild_refreshes_the_last_seen_watermark(conn):
    """first_derived_ingest is write-once provenance; last_derived_ingest answers
    'is this gap still open' without anyone writing a state row."""
    run_id = _run(conn)
    _moiety(conn, run_id)
    questions.register_from_gaps(conn, run_id)
    later = _run(conn)
    questions.register_from_gaps(conn, later)

    first, last = conn.execute("SELECT first_derived_ingest, last_derived_ingest "
                               "FROM drugref.open_question").fetchone()
    assert (first, last) == (run_id, later)


def _classify(conn, run_id, moiety, code="N0000000009"):
    """Give `moiety` a has_PE membership, which closes its unclassified_moiety gap."""
    cu = ids.mint_class_uuid("MED-RT", code)
    conn.execute("INSERT INTO drugref.substance_class (class_uuid, source, source_code, "
                 "published_code, class_name, concept_type, first_seen_ingest) "
                 "VALUES (%s, 'MED-RT', %s, %s, 'C [PE]', 'PE', %s)",
                 (cu, code, code, run_id))
    conn.execute("INSERT INTO drugref.class_membership "
                 "(moiety_uuid, class_uuid, relationship, ingest_run) "
                 "VALUES (%s, %s, 'has_PE', %s)", (moiety, cu, run_id))
    return cu


def test_a_closed_gap_leaves_the_register(conn):
    """A gap that closes must be able to leave -- the projection tracks reality, and
    a register that only ever grows is the stale document these views replace."""
    run_id = _run(conn)
    m = _moiety(conn, run_id)
    questions.register_from_gaps(conn, run_id)

    _classify(conn, run_id, m)
    questions.register_from_gaps(conn, run_id)

    assert conn.execute("SELECT count(*) FROM drugref.open_question").fetchone()[0] == 0


def test_a_closed_gap_carrying_evidence_is_RETAINED_not_deleted(conn):
    """The cascade cuts both ways. Every curated table is ON DELETE CASCADE from
    open_question, so deleting a closed question destroys append-only rows whose own
    contract promises "the record of what was believed before must survive the
    revision". A question anyone has contributed to is kept, marked not-current."""
    run_id = _run(conn)
    m = _moiety(conn, run_id)
    questions.register_from_gaps(conn, run_id)
    qu = conn.execute("SELECT question_uuid FROM drugref.open_question").fetchone()[0]
    questions.add_evidence(conn, qu, "DOI", "10.1000/x", "supports", run_id)

    _classify(conn, run_id, m)                       # the gap closes
    questions.register_from_gaps(conn, run_id)

    assert conn.execute(
        "SELECT is_current FROM drugref.open_question "
        "WHERE question_uuid = %s", (qu,)).fetchall() == [(False,)]
    # the finding survived, which is the whole point
    assert conn.execute("SELECT count(*) FROM drugref.question_evidence "
                        "WHERE question_uuid = %s", (qu,)).fetchone()[0] == 1


def test_a_retained_question_is_off_the_worklist(conn):
    """Retention must cost no noise, or the fix above would trade a data-loss bug for
    a worklist full of questions nobody can act on."""
    run_id = _run(conn)
    m = _moiety(conn, run_id)
    questions.register_from_gaps(conn, run_id)
    qu = conn.execute("SELECT question_uuid FROM drugref.open_question").fetchone()[0]
    questions.record_source_check(conn, qu, "MED-RT", "2026.07.06", "not_covered")

    _classify(conn, run_id, m)
    questions.register_from_gaps(conn, run_id)

    assert conn.execute("SELECT count(*) FROM drugref.question_worklist").fetchone()[0] == 0


def test_a_reopened_gap_becomes_current_again_under_the_same_uuid(conn):
    """Immortal identity is what makes retention safe: the external tool holding this
    UUID sees the same question come back, not a new one."""
    run_id = _run(conn)
    m = _moiety(conn, run_id)
    questions.register_from_gaps(conn, run_id)
    qu = conn.execute("SELECT question_uuid FROM drugref.open_question").fetchone()[0]
    questions.add_evidence(conn, qu, "DOI", "10.1000/x", "inconclusive", run_id)

    cu = _classify(conn, run_id, m)
    questions.register_from_gaps(conn, run_id)
    conn.execute("DELETE FROM drugref.class_membership WHERE class_uuid = %s", (cu,))
    questions.register_from_gaps(conn, run_id)       # the gap reopens

    assert conn.execute(
        "SELECT question_uuid, is_current FROM drugref.open_question").fetchall() \
        == [(qu, True)]


# ---- the curated half, and the property that motivated it --------------------


def test_curator_state_survives_a_rebuild(conn):
    """THE test. `state` lived on open_question in the first design -- the same table
    the rebuild re-derives -- so every ingest silently erased every `withdrawn` and
    the suppressed question came straight back. Keyed off the immortal question_uuid
    in its own append-only table, it survives."""
    run_id = _run(conn)
    _moiety(conn, run_id)
    questions.register_from_gaps(conn, run_id)
    qu = conn.execute("SELECT question_uuid FROM drugref.open_question").fetchone()[0]
    questions.set_state(conn, qu, "withdrawn", "duplicate", run_id)

    questions.register_from_gaps(conn, _run(conn))          # the next ingest

    assert questions.current_state(conn, qu) == "withdrawn"
    assert qu not in [r[0] for r in
                      conn.execute("SELECT question_uuid FROM drugref.question_worklist")]


def test_a_question_with_no_state_row_is_open(conn):
    """Absence means open, so thousands of questions register without writing any
    state at all -- which is what makes auto-registration affordable."""
    run_id = _run(conn)
    _moiety(conn, run_id)
    questions.register_from_gaps(conn, run_id)
    qu = conn.execute("SELECT question_uuid FROM drugref.open_question").fetchone()[0]
    assert questions.current_state(conn, qu) == "open"
    assert conn.execute("SELECT count(*) FROM drugref.question_state").fetchone()[0] == 0


def test_changing_state_supersedes_rather_than_overwrites(conn):
    run_id = _run(conn)
    _moiety(conn, run_id)
    questions.register_from_gaps(conn, run_id)
    qu = conn.execute("SELECT question_uuid FROM drugref.open_question").fetchone()[0]

    questions.set_state(conn, qu, "evidence_under_review", "probing openFDA", run_id)
    questions.set_state(conn, qu, "answered", "label names the class", run_id)

    assert questions.current_state(conn, qu) == "answered"
    # both are still on record: what was believed, and when, stays answerable
    assert conn.execute("SELECT count(*) FROM drugref.question_state "
                        "WHERE question_uuid = %s", (qu,)).fetchone()[0] == 2


# ---- the worklist and the cost ladder ---------------------------------------


def test_an_unchecked_question_outranks_a_checked_one(conn):
    """The ladder that governs where effort goes: a question with no openFDA-SPL row
    has not yet earned literature-mining effort, so it must sort first. Asserted
    rather than assumed, because nothing else makes the ordering real."""
    run_id = _run(conn)
    _moiety(conn, run_id, "unchecked")
    _empty_ci_class(conn, run_id)
    questions.register_from_gaps(conn, run_id)

    checked = conn.execute(
        "SELECT question_uuid FROM drugref.open_question "
        "WHERE gap_kind = 'unpopulated_contraindication'").fetchone()[0]
    questions.record_source_check(conn, checked, "openFDA-SPL", "2026-07-01", "not_covered")

    # ORDER BY restated rather than leaning on the one inside the view: Postgres does
    # not guarantee a view's ordering survives the query that wraps it, so a test
    # that relies on it is asserting something the database has not promised.
    order = [r[0] for r in conn.execute(
        "SELECT question_uuid FROM drugref.question_worklist "
        "ORDER BY cheapest_unchecked_rank NULLS LAST, gap_kind, question_uuid")]
    assert order.index(checked) == len(order) - 1


def test_recording_the_same_check_twice_reports_the_no_op(conn):
    """The bool is the whole insert-vs-conflict signal, so a caller sweeping a tier
    can tell new work from a re-run. Untested, it could invert and nothing would
    notice."""
    run_id = _run(conn)
    _moiety(conn, run_id)
    questions.register_from_gaps(conn, run_id)
    qu = conn.execute("SELECT question_uuid FROM drugref.open_question").fetchone()[0]

    assert questions.record_source_check(conn, qu, "MED-RT", "2026.07.06", "not_covered")
    assert not questions.record_source_check(conn, qu, "MED-RT", "2026.07.06", "not_covered")


def test_add_evidence_returns_the_id_a_correction_needs(conn):
    """Supersession is insert-then-point, so the caller cannot correct a finding it
    cannot name."""
    run_id = _run(conn)
    _moiety(conn, run_id)
    questions.register_from_gaps(conn, run_id)
    qu = conn.execute("SELECT question_uuid FROM drugref.open_question").fetchone()[0]

    eid = questions.add_evidence(conn, qu, "PMID", "12345678", "supports", run_id)
    assert conn.execute(
        "SELECT reference_value FROM drugref.question_evidence "
        "WHERE question_evidence_id = %s", (eid,)).fetchone() == ("12345678",)


def test_a_withdrawn_question_is_off_the_worklist_but_still_registered(conn):
    """Withdrawal suppresses noise; it does not delete the question, which an
    external tool may already have cited."""
    run_id = _run(conn)
    _moiety(conn, run_id)
    questions.register_from_gaps(conn, run_id)
    qu = conn.execute("SELECT question_uuid FROM drugref.open_question").fetchone()[0]
    questions.set_state(conn, qu, "withdrawn", "malformed", run_id)

    assert conn.execute("SELECT count(*) FROM drugref.question_worklist").fetchone()[0] == 0
    assert conn.execute("SELECT count(*) FROM drugref.open_question").fetchone()[0] == 1


def test_an_old_check_does_not_close_a_question(conn):
    """Watermark, not closure: 'no evidence found' leaves the question open with a
    recent check row. Medicine moves, and a question unanswerable this month may be
    answerable next."""
    run_id = _run(conn)
    _moiety(conn, run_id)
    questions.register_from_gaps(conn, run_id)
    qu = conn.execute("SELECT question_uuid FROM drugref.open_question").fetchone()[0]
    questions.record_source_check(conn, qu, "literature", "2020-01-01", "not_covered")

    assert questions.current_state(conn, qu) == "open"
    assert conn.execute("SELECT count(*) FROM drugref.question_worklist").fetchone()[0] == 1


# ---- unreviewed_expansion_root (Plan B) --------------------------------------


def _unreviewed_root(conn, run_id, code="N0000900000", descendants=25):
    """A contraindicated class with enough descendants to clear the discovery
    heuristic, and no expansion-policy decision recorded against it."""
    root = ids.mint_class_uuid("MED-RT", code)
    conn.execute("INSERT INTO drugref.substance_class (class_uuid, source, source_code, "
                 "published_code, class_name, concept_type, first_seen_ingest) "
                 "VALUES (%s, 'MED-RT', %s, %s, 'Sprawling Activity Alteration [PE]', "
                 "'PE', %s)", (root, code, code, run_id))
    base = int(code[1:])
    for i in range(descendants):
        child_code = f"N{base + i + 1:010d}"
        child = ids.mint_class_uuid("MED-RT", child_code)
        conn.execute("INSERT INTO drugref.substance_class (class_uuid, source, "
                     "source_code, published_code, class_name, concept_type, "
                     "first_seen_ingest) VALUES (%s, 'MED-RT', %s, %s, 'c', 'PE', %s)",
                     (child, child_code, child_code, run_id))
        conn.execute("INSERT INTO drugref.class_parent (child_class_uuid, "
                     "parent_class_uuid, ingest_run) VALUES (%s, %s, %s)",
                     (child, root, run_id))
    conn.execute("INSERT INTO drugref.class_contraindication (subject_moiety_uuid, "
                 "object_class_uuid, relationship, source, ingest_run) "
                 "VALUES (%s, %s, 'CI_PE', 'MED-RT', %s)",
                 (_moiety(conn, run_id, "subj"), root, run_id))
    return root


def test_an_unreviewed_expansion_root_becomes_a_question(conn):
    """The review gate reaches the worklist. Left as a view alone it would be a report
    nobody reads; as a question it carries a citable UUID, a state and a watermark."""
    run_id = _run(conn)
    root = _unreviewed_root(conn, run_id)
    questions.register_from_gaps(conn, run_id)

    gap_key, text = conn.execute(
        "SELECT gap_key, question_text FROM drugref.open_question "
        "WHERE gap_kind = 'unreviewed_expansion_root'").fetchone()
    assert gap_key == f"CLASS:{root}"
    # Named, not referenced by UUID: a reviewer has to judge it on sight, and the
    # count is the whole reason it was asked about.
    assert "Sprawling Activity Alteration [PE]" in text and "25" in text


def test_recording_a_decision_closes_the_expansion_question(conn):
    """The gap is answerable by drugref itself rather than by literature -- which is
    what makes it a good end-to-end test of the register: the answer goes into a
    table, the next rebuild sees the gap gone, and the question leaves."""
    run_id = _run(conn)
    _unreviewed_root(conn, run_id)
    questions.register_from_gaps(conn, run_id)
    assert conn.execute("SELECT count(*) FROM drugref.open_question "
                        "WHERE gap_kind = 'unreviewed_expansion_root'").fetchone()[0] == 1

    conn.execute(
        "INSERT INTO drugref.class_expansion_policy (source, source_code, decision, "
        "class_name, rationale, reviewed_by, reviewed_against) VALUES "
        "('MED-RT', 'N0000900000', 'deny', 'Sprawling Activity Alteration [PE]', "
        "'abstract organ-system bucket', 'test', '2026.07.06')")
    questions.register_from_gaps(conn, run_id)

    assert conn.execute("SELECT count(*) FROM drugref.open_question "
                        "WHERE gap_kind = 'unreviewed_expansion_root'").fetchone()[0] == 0


def test_the_same_class_can_raise_two_different_questions(conn):
    """gap_key is CLASS:{uuid} for both unpopulated_contraindication and
    unreviewed_expansion_root, so only gap_kind separates them. A sprawling class
    nothing is filed under is BOTH -- two questions, two UUIDs, answerable
    independently."""
    run_id = _run(conn)
    root = _unreviewed_root(conn, run_id)
    questions.register_from_gaps(conn, run_id)

    assert sorted(k for (k,) in conn.execute(
        "SELECT gap_kind FROM drugref.open_question WHERE gap_key = %s",
        (f"CLASS:{root}",)).fetchall()) == ["unpopulated_contraindication",
                                            "unreviewed_expansion_root"]
