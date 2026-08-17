# tests/test_questions.py
"""Deriving the register from the gap views, and the curator API over it.

The load-bearing test in this file is test_curator_state_survives_a_rebuild. Every
other property here would hold just as well under the design that put `state` on
open_question -- on a FRESH database. That design fails only on the second ingest of
a long-lived one, which is exactly the shape of bug that reaches production.
"""
import uuid

import psycopg
import pytest

from drugref import curation, ids, interactions, questions


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
        "INSERT INTO drugref.ingest_run "
        "(source, upstream_release, source_checksum, writer) "
        "VALUES (%s, 'test', 'deadbeef', 'medrt_run') RETURNING ingest_run_id",
        (source,)).fetchone()[0]


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


@pytest.mark.parametrize("working_record", ["annotation", "reference"])
def test_a_closed_gap_carrying_reviewer_work_is_retained(
    conn: psycopg.Connection, working_record: str
) -> None:
    """db/045 research history keeps its immortal question after the gap closes."""
    run_id = _run(conn)
    moiety_uuid = _moiety(conn, run_id)
    questions.register_from_gaps(conn, run_id)
    question_uuid = conn.execute(
        "SELECT question_uuid FROM drugref.open_question"
    ).fetchone()[0]
    reviewer_uuid = uuid.uuid4()
    conn.execute(
        "INSERT INTO drugref.reviewer_account (reviewer_uuid, username) "
        "VALUES (%s, 'maya.chen')",
        (reviewer_uuid,),
    )
    if working_record == "annotation":
        conn.execute(
            "INSERT INTO drugref.reviewer_annotation "
            "(question_uuid, reviewer_uuid, annotation_markdown) "
            "VALUES (%s, %s, 'Working note')",
            (question_uuid, reviewer_uuid),
        )
    else:
        conn.execute(
            "INSERT INTO drugref.reviewer_evidence_reference "
            "(question_uuid, reviewer_uuid, reference_scheme, reference_value) "
            "VALUES (%s, %s, 'PMID', '12345678')",
            (question_uuid, reviewer_uuid),
        )

    _classify(conn, run_id, moiety_uuid)
    questions.register_from_gaps(conn, run_id)

    assert conn.execute(
        "SELECT is_current FROM drugref.open_question WHERE question_uuid = %s",
        (question_uuid,),
    ).fetchone() == (False,)


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


def _a_class(conn, run_id, code, name="Some Class [PE]"):
    """One substance_class row, for the class-grain curated judgement below."""
    cu = ids.mint_class_uuid("MED-RT", code)
    conn.execute("INSERT INTO drugref.substance_class (class_uuid, source, source_code, "
                 "published_code, class_name, concept_type, first_seen_ingest) "
                 "VALUES (%s, 'MED-RT', %s, %s, %s, 'PE', %s)",
                 (cu, code, code, name, run_id))
    return cu


def test_a_closed_gap_carrying_a_CLASS_grain_judgement_is_RETAINED_not_deleted(conn):
    """db/032's curated_class_interaction is the SIXTH table that cites a question.

    Every curated table is `ON DELETE CASCADE` from open_question AND carries an
    append-only trigger that refuses DELETE outright. So a citing table missing
    from the retention guard below does not lose data quietly -- it makes the
    cascade hit `forbid_overlay_rewrite`, which RAISEs, which aborts THE WHOLE
    INGEST TRANSACTION. Every subsequent ingest of every source then fails
    identically until someone hand-edits the database.

    This is verbatim the failure register_from_gaps' own docstring records
    db/029 being written to prevent ("the very row that answers a question is
    what would otherwise make the next ingest try to delete it"), and it was
    reachable the moment anything passed `question_uuid` to
    curation.record_class_interaction_judgement -- a public keyword argument.
    """
    run_id = _run(conn)
    m = _moiety(conn, run_id)
    questions.register_from_gaps(conn, run_id)
    qu = conn.execute("SELECT question_uuid FROM drugref.open_question").fetchone()[0]

    curation.record_class_interaction_judgement(
        conn,
        _a_class(conn, run_id, "N0000000101", "Subject Class [PE]"),
        _a_class(conn, run_id, "N0000000102", "Object Class [PE]"),
        "CI_PE", False, question_uuid=qu,
        reviewed_by="Dr X", reviewed_against="test")

    _classify(conn, run_id, m)                       # the gap closes
    questions.register_from_gaps(conn, run_id)       # must NOT abort

    assert conn.execute(
        "SELECT is_current FROM drugref.open_question "
        "WHERE question_uuid = %s", (qu,)).fetchall() == [(False,)]
    # the curator's judgement survived, which is the whole point
    assert conn.execute(
        "SELECT count(*) FROM drugref.curated_class_interaction "
        "WHERE question_uuid = %s", (qu,)).fetchone()[0] == 1


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


def test_withdrawing_the_decision_reopens_the_expansion_question(conn):
    """db/027. `withdrawn` means NO CURRENT JUDGEMENT, so the class goes back on the
    worklist -- the whole reason a third `decision` value exists rather than nothing.

    Supersession alone can retire nothing: a correction must point at a later row
    carrying the same natural key, so every correction leaves another live row
    standing, and an append-only table can never return a class to "no row". Without
    `withdrawn` a class that had ever been ruled on could never be asked about again.

    This also pins gap_unreviewed_expansion_root ON THE VIEW. Revert its NOT EXISTS to
    drugref.class_expansion_policy and the SUPERSEDED `deny` still exists, so the
    question stays shut and a curator who withdrew a stale ruling is never asked to
    replace it.

    Registers the gap ONCE BEFORE any decision exists (phase 0) so there is a real
    original question_uuid on record. Deny-then-withdraw alone never lets that row
    exist in the first place -- the deny lands before the first register_from_gaps,
    so the phase-1 `count == 0` holds because the question was never opened, not
    because it was closed and is being "restored". Phase 0 is what turns the final
    assertion from a claim about history into one the test actually establishes.
    """
    run_id = _run(conn)
    root = _unreviewed_root(conn, run_id)

    # Phase 0: the ORIGINAL question, minted before any decision has ever touched the
    # class, so phase 3 below has something concrete to compare against.
    questions.register_from_gaps(conn, run_id)
    original_uuid = conn.execute(
        "SELECT question_uuid FROM drugref.open_question "
        "WHERE gap_kind = 'unreviewed_expansion_root' AND gap_key = %s",
        (f"CLASS:{root}",)).fetchone()[0]

    interactions.record_expansion_decision(
        conn, "MED-RT", "N0000900000", "deny", "Sprawling Activity Alteration [PE]",
        "abstract organ-system bucket", "test", "2026.07.06")
    questions.register_from_gaps(conn, run_id)
    assert conn.execute("SELECT count(*) FROM drugref.open_question "
                        "WHERE gap_kind = 'unreviewed_expansion_root'").fetchone()[0] == 0

    interactions.withdraw_expansion_decision(
        conn, "MED-RT", "N0000900000", "the release it was judged against is gone",
        "test", "2026.08.03")
    questions.register_from_gaps(conn, run_id)

    # Exactly one row, and its question_uuid is the SAME one phase 0 minted --
    # immortal in the sense external tooling relies on: a citation made before the
    # class was ever ruled on still resolves after a deny-then-withdraw round trip.
    # (The row itself was deleted in phase 1 -- nothing had cited it yet, so
    # register_from_gaps' untouched-question cleanup removed it outright -- and
    # phase 3 inserts a fresh row; the two UUIDs match only because
    # ids.mint_question_uuid is a pure function of (gap_kind, gap_key), not because
    # any row survived. Verified by mutation: swapping that call for uuid.uuid4() in
    # questions.register_from_gaps makes this assertion fail while leaving the
    # phase-1 count assertion above green.)
    assert conn.execute(
        "SELECT question_uuid, gap_key FROM drugref.open_question "
        "WHERE gap_kind = 'unreviewed_expansion_root'").fetchall() == \
        [(original_uuid, f"CLASS:{root}")]


def test_the_same_class_can_raise_several_different_questions(conn):
    """gap_key is CLASS:{uuid} for four kinds now, so only gap_kind separates them. A
    sprawling PE class nothing is filed under raises all three of these at once --
    three questions, three UUIDs, each answerable independently and by a different
    remedy (file a drug / rule on expansion / rule on accumulation). question_uuid
    takes gap_kind as an input precisely so they cannot collide.

    This list grew from two when Plan C landed, and that is the shape working rather
    than a regression: a class MED-RT thought worth contraindicating over is exactly a
    class somebody owes an accumulation ruling on."""
    run_id = _run(conn)
    root = _unreviewed_root(conn, run_id)
    questions.register_from_gaps(conn, run_id)

    assert sorted(k for (k,) in conn.execute(
        "SELECT gap_kind FROM drugref.open_question WHERE gap_key = %s",
        (f"CLASS:{root}",)).fetchall()) == ["uncurated_additive_effect",
                                            "unpopulated_contraindication",
                                            "unreviewed_expansion_root"]
