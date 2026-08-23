"""The ONLY module that writes the interaction tables.

It mirrors classes.py's single-writer role, and enforces the same discipline:
`class_contraindication` is a REBUILDABLE PROJECTION of MED-RT, not the
append-only signed overlay. So inserts dedupe (ON CONFLICT DO NOTHING) and
clear_source_contraindications() deliberately DELETEs, letting a newer MED-RT
release fully replace the previous one -- a contraindication retracted upstream
has to disappear here too, which an insert-only merge could never express.

Slice 5b (db/014) added three more tables to this module's remit, so it now
writes FOUR: `class_contraindication` above, plus `moiety_condition_contraindication`
(drug-condition, e.g. CI_with) and `moiety_contraindication` (drug-drug, CI_ChemClass's
moiety arm), plus the `ingest_unresolved_ci_object` worklist that records what an
ingest deliberately withheld. Same rebuildable-projection discipline throughout.

Since db/027 (#35) it also writes `class_expansion_policy`, which is NOT a
rebuildable projection -- it holds curator judgement and an ingest must never wipe
it. `record_expansion_decision` and `withdraw_expansion_decision` are the only
correct way to revise it: the table is append-only, so a revision is INSERT-then-
supersede, and getting that ordering backwards fails at COMMIT rather than at the
call that caused it. The UPDATE half now executes in overlay.py (#59) --
overlay.supersede is what record_expansion_decision calls, and overlay.py's
docstring is where the reason for the ordering lives, stated once rather than
restated here.
"""
import uuid
from collections.abc import Iterable

import psycopg

from drugref import db, overlay


CONTRAINDICATION_TABLES = ("class_contraindication",)


def clear_source_contraindications(conn: psycopg.Connection, source: str) -> None:
    """Drop every contraindication contributed by `source`.

    Called at the start of a re-ingest so a new upstream release REPLACES the
    previous one. Scoped by the run's source (as classes.clear_source_edges is), so
    an unrelated feed's rows survive; run before any of this run's rows are written,
    it only ever removes the prior release's.
    """
    db.clear_source_tables(conn, CONTRAINDICATION_TABLES, source)


def unresolved_expansion_policy(conn: psycopg.Connection, source: str) -> list[str]:
    """The `source_code`s of `source`'s BINDING expansion decisions that resolve to no
    class.

    A read rather than a write, kept in this module because it reads
    class_expansion_policy, which is contraindication-expansion policy and so this
    module's business.

    WHY AN ORCHESTRATOR HAS TO ASK, AND NOT MERELY A VIEW EXIST. A deny that matches
    nothing looks exactly like a deny that is working: the pair set is silently wider
    and nothing fails. The condition only ever arises FROM AN INGEST -- upstream
    re-keys or withdraws a class the curator ruled on -- so the ingest that can cause
    it is the thing that has to report it. db/010 shipped the detector
    (expansion_policy_unresolved) with no consumer at all, which is precisely the
    failure mode it was written to catch.

    NOT an error: a re-keyed class is upstream's prerogative, and aborting an ingest
    over a stale curator note would be worse than the stale note. It is a worklist
    number, in the same class as the unmatched-ingredient counts.

    Scoped by source so a MED-RT run reports on MED-RT's decisions only -- a
    MeSH-keyed decision dangling because MeSH has not been ingested yet is not MED-RT's
    news to report.
    """
    return [row[0] for row in conn.execute(
        "SELECT source_code FROM drugref.expansion_policy_unresolved "
        "WHERE source = %s ORDER BY source_code", (source,)).fetchall()]


class NoLiveDecisionError(LookupError):
    """Raised when a withdrawal names a class carrying no live expansion decision."""


# The one Python copy of a value whose home is db/027's CHECK. It exists because
# withdraw_expansion_decision has to name the value it writes, and because an operator
# surface has to be able to refuse it (see cli._handle_policy_record). Two literals
# would be two things to disagree with each other; one constant read by both is not a
# second vocabulary.
WITHDRAWN = "withdrawn"


def record_expansion_decision(conn: psycopg.Connection, source: str, source_code: str,
                              decision: str, class_name: str, rationale: str,
                              reviewed_by: str, reviewed_against: str) -> int:
    """Record (or revise) whether a contraindicated class expands over its subtree.

    Returns the new `policy_id`. THE ONLY WAY TO REVISE A DECISION: since db/027 the
    table is append-only, so a revision INSERTs the new judgement and then points
    whatever was live at it. The previous rationale survives as history, which is the
    whole of #35 -- "what did we last say about this class, against which release, and
    why did we change our mind" has to be answerable from the database.

    ORDER MATTERS AND IS EASY TO GET WRONG: `superseded_by` must reference a row that
    already exists, and getting it backwards fails at COMMIT rather than here. That is
    why this is a function and not a paragraph of documentation.

    `decision` is passed straight through to the CHECK in db/027, which is the one
    place the vocabulary lives -- restating it here would be a second list to disagree
    with the first (db/006). An unrecognised value therefore raises CheckViolation.

    THAT INCLUDES `withdrawn`, AND THIS FUNCTION DOES NOT GUARD IT. Passing it here
    succeeds even when the class carries no live decision at all, writing a row that
    says a judgement was retracted where none was ever made. It binds nothing (every
    reader goes through class_expansion_policy_current, to which `withdrawn` and absent
    look alike), so the harm is a misleading audit trail rather than a wrong pair set.
    But the two things withdraw_expansion_decision exists to guarantee -- the
    NoLiveDecisionError that catches a caller believing something false, and carrying
    `class_name` forward so a withdrawal cannot introduce a name nobody reviewed -- are
    BYPASSED on this path. Withdraw through withdraw_expansion_decision. The check is
    not repeated here because it would put a member of the decision vocabulary back
    into Python, which is exactly what the paragraph above refuses to do; a caller
    reaching for `withdrawn` here is reaching past the function that owns it.

    NOTE the caller owns the transaction, as everywhere else in this module: nothing
    here commits. The single-live check is DEFERRED, so a mistake surfaces at the
    caller's COMMIT.
    """
    new_id = conn.execute(
        "INSERT INTO drugref.class_expansion_policy "
        "(source, source_code, decision, class_name, rationale, reviewed_by, "
        "reviewed_against) VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING policy_id",
        (source, source_code, decision, class_name, rationale, reviewed_by,
         reviewed_against)).fetchone()[0]
    # Point whatever was live at the new row -- including a `withdrawn` one, which is
    # live but does not bind.
    overlay.supersede(conn, "class_expansion_policy", "policy_id", new_id,
                      ("source", "source_code"), (source, source_code))
    return new_id


def withdraw_expansion_decision(conn: psycopg.Connection, source: str, source_code: str,
                                rationale: str, reviewed_by: str,
                                reviewed_against: str) -> int:
    """Retract the live decision for a class, returning it to
    gap_unreviewed_expansion_root.

    WITHDRAWN IS NOT `allow`. Absent means UNREVIEWED -- it expands AND raises a
    question on gap_unreviewed_expansion_root -- and an append-only table can never
    return a class to absent, so `withdrawn` is what says "no current judgement". Use
    it when a rationale has gone stale (it rested on a release measurement that no
    longer holds), or when a release stops defining the class at all: that is the case
    medrt_run's "re-key or withdraw" warning is about.

    `class_name` is carried forward from the row being withdrawn rather than asked of
    the caller, so a withdrawal cannot introduce a name nobody reviewed.

    Raises NoLiveDecisionError if no decision is live: withdrawing one nobody made
    means the caller believes something false, and silently doing nothing would leave
    them believing it.
    """
    row = conn.execute(
        "SELECT class_name FROM drugref.class_expansion_policy "
        "WHERE source = %s AND source_code = %s AND superseded_by IS NULL",
        (source, source_code)).fetchone()
    if row is None:
        raise NoLiveDecisionError(
            f"no live expansion decision for {source} {source_code}: "
            "nothing to withdraw")
    return record_expansion_decision(conn, source, source_code, WITHDRAWN, row[0],
                                     rationale, reviewed_by, reviewed_against)


def live_decisions(conn: psycopg.Connection) -> list[tuple[str, str, str, str]]:
    """Every expansion decision that currently BINDS, as (source, code, decision, name).

    Through class_expansion_policy_current, like every other reader -- the view is
    where "live" and "binding" are told apart, and a `withdrawn` row is live without
    binding. A reader that asked the base table for `superseded_by IS NULL` would
    report withdrawals as decisions, which is exactly the merge db/027 forbids.
    """
    return conn.execute(
        "SELECT source, source_code, decision, class_name "
        "FROM drugref.class_expansion_policy_current "
        "ORDER BY source, source_code").fetchall()


def decision_history(conn: psycopg.Connection, source: str,
                     source_code: str) -> list[tuple]:
    """Every ruling ever recorded for one class, oldest first.

    (policy_id, decision, rationale, reviewed_by, reviewed_against, superseded_by).

    THE ONE READER THAT MUST NAME THE BASE TABLE, and deliberately so: history is
    precisely what class_expansion_policy_current filters out, so asking the view
    would return at most one row and answer none of the question #35 exists to answer
    -- what did we last say, against which release, and why did we change our mind.

    Ordered by policy_id, which is a surrogate sequence: rows are strictly
    append-only, so insertion order IS chronology. reviewed_at would tie for two
    rulings recorded in one transaction.

    An empty list is a legitimate answer -- absent means UNREVIEWED, which expands and
    raises a question -- not an error.
    """
    return conn.execute(
        "SELECT policy_id, decision, rationale, reviewed_by, reviewed_against, "
        "superseded_by FROM drugref.class_expansion_policy "
        "WHERE source = %s AND source_code = %s ORDER BY policy_id",
        (source, source_code)).fetchall()


def add_contraindication(conn: psycopg.Connection, subject_moiety_uuid: uuid.UUID,
                         object_class_uuid: uuid.UUID, relationship: str,
                         source: str, ingest_run_id: int) -> bool:
    """Record that `subject_moiety_uuid` is contraindicated with a co-administered
    drug of `object_class_uuid`, on axis `relationship` (CI_MoA / CI_PE).

    Returns True if a new row was inserted. ON CONFLICT DO NOTHING keeps a file that
    repeats the same assertion harmless.
    """
    cur = conn.execute(
        "INSERT INTO drugref.class_contraindication "
        "(subject_moiety_uuid, object_class_uuid, relationship, source, ingest_run) "
        "VALUES (%s, %s, %s, %s, %s) ON CONFLICT DO NOTHING",
        (subject_moiety_uuid, object_class_uuid, relationship, source, ingest_run_id))
    return cur.rowcount == 1


# All THREE tables one slice-5b ingest writes. The third is the odd one out and is
# the one whose omission is invisible: drop it and both relations still rebuild
# correctly, the ingest still succeeds, and only gap_unresolved_ci_object's
# curator-facing rule counts creep upward release after release (405 -> 810 ->
# 1,215). Pinned by name in test_source_clear_contract.
MESH_CONTRAINDICATION_TABLES = ("moiety_condition_contraindication",
                                "moiety_contraindication",
                                "ingest_unresolved_ci_object")


def clear_source_mesh_contraindications(conn: psycopg.Connection, source: str) -> None:
    """Drop every slice-5b contraindication contributed by `source`.

    Covers BOTH relations plus the unresolved-object worklist, because one ingest
    writes all three and a partial clear would leave last release's rows beside this
    one's. Same rebuildable-projection discipline as
    clear_source_contraindications: a contraindication retracted upstream has to
    disappear here too, and an insert-only merge could never express that.

    The worklist is cleared for the reason classes.clear_source_unmatched_ingredients
    gives: an object that starts resolving must LEAVE the list, or the worklist grows
    by its own length every ingest and never shrinks.
    """
    db.clear_source_tables(conn, MESH_CONTRAINDICATION_TABLES, source)


def add_condition_contraindication(conn: psycopg.Connection,
                                   subject_moiety_uuid: uuid.UUID,
                                   object_condition_uuid: uuid.UUID,
                                   relationship: str, source: str,
                                   ingest_run_id: int) -> bool:
    """Record that `subject_moiety_uuid` is contraindicated in a patient who has
    `object_condition_uuid`, on axis `relationship` (CI_with).

    Returns True if a new row was inserted. The clinical direction is carried
    entirely by which side is which -- a condition is not a drug, so the two are not
    even the same kind of thing, but the column names still say so explicitly.
    """
    cur = conn.execute(
        "INSERT INTO drugref.moiety_condition_contraindication "
        "(subject_moiety_uuid, object_condition_uuid, relationship, source, "
        "ingest_run) "
        "VALUES (%s, %s, %s, %s, %s) ON CONFLICT DO NOTHING",
        (subject_moiety_uuid, object_condition_uuid, relationship, source,
         ingest_run_id))
    return cur.rowcount == 1


def add_moiety_contraindication(conn: psycopg.Connection,
                                subject_moiety_uuid: uuid.UUID,
                                object_moiety_uuid: uuid.UUID, relationship: str,
                                source: str, ingest_run_id: int) -> bool:
    """Record that `subject_moiety_uuid` must not be co-administered with
    `object_moiety_uuid` (CI_ChemClass's moiety arm).

    DIRECTIONAL, and both directions may legitimately be stored: MED-RT states which
    drug the assertion is ABOUT, and it does not always assert the converse. A
    consumer wanting "is this pair contraindicated at all" must query both columns
    rather than assume symmetry.

    Returns True if a new row was inserted.
    """
    cur = conn.execute(
        "INSERT INTO drugref.moiety_contraindication "
        "(subject_moiety_uuid, object_moiety_uuid, relationship, source, ingest_run) "
        "VALUES (%s, %s, %s, %s, %s) ON CONFLICT DO NOTHING",
        (subject_moiety_uuid, object_moiety_uuid, relationship, source,
         ingest_run_id))
    return cur.rowcount == 1


# ---- Task 10: the class-subject grain (db/032, design spec section 14) --------
#
# A second candidate-tier table, not a second write path for the existing one:
# class_pair_contraindication holds rules whose SUBJECT is a class ("SSRIs are
# contraindicated with MAOIs"), where class_contraindication above only ever
# holds rules whose subject is a single moiety. db/032's own preamble records
# why this is two tables rather than a nullable column on the first: the
# deferred single-live guard (db/023) compares natural-key columns by
# EQUALITY, and NULL = NULL is never true in SQL, so a polymorphic subject
# would make that guard silently stop guarding for exactly the rows it was
# widened to cover.
CLASS_PAIR_CONTRAINDICATION_TABLES = ("class_pair_contraindication",)


def clear_source_class_pair_contraindications(conn: psycopg.Connection,
                                              source: str) -> None:
    """Drop every class-subject contraindication contributed by `source`.

    Mirrors clear_source_contraindications exactly, one grain over: called at
    the start of a re-ingest so a new upstream release REPLACES the previous
    one, scoped by the run's source so an unrelated feed's rows survive.
    """
    db.clear_source_tables(conn, CLASS_PAIR_CONTRAINDICATION_TABLES, source)


def add_class_pair_contraindication(conn: psycopg.Connection,
                                    subject_class_uuid: uuid.UUID,
                                    object_class_uuid: uuid.UUID,
                                    relationship: str, source: str,
                                    ingest_run_id: int) -> bool:
    """Record that every member of `subject_class_uuid` is contraindicated
    with a co-administered member of `object_class_uuid`, on axis
    `relationship` (CI_MoA / CI_PE / CI_EPC).

    Mirrors add_contraindication exactly, one grain over: BOTH endpoints are
    classes here instead of a moiety and a class. Returns True if a new row
    was inserted. ON CONFLICT DO NOTHING keeps a file that repeats the same
    assertion harmless -- the same discipline add_contraindication already
    follows, extended to the shape Task 10 adds.
    """
    cur = conn.execute(
        "INSERT INTO drugref.class_pair_contraindication "
        "(subject_class_uuid, object_class_uuid, relationship, source, ingest_run) "
        "VALUES (%s, %s, %s, %s, %s) ON CONFLICT DO NOTHING",
        (subject_class_uuid, object_class_uuid, relationship, source, ingest_run_id))
    return cur.rowcount == 1


def record_unresolved_ci_objects(
        conn: psycopg.Connection,
        rows: Iterable[tuple[str, str, str, str, str | None, str, int]],
        ingest_run_id: int) -> int:
    """Persist contraindication objects drugref did not ingest, and WHY.

    `rows` is an iterable of (source, relationship, object_source, object_code,
    object_name, object_kind, assertion_count). `object_name` is the only optional
    field -- MeSH's chemical tree always has one, but a future object_source might
    not, so the type says so rather than assuming.

    `object_kind` is CHEMICAL_CLASS or UNREGISTERED_SUBSTANCE (db/014), and passing it
    is MANDATORY because the two get different curator questions: a class is withheld
    pending a ruling on structural-tree expansion, while an unregistered substance is a
    coverage gap answered by registering the moiety. Reading "no moiety resolved" as
    "therefore a class" is the defect db/014's object_kind closes, so this function will
    not infer the kind on a caller's behalf.

    Not an error and not a drop: these are real upstream assertions drugref could
    not or would not ingest (see db/014 on the sulfonamide case). Persisting the
    IDENTITY rather than only a count is what lets gap_unresolved_ci_object be a
    query -- the exact lesson db/008 drew when the earlier ingest kept only the
    COUNT of unmatched ingredients and discarded the RxCUIs.

    Batched, like classes.add_unmatched_ingredients: nobody needs the per-row
    insert-vs-conflict answer, because the caller already holds the deduped set.
    Returns rows written.
    """
    batch = [(ingest_run_id, *row) for row in rows]
    if not batch:
        return 0
    with conn.cursor() as cur:
        cur.executemany(
            "INSERT INTO drugref.ingest_unresolved_ci_object "
            "(ingest_run, source, relationship, object_source, object_code, "
            " object_name, object_kind, assertion_count) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s) "
            "ON CONFLICT DO NOTHING", batch)
    return len(batch)


# ---- issue 101: DrugCentral's unordered graded pairs (db/049) ------------------
#
# The projection this source owns, cleared per-source on every re-ingest. Named as
# a tuple to match db.clear_source_tables's signature and every sibling constant
# (MESH_CONTRAINDICATION_TABLES above, onchigh_run's UNRESOLVED_ENDPOINT_TABLES,
# fda_cyp_run's FDA_CYP_TABLES). ONE table, because unlike db/031's ONC endpoints
# the unresolvable rows live in the assertion table itself.
DRUGCENTRAL_TABLES = ("drugcentral_ddi_assertion",)


def clear_source_drugcentral(conn: psycopg.Connection, source: str) -> None:
    """Drop every DrugCentral assertion contributed by `source`.

    Same rebuildable-projection discipline as clear_source_contraindications: an
    assertion retracted upstream has to be able to DISAPPEAR, and an insert-only
    merge could never express that. It also clears the unresolved rows, for the
    reason classes.clear_source_unmatched_ingredients gives: an endpoint that
    starts resolving must LEAVE the worklist, or the worklist grows by its own
    length every ingest and never shrinks.
    """
    db.clear_source_tables(conn, DRUGCENTRAL_TABLES, source)


def add_drugcentral_assertion(conn: psycopg.Connection, *,
                              ingest_run_id: int,
                              source: str,
                              upstream_key: str,
                              endpoint_1_name: str,
                              endpoint_2_name: str,
                              upstream_label: str,
                              severity_label: str,
                              moiety_1_uuid: uuid.UUID | None,
                              moiety_2_uuid: uuid.UUID | None,
                              route_1: str,
                              route_2: str) -> bool:
    """Record one published DrugCentral interaction, resolved or not.

    KEYWORD-ONLY, and that is not style: this function takes four strings and two
    optional UUIDs in two matched pairs, and a positional call that swapped
    endpoint_1 with endpoint_2 -- or route_1 with route_2 -- would insert cleanly
    and produce a wrong resolution route beside a right moiety. The endpoints are
    UNORDERED, so nothing downstream could ever detect the swap.

    NOT DIRECTIONAL. `endpoint_1`/`endpoint_2` are the dump's own column order and
    carry no clinical meaning: measured 2026-08-23, 33 pairs are published in both
    orders and no ordered pair repeats. drugcentral_ddi_pair canonicalises.

    Returns True if a new row was inserted. ON CONFLICT DO NOTHING keeps a dump
    that repeats one assertion harmless, as every sibling writer does.
    """
    cur = conn.execute(
        "INSERT INTO drugref.drugcentral_ddi_assertion "
        "(ingest_run, source, upstream_key, endpoint_1_name, endpoint_2_name, "
        " upstream_label, severity_label, moiety_1_uuid, moiety_2_uuid, "
        " route_1, route_2) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) "
        "ON CONFLICT DO NOTHING",
        (ingest_run_id, source, upstream_key, endpoint_1_name, endpoint_2_name,
         upstream_label, severity_label, moiety_1_uuid, moiety_2_uuid,
         route_1, route_2))
    return cur.rowcount == 1
