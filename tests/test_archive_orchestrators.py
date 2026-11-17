"""Every orchestrator archives its inputs through `archived_checksum` (#258)."""
import ast
import pathlib

from drugref import archive
from drugref.ingest import run
from drugref.ingest.checksum import checksum, archived_checksum
from tests.test_ingest_run import AL, FIX, XW, _clean_ingest_tables  # noqa: F401

INGEST = pathlib.Path(__file__).parent.parent / "src" / "drugref" / "ingest"


def test_no_orchestrator_calls_bare_checksum():
    """Only checksum.py may call `checksum(...)`; the rest go via the archive."""
    offenders = []
    for path in sorted(INGEST.glob("*.py")):
        if path.name == "checksum.py":
            continue
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                    and node.func.id == "checksum"):
                offenders.append(f"{path.name}:{node.lineno}")
    assert offenders == []


def test_archived_checksum_is_plain_checksum_when_unset(monkeypatch):
    monkeypatch.delenv(archive.ENV_VAR, raising=False)
    assert archived_checksum(FIX, XW) == checksum(FIX, XW)


def test_an_ingest_with_the_archive_set_writes_its_run_record(
        conn, tmp_path, monkeypatch):
    monkeypatch.setenv(archive.ENV_VAR, str(tmp_path))
    run.ingest_unii(conn, unii_path=FIX, crosswalk_path=XW,
                    allowlist_path=AL, upstream_release="2026-07")
    sc = conn.execute(
        "SELECT source_checksum FROM drugref.ingest_run").fetchone()[0]
    assert archive.run_record_path(tmp_path, sc).is_file()
    assert archive.missing_runs(tmp_path, [sc]) == []


def test_copy_blob_refuses_bytes_that_do_not_match_the_expected_hash(tmp_path):
    src = tmp_path / "in.bin"
    src.write_bytes(b"changed between passes")
    dest = tmp_path / "blobs" / "ab" / "abc"
    try:
        archive._copy_blob(src, dest, archive.ArchivedFile(
            "in.bin", src.stat().st_size, "0" * 64))
    except ValueError as e:
        assert "in.bin" in str(e)
    else:
        raise AssertionError("expected ValueError")
    assert not dest.exists()
    assert list(dest.parent.iterdir()) == []  # the unique temp is gone too
