# src/drugref/ingest/spl_advice_scope.py
"""Find the SCOPE the advice reader reads around a mention: its sentence, the
heading block it stands in, or the PLR table row (`spl_advice.read_advice`).

PURE: no database, no I/O, standard library `re` only. Split out of `spl_advice`
(#216, rule 4); the phrase rules that read a scope stay there.

`scope_index` computes a wording's heading markers and sentence starts ONCE; the
reader takes them per mention, so a wording with many class mentions is scanned
once rather than once per mention (#216: the class pass cost ~245 s -> ~304 s).
"""

import re
from bisect import bisect_right

# "7.2 Heading": numbers with a dot, then an upper-case word, at a word start.
_MARKER = re.compile(r"(?<!\S)\d+(?:\.\d+)+\s+(?=[A-Z])")
# A sentence ends at "." or ";" followed by space and an upper-case letter/digit.
# The match consumes the whitespace, so m.end() is the next sentence's start.
_BOUNDARY = re.compile(r"[.;]\s+(?=[A-Z0-9])")
_ABBREV = re.compile(r"(?:\be\.g|\bi\.e|\bapprox|\bvs)\.$", re.IGNORECASE)
# Task 9B: highlights end an item with a cross-reference "(7.1)", "( 5.6 , 7.2 )"
# and no full stop before the next item; a bullet "•" starts one. Both are sentence
# starts, or the next item inherits the previous item's advice.
_XREF_END = re.compile(
    r"\(\s*\d+(?:\.\d+)*(?:\s*,\s*\d+(?:\.\d+)*)*\s*\)\s+(?=[A-Z•·])")
_BULLET = re.compile(r"•\s*|(?<=\s)·\s*|^·\s*")
# PLR table cell labels start a sentence: without a full stop between cells, the
# next row's heading and the previous row's Intervention would read as one.
_CELL_LABEL = re.compile(
    r"(?<=\s)(?:Clinical\s*Impact|linical\s+Impact|Intervention|Examples?)(?=\s*:|\s)")


def _markers(text: str) -> list[int]:
    return [m.start() for m in _MARKER.finditer(text)]


def _sentence_starts(text: str, markers: list[int]) -> list[int]:
    """Sorted offsets where a sentence begins (always includes 0)."""
    starts = {0, *markers}
    starts.update(m.end() for m in _XREF_END.finditer(text))
    starts.update(m.start() for m in _BULLET.finditer(text))
    starts.update(m.start() for m in _CELL_LABEL.finditer(text))
    for m in _BOUNDARY.finditer(text):
        # short window only: "e.g." is at most 6 characters before the dot
        if _ABBREV.search(text, max(0, m.start() - 6), m.start() + 1):
            continue
        starts.add(m.end())
    return sorted(starts)


def span_from(starts: list[int], n: int, char_start: int) -> tuple[int, int]:
    i = bisect_right(starts, char_start)
    return starts[i - 1], (starts[i] if i < len(starts) else n)


def span_crosses_sentence_boundary(text: str, char_start: int, char_end: int) -> bool:
    """True when a sentence boundary falls strictly inside the span.

    The matcher tokenises on letters/digits and ignores punctuation, so a class name
    can match ACROSS ". " ("Avoid Potassium. Salts ..." -> "Potassium Salts"): a
    false mention. Same rule as the splitter (`_BOUNDARY`, abbreviations excluded);
    a boundary that merely ends the span is fine.
    """
    for m in _BOUNDARY.finditer(text, char_start, char_end):
        if m.end() < char_end and not _ABBREV.search(
                text, max(0, m.start() - 6), m.start() + 1):
            return True
    return False


def sentence_span(text: str, char_start: int, char_end: int) -> tuple[int, int]:
    """The sentence holding the span (`char_end` is unused; the start decides)."""
    return span_from(_sentence_starts(text, _markers(text)), len(text), char_start)


def block_from(text: str, markers: list[int], starts: list[int],
                char_start: int) -> tuple[int, int] | None:
    i = bisect_right(markers, char_start)
    if i == 0:
        return None
    m = markers[i - 1]
    after_marker = _MARKER.match(text, m).end()
    if len(text[after_marker:char_start].split()) > 8:
        return None
    if span_from(starts, len(text), char_start)[0] > m:
        return None  # a sentence boundary lies between marker and mention
    return m, (markers[i] if i < len(markers) else len(text))


# --- PLR table rows (Task 9B) --------------------------------------------
# A PLR interaction table writes each row as "<row heading> Clinical Impact: ... .
# Intervention: ... . [Examples: ...]". The class is named in the heading (or the
# Impact or Examples cell) and the advice sits in the Intervention cell, a sentence
# or more away -- 341 run-12 mentions read not_read whose own row says
# "contraindicated". The row is read as a heading block (it IS the block under a
# heading, design 5.2), and only when the row structure is unambiguous: an Impact
# cell at or just before the mention, no other row in between, an Intervention cell
# within a few sentences. Anything else stays not_read.
_ROW_IMPACT = re.compile(r"\s*(?:Clinical\s*|linical\s+)Impact\b", re.IGNORECASE)
_ROW_INTERVENTION = re.compile(r"\s*Intervention\b", re.IGNORECASE)
_ROW_EXAMPLES = re.compile(r"\s*Examples?\b", re.IGNORECASE)
_ROW_LOOK = 6  # sentences searched either way; a table cell is rarely longer
_ROW_CELL = 2  # sentences an Intervention cell may run on after its first
_ROW_HEADING_TAIL = 8  # tokens a row heading may run on after the mention


def _intervention_end(sent, n: int, k0: int) -> int | None:
    """Index of the last sentence of the first Intervention cell from `k0`, or None
    when another row's Impact cell comes first. The cell runs on for up to
    `_ROW_CELL` sentences, stopping at an Examples cell, a new row or a bullet."""
    for k in range(k0, min(n, k0 + _ROW_LOOK)):
        if _ROW_INTERVENTION.match(sent(k)):
            end = k
            for m in range(k + 1, min(n, k + 1 + _ROW_CELL)):
                nxt = sent(m)
                if (_ROW_EXAMPLES.match(nxt) or _ROW_IMPACT.match(nxt)
                        or nxt.lstrip()[:1] in ("•", "·")
                        or (m + 1 < n and _ROW_IMPACT.match(sent(m + 1)))):
                    break  # ... or `nxt` is the next row's heading
                end = m
            return end
        if _ROW_IMPACT.match(sent(k)):
            return None  # the next row began first: this row has no Intervention
    return None


def row_block(text: str, starts: list[int], char_start: int,
               char_end: int) -> tuple[int, int, bool] | None:
    """`(start, end, is_heading)` of the PLR table row holding the mention, or None.

    Cell labels are sentence starts (`_CELL_LABEL`), so a row heading sits at the
    END of the sentence before its Impact cell -- glued to the previous row's cell
    when no full stop separates them. `is_heading` says the mention is that heading:
    nothing but a few words, no comma, between it and the Impact cell. The caller
    reads a heading's row BEFORE its sentence, which holds the previous row's text.
    """
    bounds = list(zip(starts, starts[1:] + [len(text)]))
    n = len(bounds)
    i = bisect_right(starts, char_start) - 1

    def sent(j: int) -> str:
        return text[bounds[j][0]:bounds[j][1]]

    tail = text[char_end:bounds[i][1]]
    if (i + 1 < n and _ROW_IMPACT.match(sent(i + 1)) and not re.search(r"[,;]", tail)
            and len(tail.split()) <= _ROW_HEADING_TAIL):
        end = _intervention_end(sent, n, i + 2)
        return None if end is None else (char_start, bounds[end][1], True)
    if _ROW_EXAMPLES.match(sent(i)):
        # An Examples cell belongs to the row whose Intervention precedes it.
        for j in range(i - 1, max(-1, i - _ROW_LOOK), -1):
            if _ROW_INTERVENTION.match(sent(j)):
                return bounds[j][0], max(bounds[i][1], char_end), False
            if _ROW_IMPACT.match(sent(j)):
                return None  # reached the Impact cell without an Intervention
        return None
    for j in range(i, max(-1, i - _ROW_LOOK), -1):  # the row's Impact cell
        if j < i and (_ROW_INTERVENTION.match(sent(j)) or _ROW_EXAMPLES.match(sent(j))):
            return None  # a previous row's cell lies between: not inside a row
        if _ROW_IMPACT.match(sent(j)):
            break
    else:
        return None
    end = _intervention_end(sent, n, i + 1)
    return None if end is None else (bounds[j][0], max(bounds[end][1], char_end), False)


def scope_index(text: str) -> tuple[list[int], list[int]]:
    """`(markers, sentence_starts)` for one wording: compute once, pass to every
    `read_advice` call on that wording."""
    markers = _markers(text)
    return markers, _sentence_starts(text, markers)
