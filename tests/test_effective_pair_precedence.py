"""`curated_ddi_pair_effective` -- db/035's precedence, applied rather than described
(`db/037`, issue 110). DB-gated.

WHAT ISSUE 110 FOUND. db/035 described the defect it was fixing as: *"A client doing
`SELECT severity ... LIMIT 1` got an arbitrary answer, AND WHICHEVER IT TOOK MIGHT BE
THE LOWER ONE."* What shipped was a `severity_rank` COLUMN. `curated_ddi_pair` still had
no `ORDER BY`, there was no wrapper view, and `grep -rn "curated_ddi_pair\\|severity_rank"
src/drugref/` found only comment mentions -- so **drugref never applied its own
precedence, and no test could regress it**. A client that did not change its query was
affected exactly as before. The migration consolidated the ORDINAL and left the ORDERING
RULE in prose, which is the same anti-pattern `severity_kind` was created to fix, one
level up.

TWO THINGS ARE PINNED HERE and they fail differently. The VIEW is the rule (one row per
pair, the more severe grade, the moiety grain breaking ties, NULLS FIRST); `curated_read.
effective_grades_for` is its CALLER, because a view with no consumer is half a feature --
this project has shipped that twice and written the rule down after the second.
"""
import uuid

import pytest

from drugref import curated_read, curation, interactions
from tests.test_class_grain_detectors import _a_pair_graded_by_both_grains
from tests.test_class_subject_read_path import (_a_graded_class_rule, _a_moiety,
                                                _file_member)
from tests.test_curated_overlay import _a_class


def _effective(conn, subject, partner):
    """The one row the view selects for a pair, as (severity, rule_grain)."""
    return conn.execute(
        "SELECT severity, rule_grain FROM drugref.curated_ddi_pair_effective "
        "WHERE subject_moiety = %s AND partner_moiety = %s",
        (subject, partner)).fetchall()


# ============================================================================
# 1. the rule: one row per pair, most severe first, moiety grain breaking ties
# ============================================================================


def test_one_row_per_subject_partner_relationship(conn, ingest_run_id):
    """THE HEADLINE. `curated_ddi_pair` returns BOTH grades when the grains disagree --
    deliberately, because dropping one would make it state less than it knows -- and a
    client taking `LIMIT 1` over it got whichever the planner happened to emit."""
    subject, partner, _sc, _oc = _a_pair_graded_by_both_grains(
        conn, ingest_run_id, moiety_severity="moderate",
        class_severity="contraindicated")

    both = conn.execute(
        "SELECT count(*) FROM drugref.curated_ddi_pair "
        "WHERE subject_moiety = %s AND partner_moiety = %s",
        (subject, partner)).fetchone()[0]
    assert both == 2, "the premise: two grades for one pair, which is what needs deciding"
    assert len(_effective(conn, subject, partner)) == 1


@pytest.mark.parametrize("moiety_severity,class_severity,winner", [
    ("moderate", "contraindicated", "contraindicated"),
    ("contraindicated", "moderate", "contraindicated"),
    ("minor", "moderate", "moderate"),
    ("moderate", "minor", "moderate"),
])
def test_the_more_severe_grade_wins_whichever_grain_holds_it(
        conn, ingest_run_id, moiety_severity, class_severity, winner):
    """SEVERITY FIRST, both ways round, because the tie-break is not what decides this.

    Driving only one direction would leave the view passing if it had been written to
    prefer the moiety grain OUTRIGHT rather than as a tie-break -- and that is not a
    hypothetical mis-ordering, it is precisely the ordering a reader gets by putting
    the two ORDER BY keys the other way round.

    `minor` vs `moderate` is the second case for db/035's own reason: `ORDER BY
    severity` (text) sorts 'minor' ABOVE 'moderate', so a view that forgot to use the
    rank would pass the contraindicated cases and fail here.
    """
    subject, partner, _sc, _oc = _a_pair_graded_by_both_grains(
        conn, ingest_run_id, moiety_severity=moiety_severity,
        class_severity=class_severity)
    assert _effective(conn, subject, partner) == [
        (winner, "moiety_rule" if moiety_severity == winner else "class_rule")]


def test_the_moiety_grain_breaks_a_tie(conn, ingest_run_id):
    """EQUAL SEVERITY, and the rule naming an actual drug wins -- it carries better
    mechanism and management text than one naming the drug's whole class.

    This is the assertion that makes the two ORDER BY keys distinguishable from one:
    with severity equal, only the tie-break can decide.

    `class_source='MED-RT'` IS WHAT MAKES THAT TRUE, and without it this test was
    over-determined -- PR #113's review measured it. The fixture's ordinary pairing is
    'MED-RT' on the moiety grain and 'ONCHIGH' on the class grain, and 'MED-RT' <
    'ONCHIGH'; `candidate_source` is the FIRST determinism tie-break AFTER the grain key,
    so the moiety row was winning twice over. Deleting `(rule_grain = 'moiety_rule')
    DESC` from db/037 entirely left this test -- and the whole suite -- green, which is
    the opposite of what the paragraph above claimed. Sourcing BOTH grains from 'MED-RT'
    ties `candidate_source` and hands the decision to `via_subject_class`, which is
    non-NULL on the class row and NULL on the moiety row and therefore favours the CLASS
    row under ASC. The moiety row now wins ONLY by the grain key, and deleting that key
    makes this test fail -- which is the point of it.
    """
    subject, partner, _sc, _oc = _a_pair_graded_by_both_grains(
        conn, ingest_run_id, moiety_severity="major", class_severity="major",
        class_source="MED-RT")
    assert _effective(conn, subject, partner) == [("major", "moiety_rule")]


def test_a_pair_graded_by_one_grain_only_is_unchanged(conn, ingest_run_id):
    """THE ANTI-VACUITY CONTROL. Every case above has two rows to choose between, so all
    of them would pass a view that returned nothing at all for an undisputed pair.

    An overwhelming majority of real rows are this shape -- 255 curated pairs on the
    reference database and not one of them graded by both grains -- so a view that only
    worked on the disputed case would look fine on every count anybody publishes.
    """
    _sc, _oc, subjects, objects = _a_graded_class_rule(
        conn, ingest_run_id, subject_code="N0000007100", object_code="N0000007200",
        subject_members=[("TESTUNIIE1", "s")], object_members=[("TESTUNIIE2", "o")],
        severity="moderate")
    assert _effective(conn, subjects[0], objects[0]) == [("moderate", "class_rule")]


def test_provenance_duplication_collapses_without_changing_the_grade(conn,
                                                                     ingest_run_id):
    """TWO AUTHORITIES ASSERTING ONE RULE IS ONE GRADE, not two, and this is the case
    that actually fires on today's data.

    `curated_ddi_pair` carries `candidate_source`, so a rule both MED-RT and ONCHIGH
    assert produces one row PER SOURCE with an IDENTICAL grade. Measured on the
    reference database: the effective view collapses **255 rows to 213**, and all 42
    doubled pairs are explained by `candidate_source` alone -- zero differ in severity,
    zero in `via_class`, zero in `rule_grain`. So the view is not a no-op today even
    with no class-grain content, and what it removes is pure provenance duplication.

    THE GRADE MUST BE UNTOUCHED, which is the half worth asserting: collapsing rows is
    only safe while the rows being collapsed agree, and `curated_ddi_pair` remains the
    place to see which authorities asserted the rule.
    """
    subject_class, object_class, subjects, objects = _a_graded_class_rule(
        conn, ingest_run_id, subject_code="N0000007300", object_code="N0000007400",
        subject_members=[("TESTUNIIE3", "s")], object_members=[("TESTUNIIE4", "o")],
        source="ONCHIGH", severity="major")
    interactions.add_class_pair_contraindication(
        conn, subject_class, object_class, "CI_MoA", "MED-RT", ingest_run_id)

    sources = conn.execute(
        "SELECT count(DISTINCT candidate_source) FROM drugref.curated_ddi_pair "
        "WHERE subject_moiety = %s AND partner_moiety = %s",
        (subjects[0], objects[0])).fetchone()[0]
    assert sources == 2, "the premise: one rule, two authorities, two rows"
    assert _effective(conn, subjects[0], objects[0]) == [("major", "class_rule")]


def test_the_selection_is_deterministic(conn, ingest_run_id):
    """DISTINCT ON picks the SAME row every time, including where the two precedence
    keys tie.

    The tie-break after `severity_rank` and the grain is determinism, not a clinical
    preference -- but an unstable one is a flake that appears in whichever test runs
    when the planner changes its mind, not here. Asserted by reading twice rather than
    by inspecting the ORDER BY, so it holds for ties nobody has thought of.
    """
    subject_class, object_class, subjects, objects = _a_graded_class_rule(
        conn, ingest_run_id, subject_code="N0000007500", object_code="N0000007600",
        subject_members=[("TESTUNIIE5", "s")], object_members=[("TESTUNIIE6", "o")],
        source="ONCHIGH", severity="minor")
    interactions.add_class_pair_contraindication(
        conn, subject_class, object_class, "CI_MoA", "MED-RT", ingest_run_id)

    first = conn.execute(
        "SELECT * FROM drugref.curated_ddi_pair_effective WHERE subject_moiety = %s",
        (subjects[0],)).fetchall()
    second = conn.execute(
        "SELECT * FROM drugref.curated_ddi_pair_effective WHERE subject_moiety = %s",
        (subjects[0],)).fetchall()
    assert first == second


# ============================================================================
# 2. NULLS FIRST -- a latent defect in a stated mitigation, pinned by mutation
# ============================================================================


def test_an_unrankable_severity_outranks_contraindicated(conn, ingest_run_id):
    """`NULLS FIRST`, and this is issue 110's second half.

    Postgres sorts `ORDER BY x ASC` with NULLs LAST, and `severity_rank` 1 is MOST
    severe -- so with the default an unrankable severity sorted BELOW `minor` and a
    `LIMIT 1` client would never see it. db/035 argued its defensive LEFT JOIN to
    `severity_kind` "makes it harmless if it ever became reachable again": it keeps the
    row but out-ranks it by everything, which is UNDER-warning, and under-warning is the
    harm direction on this path.

    UNREACHABLE ON A HEALTHY DATABASE, which is why it is driven by mutation on
    controlled input -- this project's rule for a branch the release cannot exercise.
    Both halves of `curated_ddi_pair` filter `AND applies`, the completeness CHECK forces
    `applies => severity IS NOT NULL`, and `severity` is a FOREIGN KEY into
    `severity_kind`, so `severity_rank` is always populated. Dropping that ONE foreign
    key inside the rolled-back fixture transaction reaches the state the mitigation
    claims to handle, without weakening anything a committed database relies on.

    The assertion is that the unrankable row WINS, not merely that it appears: appearing
    is what db/035 already achieved, and being outranked by everything is the defect.

    THE FOREIGN KEY IS DROPPED BEFORE ANY CURATED ROW EXISTS, not after. The overlay
    floor forbids UPDATE on these tables outright -- a correction is INSERT-then-
    supersede -- and an `ALTER TABLE` after the fixture has written is refused anyway
    ("cannot ALTER TABLE ... because it has pending trigger events", the deferred
    single-live check). Grading the class rule `unrankable` from the start reaches the
    same state without fighting either guard.
    """
    conn.execute("ALTER TABLE drugref.curated_class_interaction "
                 "DROP CONSTRAINT curated_class_interaction_severity")
    subject, partner, _sc, _oc = _a_pair_graded_by_both_grains(
        conn, ingest_run_id, moiety_severity="minor", class_severity="unrankable")

    rows = conn.execute(
        "SELECT severity, severity_rank FROM drugref.curated_ddi_pair "
        "WHERE subject_moiety = %s AND partner_moiety = %s ORDER BY 1",
        (subject, partner)).fetchall()
    assert ("unrankable", None) in rows, (
        "the premise: an unrankable severity reaches curated_ddi_pair with a NULL rank")

    assert _effective(conn, subject, partner) == [("unrankable", "class_rule")], (
        "an unrankable severity must outrank a real one -- under NULLS LAST it sorts "
        "below `minor` and a LIMIT 1 client never sees it")


# ============================================================================
# 3. the caller -- a view with no consumer is half a feature
# ============================================================================


def test_effective_grades_for_returns_the_selected_grade(conn, ingest_run_id):
    """`curated_read.effective_grades_for` is what makes `severity_rank` read by src/
    at all -- issue 110's own measurement was that nothing did."""
    subject, partner, _sc, _oc = _a_pair_graded_by_both_grains(
        conn, ingest_run_id, moiety_severity="moderate",
        class_severity="contraindicated")

    grades = curated_read.effective_grades_for(conn, subject)
    assert [(g.partner_moiety, g.severity, g.severity_rank, g.rule_grain)
            for g in grades] == [(partner, "contraindicated", 1, "class_rule")]


def test_effective_grades_for_is_most_severe_first_and_totally_ordered(conn,
                                                                       ingest_run_id):
    """A LIST of different pairs, ordered so the most concerning partner reads first.

    Distinct from the view's precedence, which chooses between two rows about ONE pair.
    Both mention `severity_rank` and they are different rules; this one is presentation.
    """
    _sc, _oc, subjects, objects = _a_graded_class_rule(
        conn, ingest_run_id, subject_code="N0000007700", object_code="N0000007800",
        subject_members=[("TESTUNIIE7", "s")],
        object_members=[("TESTUNIIE8", "mild"), ("TESTUNIIE9", "severe")],
        severity="minor")
    # A second, more severe rule over the same subject, so the list has something to
    # order. Different object class, so the two rules are independent.
    _a_graded_class_rule(
        conn, ingest_run_id, subject_code="N0000007700", object_code="N0000007900",
        subject_members=[("TESTUNIIE7", "s")], object_members=[("TESTUNIIF1", "worst")],
        severity="contraindicated")

    grades = curated_read.effective_grades_for(conn, subjects[0])
    ranks = [g.severity_rank for g in grades]
    assert ranks == sorted(ranks), f"most severe first, got {ranks}"
    assert grades[0].severity == "contraindicated"
    # TOTALLY ordered: the two `minor` partners tie on rank, so only the secondary key
    # keeps the list stable between runs.
    minor = [g.partner_moiety for g in grades if g.severity == "minor"]
    assert minor == sorted(minor)
    assert set(minor) == set(objects)


def test_effective_grades_for_returns_empty_for_an_ungraded_moiety(conn):
    """EMPTY IS ORDINARY, not an error: most moieties carry no curated grade, and the
    overlay is deliberately small. Pinned so a future change cannot start raising."""
    assert curated_read.effective_grades_for(conn, uuid.UUID(int=0)) == []


def test_the_read_is_directional(conn, ingest_run_id):
    """db/006's convention survives the wrapper. A consumer asking "do X and Y
    interact" queries BOTH directions -- `effective_grades_for` deliberately does not
    fold the mirror in, and a caller who assumes it does gets silence rather than an
    error, which is why this is pinned rather than left to the docstring."""
    subject, partner, _sc, _oc = _a_pair_graded_by_both_grains(
        conn, ingest_run_id, moiety_severity="major", class_severity="major")
    assert [g.partner_moiety for g in
            curated_read.effective_grades_for(conn, subject)] == [partner]
    assert curated_read.effective_grades_for(conn, partner) == []


def test_the_caller_reads_the_view_that_applies_the_precedence(conn):
    """It must read `curated_ddi_pair_effective`, NEVER `curated_ddi_pair` plus its own
    ORDER BY -- asked of the CATALOGUE, not of the source text.

    The rule that decides between two grains has to live in ONE place, and the view is
    that place precisely so a consumer querying from any language gets it. A caller that
    selected from `curated_ddi_pair` and re-applied the ordering in Python would pass
    every behavioural test in this file while putting db/035's own defect back: one rule
    in two homes, SQL and Python free to drift, and nothing saying which is
    authoritative.

    TWO DIFFERENT INSTRUMENTS, and the docstring used to describe a third that is not
    here. The catalogue half asks `pg_rewrite`/`pg_depend` what the VIEW's rule
    references -- a dependency question, not a plan question, and `pg_depend` could not
    answer a plan question if asked. The two assertions after it ARE substring checks
    against the module's SQL constant, which is the honest way to say that the caller
    selects from the view and does not re-apply the precedence itself; they are narrow
    enough to be safe (the constant is assembled from `_COLUMNS`, so neither depends on
    hand-written column text) and they are not disclaimed here any more. An earlier
    version of this paragraph claimed the module's SQL was executed and a plan inspected,
    and called itself grep-free twelve lines above two greps.
    """
    reads = {r[0] for r in conn.execute(
        "SELECT DISTINCT c.relname FROM pg_depend d "
        "JOIN pg_rewrite rw ON rw.oid = d.objid "
        "JOIN pg_class dep ON dep.oid = rw.ev_class "
        "JOIN pg_class c ON c.oid = d.refobjid "
        "JOIN pg_namespace n ON n.oid = c.relnamespace "
        "WHERE d.classid = 'pg_rewrite'::regclass AND n.nspname = 'drugref' "
        "  AND dep.relname = 'curated_ddi_pair_effective' "
        "  AND c.relname <> 'curated_ddi_pair_effective'").fetchall()}
    assert reads == {"curated_ddi_pair"}, (
        "the effective view is a thin wrapper over curated_ddi_pair and nothing else")
    assert "curated_ddi_pair_effective" in curated_read._EFFECTIVE_FOR_SUBJECT
    assert "rule_grain = 'moiety_rule'" not in curated_read._EFFECTIVE_FOR_SUBJECT


def test_every_field_of_a_graded_pair_carries_its_own_column(conn, ingest_run_id):
    """ALL NINE FIELDS, against nine distinguishable values -- PR #113's review finding.

    `GradedPair` was built by positional splat from a hand-written SELECT, and only four
    of its nine fields were asserted anywhere. Seven of the nine are text or nullable
    text, so a transposition builds a WELL-TYPED WRONG record: the review swapped
    `mechanism` and `management` in the SELECT and the ENTIRE SUITE STAYED GREEN, while
    drugref handed clinical management advice to a client labelled "mechanism".
    `relationship`/`evidence_grade` and `rule_grain`/`signature_status` are the same
    shape.

    `curated_read._COLUMNS` now binds by keyword, which removes the failure mode rather
    than testing for it -- but the column list still has to name the RIGHT columns, and
    that is what this pins. EVERY VALUE IS DELIBERATELY DISTINCT, including the two the
    fixture would otherwise default to the same word, so no assertion here can pass by
    coincidence.
    """
    _sc, _oc, subjects, objects = _a_graded_class_rule(
        conn, ingest_run_id, subject_code="N0000008100", object_code="N0000008200",
        subject_members=[("TESTUNIIG1", "subject-drug")],
        object_members=[("TESTUNIIG2", "partner-drug")],
        severity="major", evidence_grade="theoretical",
        mechanism="additive QT prolongation",
        management="avoid; if unavoidable, ECG before and 4h after the second dose")

    grades = curated_read.effective_grades_for(conn, subjects[0])
    assert len(grades) == 1
    got = grades[0]
    assert got.partner_moiety == objects[0]
    assert got.relationship == "CI_MoA"
    assert got.severity == "major"
    assert got.severity_rank == 2
    assert got.evidence_grade == "theoretical"
    assert got.mechanism == "additive QT prolongation"
    assert got.management == (
        "avoid; if unavoidable, ECG before and 4h after the second dose")
    assert got.rule_grain == "class_rule"
    assert got.signature_status == "unsigned"


def test_the_callers_own_order_by_puts_an_unrankable_severity_first(conn,
                                                                    ingest_run_id):
    """`NULLS FIRST` ON THE CALLER'S LIST, not just inside the view -- and they are two
    different ORDER BYs that both mention `severity_rank`.

    Section 2 above drives the VIEW's precedence, which chooses between two rows about
    ONE pair. This drives `effective_grades_for`'s own ordering, which ranks DIFFERENT
    pairs against each other so a client taking the head of the list sees the most
    concerning partner first. PR #113's review measured that the second one was pinned
    by nothing: removing `NULLS FIRST` from `curated_read._EFFECTIVE_FOR_SUBJECT` left
    fifteen tests green. The view would have sorted the unrankable row first and the
    Python caller would have re-buried it one layer up, which is the whole harm
    direction argument defeated by the last hop.

    Same mutation as section 2, for the same reason -- both halves of `curated_ddi_pair`
    filter `AND applies`, the completeness CHECK forces `applies => severity IS NOT
    NULL`, and `severity` is a FOREIGN KEY into `severity_kind` -- so the state is
    reached by dropping that one constraint inside the rolled-back fixture transaction.
    """
    conn.execute("ALTER TABLE drugref.curated_class_interaction "
                 "DROP CONSTRAINT curated_class_interaction_severity")
    _sc, _oc, subjects, objects = _a_graded_class_rule(
        conn, ingest_run_id, subject_code="N0000008300", object_code="N0000008400",
        subject_members=[("TESTUNIIG3", "subject-drug")],
        object_members=[("TESTUNIIG4", "unrankable-partner")],
        severity="unrankable")
    # A genuinely rank-1 partner on a second rule, so the list has something for the
    # unrankable row to outrank. Without it the assertion is satisfied by a one-element
    # list and pins nothing at all.
    _a_graded_class_rule(
        conn, ingest_run_id, subject_code="N0000008300", object_code="N0000008500",
        subject_members=[("TESTUNIIG3", "subject-drug")],
        object_members=[("TESTUNIIG5", "worst-partner")],
        severity="contraindicated")

    grades = curated_read.effective_grades_for(conn, subjects[0])
    assert [(g.severity, g.severity_rank) for g in grades] == [
        ("unrankable", None), ("contraindicated", 1)], (
        "an unrankable severity must head the caller's list too -- under Postgres's "
        "default NULLS LAST it sorts below every real grade and a client reading the "
        "head never sees it")
    assert grades[0].partner_moiety == objects[0]


# ============================================================================
# 4. the determinism tail -- it has to close BOTH grains (PR #113 review)
# ============================================================================


def _one_pair_graded_by_two_class_rules(conn, ingest_run_id):
    """ONE drug pair reached by TWO class-grain rules differing only in subject class.

    The shape the old tail could not resolve: one subject drug filed under two subject
    classes (MED-RT files one drug under many classes, which section 1 of db/037 argues
    at length is ordinary), both rules naming the SAME object class on the SAME axis
    with the SAME severity and the SAME source, curated in ONE transaction so
    `reviewed_at` -- which defaults to now(), the TRANSACTION timestamp -- is identical
    too. Every key in the tail ties except `via_subject_class`.

    WRITTEN LARGER-UUID-FIRST ON PURPOSE. `via_subject_class` orders ASC, so the
    expected winner is the SMALLER class uuid -- and inserting it second means heap
    order disagrees with the answer. A view that dropped the key would have to beat
    physical row order to pass.

    Returns (subject_moiety, partner_moiety, smaller_class, larger_class).
    """
    first = _a_class(conn, ingest_run_id, code="N0000008600", name="Class A [MoA]")
    second = _a_class(conn, ingest_run_id, code="N0000008700", name="Class B [MoA]")
    smaller, larger = sorted([first, second], key=str)
    object_class = _a_class(conn, ingest_run_id, code="N0000008800",
                            name="Object class [MoA]")

    subject = _a_moiety(conn, ingest_run_id, "TESTUNIIG6", "the shared subject drug")
    partner = _a_moiety(conn, ingest_run_id, "TESTUNIIG7", "the partner drug")
    _file_member(conn, subject, smaller, ingest_run_id)
    _file_member(conn, subject, larger, ingest_run_id)
    _file_member(conn, partner, object_class, ingest_run_id)

    for subject_class in (larger, smaller):
        interactions.add_class_pair_contraindication(
            conn, subject_class, object_class, "CI_MoA", "ONCHIGH", ingest_run_id)
        curation.record_class_interaction_judgement(
            conn, subject_class, object_class, "CI_MoA", True, severity="major",
            evidence_grade="established",
            mechanism=f"mechanism via {subject_class}", reviewed_by="test",
            reviewed_against="Phansalkar 2012")
    return subject, partner, smaller, larger


def test_two_class_rules_over_one_pair_resolve_by_subject_class(conn, ingest_run_id):
    """THE HOLE PR #113's REVIEW MEASURED: the tail closed the moiety grain and not the
    class grain.

    A class-grain row is identified by `(via_subject_class, via_class, relationship)` --
    `curated_class_interaction`'s live-unique natural key -- plus `candidate_source` for
    the `class_pair_contraindication` fan-out, and `via_subject_class` was the one
    component the first draft of db/037 left out. Two such rules tied on every key that
    was there, so `DISTINCT ON` followed heap order: which `mechanism` and `management`
    a prescribing client read was decided by physical row order, and a per-source
    rebuild, a `VACUUM FULL` or a dump/restore could flip it. Silent, too -- severity is
    equal, so no detector fires and `curated_grain_disagreement` never sees it, both
    rows being `class_rule`.
    """
    subject, partner, smaller, _larger = _one_pair_graded_by_two_class_rules(
        conn, ingest_run_id)

    both = conn.execute(
        "SELECT count(*) FROM drugref.curated_ddi_pair "
        "WHERE subject_moiety = %s AND partner_moiety = %s",
        (subject, partner)).fetchone()[0]
    assert both == 2, "the premise: two class rules reach one pair, tied on every "\
                      "published precedence key"

    rows = conn.execute(
        "SELECT via_subject_class, mechanism FROM drugref.curated_ddi_pair_effective "
        "WHERE subject_moiety = %s AND partner_moiety = %s",
        (subject, partner)).fetchall()
    assert rows == [(smaller, f"mechanism via {smaller}")]


def test_the_subject_class_key_is_what_selects_it(conn, ingest_run_id):
    """MUTATION: flip that ONE key to DESC and the answer must flip with it.

    The assertion above is compatible with a view that happens to return the smaller
    class for some other reason -- heap order, or a planner choice on this data. Flipping
    `via_subject_class` alone, leaving every other key exactly as shipped, is what
    demonstrates the answer is produced BY that key. An unmutated pin is a claim, not
    evidence, and this project has shipped two of those.

    Safe to run: DDL is transactional in Postgres and the `conn` fixture rolls back.
    """
    subject, partner, smaller, larger = _one_pair_graded_by_two_class_rules(
        conn, ingest_run_id)
    assert conn.execute(
        "SELECT via_subject_class FROM drugref.curated_ddi_pair_effective "
        "WHERE subject_moiety = %s AND partner_moiety = %s",
        (subject, partner)).fetchone()[0] == smaller

    conn.execute("""
        CREATE OR REPLACE VIEW drugref.curated_ddi_pair_effective AS
        SELECT DISTINCT ON (subject_moiety, partner_moiety, relationship) *
        FROM   drugref.curated_ddi_pair
        ORDER  BY subject_moiety, partner_moiety, relationship,
                  severity_rank NULLS FIRST,
                  (rule_grain = 'moiety_rule') DESC,
                  candidate_source, via_subject_class DESC, via_class, member_class,
                  reviewed_at, reviewed_by
    """)
    assert conn.execute(
        "SELECT via_subject_class FROM drugref.curated_ddi_pair_effective "
        "WHERE subject_moiety = %s AND partner_moiety = %s",
        (subject, partner)).fetchone()[0] == larger, (
        "the shipped view's answer is not produced by via_subject_class, so this pin "
        "is not a pin")
