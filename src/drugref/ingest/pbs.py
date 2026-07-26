# src/drugref/ingest/pbs.py
"""PURE parsing and name normalisation for the Australian PBS feed (slice 8a).

No database access and no network: this module turns CSV rows into dataclasses
and drug names into candidate ingredient names, and nothing else. The
orchestrator (ingest/pbs_run.py) owns the transaction and is the only writer,
the same split MED-RT and MeSH already use.

LICENCE (spec section 1): this module reads ONLY tables_as_csv/items.csv. It must
never read atc-codes.csv, item-atc-relationships.csv or amt-items.csv -- ATC
(WHO, NonCommercial + NoDerivatives) and AMT/SNOMED CT-AU (NCTS affiliate
licence) may not enter drugref at all. items.csv carries neither, so the
quarantine costs nothing.

WHY THE RULES BELOW LOOK ARBITRARY: they are measured against the real 2026-07
release, not taken from the data dictionary (spec 5.3). The separator set, the
'null' sentinel and the absent "acid" suffix each encode a fact about the actual
file that intuition gets wrong.
"""
import csv
import pathlib
import re
from collections.abc import Iterator
from dataclasses import dataclass

from drugref import ids

SALT_SUFFIX_PATH = pathlib.Path(__file__).parent.parent / "data" / "salt_suffixes.tsv"

# PBS's empty-value sentinel is the LITERAL FOUR-LETTER STRING "null", used in 44
# of items.csv's 75 columns. Untreated, drugref would earnestly register a drug
# named "null" -- 159 rows carry li_drug_name = 'null'.
_NULL_SENTINEL = "null"

# The name a product with NO usable drug name at all is recorded under (review
# round, finding 2). Both li_drug_name and drug_name being absent/'null' is rare
# (0 rows in the measured 2026-07 release) but not impossible, and split_components
# correctly returns [] for it -- "no name" is a legitimate data condition, not a
# bug in the split. The bug would be letting the CALLER'S component list end up
# empty too: the product is still written, but with zero components it would
# never appear in a bridge row NOR in local_unmatched_ingredient, vanishing from
# both the numerator and the residual with no queryable trace (spec section 7
# exists precisely to forbid that). pbs_run.py substitutes this sentinel whenever
# split_components returns [], so the item stays visible in the unmatched worklist
# instead of disappearing silently.
NO_DRUG_NAME_SENTINEL = "<no drug name>"

# Combination separators, in the forms actually present upstream. " + " is NOT
# here: it appears in zero of the 1,086 distinct names, so treating it as a
# separator could only ever shred a real name.
_SEPARATORS = re.compile(r"\s+with\s+|\s+and\s+|,\s*", re.IGNORECASE)

# A trailing " (...)" annotation: "Acetic Acid (33 per cent)", "Acetone (use as
# additive only)". The same annotation strip mesh.registry_keys() performs.
_PARENTHETICAL = re.compile(r"\s*\([^)]*\)")


def is_missing(value: str | None) -> bool:
    """True if an upstream field is absent -- blank OR the literal 'null'."""
    return value is None or value.strip() == "" or value.strip() == _NULL_SENTINEL


def load_salt_suffixes(path: str | pathlib.Path = SALT_SUFFIX_PATH) -> frozenset[str]:
    """Load the closed salt/hydrate suffix list, ignoring comments and blanks."""
    with open(path, encoding="utf-8") as fh:
        return frozenset(
            line.strip().lower() for line in fh
            if line.strip() and not line.startswith("#"))


def split_components(name: str) -> list[str]:
    """Split a PBS drug name into its normalised component ingredient names.

    Order-preserving and duplicate-free. Returns [] for a missing name.

    A combination product resolves each component INDEPENDENTLY, so a name where
    one component is a known moiety and another is not can be recorded honestly:
    the known one bridges, the unknown one is counted. Rounding such a product up
    to "matched" or down to "unmatched" would both be lies.
    """
    if is_missing(name):
        return []
    cleaned = _PARENTHETICAL.sub("", name)
    seen: list[str] = []
    for part in _SEPARATORS.split(cleaned):
        component = ids.normalise_name(part)
        if component and component not in seen:
            seen.append(component)
    return seen


def strip_salt(name: str, suffixes: frozenset[str]) -> str | None:
    """Drop ONE trailing salt/hydrate token, or None if there is nothing to drop.

    Deliberately dumb, and deliberately NOT safe to use on its own: "Dimethyl
    fumarate" is an INN in its own right, so calling this eagerly would turn a
    correct match into a miss. The SAFEGUARD IS THE CALLER'S ORDERING -- try the
    unstripped name first, come here only when it misses (see pbs_run.resolve).

    Returns None rather than the unchanged name so the caller cannot accidentally
    retry an identical lookup, and never strips a name down to nothing (a bare
    "sodium" would otherwise become "" and match indiscriminately).
    """
    words = name.split()
    if len(words) < 2 or words[-1].lower() not in suffixes:
        return None
    return " ".join(words[:-1])


@dataclass(frozen=True)
class PbsItem:
    """One PBS item instance, reduced to the licence-clean fields drugref keeps.

    This dataclass IS the quarantine boundary (spec section 6): it has no field
    for an ATC code or an AMT/SNOMED concept id, so no amount of downstream
    carelessness can put one in the database. items.csv carries neither today --
    they live in separate files the ingest never opens -- and the fixed allow-list
    in parse_items keeps that true if a future release changes its mind.
    """
    source_code: str | None       # li_item_id -- unique per row upstream, or None
                                  # if the row carried no usable value (see
                                  # parse_items: the identity gate that refuses
                                  # such a row lives with the orchestrator, not here)
    pbs_code: str | None          # the Item Code: an attribute, NOT the key
    brand_name: str | None
    drug_name: str | None         # li_drug_name, falling back to drug_name
    form_strength: str | None
    program_code: str | None
    benefit_type_code: str | None  # U/R/S/A


def _clean(row: dict[str, str], column: str) -> str | None:
    """Read one column, mapping blank and the 'null' sentinel to None."""
    value = row.get(column)
    return None if is_missing(value) else value.strip()


def parse_items(path: str | pathlib.Path) -> Iterator[PbsItem]:
    """Stream tables_as_csv/items.csv, yielding one PbsItem per CSV row.

    A GENERATOR, so the 8.3 MB file never lands in memory at once -- the same
    streaming discipline mesh.py applies, and the reason the production-ingest
    follow-up (#7) does not apply to this feed.

    Opened with utf-8-sig because the real files carry a BOM: read as plain utf-8,
    the first column name arrives as '﻿li_item_id' and every lookup of it
    silently misses, yielding rows that are entirely empty.

    THE COLUMN ITSELF IS CHECKED EAGERLY, before the per-row loop (review round,
    finding 1). If a future release renames li_item_id, every row would otherwise
    be missing the key, this generator would silently yield PbsItem after PbsItem
    with source_code=None, and the caller (pbs_run.ingest_pbs) would count every
    one of them as rows_without_identity and write NOTHING -- but only after
    clear_source_products had already deleted the previous release's rows. That
    reads as a successful, empty re-ingest with items_read == 0 and no error: the
    same silent-drift failure mode issue #27 found in ingest/unii.py (a renamed
    column there quietly disabled matching with no exception). A missing COLUMN
    is a broken upstream contract, not a per-row data condition, so it raises
    immediately instead of being discovered one skipped row at a time.

    Rows with no li_item_id VALUE (the column exists, the row just has none) are
    still YIELDED, with source_code=None, rather than skipped here: refusing such
    a row is an identity-gate decision -- the product UUID derives from that
    value, so an empty one would mint a single shared UUID every such row
    collapses onto -- and that gate belongs with the orchestrator, exactly as
    gate.has_identity_key belongs with ingest/run.py rather than ingest/unii.py.
    Counting it there (PbsSummary.rows_without_identity) is what makes a skipped
    row visible instead of silently dropped.
    """
    with open(path, newline="", encoding="utf-8-sig") as fh:
        reader = csv.DictReader(fh)
        if not reader.fieldnames or "li_item_id" not in reader.fieldnames:
            raise ValueError(
                "PBS items.csv is missing the 'li_item_id' column "
                f"(columns found: {reader.fieldnames!r}). Both the product "
                "UUID and the identity gate key on this column; a rename or "
                "drop upstream is a broken contract, not a row-level data "
                "condition, so parsing refuses rather than silently yielding "
                "zero usable rows.")
        for row in reader:
            source_code = _clean(row, "li_item_id")
            # li_drug_name is the legally-determined name and the better key;
            # drug_name is the Medicinal Product Pack name and covers the 159
            # rows where the former is the 'null' sentinel.
            drug_name = _clean(row, "li_drug_name") or _clean(row, "drug_name")
            yield PbsItem(
                source_code=source_code,
                pbs_code=_clean(row, "pbs_code"),
                brand_name=_clean(row, "brand_name"),
                drug_name=drug_name,
                form_strength=_clean(row, "li_form") or _clean(row, "schedule_form"),
                program_code=_clean(row, "program_code"),
                benefit_type_code=_clean(row, "benefit_type_code"),
            )
