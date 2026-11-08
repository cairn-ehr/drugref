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

Levels are fixed (`LEVELS`). The phrase vocabulary (`ADVICE_RULES`) is a seed to
be tuned against the corpus.
"""

import re
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
    # The two lookbehinds are the negation guard:
    # "not/no contraindicated" must not fire.
    _rule("contraindicated", "contraindicated",
          r"(?<!not )(?<!no )\bcontraindicated\b"),
    _rule("avoid", "avoid", r"\bavoid(?:ed|ing)?\b"),
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
          r"\bno (?:(?:initial|starting|specific) )?dos(?:e|age) adjustments?\b"
          r"|\bdos(?:e|age) adjustments? (?:is|are) not "
          r"(?:needed|required|necessary)\b"),
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

# "7.2 Heading": numbers with a dot, then an upper-case word, at a word start.
_MARKER = re.compile(r"(?<!\S)\d+(?:\.\d+)+\s+(?=[A-Z])")
# A sentence ends at "." or ";" followed by space and an upper-case letter/digit.
_BOUNDARY = re.compile(r"[.;](?=\s+[A-Z0-9])")
_ABBREV = re.compile(r"(?:\be\.g|\bi\.e|\bapprox|\bvs)\.$", re.IGNORECASE)


def _sentence_starts(text: str) -> list[int]:
    """Sorted offsets where a sentence begins (always includes 0)."""
    starts = {0}
    for m in _BOUNDARY.finditer(text):
        if _ABBREV.search(text[: m.end()]):
            continue  # "e.g. Ketoconazole" is not a sentence end
        starts.add(len(text) - len(text[m.end():].lstrip()))
    starts.update(m.start() for m in _MARKER.finditer(text))
    return sorted(starts)


def sentence_span(text: str, char_start: int, char_end: int) -> tuple[int, int]:
    """The sentence holding the span (`char_end` is unused; the start decides)."""
    starts = _sentence_starts(text)
    s = max(p for p in starts if p <= char_start)
    after = [p for p in starts if p > char_start]
    return s, (after[0] if after else len(text))


def heading_block_span(text: str, char_start: int) -> tuple[int, int] | None:
    """Marker-to-next-marker span when a marker sits <= 8 tokens before the mention."""
    before = [m for m in _MARKER.finditer(text) if m.start() <= char_start]
    if not before:
        return None
    m = before[-1]
    if len(text[m.end():char_start].split()) > 8:
        return None
    if sentence_span(text, char_start, char_start)[0] > m.start():
        return None  # a sentence boundary lies between marker and mention
    nxt = [n.start() for n in _MARKER.finditer(text) if n.start() > char_start]
    return m.start(), (nxt[0] if nxt else len(text))


# --- reading ------------------------------------------------------------

def _read_scope(chunk: str, allow_nmi: bool) -> tuple[str, bool, str | None] | None:
    """(level, mixed, rule_id) for one chunk of text, or None when nothing is read."""
    nmi_hits = [(m.start(), m.end()) for r in ADVICE_RULES if r.level == NMI
                for m in r.pattern.finditer(chunk)]
    masked = chunk
    for a, b in nmi_hits:  # "no dose adjustment" must not fire adjust_dose
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
    if allow_nmi and nmi_hits:
        first_rule = min((m.start(), r.rule_id) for r in ADVICE_RULES if r.level == NMI
                         for m in r.pattern.finditer(chunk))
        return NMI, False, first_rule[1]
    return None


def read_advice(text: str, char_start: int, char_end: int) -> AdviceReading:
    """Read the label's advice for the mention at [char_start, char_end)."""
    s, e = sentence_span(text, char_start, char_end)
    got = _read_scope(text[s:e], allow_nmi=True)
    if got:
        return AdviceReading(got[0], got[1], got[2], SCOPE_SENTENCE, s, e)
    block = heading_block_span(text, char_start)
    if block:
        # NMI is not read here: a block can mix statements about different things.
        got = _read_scope(text[block[0]:block[1]], allow_nmi=False)
        if got:
            return AdviceReading(got[0], got[1], got[2], SCOPE_HEADING_BLOCK, *block)
    return AdviceReading("not_read", False, None, SCOPE_SENTENCE, s, e)
