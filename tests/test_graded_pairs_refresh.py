"""db/083: every pair a reviewed grade reached is written down (#281, #279).

`curated_ddi_pair` only says which pairs rulings grade NOW. db/083 keeps an
observation per (ruling key, pair) episode, written by a refresh that runs at
COMMIT, so a pair that later leaves the view is still known to have been graded.
Tests stand in for COMMIT with `commit_point` (see tests/graded_pair_helpers.py).
"""
import re

import psycopg
import pytest

from drugref import curation, db, graded_pairs, interactions, provenance
from tests.graded_pair_helpers import commit_point, observations
from tests.test_class_subject_read_path import (_a_descent_and_policy_fixture,
                                                _a_graded_class_rule, _a_moiety,
                                                _file_member)


def _class_rule(conn, ingest_run_id):
    """A graded class rule A x B with one member each side: (A, B, s, p)."""
    a, b, (s,), (p,) = _a_graded_class_rule(
        conn, ingest_run_id, subject_code="N0000028100", object_code="N0000028101",
        subject_members=[("TESTUNI281A", "graded subject")],
        object_members=[("TESTUNI281B", "graded partner")])
    return a, b, s, p


def _grade_moiety_rule(conn, rule, applies=True):
    curation.record_interaction_judgement(
        conn, rule["subject"], rule["class"], "CI_MoA", applies,
        severity="major" if applies else None,
        evidence_grade="established" if applies else None,
        reviewed_by="test", reviewed_against="2026")


def _acknowledge(conn, observation_id):
    conn.execute(
        f"INSERT INTO {graded_pairs.ACK_TABLE} "
        "(observation_id, rationale, reviewed_by, reviewed_against) "
        "VALUES (%s, 'left the class', 'test', '2026')", (observation_id,))


def _a_finished_run(conn) -> int:
    """A fresh run, finished in this transaction: queues the ingest_run refresh."""
    run = conn.execute(
        "INSERT INTO drugref.ingest_run (source, upstream_release, source_checksum, "
        "writer) VALUES ('PBS', 'test2', 'test2', 'pbs_run') "
        "RETURNING ingest_run_id").fetchone()[0]
    provenance.finish_run(conn, run)
    return run


def _run_backfill(conn):
    sql = (db.migration_dir() / "083_graded_pair.sql").read_text()
    conn.execute(re.search(r"-- backfill:begin\n(.*?)-- backfill:end", sql, re.S)[1])


def _count(conn):
    return conn.execute(
        f"SELECT count(*) FROM {graded_pairs.OBSERVATION_TABLE}").fetchone()[0]


def test_a_class_ruling_is_observed_at_its_commit_point(conn, ingest_run_id):
    a, b, s, p = _class_rule(conn, ingest_run_id)
    assert observations(conn) == set()
    commit_point(conn)
    assert observations(conn) == {("class_rule", a, b, "CI_MoA", s, p, "observed")}
    assert _count(conn) == 1


def test_a_moiety_ruling_is_observed_at_its_commit_point(conn, a_graded_rule):
    _grade_moiety_rule(conn, a_graded_rule)
    assert observations(conn) == set()
    commit_point(conn)
    s, p, b = a_graded_rule["subject"], a_graded_rule["partner"], a_graded_rule["class"]
    assert observations(conn) == {("moiety_rule", s, b, "CI_MoA", s, p, "observed")}
    assert _count(conn) == 1


def test_finishing_an_ingest_run_observes_new_members(conn, ingest_run_id):
    """Membership changes only by ingest, and every ingest ends in `finish_run`:
    that one UPDATE is the choke point that catches a drug JOINING a class."""
    a, b, s, p = _class_rule(conn, ingest_run_id)
    commit_point(conn)
    joiner = _a_moiety(conn, ingest_run_id, "TESTUNI281C", "joined later")
    _file_member(conn, joiner, a, ingest_run_id)
    _a_finished_run(conn)
    assert ("class_rule", a, b, "CI_MoA", joiner, p, "observed") not in observations(conn)
    commit_point(conn)
    assert ("class_rule", a, b, "CI_MoA", joiner, p, "observed") in observations(conn)


def test_an_open_episode_is_not_observed_twice(conn, ingest_run_id):
    _class_rule(conn, ingest_run_id)
    commit_point(conn)
    _a_finished_run(conn)     # queues a real refresh for the second commit point
    commit_point(conn)
    assert graded_pairs.refresh(conn) == 0
    assert _count(conn) == 1


def test_an_acknowledged_pair_graded_again_opens_a_new_episode(conn, ingest_run_id):
    a, b, s, p = _class_rule(conn, ingest_run_id)
    commit_point(conn)
    (oid,) = conn.execute(
        f"SELECT observation_id FROM {graded_pairs.OBSERVATION_TABLE}").fetchone()
    _acknowledge(conn, oid)
    assert graded_pairs.refresh(conn) == 1
    assert _count(conn) == 2


def test_a_policy_change_is_observed_at_its_commit_point(conn, ingest_run_id):
    """An `allow` replacing a `deny` widens a class rule's reach with no ruling or
    ingest in the transaction: only the class_expansion_policy trigger sees it."""
    f = _a_descent_and_policy_fixture(conn, ingest_run_id, expanding_side="subject")
    source, code = conn.execute(
        "SELECT source, source_code FROM drugref.substance_class "
        "WHERE class_uuid = %s", (f["expanding_class"],)).fetchone()

    def decide(decision):
        interactions.record_expansion_decision(
            conn, source, code, decision, "Subject Parent [MoA]", "test", "test", "2026")
    key = ("class_rule", f["subject_class"], f["object_class"], "CI_MoA")
    decide("deny")
    commit_point(conn)
    assert observations(conn) == {(*key, f["direct"], f["fixed"], "observed")}
    decide("allow")
    commit_point(conn)
    assert (*key, f["descendant_only"], f["fixed"], "observed") in observations(conn)


def test_applies_false_and_orphans_are_never_observed(conn, ingest_run_id, a_graded_rule):
    """`curated_ddi_pair` drops withdrawn rulings and rulings whose candidate is
    gone; the refresh reads that view, so neither is ever observed."""
    _grade_moiety_rule(conn, a_graded_rule, applies=True)
    _grade_moiety_rule(conn, a_graded_rule, applies=False)   # withdrawn
    a, b, _s, _p = _class_rule(conn, ingest_run_id)
    conn.execute("DELETE FROM drugref.class_pair_contraindication "
                 "WHERE subject_class_uuid = %s", (a,))       # orphaned
    commit_point(conn)
    assert observations(conn) == set()


def test_every_refresh_trigger_exists_and_is_deferred(conn):
    """The spec's guard (§6.6): a missing or immediate trigger would refresh
    before append-then-point finished, or not at all."""
    rows = conn.execute(
        "SELECT n.nspname || '.' || g.tgname, c.relname, g.tgdeferrable, "
        "g.tginitdeferred FROM pg_trigger g "
        "JOIN pg_class c ON c.oid = g.tgrelid "
        "JOIN pg_namespace n ON n.oid = c.relnamespace "
        "WHERE n.nspname || '.' || g.tgname = ANY(%s)",
        (list(graded_pairs.REFRESH_TRIGGERS),)).fetchall()
    assert {r[0] for r in rows} == set(graded_pairs.REFRESH_TRIGGERS)
    assert {r[1] for r in rows} == {"curated_interaction", "curated_class_interaction",
                                    "class_expansion_policy", "ingest_run"}
    assert all(r[2] and r[3] for r in rows)


@pytest.mark.parametrize("table", [graded_pairs.OBSERVATION_TABLE,
                                   graded_pairs.ACK_TABLE])
@pytest.mark.parametrize("verb", ["UPDATE {t} SET observation_id = observation_id",
                                  "DELETE FROM {t}"])
def test_observations_and_acks_are_insert_only(conn, ingest_run_id, table, verb):
    _class_rule(conn, ingest_run_id)
    commit_point(conn)
    _acknowledge(conn, conn.execute(
        f"SELECT observation_id FROM {graded_pairs.OBSERVATION_TABLE}").fetchone()[0])
    with pytest.raises(psycopg.errors.RaiseException, match="insert-only"):
        conn.execute(verb.format(t=table))


def test_the_backfill_records_reach_and_marks_it(conn, ingest_run_id):
    """A drug that left after its db/080 snapshot is no longer in
    `curated_ddi_pair`; the backfill recovers the pair from the snapshot."""
    a, b, s, p = _class_rule(conn, ingest_run_id)
    conn.execute("DELETE FROM drugref.class_membership WHERE moiety_uuid = %s", (s,))
    _run_backfill(conn)
    assert observations(conn) == {("class_rule", a, b, "CI_MoA", s, p, "backfill_db083")}


def test_the_backfill_records_a_still_graded_pair_once(conn, ingest_run_id):
    """Source 1 (the refresh) records it; source 2 (its db/080 snapshot) must not
    add a second row for the same open episode."""
    a, b, s, p = _class_rule(conn, ingest_run_id)
    _run_backfill(conn)
    assert observations(conn) == {("class_rule", a, b, "CI_MoA", s, p, "backfill_db083")}
    assert _count(conn) == 1


def test_the_backfill_skips_a_non_applying_versions_snapshot(conn, ingest_run_id):
    """Only an APPLYING version's reach was graded. v1 applies and reached (s, p);
    v2 withdraws the key and (by hand) carries a reach row for drug m: m's pair was
    never graded and must not be recorded."""
    a, b, s, p = _class_rule(conn, ingest_run_id)
    conn.execute("DELETE FROM drugref.class_membership WHERE moiety_uuid = %s", (s,))
    m = _a_moiety(conn, ingest_run_id, "TESTUNI281D", "withdrawn-only reach")
    v2 = conn.execute(
        "INSERT INTO drugref.curated_class_interaction "
        "(subject_class_uuid, object_class_uuid, relationship, applies, source, "
        " reviewed_by, reviewed_against) VALUES (%s, %s, 'CI_MoA', false, 'DRUGREF', "
        "'t', 't') RETURNING curated_class_interaction_id", (a, b)).fetchone()[0]
    conn.execute("UPDATE drugref.curated_class_interaction SET superseded_by = %s "
                 "WHERE superseded_by IS NULL AND curated_class_interaction_id <> %s "
                 "AND subject_class_uuid = %s", (v2, v2, a))
    conn.execute("INSERT INTO drugref.curated_class_interaction_reach "
                 "(curated_class_interaction_id, side, moiety_uuid, captured) "
                 "VALUES (%s, 'subject', %s, 'at_ruling'), (%s, 'partner', %s, "
                 "'at_ruling')", (v2, m, v2, p))
    _run_backfill(conn)
    assert observations(conn) == {("class_rule", a, b, "CI_MoA", s, p, "backfill_db083")}


def test_the_backfill_skips_self_pairs(conn, ingest_run_id):
    """A class ruled against itself snapshots each drug on BOTH sides; a drug is
    never paired with itself (the view drops self-pairs too)."""
    qt, _, (m1, m2), _ = _a_graded_class_rule(
        conn, ingest_run_id, subject_code="N0000028102", object_code="N0000028102",
        subject_members=[("TESTUNI281E", "qt-a"), ("TESTUNI281F", "qt-b")],
        object_members=[])
    conn.execute("DELETE FROM drugref.class_membership WHERE class_uuid = %s", (qt,))
    _run_backfill(conn)
    assert observations(conn) == {
        ("class_rule", qt, qt, "CI_MoA", x, y, "backfill_db083")
        for x, y in ((m1, m2), (m2, m1))}
