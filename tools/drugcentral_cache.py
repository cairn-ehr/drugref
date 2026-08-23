"""Extract the wanted DrugCentral tables to TSV once, then read them back strictly.

**Why a cache at all.** One streaming pass over the decompressed dump takes ~14 s.
Caching it is what lets the measurement be re-run while a design is being argued
about, rather than once at the end.

**Why the cache is a liability if it is not committed properly.** Every guard in
`tools/drugcentral_dump.py` -- refusing a malformed row, an unterminated block, a
merged table -- exists so that a partially working parser cannot report a plausible
figure. The cache sits downstream of all of them and used to undo every one:

* a crashed extract left well-formed but truncated TSVs behind, and the next run
  found ``ddi.tsv``, said "using cached extract" and measured the wreckage;
* nothing tied the files to the dump that produced them, so a warm cache plus a new
  ``--dump`` printed the new dump's SHA-256 above the old dump's numbers;
* a projected column the dump did not declare became an empty string with no error.

So: `extract` builds into a sibling directory and renames it into place, writing the
manifest **last**; `cache_status` refuses anything whose manifest does not match the
dump and the projection it is about to be used for; and `load` is as strict about
field counts as `decode_copy_row` is.

Nothing here touches a database or the network.
"""
from __future__ import annotations

import csv
import json
import pathlib
import shutil
from collections.abc import Iterable, Iterator, Mapping, Sequence
from dataclasses import asdict, dataclass
from typing import TextIO

from tools.drugcentral_dump import iter_copy_rows

MANIFEST_NAME = "manifest.json"

# Sentinels handed to `csv.DictReader` so that a short or long row becomes a named
# error instead of `None`-padding and a silently discarded overflow field.
_MISSING = "\x00missing\x00"
_OVERFLOW = "\x00overflow\x00"

# csv writes `\r\n` by default; the TSVs are ours alone, so keep them plain.
_DIALECT = {"delimiter": "\t", "lineterminator": "\n"}


class CacheError(RuntimeError):
    """The extract cache is missing, stale, or not shaped the way it claims.

    A `RuntimeError` rather than a `ValueError` because every one of these means
    "stop, the thing you are about to measure is not what you think it is" -- not
    "this argument was wrong".
    """


class _StreamStats:
    """Count the decompressed dump as it streams past, for free.

    The published *"4.98 GB / 13,570,317 lines"* reproducibility anchor came from
    a terminal someone once ran and could not be re-derived from anything in the
    repo. It is one pass we are already making.
    """

    def __init__(self) -> None:
        self.lines = 0
        self.characters = 0

    def wrap(self, lines: Iterable[str]) -> Iterator[str]:
        for line in lines:
            self.lines += 1
            self.characters += len(line)
            yield line


@dataclass(frozen=True)
class CacheManifest:
    """What a completed extract records about itself.

    Written last, so its presence IS the commit. Read before the cache is trusted,
    so a run can prove the TSVs beside it came from the dump it is reporting on.

    Attributes:
        dump_path: the ``--dump`` the extract ran against, for the operator.
        dump_bytes: its size.
        dump_sha256: its digest -- the field that ties figures to bytes.
        columns: ``{table: [column, ...]}`` actually written, so a widened
            projection invalidates the cache instead of silently reading a
            narrower one.
        counts: ``{table: rows}``.
        dump_lines: lines in the DECOMPRESSED dump, and
        decompressed_bytes: its size -- both counted during the one streaming pass
            rather than quoted from a terminal someone once ran, which is what
            they were.
        complete: always True on disk; a manifest is only written on success.
    """

    dump_path: str
    dump_bytes: int
    dump_sha256: str
    columns: Mapping[str, Sequence[str]]
    counts: Mapping[str, int]
    dump_lines: int = 0
    decompressed_bytes: int = 0
    complete: bool = True


def manifest_path(work_dir: pathlib.Path) -> pathlib.Path:
    return work_dir / MANIFEST_NAME


def read_manifest(work_dir: pathlib.Path) -> CacheManifest | None:
    """Return the manifest, or ``None`` if there is not a readable one."""
    path = manifest_path(work_dir)
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return CacheManifest(
            dump_path=payload["dump_path"],
            dump_bytes=int(payload["dump_bytes"]),
            dump_sha256=payload["dump_sha256"],
            columns={t: list(c) for t, c in payload["columns"].items()},
            counts={t: int(n) for t, n in payload["counts"].items()},
            dump_lines=int(payload["dump_lines"]),
            decompressed_bytes=int(payload["decompressed_bytes"]),
            complete=bool(payload["complete"]),
        )
    except (json.JSONDecodeError, KeyError, TypeError, ValueError):
        return None


def cache_status(
    work_dir: pathlib.Path,
    dump_sha256: str,
    wanted_columns: Mapping[str, Sequence[str] | None],
) -> tuple[bool, str]:
    """Return ``(usable, reason)``. *reason* is empty when usable.

    Checked rather than assumed, because the previous guard was
    ``(work_dir / "ddi.tsv").exists()`` and every failure below satisfied it.
    """
    manifest = read_manifest(work_dir)
    if manifest is None:
        return False, "no manifest: the cache is absent or a previous run crashed"
    if not manifest.complete:
        return False, "the manifest is marked incomplete"
    if manifest.dump_sha256 != dump_sha256:
        return False, (
            f"cache sha256 {manifest.dump_sha256[:12]}... does not match the dump "
            f"{dump_sha256[:12]}...")

    for table, columns in wanted_columns.items():
        if table not in manifest.columns:
            return False, f"the cache has no {table!r}"
        if columns is not None and list(columns) != list(manifest.columns[table]):
            return False, f"the projection for {table!r} changed since the extract"
        path = work_dir / f"{table}.tsv"
        if not path.exists():
            return False, f"{path.name} is listed in the manifest but missing"
    return True, ""


def extract(
    lines: Iterable[str],
    work_dir: pathlib.Path,
    *,
    wanted_columns: Mapping[str, Sequence[str] | None],
    dump_path: str,
    dump_bytes: int,
    dump_sha256: str,
) -> CacheManifest:
    """Stream *lines* once, writing one TSV per wanted table. Returns the manifest.

    Built in ``<work_dir>.partial`` and renamed into place on success, which does
    three things at once: a crash commits nothing, a re-extract cannot leave a
    stale table behind from an earlier dump, and the manifest can be written last
    as the commit marker.

    Args:
        lines: dump lines -- ``gzip.open(path, "rt", encoding="utf-8")``.
        work_dir: where the committed cache lives.
        wanted_columns: ``{table: columns or None}``; ``None`` keeps every column
            the dump declares.
        dump_path, dump_bytes, dump_sha256: recorded in the manifest so a later run
            can prove which bytes these figures came from.

    Raises:
        CacheError: if the dump does not declare a projected column. `DictWriter`
            would otherwise write it as an empty string, and an all-blank column is
            indistinguishable from a column of genuinely empty values.
    """
    staging = work_dir.with_name(work_dir.name + ".partial")
    shutil.rmtree(staging, ignore_errors=True)
    staging.mkdir(parents=True, exist_ok=True)

    seen = _StreamStats()
    writers: dict[str, csv.DictWriter] = {}
    handles: dict[str, TextIO] = {}
    counts: dict[str, int] = {}
    written_columns: dict[str, list[str]] = {}

    try:
        for table, row in iter_copy_rows(seen.wrap(lines), set(wanted_columns)):
            if table not in writers:
                columns = list(wanted_columns[table] or row)
                missing = [c for c in columns if c not in row]
                if missing:
                    raise CacheError(
                        f"the dump's {table!r} block does not declare "
                        f"{', '.join(sorted(missing))} -- the projection in "
                        "WANTED_COLUMNS is stale, and writing the column blank "
                        "would make an absent column look like an empty one")
                written_columns[table] = columns
                counts[table] = 0
                handle = (staging / f"{table}.tsv").open(
                    "w", newline="", encoding="utf-8")
                handles[table] = handle
                writers[table] = csv.DictWriter(
                    handle, fieldnames=columns, extrasaction="ignore", **_DIALECT)
                writers[table].writeheader()
            writers[table].writerow(
                {k: ("" if v is None else v) for k, v in row.items()})
            counts[table] += 1
    finally:
        for handle in handles.values():
            handle.close()

    manifest = CacheManifest(
        dump_path=dump_path,
        dump_bytes=dump_bytes,
        dump_sha256=dump_sha256,
        columns=written_columns,
        counts=counts,
        dump_lines=seen.lines,
        decompressed_bytes=seen.characters,
    )
    (staging / MANIFEST_NAME).write_text(
        json.dumps(asdict(manifest), indent=2, sort_keys=True), encoding="utf-8")

    shutil.rmtree(work_dir, ignore_errors=True)
    staging.replace(work_dir)
    return manifest


def load(work_dir: pathlib.Path, table: str) -> list[dict[str, str]]:
    """Read one cached TSV back, refusing any row that is not the declared shape.

    `csv.DictReader` pads a short row with ``None`` and files a long row's overflow
    under ``row[None]``, both silently. `decode_copy_row` raises on exactly those
    two cases and its docstring calls that strictness the point, so the read path
    is held to the same standard rather than quietly relaxing it.
    """
    path = work_dir / f"{table}.tsv"
    if not path.exists():
        raise CacheError(
            f"the cache has no {table!r} ({path} is missing) -- re-extract with "
            "--refresh")

    with path.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(
            handle, restkey=_OVERFLOW, restval=_MISSING, **_DIALECT)
        rows: list[dict[str, str]] = []
        for line_no, row in enumerate(reader, start=2):
            if _OVERFLOW in row:
                raise CacheError(
                    f"{path.name} line {line_no} has more fields than its header")
            short = [column for column, value in row.items() if value == _MISSING]
            if short:
                raise CacheError(
                    f"{path.name} line {line_no} is missing "
                    f"{', '.join(short)}")
            rows.append(row)
    return rows
