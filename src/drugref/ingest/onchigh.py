# src/drugref/ingest/onchigh.py
"""PURE structural parser for the ONC high-priority DDI list (slice 5c.2).

No database access and no network: this module reads
`src/drugref/data/onc_high_priority.toml` (or, in tests, a fixture with the
same shape) with the standard-library `tomllib` and turns it into frozen
dataclasses. It never resolves a UNII or a MED-RT code to anything, never
opens a connection, and never writes a row. The orchestrator
(ingest/onchigh_run.py, a later task) owns the transaction, resolves
identifiers to moiety/class UUIDs, and is the only writer -- the same split
pbs.py and medrt.py already use for their own feeds.

THE DIVISION OF VALIDATION LABOUR, AND WHY IT MATTERS HERE. This parser
validates STRUCTURE: is a citation present, is a grade present exactly when
`applies` is true, is every entry_id unique. It does NOT validate VOCABULARY:
whether "major" is a legal severity, "established" a legal evidence grade, or
"CI_with"/"may_treat" a legal relationship. Those lists live in ONE place --
db/029's CHECK constraints on curated_interaction (and, for `applies`'s
completeness rule, the same migration's
curated_interaction_ruling_is_complete) -- and copying them into Python here
would recreate exactly the defect db/006 was issued to fix: db/004 once kept
a CHECK constraint AND a matching Python CASE in step, and the day they drifted
an inserted row expanded to zero pairs with no error at all. This repo has
lost several rounds to that shape already (see PROJECT-NOTES.md, "a
vocabulary written down twice is two things that can disagree"), so the rule
here is simple: if the database can refuse it with a CHECK, this module does
not also refuse it by value -- it only refuses malformed SHAPES, and lets an
illegal value reach the orchestrator's INSERT, where the real constraint lives.

THE ONE DELIBERATE EXCEPTION IS `axis`. Unlike severity/evidence_grade, an
axis selects which downstream table gets written and how it is joined
(ci_axis maps CI_MoA -> has_MoA, CI_PE -> has_PE, CI_EPC -> has_EPC; db/031
section 3). A bad severity fails one row's CHECK constraint inside the
orchestrator's transaction and rolls back cleanly. A bad axis, left
unchecked, would let onchigh_run.py successfully write every EARLIER entry in
the file, then fail on the constraint for THIS entry midway through the
run -- an ingest that is neither fully applied nor fully absent, with no
single point where a rerun is safe. Catching it here, before any row is
written, is what keeps the whole file atomic from the orchestrator's point of
view. `_KNOWN_AXES` is intentionally a literal, hand-copied list (not read
from ci_axis, which would need a database connection this module must never
open) -- it drifts only if a future db/03x migration adds a fourth axis and
nobody updates this constant, and that failure mode is a test the next axis's
migration should add, not a reason to weaken the check here.
"""
import pathlib
import tomllib
from dataclasses import dataclass
from typing import NoReturn

# Legal ci_axis values as of db/031 (CI_MoA and CI_PE predate this slice;
# CI_EPC is db/031 section 3's own addition). See the module docstring above
# for why this one vocabulary is checked here despite the "structure only"
# rule everything else in this file follows.
_KNOWN_AXES = frozenset({"CI_MoA", "CI_PE", "CI_EPC"})

# The candidate fields the design spec (section 4) declares required on every
# entry, regardless of `applies`. All six are meant to be short, stable
# identifiers/labels -- unlike severity/evidence_grade there is no vocabulary
# to check, only "is something here at all".
_CANDIDATE_FIELDS = (
    "subject_unii", "subject_name", "object_medrt_code", "object_name",
    "axis", "citation")


class OncFormatError(ValueError):
    """Raised for any STRUCTURAL defect in the ONC file.

    Never raised for an illegal severity/evidence_grade/relationship VALUE --
    see the module docstring's division of labour. Every message names the
    offending `entry_id` (or, for a file-level defect such as a duplicate
    entry_id, the entry_id itself is the defect), so a curator reading the
    error learns which hand-authored entry to fix rather than reading a bare
    constraint name off a database traceback.
    """


@dataclass(frozen=True)
class OncCandidate:
    """WHAT THE PAPER SAYS -- the endpoint pair and citation, unresolved.

    Every field is the raw value as the file states it. Nothing here is
    looked up: the orchestrator, not this module, turns subject_unii into a
    moiety_uuid and object_medrt_code into a class_uuid.
    """
    subject_unii: str
    subject_name: str          # review aid ONLY -- not verified against the
                                # UNII by this module (needs a database; see
                                # the orchestrator, a later task)
    object_medrt_code: str
    object_name: str           # review aid ONLY, same caveat
    axis: str                  # checked against _KNOWN_AXES -- see above
    citation: str               # rule 6: never absent


@dataclass(frozen=True)
class OncJudgement:
    """WHAT DRUGREF SAYS -- the curator's own ruling over the candidate pair.

    `severity`/`evidence_grade` are left as `str | None` rather than an enum
    deliberately: their LEGAL values are a database concern (db/029), not
    this module's. What this module DOES enforce is completeness -- present
    together when `applies` is true, absent together when it is false --
    mirroring curated_interaction_ruling_is_complete's CHECK exactly, so a
    curator learns of the mismatch by entry_id here rather than from a
    constraint violation once the orchestrator writes the row.
    """
    applies: bool
    severity: str | None
    evidence_grade: str | None
    mechanism: str | None
    management: str | None


@dataclass(frozen=True)
class OncEntry:
    """One [[entry]] block: the human's handle, plus both its lifetimes."""
    entry_id: str
    candidate: OncCandidate
    judgement: OncJudgement


def _fail(entry_id: str | None, message: str) -> NoReturn:
    """Raise OncFormatError with entry_id folded into the message.

    A single choke point so every raise in this module names the entry the
    same way, rather than each call site re-deriving its own phrasing.
    Typed NoReturn (rather than None) so every caller below -- which reads a
    value back on the line after calling this on a failure path -- type-checks
    as unreachable-after-raise instead of "maybe None".
    """
    label = "(no entry_id)" if entry_id is None else repr(entry_id)
    raise OncFormatError(f"entry {label}: {message}")


def _require_block(entry: dict, block_name: str, entry_id: str | None) -> dict:
    """Fetch entry[block_name] as a dict, or raise naming what's missing."""
    block = entry.get(block_name)
    if not isinstance(block, dict):
        _fail(entry_id, f"missing required [entry.{block_name}] block")
    return block


def _require_str(block: dict, key: str, block_name: str,
                  entry_id: str | None) -> str:
    """Fetch block[key] as a non-blank string, or raise naming the field."""
    value = block.get(key)
    if not isinstance(value, str) or not value.strip():
        _fail(entry_id,
              f"[entry.{block_name}] is missing required field '{key}'")
    return value


def _parse_candidate(entry: dict, entry_id: str) -> OncCandidate:
    """Build one OncCandidate, checking presence of all six fields plus the
    one vocabulary this module is allowed to police: axis."""
    block = _require_block(entry, "candidate", entry_id)
    values = {key: _require_str(block, key, "candidate", entry_id)
               for key in _CANDIDATE_FIELDS}
    if values["axis"] not in _KNOWN_AXES:
        _fail(entry_id,
              f"unknown axis {values['axis']!r} -- ci_axis (db/031) currently "
              f"carries {sorted(_KNOWN_AXES)}; a genuinely new axis needs its "
              "own migration before this file can use it")
    return OncCandidate(**values)


def _optional_str(block: dict, key: str) -> str | None:
    """Fetch block[key] as a string, or None if it is absent/blank.

    Unlike _require_str, absence is not an error here -- mechanism,
    management, severity and evidence_grade are all legitimately unset on a
    non-asserting entry (see _parse_judgement's completeness check).
    """
    value = block.get(key)
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        return None
    return value


def _parse_judgement(entry: dict, entry_id: str) -> OncJudgement:
    """Build one OncJudgement, enforcing db/029's completeness shape:
    severity and evidence_grade present TOGETHER when applies is true,
    absent TOGETHER when it is false. The values themselves are never
    checked against a vocabulary -- see the module docstring."""
    block = _require_block(entry, "judgement", entry_id)
    applies = block.get("applies")
    if not isinstance(applies, bool):
        _fail(entry_id, "[entry.judgement] is missing required boolean "
                         "field 'applies'")
    severity = _optional_str(block, "severity")
    evidence_grade = _optional_str(block, "evidence_grade")
    if applies and (severity is None or evidence_grade is None):
        _fail(entry_id,
              "applies = true but severity/evidence_grade is missing -- "
              "an asserting judgement must grade both (db/029's "
              "curated_interaction_ruling_is_complete)")
    if not applies and (severity is not None or evidence_grade is not None):
        _fail(entry_id,
              "applies = false but carries a severity/evidence_grade -- a "
              "non-asserting judgement must carry neither (db/029's "
              "curated_interaction_ruling_is_complete, the other half)")
    return OncJudgement(
        applies=applies,
        severity=severity,
        evidence_grade=evidence_grade,
        mechanism=_optional_str(block, "mechanism"),
        management=_optional_str(block, "management"),
    )


def parse(path: pathlib.Path) -> tuple[OncEntry, ...]:
    """Read and structurally validate the ONC file, returning every entry.

    Opened in binary mode with tomllib.load, per tomllib's own contract (it
    refuses text-mode files). Returns a TUPLE, not a generator: unlike
    pbs.parse_items's streaming design, this file is small (a curated list,
    not a multi-megabyte release) and the whole-file checks below --
    duplicate entry_id in particular -- need every entry in hand before any
    of them can be trusted, so there is no meaningful partial result to
    stream out.
    """
    with open(path, "rb") as fh:
        data = tomllib.load(fh)
    raw_entries = data.get("entry", [])
    if not isinstance(raw_entries, list) or not raw_entries:
        raise OncFormatError(
            f"{path}: no [[entry]] blocks found -- an ONC file with nothing "
            "to ingest is a broken file, not an empty one")
    seen_ids: set[str] = set()
    parsed: list[OncEntry] = []
    for raw in raw_entries:
        entry_id = raw.get("entry_id")
        if not isinstance(entry_id, str) or not entry_id.strip():
            _fail(None, "missing required top-level field 'entry_id'")
        if entry_id in seen_ids:
            _fail(entry_id,
                  f"duplicate entry_id {entry_id!r} -- entry_id is the "
                  "handle a gap_key is minted from, so two entries sharing "
                  "one would collide on a single question_uuid")
        seen_ids.add(entry_id)
        parsed.append(OncEntry(
            entry_id=entry_id,
            candidate=_parse_candidate(raw, entry_id),
            judgement=_parse_judgement(raw, entry_id),
        ))
    return tuple(parsed)
