# src/drugref/curation.py
"""Writers for the curated overlay: drugref's own clinical judgements (db/029).

WHAT THIS MODULE OWNS. Slices 5a/5b/5b.2 project CANDIDATE rows from upstream -- MED-RT
asserts that a drug is contraindicated with a class, or in a condition, and asserts
nothing about how severe that is, by what mechanism, what to do about it, or how well
attested it is. Those four dimensions are drugref's to state, and this module is the
only supported way to state them.

THE ONE SEQUENCE THE TIER ADMITS, per overlay.py:

    1. INSERT the new assertion, which becomes live.
    2. UPDATE whatever was live for the same natural key to point at it.

In that order, always. Both functions below do exactly that, which is the whole reason
they exist rather than a note in the documentation telling each caller to get it right.

NOTHING HERE COMMITS. The caller owns the transaction, as everywhere in these modules,
and the single-live check is DEFERRED -- so a mistake surfaces at the caller's COMMIT,
not here.

NO VOCABULARY IS RESTATED IN PYTHON. `severity`, `evidence_grade`, `relationship` and
`ruling` live in db/029's CHECK constraints, which is the one place they can live
without a second list to drift from the first (db/006's lesson, learned when a CASE in
a view and a CHECK in a table disagreed silently). An unrecognised value raises
CheckViolation from the database, and that is the intended behaviour rather than a gap.
"""
import uuid
from dataclasses import dataclass

import psycopg

from drugref import overlay


def record_interaction_judgement(
        conn: psycopg.Connection,
        subject_moiety_uuid: uuid.UUID,
        object_class_uuid: uuid.UUID,
        relationship: str,
        applies: bool,
        *,
        severity: str | None = None,
        mechanism: str | None = None,
        management: str | None = None,
        evidence_grade: str | None = None,
        question_uuid: uuid.UUID | None = None,
        source: str = "DRUGREF",
        reviewed_by: str,
        reviewed_against: str) -> int:
    """Record (or revise) drugref's judgement on one class-level CI_MoA/CI_PE rule.

    Returns the new `curated_interaction_id`. THE ONLY SUPPORTED WAY TO REVISE ONE:
    the table is append-only, so a revision INSERTs the new judgement and then points
    whatever was live at it. The previous grade survives as history, which matters most
    for exactly the rows that fired an alert.

    `applies=False` is how a rule is RETIRED, and it is not a deletion: supersession
    alone can never withdraw anything, because a correction must point at a later row
    carrying the SAME natural key and therefore always leaves one live. A retired rule
    stops reaching `curated_ddi_pair` and stops being asked about on the worklist.

    A retiring call passes no grading -- db/029's completeness CHECK refuses a
    non-applying row that carries severity or evidence_grade, and refuses an applying
    row that omits either. That is deliberately enforced in the database rather than
    here, so a caller bypassing this function cannot write an incoherent row.

    `question_uuid` is optional: it links the judgement to the gap question it answers,
    whose citations live in `question_evidence`. Omitting it is legal and MEANS
    SOMETHING -- the grade rests on nothing recorded. Curated is not verified.

    THE JUDGEMENT IS KEYED ON THE RULE, not on the drug pairs it expands to, so one
    call grades every pair the rule reaches. That is the point of curating at this
    grain: 635 rules against 21,664 pairs (not the ~739 earlier drafts of this module
    quoted -- that was the raw pre-gate MED-RT terminology count, never
    class_contraindication's own measured row count; of the 635, 595 reach the
    worklist and 40 pair with nobody, see PROJECT-NOTES.md "Slice 5c.1").
    """
    new_id = conn.execute(
        "INSERT INTO drugref.curated_interaction "
        "(subject_moiety_uuid, object_class_uuid, relationship, applies, severity, "
        " mechanism, management, evidence_grade, question_uuid, source, reviewed_by, "
        " reviewed_against) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) "
        "RETURNING curated_interaction_id",
        (subject_moiety_uuid, object_class_uuid, relationship, applies, severity,
         mechanism, management, evidence_grade, question_uuid, source, reviewed_by,
         reviewed_against)).fetchone()[0]
    overlay.supersede(
        conn, "curated_interaction", "curated_interaction_id", new_id,
        ("subject_moiety_uuid", "object_class_uuid", "relationship"),
        (subject_moiety_uuid, object_class_uuid, relationship))
    return new_id


def record_condition_ruling(
        conn: psycopg.Connection,
        subject_moiety_uuid: uuid.UUID,
        object_condition_uuid: uuid.UUID,
        ruling: str,
        *,
        severity: str | None = None,
        mechanism: str | None = None,
        management: str | None = None,
        evidence_grade: str | None = None,
        question_uuid: uuid.UUID | None = None,
        source: str = "DRUGREF",
        reviewed_by: str,
        reviewed_against: str) -> int:
    """Record (or revise) drugref's ruling on one (drug, condition) pair.

    Returns the new `curated_condition_id`. Same append-then-point sequence as its
    sibling, and the same reason for existing.

    NOTE WHAT IS ABSENT FROM THE ARGUMENTS: `relationship`. The ruling is about the
    PAIR, not about one predicate over it, because the same pair carries both an
    indication and a contraindication in 168 cases and BOTH ARE OFTEN TRUE -- nine
    beta-blockers are both may_treat and CI_with against MeSH "Heart Failure", first
    line in stable chronic HFrEF and contraindicated in acute decompensation, with one
    MeSH descriptor covering both states. `ruling='context_dependent'` is how that is
    said, and taking a relationship here would let the same judgement be written twice
    and disagree with itself.

    `ruling='spurious'` retires the pair: reviewed, and the upstream assertion is
    wrong. It records the disagreement WITHOUT acting on it -- the candidate stays in
    its projection, because contradicting a source is not the same act as drugref
    changing how it reads its own DAG, and "what did the release say" must stay
    answerable next to "what does drugref say". A spurious row therefore reaches no
    read view. Like a retiring interaction judgement, it passes no grading.
    """
    new_id = conn.execute(
        "INSERT INTO drugref.curated_condition "
        "(subject_moiety_uuid, object_condition_uuid, ruling, severity, mechanism, "
        " management, evidence_grade, question_uuid, source, reviewed_by, "
        " reviewed_against) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) "
        "RETURNING curated_condition_id",
        (subject_moiety_uuid, object_condition_uuid, ruling, severity, mechanism,
         management, evidence_grade, question_uuid, source, reviewed_by,
         reviewed_against)).fetchone()[0]
    overlay.supersede(
        conn, "curated_condition", "curated_condition_id", new_id,
        ("subject_moiety_uuid", "object_condition_uuid"),
        (subject_moiety_uuid, object_condition_uuid))
    return new_id


_GRADED_COLUMNS = ("applies", "severity", "mechanism", "management", "evidence_grade")


def live_interaction_judgement(
        conn: psycopg.Connection,
        subject_moiety_uuid: uuid.UUID,
        object_class_uuid: uuid.UUID,
        relationship: str) -> dict | None:
    """The live row's GRADED fields for one (subject, class, relationship) natural
    key, or None if nothing has been curated yet.

    THIS IS WHAT MAKES `drugref curate` IDEMPOTENT BY COMPARISON RATHER THAN BY LUCK.
    curated_interaction is append-only, so a caller re-running the same file must be
    able to tell "nothing changed" from "this needs a new row" BEFORE writing --
    inserting unconditionally would leave a permanent duplicate that the deferred
    single-live trigger only reports at COMMIT, long after the write happened.

    RETURNS ONLY THE FIVE GRADED FIELDS -- `applies`, `severity`, `mechanism`,
    `management`, `evidence_grade` -- and deliberately NOT `reviewed_at` or
    `reviewed_by`. Those two describe WHO ran this comparison and WHEN, not WHAT was
    judged: `reviewed_at` moves on every invocation and `reviewed_by` is whatever the
    operator typed on this run, so a caller comparing them against a fresh judgement
    would supersede the entire file every time it ran -- which is the opposite of the
    append-only discipline this function exists to protect.
    """
    row = conn.execute(
        f"SELECT {', '.join(_GRADED_COLUMNS)} FROM drugref.curated_interaction "
        "WHERE subject_moiety_uuid = %s AND object_class_uuid = %s "
        "AND relationship = %s AND superseded_by IS NULL",
        (subject_moiety_uuid, object_class_uuid, relationship)).fetchone()
    if row is None:
        return None
    return dict(zip(_GRADED_COLUMNS, row, strict=True))


# ---- Task 10: the class-subject grain (db/032, design spec section 14) --------
#
# curated_class_interaction is curated_interaction's SIBLING, not its
# replacement: it grades a rule whose SUBJECT is a class ("SSRIs are
# contraindicated with MAOIs") rather than a single moiety, over the
# `class_pair_contraindication` candidate tier interactions.py writes. Same
# append-then-point sequence, same reason for existing as a function rather
# than a paragraph telling every caller to get the ordering right, and the
# same "no vocabulary restated in Python" discipline -- db/032's own CHECKs
# and ci_axis FK are the one place severity/evidence_grade/relationship live.


def record_class_interaction_judgement(
        conn: psycopg.Connection,
        subject_class_uuid: uuid.UUID,
        object_class_uuid: uuid.UUID,
        relationship: str,
        applies: bool,
        *,
        severity: str | None = None,
        mechanism: str | None = None,
        management: str | None = None,
        evidence_grade: str | None = None,
        question_uuid: uuid.UUID | None = None,
        source: str = "DRUGREF",
        reviewed_by: str,
        reviewed_against: str) -> int:
    """Record (or revise) drugref's judgement on one CLASS x CLASS rule.

    Returns the new `curated_class_interaction_id`. Mirrors
    record_interaction_judgement exactly, one grain over -- see that
    function's own docstring for the full reasoning (append-then-point,
    `applies=False` as retirement rather than deletion, `question_uuid`
    optional and meaningfully so). The one difference is the natural key:
    (subject_class_uuid, object_class_uuid, relationship) instead of
    (subject_moiety_uuid, object_class_uuid, relationship), because THE RULE
    ITSELF is about a class, not a single drug (design spec section 14) --
    one call grades every pair the rule expands to on BOTH sides once the
    read path (Task 11) builds the both-sides expansion.
    """
    new_id = conn.execute(
        "INSERT INTO drugref.curated_class_interaction "
        "(subject_class_uuid, object_class_uuid, relationship, applies, severity, "
        " mechanism, management, evidence_grade, question_uuid, source, reviewed_by, "
        " reviewed_against) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) "
        "RETURNING curated_class_interaction_id",
        (subject_class_uuid, object_class_uuid, relationship, applies, severity,
         mechanism, management, evidence_grade, question_uuid, source, reviewed_by,
         reviewed_against)).fetchone()[0]
    overlay.supersede(
        conn, "curated_class_interaction", "curated_class_interaction_id", new_id,
        ("subject_class_uuid", "object_class_uuid", "relationship"),
        (subject_class_uuid, object_class_uuid, relationship))
    return new_id


def live_class_interaction_judgement(
        conn: psycopg.Connection,
        subject_class_uuid: uuid.UUID,
        object_class_uuid: uuid.UUID,
        relationship: str) -> dict | None:
    """The live row's GRADED fields for one (subject class, object class,
    relationship) natural key, or None if nothing has been curated yet.

    Mirrors live_interaction_judgement exactly, one grain over -- the same
    idempotent-by-comparison contract `curate_onchigh`'s class-subject path
    (Task 10) relies on, reusing the SAME `_GRADED_COLUMNS` tuple: the five
    graded fields have the same names and the same meaning on both grains, so
    a second, identically-spelled tuple here would be a second thing to
    disagree with the first the moment one of them is widened.
    """
    row = conn.execute(
        f"SELECT {', '.join(_GRADED_COLUMNS)} FROM drugref.curated_class_interaction "
        "WHERE subject_class_uuid = %s AND object_class_uuid = %s "
        "AND relationship = %s AND superseded_by IS NULL",
        (subject_class_uuid, object_class_uuid, relationship)).fetchone()
    if row is None:
        return None
    return dict(zip(_GRADED_COLUMNS, row, strict=True))


@dataclass(frozen=True)
class UnresolvedTarget:
    """One live curated row whose candidate is no longer projected.

    A named record rather than a bare tuple because the caller printing it must not
    have to remember column order, and because `relationship` is None for a condition
    ruling -- which is meaning, not missing data: `curated_condition` is keyed on the
    (drug, condition) PAIR and deliberately carries no predicate.

    `target_table` DISCRIMINATES the other two fields, and Python cannot say so here:
    on a `curated_interaction` row `object_uuid` is a substance_class UUID and
    `relationship` is present; on a `curated_condition` row it is a condition UUID and
    `relationship` is None. Two disjoint namespaces in one uuid field. Deliberately NOT
    enforced in a __post_init__, for this module's opening rule -- such a check would
    restate the view's arm labels in Python, and a third UNION arm in a later migration
    would then make `drugref status` refuse a legitimate row.
    """
    target_table: str
    subject_moiety: uuid.UUID
    object_uuid: uuid.UUID
    relationship: str | None
    reviewed_by: str
    reviewed_against: str


# THE ONE COLUMN LIST, and it is one on purpose. It was two -- this tuple's contents
# spelled out in the SELECT below, and UnresolvedTarget's field list above -- kept in
# step by nothing but sitting a few lines apart. POSITIONALLY, `subject_moiety` and
# `object_uuid` are both uuid and `reviewed_by`/`reviewed_against` are both text, so
# four text columns and two uuid ones admit 48 type-compatible orderings, of which one
# is right; transposing either pair builds a WELL-TYPED WRONG record that no annotation
# and no arity check can see. Binding by NAME removes the failure mode rather than
# testing for it, and `strict=True` catches a column the view gained or lost.
_UNRESOLVED_COLUMNS = ("target_table", "subject_moiety", "object_uuid",
                       "relationship", "reviewed_by", "reviewed_against")


def unresolved_targets(conn: psycopg.Connection) -> list[UnresolvedTarget]:
    """Live curated rows pointing at a candidate that no longer exists. EXPECTED EMPTY.

    WHY A FUNCTION HAS TO ASK, AND NOT MERELY A VIEW EXIST -- issue 76, and the second
    time this project has had to learn it. See `unresolved_expansion_policy` in
    interactions.py for the first: db/010 shipped `expansion_policy_unresolved` with no
    consumer at all. db/029 then shipped `curated_target_unresolved` the same way. A
    detector nobody calls reports nothing to nobody. (That docstring is CITED, not
    quoted: the same sentence copied into three files is three things to disagree with
    each other the first time one of them is reworded.)

    HOW AN ORPHAN HAPPENS. A curated row names its candidate by NATURAL KEY and carries
    no foreign key into it, because candidates are rebuildable projections and an FK
    would either block the per-source rebuild or cascade curator judgement away with it
    (db/029 section 5). The cost of that deliberate choice is that a rebuild CAN leave a
    judgement pointing at a candidate upstream has re-keyed or withdrawn, and nothing
    fails when it does.

    NOT AN ERROR, for `unresolved_expansion_policy`'s reason: upstream re-keying a
    concept is upstream's prerogative, and treating a stale curator note as a fault
    would be worse than the stale note. It is an operator signal, deliberately not a
    gap kind -- a vanished candidate is news about an upstream change, not a clinical
    question a curator can answer. (The sibling says "aborting an ingest", which is
    ITS stake and not this one's: that function runs inside `medrt_run`, whereas this
    one's only caller is `drugref status`, which starts nothing and can abort nothing.)

    NOT SCOPED BY SOURCE, unlike its expansion-policy sibling, because
    `curated_target_unresolved` has no source column to scope by: it compares curated
    rows against three projections at once (`class_contraindication`, and both
    `moiety_condition_*` tables), and `db/029` is merged and therefore frozen, so adding
    one would mean a new migration. That makes this a whole-database question rather
    than a per-run one, which is why `drugref status` is its consumer rather than an
    ingest summary.

    LIVE ROWS ONLY -- the view's own `superseded_by IS NULL`. A corrected judgement's
    predecessor still names the old candidate, and reporting it would make every
    correction look like breakage.

    ORDERED TOTALLY, on all four key columns rather than the first two. The expected
    shape of a rebuild orphan is SEVERAL rows sharing a subject -- a re-key drops every
    `class_contraindication` row for that moiety at once -- and those tie on
    (target_table, subject_moiety), which left Postgres free to return them in any
    order. `drugref status` would print a different ordering run to run, and any test
    asserting more than one row would flake.
    """
    return [UnresolvedTarget(**dict(zip(_UNRESOLVED_COLUMNS, row, strict=True)))
            for row in conn.execute(
                f"SELECT {', '.join(_UNRESOLVED_COLUMNS)} "
                "FROM drugref.curated_target_unresolved "
                "ORDER BY target_table, subject_moiety, object_uuid, "
                "relationship").fetchall()]
