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
    grain: ~739 rules against 21,664 pairs.
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
