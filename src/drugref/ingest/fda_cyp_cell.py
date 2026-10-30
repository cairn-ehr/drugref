# src/drugref/ingest/fda_cyp_cell.py
"""The grammar of ONE cell of FDA's CYP/transporter table. PURE: no DB, no network.

Split out of fda_cyp.py (issue 177) along the seam between the module's two
vocabularies. fda_cyp.py keeps the PAGE: where the table is, how many cells a
row carries, the column headings, the release stamp and the footnote prose.
This module keeps the CELL: what each role column means (ROLE_COLUMNS), the
closed pathway vocabulary, the role/potency phrases, how footnote markers are
split off, and parse_cell, which turns one cell into its (pathway, markers)
pairs. fda_cyp.parse_table is the only production caller.

FdaCypParseError is defined HERE, not in fda_cyp.py, only because both halves
raise it and this is the half that imports nothing from the other -- putting it
in fda_cyp.py would make the two modules import each other. fda_cyp.py imports
it and raises it too, so `fda_cyp.FdaCypParseError` is the same class and every
caller that already catches it keeps working.

Why the parse is regex-based and why every guard RAISES is argued once, in
fda_cyp.py's module docstring; the guards that argument lists for the pathway
vocabulary and the role/potency cross-check are the ones in parse_cell below.
"""
import re

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


class FdaCypParseError(Exception):
    """The source did not have the shape this parser asserts.

    RAISED, NEVER LOGGED AND SKIPPED. Every condition that reaches this class is
    a change in the source's structure or vocabulary, and absorbing one silently
    is how four garbage classes get minted with immortal UUIDs while the run
    reports success.
    """


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


# THE CLOSED PATHWAY VOCABULARY. An unrecognised token aborts the ingest.
#
# This is not defensiveness; it is the finding that justified the whole parser
# (this module and fda_cyp.py).
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
