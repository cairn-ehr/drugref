# src/drugref/ingest/spl_advice.py
"""Read a drug label's OWN advice level for the sentence around a mention.

PURE: no database, no I/O, standard library `re` only.

**WHY THIS MODULE EXISTS, AND WHY IT BREAKS A RULE.** Slice 5c.3 decided "no
relation extraction": `spl_match` recognises entities and asserts no relation.
On 2026-11-08 the owner REVERSED that rule for this one grain. Lightening
clinicians' memory needs the label's advice ("contraindicated", "avoid", "adjust
the dose", "monitor"), not merely the fact that a class was mentioned. So this
module reads the advice level out of the sentence around a mention.

**A READING IS MACHINE-READ EVIDENCE -- NEVER AN ASSERTION OR A RULING.** It says
what the label's wording appears to say. It never feeds a signature, a candidate
pair or a curated pair; only a curator's ruling promotes anything.

**THE HARM DIRECTION IS UNDER-WARNING.** A wrong "no_meaningful_interaction" is
the worst error this module can make, because it tells a clinician a hazard is
absent. Hence: (1) the reader prefers `not_read` to a guess; (2) a strength level
(contraindicated > avoid > adjust_dose > monitor) always beats
"no meaningful interaction"; (3) "not contraindicated" is never read as
"contraindicated" (that would over-warn the other way, and is plainly wrong), but
is left `not_read` rather than being promoted to an all-clear.

**NEGATION.** Only two negations are understood. "not/no ... contraindicated"
(up to one word between, any whitespace) is masked before the strength rules, so it
reads `not_read`, never `contraindicated`. "no dose adjustment/reduction", "no
clinically significant interaction", "dose adjustment is not required" are the NMI
phrases in `ADVICE_RULES`; they are MASKS ONLY, so "dose adjustment" inside them
cannot fire `adjust_dose`. A mask hit alone never yields NMI (see R10). Any other
negation is not modelled; the vocabulary avoids phrases that negation would flip.

**CONDITIONALS.** "If coadministration is necessary, reduce the dose" is read as
`adjust_dose`: the label does give dose advice, and a conditional still tells the
reader something is to be done. That is the safe direction (it can over-warn
slightly, never produces an all-clear).

**RULING R10: NMI ONLY VIA A CLOSED FULL-MATCH TEMPLATE.** `no_meaningful_interaction`
is the worst error this module can make, and the reading is meant to be RARE. Rulings
R6-R9 tried to refuse NMI with blocklists (contrast words, separators, conditionals,
"no ... data"); four review rounds each found a new sentence that slipped through
("No dose adjustment is POSSIBLE with X" was the last), because a blocklist leaks by
construction: it can never list every way a sentence is not a plain all-clear. So the
rule is turned around -- list what IS an all-clear, and nothing else. In the sentence
scope, the mention is replaced by a placeholder M, the scope is trimmed and one
trailing "." dropped, and the WHOLE remainder must fully match (case-insensitive,
single spaces) one of `NMI_TEMPLATES`:

  T1 no [clinically significant|relevant|meaningful] [pharmacokinetic|drug]
     interaction(s) was|were|has been|have been observed with M
  T2 no [...same...] interaction(s) is|are expected with M
  T3 no dose|dosage adjustment(s)|reduction(s) is|are needed|required|necessary|
     recommended with|for patients taking|receiving M
  T4 dose|dosage adjustment(s)|reduction(s) is|are not needed|required|necessary
     with|for patients taking|receiving M      (NO "recommended": see R11)

RULING R11: T4 omits "recommended", and the `no_dose_adjustment` mask does too, so
"not recommended" always reaches `avoid.not_recommended`. "Dose reduction is not
recommended with X" can mean "dose changes will not fix this -- avoid X", so reading it
as an all-clear risks the worst error; reading it `avoid` over-warns (safe direction).

The mention must END the sentence and is the only open slot, so any extra word, comma,
clause, condition or odd whitespace means no match and `not_read`. The R8/R9 blocker
was dropped: every word a template admits is fixed, none is a blocker word or
separator (T4's own "not" was already exempt), so the blocker could never fire on a
template match. A strength level that fired still wins. Cost: low NMI recall (safe
direction; Task 9 measures it).

Levels are fixed (`LEVELS`). The phrase vocabulary (`ADVICE_RULES`) is a seed to
be tuned against the corpus.
"""

import re
from bisect import bisect_right
from dataclasses import dataclass

LEVELS = ("contraindicated", "avoid", "adjust_dose", "monitor",
          "no_meaningful_interaction", "not_read")
STRENGTH = ("contraindicated", "avoid", "adjust_dose", "monitor")  # strongest first
NMI = "no_meaningful_interaction"

SCOPE_SENTENCE = "sentence"
SCOPE_HEADING_BLOCK = "heading_block"
SCOPES = (SCOPE_SENTENCE, SCOPE_HEADING_BLOCK)


@dataclass(frozen=True)
class AdviceRule:
    rule_id: str  # "<level>.<name>"
    level: str
    pattern: re.Pattern[str]


def _rule(level: str, name: str, regex: str) -> AdviceRule:
    """Build a rule; patterns are case-insensitive and whole-word (`\\b`) by hand."""
    return AdviceRule(f"{level}.{name}", level, re.compile(regex, re.IGNORECASE))


ADVICE_RULES: tuple[AdviceRule, ...] = (
    # Negation ("not contraindicated") is masked in _read_scope, not here.
    _rule("contraindicated", "contraindicated", r"\bcontraindicated\b"),
    _rule("avoid", "avoid", r"\bavoid(?:ed|ing)?\b"),
    _rule("avoid", "not_recommended", r"\bnot recommended\b"),
    _rule("avoid", "do_not_use", r"\b(?:do|should) not (?:be )?use[d]?\b"),
    _rule("avoid", "do_not_coadminister",
          r"\bdo not co-?administer\b|\bshould not be co-?administered\b"),
    _rule("adjust_dose", "reduce_dose",
          r"\b(?:reduce|decrease|lower)(?:d|ing)? (?:the )?(?:dose|dosage)\b"
          r"|\bdose reduction\b"),
    _rule("adjust_dose", "dose_adjustment",
          r"\bdos(?:e|age)[- ]adjust(?:ment|ed)\b"
          r"|\badjust(?:ing|ment of)? (?:the )?dos(?:e|age)\b"),
    _rule("monitor", "monitor", r"\bmonitor(?:ed|ing)?\b"),
    _rule("monitor", "observe_closely",
          r"\bobserve[sd]? closely\b|\bclosely observe[sd]?\b"),
    # NMI needs an explicit "interaction/effect" noun: "no significant
    # difference" is not an all-clear.
    _rule(NMI, "no_clinically_significant",
          r"\bno (?:clinically )?(?:significant|meaningful|relevant) "
          r"(?:\w+ )?(?:interactions?|effects?)\b"),
    _rule(NMI, "no_dose_adjustment",
          r"\bno (?:(?:initial|starting|specific) )?dos(?:e|age) "
          r"(?:adjustments?|reductions?)\b"
          r"|\bdos(?:e|age) (?:adjustments?|reductions?) (?:is|are) not "
          r"(?:needed|required|necessary)\b"),
)

# R10: the ONLY way to read NMI. Each fully matches the trimmed sentence scope (one
# trailing "." dropped) with the mention replaced by _PH; see the module docstring.
_PH = "\ue000"  # private-use code point: never occurs in label text
_INTERACTION = (r"no (?:clinically (?:significant|relevant|meaningful) )?"
                r"(?:(?:pharmacokinetic|drug) )?interactions? ")
_DOSE = r"dos(?:e|age) (?:adjustments?|reductions?) (?:is|are) "
_DOSE_TAIL = r"(?:with|for patients (?:taking|receiving)) " + _PH
_DOSE_END = r"(?:needed|required|necessary|recommended) " + _DOSE_TAIL
_DOSE_NOT_END = r"(?:needed|required|necessary) " + _DOSE_TAIL  # R11: no "recommended"
NMI_TEMPLATES: tuple[AdviceRule, ...] = (
    _rule(NMI, "interaction_observed",
          _INTERACTION + r"(?:was|were|has been|have been) observed with " + _PH),
    _rule(NMI, "interaction_expected",
          _INTERACTION + r"(?:is|are) expected with " + _PH),
    _rule(NMI, "no_dose_adjustment_needed", r"no " + _DOSE + _DOSE_END),
    _rule(NMI, "dose_adjustment_not_needed", _DOSE + r"not " + _DOSE_NOT_END),
)


@dataclass(frozen=True)
class AdviceReading:
    level: str
    mixed: bool  # more than one strength level in the scope
    rule_id: str | None
    scope: str
    scope_start: int
    scope_end: int


# --- scope finding ------------------------------------------------------
# Starts and markers are computed ONCE per read_advice call and passed down;
# every helper below works from offsets, never re-slicing the text (a section
# can be 50k+ characters, and per-boundary slicing was quadratic).

# "7.2 Heading": numbers with a dot, then an upper-case word, at a word start.
_MARKER = re.compile(r"(?<!\S)\d+(?:\.\d+)+\s+(?=[A-Z])")
# A sentence ends at "." or ";" followed by space and an upper-case letter/digit.
# The match consumes the whitespace, so m.end() is the next sentence's start.
_BOUNDARY = re.compile(r"[.;]\s+(?=[A-Z0-9])")
_ABBREV = re.compile(r"(?:\be\.g|\bi\.e|\bapprox|\bvs)\.$", re.IGNORECASE)
# Negated contraindication, masked before the strength rules.
# "not only/merely/just contraindicated" is NOT a negation: it is stronger advice.
_NEG_CONTRA = re.compile(
    r"\b(?:not|no)\s+(?:(?!only\b|merely\b|just\b)\w+\s+)?contraindicated\b",
    re.IGNORECASE)


def _markers(text: str) -> list[int]:
    return [m.start() for m in _MARKER.finditer(text)]


def _sentence_starts(text: str, markers: list[int]) -> list[int]:
    """Sorted offsets where a sentence begins (always includes 0)."""
    starts = {0, *markers}
    for m in _BOUNDARY.finditer(text):
        # short window only: "e.g." is at most 6 characters before the dot
        if _ABBREV.search(text, max(0, m.start() - 6), m.start() + 1):
            continue
        starts.add(m.end())
    return sorted(starts)


def _span_from(starts: list[int], n: int, char_start: int) -> tuple[int, int]:
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
    return _span_from(_sentence_starts(text, _markers(text)), len(text), char_start)


def _block_from(text: str, markers: list[int], starts: list[int],
                char_start: int) -> tuple[int, int] | None:
    i = bisect_right(markers, char_start)
    if i == 0:
        return None
    m = markers[i - 1]
    after_marker = _MARKER.match(text, m).end()
    if len(text[after_marker:char_start].split()) > 8:
        return None
    if _span_from(starts, len(text), char_start)[0] > m:
        return None  # a sentence boundary lies between marker and mention
    return m, (markers[i] if i < len(markers) else len(text))


def heading_block_span(text: str, char_start: int) -> tuple[int, int] | None:
    """Marker-to-next-marker span when a marker sits <= 8 tokens before the mention."""
    mk = _markers(text)
    return _block_from(text, mk, _sentence_starts(text, mk), char_start)


# --- reading ------------------------------------------------------------

def _read_scope(chunk: str, allow_nmi: bool, rel_start: int = 0,
                rel_end: int = 0) -> tuple[str, bool, str | None] | None:
    """(level, mixed, rule_id) for one chunk of text, or None when nothing is read.

    [rel_start, rel_end) is the mention, relative to `chunk`; only NMI uses it.
    """
    masked = chunk
    spans = [m.span() for r in ADVICE_RULES if r.level == NMI
             for m in r.pattern.finditer(chunk)]
    spans += [m.span() for m in _NEG_CONTRA.finditer(chunk)]
    for a, b in spans:  # "no dose adjustment" must not fire adjust_dose
        masked = masked[:a] + " " * (b - a) + masked[b:]
    first: dict[str, tuple[int, str]] = {}
    for r in ADVICE_RULES:
        if r.level == NMI:
            continue
        m = r.pattern.search(masked)
        if m and (r.level not in first or m.start() < first[r.level][0]):
            first[r.level] = (m.start(), r.rule_id)
    for level in STRENGTH:
        if level in first:
            return level, len(first) > 1, first[level][1]
    if not allow_nmi or rel_end > len(chunk):
        return None  # a mention running past the sentence end is refused
    scope = (chunk[:rel_start] + _PH + chunk[rel_end:]).strip()
    scope = scope[:-1] if scope.endswith(".") else scope
    hit = next((t for t in NMI_TEMPLATES if t.pattern.fullmatch(scope)), None)
    return (NMI, False, hit.rule_id) if hit else None


def read_advice(text: str, char_start: int, char_end: int) -> AdviceReading:
    """Read the label's advice for the mention at [char_start, char_end)."""
    mk = _markers(text)
    starts = _sentence_starts(text, mk)
    s, e0 = _span_from(starts, len(text), char_start)
    # READ the original sentence (so a mention running past its end still refuses
    # NMI inside _read_scope) but WIDEN the returned scope on EVERY path: db/066's
    # CHECK needs scope to contain the span.
    e = max(e0, char_end)
    got = _read_scope(text[s:e0], allow_nmi=True, rel_start=char_start - s,
                      rel_end=char_end - s)
    if got:
        return AdviceReading(got[0], got[1], got[2], SCOPE_SENTENCE, s, e)
    block = _block_from(text, mk, starts, char_start)
    if block and block[1] >= char_end:
        # NMI is not read here: a block can mix statements about different things.
        got = _read_scope(text[block[0]:block[1]], allow_nmi=False)
        if got:
            return AdviceReading(got[0], got[1], got[2], SCOPE_HEADING_BLOCK, *block)
    return AdviceReading("not_read", False, None, SCOPE_SENTENCE, s, e)
