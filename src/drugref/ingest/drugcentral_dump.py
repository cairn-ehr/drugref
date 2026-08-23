"""Read COPY blocks out of a plain-SQL ``pg_dump``, as pure streaming functions.

**Why this file exists.** Issue #101 evaluated DrugCentral's `ddi` table on
2026-08-13 by streaming the dump with throwaway code. The dump was then deleted,
so every figure on that issue -- 7,621 rows, 970 endpoint names, 6,337 new pairs,
and the three `reference` rows that decide the licensing question -- rested on a
single unrepeated run that nobody could reproduce. (The `reference` table itself
holds 1,195 rows; three of them are the ones `ddi` cites.) PROJECT-NOTES §
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

1. *Streaming.* `iter_copy_rows` consumes an iterable of lines and yields as it
   goes, so the caller can hand it ``gzip.open(...)`` and never hold the
   decompressed dump (several GB) in memory. Everything else in this module is a
   pure function over a single string.
2. *Stateful, not line-by-line.* Inside a COPY block **only** ``\\.`` ends it.
   `ddi.description` is free prose in a ``varchar(500)``, so a description that
   happens to quote a COPY statement would, under a stateless regex, silently
   re-point a naive parser and drop the rest of the real table. See
   ``test_iter_copy_rows_does_not_treat_data_that_looks_like_a_header_as_one``
   and its mirror,
   ``test_a_data_line_that_merely_LOOKS_like_the_terminator_does_not_close_the_block``.

**One assumption, checked rather than trusted.** The schema qualifier is dropped
so callers can ask for tables by bare name. Two same-named tables in different
schemas would therefore merge into one stream and report the sum of their rows as
one table's count, so `iter_copy_rows` raises instead. DrugCentral puts
everything in ``public``; the guard costs one comparison per block.

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
    has already paid for that once: a plausible figure from a partially working
    parser is not a measurement, and the run that produces one looks exactly like
    the run that produces a real one.

    Carries `table` and `line_no` where the caller knows them. Without a line
    number the operator's only recourse on a 13.5-million-line dump is to bisect
    a 5 GB file by hand.
    """

    def __init__(
        self,
        message: str,
        *,
        table: str | None = None,
        line_no: int | None = None,
    ) -> None:
        where = ""
        if table is not None:
            where += f" [table {table!r}"
            where += f", line {line_no}]" if line_no is not None else "]"
        elif line_no is not None:
            where += f" [line {line_no}]"
        super().__init__(message + where)
        self.table = table
        self.line_no = line_no


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

# PostgreSQL's COPY TEXT single-character backslash escapes.
_ESCAPES = {
    "b": "\b",
    "f": "\f",
    "n": "\n",
    "r": "\r",
    "t": "\t",
    "v": "\v",
    "\\": "\\",
}

_OCTAL_DIGITS = "01234567"
_HEX_DIGITS = "0123456789abcdefABCDEF"

# The field that means SQL NULL. It is a whole-field marker, not an escape: a
# field is NULL only when it is EXACTLY this, never when it merely contains it.
_NULL_FIELD = "\\N"


def parse_copy_header(line: str) -> tuple[str, list[str]] | None:
    """Return ``(table, columns)`` if *line* opens a COPY block, else ``None``.

    The schema qualifier is dropped: DrugCentral puts everything in ``public``,
    and callers ask for tables by bare name. `iter_copy_rows` checks that no two
    schemas claim the same bare name; this function does not, because it sees one
    line at a time.

    >>> parse_copy_header("COPY public.ddi (id, ddi_ref_id) FROM stdin;")
    ('ddi', ['id', 'ddi_ref_id'])
    >>> parse_copy_header("SET statement_timeout = 0;") is None
    True
    """
    header = _parse_copy_header_with_schema(line)
    if header is None:
        return None
    _schema, table, columns = header
    return table, columns


def _parse_copy_header_with_schema(
    line: str,
) -> tuple[str | None, str, list[str]] | None:
    """`parse_copy_header`, keeping the schema so the merge guard can compare it."""
    match = _COPY_HEADER.match(line.rstrip("\n"))
    if match is None:
        return None
    columns = [column.strip() for column in match.group("columns").split(",")]
    return match.group("schema"), match.group("table"), columns


def _decode_numeric_escape(raw: str, index: int) -> tuple[str, int] | None:
    """Decode ``\\ooo`` or ``\\xhh`` starting at the backslash, or return ``None``.

    *index* points at the backslash. Returns ``(character, characters_consumed)``.
    ``None`` means this is not a numeric escape and the caller should fall back to
    the single-character rules.

    `pg_dump` never emits either form -- it writes raw UTF-8 -- so nothing in the
    DrugCentral measurement depends on this. It is handled because the module is
    the reusable reader for the next dump-shaped source, and a parser that
    silently decodes ``\\101`` to the three characters ``101`` is wrong in exactly
    the quiet way this file exists to refuse.
    """
    following = raw[index + 1]

    if following == "x":
        digits = ""
        while len(digits) < 2 and index + 2 + len(digits) < len(raw):
            char = raw[index + 2 + len(digits)]
            if char not in _HEX_DIGITS:
                break
            digits += char
        if not digits:
            return None                    # bare `\x`: an unknown escape, not hex
        return chr(int(digits, 16)), 2 + len(digits)

    if following in _OCTAL_DIGITS:
        digits = ""
        while len(digits) < 3 and index + 1 + len(digits) < len(raw):
            char = raw[index + 1 + len(digits)]
            if char not in _OCTAL_DIGITS:
                break
            digits += char
        return chr(int(digits, 8)), 1 + len(digits)

    return None


def decode_copy_field(raw: str) -> str | None:
    r"""Decode one COPY TEXT field. ``\N`` becomes ``None``; escapes are undone.

    Decoding is a **single left-to-right pass**, which is the only correct way to
    do it. A sequence of ``str.replace`` calls double-decodes: replacing ``\t``
    before ``\\`` turns the three-character field ``\\t`` (an escaped backslash,
    then a literal ``t``) into backslash-then-tab, when the answer is
    backslash-then-``t``.

    Handles every escape PostgreSQL's COPY TEXT format defines -- the named ones,
    ``\ooo`` octal and ``\xhh`` hex -- and falls back to PostgreSQL's own rule
    that anything else decodes to the escaped character itself (``\q`` -> ``q``).

    >>> decode_copy_field(r"\N") is None
    True
    >>> decode_copy_field(r"a\tb")
    'a\tb'
    >>> decode_copy_field(r"\101")
    'A'
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
        numeric = _decode_numeric_escape(raw, index)
        if numeric is not None:
            character, consumed = numeric
            out.append(character)
            index += consumed
            continue
        following = raw[index + 1]
        out.append(_ESCAPES.get(following, following))
        index += 2
    return "".join(out)


def decode_copy_row(
    line: str,
    columns: Sequence[str],
    *,
    table: str | None = None,
    line_no: int | None = None,
) -> dict[str, str | None]:
    """Decode one data line into ``{column: value}``, ``None`` where SQL had NULL.

    Raises `CopyFormatError` when the field count disagrees with the header. That
    strictness is the point: a shifted column produces counts that look entirely
    reasonable, so the mismatch has to stop the run rather than colour it.

    Args:
        line: one data line, with or without its trailing newline.
        columns: the column names the block's header declared.
        table: the table being decoded, for the error message only.
        line_no: the 1-based line number in the dump, for the error message only.
    """
    fields = line.rstrip("\n").split("\t")
    if len(fields) != len(columns):
        raise CopyFormatError(
            f"row has {len(fields)} fields but the header declared "
            f"{len(columns)} columns: {line!r}",
            table=table,
            line_no=line_no,
        )
    return {column: decode_copy_field(field) for column, field in zip(columns, fields)}


def iter_copy_rows(
    lines: Iterable[str],
    wanted: Collection[str],
) -> Iterator[tuple[str, dict[str, str | None]]]:
    """Stream ``(table, row)`` pairs for each table named in *wanted*.

    Lines outside a COPY block are ignored, and blocks for unwanted tables are
    skipped without decoding a single field -- which is what makes pointing this
    at a multi-gigabyte dump for a handful of tables cheap.

    Args:
        lines: any iterable of dump lines, with or without trailing newlines.
            ``gzip.open(path, "rt", encoding="utf-8")`` is the intended caller.
        wanted: the bare table names to decode, e.g. ``{"ddi", "reference"}``.

    Raises:
        CopyFormatError: if a block is still open when the input ends -- for ANY
            table, wanted or not. `pg_dump` emits tables in dependency order, so a
            truncated download leaves the LAST block open and that block is
            usually one this measurement does not decode; silence there would
            mean a truncated dump measured as a whole one. Also raised if two
            schemas hold the same bare table name, which would otherwise merge
            two tables into one stream and report their sum.
    """
    table: str | None = None
    columns: Sequence[str] = ()
    decoding = False
    opened_at: int | None = None
    schema_of: dict[str, str | None] = {}

    for line_no, line in enumerate(lines, start=1):
        if table is None:
            header = _parse_copy_header_with_schema(line)
            if header is not None:
                schema, table, columns = header
                if table in schema_of and schema_of[table] != schema:
                    raise CopyFormatError(
                        f"table {table!r} appears under two schemas "
                        f"({schema_of[table]!r} and {schema!r}); dropping the "
                        "qualifier would merge them into one stream",
                        table=table,
                        line_no=line_no,
                    )
                schema_of[table] = schema
                decoding = table in wanted
                opened_at = line_no
            continue

        # Inside a block. ONLY the terminator closes it -- never a line that
        # merely looks like a header, because row data is arbitrary text.
        if line.rstrip("\n") == _COPY_TERMINATOR:
            table, columns, decoding, opened_at = None, (), False, None
            continue

        if decoding:
            yield table, decode_copy_row(
                line, columns, table=table, line_no=line_no)

    if table is not None:
        raise CopyFormatError(
            f"the COPY block for {table!r} was never closed by '\\.': "
            "the dump is truncated",
            table=table,
            line_no=opened_at,
        )
