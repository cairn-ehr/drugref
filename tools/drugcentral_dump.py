"""Read COPY blocks out of a plain-SQL ``pg_dump``, as pure streaming functions.

**Why this file exists.** Issue #101 evaluated DrugCentral's `ddi` table on
2026-08-13 by streaming the dump with throwaway code. The dump was then deleted,
so every figure on that issue -- 7,621 rows, 970 endpoint names, 6,337 new pairs,
and the three-row `reference` table that decides the licensing question -- rests
on a single unrepeated run that nobody can reproduce. PROJECT-NOTES §
"Which of these figures can be RE-DERIVED" states the remedy directly: *a future
source evaluation puts its measurement in ``tools/``*. This is that instrument.

**What a plain-SQL dump looks like.** ``pg_dump`` without ``-Fc`` emits ordinary
SQL, and bulk data arrives as a COPY block::

    COPY public.ddi (id, drug_class1, drug_class2) FROM stdin;
    1	aspirin	warfarin
    2	digoxin	\\N
    \\.

The header names the table and its columns; each following line is one row with
**tab-separated** fields; a line consisting of exactly ``\\.`` closes the block.

**Two properties make this safe to point at a 1.4 GB file.**

1. *Streaming.* Every function here consumes an iterable of lines and yields as
   it goes, so the caller can hand it ``gzip.open(...)`` and never hold the
   decompressed dump (several GB) in memory.
2. *Stateful, not line-by-line.* Inside a COPY block **only** ``\\.`` ends it.
   `ddi.description` is free prose in a ``varchar(500)``, so a description that
   happens to quote a COPY statement would, under a stateless regex, silently
   re-point a naive parser and drop the rest of the real table. See
   ``test_iter_copy_rows_does_not_treat_data_that_looks_like_a_header_as_one``.

Nothing here touches a database or the network, which is what lets the whole
module be tested against a handful of literal strings.
"""
from __future__ import annotations

import re
from collections.abc import Collection, Iterable, Iterator, Sequence


class CopyFormatError(ValueError):
    """The dump did not have the shape a COPY block is required to have.

    Raised rather than tolerated **on purpose**. A measurement that shrugs off a
    malformed row reports a number that is plausible and wrong, and this project
    has already paid for that once: PROJECT-NOTES § "Slice 5c.2g" records *"a
    plausible figure from a partially working parser is not a measurement"* as
    one of the two lessons a future session must not lose.
    """


# `COPY [schema.]table (col, col, ...) FROM stdin;`
#
# Anchored at the start of the line so that a COMMENT or an indented occurrence
# inside prose cannot match. The column list is captured whole and split below,
# because a regex that tries to enumerate columns is harder to read than one
# `str.split` and buys nothing.
_COPY_HEADER = re.compile(
    r"^COPY\s+(?:(?P<schema>[^\s.(]+)\.)?(?P<table>[^\s.(]+)\s*"
    r"\((?P<columns>[^)]*)\)\s+FROM\s+stdin;\s*$"
)

# The line that closes a COPY block: a backslash and a dot, alone.
_COPY_TERMINATOR = "\\."

# PostgreSQL's COPY TEXT backslash escapes. Anything not listed here decodes to
# the escaped character itself (`\q` -> `q`), which is PostgreSQL's own rule.
_ESCAPES = {
    "b": "\b",
    "f": "\f",
    "n": "\n",
    "r": "\r",
    "t": "\t",
    "v": "\v",
    "\\": "\\",
}

# The field that means SQL NULL. It is a whole-field marker, not an escape: a
# field is NULL only when it is EXACTLY this, never when it merely contains it.
_NULL_FIELD = "\\N"


def parse_copy_header(line: str) -> tuple[str, list[str]] | None:
    """Return ``(table, columns)`` if *line* opens a COPY block, else ``None``.

    The schema qualifier is dropped: DrugCentral puts everything in ``public``,
    and callers ask for tables by bare name.

    >>> parse_copy_header("COPY public.ddi (id, ddi_ref_id) FROM stdin;")
    ('ddi', ['id', 'ddi_ref_id'])
    >>> parse_copy_header("SET statement_timeout = 0;") is None
    True
    """
    match = _COPY_HEADER.match(line.rstrip("\n"))
    if match is None:
        return None
    columns = [column.strip() for column in match.group("columns").split(",")]
    return match.group("table"), columns


def decode_copy_field(raw: str) -> str | None:
    """Decode one COPY TEXT field. ``\\N`` becomes ``None``; escapes are undone.

    Decoding is a **single left-to-right pass**, which is the only correct way to
    do it. A sequence of ``str.replace`` calls double-decodes: replacing ``\\t``
    before ``\\\\`` turns the two-character input ``\\\\t`` (an escaped backslash
    followed by a literal ``t``) into a tab, which is a different string.

    >>> decode_copy_field(r"\\N") is None
    True
    >>> decode_copy_field(r"a\\tb")
    'a\\tb'
    """
    if raw == _NULL_FIELD:
        return None
    if "\\" not in raw:
        # The overwhelmingly common case, and worth taking early: the dump has
        # millions of rows and most fields carry no escape at all.
        return raw

    out: list[str] = []
    index = 0
    length = len(raw)
    while index < length:
        char = raw[index]
        if char != "\\":
            out.append(char)
            index += 1
            continue
        if index + 1 >= length:
            raise CopyFormatError(f"field ends with a dangling backslash: {raw!r}")
        following = raw[index + 1]
        out.append(_ESCAPES.get(following, following))
        index += 2
    return "".join(out)


def decode_copy_row(line: str, columns: Sequence[str]) -> dict[str, str | None]:
    """Decode one data line into ``{column: value}``, ``None`` where SQL had NULL.

    Raises `CopyFormatError` when the field count disagrees with the header. That
    strictness is the point: a shifted column produces counts that look entirely
    reasonable, so the mismatch has to stop the run rather than colour it.
    """
    fields = line.rstrip("\n").split("\t")
    if len(fields) != len(columns):
        raise CopyFormatError(
            f"row has {len(fields)} fields but the header declared "
            f"{len(columns)} columns: {line!r}"
        )
    return {column: decode_copy_field(field) for column, field in zip(columns, fields)}


def iter_copy_rows(
    lines: Iterable[str],
    wanted: Collection[str],
) -> Iterator[tuple[str, dict[str, str | None]]]:
    """Stream ``(table, row)`` pairs for each table named in *wanted*.

    Lines outside a COPY block are ignored, and blocks for unwanted tables are
    skipped without decoding a single field -- which is what makes pointing this
    at a multi-gigabyte dump for three small tables cheap.

    Args:
        lines: any iterable of dump lines, with or without trailing newlines.
            ``gzip.open(path, "rt", encoding="utf-8")`` is the intended caller.
        wanted: the bare table names to decode, e.g. ``{"ddi", "reference"}``.

    Raises:
        CopyFormatError: if a block is still open when the input ends. A
            truncated download decompresses perfectly well and simply stops
            mid-table, so silence here would mean reporting a short count as a
            measurement.
    """
    table: str | None = None
    columns: Sequence[str] = ()
    decoding = False

    for line in lines:
        if table is None:
            header = parse_copy_header(line)
            if header is not None:
                table, columns = header
                decoding = table in wanted
            continue

        # Inside a block. ONLY the terminator closes it -- never a line that
        # merely looks like a header, because row data is arbitrary text.
        if line.rstrip("\n") == _COPY_TERMINATOR:
            table, columns, decoding = None, (), False
            continue

        if decoding:
            yield table, decode_copy_row(line, columns)

    if table is not None:
        raise CopyFormatError(
            f"the COPY block for {table!r} was never closed by '\\.': "
            "the dump is truncated"
        )
