# tests/test_question_registry_drift.py
"""Issue 104: the stored question registry lagging the gap views, made VISIBLE.

`open_question` is re-derived only at the end of an ingest. `drugref curate` is
deliberately not an ingest, so between a curate run and the next ingest the worklist
still shows the questions that curation just answered. These tests drive that window
(close a gap without re-registering) and its mirror (open one), and check that
`questions.registry_drift` names both and that a re-derive clears them.
"""
import uuid

import pytest

from drugref import cli_status, ids, questions


@pytest.fixture(autouse=True)
def _isolate(conn):
    """The gap views read the WHOLE registry, so rows another module committed would
    show up as gaps here. Truncate first, as tests/test_questions.py does."""
    conn.execute("TRUNCATE drugref.class_contraindication, drugref.class_membership, "
                 "drugref.class_parent, drugref.substance_class, drugref.identity_claim, "
                 "drugref.substance_moiety, drugref.ingest_run RESTART IDENTITY CASCADE")
    conn.commit()
    yield


def _run(conn):
    return conn.execute(
        "INSERT INTO drugref.ingest_run (source, upstream_release, source_checksum, "
        "writer) VALUES ('MED-RT', 'test', 'deadbeef', 'medrt_run') "
        "RETURNING ingest_run_id").fetchone()[0]


def _moiety(conn, run_id, name):
    """A moiety with no has_PE membership, which is an `unclassified_moiety` gap."""
    m = uuid.uuid4()
    conn.execute("INSERT INTO drugref.substance_moiety "
                 "(moiety_uuid, display_name, first_seen_ingest) VALUES (%s, %s, %s)",
                 (m, name, run_id))
    return m


def _classify(conn, run_id, moiety):
    """Give `moiety` a has_PE membership, which closes its gap -- the stand-in for a
    curate run answering a question without re-deriving the registry."""
    cu = ids.mint_class_uuid("MED-RT", "N0000000009")
    conn.execute("INSERT INTO drugref.substance_class (class_uuid, source, source_code, "
                 "published_code, class_name, concept_type, first_seen_ingest) "
                 "VALUES (%s, 'MED-RT', 'N0000000009', 'N0000000009', 'C [PE]', 'PE', %s)",
                 (cu, run_id))
    conn.execute("INSERT INTO drugref.class_membership "
                 "(moiety_uuid, class_uuid, relationship, ingest_run) "
                 "VALUES (%s, %s, 'has_PE', %s)", (moiety, cu, run_id))


def test_a_freshly_derived_registry_has_no_drift(conn):
    run_id = _run(conn)
    _moiety(conn, run_id, "orphanium")
    questions.register_from_gaps(conn, run_id)
    assert questions.registry_drift(conn) == []


def test_a_gap_closed_since_the_last_derive_is_a_STALE_question(conn):
    """The #104 window itself: the question is still current on the worklist, but
    the gap view no longer derives it."""
    run_id = _run(conn)
    m = _moiety(conn, run_id, "orphanium")
    questions.register_from_gaps(conn, run_id)

    _classify(conn, run_id, m)
    assert questions.registry_drift(conn) == [
        questions.RegistryDrift("unclassified_moiety", stale=1, unregistered=0)]


def test_a_gap_opened_since_the_last_derive_is_UNREGISTERED(conn):
    run_id = _run(conn)
    questions.register_from_gaps(conn, run_id)

    _moiety(conn, run_id, "newcomium")
    assert questions.registry_drift(conn) == [
        questions.RegistryDrift("unclassified_moiety", stale=0, unregistered=1)]


def test_a_RETIRED_question_whose_gap_reopened_counts_as_unregistered(conn):
    """A retired question (is_current false) is off the worklist, so a reopened gap
    it would answer is missing from the worklist until the next derive, even though
    its row exists."""
    run_id = _run(conn)
    m = _moiety(conn, run_id, "orphanium")
    questions.register_from_gaps(conn, run_id)
    conn.execute("UPDATE drugref.open_question SET is_current = false "
                 "WHERE gap_key = %s", (f"MOIETY:{m}",))
    assert questions.registry_drift(conn) == [
        questions.RegistryDrift("unclassified_moiety", stale=0, unregistered=1)]


def test_a_closed_and_an_opened_gap_in_one_kind_do_not_cancel(conn):
    """The docstring's reason for comparing KEY SETS: by count, one gap closed and
    one opened in the same kind is 1 stored vs 1 derived and reads as in step."""
    run_id = _run(conn)
    m = _moiety(conn, run_id, "orphanium")
    questions.register_from_gaps(conn, run_id)
    _classify(conn, run_id, m)
    _moiety(conn, run_id, "newcomium")
    assert questions.registry_drift(conn) == [
        questions.RegistryDrift("unclassified_moiety", stale=1, unregistered=1)]


def test_re_deriving_clears_the_drift(conn):
    run_id = _run(conn)
    m = _moiety(conn, run_id, "orphanium")
    questions.register_from_gaps(conn, run_id)
    _classify(conn, run_id, m)
    _moiety(conn, run_id, "newcomium")
    assert questions.registry_drift(conn) != []

    questions.register_from_gaps(conn, run_id)
    assert questions.registry_drift(conn) == []


def test_the_same_key_under_another_kind_is_not_a_match(conn):
    """Kinds share key shapes ('CLASS:' alone has five), so the join must match on
    the kind too: a question stored under one kind does not answer another kind's
    gap with the same key. Also pins one row per kind, ordered by kind."""
    run_id = _run(conn)
    m = _moiety(conn, run_id, "orphanium")
    key = f"MOIETY:{m}"
    conn.execute("INSERT INTO drugref.open_question (question_uuid, gap_kind, gap_key, "
                 "question_text, first_derived_ingest, last_derived_ingest) "
                 "VALUES (%s, 'condition_without_indication', %s, 'q', %s, %s)",
                 (ids.mint_question_uuid("condition_without_indication", key), key,
                  run_id, run_id))
    assert questions.registry_drift(conn) == [
        questions.RegistryDrift("condition_without_indication", stale=1,
                                unregistered=0),
        questions.RegistryDrift("unclassified_moiety", stale=0, unregistered=1)]


def test_a_database_without_a_gap_view_gets_the_guards_sentence(conn):
    """`drugref status` on a database short of db/075 must print the guard's one
    line, not a psycopg traceback. Renamed inside the transaction, as
    tests/test_spl_run_census.py does for db/078: the guard's probe rolls the
    rename back, so this lands in its "NOT a missing migration" branch, and what it
    pins is THIS block's migration and consequence."""
    conn.execute("ALTER VIEW drugref.gap_unclassified_moiety RENAME TO before_db075")
    with pytest.raises(RuntimeError, match=r"(?i)db/075[\s\S]*a worklist behind "
                       r"the gap views reads as current"):
        cli_status.print_registry_drift_block(conn)


# ---- the status block's wording (pure, no database) --------------------------


def test_an_in_step_registry_says_so_in_one_line():
    assert cli_status.registry_drift_lines([]) == [
        "question registry: in step with the gap views"]


def test_drift_is_bannered_with_its_remedy_and_listed_per_kind():
    lines = cli_status.registry_drift_lines([
        questions.RegistryDrift("uncurated_interaction_rule", stale=8, unregistered=0),
        questions.RegistryDrift("unclassified_moiety", stale=1, unregistered=2)])
    assert lines[0].startswith("question registry: 9 stale, 2 unregistered")
    # The banner must say what fixes it: the next ingest is the only re-deriver.
    assert "next ingest" in lines[0]
    assert lines[1].split() == ["uncurated_interaction_rule", "stale", "8",
                                "unregistered", "0"]
    assert lines[2].split() == ["unclassified_moiety", "stale", "1",
                                "unregistered", "2"]
