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
`ruling` live in the database, which is the one place they can live without a second
list to drift from the first (db/006's lesson, learned when a CASE in a view and a
CHECK in a table disagreed silently). An unrecognised value raises from the database,
and that is the intended behaviour rather than a gap. SINCE db/035 THE CLASS DIFFERS BY
COLUMN and nothing here depends on which: `severity` is a foreign key into
`drugref.severity_kind` (so `ForeignKeyViolation`), `relationship` one into `ci_axis`,
while `evidence_grade` and `ruling` are still db/029 CHECKs (so `CheckViolation`).
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

    `target_table` DISCRIMINATES the two subject fields, `object_uuid` and
    `relationship` -- not `reviewed_by`/`reviewed_against`, which mean the same thing on
    every arm. Python cannot say so here:
    on a `curated_interaction` row `object_uuid` is a substance_class UUID and
    `relationship` is present; on a `curated_condition` row it is a condition UUID and
    `relationship` is None. Two disjoint namespaces in one uuid field. Deliberately NOT
    enforced in a __post_init__, for this module's opening rule -- such a check would
    restate the view's arm labels in Python, and a third UNION arm in a later migration
    would then make `drugref status` refuse a legitimate row.

    THE THIRD ARM ARRIVED (db/035, issue #90) AND THAT PARAGRAPH'S LAST SENTENCE IS WHY
    NOTHING HERE HAD TO CHANGE except a field: `curated_class_interaction`'s subject is
    a CLASS, so it lands in `subject_class` while `subject_moiety` is None -- and the
    reverse for the two older arms. Both subject fields are therefore OPTIONAL, and
    `target_table` remains the only field that always says which shape a row is. A
    consumer filtering on either subject column silently drops the other arms: filter
    on `target_table`.
    """
    target_table: str
    subject_moiety: uuid.UUID | None
    object_uuid: uuid.UUID
    relationship: str | None
    reviewed_by: str
    reviewed_against: str
    subject_class: uuid.UUID | None

    @property
    def subject(self) -> uuid.UUID | None:
        """THE SUBJECT, whichever column this arm files it in.

        `target_table` says which SHAPE a row is; this says what the row is ABOUT,
        without a caller having to know the arm labels to find out. Added because the
        view's only consumer (`drugref status`) read `subject_moiety` unconditionally
        and so printed "None" for every class-grain orphan -- the detector reported
        that a judgement was orphaned without saying which one.

        DELIBERATELY NO ARM LABELS HERE, which is what keeps the class docstring's
        open-to-extension argument true: a fourth UNION arm filing its subject in
        either existing column is served by this property unchanged. That argument is
        sound for the DISCRIMINATOR; it is not a reason to leave every consumer
        re-deriving the subject, which is how the "None" reached an operator.

        Returns None only if some future arm files a subject in NEITHER column -- no
        curated judgement in this schema can, since each of the three arms hardcodes a
        literal NULL in exactly the column it does not use. A caller seeing None is
        looking at a view whose shape changed, and an empty render is the right
        operator signal for that: this module does not abort a status run over one row
        (see `unresolved_targets`).
        """
        return (self.subject_moiety if self.subject_moiety is not None
                else self.subject_class)


# THE ONE COLUMN LIST, and it is one on purpose. It was two -- this tuple's contents
# spelled out in the SELECT below, and UnresolvedTarget's field list above -- kept in
# step by nothing but sitting a few lines apart. POSITIONALLY, `subject_moiety`,
# `object_uuid` and (since db/035) `subject_class` are all uuid, and `target_table`,
# `relationship`, `reviewed_by`/`reviewed_against` are all text, so four text columns
# and three uuid ones admit 4! x 3! = 144 type-compatible orderings, of which one is
# right; transposing any two of a kind builds a WELL-TYPED WRONG record that no
# annotation and no arity check can see. The count grew with the arm, which is the
# argument getting STRONGER rather than the comment going stale: db/035 added a third
# uuid column and tripled the ways to be wrong. Binding by NAME removes the failure
# mode rather than testing for it, and `strict=True` catches a column the view gained
# or lost.
# EXPORTED for `cli.py`'s migration guard (issue 122): the guard probes the relation
# this read names, rather than carrying a second spelling that a rename would miss.
UNRESOLVED_VIEW = "drugref.curated_target_unresolved"

_UNRESOLVED_COLUMNS = ("target_table", "subject_moiety", "object_uuid",
                       "relationship", "reviewed_by", "reviewed_against",
                       # db/035's trailing add, and the reason this list being ONE
                       # list paid off: adding the class grain's subject here is the
                       # whole Python change, because the SELECT and the record are
                       # both built from it.
                       "subject_class")


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
    rows against FOUR projections at once (`class_contraindication`, both
    `moiety_condition_*` tables, and since db/035 `class_pair_contraindication`), and
    adding a source column would mean yet another migration -- db/035 re-issued this
    view and deliberately did not, because the answer is unchanged: a source column
    here would have to name FOUR sources per row. That makes this a whole-database
    question rather than a per-run one, which is why `drugref status` is its consumer
    rather than an ingest summary.

    LIVE ROWS ONLY -- the view's own `superseded_by IS NULL`. A corrected judgement's
    predecessor still names the old candidate, and reporting it would make every
    correction look like breakage.

    ORDERED TOTALLY, on all FIVE key columns rather than the first two. The expected
    shape of a rebuild orphan is SEVERAL rows sharing a subject -- a re-key drops every
    `class_contraindication` row for that moiety at once -- and those tie on
    (target_table, subject_moiety), which left Postgres free to return them in any
    order. `drugref status` would print a different ordering run to run, and any test
    asserting more than one row would flake.
    """
    return [UnresolvedTarget(**dict(zip(_UNRESOLVED_COLUMNS, row, strict=True)))
            for row in conn.execute(
                f"SELECT {', '.join(_UNRESOLVED_COLUMNS)} "
                f"FROM {UNRESOLVED_VIEW} "
                # subject_class LAST, and it is not decoration: on the class-grain arm
                # (db/035) `subject_moiety` is NULL for EVERY row, so within that arm
                # -- where `target_table` is constant and therefore sorts nothing --
                # the first EFFECTIVE key stops discriminating entirely, and two class
                # rules sharing an object and an axis would tie on all four original
                # columns. Same flake this ORDER BY was widened once before to prevent.
                "ORDER BY target_table, subject_moiety, object_uuid, "
                "relationship, subject_class").fetchall()]


@dataclass(frozen=True)
class ClassGrainCounts:
    """The numbers `drugref status`'s fifth block prints (db/035; 111 added the
    denominator, 115 named it).

    ONE RECORD RATHER THAN FOUR RETURN VALUES, so a caller cannot silently transpose
    two ints -- the same reason `UnresolvedTarget` above binds by name. Three of the
    four are counts of things that should be ZERO on a healthy database, and they are
    three DIFFERENT failures, so the block says them in three different voices.

    `rules_total` IS THE ONE THAT IS NOT A FAULT COUNT, and it is here because the
    others are useless without it (issue 111). They report only on rules that EXIST, so
    an ONCHIGH re-ingest whose parser yields nothing -- upstream format change,
    truncated download, resolver regression -- empties the tier and silences all three
    at once. The block then renders byte-identically to a healthy, fully-curated
    registry, while `loaded_release` still shows ONCHIGH loaded and the command still
    exits 0. A count with a denominator distinguishes "healthy" from "the detector can
    see nothing"; a bare zero cannot.

    IT DENOMINATES `ungraded` AND `dead`, AND NOTHING ELSE (issue 115, which is why it
    is no longer called `total`). Those two are filters over `class_pair_rule_reach`,
    the same RULE tier `rules_total` counts, so `ungraded <= rules_total` and
    `dead <= rules_total` hold by construction. **`disagreements` counts PAIRS, not
    rules** -- rows in `curated_grain_disagreement`, whose grain is the rule pair over
    `curated_ddi_pair`'s two-grain expansion -- so it is not bounded by `rules_total`
    and never was. One class rule can expand to ~2,263 pairs (db/035), so
    `ClassGrainCounts(rules_total=7, ..., disagreements=2263)` is the EXPECTED shape
    once class-grain content ships, and the obvious `{disagreements} of {rules_total}`
    line a maintainer would write next to the one above it would be wrong by two orders
    of magnitude. The old name made that division read natural; this one makes it read
    wrong, which is the entire point of the rename. (Spelled `rules_total=9` and
    `{total}` until the review of PR #119: the illustration used the very figure db/038
    § 3 was correcting in the same commit, and quoted the removed field name in the line
    arguing the new one reads wrong.)

    `ungraded` AND `dead` ARE DISJOINT, which the field names do not say and a reader
    should not have to reconstruct: `gap_uncurated_class_interaction_rule` omits a rule
    reaching no pair via `HAVING max(max_pair_count) > 0` (#36 -- a review gate must
    only ask what an answer could change), so a dead rule is never also counted
    ungraded.
    """
    rules_total: int
    ungraded: int
    dead: int
    disagreements: int


# DISTINCT ON THE THREE NATURAL-KEY COLUMNS, not count(*): `class_pair_rule_reach`
# inherits the candidate tier's primary key, which includes `source`, so one clinical
# rule asserted by two authorities is two rows there and count(*) would report it twice.
# `gap_uncurated_class_interaction_rule` already groups for this reason, so the other
# two numbers have to as well or the block's own lines disagree about what a rule is --
# db/018's "one quantity stated twice is a quantity that will disagree", on the operator
# surface instead of in the schema.
#
# ONE FORMAT STRING, TWO CALLERS. The denominator (issue 111) and the dead count are
# the same query over the same population differing only by a WHERE, and writing them
# out twice is how they would drift apart. `{where}` is interpolated from the two
# literals below and never from anything reaching this module from outside.
#
# `shared_effective_member_count` IS NAMED TO WIDEN THE GUARD, not because the count
# needs it (PR #113 review). cli.py's rule -- "a migration widening a view a guarded
# block reads must widen the guard in the same commit" -- did not reach db/037, which
# corrects this view's ARITHMETIC while every name read here still resolves under
# db/035. So the guard stayed quiet on a db/035-or-036 database and the block printed
# counts from the old, overstated `max_pair_count`, under-reporting `dead` exactly
# where db/037 section 1 exists to help. Naming a db/037 column makes the existing
# UndefinedColumn arm cover that case.
#
# IT CANNOT CHANGE THE COUNT: every input to this view's arithmetic is a function of
# (subject_class_uuid, object_class_uuid, relationship) alone -- `cpc.source` and
# `cpc.ingest_run` feed none of it -- so the two rows one rule asserted by two
# authorities produces carry identical values here. Tested rather than argued, both
# halves, in tests/test_class_grain_detectors.py.
# THE VIEW NAMES, EXPORTED, so the migration guards in `cli.py` and `cli_status.py`
# probe the relations these reads actually name rather than a hand-copied second
# spelling of them (issue 122). One home per name: a rename that missed the guard would
# leave it reporting a healthy database's view permanently absent.
CLASS_GRAIN_VIEWS = ("drugref.class_pair_rule_reach",
                     "drugref.gap_uncurated_class_interaction_rule",
                     "drugref.curated_grain_disagreement")

_RULE_COUNT = ("SELECT count(*) FROM (SELECT DISTINCT subject_class_uuid, "
               "object_class_uuid, relationship, shared_effective_member_count "
               f"FROM {CLASS_GRAIN_VIEWS[0]}" "{where}) z")


def class_grain_counts(conn: psycopg.Connection) -> ClassGrainCounts:
    """Read the class grain's detectors and the denominator they need (db/035, 111).

    IN THIS MODULE RATHER THAN IN `cli.py`, for the rule cli.py's own module docstring
    states and `unresolved_targets` above already obeys: a handler must not embed SQL
    against curated, append-only tables, because the sweep that finds readers works
    through `pg_rewrite` and cannot see a query living in a Python string. Two of these
    views are derived from `curated_class_interaction`, so the read belongs here.
    It also keeps cli.py under CLAUDE.md rule 4's size cap, which it had just breached.

    THE DENOMINATOR IS TAKEN FROM `class_pair_rule_reach`, NOT from
    `class_pair_contraindication`, even though the tier is what it counts. The view
    reaches the tier through INNER joins to `ci_axis` and `substance_class` only, both
    of which the tier's own foreign keys guarantee, and every count it adds is a LEFT
    join -- so it drops nothing and is 1:1 with the tier by construction. Reading the
    tier directly would be a second, differently-written statement of one population,
    and it is the NUMERATORS' population that matters here: a denominator that could
    disagree with the numbers beside it is worse than no denominator at all.

    RAISES psycopg's UndefinedTable UNCAUGHT on a database predating db/035. Converting
    that into an operator sentence is the CALLER's job, exactly as it is for
    `unresolved_targets` -- this module owns the read, cli.py owns the voice.
    """
    rules_total = conn.execute(_RULE_COUNT.format(where="")).fetchone()[0]
    ungraded = conn.execute(
        f"SELECT count(*) FROM {CLASS_GRAIN_VIEWS[1]}").fetchone()[0]
    dead = conn.execute(
        _RULE_COUNT.format(where=" WHERE max_pair_count = 0")).fetchone()[0]
    disagreements = conn.execute(
        f"SELECT count(*) FROM {CLASS_GRAIN_VIEWS[2]}").fetchone()[0]
    return ClassGrainCounts(rules_total=rules_total, ungraded=ungraded, dead=dead,
                            disagreements=disagreements)
