# src/drugref/ingest/fda_cyp.py
"""Parse FDA's CYP/transporter examples table. PURE: no DB, no network.

The architecture invariant every parser here follows -- parsers are pure and
streaming, orchestrators own the transaction and are the only writers.

WHAT THE SOURCE IS. One HTML page carrying six tables; the first is the data.
It is a MATRIX, not a list of facts: 245 data rows x 11 columns, where the first
column names the substance and EACH OF THE OTHER TEN IS a (system, role,
potency) tuple. The cell holds the pathway list. So one cell such as
'P-gp; BCRP inhibitor' in the TRNSP INH column is two facts, and the whole table
is 337 non-empty cells expanding to 419 tuples over 65 classes.

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
import dataclasses
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


# A footnote marker is a whitespace-separated BARE INTEGER or SINGLE LOWER-CASE
# LETTER, optionally repeated and comma-separated, at the very end of the text.
#
# THE PRECISION IS THE POINT, in both directions:
#  * too loose eats real name characters -- 'peginterferon alpha-2a' ends in '2a'
#    and 'MATE2-K substrate' contains '2-K', neither of which is a marker;
#  * too tight misses 'ritonavir 14, 15,' -- markers can be a comma-separated
#    list WITH A TRAILING COMMA, and missing it drops ritonavir from the ingest
#    silently while the run reports success.
_FOOTNOTE_TAIL = re.compile(r"((?:\s+(?:\d+|[a-z])\s*,?)+)\s*$")
_MARKER = re.compile(r"\d+|[a-z]")


def split_footnotes(text: str) -> tuple[str, str | None]:
    """Split trailing footnote markers off a substance name or cell body.

    Returns (text_without_markers, "14, 15") or (text, None).

    Markers appear in THREE positions on this page and this handles the trailing
    one; a marker attached to an individual pathway MID-cell (ciprofloxacin's
    '1A2 20 ; 3A moderate inhibitor') is handled by the cell parser, which calls
    this per list item.
    """
    match = _FOOTNOTE_TAIL.search(text)
    if not match:
        return text, None
    markers = _MARKER.findall(match.group(1))
    if not markers:
        return text, None
    return text[:match.start()].strip(), ", ".join(markers)


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


# THE CLOSED PATHWAY VOCABULARY. An unrecognised token aborts the ingest.
#
# This is not defensiveness; it is the finding that justified the whole module.
# A lenient parse of the real page -- one that strips trailing footnotes and
# accepts whatever remains -- produces 69 classes instead of 65 while reporting
# ZERO errors, and four are garbage minted with real immortal UUIDs:
#   cyp:1a2 20:inhibitor:moderate            (ciprofloxacin, mid-cell footnote)
#   transporter:oatp1b1 13:inhibitor         (rifampin, footnote on both pathways)
#   transporter:oatp1b3 13:inhibitor
#   transporter:oatp1b1 inhibitor:inhibitor  (teriflunomide, per-item role phrase)
#
# OATP1B is listed SEPARATELY from OATP1B1 and OATP1B3 and is never expanded into
# them: FDA writes the coarser name on some rows, and expanding it would
# manufacture a specificity FDA declined to state.
#
# PARTITIONED BY SYSTEM, not one flat set: ROLE_COLUMNS declares a 'CYP' or
# 'transporter' system per column, and a token must belong to the RIGHT one --
# 'OATP1B1' (a transporter) is not a valid CYP enzyme even though it is a valid
# pathway in general, and a flat set would silently accept it under a CYP
# column, minting a nonsense class such as 'cyp:oatp1b1:inhibitor:moderate'.
CYP_PATHWAYS = frozenset({"1A2", "2B6", "2C8", "2C9", "2C19", "2D6", "3A"})
TRANSPORTER_PATHWAYS = frozenset({
    "P-gp", "BCRP", "OATP1B1", "OATP1B3", "OATP1B",
    "OAT1", "OAT3", "OCT2", "MATE1", "MATE2-K",
})

# The union, kept for callers (and tests) that only need "is this pathway known
# at all", independent of which system it belongs to.
PATHWAYS = CYP_PATHWAYS | TRANSPORTER_PATHWAYS

# ROLE_COLUMNS' own system strings ('CYP', 'transporter') index straight into
# this -- the per-column vocabulary a token must belong to.
_PATHWAYS_BY_SYSTEM: dict[str, frozenset[str]] = {
    "CYP": CYP_PATHWAYS,
    "transporter": TRANSPORTER_PATHWAYS,
}

# Case-folded lookup, so 'p-gp' and 'P-gp' are one pathway while the CANONICAL
# spelling (which reaches source_code and class_name) stays FDA's own.
_PATHWAY_BY_FOLD = {p.upper(): p for p in PATHWAYS}

# The role phrase that closes a cell (or a list item). 'moderately sensitive' and
# 'moderate sensitive' are the SAME band under two spellings -- the legend says
# one, some cells say the other.
_ROLE_PHRASE = re.compile(
    r"\b(strong|moderate|moderately|weak|sensitive|"
    r"moderate sensitive|moderately sensitive)?\s*"
    r"(inhibitors?|inducers?|substrates?)\s*$", re.I)

# Separators. THREE spellings of one concept: ';', ',' and the word 'and'.
_SEPARATOR = re.compile(r";|,|\band\b", re.I)

# Nouns FDA appends to a pathway list ('BCRP and P-gp transporters'). Not pathways.
_TRAILING_NOUN = re.compile(r"\b(transporters?|enzymes?)\b", re.I)


@dataclasses.dataclass(frozen=True)
class CypTuple:
    """One (substance x pathway x role x potency) fact, before any DB contact.

    row_ordinal is carried because THE SUBSTANCE NAME IS NOT A KEY: aprepitant
    occupies two rows, and FDA publishes no row identifier, so the 1-based
    position is the only stable within-release handle back to the exact line.

    raw_substance keeps FDA's printed form INCLUDING markers ('ritonavir 14, 15,')
    while `substance` is the cleaned name -- the raw fact and the derived one are
    both stored, never one in place of the other.
    """
    row_ordinal: int
    raw_substance: str
    substance: str
    column_heading: str
    raw_cell: str
    system: str
    pathway: str
    role: str
    potency: str | None
    footnote_markers: str | None


def _normalise_potency(word: str | None) -> str | None:
    """Fold a cell's potency word onto the column's spelling of the same band.

    'moderately' -> 'moderate' handles plain cells ('2D6 moderately inhibitor').
    'moderately sensitive' -> 'moderate sensitive' handles the substrate columns,
    where the LEGEND's word ('Mod SENS SUB') and the CELL's word ('moderately
    sensitive substrate') are the same band spelled two ways -- design spec
    section 2.2 finding 4. Without this second mapping the cross-check computes
    'moderate sensitive'.startswith('moderately sensitive') == False and a
    perfectly good cell aborts the ingest.
    """
    if word is None:
        return None
    folded = word.lower()
    if folded == "moderately":
        return "moderate"
    if folded == "moderately sensitive":
        return "moderate sensitive"
    return folded


def parse_cell(raw_cell: str, column_index: int,
               column_heading: str) -> list[tuple[str, str | None]]:
    """One cell to its (pathway, footnote_markers) pairs.

    THE GRAMMAR, derived from the real bytes rather than assumed: a cell is a
    list of `pathway [footnote] [role phrase]` items separated by ';', ',' or the
    word 'and', closed by a trailing role phrase that applies to every item which
    did not state its own.

    Raises FdaCypParseError on an unknown pathway token, or when the cell's own
    role/potency disagrees with the column it sits in.
    """
    system, column_role, column_potency = ROLE_COLUMNS[column_index]
    # THE CELL-LEVEL MARKER MUST BE KEPT, NOT DISCARDED. conivaptan's cell reads
    # '3A moderate inhibitor 5' -- the trailing '5' qualifies the WHOLE cell, and
    # dropping it here would let that membership through unwithheld, which is the
    # exact defect this slice exists to prevent (design section 5, the 29
    # qualified-cell figure). It is merged into every pair this call returns,
    # below, alongside any per-item marker.
    body, cell_markers = split_footnotes(raw_cell)

    match = _ROLE_PHRASE.search(body)
    if not match:
        raise FdaCypParseError(
            f"cell {raw_cell!r} in column {column_heading!r} states no role phrase")

    # THE CROSS-CHECK. The page states role and potency twice, so verifying them
    # against each other costs nothing and catches a source whose shape changed
    # under an unchanged checksum. Preferring one over the other would hide it.
    cell_role = match.group(2).lower().rstrip("s")
    cell_potency = _normalise_potency(match.group(1))
    if cell_role != column_role:
        raise FdaCypParseError(
            f"cell {raw_cell!r} says role {cell_role!r} but column "
            f"{column_heading!r} says {column_role!r} -- they disagree")
    # THE CHECK MUST BE TOTAL, not "compare if both sides happen to state one".
    # An `is not None and is not None` guard SKIPS rather than FAILS whenever
    # either side is missing -- and 'skipped' is exactly the silent pass spec
    # section 8's cross-check exists to convert into a stopped ingest. Both
    # missing-side shapes are real drift a re-fetch could introduce: a CYP
    # column cell that stops stating its band ('3A inhibitor' under 'CYP Mod
    # INH'), or a transporter cell that starts stating one ('P-gp strong
    # inhibitor' under 'TRNSP INH', which has no potency vocabulary at all).
    if column_potency is None:
        if cell_potency is not None:
            raise FdaCypParseError(
                f"cell {raw_cell!r} states potency {cell_potency!r} but column "
                f"{column_heading!r} declares no potency vocabulary -- they disagree")
    elif cell_potency is None:
        raise FdaCypParseError(
            f"cell {raw_cell!r} states no potency but column {column_heading!r} "
            f"says {column_potency!r} -- they disagree")
    elif not column_potency.startswith(cell_potency):
        # 'moderate sensitive' is spelled 'moderately sensitive' in some cells and
        # reaches here already folded onto the column's spelling.
        raise FdaCypParseError(
            f"cell {raw_cell!r} says potency {cell_potency!r} but column "
            f"{column_heading!r} says {column_potency!r} -- they disagree")

    listed = _TRAILING_NOUN.sub("", body[:match.start()]).strip().rstrip(",")
    pairs: list[tuple[str, str | None]] = []
    for item in (part.strip() for part in _SEPARATOR.split(listed)):
        if not item:
            continue
        # Each item may carry its OWN role phrase (teriflunomide) and its OWN
        # footnote (ciprofloxacin, rifampin). The role phrase MUST be peeled off
        # BEFORE split_footnotes runs: rifampin's second item is 'OATP1B3 13
        # inhibitor', where the marker sits BEFORE the (per-item) role word, not
        # at the item's trailing edge -- split_footnotes only strips a TRAILING
        # marker, so it would see 'inhibitor' at the tail and find no marker at
        # all unless the role word is gone first.
        item = _ROLE_PHRASE.sub("", item).strip()
        token, item_markers = split_footnotes(item)
        token = re.sub(r"^CYP", "", token, flags=re.I).strip()
        if not token:
            continue
        canonical = _PATHWAY_BY_FOLD.get(token.upper())
        if canonical is None:
            raise FdaCypParseError(
                f"unknown pathway {token!r} in cell {raw_cell!r} "
                f"(column {column_heading!r}). The closed vocabulary is "
                f"{sorted(PATHWAYS)}. Widen it deliberately or fix the parse -- "
                "accepting it would mint a class with an immortal UUID.")
        # A pathway can be REAL and still be wrong HERE: 'OATP1B1' is a genuine
        # transporter, but a CYP column naming it would mint a class under the
        # wrong system ('cyp:oatp1b1:...'). The vocabulary a token must belong
        # to is the COLUMN's declared system, not the flat union.
        allowed_for_system = _PATHWAYS_BY_SYSTEM[system]
        if canonical not in allowed_for_system:
            raise FdaCypParseError(
                f"pathway {canonical!r} in cell {raw_cell!r} does not belong to "
                f"the {system!r} system column {column_heading!r} declares. The "
                f"closed {system!r} vocabulary is {sorted(allowed_for_system)}. "
                "Accepting it would mint a class under the wrong system.")
        # A cell-level marker qualifies EVERY item in the cell; an item-level one
        # qualifies only that item. Both are kept, joined, rather than one
        # overwriting the other.
        markers = ", ".join(m for m in (cell_markers, item_markers) if m) or None
        pairs.append((canonical, markers))
    return pairs


def parse_table(page: str) -> list[CypTuple]:
    """The whole table to its tuples, in row then column order."""
    headings = _column_headings(page)
    tuples: list[CypTuple] = []
    for ordinal, row in enumerate(extract_rows(page), start=1):
        raw_substance = row[0]
        # Computed ONCE per row, not once per role column: split_footnotes does
        # the same regex work every time it is called, and calling it again
        # inside the column loop below would be a second copy of one fact that
        # could silently drift from this one.
        substance, row_markers = split_footnotes(raw_substance)
        for index in sorted(ROLE_COLUMNS):
            raw_cell = row[index]
            if not raw_cell:
                continue
            system, role, potency = ROLE_COLUMNS[index]
            heading = headings[index]
            for pathway, cell_markers in parse_cell(raw_cell, index, heading):
                # A row-level marker qualifies EVERY cell in that row; a cell- or
                # item-level one qualifies only where it sits. Both are kept.
                markers = ", ".join(m for m in (row_markers, cell_markers) if m) or None
                tuples.append(CypTuple(
                    row_ordinal=ordinal, raw_substance=raw_substance,
                    substance=substance, column_heading=heading, raw_cell=raw_cell,
                    system=system, pathway=pathway, role=role, potency=potency,
                    footnote_markers=markers))
    return tuples


def _column_headings(page: str) -> list[str]:
    """FDA's own column headings, read from the header row rather than restated.

    Restating them here would be a second copy of a vocabulary the page already
    publishes -- the 'written down twice' hazard this project keeps paying for.
    """
    tables = _TABLE.findall(page)
    header = _ROW.findall(tables[DATA_TABLE_INDEX])[0]
    return [_clean(cell) for cell in _CELL.findall(header)]


# FDA's CMS prints the modification stamp in three places with one format:
# JSON-LD "dateModified", og:updated_time and article:modified_time.
# Accepting all three is not redundancy -- it is not knowing which the CMS will
# keep, and they are read in this order.
_MODIFIED = re.compile(
    r'"dateModified"\s*:\s*"([^"]+)"'
    r'|(?:article:modified_time|og:updated_time)"\s+content="([^"]+)"')

# 'Fri, 05/29/2026 - 14:00'
_STAMP = re.compile(r"(\d{2})/(\d{2})/(\d{4})\s*-\s*(\d{2}:\d{2})")


def parse_release(page: str) -> str:
    """The page's own modification stamp, as '2026-05-29T14:00'.

    WHY NOT FETCH TIME, which the source spike proposed: fetch time records when
    drugref looked, and dateModified records when FDA changed the content. Only
    the second can tell a re-fetch of unchanged material from a genuine revision
    -- which is the question check_release_agreement and every per-source rebuild
    actually ask.

    RAISES rather than falling back. Substituting fetch time would put a value
    with a DIFFERENT MEANING into upstream_release, and one field carrying two
    meanings is a defect this project has already paid for more than once. If FDA
    stops publishing the field, that is a decision for a human, not a default.
    """
    for match in _MODIFIED.finditer(page):
        raw = match.group(1) or match.group(2)
        stamp = _STAMP.search(raw or "")
        if stamp:
            month, day, year, clock = stamp.groups()
            return f"{year}-{month}-{day}T{clock}"
    raise FdaCypParseError(
        "the page carries no dateModified / article:modified_time / "
        "og:updated_time stamp, so its release identity is unknown. Fetch time is "
        "NOT a substitute: it records when drugref looked, not when FDA changed "
        "the content. Decide deliberately before ingesting this page.")
