"""The content-addressed input archive (#258): files only, no database."""
import json
from importlib import resources

import pytest

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


def test_a_malformed_run_record_is_a_problem_not_a_crash(tmp_path):
    """`drugref audit archive` exists to report a hand-damaged archive (#258)."""
    root = tmp_path / "arch"
    (root / "runs").mkdir(parents=True)
    bad, nofiles, badsha = "a" * 64, "b" * 64, "c" * 64
    archive.run_record_path(root, bad).write_text("{not json")
    archive.run_record_path(root, nofiles).write_text('{"source_checksum": "x"}')
    # A sha that is not 64 lowercase hex must never become part of a path.
    archive.run_record_path(root, badsha).write_text(
        '{"files": [{"name": "f", "size": 1, "sha256": "../../escape"}]}')
    problems = archive.verify_runs(root, [bad, nofiles, badsha])
    assert [(p.source_checksum, p.what) for p in problems] == [
        (bad, "malformed run record"), (nofiles, "malformed run record"),
        (badsha, "malformed run record")]


def test_a_non_sha256_checksum_is_a_problem_and_builds_no_path(tmp_path):
    assert archive.missing_runs(tmp_path, ["../x", "F" * 64]) == [
        archive.ArchiveProblem("../x", "not a sha256 checksum"),
        archive.ArchiveProblem("F" * 64, "not a sha256 checksum")]
    with pytest.raises(ValueError):
        archive.blob_path(tmp_path, "../x")


def test_verify_collects_the_blobs_it_checked(tmp_path):
    a, b = _files(tmp_path, 2)
    root = tmp_path / "arch"
    digest = archive.store_run(root, a, b)
    seen: set[str] = set()
    assert archive.verify_runs(root, [digest], seen=seen) == []
    assert seen == {f.sha256 for f in archive.file_digests(a, b)[1]}


@pytest.mark.parametrize("tag", ["../x", "a/b", "a\\b", ".."])
def test_a_path_like_release_tag_is_rejected(tmp_path, tag):
    with pytest.raises(ValueError, match="path separator"):
        archive.check_release_tag(tag)
    with pytest.raises(ValueError):
        archive.release_record_path(tmp_path, tag)
    assert archive.curated_export_path(tmp_path, "r1") == (
        tmp_path / "releases" / "r1.curated.json")


def test_a_relative_archive_root_is_rejected(monkeypatch):
    """A relative path would land the archive wherever the command happened to run."""
    monkeypatch.setenv(archive.ENV_VAR, "relative/arch")
    with pytest.raises(ValueError, match=archive.ENV_VAR):
        archive.archive_root()


def test_re_storing_repairs_a_wrong_size_blob(tmp_path):
    (a,) = _files(tmp_path, 1)
    root = tmp_path / "arch"
    digest = archive.store_run(root, a)
    blob = archive.blob_path(root, archive.file_digests(a)[1][0].sha256)
    blob.write_bytes(b"x")
    archive.store_run(root, a)
    assert blob.read_bytes() == a.read_bytes()
    assert archive.verify_runs(root, [digest]) == []


def test_a_changed_input_leaves_no_temp_file_and_no_blob(tmp_path, monkeypatch):
    (a,) = _files(tmp_path, 1)
    root = tmp_path / "arch"
    real = archive.file_digests
    monkeypatch.setattr(archive, "file_digests", lambda *i: (
        real(*i)[0], [archive.ArchivedFile(a.name, a.stat().st_size, "0" * 64)]))
    with pytest.raises(ValueError, match="changed while being archived"):
        archive.store_run(root, a)
    assert [p for p in root.rglob("*") if p.is_file()] == []


def test_temp_files_are_unique_per_write(tmp_path, monkeypatch):
    """Two ingests sharing an input (MeSH descriptors) must not share a temp name."""
    import tempfile
    (a,) = _files(tmp_path, 1)
    names = []
    real = tempfile.mkstemp

    def spy(*args, **kw):
        fd, name = real(*args, **kw)
        names.append(name)
        return fd, name
    monkeypatch.setattr(tempfile, "mkstemp", spy)
    archive.store_run(tmp_path / "arch", a)
    archive.store_run(tmp_path / "arch2", a)
    assert len(names) == 4 and len(set(names)) == 4
