"""Tests for the DrugCentral extract cache -- the layer that lost the parser's strictness.

`tools/drugcentral_dump.py` refuses a malformed row, an unterminated block and a
merged table, loudly, and its docstring explains why tolerance would be worse than
a crash. **None of that survived the trip through the TSV cache**, and the cache is
the path every run after the first one takes:

* a crashed extract left well-formed but truncated TSVs on disk, and the next run
  found `ddi.tsv`, printed "using cached extract" and reported the short count as a
  measurement;
* nothing tied the cache to the dump, so pointing ``--dump`` at a new release with
  a warm cache printed the new dump's SHA-256 above the old dump's figures -- worse
  than no hash, because a 64-hex digest is what invites a reader to trust the table
  it sits in;
* `csv.DictWriter` silently wrote an empty column for any projected column the dump
  did not have, and `csv.DictReader` silently padded a short row with ``None``.

So the cache is now committed by a manifest written last, validated before use, and
read strictly. These tests pin all three.
"""
from __future__ import annotations

import json

import pytest

from tools.drugcentral_cache import (
    CacheError,
    cache_status,
    extract,
    load,
    read_manifest,
)

WANTED = {
    "ddi": ("id", "ddi_ref_id", "description"),
    "structures": ("id", "name", "inchikey", "cas_reg_no"),
}

DUMP_LINES = [
    "SET statement_timeout = 0;",
    "COPY public.ddi (id, ddi_ref_id, description, source_id) FROM stdin;",
    "1\t2\tincreases the risk\t900",
    "2\t3\t\\N\t901",
    "\\.",
    "COPY public.structures (id, name, inchikey, cas_reg_no, molfile) FROM stdin;",
    "1\twarfarin\tPJVWKTKQMONHTI-UPHRSURJSA-N\t81-81-2\tBIGBLOB",
    "\\.",
    "COPY public.ignored (id) FROM stdin;",
    "1",
    "\\.",
]


def _extract(tmp_path, lines=DUMP_LINES, wanted=None, sha="abc123", size=99):
    return extract(
        lines,
        tmp_path / "cache",
        wanted_columns=wanted or WANTED,
        dump_path="downloads/x.sql.gz",
        dump_bytes=size,
        dump_sha256=sha,
    )


# ---------------------------------------------------------------------------
# extract -- projection, and the column it must not invent
# ---------------------------------------------------------------------------

def test_extract_keeps_only_the_projected_columns_and_counts_every_row(tmp_path):
    manifest = _extract(tmp_path)

    assert manifest.counts == {"ddi": 2, "structures": 1}
    assert load(tmp_path / "cache", "structures") == [
        {"id": "1", "name": "warfarin",
         "inchikey": "PJVWKTKQMONHTI-UPHRSURJSA-N", "cas_reg_no": "81-81-2"},
    ]


def test_extract_refuses_a_projection_the_dump_does_not_declare(tmp_path):
    """THE FAILURE THIS GUARD EXISTS FOR, and it is not hypothetical.

    `csv.DictWriter` fills a missing fieldname from `restval` -- an empty string,
    no error. Rename `structures.inchikey` in a future release and every row gets
    a blank InChIKey; the cascade's blank-key guard then correctly declines to look
    any of them up, the InChIKey route contributes nothing, and the report prints
    `names resolved 857 -> 857, delta +0`. A reader concludes the structural route
    was tested and failed. It was never run.
    """
    wanted = {"structures": ("id", "name", "inchikey", "cas_reg_no")}
    lines = [
        "COPY public.structures (id, name, inchi_key, cas_reg_no) FROM stdin;",
        "1\twarfarin\tPJVW\t81-81-2",
        "\\.",
    ]

    with pytest.raises(CacheError) as excinfo:
        _extract(tmp_path, lines=lines, wanted=wanted)

    assert "inchikey" in str(excinfo.value) and "structures" in str(excinfo.value)


def test_extract_round_trips_a_value_carrying_tabs_newlines_and_quotes(tmp_path):
    r"""`ddi.description` is free prose; the TSV must not lose or split it.

    A decoded newline inside a description would, unquoted, split one row into two:
    the row count silently grows and every field after it shifts.
    """
    lines = [
        "COPY public.ddi (id, ddi_ref_id, description) FROM stdin;",
        "1\t2\ta\\tb\\nc \"quoted\" and a \\\\ backslash",
        "\\.",
    ]

    _extract(tmp_path, lines=lines, wanted={"ddi": ("id", "ddi_ref_id", "description")})

    assert load(tmp_path / "cache", "ddi")[0]["description"] == (
        'a\tb\nc "quoted" and a \\ backslash')


def test_extract_writes_sql_null_as_the_empty_string(tmp_path):
    """Documented because the resolver's blank guards depend on knowing it."""
    _extract(tmp_path)

    assert load(tmp_path / "cache", "ddi")[1]["description"] == ""


# ---------------------------------------------------------------------------
# The manifest is the commit marker
# ---------------------------------------------------------------------------

def test_a_crashed_extract_leaves_no_usable_cache(tmp_path):
    """THE FAILURE MODE THAT DEFEATED THE PARSER'S STRICTNESS ONE LAYER UP.

    `CopyFormatError` fires exactly as designed, the operator re-runs the same
    command -- the natural reaction to a traceback -- and the second run finds a
    truncated `ddi.tsv` and reports it as a measurement. The manifest is written
    last and validated first, so a crashed run commits nothing.
    """
    lines = [
        "COPY public.ddi (id, ddi_ref_id, description) FROM stdin;",
        "1\t2\tfine",
        "2\tONLY_TWO_FIELDS",
        "\\.",
    ]

    with pytest.raises(Exception):
        _extract(tmp_path, lines=lines, wanted={"ddi": ("id", "ddi_ref_id", "description")})

    usable, reason = cache_status(tmp_path / "cache", "abc123", WANTED)
    assert usable is False
    assert reason


def test_a_cache_built_from_a_different_dump_is_refused(tmp_path):
    """A warm cache plus a new `--dump` printed the new SHA over the old figures.

    That is worse than recording no hash at all: the digest is precisely what
    invites a reader to trust the provenance table it sits in.
    """
    _extract(tmp_path, sha="abc123")

    usable, reason = cache_status(tmp_path / "cache", "DIFFERENT", WANTED)

    assert usable is False
    assert "sha256" in reason.lower()


def test_a_cache_built_from_a_different_projection_is_refused(tmp_path):
    """Widen `WANTED_COLUMNS` and the old cache cannot answer the new question."""
    _extract(tmp_path)

    widened = dict(WANTED, structures=("id", "name", "inchikey", "cas_reg_no", "status"))
    usable, reason = cache_status(tmp_path / "cache", "abc123", widened)

    assert usable is False
    assert "structures" in reason


def test_a_matching_cache_is_usable(tmp_path):
    _extract(tmp_path)

    assert cache_status(tmp_path / "cache", "abc123", WANTED) == (True, "")


def test_a_manifest_marked_incomplete_is_refused(tmp_path):
    """Belt and braces: the flag is checked as well as the file's presence."""
    _extract(tmp_path)
    path = tmp_path / "cache" / "manifest.json"
    payload = json.loads(path.read_text())
    payload["complete"] = False
    path.write_text(json.dumps(payload))

    usable, _reason = cache_status(tmp_path / "cache", "abc123", WANTED)

    assert usable is False


def test_re_extracting_clears_a_table_left_behind_by_an_earlier_dump(tmp_path):
    """`--refresh` used to leave stale TSVs in place for tables the new dump lacks.

    The old file was then read happily while `table_counts` had no entry for it,
    so the report printed "`pharma_class` holds 0 rows" beside figures derived
    from 25,687 of them.
    """
    _extract(tmp_path)
    assert (tmp_path / "cache" / "structures.tsv").exists()

    _extract(tmp_path, wanted={"ddi": ("id", "ddi_ref_id", "description")})

    assert not (tmp_path / "cache" / "structures.tsv").exists()
    assert read_manifest(tmp_path / "cache").counts == {"ddi": 2}


# ---------------------------------------------------------------------------
# load -- as strict as the parser it reads behind
# ---------------------------------------------------------------------------

def test_load_refuses_a_row_with_too_few_fields(tmp_path):
    """`csv.DictReader` pads a short row with `None` and says nothing.

    `decode_copy_row` raises on exactly this, and the docstring calls that
    strictness the point -- so the cache read path may not undo it.
    """
    _extract(tmp_path)
    path = tmp_path / "cache" / "ddi.tsv"
    path.write_text(path.read_text() + "3\t2\n")

    with pytest.raises(CacheError):
        load(tmp_path / "cache", "ddi")


def test_load_refuses_a_row_with_too_many_fields(tmp_path):
    """`csv.DictReader` files the overflow under `row[None]`, where nothing reads it."""
    _extract(tmp_path)
    path = tmp_path / "cache" / "ddi.tsv"
    path.write_text(path.read_text() + "3\t2\tdesc\tEXTRA\n")

    with pytest.raises(CacheError):
        load(tmp_path / "cache", "ddi")


def test_load_names_the_table_when_the_file_is_missing(tmp_path):
    """A bare FileNotFoundError naming a path does not say which table went absent."""
    _extract(tmp_path)
    (tmp_path / "cache" / "ddi.tsv").unlink()

    with pytest.raises(CacheError) as excinfo:
        load(tmp_path / "cache", "ddi")

    assert "ddi" in str(excinfo.value)
