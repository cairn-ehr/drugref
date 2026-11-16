"""The content-addressed input archive (#258): files only, no database."""
import json
from importlib import resources

from drugref import archive
from drugref.ingest.checksum import checksum


def _files(tmp_path, n):
    paths = []
    for i in range(n):
        p = tmp_path / f"in{i}.txt"
        p.write_text(f"payload {i} é")
        paths.append(p)
    return paths


def test_combined_digest_equals_checksum_for_one_and_three_files(tmp_path):
    for n in (1, 3):
        paths = _files(tmp_path, n)
        digest, files = archive.file_digests(*paths)
        assert digest == checksum(*paths)
        assert [f.name for f in files] == [p.name for p in paths]
        assert files[0].size == paths[0].stat().st_size


def test_a_traversable_input_is_archived(tmp_path):
    shipped = resources.files("drugref.data") / "spl_suppression_terms.txt"
    root = tmp_path / "arch"
    digest = archive.store_run(root, shipped)
    assert digest == checksum(str(shipped))
    record = json.loads(archive.run_record_path(root, digest).read_text())
    blob = archive.blob_path(root, record["files"][0]["sha256"])
    assert blob.read_bytes() == shipped.read_bytes()
    assert record["files"][0]["name"] == "spl_suppression_terms.txt"


def test_store_is_idempotent_and_dedupes(tmp_path):
    a, b, c = _files(tmp_path, 3)
    root = tmp_path / "arch"
    first = archive.store_run(root, a, b)
    assert archive.store_run(root, a, b) == first
    archive.store_run(root, a, c)  # shares `a` with the first run
    blobs = [p for p in (root / "blobs").rglob("*") if p.is_file()]
    assert len(blobs) == 3
    assert not list(root.rglob("*.tmp"))


def test_missing_runs_names_a_missing_record_and_a_missing_blob(tmp_path):
    a, b = _files(tmp_path, 2)
    root = tmp_path / "arch"
    digest = archive.store_run(root, a, b)
    assert archive.missing_runs(root, [digest]) == []
    assert archive.missing_runs(root, ["f" * 64]) == [
        archive.ArchiveProblem("f" * 64, "no run record")]
    _, files = archive.file_digests(a, b)
    archive.blob_path(root, files[1].sha256).unlink()
    assert archive.missing_runs(root, [digest]) == [
        archive.ArchiveProblem(digest, f"missing blob {files[1].sha256}")]


def test_verify_names_a_truncated_blob(tmp_path):
    (a,) = _files(tmp_path, 1)
    root = tmp_path / "arch"
    digest = archive.store_run(root, a)
    _, files = archive.file_digests(a)
    archive.blob_path(root, files[0].sha256).write_bytes(b"x")
    # same-size corruption is invisible without a re-hash; size mismatch is not
    blob = archive.blob_path(root, files[0].sha256)
    assert archive.missing_runs(root, [digest]) == [
        archive.ArchiveProblem(digest, f"truncated blob {files[0].sha256}")]
    blob.write_bytes(b"y" * files[0].size)
    assert archive.missing_runs(root, [digest]) == []  # no re-hash
    assert archive.verify_runs(root, [digest]) == [
        archive.ArchiveProblem(digest, f"corrupt blob {files[0].sha256}")]


def test_archive_root_is_none_when_unset(monkeypatch, tmp_path):
    monkeypatch.delenv(archive.ENV_VAR, raising=False)
    assert archive.archive_root() is None
    monkeypatch.setenv(archive.ENV_VAR, str(tmp_path))
    assert archive.archive_root() == tmp_path
