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
from dataclasses import dataclass

from drugref import ids

SALT_SUFFIX_PATH = pathlib.Path(__file__).parent.parent / "data" / "salt_suffixes.tsv"

# PBS's empty-value sentinel is the LITERAL FOUR-LETTER STRING "null", used in 44
# of items.csv's 75 columns. Untreated, drugref would earnestly register a drug
# named "null" -- 159 rows carry li_drug_name = 'null'.
_NULL_SENTINEL = "null"

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
    source_code: str              # li_item_id -- unique per row upstream
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


def parse_items(path: str | pathlib.Path):
    """Stream tables_as_csv/items.csv, yielding one PbsItem per usable row.

    A GENERATOR, so the 8.3 MB file never lands in memory at once -- the same
    streaming discipline mesh.py applies, and the reason the production-ingest
    follow-up (#7) does not apply to this feed.

    Opened with utf-8-sig because the real files carry a BOM: read as plain utf-8,
    the first column name arrives as '﻿li_item_id' and every lookup of it
    silently misses, yielding rows that are entirely empty.

    Rows with no li_item_id are SKIPPED, not defaulted: the product UUID derives
    from that value, so an empty one would mint a single shared UUID that every
    such row collapses onto (the failure gate.has_identity_key exists to prevent
    on the identity spine).
    """
    with open(path, newline="", encoding="utf-8-sig") as fh:
        for row in csv.DictReader(fh):
            source_code = _clean(row, "li_item_id")
            if not source_code:
                continue
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
