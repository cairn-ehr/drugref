"""Parse the FDA UNII data file into moiety-candidate records.

Each row is one substance. We extract the UNII (identity key), the preferred
term, the has-INN membership signal (presence of INN_ID -> the substance has a
WHO INN, design §6.1), and the cheap cross-references (CAS/RxCUI/PubChem/
InChIKey) that make drugref a public identifier cross-walk. The gate itself
lives in gate.py; this module only reads the file.
"""
import csv
import pathlib
from dataclasses import dataclass, field
from typing import Iterator

# Map UNII column headers -> the identity-claim scheme we store them under.
_CROSS_REF_COLUMNS = {
    "RN": "CAS",
    "RXCUI": "RXNORM_IN",
    "PUBCHEM": "PUBCHEM_CID",
    "INCHIKEY": "INCHIKEY",
}


@dataclass
class MoietyCandidate:
    unii: str
    preferred_name: str
    has_inn: bool
    cross_refs: dict[str, str] = field(default_factory=dict)


def parse(path: str | pathlib.Path) -> Iterator[MoietyCandidate]:
    """Yield one MoietyCandidate per row of the UNII data file."""
    with open(path, newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh, delimiter="\t"):
            # `csv.DictReader` yields None for any column missing from a short row,
            # so coerce with `or ""` before stripping (None.strip() would crash).
            cross_refs = {
                scheme: (row.get(col) or "").strip()
                for col, scheme in _CROSS_REF_COLUMNS.items()
                if (row.get(col) or "").strip()      # omit empty/absent upstream cells
            }
            yield MoietyCandidate(
                unii=(row.get("UNII") or "").strip(),
                preferred_name=(row.get("PT") or "").strip(),
                has_inn=bool((row.get("INN_ID") or "").strip()),
                cross_refs=cross_refs,
            )
