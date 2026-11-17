"""The content-addressed input archive (#258): files only, no database."""
import json
import os
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
        archive.ArchiveProblem(digest, f"wrong-size blob {files[0].sha256}")]
    blob.write_bytes(b"y" * files[0].size)
    assert archive.missing_runs(root, [digest]) == []  # no re-hash
    problems = archive.verify_runs(root, [digest])
    assert [p.what.split(" (")[0] for p in problems] == [
        f"corrupt blob {files[0].sha256}"]
    # Re-ingest skips a right-size blob, so the operator must be told to delete it.
    assert "delete" in problems[0].what and "re-ingest" in problems[0].what


def test_a_record_must_reproduce_its_runs_checksum(tmp_path):
    """Each blob can be intact while the RECORD lies: no files, files out of order, or
    a source_checksum that is not its own name. The audit must still say so."""
    a, b = _files(tmp_path, 2)
    root = tmp_path / "arch"
    digest = archive.store_run(root, a, b)
    path = archive.run_record_path(root, digest)
    good = json.loads(path.read_text())

    path.write_text(json.dumps({**good, "files": []}))
    assert archive.missing_runs(root, [digest]) == [
        archive.ArchiveProblem(digest, "malformed run record")]
    path.write_text(json.dumps({**good, "source_checksum": "d" * 64}))
    assert archive.missing_runs(root, [digest]) == [
        archive.ArchiveProblem(digest, "malformed run record")]
    path.write_text(json.dumps({**good, "files": good["files"][::-1]}))
    assert archive.missing_runs(root, [digest]) == []      # cheap check: sizes only
    assert archive.verify_runs(root, [digest]) == [archive.ArchiveProblem(
        digest, "blobs do not reproduce the run's source_checksum")]


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
    """A relative path would land the archive wherever the command happened to run.

    RuntimeError, not ValueError: `cli.main` renders RuntimeError as one line and exit
    2 (operator error); a ValueError escaped as a traceback with exit 1, which
    `audit archive` and `publish` both use to mean "the archive has problems".
    """
    monkeypatch.setenv(archive.ENV_VAR, "relative/arch")
    with pytest.raises(RuntimeError, match=archive.ENV_VAR):
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


def test_archive_files_get_the_umask_mode_not_mkstemps_0600(tmp_path):
    """mkstemp always creates 0600, so an auditor under a second account could not
    read the archive; nothing in it is secret, so honour the umask instead (#261)."""
    import os
    import stat
    (a,) = _files(tmp_path, 1)
    root = tmp_path / "arch"
    old = os.umask(0o022)
    try:
        digest = archive.store_run(root, a)
    finally:
        os.umask(old)
    blob = archive.blob_path(root, archive.file_digests(a)[1][0].sha256)
    for path in (blob, archive.run_record_path(root, digest)):
        assert stat.S_IMODE(path.stat().st_mode) == 0o644


def test_a_no_clobber_write_refuses_an_existing_file_and_leaves_no_temp(tmp_path):
    """Release files are never overwritten (#262): the check and the write are one step."""
    dest = tmp_path / "r1.json"
    dest.write_text("first")
    with pytest.raises(FileExistsError):
        archive.atomic_write(dest, lambda fh: fh.write(b"second"), overwrite=False)
    assert dest.read_text() == "first"
    assert list(tmp_path.iterdir()) == [dest]
    fresh = tmp_path / "r2.json"
    archive.atomic_write(fresh, lambda fh: fh.write(b"new"), overwrite=False)
    assert fresh.read_text() == "new"


def _unsearchable(path):
    """Context: `path` is a directory this process cannot search (mode 0600)."""
    import contextlib

    @contextlib.contextmanager
    def ctx():
        path.chmod(0o600)
        try:
            yield
        finally:
            path.chmod(0o755)
    return ctx()


@pytest.mark.skipif(os.geteuid() == 0, reason="root searches a 0600 dir anyway")
@pytest.mark.parametrize("check", [archive.missing_runs, archive.verify_runs])
@pytest.mark.parametrize("where", ["runs", "blob shard"])
def test_an_unsearchable_dir_raises_rather_than_reading_as_missing(
        tmp_path, check, where):
    """Unreadable is not missing, on EVERY Python (#268): `is_file()` raises here on
    3.12 but returns False on 3.14, which reported an intact run as "no run record"
    / "missing blob" and told publish to re-ingest bytes that are there."""
    (a,) = _files(tmp_path, 1)
    root = tmp_path / "arch"
    digest = archive.store_run(root, a)
    blob = archive.blob_path(root, archive.file_digests(a)[1][0].sha256)
    target = root / "runs" if where == "runs" else blob.parent
    with _unsearchable(target), pytest.raises(PermissionError):
        check(root, [digest])


@pytest.mark.parametrize("check", [archive.missing_runs, archive.verify_runs])
def test_a_directory_where_a_record_or_blob_should_be_is_missing(tmp_path, check):
    """Not a regular file is still "missing" (what `is_file()` used to say), not an
    unreadable-file error."""
    (a,) = _files(tmp_path, 1)
    root = tmp_path / "arch"
    digest = archive.store_run(root, a)
    sha = archive.file_digests(a)[1][0].sha256
    blob = archive.blob_path(root, sha)
    blob.unlink()
    blob.mkdir()
    assert check(root, [digest]) == [archive.ArchiveProblem(digest, f"missing blob {sha}")]
    record = archive.run_record_path(root, "e" * 64)
    record.mkdir()
    assert check(root, ["e" * 64]) == [archive.ArchiveProblem("e" * 64, "no run record")]
