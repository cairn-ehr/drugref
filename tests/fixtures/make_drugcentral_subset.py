#!/usr/bin/env python3
"""Build tests/fixtures/drugcentral_ddi_subset.sql.gz from the real dump.

    uv run python tests/fixtures/make_drugcentral_subset.py \
        downloads/DRUGCENTRAL/drugcentral.dump.11012023.sql.gz \
        tests/fixtures/drugcentral_ddi_subset.sql.gz

WHAT IT KEEPS, and every choice is load-bearing for a test:

* `reference` rows 1, 2 AND 3 -- all three, so the rule-6 filter and
  check_reference_identity are both exercised against a dump that really does
  carry the excluded references.
* A handful of `ddi` rows per reference, including ONE PAIR PUBLISHED IN BOTH
  ORDERS with disagreeing bands (id 15 vs id 2890, gatifloxacin/pioglitazone --
  one of the 4 real pairs, out of 33, that disagree), so the view's collapse
  has something to collapse.
* One endpoint resolvable only through `synonyms` ('acetaminophen', id 870 --
  DrugCentral's own primary name for that structure is 'paracetamol', struct_id
  52; 'acetaminophen' reaches it only via a synonyms row), and one resolvable
  through neither ('cortisone', id 1288 -- genuinely absent from both
  `structures.name` and `synonyms.name` in the real release; only 2 such names
  exist among the 924 distinct ref-2 endpoints), so the cascade and the gap
  view both have a case.
* The `structures` and `synonyms` rows those endpoints need, and nothing else.
  'cortisone' deliberately has NO structures row here -- that absence, which
  matches the real release, IS the unresolvable-endpoint case; adding one would
  quietly turn a genuine gap into an invented one.

WHAT IT REDACTS, and this is a LICENCE requirement rather than tidiness: the
`description` of every `ddi_ref_id` 1 and 3 row is replaced with the literal
string '[redacted: cites a reference CLAUDE.md rule 6 excludes]'. Those rows cite
a copyrighted book and a commercial compendium, and committing their text into an
AGPL repository is exactly what rule 6 forbids. The MED-RT fixture's endpoint
redaction is the precedent, and a test enforces it there and here.

The `ddi_ref_id = 2` rows are VHA NDF-RT content -- a US federal work -- and are
committed in full, at the fair-dealing scale tests/fixtures/pbs_items_subset.csv
already established for a handful of upstream rows.

WHAT IS DELIBERATELY *NOT* REDACTED, AND WHY THAT WAS CHECKED RATHER THAN
ASSUMED: `source_id` is committed unredacted on every row, including the
excluded ref-1/ref-3 ones, even though on those rows it carries readable text
that mirrors the source compendium's own monograph heading rather than an
opaque code -- e.g. id 15522's "MAOIs or RIMAs + Buspirone" (Stockley's) and id
15535's "Conivaptan: CYP3A4 Substrates" (Lexicomp). Rule 6 requires the check to
be made BEFORE a source is added, not merely that the result look safe in
hindsight, so the determination is recorded here rather than left for a future
maintainer to re-derive under time pressure. Two independent grounds: (1) these
are short noun-phrase titles/headings, categorically outside US copyright
protection regardless of authorship (37 C.F.R. Sec 202.1(a) -- "words and short
phrases such as names, titles, and slogans"); (2) they restate the same
drug-pair fact already committed unredacted in `drug_class1`/`drug_class2` on
the very same rows, under the same facts-are-not-copyrightable reasoning NOTICE
already invokes for the ONCHIGH list (Feist Publications, Inc. v. Rural
Telephone Service Co., 499 U.S. 340 (1991)). If a future regeneration ever
selects a ref-1/ref-3 row whose `source_id` is not a short heading but
free-form prose, THIS determination no longer covers it and the row needs the
same redaction `description` gets.

WHY THE GENERATOR READS THE REAL .sql.gz RATHER THAN THE EXTRACTED TSV CACHE
under downloads/DRUGCENTRAL/extracted/: that cache exists to make repeated
*measurement* runs cheap (tools/drugcentral_cache.py), and it round-trips SQL
NULL as an empty string, which would silently turn every NULL field in this
fixture into a wrong-but-plausible ''. A future regeneration will have the real
dump and not necessarily a warm cache, so this script depends on the one
artefact that is guaranteed to exist: the dump itself. One streaming pass over
it (~1.4 GB gzipped, ~5 GB of text) takes about 14 seconds.

WHY THE COLUMN PROJECTIONS ARE WHAT THEY ARE: `reference`, `ddi` and `synonyms`
are written with every column the real dump declares for them (14, 7 and 6
columns respectively -- there is nothing to trim). `structures`' real header is
30 columns wide (molfile, SMILES, Lipinski parameters, ...), none of which
`drugcentral_resolve.build_endpoint_index` reads; it is projected down to the
five columns `tools/drugcentral_ddi_spike.py`'s own WANTED_COLUMNS already
uses for the same table, so this fixture's shape matches the rest of the
DrugCentral tooling's convention rather than inventing a new one.
"""
from __future__ import annotations

import gzip
import sys
from collections.abc import Iterable, Mapping, Sequence

from drugref.ingest import drugcentral
from drugref.ingest.drugcentral_dump import decode_copy_field

#: The redaction text a rule-6-excluded row's description is replaced with.
#: tests/test_drugcentral_fixture.py asserts every ref-1/ref-3 row carries
#: exactly this string, so it is a public constant rather than an inline literal
#: repeated in two files that could drift apart.
REDACTED = "[redacted: cites a reference CLAUDE.md rule 6 excludes]"

#: Column order for each COPY block, taken verbatim from the real dump's own
#: headers (`gunzip -c ... | grep '^COPY public.<table> '`) -- except
#: `structures`, projected per the module docstring above.
COLUMNS: Mapping[str, Sequence[str]] = {
    "reference": ("id", "pmid", "doi", "document_id", "type", "authors", "title",
                  "isbn10", "url", "journal", "volume", "issue", "dp_year", "pages"),
    "structures": ("id", "name", "cas_reg_no", "inchikey", "status"),
    "synonyms": ("syn_id", "id", "name", "preferred_name", "parent_id", "lname"),
    "ddi": ("id", "drug_class1", "drug_class2", "ddi_ref_id", "ddi_risk",
            "description", "source_id"),
}

# Every `reference` id the fixture carries -- all three, per the module docstring.
WANTED_REFERENCE_IDS = frozenset({"1", "2", "3"})

# Every `ddi` id the fixture carries, with why. Kept as an explicit allow-list,
# like every other subset generator in this directory, so the fixture is
# reproducible id-for-id rather than "whatever a sampling rule happened to pick".
WANTED_DDI_IDS = {
    # ref 2 (VHA NDF-RT) -- bundleable under rule 6.
    "15": "gatifloxacin/pioglitazone, Critical -- both-order pair, forward",
    "2890": "pioglitazone/gatifloxacin, Significant -- same pair, reverse, "
            "DISAGREEING band",
    "870": "acetaminophen/sulfinpyrazone -- 'acetaminophen' resolves ONLY via "
           "synonyms",
    "1288": "cortisone/rifabutin -- 'cortisone' resolves through NEITHER table",
    # ref 1 (Stockley's Drug Interactions) -- EXCLUDED by rule 6.
    "15522": "Monoamine Oxidase Inhibitors/buspirone -- description redacted",
    "15523": "Monoamine Oxidase Inhibitors/dextromethorphan -- description "
             "redacted",
    # ref 3 (Lexicomp Online) -- EXCLUDED by rule 6.
    "15517": "Monoamine Oxidase Inhibitors/Alpha-Beta Agonists -- description "
             "redacted",
    "15535": "conivaptan/CYP3A4 Substrates -- description redacted",
}

# `structures` rows the ref-2 endpoints above need, by their PRIMARY name
# (folded the way drugcentral_resolve.fold_name does: stripped and lowercased).
# 'cortisone' is deliberately ABSENT -- see the module docstring.
WANTED_STRUCTURE_NAMES = frozenset({
    "gatifloxacin", "pioglitazone", "rifabutin", "sulfinpyrazone",
    "paracetamol",  # struct_id 52's primary name; 'acetaminophen' is a synonym
})

# `synonyms` rows those endpoints need: just the one bridge that makes
# 'acetaminophen' resolvable at all -- acetaminophen -> struct_id 52.
WANTED_SYNONYM_NAMES = frozenset({"acetaminophen"})

# The escapes `decode_copy_field` undoes, reversed. `encode_copy_field` is
# `decode_copy_field`'s exact inverse: for every character COPY TEXT format
# gives a NAMED escape to, this is that escape; every other character (which is
# everything else in this fixture's real prose) passes through unchanged, the
# same "the common case costs nothing" shortcut `decode_copy_field` takes.
# pg_dump itself only ever emits three of these (backslash, tab, newline), but
# the map covers all seven so the function stays a TRUE inverse rather than one
# that happens to work on today's selected rows.
_ENCODE_ESCAPES: Mapping[str, str] = {
    "\\": "\\\\",
    "\b": "\\b",
    "\f": "\\f",
    "\n": "\\n",
    "\r": "\\r",
    "\t": "\\t",
    "\v": "\\v",
}


def encode_copy_field(value: str | None) -> str:
    r"""Encode one field for a COPY TEXT block. The exact inverse of
    `drugcentral_dump.decode_copy_field`: for every *value*,
    ``decode_copy_field(encode_copy_field(value)) == value``.

    `None` (SQL NULL) becomes the two-character sentinel ``\N`` -- never
    produced by escaping an ordinary character, so it cannot collide with real
    data (a field that IS the two literal characters backslash-then-N encodes
    to the four characters ``\\N``, which decodes back to exactly that, not to
    NULL; see decode_copy_field's `_NULL_FIELD` check, which compares the RAW,
    still-escaped field).

    >>> encode_copy_field(None)
    '\\N'
    >>> encode_copy_field("a\tb")
    'a\\tb'
    >>> decode_copy_field(encode_copy_field("a\tb\n\\c"))
    'a\tb\n\\c'
    """
    if value is None:
        return "\\N"
    return "".join(_ENCODE_ESCAPES.get(char, char) for char in value)


def write_copy_block(
    handle,
    table: str,
    columns: Sequence[str],
    rows: Iterable[Mapping[str, str | None]],
) -> int:
    """Write one `COPY ... FROM stdin;` block and return how many rows it held.

    Mirrors the shape `drugcentral_dump.iter_copy_rows` reads: a header naming
    the table and its columns, one tab-separated data line per row, then a bare
    ``\\.`` terminator.
    """
    handle.write(f"COPY public.{table} ({', '.join(columns)}) FROM stdin;\n")
    count = 0
    for row in rows:
        fields = (encode_copy_field(row.get(column)) for column in columns)
        handle.write("\t".join(fields) + "\n")
        count += 1
    handle.write("\\.\n")
    return count


def _folded(value: str | None) -> str:
    """Fold a name the way `drugcentral_resolve.fold_name` does, for selection."""
    return (value or "").strip().lower()


def select_rows(tables: drugcentral.DumpTables) -> dict[str, list[Mapping[str, str | None]]]:
    """Pick the rows described in the module docstring out of the real dump.

    Raises `SystemExit` if any WANTED id or name is missing from *tables* -- a
    future DrugCentral release renumbering or dropping one of these rows must
    fail loudly here, not silently shrink the fixture by one case (the same
    self-report discipline make_pbs_subset.py's `missing` check follows).
    """
    reference_rows = [tables.reference[rid] for rid in sorted(WANTED_REFERENCE_IDS)
                      if rid in tables.reference]
    missing_reference = WANTED_REFERENCE_IDS - {r["id"] for r in reference_rows}

    ddi_by_id = {row["id"]: row for row in tables.ddi if row["id"] in WANTED_DDI_IDS}
    missing_ddi = set(WANTED_DDI_IDS) - set(ddi_by_id)
    ddi_rows = []
    for ddi_id in sorted(WANTED_DDI_IDS, key=int):
        row = ddi_by_id.get(ddi_id)
        if row is None:
            continue
        if row.get("ddi_ref_id") != "2":
            # RULE 6: redact before it ever reaches the output file, not after.
            row = {**row, "description": REDACTED}
        ddi_rows.append(row)

    structures_rows = [row for row in tables.structures
                       if _folded(row.get("name")) in WANTED_STRUCTURE_NAMES]
    found_structure_names = {_folded(row.get("name")) for row in structures_rows}
    missing_structures = WANTED_STRUCTURE_NAMES - found_structure_names

    synonyms_rows = [row for row in tables.synonyms
                     if _folded(row.get("name")) in WANTED_SYNONYM_NAMES]
    found_synonym_names = {_folded(row.get("name")) for row in synonyms_rows}
    missing_synonyms = WANTED_SYNONYM_NAMES - found_synonym_names

    problems = []
    if missing_reference:
        problems.append(f"reference ids {sorted(missing_reference)}")
    if missing_ddi:
        problems.append(f"ddi ids {sorted(missing_ddi, key=int)}")
    if missing_structures:
        problems.append(f"structures names {sorted(missing_structures)}")
    if missing_synonyms:
        problems.append(f"synonyms names {sorted(missing_synonyms)}")
    if problems:
        raise SystemExit(
            "make_drugcentral_subset: the dump no longer carries: "
            + "; ".join(problems)
            + " -- the WANTED allow-lists are stale for this release, or the "
              "wrong dump was given.")

    return {
        "reference": reference_rows,
        "structures": structures_rows,
        "synonyms": synonyms_rows,
        "ddi": ddi_rows,
    }


def _verify_escaper_is_invertible() -> None:
    """Belt-and-braces: prove `encode_copy_field` inverts against the real
    decoder before trusting it with 17 rows of real data. Exercises the cases a
    plain-prose ddi.description will never happen to cover on its own -- a
    literal tab, a literal backslash, the two-character string that LOOKS like
    the NULL sentinel but must not decode as one, and the three named escapes
    (backspace, form feed, vertical tab) no selected row's text contains, so
    every entry in `_ENCODE_ESCAPES` is proven by execution here, not merely
    reasoned about from matching `decode_copy_field`'s own table.
    """
    for value in (None, "", "a\tb\n\\c", "\\N", "trailing backslash: \\",
                 "a\bb\fc\vd"):
        encoded = encode_copy_field(value)
        decoded = decode_copy_field(encoded)
        if decoded != value:
            raise SystemExit(
                f"make_drugcentral_subset: encode_copy_field is not an inverse "
                f"of decode_copy_field for {value!r}: encoded to {encoded!r}, "
                f"decoded back to {decoded!r}. Refusing to generate a fixture "
                f"an escaping bug could make unreadable.")


def main(dump_path: str, out_path: str) -> None:
    _verify_escaper_is_invertible()
    with gzip.open(dump_path, "rt", encoding="utf-8") as handle:
        tables = drugcentral.read_tables(handle)

    selected = select_rows(tables)

    # Table order here is arbitrary -- iter_copy_rows tolerates COPY blocks in
    # any order -- but `reference`, `structures` and `synonyms` before `ddi`
    # mirrors the real dump's own dependency order (`ddi` cites the other
    # three), which is what a reader skimming the file by eye expects.
    with gzip.open(out_path, "wt", encoding="utf-8") as out:
        counts = {
            table: write_copy_block(out, table, COLUMNS[table], selected[table])
            for table in ("reference", "structures", "synonyms", "ddi")
        }

    print(f"make_drugcentral_subset: wrote {out_path}", file=sys.stderr)
    for table, count in counts.items():
        print(f"  {table}: {count} rows", file=sys.stderr)


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print("usage: make_drugcentral_subset.py <dump.sql.gz> <out.sql.gz>",
              file=sys.stderr)
        raise SystemExit(2)
    main(sys.argv[1], sys.argv[2])
