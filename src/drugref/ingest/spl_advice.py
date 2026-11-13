# src/drugref/ingest/spl_advice.py
"""Read a drug label's OWN advice level for the scope around a mention (its
sentence, or the heading block / PLR table row it stands in; see `read_advice`).

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

**NEGATION.** Only three negations are understood. "not/no ... contraindicated"
(up to one word between, any whitespace) is masked before the strength rules, so it
reads `not_read`, never `contraindicated`. "no dose adjustment/reduction", "no
clinically significant interaction", "dose adjustment is not required" are the NMI
phrases in `ADVICE_RULES`; they are MASKS ONLY, so "dose adjustment" inside them
cannot fire `adjust_dose`. A mask hit alone never yields NMI (see R10). "separate
administration ... is not necessary/needed/required" is masked by `_NOT_ADVICE`
(R24), so it cannot read `adjust_dose`. Any other negation is not modelled; the
vocabulary avoids phrases that negation would flip.

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

**TASK 9B TUNING (measured on SPL run 12, recorded in the results record).** The
hand sample found the reader too weak on dose phrasing (dose_change, dose_cap,
titrate), on "caution", on "not generally recommended", blind to a glued table
footnote ("Contraindicateda") and to a cross-reference into the Contraindications
section, merging highlight items that end in "(7.1)" or start with "•", and blind
to the Intervention cell of a PLR table row (`row_block`). Each fix has a test.
"""

import re
from dataclasses import dataclass

from drugref.ingest.spl_advice_scope import (
    block_from, row_block, scope_index, span_from)

LEVELS = ("contraindicated", "avoid", "adjust_dose", "monitor",
          "no_meaningful_interaction", "not_read")
STRENGTH = ("contraindicated", "avoid", "adjust_dose", "monitor")  # strongest first
NMI = "no_meaningful_interaction"

SCOPE_SENTENCE = "sentence"
SCOPE_HEADING_BLOCK = "heading_block"
SCOPES = (SCOPE_SENTENCE, SCOPE_HEADING_BLOCK)

#: (level, scope) readings WITHHELD from what ships: skipped as if empty, so the next
#: scope is tried (R21), and not_read if none reads (design 7: a level that cannot
#: reach its gate after tuning is withheld). Task 9B,
#: after the two permitted tuning iterations, final hand sample on SPL run 14:
#: sentence-scope avoid 39/50 (78%) and monitor 44/50 (88%) exact agreement against
#: the owner's 90% gate. The misses are mostly advice belonging to ANOTHER drug in a
#: merged table row or bullet run, which a phrase reader cannot attribute. Every
#: other (level, scope) cleared its gate. Recorded in the results record, "Gates".
WITHHELD: frozenset[tuple[str, str]] = frozenset({
    ("avoid", SCOPE_SENTENCE),
    ("monitor", SCOPE_SENTENCE),
})


@dataclass(frozen=True)
class AdviceRule:
    rule_id: str  # "<level>.<name>"
    level: str
    pattern: re.Pattern[str]


def _rule(level: str, name: str, regex: str) -> AdviceRule:
    """Build a rule; patterns are case-insensitive (except inline `(?-i:...)`
    groups, used where only a capitalised heading counts) and whole-word (`\\b`)
    by hand."""
    return AdviceRule(f"{level}.{name}", level, re.compile(regex, re.IGNORECASE))


# A dose word, singular or plural ("dose", "dosages", "dosing").
_DOSE_WORD = r"(?:dose|doses|dosage|dosages|dosing)"

ADVICE_RULES: tuple[AdviceRule, ...] = (
    # Negation ("not contraindicated") is masked in _read_scope, not here.
    # `[a-z]?`: a table cell can carry a glued footnote letter ("Contraindicateda").
    # R20: also the truncated table cell "Contraindicate".
    _rule("contraindicated", "contraindicated", r"\bcontraindicate(?:d[a-z]?)?\b"),
    # The label filing the interaction under its Contraindications section ("[See
    # Dosage and Administration (2.5), Contraindications (4.1)]"). Read lower, that is
    # an under-warning; when the reference belongs to another clause it over-warns,
    # which is the safe direction (Task 9B hand sample).
    # Iteration 2: brackets may sit inside the reference ("See D&A [2.5], CONTRA-
    # INDICATIONS [4.2]") and "See" may be glued to the section name.
    _rule("contraindicated", "see_contraindications",
          r"\bsee(?-i:CONTRAINDICATIONS)\b"
          # R20: extraction can split the word ("s ee", "se e").
          r"|\b(?:see|s\s+ee|se\s+e)\b[^\n]{0,120}?\bcontraindications?\b"),
    # A table cell carrying the bare upper-case section name files the row under it.
    # Where a garbled extraction leaves the word in another row it over-warns (the
    # safe direction); measured in the results record.
    _rule("contraindicated", "contraindications_section",
          # R20: or glued to the next word ("CONTRAINDICATIONSand").
          r"(?-i:\bCONTRAINDICATIONS(?:\b|(?=[a-z])))"),
    _rule("avoid", "avoid", r"\bavoid(?:ed|ing)?\b"),
    _rule("avoid", "not_recommended",
          r"\bnot (?:generally |ordinarily |usually |routinely )?recommended\b"),
    _rule("avoid", "do_not_use", r"\b(?:do|should) not (?:be )?use[d]?\b"),
    _rule("avoid", "do_not_coadminister",
          r"\bdo not co-?administer\b|\bshould not be co-?administered\b"),
    # "Reserve concomitant prescribing ..." (only where nothing else will do) and
    # "cessation ... is preferred": avoid unless necessary.
    _rule("avoid", "reserve",
          r"\breserve\s+concomitant\b|\bcessation\s+of\b[^.;]{0,80}?\bis\s+preferred\b"),
    _rule("adjust_dose", "reduce_dose",
          r"\b(?:reduce|decrease|lower)(?:d|ing)? (?:the )?(?:dose|dosage)\b"
          r"|\bdose reduction\b"),
    _rule("adjust_dose", "dose_adjustment",
          r"\bdos(?:e|age)[- ]adjust(?:ments?|ed)\b"
          r"|\badjust(?:ing|ment of)? (?:the )?dos(?:e|age)\b"),
    # Task 9B: the dose-change phrasings the seed missed. Up to two words may sit
    # between verb and dose word ("reduce the starting dose", "adjust insulin
    # dosage", "downward adjustment of their anticoagulant dosage"); "low-dose" (a
    # product strength) is not matched because the low/lower forms need a space.
    _rule("adjust_dose", "dose_change",
          r"\b(?:reduc|decreas|increas|lower)(?:e|es|ed|ing)?\s+(?:the\s+|their\s+|a\s+)?"
          r"(?:\w+\s+){0,2}?" + _DOSE_WORD + r"\b"
          r"|\b(?:downward\s+)?adjust(?:s|ed|ing|ment|ments)?\s+(?:(?:in|of|to)\s+)?"
          r"(?:the\s+|their\s+)?(?:\w+\s+){0,2}?" + _DOSE_WORD + r"\b"
          r"|\b" + _DOSE_WORD
          + r"\s+(?:of\s+(?:the\s+)?(?:[\w/-]+\s+){1,3})?(?:\w+\s+){0,2}?"
          r"(?:be|is|are)\s+(?:adjusted|reduced|decreased|lowered|increased|titrated)\b"
          r"|\bdos(?:e|age)\s+(?:decrease|reduction|increase)s?\b"
          r"|\b(?:need|needs|require|requires)\s+adjustment\b"
          r"|\blowest\s+(?:\w+\s+){0,2}?" + _DOSE_WORD + r"\b"
          r"|\b(?:at|with)\s+(?:a\s+)?low(?:er)?\s+(?:\w+\s+){0,3}?"
          + _DOSE_WORD + r"\b"
          r"|\blimit(?:ing)?\s+(?:the\s+)?" + _DOSE_WORD + r"\b"
          r"|\blow(?:er)?\s+(?:initial|starting)\s+" + _DOSE_WORD + r"\b"),
    # Separating the doses in time is a regimen adjustment, read in the adjust_dose
    # band (a Task 9B reading convention, recorded for the owner's review).
    _rule("adjust_dose", "separate_administration",
          r"\bseparate(?:ly)?\s+(?:administration|dosing|administered|doses)\b"
          r"|\badminist\w*\s+separately\b"
          r"|\bat\s+least\s+\d+(?:\.\d+)?\s*(?:hours?|h|hrs?)\s+(?:before|after|prior|apart)\b"),
    _rule("adjust_dose", "dose_cap", r"\b(?:should not|do not|not to) exceed\b"),
    _rule("adjust_dose", "titrate", r"\btitrat(?:e|ed|es|ing|ion)\b"),
    _rule("monitor", "monitor", r"\bmonitor(?:ed|ing)?\b"),
    _rule("monitor", "observe_closely",
          r"\bobserve[sd]? closely\b|\bclosely observe[sd]?\b"),
    # "Use with caution": a warning to watch, the monitor band. `\b` keeps the
    # section name PRECAUTIONS out.
    _rule("monitor", "caution", r"\bcaution(?:ed)?\b|\bcautious(?:ly)?\b"),
    _rule("monitor", "observe",
          r"\bobserve[sd]?\s+carefully\b|\bcarefully\s+observe[sd]?\b"
          r"|\bevaluate\s+(?:the\s+)?patients?\b|\bfollow\s+(?:the\s+)?patients?\b"),
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


# --- masks (the scope finding lives in spl_advice_scope) -----------------
# Negated contraindication, masked before the strength rules. "not only/merely/just/
# recommended/advised contraindicated" is not a negation (R25: stronger advice).
_NEG_CONTRA = re.compile(
    r"\b(?:not|no)\s+(?:(?!(?:only|merely|just|recommended|advised)\b)\w+\s+)?"
    r"contraindicate(?:d[a-z]?)?\b",
    re.IGNORECASE)
# A time reference, not advice ("during treatment initiation and dose adjustment",
# "after initiation, dose titration, or discontinuation"), and a negated separation
# ("separate administration ... is not necessary"): masked like the NMI phrases.
_NOT_ADVICE = re.compile(
    r"\bduring\s+(?:\w+\s+){0,3}?(?:and\s+)?dose\s+(?:titration|adjustment)s?\b"
    r"|\binitiation,?\s+(?:and\s+)?dose\s+(?:titration|adjustment)s?\b"
    r"|\bseparate(?:ly)?\s+(?:administration|dosing|doses)\b(?P<gap>[^.;,]{0,80}?)"
    r"\b(?:is|are)\s+not\s+(?:necessary|needed|required)\b", re.IGNORECASE)


# --- reading ------------------------------------------------------------

def _read_scope(chunk: str, allow_nmi: bool, rel_start: int = 0,
                rel_end: int = 0) -> tuple[str, bool, str | None] | None:
    """(level, mixed, rule_id) for one chunk of text, or None when nothing is read.

    [rel_start, rel_end) is the mention, relative to `chunk`; only NMI uses it.
    """
    masked = chunk
    spans = [m.span() for r in ADVICE_RULES if r.level == NMI
             for m in r.pattern.finditer(chunk)]
    # #218 (R24's rule): a negation whose span overlaps another strength phrase
    # ("Do not use Contraindicated", glued cells) is not masked: the "not" belongs
    # to that phrase, and masking it would hide both readings.
    other = [m.span() for r in ADVICE_RULES if r.level not in (NMI, "contraindicated")
             for m in r.pattern.finditer(chunk)]
    spans += [m.span() for m in _NEG_CONTRA.finditer(chunk)
              if not any(a < m.end() and m.start() < b for a, b in other)]
    # R24: a strength word inside the "separate ... not needed" gap must stay visible.
    spans += [m.span() for m in _NOT_ADVICE.finditer(chunk)
              if not any(r.pattern.search(m.group("gap") or "")
                         for r in ADVICE_RULES if r.level != NMI)]
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


def _reading(got: tuple[str, bool, str | None] | None, scope: str, start: int, end: int,
             withheld: frozenset[tuple[str, str]]) -> AdviceReading | None:
    """`got` as a reading over [start, end), or None when nothing was read OR the
    (level, scope) is withheld -- R21: the caller then tries the next scope exactly
    as it would after not_read (a withheld sentence "avoid" must not hide the
    heading block's "contraindicated")."""
    if got is None or (got[0], scope) in withheld:
        return None
    return AdviceReading(got[0], got[1], got[2], scope, start, end)


def read_advice(text: str, char_start: int, char_end: int, *,
                withheld: frozenset[tuple[str, str]] = WITHHELD,
                index: tuple[list[int], list[int]] | None = None) -> AdviceReading:
    """Read the label's advice for the mention at [char_start, char_end).

    Scopes are tried in order (row heading, sentence, table row, heading block); a
    reading whose (level, scope) is in `withheld` is skipped like an empty one (R21),
    so a withheld sentence never ships its weaker co-reading either, and when no
    scope reads, the span ships not_read over its sentence. Tests of the phrase
    rules pass `withheld=frozenset()`.

    `index` is `spl_advice_scope.scope_index(text)`: a caller reading several
    mentions of one wording computes it once and passes it in (#216); None computes it.
    """
    mk, starts = index if index is not None else scope_index(text)
    s, e0 = span_from(starts, len(text), char_start)
    # READ the original sentence (so a mention running past its end still refuses
    # NMI inside _read_scope) but WIDEN the returned scope on EVERY path: db/066's
    # CHECK needs scope to contain the span.
    e = max(e0, char_end)
    row = row_block(text, starts, char_start, char_end)
    if row and row[2]:  # a row heading: its sentence is the previous row's text
        got = _reading(_read_scope(text[row[0]:row[1]], allow_nmi=False),
                       SCOPE_HEADING_BLOCK, *row[:2], withheld)
        if got:
            return got
    got = _reading(_read_scope(text[s:e0], allow_nmi=True, rel_start=char_start - s,
                               rel_end=char_end - s), SCOPE_SENTENCE, s, e, withheld)
    if got:
        return got
    if row and not row[2]:  # NMI is not read here either: a row mixes cells.
        got = _reading(_read_scope(text[row[0]:row[1]], allow_nmi=False),
                       SCOPE_HEADING_BLOCK, *row[:2], withheld)
        if got:
            return got
    block = block_from(text, mk, starts, char_start)
    if block and block[1] >= char_end:
        # NMI is not read here: a block can mix statements about different things.
        got = _reading(_read_scope(text[block[0]:block[1]], allow_nmi=False),
                       SCOPE_HEADING_BLOCK, *block, withheld)
        if got:
            return got
    return AdviceReading("not_read", False, None, SCOPE_SENTENCE, s, e)
