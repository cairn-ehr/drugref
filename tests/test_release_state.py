# tests/test_release_state.py
"""current_release view + release_line header copy (#258, db/081)."""

from drugref import release_state
from drugref.release_state import CurrentRelease, current_release, release_line

TAG = "r1"


def test_release_line_all_four_copies():
    assert release_line(CurrentRelease(TAG, None, "abc", ())) == "drugref release: r1"
    assert release_line(CurrentRelease(
        TAG, None, "abc", ("re-ingested", "newer migration"))) == (
        "drugref release: r1 (DRIFTED since publication: re-ingested, newer migration)"
        " — answers may differ from that release")
    assert release_line(None) == (
        "drugref release: none published (answers are not tied to a reconstructable release)")
    assert release_state.UNKNOWN_LINE == (
        "drugref release: unknown (database predates db/081)")


def _publish(conn, runs=(), build=True, head=None):
    """Manifest + build row. The build is stamped a day AHEAD so seeded rows (policy,
    migrations-time data) predate it, and 'later' events use LATER (two days ahead)."""
    conn.execute(
        "INSERT INTO drugref.release_manifest (release_tag, manifest_digest, row_count,"
        " upstream_releases, published_by, published_at) "
        "VALUES (%s, %s, 0, '[]'::jsonb, 'op', now())", (TAG, b"\x04" * 32))
    if build:
        if head is None:
            head = conn.execute(
                "SELECT max(filename) FROM drugref.schema_migration").fetchone()[0]
        conn.execute(
            "INSERT INTO drugref.release_build (release_tag, git_commit, ledger_head,"
            " run_ids, recorded_at) VALUES (%s, 'abc', %s, %s, now() + interval '1 day')",
            (TAG, head, list(runs)))


LATER = "now() + interval '2 days'"


def _run(conn, source, finished="now()"):
    return conn.execute(
        "INSERT INTO drugref.ingest_run (source, writer, upstream_release, "
        "source_checksum, started_at, finished_at) "
        f"VALUES (%s, 'unattributed', 'x', 'x', {finished}, {finished}) "
        "RETURNING ingest_run_id",
        (source,)).fetchone()[0]


def _old_run(conn, source):
    return _run(conn, source)


def test_no_manifest_is_none(conn):
    assert current_release(conn) is None


def test_a_fresh_release_has_no_drift(conn):
    _publish(conn, [_old_run(conn, "UNII")])
    cr = current_release(conn)
    assert (cr.release_tag, cr.git_commit, cr.drift_reasons) == (TAG, "abc", ())
    assert not cr.drifted


def test_a_pbs_run_after_publication_is_not_drift(conn):
    _publish(conn, [_old_run(conn, "UNII")])
    _run(conn, "PBS", LATER)
    assert current_release(conn).drift_reasons == ()


def test_a_carried_source_reingested_is_drift(conn):
    _publish(conn, [_old_run(conn, "UNII")])
    _run(conn, "UNII", LATER)
    assert current_release(conn).drift_reasons == ("re-ingested",)


def test_a_new_ruling_is_drift(conn, a_moiety, ingest_run_id):
    from tests.test_curated_overlay import _a_class, _assert_interaction
    _publish(conn)
    _assert_interaction(conn, a_moiety, _a_class(conn, ingest_run_id),
                        reviewed_at=conn.execute(f"SELECT {LATER}").fetchone()[0])
    assert current_release(conn).drift_reasons == ("re-curated",)


def test_a_new_class_ruling_is_drift(conn, ingest_run_id):
    """The probe on curated_class_interaction, the table this round is about, is
    checked on its own: a typo in its column would otherwise pass unnoticed."""
    from tests.test_curated_overlay import _a_class
    _publish(conn)
    conn.execute(
        "INSERT INTO drugref.curated_class_interaction (subject_class_uuid, "
        "object_class_uuid, relationship, applies, severity, evidence_grade, source, "
        f"reviewed_by, reviewed_against, reviewed_at) VALUES (%s, %s, 'CI_MoA', true, "
        f"'major', 'established', 'DRUGREF', 't', 'x', {LATER})",
        (_a_class(conn, ingest_run_id), _a_class(conn, ingest_run_id)))
    assert current_release(conn).drift_reasons == ("re-curated",)


def test_a_policy_row_is_drift(conn):
    _publish(conn)
    conn.execute(
        "INSERT INTO drugref.class_expansion_policy (source, source_code, decision, "
        "class_name, rationale, reviewed_by, reviewed_against, reviewed_at) "
        f"VALUES ('MED-RT', 'N0000TEST', 'deny', 'c', 'r', 't', 'x', {LATER})")
    assert current_release(conn).drift_reasons == ("expansion policy changed",)


def test_a_ledger_mismatch_is_drift(conn):
    _publish(conn, head="000_old.sql")
    assert current_release(conn).drift_reasons == ("newer migration",)


def test_manifest_without_build_row_says_so(conn):
    _publish(conn, build=False)
    cr = current_release(conn)
    assert cr.git_commit is None
    assert cr.drift_reasons == ("no build record (published before db/081)",)
    # Controller ruling (#258): the view exists, so this is DRIFT, not "predates db/081".
    assert release_line(cr) == (
        "drugref release: r1 (DRIFTED since publication: no build record "
        "(published before db/081)) — answers may differ from that release")


def test_current_release_or_absent_distinguishes_all_three(conn):
    assert release_state.current_release_or_absent(conn) is None
    _publish(conn)
    assert release_state.current_release_or_absent(conn).release_tag == TAG
    conn.execute("DROP VIEW drugref.current_release")
    assert release_state.current_release_or_absent(conn) is release_state.VIEW_ABSENT
