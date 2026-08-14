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

from drugref import curated_read, interactions
from tests.test_class_grain_detectors import _a_pair_graded_by_both_grains
from tests.test_class_subject_read_path import _a_graded_class_rule


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
    with severity equal, only the tie-break can decide, so a view that dropped it would
    return an arbitrary row here and flake rather than fail.
    """
    subject, partner, _sc, _oc = _a_pair_graded_by_both_grains(
        conn, ingest_run_id, moiety_severity="major", class_severity="major")
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

    `pg_prepare`-free and grep-free: the module's SQL is executed and `pg_depend` is
    asked which relation the resulting plan touched -- rather than matching a string
    against the source, which the module's own DOCSTRING would satisfy, since it quotes
    the precedence rule as prose in order to explain it.
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
