"""`drugref audit archive` verifies a release's archived inputs (#258). DB-gated."""
import contextlib
import json
import os

import pytest

from drugref import archive, cli
from tests.test_release_state import _old_run, _publish

TAG = "r1"


def _run_cli(conn, *extra):
    args = cli.build_parser().parse_args(["audit", "archive", *extra])
    return args.handler(conn, args)


def _archived_release(tmp_path, monkeypatch, bodies=("x", "y")):
    """An archive holding one run per body, and a release record naming them."""
    root = tmp_path / "arch"
    sums = []
    for i, body in enumerate(bodies):
        f = tmp_path / f"in{i}.txt"
        f.write_text(body)
        sums.append(archive.store_run(root, f))
    record = root / "releases" / f"{TAG}.json"
    record.parent.mkdir(parents=True, exist_ok=True)
    record.write_text(json.dumps(
        {"release_tag": TAG, "git_commit": "abc", "ledger_head": "h",
         "runs": [{"source_checksum": s} for s in sums]}))
    (root / "releases" / f"{TAG}.curated.json").write_text("{}")
    monkeypatch.setenv(archive.ENV_VAR, str(root))
    return root, sums


def test_a_complete_archive_exits_0(conn, tmp_path, monkeypatch, capsys):
    _archived_release(tmp_path, monkeypatch)
    assert _run_cli(conn, "--release", TAG) == 0
    assert capsys.readouterr().out.strip() == (
        f"archive complete for {TAG}: 2 runs, 2 blobs verified")


def test_a_tag_this_database_never_published_gets_a_note(
        conn, tmp_path, monkeypatch, capsys):
    """Files alone do not prove a publish: they may be debris of one that never
    committed. Still checked (the DB may be the thing that was lost), but flagged."""
    _archived_release(tmp_path, monkeypatch)
    assert _run_cli(conn, "--release", TAG) == 0
    assert f"{TAG} has no release_build row in this database" in capsys.readouterr().err
    _publish(conn, [_old_run(conn, "UNII")])
    assert _run_cli(conn, "--release", TAG) == 0
    assert "release_build" not in capsys.readouterr().err


def test_the_default_release_is_the_current_one(conn, tmp_path, monkeypatch, capsys):
    _archived_release(tmp_path, monkeypatch)
    _publish(conn, [_old_run(conn, "UNII")])
    assert _run_cli(conn) == 0
    assert f"archive complete for {TAG}" in capsys.readouterr().out


def test_a_deleted_blob_exits_1_and_names_its_sha(conn, tmp_path, monkeypatch, capsys):
    root, sums = _archived_release(tmp_path, monkeypatch)
    sha = json.loads(archive.run_record_path(root, sums[0]).read_text())["files"][0]["sha256"]
    archive.blob_path(root, sha).unlink()
    assert _run_cli(conn, "--release", TAG) == 1
    assert sha in capsys.readouterr().out


@pytest.mark.parametrize("command", [
    ["audit", "archive", "--release", TAG],
    # A file that does not exist: refused at startup, before the ingest opens it,
    # rather than after a parse that can take minutes for MeSH.
    ["ingest", "gsrs", "--release", "x", "--dump", "/nonexistent/dump.gsrs"]])
def test_a_relative_archive_env_exits_2_through_main(conn, monkeypatch, capsys, command):
    """A config typo is operator error (2), never "the archive has problems" (1)."""
    monkeypatch.setattr(cli.db, "connect", lambda dsn: contextlib.nullcontext(conn))
    monkeypatch.setenv(archive.ENV_VAR, "relative/arch")
    assert cli.main(["--dsn", "x", *command]) == 2
    assert archive.ENV_VAR in capsys.readouterr().err


def test_unset_env_exits_2(conn, capsys):
    assert _run_cli(conn, "--release", TAG) == 2
    assert archive.ENV_VAR in capsys.readouterr().err


def test_a_missing_release_record_exits_2(conn, tmp_path, monkeypatch, capsys):
    monkeypatch.setenv(archive.ENV_VAR, str(tmp_path / "empty"))
    assert _run_cli(conn, "--release", "nope") == 2
    assert "nope" in capsys.readouterr().err


def test_no_release_published_and_none_named_exits_2(conn, tmp_path, monkeypatch, capsys):
    monkeypatch.setenv(archive.ENV_VAR, str(tmp_path))
    assert _run_cli(conn) == 2


@pytest.mark.parametrize("body", [None, "{not json"])
def test_a_missing_or_unparseable_curated_export_exits_1(
        conn, tmp_path, monkeypatch, capsys, body):
    """The export is the only copy of the curated inputs if the DB is lost (spec 6.2)."""
    root, _ = _archived_release(tmp_path, monkeypatch)
    export = root / "releases" / f"{TAG}.curated.json"
    if body is None:
        export.unlink()
    else:
        export.write_text(body)
    assert _run_cli(conn, "--release", TAG) == 1
    out = capsys.readouterr().out
    assert str(export) in out and "archive complete" not in out


@pytest.mark.parametrize("body", [
    "{not json", '{"release_tag": "r1"}', '{"runs": 5}',
    '{"runs": [{"source_checksum": "../../etc/passwd"}]}'])
def test_a_malformed_release_record_exits_2(conn, tmp_path, monkeypatch, capsys, body):
    """Exit 1 means "problems found in the archive"; a record drugref cannot read at
    all must not look like that, nor like a crash."""
    root, _ = _archived_release(tmp_path, monkeypatch)
    record = root / "releases" / f"{TAG}.json"
    record.write_text(body)
    assert _run_cli(conn, "--release", TAG) == 2
    assert f"malformed release record {record}" in capsys.readouterr().err


@pytest.mark.skipif(os.geteuid() == 0, reason="root reads a 0000 file anyway")
def test_an_unreadable_blob_exits_2_with_one_line(conn, tmp_path, monkeypatch, capsys):
    """Could not check is not "problems found" (1), and never a traceback (#261)."""
    root, sums = _archived_release(tmp_path, monkeypatch)
    sha = json.loads(archive.run_record_path(root, sums[0]).read_text())["files"][0]["sha256"]
    blob = archive.blob_path(root, sha)
    blob.chmod(0)
    try:
        assert _run_cli(conn, "--release", TAG) == 2
    finally:
        blob.chmod(0o644)
    assert capsys.readouterr().err.splitlines()[-1] == f"drugref: {blob}: Permission denied"


@pytest.mark.parametrize("tag", ["../x", "a/b", "a\\b"])
def test_a_path_like_release_tag_exits_2(conn, tmp_path, monkeypatch, capsys, tag):
    monkeypatch.setenv(archive.ENV_VAR, str(tmp_path))
    assert _run_cli(conn, "--release", tag) == 2
    assert "path separator" in capsys.readouterr().err


def test_a_database_predating_db081_says_so(conn, tmp_path, monkeypatch, capsys):
    monkeypatch.setenv(archive.ENV_VAR, str(tmp_path))
    conn.execute("DROP VIEW drugref.current_release")
    assert _run_cli(conn) == 2
    err = capsys.readouterr().err
    assert "predates db/081" in err and "no release is published" not in err
