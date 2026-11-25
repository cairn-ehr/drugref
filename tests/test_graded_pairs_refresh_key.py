"""db/084: a ruling's commit refreshes only the ruling's own key (#281 review).

db/083's refresh re-reads ALL of `curated_ddi_pair` at every ruling's commit, and
the moiety half of that view costs ~5 s on a real registry even with no rulings.
A ruling changes grades only for its own natural key -- both halves of the view
join rulings on exactly (subject, object class, relationship) -- so db/084 runs a
per-key refresh from the two ruling triggers. These tests pin that the per-key
refresh records exactly what the full one would for that key, and nothing else.
"""
import psycopg

from drugref import graded_pairs
from tests.graded_pair_helpers import commit_point, observations
from tests.test_class_subject_read_path import _a_moiety, _file_member
from tests.test_graded_pairs_refresh import (_class_rule, _count,
                                             _grade_moiety_rule)


def _refresh_key(conn, grain, subject_key, object_class, relationship="CI_MoA"):
    return conn.execute(
        "SELECT drugref.refresh_graded_pairs_for_key(%s, %s, %s, %s)",
        (grain, subject_key, object_class, relationship)).fetchone()[0]


def _what_the_full_refresh_records(conn) -> set[tuple]:
    """The observations `refresh_graded_pairs()` would add now, then undone."""
    with conn.transaction():
        graded_pairs.refresh(conn)
        full = observations(conn)
        raise psycopg.Rollback()
    return full


def test_the_per_key_refresh_records_what_the_full_one_would_for_its_key(
        conn, ingest_run_id, a_graded_rule):
    _grade_moiety_rule(conn, a_graded_rule)
    a, b, _s, _p = _class_rule(conn, ingest_run_id)
    full = _what_the_full_refresh_records(conn)
    moiety_key = ("moiety_rule", a_graded_rule["subject"], a_graded_rule["class"],
                  "CI_MoA")
    class_key = ("class_rule", a, b, "CI_MoA")
    assert {o[:4] for o in full} == {moiety_key, class_key}
    assert observations(conn) == set()

    assert _refresh_key(conn, *moiety_key) == 1
    assert observations(conn) == {o for o in full if o[:4] == moiety_key}
    assert _refresh_key(conn, *class_key) == 1
    assert observations(conn) == full


def test_the_per_key_refresh_keeps_the_open_episode_rule(conn, ingest_run_id):
    a, b, _s, _p = _class_rule(conn, ingest_run_id)
    assert _refresh_key(conn, "class_rule", a, b) == 1
    assert _refresh_key(conn, "class_rule", a, b) == 0
    assert _count(conn) == 1


def test_a_key_of_the_other_grain_records_nothing(conn, ingest_run_id):
    """The grain filter is literal: a class key named as a moiety key matches no
    moiety ruling, so nothing is recorded."""
    a, b, _s, _p = _class_rule(conn, ingest_run_id)
    assert _refresh_key(conn, "moiety_rule", a, b) == 0
    assert observations(conn) == set()


def test_a_rulings_commit_refreshes_only_its_own_key(conn, ingest_run_id,
                                                      a_graded_rule):
    """A drug joins class A with no ingest finishing (no ingest_run trigger): a
    moiety ruling's commit does not observe it, because the ruling's refresh is
    its own key only. The finished ingest that really adds a member observes it."""
    a, b, _s, p = _class_rule(conn, ingest_run_id)
    commit_point(conn)
    joiner = _a_moiety(conn, ingest_run_id, "TESTUNI284A", "joined later")
    _file_member(conn, joiner, a, ingest_run_id)
    _grade_moiety_rule(conn, a_graded_rule)
    commit_point(conn)
    seen = observations(conn)
    assert ("moiety_rule", a_graded_rule["subject"], a_graded_rule["class"],
            "CI_MoA", a_graded_rule["subject"], a_graded_rule["partner"],
            "observed") in seen
    assert ("class_rule", a, b, "CI_MoA", joiner, p, "observed") not in seen


def test_ruling_triggers_refresh_per_key_and_the_others_in_full(conn):
    """The guard for db/084: which function each refresh trigger runs."""
    rows = dict(conn.execute(
        "SELECT n.nspname || '.' || g.tgname, f.proname FROM pg_trigger g "
        "JOIN pg_class c ON c.oid = g.tgrelid "
        "JOIN pg_namespace n ON n.oid = c.relnamespace "
        "JOIN pg_proc f ON f.oid = g.tgfoid "
        "WHERE n.nspname || '.' || g.tgname = ANY(%s)",
        (list(graded_pairs.REFRESH_TRIGGERS),)).fetchall())
    assert rows == {
        "drugref.curated_interaction_graded_pairs":
            "refresh_graded_pairs_for_key_at_commit",
        "drugref.curated_class_interaction_graded_pairs":
            "refresh_graded_pairs_for_key_at_commit",
        "drugref.class_expansion_policy_graded_pairs":
            "refresh_graded_pairs_at_commit",
        "drugref.ingest_run_graded_pairs": "refresh_graded_pairs_at_commit",
    }
