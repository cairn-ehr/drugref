"""publish writes the release record, curated export and build row (#258). DB-gated."""
import json

import pytest

from drugref import archive, curation, release_archive, releases, signing

TAG = "2026.11.16-audit"


@pytest.fixture
def key():
    kp = signing.generate_keypair()
    return {"private_key": kp.private_key,
            "key_fingerprint": signing.fingerprint(kp.public_key)}


def _run(conn, source="UNII", checksum="cs-a", finished=True):
    return conn.execute(
        "INSERT INTO drugref.ingest_run (source, upstream_release, source_checksum, writer,"
        " finished_at) VALUES (%s, 'v', %s, 'unii_run', "
        + ("now() + interval '1 second'" if finished else "NULL") + ") RETURNING ingest_run_id",
        (source, checksum)).fetchone()[0]


def _archive_run(root, tmp_path, name, body):
    f = tmp_path / name
    f.write_text(body)
    return archive.store_run(root, f)


def _publish(conn, key, root):
    return release_archive.publish_archived(
        conn, root=root, release_tag=TAG, published_by="op", **key)


def test_publish_refuses_when_a_run_is_unarchived(conn, key, tmp_path):
    _run(conn, checksum="never-archived")
    with pytest.raises(release_archive.ArchiveIncomplete) as exc:
        _publish(conn, key, tmp_path / "arch")
    assert any(p.source_checksum == "never-archived" for p in exc.value.problems)
    assert conn.execute("SELECT 1 FROM drugref.release_manifest "
                        "WHERE release_tag = %s", (TAG,)).fetchone() is None


def test_publish_writes_record_export_and_build_row(conn, key, tmp_path, a_graded_rule):
    curation.record_interaction_judgement(
        conn, a_graded_rule["subject"], a_graded_rule["class"], "CI_MoA", True,
        severity="major", evidence_grade="established", reviewed_by="é curator",
        reviewed_against="MED-RT 2026.07.06")
    root = tmp_path / "arch"
    cs = _archive_run(root, tmp_path, "in.txt", "x")
    run_id = _run(conn, checksum=cs)
    _publish(conn, key, root)

    record = json.loads((root / "releases" / f"{TAG}.json").read_text(encoding="utf-8"))
    assert record["release_tag"] == TAG
    assert record["ledger_head"] == conn.execute(
        "SELECT max(filename) FROM drugref.schema_migration").fetchone()[0]
    assert run_id in [r["ingest_run_id"] for r in record["runs"]]
    export = json.loads(
        (root / "releases" / f"{TAG}.curated.json").read_text(encoding="utf-8"))
    assert set(export) == {*releases._CURATED_KINDS, "class_expansion_policy"}
    assert any(r["reviewed_by"] == "é curator" for r in export["curated_interaction"])
    build = conn.execute("SELECT git_commit, ledger_head, run_ids FROM "
                         "drugref.release_build WHERE release_tag = %s", (TAG,)).fetchone()
    assert build[1] == record["ledger_head"]
    all_finished = [r[0] for r in conn.execute(
        "SELECT ingest_run_id FROM drugref.ingest_run WHERE finished_at IS NOT NULL")]
    assert run_id in build[2] and set(build[2]) == set(all_finished)


def test_publish_without_archive_still_writes_build_row(conn, key):
    _run(conn)
    _publish(conn, key, None)
    run_ids = conn.execute("SELECT run_ids FROM drugref.release_build "
                           "WHERE release_tag = %s", (TAG,)).fetchone()[0]
    assert run_ids


def test_curated_export_includes_superseded_rows(conn, a_graded_rule):
    args = (conn, a_graded_rule["subject"], a_graded_rule["class"], "CI_MoA", True)
    kw = dict(evidence_grade="established", reviewed_by="c",
              reviewed_against="MED-RT 2026.07.06")
    curation.record_interaction_judgement(*args, severity="major", **kw)
    curation.record_interaction_judgement(*args, severity="minor", **kw)
    rows = release_archive.curated_export(conn)["curated_interaction"]
    mine = [r for r in rows if r["subject_moiety_uuid"] == str(a_graded_rule["subject"])]
    assert len(mine) == 2 and any(r["superseded_by"] is not None for r in mine)


def test_release_record_lists_every_run_not_only_loaded(conn):
    a, b = _run(conn, checksum="one"), _run(conn, checksum="two")
    ids = [r["ingest_run_id"] for r in release_archive.finished_runs(conn)]
    assert a in ids and b in ids and ids == sorted(ids)
    open_id = _run(conn, checksum="x", finished=False)
    assert open_id not in [r["ingest_run_id"]
                           for r in release_archive.finished_runs(conn)]


def test_git_commit_is_a_string():
    assert release_archive.git_commit()


def test_current_release_view_reads_every_curated_table(conn):
    read = {r[0] for r in conn.execute(
        "SELECT table_name FROM information_schema.view_table_usage "
        "WHERE view_name = 'current_release'")}
    assert set(releases._CURATED_KINDS) <= read
