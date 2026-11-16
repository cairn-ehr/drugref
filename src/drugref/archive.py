"""Content-addressed archive of every ingest run's input files (#258).

WHY. A release must be reconstructable: an audit of decision support has to be
able to say "these were the bytes the answer was built from". `ingest_run`
records only a checksum; this module keeps the bytes behind it.

LAYOUT (fixed; other modules rely on it)::

    <root>/blobs/sha256/<first 2 hex>/<64 hex>   one file per distinct content
    <root>/runs/<source_checksum>.json           which blobs a run read, in order

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
from collections.abc import Iterable
from dataclasses import dataclass
from importlib import resources

from drugref.ingest.checksum import CHUNK_BYTES

ENV_VAR = "DRUGREF_ARCHIVE"

# A path, or a file shipped inside the package (`importlib.resources`).
Input = str | pathlib.Path | resources.abc.Traversable


def archive_root() -> pathlib.Path | None:
    """The archive directory from the environment, or None when unset/empty."""
    value = os.environ.get(ENV_VAR)
    return pathlib.Path(value) if value else None


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


def blob_path(root: pathlib.Path, sha256: str) -> pathlib.Path:
    """Where the blob with this content hash lives (sharded by 2 hex chars)."""
    return root / "blobs" / "sha256" / sha256[:2] / sha256


def run_record_path(root: pathlib.Path, source_checksum: str) -> pathlib.Path:
    """Where the run record for this combined digest lives."""
    return root / "runs" / f"{source_checksum}.json"


def _copy_blob(item: resources.abc.Traversable, dest: pathlib.Path,
               expected_sha256: str) -> None:
    """Copy `item` to `dest` unless present, via `<dest>.tmp` + `os.replace`.

    The rename is atomic, so a crash leaves at worst a stray .tmp and never a
    half-written file under the final (content-addressed) name. The copy hashes
    what it writes: an input that changed since `file_digests` read it would
    otherwise land wrong bytes under a sha-named blob that is never repaired.
    """
    if dest.is_file():
        return
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.name + ".tmp")
    written = hashlib.sha256()
    with item.open("rb") as src, open(tmp, "wb") as out:
        for chunk in iter(lambda: src.read(CHUNK_BYTES), b""):
            written.update(chunk)
            out.write(chunk)
    if written.hexdigest() != expected_sha256:
        tmp.unlink()
        raise ValueError(f"{item.name} changed while being archived "
                         f"(expected sha256 {expected_sha256})")
    os.replace(tmp, dest)


def store_run(root: pathlib.Path, *inputs: Input) -> str:
    """Archive a run's inputs and its record; return the combined digest.

    Idempotent: blobs already present are not rewritten, and the record is
    rewritten with identical content.
    """
    digest, files = file_digests(*inputs)
    for item, rec in zip(map(_as_traversable, inputs), files):
        _copy_blob(item, blob_path(root, rec.sha256), rec.sha256)
    record = {"source_checksum": digest,
              "files": [{"name": f.name, "size": f.size, "sha256": f.sha256}
                        for f in files]}
    path = run_record_path(root, digest)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(record, ensure_ascii=False, indent=2),
                   encoding="utf-8")
    os.replace(tmp, path)
    return digest


def _check(root: pathlib.Path, checksums: Iterable[str],
           rehash: bool) -> list[ArchiveProblem]:
    problems: list[ArchiveProblem] = []
    for cs in checksums:
        record = run_record_path(root, cs)
        if not record.is_file():
            problems.append(ArchiveProblem(cs, "no run record"))
            continue
        for f in json.loads(record.read_text(encoding="utf-8"))["files"]:
            blob = blob_path(root, f["sha256"])
            if not blob.is_file():
                problems.append(ArchiveProblem(cs, f"missing blob {f['sha256']}"))
            elif rehash:
                if file_digests(blob)[0] != f["sha256"]:
                    problems.append(ArchiveProblem(cs, f"corrupt blob {f['sha256']}"))
            elif blob.stat().st_size != f["size"]:
                # Cheap truncation check for publish: a stat, no re-hash.
                problems.append(ArchiveProblem(cs, f"truncated blob {f['sha256']}"))
    return problems


def missing_runs(root: pathlib.Path,
                 checksums: Iterable[str]) -> list[ArchiveProblem]:
    """Missing records/blobs and wrong-size blobs; cheap (stat, no re-hash)."""
    return _check(root, checksums, rehash=False)


def verify_runs(root: pathlib.Path,
                checksums: Iterable[str]) -> list[ArchiveProblem]:
    """As `missing_runs`, plus re-hash every blob. The audit command uses it."""
    return _check(root, checksums, rehash=True)
