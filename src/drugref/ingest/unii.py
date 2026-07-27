"""Parse the FDA UNII data file into moiety-candidate records.

Each row is one substance. We extract the UNII (identity key), the preferred
term, the has-INN membership signal (presence of INN_ID -> the substance has a
WHO INN, design §6.1), and the cheap cross-references (CAS/RxCUI/PubChem/
InChIKey) that make drugref a public identifier cross-walk. The gate itself
lives in gate.py; this module only reads the file.

VERIFY-BEFORE-PRODUCTION, now PERFORMED (issue #27). The header was checked
against the real UNII_Records_26Feb2026.txt and it did NOT match what this module
assumed: there is no `PT` column. The preferred term is `Display Name`; `PT` is a
*value* of the TYPE column in the separate UNII_Names_*.txt file, never a header.
Because the old code read `row.get("PT") or ""`, every real row produced an empty
preferred_name -- and that name becomes the moiety's display_name AND its INN
claim value, and is the key both the legacy allow-list and the USAN<->INN
crosswalk are looked up by. A production run would have completed "successfully"
over a correctly-identified but entirely unlabelled registry, raising nothing.

Two things follow, and they are the actual fix:

1. The REQUIRED columns are declared and CHECKED (_require_columns). A column
   whose absence corrupts identity, labelling or membership now raises on the
   first row instead of degrading to "". The bug was never the wrong column name
   -- names drift, that is ordinary -- it was `or ""` silently absorbing a
   structural mismatch.
2. Cross-ref columns stay OPTIONAL by design: they are cross-walk enrichment, so
   a release that drops PUBCHEM should cost drugref a scheme, not an ingest.

tests/fixtures/unii_subset.tsv is extracted from the real release by a committed
script (tests/fixtures/make_unii_subset.py) rather than hand-written, so it can
no longer disagree with upstream about column names -- which is what let this
survive.
"""
import csv
import pathlib
from dataclasses import dataclass, field
from typing import Iterator, Sequence

# The upstream column holding the substance's preferred term. See the module
# docstring: this is `Display Name`, NOT `PT`.
_NAME_COLUMN = "Display Name"

# Presence of a value here is the has-INN membership signal (design §6.1).
_MEMBERSHIP_COLUMN = "INN_ID"

# Columns whose ABSENCE cannot be tolerated, because each silently corrupts a
# different thing: UNII is the identity the immortal moiety_uuid derives from,
# the name is every label and lookup key downstream, and INN_ID decides who is in
# the registry at all (without it, bool("") gates out every substance and the
# ingest reports a clean run over an empty database).
_REQUIRED_COLUMNS = ("UNII", _NAME_COLUMN, _MEMBERSHIP_COLUMN)

# Map UNII column headers -> the identity-claim scheme we store them under.
# OPTIONAL, unlike _REQUIRED_COLUMNS: an absent one costs a cross-walk scheme.
_CROSS_REF_COLUMNS = {
    "RN": "CAS",
    "RXCUI": "RXNORM_IN",
    "PUBCHEM": "PUBCHEM_CID",
    "INCHIKEY": "INCHIKEY",
}


def _require_columns(header: Sequence[str] | None, path: str | pathlib.Path) -> None:
    """Refuse a file whose header lacks a column drugref cannot work without.

    Raises ValueError naming BOTH the missing columns and the header actually
    found -- the header is what a maintainer needs to fix the constant above, and
    printing it turns "the ingest broke" into a one-line diff.
    """
    present = set(header or ())
    missing = [col for col in _REQUIRED_COLUMNS if col not in present]
    if missing:
        raise ValueError(
            f"{path}: UNII file is missing required column(s) {missing}. "
            f"Header found: {list(header or ())}. See ingest/unii.py -- drugref "
            f"refuses rather than degrading these to empty strings (issue #27).")


@dataclass
class MoietyCandidate:
    unii: str
    preferred_name: str
    has_inn: bool
    cross_refs: dict[str, str] = field(default_factory=dict)


def parse(path: str | pathlib.Path) -> Iterator[MoietyCandidate]:
    """Yield one MoietyCandidate per row of the UNII data file.

    Rows with a blank UNII are still yielded: this module only READS the file,
    and refusing them is an identity decision that belongs with the gate (see
    gate.has_identity_key, applied by ingest/run.py so the refusal is counted).
    """
    with open(path, newline="", encoding="utf-8") as fh:
        # QUOTE_NONE: the UNII file is tab-delimited text with no quoting
        # convention, but csv's default QUOTE_MINIMAL treats a leading double
        # quote as opening a quoted field and then swallows every following line
        # until it finds a closing one. A single stray double-prime in a chemical
        # name would therefore merge an unbounded run of substances into one
        # mangled record, silently. Reading as pure delimited text removes that.
        reader = csv.DictReader(fh, delimiter="\t", quoting=csv.QUOTE_NONE)
        # Header contract first (issue #27). parse() is a generator, so this
        # fires on the first row pulled, not at call time -- early enough that
        # no caller can have acted on a degraded record.
        _require_columns(reader.fieldnames, path)
        for row in reader:
            # `csv.DictReader` yields None for any column missing from a short row,
            # so coerce with `or ""` before stripping (None.strip() would crash).
            cross_refs = {
                scheme: (row.get(col) or "").strip()
                for col, scheme in _CROSS_REF_COLUMNS.items()
                if (row.get(col) or "").strip()      # omit empty/absent upstream cells
            }
            yield MoietyCandidate(
                unii=(row.get("UNII") or "").strip(),
                preferred_name=(row.get(_NAME_COLUMN) or "").strip(),
                has_inn=bool((row.get(_MEMBERSHIP_COLUMN) or "").strip()),
                cross_refs=cross_refs,
            )
