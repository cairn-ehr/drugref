"""`drugref audit archive` verifies a release's archived inputs (#258). DB-gated."""
import json

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
    monkeypatch.setenv(archive.ENV_VAR, str(root))
    return root, sums


def test_a_complete_archive_exits_0(conn, tmp_path, monkeypatch, capsys):
    _archived_release(tmp_path, monkeypatch)
    assert _run_cli(conn, "--release", TAG) == 0
    assert capsys.readouterr().out.strip() == (
        f"archive complete for {TAG}: 2 runs, 2 blobs verified")


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
