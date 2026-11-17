"""Content-addressed archive of every ingest run's input files (#258).

WHY. A release must be reconstructable: an audit of decision support has to be
able to say "these were the bytes the answer was built from". `ingest_run`
records only a checksum; this module keeps the bytes behind it.

LAYOUT (fixed; other modules rely on it)::

    <root>/blobs/sha256/<first 2 hex>/<64 hex>   one file per distinct content
    <root>/runs/<source_checksum>.json           which blobs a run read, in order
    <root>/releases/<release_tag>.json           a release's runs + code version
    <root>/releases/<release_tag>.curated.json   a release's curated rows

Blobs are keyed by their own SHA-256, so two runs sharing a file store it once.
A run is keyed by the combined digest `ingest.checksum.checksum` already puts in
`ingest_run.source_checksum`, so the database row leads straight to its files.

UNSET MEANS NO I/O: with `DRUGREF_ARCHIVE` unset, `archive_root()` is None and
callers skip the archive entirely, so tests and dev ingests are unchanged.
"""
import hashlib
import json
import os
import pathlib
import re
import tempfile
from collections.abc import Iterable
from dataclasses import dataclass
from importlib import resources

from drugref.ingest.checksum import CHUNK_BYTES

ENV_VAR = "DRUGREF_ARCHIVE"

# A path, or a file shipped inside the package (`importlib.resources`).
Input = str | pathlib.Path | resources.abc.Traversable


def archive_root() -> pathlib.Path | None:
    """The archive directory from the environment, or None when unset/empty.

    MUST BE ABSOLUTE: a relative value would resolve against whatever directory
    the command ran in, scattering one archive over several (#258). RuntimeError
    so `cli.main` prints it as one line with exit 2 (operator error), which `cli.main`
    also checks before any command runs.
    """
    value = os.environ.get(ENV_VAR)
    if not value:
        return None
    root = pathlib.Path(value)
    if not root.is_absolute():
        raise RuntimeError(f"{ENV_VAR} must be an absolute path, got {value!r}")
    return root


@dataclass(frozen=True)
class ArchivedFile:
    """One input as recorded in a run record."""
    name: str
    size: int
    sha256: str


@dataclass(frozen=True)
class ArchiveProblem:
    """One thing wrong with the archive for a run; `what` says which."""
    source_checksum: str
    what: str


def _as_traversable(item: Input) -> resources.abc.Traversable:
    # str -> Path; Path and Traversable both offer .name and .open("rb").
    return pathlib.Path(item) if isinstance(item, str) else item


def file_digests(*inputs: Input) -> tuple[str, list[ArchivedFile]]:
    """(combined digest, per-file records) from ONE read pass over the inputs.

    The combined digest feeds every file's bytes, in order, into one hasher, so
    it equals `ingest.checksum.checksum(*inputs)` exactly (pinned by test) and
    can be compared with `ingest_run.source_checksum`. Chunked reads keep memory
    bounded for the ~750 MB MeSH files, as `checksum` does.
    """
    combined = hashlib.sha256()
    files: list[ArchivedFile] = []
    for item in map(_as_traversable, inputs):
        one, size = hashlib.sha256(), 0
        with item.open("rb") as fh:
            for chunk in iter(lambda: fh.read(CHUNK_BYTES), b""):
                combined.update(chunk)
                one.update(chunk)
                size += len(chunk)
        files.append(ArchivedFile(item.name, size, one.hexdigest()))
    return combined.hexdigest(), files


_SHA256 = re.compile(r"[0-9a-f]{64}")


def require_sha256(value) -> str:
    """Return `value` if it is 64 lowercase hex, else raise ValueError.

    WHY: checksums come back out of JSON records and database rows, and become
    file names. A hand-edited "../../x" must be reported, never followed (#258).
    """
    if not isinstance(value, str) or not _SHA256.fullmatch(value):
        raise ValueError(f"not a lowercase 64-hex sha256: {value!r}")
    return value


def blob_path(root: pathlib.Path, sha256: str) -> pathlib.Path:
    """Where the blob with this content hash lives (sharded by 2 hex chars)."""
    require_sha256(sha256)
    return root / "blobs" / "sha256" / sha256[:2] / sha256


def run_record_path(root: pathlib.Path, source_checksum: str) -> pathlib.Path:
    """Where the run record for this combined digest lives."""
    require_sha256(source_checksum)
    return root / "runs" / f"{source_checksum}.json"


def check_release_tag(tag: str) -> None:
    """Raise ValueError if `tag` could name a file outside `<root>/releases/`.

    ONE home for the rule: `publish` (which writes these files) and
    `audit archive --release` (which reads them) must agree on it (#258).
    """
    if "/" in tag or "\\" in tag or ".." in tag:
        raise ValueError("release tag must not contain a path separator or '..'")


def release_record_path(root: pathlib.Path, tag: str) -> pathlib.Path:
    """`<root>/releases/<tag>.json`: the release's runs and code version."""
    check_release_tag(tag)
    return root / "releases" / f"{tag}.json"


def curated_export_path(root: pathlib.Path, tag: str) -> pathlib.Path:
    """`<root>/releases/<tag>.curated.json`: every curated row at publication."""
    check_release_tag(tag)
    return root / "releases" / f"{tag}.curated.json"


def _umask_file_mode() -> int:
    """The mode a plain `open(path, "w")` would give a new file under this umask."""
    umask = os.umask(0)    # reading the umask means setting it; restore at once
    os.umask(umask)
    return 0o666 & ~umask


def atomic_write(dest: pathlib.Path, fill, *, overwrite: bool = True) -> None:
    """Write `dest` through a UNIQUE temp file in its directory, then rename it in.

    `fill(fh)` writes the bytes to a binary file handle. The data is fsynced before
    the atomic rename and the directory after it, so neither a crash nor power loss
    leaves a half-written file under the final name. The temp name is
    unique (`tempfile.mkstemp`), so two ingests archiving the same MeSH file at
    once cannot clobber each other's temp. If `fill` raises, the temp is removed.

    `overwrite=False` makes the final step `os.link`, which raises
    `FileExistsError` if `dest` already exists: the existence check and the write
    are then ONE atomic step, so a racing writer cannot be clobbered (#262).
    `os.replace` (the default) silently replaces an existing `dest`.

    The file gets the mode the umask gives any new file, not mkstemp's 0600, so a
    second account (an auditor) can read the archive (#261).
    """
    dest.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=dest.parent, prefix=f".{dest.name}.", suffix=".tmp")
    try:
        os.fchmod(fd, _umask_file_mode())   # before any byte, so 0600 is never final
        with os.fdopen(fd, "wb") as out:
            fill(out)
            # Durable before it is visible: without fsync, power loss after the
            # rename can leave an empty file under the final name.
            out.flush()
            os.fsync(out.fileno())
        if overwrite:
            os.replace(tmp, dest)
        else:
            # shortcut: needs hard-link support (any POSIX local fs); on one without
            # it (FAT, some SMB mounts) this raises OSError, so publish fails loudly.
            os.link(tmp, dest)
            os.unlink(tmp)
    except BaseException:
        pathlib.Path(tmp).unlink(missing_ok=True)
        raise
    dir_fd = os.open(dest.parent, os.O_RDONLY)   # make the rename itself durable
    try:
        os.fsync(dir_fd)
    finally:
        os.close(dir_fd)


def _copy_blob(item: resources.abc.Traversable, dest: pathlib.Path,
               expected: ArchivedFile) -> None:
    """Copy `item` to `dest` unless a blob of the right size is already there.

    A blob of the WRONG size is not the content its name claims (a truncated
    copy, a hand edit), so it is replaced through the same verified path rather
    than skipped forever. The copy hashes what it writes: an input that changed
    since `file_digests` read it would otherwise land wrong bytes under a
    sha-named blob.
    """
    if dest.is_file() and dest.stat().st_size == expected.size:
        return

    def fill(out):
        written = hashlib.sha256()
        with item.open("rb") as src:
            for chunk in iter(lambda: src.read(CHUNK_BYTES), b""):
                written.update(chunk)
                out.write(chunk)
        if written.hexdigest() != expected.sha256:
            raise ValueError(f"{item.name} changed while being archived "
                             f"(expected sha256 {expected.sha256})")
    atomic_write(dest, fill)


def store_run(root: pathlib.Path, *inputs: Input) -> str:
    """Archive a run's inputs and its record; return the combined digest.

    Idempotent: blobs already present (at the right size) are not rewritten, and
    the record is rewritten with identical content.
    """
    digest, files = file_digests(*inputs)
    for item, rec in zip(map(_as_traversable, inputs), files):
        _copy_blob(item, blob_path(root, rec.sha256), rec)
    record = {"source_checksum": digest,
              "files": [{"name": f.name, "size": f.size, "sha256": f.sha256}
                        for f in files]}
    body = json.dumps(record, ensure_ascii=False, indent=2).encode("utf-8")
    atomic_write(run_record_path(root, digest), lambda fh: fh.write(body))
    return digest


def _check(root: pathlib.Path, checksums: Iterable[str], rehash: bool,
           seen: set[str] | None) -> list[ArchiveProblem]:
    problems: list[ArchiveProblem] = []
    for cs in checksums:
        try:
            record = run_record_path(root, cs)
        except ValueError:
            problems.append(ArchiveProblem(cs, "not a sha256 checksum"))
            continue
        if not record.is_file():
            problems.append(ArchiveProblem(cs, "no run record"))
            continue
        try:
            body = json.loads(record.read_text(encoding="utf-8"))
            files = [(require_sha256(f["sha256"]), f["size"]) for f in body["files"]]
            if not all(isinstance(size, int) for _, size in files):
                raise TypeError("size is not an integer")
            # A record that lists no files, or claims another run's checksum, can
            # never rebuild this run, however intact its blobs are.
            if not files or body["source_checksum"] != cs:
                raise ValueError("record does not describe this run")
        except (ValueError, KeyError, TypeError):
            # A hand-damaged record is exactly what the audit command reports (#258).
            problems.append(ArchiveProblem(cs, "malformed run record"))
            continue
        if seen is not None:
            seen.update(sha for sha, _ in files)
        blobs = [blob_path(root, sha) for sha, _ in files]
        missing = [sha for (sha, _), blob in zip(files, blobs) if not blob.is_file()]
        problems += [ArchiveProblem(cs, f"missing blob {sha}") for sha in missing]
        if missing:
            continue
        if not rehash:
            # Cheap check for publish: a stat, no re-hash.
            problems += [ArchiveProblem(cs, f"wrong-size blob {sha}")
                         for (sha, size), blob in zip(files, blobs)
                         if blob.stat().st_size != size]
            continue
        # ONE read pass gives each blob's own sha AND the combined digest, which
        # must equal the run's checksum (catches a record listing files out of order).
        combined, read = file_digests(*blobs)
        corrupt = [sha for (sha, _), got in zip(files, read) if got.sha256 != sha]
        # Re-ingest skips a blob already at the right size, so it cannot repair one.
        problems += [ArchiveProblem(cs, f"corrupt blob {sha} (delete it, then "
                                        "re-ingest the run to restore it)")
                     for sha in corrupt]
        if not corrupt and combined != cs:
            problems.append(ArchiveProblem(
                cs, "blobs do not reproduce the run's source_checksum"))
    return problems


def missing_runs(root: pathlib.Path,
                 checksums: Iterable[str]) -> list[ArchiveProblem]:
    """Missing/malformed records, non-sha checksums, missing and wrong-size blobs;
    cheap (stat, no re-hash)."""
    return _check(root, checksums, rehash=False, seen=None)


def verify_runs(root: pathlib.Path, checksums: Iterable[str], *,
                seen: set[str] | None = None) -> list[ArchiveProblem]:
    """As `missing_runs`, plus re-hash every blob. The audit command uses it.

    `seen`, when given, is filled with every blob sha the readable run records
    name, so the caller can count them without parsing the records again.
    """
    return _check(root, checksums, rehash=True, seen=seen)
