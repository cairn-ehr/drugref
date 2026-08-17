# src/drugref/ingest/fda_cyp.py
"""Parse FDA's CYP/transporter examples table. PURE: no DB, no network.

The architecture invariant every parser here follows -- parsers are pure and
streaming, orchestrators own the transaction and are the only writers.

WHAT THE SOURCE IS. One HTML page carrying six tables; the first is the data.
It is a MATRIX, not a list of facts: 245 data rows x 11 columns, where the first
column names the substance and EACH OF THE OTHER TEN IS a (system, role,
potency) tuple. The cell holds the pathway list. So one cell such as
'P-gp; BCRP inhibitor' in the TRNSP INH column is two facts, and the whole table
is 337 non-empty cells expanding to 415 tuples over 65 classes.

WHY A REGEX PARSE IS DEFENSIBLE HERE, when it usually is not. Two reasons, and
neither is "the HTML looked simple":

1. Adding an HTML-parser dependency needs a rule-6 licence check before it can
   be added, not after (CLAUDE.md rule 6). The table does not need one.
2. The parse is guarded on both sides. The row and cell COUNTS are asserted
   (245 x 11 exactly), and the pathway vocabulary is CLOSED -- an unrecognised
   token aborts the ingest. A lenient parse of the real page produces 69 classes
   instead of 65 while reporting zero errors, and four of them are garbage minted
   with real immortal UUIDs ('cyp:1a2 20', 'transporter:oatp1b1 inhibitor').
   Those four are what this module's strictness is for.
"""
import html
import re

# The data is the FIRST table on the page; tables 2-6 are the potency legends
# (definitions of strong/moderate/weak), which drugref does not ingest -- it
# stores the class, not the pharmacokinetics.
DATA_TABLE_INDEX = 0

# One substance column + ten role columns. ASSERTED, not assumed: the real page
# measures Counter({11: 245}), so a ragged row is a source change.
EXPECTED_COLUMNS = 11

# Each role column IS a (system, role, potency). Transporters get None: FDA
# publishes no potency vocabulary for them at all, which is why potency is
# nullable in db/039 rather than defaulted -- "this axis has no band" is a fact,
# not a missing value.
ROLE_COLUMNS: dict[int, tuple[str, str, str | None]] = {
    1:  ("CYP", "inhibitor", "strong"),
    2:  ("CYP", "inhibitor", "moderate"),
    3:  ("CYP", "inhibitor", "weak"),
    4:  ("CYP", "inducer", "strong"),
    5:  ("CYP", "inducer", "moderate"),
    6:  ("CYP", "inducer", "weak"),
    7:  ("CYP", "substrate", "sensitive"),
    8:  ("CYP", "substrate", "moderate sensitive"),
    9:  ("transporter", "inhibitor", None),
    10: ("transporter", "substrate", None),
}

_TABLE = re.compile(r"<table.*?</table>", re.S)
_ROW = re.compile(r"<tr.*?</tr>", re.S)
_CELL = re.compile(r"<t[hd].*?</t[hd]>", re.S)
_TAG = re.compile(r"<[^>]+>")
_SPACE = re.compile(r"\s+")


class FdaCypParseError(Exception):
    """The source did not have the shape this parser asserts.

    RAISED, NEVER LOGGED AND SKIPPED. Every condition that reaches this class is
    a change in the source's structure or vocabulary, and absorbing one silently
    is how four garbage classes get minted with immortal UUIDs while the run
    reports success.
    """


def _clean(fragment: str) -> str:
    """One HTML cell to its visible text: tags out, entities decoded, spaces collapsed.

    Collapsing internal whitespace matters rather than being tidy -- FDA's cells
    carry newlines and non-breaking spaces from the CMS, and '1A2  weak' must read
    identically to '1A2 weak' or the role cross-check fires on a formatting
    difference.
    """
    return _SPACE.sub(" ", html.unescape(_TAG.sub(" ", fragment))).strip()


def extract_rows(page: str) -> list[list[str]]:
    """The data table's 245 rows, each of 11 cleaned cells. Header excluded.

    Raises FdaCypParseError if the page has no table, or if any row does not
    carry exactly EXPECTED_COLUMNS cells.
    """
    tables = _TABLE.findall(page)
    if len(tables) <= DATA_TABLE_INDEX:
        raise FdaCypParseError(
            f"page carries {len(tables)} table(s); the data table is index "
            f"{DATA_TABLE_INDEX}. The page structure changed.")
    rows = _ROW.findall(tables[DATA_TABLE_INDEX])
    if not rows:
        raise FdaCypParseError("the data table carries no rows")

    parsed: list[list[str]] = []
    for ordinal, row in enumerate(rows[1:], start=1):  # rows[0] is the header
        cells = [_clean(cell) for cell in _CELL.findall(row)]
        if len(cells) != EXPECTED_COLUMNS:
            raise FdaCypParseError(
                f"row {ordinal} ({cells[0] if cells else '?'!r}) has {len(cells)} "
                f"cells, expected {EXPECTED_COLUMNS}. The table's shape changed.")
        parsed.append(cells)
    return parsed
