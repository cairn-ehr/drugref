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
    assert set(export) == {*releases._CURATED_KINDS, "class_expansion_policy",
                           "curated_class_interaction_reach"}
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


def test_git_commit_is_40_hex_or_unknown_and_falls_back(monkeypatch):
    import re
    import subprocess
    assert re.fullmatch(r"[0-9a-f]{40}(-dirty)?|unknown", release_archive.git_commit())

    def boom(*a, **k):
        raise OSError("no git")
    monkeypatch.setattr(subprocess, "run", boom)
    assert release_archive.git_commit() == "unknown"


def fake_git(monkeypatch, status="", status_fails=False, foreign_repo=False):
    """Stand in for `git`: HEAD is 'a'*40; `status --porcelain` prints `status`;
    `foreign_repo`: `ls-files` says this package's file is not tracked there."""
    import subprocess

    def run(argv, **kw):
        assert isinstance(argv, list) and kw["cwd"] and kw["timeout"]
        if argv[1:] == ["rev-parse", "HEAD"]:
            return subprocess.CompletedProcess(argv, 0, "a" * 40 + "\n", "")
        if argv[1:3] == ["ls-files", "--error-unmatch"]:
            if foreign_repo:
                raise subprocess.CalledProcessError(1, argv)
            return subprocess.CompletedProcess(argv, 0, argv[3] + "\n", "")
        assert argv[1:] == ["status", "--porcelain"]
        if status_fails:
            raise subprocess.TimeoutExpired(argv, kw["timeout"])
        return subprocess.CompletedProcess(argv, 0, status, "")
    monkeypatch.setattr(subprocess, "run", run)


def test_git_commit_marks_a_dirty_tree(monkeypatch):
    fake_git(monkeypatch)
    assert release_archive.git_commit() == "a" * 40
    fake_git(monkeypatch, status=" M src/drugref/archive.py\n")
    assert release_archive.git_commit() == "a" * 40 + "-dirty"
    fake_git(monkeypatch, status_fails=True)
    assert release_archive.git_commit() == "unknown"


def test_git_commit_is_unknown_inside_a_foreign_repo(monkeypatch, caplog):
    """A non-editable install in a venv inside ANOTHER checkout: git walks up and finds
    that repo, whose HEAD is not drugref's code version."""
    fake_git(monkeypatch, foreign_repo=True)
    assert release_archive.git_commit() == "unknown"
    assert "git provenance unavailable" in caplog.text


def _refused(conn, key, root, tag=TAG):
    with pytest.raises(release_archive.ArchiveIncomplete) as exc:
        release_archive.publish_archived(
            conn, root=root, release_tag=tag, published_by="op", **key)
    assert conn.execute("SELECT 1 FROM drugref.release_manifest "
                        "WHERE release_tag = %s", (tag,)).fetchone() is None
    return exc.value


def test_publish_refuses_when_a_blob_is_unlinked(conn, key, tmp_path):
    root = tmp_path / "arch"
    cs = _archive_run(root, tmp_path, "in.txt", "hello")
    _run(conn, checksum=cs)
    for f in (root / "blobs").rglob("*"):
        if f.is_file():
            f.unlink()
    assert "missing blob" in _refused(conn, key, root).problems[0].what


def test_publish_refuses_when_a_blob_is_truncated(conn, key, tmp_path):
    root = tmp_path / "arch"
    cs = _archive_run(root, tmp_path, "in.txt", "hello world")
    _run(conn, checksum=cs)
    for f in (root / "blobs").rglob("*"):
        if f.is_file():
            f.write_bytes(b"h")
    assert "wrong-size blob" in _refused(conn, key, root).problems[0].what


def test_publish_never_overwrites_an_existing_release_file(conn, key, tmp_path):
    root = tmp_path / "arch"
    (root / "releases").mkdir(parents=True)
    (root / "releases" / f"{TAG}.json").write_text("{}")
    assert str(root / "releases" / f"{TAG}.json") in _refused(
        conn, key, root).problems[0].what
    assert (root / "releases" / f"{TAG}.json").read_text() == "{}"


def test_a_release_file_left_by_an_uncommitted_publish_says_so(conn, key, tmp_path):
    """The tag is in no release_manifest row, so the file is debris from a publish whose
    commit failed; the refusal says how to free the tag (drugref never deletes it)."""
    root = tmp_path / "arch"
    (root / "releases").mkdir(parents=True)
    (root / "releases" / f"{TAG}.curated.json").write_text("{}")
    what = _refused(conn, key, root).problems[0].what
    assert "did not commit" in what and "by hand" in what


def test_an_unarchived_run_is_named_with_the_fix(conn, key, tmp_path):
    run_id = _run(conn, source="MeSH", checksum="never-archived")
    what = _refused(conn, key, tmp_path / "arch").problems[0].what
    assert f"run {run_id} (MeSH/unii_run, release v)" in what
    assert "re-ingest" in what and archive.ENV_VAR in what


def test_a_fresh_publish_reads_as_not_drifted_until_a_re_ingest(conn, key):
    """End to end: publish's own build row, not a hand-made one, starts clean."""
    from drugref import release_state
    conn.execute(
        "INSERT INTO drugref.ingest_run (source, upstream_release, source_checksum,"
        " writer, started_at, finished_at) VALUES ('UNII', 'v', 'cs-old', 'unii_run',"
        " now() - interval '2 hours', now() - interval '1 hour')")
    _publish(conn, key, None)
    assert release_state.current_release(conn).drift_reasons == ()
    conn.execute(                                      # finishes after the publish
        "INSERT INTO drugref.ingest_run (source, upstream_release, source_checksum,"
        " writer, finished_at) VALUES ('UNII', 'v2', 'cs-new', 'unii_run',"
        " now() + interval '1 day')")
    assert release_state.current_release(conn).drift_reasons == ("re-ingested",)


@pytest.mark.parametrize("tag", ["../x", "a/b", "a\\b"])
def test_publish_rejects_a_path_like_release_tag(conn, key, tmp_path, tag):
    assert "path separator" in _refused(conn, key, tmp_path / "arch", tag).problems[0].what


def test_current_release_view_reads_every_curated_table(conn):
    read = {r[0] for r in conn.execute(
        "SELECT table_name FROM information_schema.view_table_usage "
        "WHERE view_name = 'current_release'")}
    assert set(releases._CURATED_KINDS) <= read


def test_release_files_are_written_through_a_unique_temp(conn, key, tmp_path, monkeypatch):
    """A crash mid-write must never leave a half file under the final name."""
    import tempfile
    names = []
    real = tempfile.mkstemp

    def spy(*args, **kw):
        fd, name = real(*args, **kw)
        names.append(name)
        return fd, name
    monkeypatch.setattr(tempfile, "mkstemp", spy)
    _publish(conn, key, tmp_path / "arch")
    assert len([n for n in names if "/releases/" in n]) == 2
    assert sorted(p.name for p in (tmp_path / "arch" / "releases").iterdir()) == [
        f"{TAG}.curated.json", f"{TAG}.json"]


def test_curated_export_covers_the_reach_table():
    assert "curated_class_interaction_reach" in release_archive._EXPORT_TABLES
