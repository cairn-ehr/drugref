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
2. The parse is guarded, and every guard RAISES rather than skipping. Asserted
   here: every row's cell count (EXPECTED_COLUMNS exactly, header row included),
   the pathway vocabulary is CLOSED and partitioned by system, the cell's own
   role/potency must agree with its column's, the Footnotes section must exist
   AND yield items, and the page's three modification stamps must agree. A
   lenient parse of the real page produces 69 classes instead of 65 while
   reporting zero errors, and four of them are garbage minted with real immortal
   UUIDs ('cyp:1a2 20', 'transporter:oatp1b1 inhibitor'). Those four are what
   this module's strictness is for.

   WHAT IS DELIBERATELY NOT ASSERTED HERE: the ROW COUNT. An earlier version of
   this docstring claimed "the row and cell COUNTS are asserted (245 x 11
   exactly)" and only the cell count ever existed -- so a truncated page, whose
   surviving rows are all still EXPECTED_COLUMNS wide, parsed green. That guard
   is real but belongs one layer up: 245 is a property of one RELEASE, not of
   the table's shape, and the harm is done by REPLACING a full projection with a
   fraction of itself. fda_cyp_run owns it, because fda_cyp_run is the writer.
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
#  * too tight misses 'ritonavir 14, 15, 16' -- markers can be a comma-separated
#    LIST, so a pattern that only strips one marker leaves '14, 15' glued to the
#    name; the row is not dropped (it becomes unresolved_substance and raises a
#    question), but it is filed under a name FDA never printed, and question_uuid
#    is immortal. The `,?` also tolerates a trailing comma, which today's page
#    does not print -- defensive, not required by it.
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


def _data_table_rows(page: str) -> list[str]:
    """The data table's raw <tr> fragments, header included. The ONE gate.

    Extracted so extract_rows and _column_headings reach the table through the
    SAME guards. They did not before: _column_headings indexed
    `tables[DATA_TABLE_INDEX]` and `_ROW.findall(...)[0]` directly, and
    parse_table calls _column_headings FIRST -- so extract_rows' guards below
    were unreachable through the only caller, and a page with no table (a
    wrong --page, a captured error page) raised a bare IndexError instead of
    the FdaCypParseError this module documents everywhere.
    """
    tables = _TABLE.findall(page)
    if len(tables) <= DATA_TABLE_INDEX:
        raise FdaCypParseError(
            f"page carries {len(tables)} table(s); the data table is index "
            f"{DATA_TABLE_INDEX}. The page structure changed.")
    rows = _ROW.findall(tables[DATA_TABLE_INDEX])
    if not rows:
        raise FdaCypParseError("the data table carries no rows")
    return rows


def extract_rows(page: str) -> list[list[str]]:
    """Every data row of the table, each of EXPECTED_COLUMNS cleaned cells.

    Header excluded. 245 rows on the 2026-05-29 release -- a figure OF THAT
    RELEASE, not a contract this function enforces: the only shape asserted
    here is the cell count per row. The guard against a TRUNCATED page (which
    parses green, because its surviving rows are all still 11 cells wide)
    cannot live here, because it is not a property of the table's shape -- it
    lives in fda_cyp_run, which is what replaces the projection and so is what
    must refuse to replace it with a fraction of itself.

    Raises FdaCypParseError if the page has no table, if the table has no
    rows, or if any row does not carry exactly EXPECTED_COLUMNS cells.
    """
    rows = _data_table_rows(page)

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

# A fragment that is NOTHING BUT a footnote marker, left behind when _SEPARATOR
# splits a comma-separated marker LIST. No pathway in the closed vocabulary is a
# bare integer or a single letter, so this can never swallow a real token.
_BARE_MARKER = re.compile(r"^(?:\d+|[a-z])$")


def _split_items(listed: str) -> list[str]:
    """Split a cell's pathway list, keeping comma-separated MARKER LISTS whole.

    _SEPARATOR splits on ',' -- the same character a marker list uses -- and it
    runs before split_footnotes ever sees the item, so '1A2 20, 21' became the
    items '1A2 20' and '21'. The stray '21' then failed the closed-vocabulary
    lookup and aborted the release with a message telling the operator to widen
    PATHWAYS: the wrong fix, for what is a footnote-parsing problem.

    Both halves of that shape are already on the real page independently -- a
    comma-separated list glued to a name ('ritonavir 14, 15, 16') and a
    mid-cell single marker (ciprofloxacin's '1A2 20') -- so their combination
    is a spelling FDA can print at any time, not a hypothetical.

    A fragment that is only a marker is therefore re-attached to the item it
    was split from, which is the item it qualifies.
    """
    items: list[str] = []
    for part in (fragment.strip() for fragment in _SEPARATOR.split(listed)):
        if not part:
            continue
        if items and _BARE_MARKER.match(part):
            items[-1] = f"{items[-1]} {part}"
        else:
            items.append(part)
    return items


def _merge_markers(*groups: str | None) -> str | None:
    """Join marker groups into one comma-separated string, each marker ONCE.

    A cell-level marker qualifies every item, so an item that also states it
    yielded '5, 5' -- and _footnote_text then joined FDA's same prose into
    footnote_text twice. dict.fromkeys rather than a set because the order the
    markers appear in on the page is the order they must be read in.
    """
    markers = [marker.strip() for group in groups if group
               for marker in group.split(",") if marker.strip()]
    return ", ".join(dict.fromkeys(markers)) or None


@dataclasses.dataclass(frozen=True)
class CypTuple:
    """One (substance x pathway x role x potency) fact, before any DB contact.

    row_ordinal is carried because THE SUBSTANCE NAME IS NOT A KEY: aprepitant
    occupies two rows, and FDA publishes no row identifier, so the 1-based
    position is the only stable within-release handle back to the exact line.

    raw_substance keeps FDA's printed form INCLUDING markers ('ritonavir 14, 15,
    16') while `substance` is the cleaned name -- the raw fact and the derived
    one are both stored, never one in place of the other. (Quote that string
    carefully: 'ritonavir 14, 15,' -- with a trailing comma and no 16 -- is the
    DESIGN ROUND'S PROBE OUTPUT, not FDA's text, and spec section 2.3 records
    that it "appears nowhere on FDA's page".)

    footnote_markers, row_footnote_markers AND cell_footnote_markers -- THREE
    fields, not one, because WHERE a marker sits on the page is part of what it
    means. A marker glued to the substance NAME is a claim about the substance;
    one attached inside a CELL (trailing the whole cell, or mid-cell on one
    pathway item) is a claim about that specific role/pathway. Merging both into
    one string (which footnote_markers still does, for every caller that only
    needs "is this row qualified at all" -- disposition and the footnote-text
    lookup) is exactly right for THAT question and wrong for "does this
    footnote narrow or negate THIS CELL'S membership", which can only be
    answered honestly by knowing whether the marker was ever attached to the
    cell in the first place. row_footnote_markers and cell_footnote_markers
    keep that distinction alive past parse_table; footnote_markers is their
    join (row_footnote_markers, then cell_footnote_markers, matching the order
    they are found in on the page), kept for backward compatibility with
    everything that only needs the merged fact.
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
    row_footnote_markers: str | None
    cell_footnote_markers: str | None


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

    Raises FdaCypParseError on ALL FIVE of its failures, listed in full because
    a partial contract is what lets a caller catch the wrong thing: when
    column_index is out of range, when the cell states no role phrase at all,
    when the cell's own role/potency disagrees with the column it sits in, on an
    unknown pathway token, and on a pathway that is real but belongs to a
    DIFFERENT system than the column declares.
    """
    if column_index not in ROLE_COLUMNS:
        # Guarded explicitly rather than left to the dict lookup below: an
        # unguarded ROLE_COLUMNS[column_index] raises a bare KeyError, which
        # contradicts this docstring's own documented contract (every failure
        # here is an FdaCypParseError) and would surprise a caller that
        # legitimately catches only the documented type.
        raise FdaCypParseError(
            f"column index {column_index!r} (heading {column_heading!r}) is not "
            f"one of the {len(ROLE_COLUMNS)} declared role columns "
            f"{sorted(ROLE_COLUMNS)} -- the table's shape changed.")
    system, column_role, column_potency = ROLE_COLUMNS[column_index]
    # THE CELL-LEVEL MARKER MUST BE KEPT, NOT DISCARDED. conivaptan's cell reads
    # '3A moderate inhibitor 5' -- the trailing '5' qualifies the WHOLE cell, and
    # dropping it here would let that membership through unwithheld, which is the
    # exact defect this slice exists to prevent (design section 5: 31 of 337
    # cells, over 24 substances, expanding to 38 tuples -- the figure was
    # re-measured with the shipped parser and corrects the design round's "29
    # cells over 22 substances", in the direction that matters). It is merged
    # into every pair this call returns, below, alongside any per-item marker.
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
        # THE LENIENCY THIS BRANCH EXISTS FOR, quoted verbatim from the pinned
        # page: atorvastatin's real 'CYP Mod SENS SUB' cell reads
        # '3A moderate substrate' -- FDA drops the word 'sensitive' from the
        # CELL entirely while the COLUMN still declares the full band
        # 'moderate sensitive'. That is a THIRD spelling, distinct from
        # 'moderately sensitive' (which _normalise_potency already folds onto
        # 'moderate sensitive' above, and which therefore compares EQUAL, never
        # reaching this branch at all). Only a startswith comparison admits the
        # shortened cell; replacing it with == aborts the real page on this
        # exact cell (pinned by
        # test_atorvastatins_real_cell_drops_the_word_sensitive_entirely,
        # which quotes the byte-for-byte cell). 'moderate substrate' is in NO
        # test fixture and NOT exercised by anything CI runs -- downloads/ is
        # gitignored, so only a skipif(not REAL_PAGE.exists()) test would ever
        # see it -- which is exactly why a "tightening" here would go green in
        # CI and abort the real ingest.
        raise FdaCypParseError(
            f"cell {raw_cell!r} says potency {cell_potency!r} but column "
            f"{column_heading!r} says {column_potency!r} -- they disagree")

    # rstrip(',') because removing a trailing noun can leave the separator that
    # introduced it dangling ('BCRP and P-gp transporters inhibitor' -> 'BCRP
    # and P-gp' is clean, but a comma-separated spelling would leave 'BCRP,').
    listed = _TRAILING_NOUN.sub("", body[:match.start()]).strip().rstrip(",")
    pairs: list[tuple[str, str | None]] = []
    for item in _split_items(listed):
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
        # FDA writes the SAME pathway both bare ('3A') and CYP-prefixed
        # ('CYP3A'), while the closed vocabulary keys on the bare form -- so
        # this line is what makes every 'CYP3A' cell resolve at all. Deleting
        # it turns each of them into an "unknown pathway" abort.
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
        # overwriting the other -- and each marker appears once, however many
        # scopes state it.
        markers = _merge_markers(cell_markers, item_markers)
        pairs.append((canonical, markers))
    return pairs


def parse_table(page: str) -> list[CypTuple]:
    """The whole table to its tuples, in row then column order.

    THE RETURN GRAIN IS ONE TUPLE PER (row x column x PATHWAY), not one per
    cell: a single cell reading 'P-gp; BCRP inhibitor' is two facts and yields
    two CypTuples. On the 2026-05-29 release, 245 rows and 337 non-empty cells
    produce 419 tuples.

    Raises FdaCypParseError for every structural failure the module documents --
    no table, no rows, a ragged row or header, a cell whose role or potency
    disagrees with its column, an unknown or wrong-system pathway. It does NOT
    read the footnote prose or the release stamp; parse_footnotes and
    parse_release are separate calls, so the orchestrator can order them.
    """
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
                # A row-level (name-glued) marker qualifies EVERY cell in that
                # row; a cell- or item-level one (parse_cell's own return,
                # already itself a merge of the two WITHIN-cell positions --
                # see parse_cell's comment above) qualifies only where it sits.
                # row_markers and cell_markers are kept SEPARATE here (issue
                # 122's shape applied to footnote SCOPE) so a caller can tell
                # "FDA qualified this substance" from "FDA qualified THIS
                # CELL" -- footnote_markers is still their join, for every
                # caller that only needs the merged fact.
                markers = ", ".join(m for m in (row_markers, cell_markers) if m) or None
                tuples.append(CypTuple(
                    row_ordinal=ordinal, raw_substance=raw_substance,
                    substance=substance, column_heading=heading, raw_cell=raw_cell,
                    system=system, pathway=pathway, role=role, potency=potency,
                    footnote_markers=markers,
                    row_footnote_markers=row_markers,
                    cell_footnote_markers=cell_markers))
    return tuples


def _column_headings(page: str) -> list[str]:
    """FDA's own column headings, read from the header row rather than restated.

    Restating them here would be a second copy of a vocabulary the page already
    publishes -- the 'written down twice' hazard this project keeps paying for.

    ASSERTED, not assumed: extract_rows checks every DATA row's cell count but
    deliberately skips rows[0] -- the header -- so a header that lost a column
    would otherwise sail through unchecked, and parse_table's `headings[index]`
    lookup would then raise a bare IndexError on a real column instead of the
    documented FdaCypParseError. This is the header-row half of the same
    integrity gate extract_rows already applies to every data row.
    """
    header = _data_table_rows(page)[0]
    headings = [_clean(cell) for cell in _CELL.findall(header)]
    if len(headings) != EXPECTED_COLUMNS:
        raise FdaCypParseError(
            f"the header row has {len(headings)} cells, expected "
            f"{EXPECTED_COLUMNS}. The table's shape changed.")
    return headings


# FDA's CMS prints the modification stamp in three places with one format:
# JSON-LD "dateModified", og:updated_time and article:modified_time.
# Accepting all three is not redundancy -- it is not knowing which the CMS will
# keep. They are NOT "read in this order": `finditer` below walks the page in
# DOCUMENT POSITION order, not in the order the three alternatives are written
# in this pattern, so which one is tried first depends on where each happens
# to sit on the page, not on this regex's own alternation order.
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

    TWO DIFFERENT FAILURES SHARE THIS FUNCTION, and they get TWO DIFFERENT
    MESSAGES rather than one message asserting a cause it has not confirmed
    (issue 122's shape): the field may be ABSENT altogether, or it may be
    PRESENT with a value the MM/DD/YYYY - HH:MM stamp regex cannot read (a CMS
    reformat, say). `candidate` tracks whether any of the three spellings was
    ever found at all, so the raised message names the failure that actually
    happened instead of defaulting to "absent" for both.
    """
    candidate = None
    readable: dict[str, str] = {}
    for match in _MODIFIED.finditer(page):
        raw = match.group(1) or match.group(2)
        candidate = raw
        stamp = _STAMP.search(raw or "")
        if stamp:
            month, day, year, clock = stamp.groups()
            readable[f"{year}-{month}-{day}T{clock}"] = raw
    # A THIRD FAILURE THE TWO MESSAGES BELOW DID NOT COVER: present, readable,
    # and CONTRADICTORY. The old loop returned on the first readable stamp
    # without ever comparing the three, so a CMS that updates og:updated_time
    # but not the JSON-LD dateModified wrote a WRONG upstream_release -- and
    # ingest_run history, as this module's docstring says, cannot be corrected
    # afterwards. check_fda_cyp_release already refuses when the OPERATOR
    # disagrees with the page; this is that same check applied to the page
    # disagreeing with itself.
    if len(readable) > 1:
        raise FdaCypParseError(
            "the page's dateModified / article:modified_time / og:updated_time "
            f"stamps disagree: {sorted(readable.values())!r} read as "
            f"{sorted(readable)!r}. One page cannot have two release identities "
            "-- decide deliberately which is authoritative before ingesting it.")
    if readable:
        return next(iter(readable))
    if candidate is not None:
        raise FdaCypParseError(
            f"found {candidate!r} in dateModified / article:modified_time / "
            "og:updated_time but could not read it as MM/DD/YYYY - HH:MM. The "
            "field is present; its format changed -- decide deliberately "
            "before ingesting this page.")
    raise FdaCypParseError(
        "the page carries no dateModified / article:modified_time / "
        "og:updated_time stamp, so its release identity is unknown. Fetch time is "
        "NOT a substitute: it records when drugref looked, not when FDA changed "
        "the content. Decide deliberately before ingesting this page.")


# FDA's footnote prose sits in its own page section, OUTSIDE table 1 --
# "<h2>Footnotes</h2>" followed by a flat list of "<p><sup>MARKER</sup>text</p>"
# paragraphs, one per footnote. Markers run 1-21 today, plus a lettered 'b'
# that TRAILS A WHOLE CELL (cenobamate's "CYP3A moderate inducer b" -- the
# conivaptan position, not the mid-cell one: "mid-cell" in this file means a
# marker on ONE ITEM of a list, as in ciprofloxacin's "1A2 20 ; 3A moderate
# inhibitor", and the two are handled by different code paths) but is NEVER
# defined here -- design section 2.3 calls this "a second namespace", and the
# live page's own Footnotes list has no entry for a letter at all. That is a
# page oddity to preserve evidence about, not a defect to paper over: see
# parse_footnotes's own docstring on why an undefined marker does not abort.
_FOOTNOTES_HEADING = re.compile(r"<h2>\s*Footnotes\s*</h2>", re.I)
_FOOTNOTE_ITEM = re.compile(r"<p>\s*<sup>(\w+)</sup>(.*?)</p>", re.S)


def parse_footnotes(page: str) -> dict[str, str]:
    """FDA's own footnote prose, keyed by marker ('2' -> "Bupropion itself...").

    WHY THIS IS STRUCTURAL, NOT HARDCODED. An earlier round of this slice kept
    a hand-copied dict of FDA's footnote text inside the orchestrator, quoted
    verbatim from a checksum-verified fetch. Review caught the defect that
    copy had and this function does not: checksum() and parse_release() exist
    to make a SOURCE CHANGE loud -- if FDA reworded footnote 2 tomorrow, both
    would change and the ingest would still run green, silently writing the
    OLD wording into footnote_text, the one column whose entire job is to
    carry FDA's current words. Reading the page's own Footnotes block on every
    ingest is what keeps footnote_text and the checksum answering the same
    question: what does the page say RIGHT NOW.

    THE SECTION ITSELF MUST EXIST -- absence RAISES, matching parse_release's
    own posture (and extract_rows's, on a missing table, for the identical
    reason). FDA removing or renaming "Footnotes" entirely is a structural
    change this parser must not absorb silently.

    A SINGLE MARKER missing its own definition is different, and is NOT an
    error: FDA's page carries a bare lettered marker ('b', cenobamate) that its
    own Footnotes list never defines at all (design section 2.3's "second
    namespace"). That is evidence to preserve about the page, not a reason to
    abort an otherwise-good row -- so an undefined marker simply has no entry
    in the returned dict, and the caller (fda_cyp_run) decides what an absent
    lookup means for a given row rather than this function deciding for it.
    """
    heading = _FOOTNOTES_HEADING.search(page)
    if not heading:
        raise FdaCypParseError(
            "the page carries no '<h2>Footnotes</h2>' heading, so no footnote "
            "prose can be read at all. That is a structural change to the "
            "page, not a missing footnote -- decide deliberately before "
            "ingesting it.")
    # Bounded to the next heading (or end of page), so a coincidental
    # '<p><sup>...' elsewhere on the page can never be mistaken for a footnote
    # -- the same reason extract_rows is bounded to table[DATA_TABLE_INDEX]
    # rather than searching the whole document.
    tail = page[heading.end():]
    next_heading = re.search(r"<h[12]\b", tail, re.I)
    end = len(tail) if next_heading is None else next_heading.start()
    segment = tail[:end]
    found = {marker: _clean(body) for marker, body in _FOOTNOTE_ITEM.findall(segment)}
    if not found:
        # THE SECTION BEING PRESENT IS NOT THE SAME AS IT BEING READABLE, and
        # only the heading was ever checked. _FOOTNOTE_ITEM requires a BARE
        # '<p><sup>', so a CMS theme change that adds one class attribute
        # leaves the heading in place and silently matches nothing. The old
        # return then handed back {}, _footnote_text found no prose for any
        # qualified row, and every withheld question read "FDA's note: (not
        # captured)" while the run reported success -- the precise inversion
        # of this function's own "a source change made loud" argument above.
        raise FdaCypParseError(
            "the page's '<h2>Footnotes</h2>' section yielded no footnote items "
            "at all. The heading is present, so this is not a missing section: "
            "the paragraph markup changed under it. Decide deliberately before "
            "ingesting this page -- absorbing it silently would blank "
            "footnote_text for every qualified row.")
    return found
