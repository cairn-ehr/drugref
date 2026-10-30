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
   AND yield items, and the page's three modification stamps must agree (the
   vocabulary and role/potency guards live in fda_cyp_cell.parse_cell). A
   lenient parse of the real page produces 69 classes instead of 65 while
   reporting zero errors, and four of them are garbage minted with real immortal
   UUIDs ('cyp:1a2 20', 'transporter:oatp1b1 inhibitor'). Those four are what
   this parser's strictness (this module and fda_cyp_cell) is for.

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

from drugref.ingest.fda_cyp_cell import (
    ROLE_COLUMNS,
    FdaCypParseError,
    parse_cell,
    split_footnotes,
)

# The data is the FIRST table on the page; tables 2-6 are the potency legends
# (definitions of strong/moderate/weak), which drugref does not ingest -- it
# stores the class, not the pharmacokinetics.
DATA_TABLE_INDEX = 0

# One substance column + ten role columns. ASSERTED, not assumed: the real page
# measures Counter({11: 245}), so a ragged row is a source change.
EXPECTED_COLUMNS = 11


_TABLE = re.compile(r"<table.*?</table>", re.S)
_ROW = re.compile(r"<tr.*?</tr>", re.S)
_CELL = re.compile(r"<t[hd].*?</t[hd]>", re.S)
_TAG = re.compile(r"<[^>]+>")
_SPACE = re.compile(r"\s+")


def _clean(fragment: str) -> str:
    """One HTML cell to its visible text: tags out, entities decoded, spaces collapsed.

    Collapsing internal whitespace matters rather than being tidy -- FDA's cells
    carry newlines and non-breaking spaces from the CMS, and '1A2  weak' must read
    identically to '1A2 weak' or the role cross-check fires on a formatting
    difference.
    """
    return _SPACE.sub(" ", html.unescape(_TAG.sub(" ", fragment))).strip()


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


def parse_table(page: str) -> list[CypTuple]:
    """The whole table to its tuples, in row then column order.

    THE RETURN GRAIN IS ONE TUPLE PER (row x column x PATHWAY), not one per
    cell: a single cell reading 'P-gp; BCRP inhibitor' is two facts and yields
    two CypTuples. On the 2026-05-29 release, 245 rows and 337 non-empty cells
    produce 419 tuples.

    Raises FdaCypParseError for every structural failure documented here and in
    fda_cyp_cell.parse_cell -- no table, no rows, a ragged row or header, and
    (from parse_cell, whose docstring lists all five) a cell that states no
    role phrase or whose role or potency disagrees with its column, an unknown
    or wrong-system pathway, or a column index that is not a role column. It does NOT
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
                # see the CELL-LEVEL MARKER comment in fda_cyp_cell.parse_cell)
                # qualifies only where it sits.
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
    # ingest_run history (append-only) cannot be corrected afterwards.
    # check_fda_cyp_release already refuses when the OPERATOR disagrees with
    # the page; this is that same check applied to the page disagreeing with
    # itself.
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
# conivaptan position, not the mid-cell one: "mid-cell" in this parser means a
# marker on ONE ITEM of a list, as in ciprofloxacin's "1A2 20 ; 3A moderate
# inhibitor", and the two are handled by different code paths, both in
# fda_cyp_cell.parse_cell) but is NEVER
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
    in the returned dict, and the caller (fda_cyp_disposition._footnote_text,
    reached through fda_cyp_run) decides what an absent lookup means for a
    given row rather than this function deciding for it.
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
