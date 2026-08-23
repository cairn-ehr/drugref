"""Tests for the pure DrugCentral ``pg_dump`` COPY-block reader.

Why this module exists at all: issue #101's figures rest on ONE unrepeated
2026-08-13 run over a 1.4 GB dump that was then deleted, so nothing on that issue
can be re-derived. PROJECT-NOTES § "Which of these figures can be RE-DERIVED"
names the remedy in as many words -- *a future source evaluation puts its
measurement in ``tools/``*. These tests pin the instrument that measurement uses.

Everything here runs on small literal strings. No database, no 1.4 GB download:
the parser is a pure function over lines, which is what makes it testable at all.
"""
from __future__ import annotations

import pytest

from tools.drugcentral_dump import (
    CopyFormatError,
    decode_copy_field,
    decode_copy_row,
    iter_copy_rows,
    parse_copy_header,
)


# ---------------------------------------------------------------------------
# parse_copy_header -- recognising the line that opens a COPY block
# ---------------------------------------------------------------------------

def test_parse_copy_header_reads_the_table_and_its_columns():
    """The real shape, copied verbatim from the 2023-11-01 dump."""
    line = "COPY public.ddi (id, drug_class1, drug_class2, ddi_ref_id) FROM stdin;"

    assert parse_copy_header(line) == ("ddi", ["id", "drug_class1", "drug_class2", "ddi_ref_id"])


def test_parse_copy_header_strips_the_schema_qualifier():
    """`public.` is noise here: drugref cares about the table name only."""
    assert parse_copy_header("COPY public.reference (id, type) FROM stdin;") == (
        "reference", ["id", "type"])


def test_parse_copy_header_accepts_an_unqualified_table():
    """Not every dump qualifies its tables, and the parser should not care."""
    assert parse_copy_header("COPY ddi (id) FROM stdin;") == ("ddi", ["id"])


def test_parse_copy_header_ignores_a_line_that_is_not_a_copy_header():
    for line in (
        "SET statement_timeout = 0;",
        "",
        "-- COPY public.ddi (id) FROM stdin;",          # a comment, not a statement
        "INSERT INTO public.ddi VALUES (1);",
    ):
        assert parse_copy_header(line) is None, line


# ---------------------------------------------------------------------------
# decode_copy_field -- PostgreSQL's COPY TEXT escaping
# ---------------------------------------------------------------------------

def test_decode_copy_field_maps_the_null_marker_to_none():
    r"""`\N` is NULL, and it is NOT the two-character string "\N"."""
    assert decode_copy_field(r"\N") is None


def test_decode_copy_field_leaves_ordinary_text_alone():
    assert decode_copy_field("Significant") == "Significant"


def test_decode_copy_field_unescapes_the_separators_that_would_otherwise_split_a_row():
    r"""A tab or newline inside a `description` arrives escaped, and MUST come back.

    DrugCentral's `ddi.description` is free prose in a varchar(500); a row whose
    text contains a tab would silently gain a column if this were not decoded.
    """
    assert decode_copy_field(r"a\tb") == "a\tb"
    assert decode_copy_field(r"a\nb") == "a\nb"
    assert decode_copy_field(r"a\rb") == "a\rb"


def test_decode_copy_field_unescapes_a_literal_backslash():
    r"""`\\` is one backslash -- and decoding it LAST would double-decode the rest."""
    assert decode_copy_field(r"a\\b") == "a\\b"
    assert decode_copy_field(r"a\\tb") == "a\\tb"      # backslash then a literal 't'


def test_decode_copy_field_leaves_an_unknown_escape_as_its_own_character():
    r"""PostgreSQL's documented behaviour: `\q` is just `q`."""
    assert decode_copy_field(r"a\qb") == "aqb"


def test_decode_copy_field_handles_a_trailing_backslash_without_crashing():
    """Malformed input must raise a named error, never IndexError."""
    with pytest.raises(CopyFormatError):
        decode_copy_field("ends with a backslash\\")


# ---------------------------------------------------------------------------
# decode_copy_row -- one data line against its declared columns
# ---------------------------------------------------------------------------

def test_decode_copy_row_pairs_the_fields_with_the_declared_columns():
    row = decode_copy_row("1\taspirin\t\\N", ["id", "name", "note"])

    assert row == {"id": "1", "name": "aspirin", "note": None}


def test_decode_copy_row_refuses_a_field_count_that_disagrees_with_the_header():
    """A silent mismatch is how a measurement reports a confident wrong number.

    This is the guard that matters most in this file: a shifted column would make
    every downstream count plausible and wrong, which is exactly the failure mode
    PROJECT-NOTES § "Slice 5c.2g" tells the next session not to repeat.
    """
    with pytest.raises(CopyFormatError) as excinfo:
        decode_copy_row("1\taspirin", ["id", "name", "note"])

    assert "3" in str(excinfo.value) and "2" in str(excinfo.value)


# ---------------------------------------------------------------------------
# iter_copy_rows -- the streaming state machine
# ---------------------------------------------------------------------------

DUMP = [
    "SET statement_timeout = 0;",
    "COPY public.reference (id, type) FROM stdin;",
    "1\tBOOK",
    "2\tNDF-RT",
    "\\.",
    "",
    "COPY public.ddi (id, description) FROM stdin;",
    "10\tincreases the risk",
    "11\t\\N",
    "\\.",
    "ALTER TABLE public.ddi OWNER TO drugman;",
]


def test_iter_copy_rows_yields_rows_from_every_requested_table():
    got = list(iter_copy_rows(DUMP, {"reference", "ddi"}))

    assert got == [
        ("reference", {"id": "1", "type": "BOOK"}),
        ("reference", {"id": "2", "type": "NDF-RT"}),
        ("ddi", {"id": "10", "description": "increases the risk"}),
        ("ddi", {"id": "11", "description": None}),
    ]


def test_iter_copy_rows_skips_the_tables_nobody_asked_for():
    """The dump is 1.4 GB and drugref wants three tables: skipping is the point."""
    got = list(iter_copy_rows(DUMP, {"ddi"}))

    assert [table for table, _ in got] == ["ddi", "ddi"]


def test_iter_copy_rows_does_not_treat_data_that_looks_like_a_header_as_one():
    r"""THE TRAP THIS PARSER EXISTS TO AVOID.

    `ddi.description` is free text. A description quoting a COPY statement would,
    under a stateless line-by-line regex, silently re-point the parser at a table
    that is not there and drop every remaining row of the real one. Inside a
    block, only a line that is exactly `\.` ends it.
    """
    dump = [
        "COPY public.ddi (id, description) FROM stdin;",
        "1\tCOPY public.evil (id) FROM stdin;",
        "2\treal row",
        "\\.",
    ]

    got = list(iter_copy_rows(dump, {"ddi"}))

    assert got == [
        ("ddi", {"id": "1", "description": "COPY public.evil (id) FROM stdin;"}),
        ("ddi", {"id": "2", "description": "real row"}),
    ]


def test_iter_copy_rows_refuses_a_block_that_never_terminates():
    r"""A truncated download must fail loudly, not report a short count.

    The dump is fetched over the network; a partial file decompresses fine and
    ends mid-block. Reporting "7,400 rows" from a truncated stream is precisely
    the "plausible figure from a partially working parser" this project has
    already been bitten by once.
    """
    dump = [
        "COPY public.ddi (id) FROM stdin;",
        "1",
    ]

    with pytest.raises(CopyFormatError) as excinfo:
        list(iter_copy_rows(dump, {"ddi"}))

    assert "ddi" in str(excinfo.value)


def test_iter_copy_rows_accepts_lines_that_still_carry_their_newline():
    """Reading a real file yields lines WITH `\\n`; the fixtures above do not.

    Both must behave identically, or every count measured from the real dump
    differs from every count measured in these tests.
    """
    dump = [
        "COPY public.ddi (id, description) FROM stdin;\n",
        "1\ta description\n",
        "\\.\n",
    ]

    assert list(iter_copy_rows(dump, {"ddi"})) == [
        ("ddi", {"id": "1", "description": "a description"}),
    ]
