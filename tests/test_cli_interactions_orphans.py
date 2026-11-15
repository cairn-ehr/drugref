"""`drugref interactions` must say when a curated grade has fallen out of its list (#121).

THE DEFECT. Each half of `curated_ddi_pair` reaches a curated grade only through an
INNER JOIN to a rebuildable candidate: the moiety grain through `ddi_candidate_pair` (a
view over `class_contraindication`), the class grain through
`class_pair_contraindication` (db/038). A re-ingest that drops the candidate row leaves
the grade alive in the curated table and gone from the read, so the command printed
`no curated grade` for a pair drugref had judged contraindicated.
`curated_target_unresolved` already detected that state, but only `drugref status` read
it: the operator was told, the person being under-warned was not.

BOTH INTERACTION GRAINS, NOT THE CONDITION ARM: a `curated_condition` ruling is not an
interaction grade. A class-grain orphan is bannered when its expanded subject OR partner
members include an asked drug, or when it expands to nobody at all (who it concerns is
then unknowable, so it is kept). Before db/079 (#255) it was bannered for every drug,
because the member expansion lived only inside `curated_ddi_pair`. DB-gated except the
pure-filter test.
"""
import uuid

import psycopg
import pytest

from drugref import class_grain_read, cli_interactions, curation
from tests.test_class_subject_read_path import _a_graded_class_rule
from tests.test_cli_interactions import (_KNOWN_TO_THE_STUB, _RaisingConn,
                                         _a_graded_pair, _args)
from tests.test_curated_overlay import _a_class


def _orphan(target_table="curated_interaction", subject_moiety=None, subject_class=None):
    """A hand-built record: the filter is pure, so its tests need no database."""
    return curation.UnresolvedTarget(
        target_table=target_table, subject_moiety=subject_moiety,
        object_uuid=uuid.uuid4(), relationship="CI_MoA", reviewed_by="a curator",
        reviewed_against="2026.07.06", subject_class=subject_class)


# ============================================================================
# 1. the pure filter
# ============================================================================


def test_the_filter_keeps_interaction_orphans_that_may_concern_the_asked_subjects():
    """Moiety grain matched on subject; class grain matched on its natural key against
    the class rules the caller found reaching the asked drugs (#255); a condition ruling
    about the same drug is not an interaction grade."""
    x, y, z = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    mine = _orphan(subject_moiety=x)
    theirs = _orphan(subject_moiety=y)
    other = _orphan(subject_moiety=z)
    condition = _orphan(target_table="curated_condition", subject_moiety=x)
    near = _orphan(target_table="curated_class_interaction", subject_class=uuid.uuid4())
    far = _orphan(target_table="curated_class_interaction", subject_class=uuid.uuid4())
    reaching = {(near.subject_class, near.object_uuid, near.relationship)}

    orphans = [mine, theirs, other, condition, near, far]
    assert curation.orphaned_interaction_grades(orphans, {x}, reaching) == [mine, near]
    assert curation.orphaned_interaction_grades(orphans, {x, y}, reaching) == [
        mine, theirs, near]
    assert curation.orphaned_interaction_grades(orphans, set(), set()) == []


# ============================================================================
# 2. the command banners it, and exits EXIT_INCOMPLETE
# ============================================================================


def _orphaned_pair(conn, ingest_run_id):
    """A graded pair whose candidate a re-ingest then dropped: the #121 sequence.
    Only THIS subject's candidates go, so other rows in a test stay healthy."""
    subject, partner = _a_graded_pair(
        conn, ingest_run_id, subject_code="N0000012100", object_code="N0000012101",
        severity="contraindicated", subject_unii="TESTUNI121", partner_unii="TESTUNI122",
        subject_name="the orphaned subject", partner_name="the orphaned partner")
    conn.execute("DELETE FROM drugref.class_contraindication "
                 "WHERE subject_moiety_uuid = %s", (subject,))
    return subject, partner


def test_the_one_drug_form_says_its_list_is_incomplete(conn, ingest_run_id, capsys):
    """THE HEADLINE: `no curated grade` must not stand alone for a judged drug."""
    subject, _ = _orphaned_pair(conn, ingest_run_id)

    assert cli_interactions._handle_interactions(
        conn, _args(subject)) == cli_interactions.EXIT_INCOMPLETE
    out = capsys.readouterr().out
    assert "INCOMPLETE" in out
    assert "curated_target_unresolved" in out
    assert f"{subject} -> class" in out
    assert "CI_MoA" in out


def test_the_pair_form_does_not_claim_an_absent_rule(conn, ingest_run_id, capsys):
    """The pair form's closing sentence ("an ABSENT RULE") is exactly the reassurance
    an orphan makes false, so it must give way to the banner. Asked in BOTH argument
    orders: the orphan's subject is the second drug in one of them."""
    subject, partner = _orphaned_pair(conn, ingest_run_id)

    for a, b in ((subject, partner), (partner, subject)):
        assert cli_interactions._handle_interactions(
            conn, _args(a, b)) == cli_interactions.EXIT_INCOMPLETE
        out = capsys.readouterr().out
        assert "INCOMPLETE" in out
        assert "ABSENT RULE" not in out


def test_a_live_grade_does_not_hide_an_orphan_beside_it(conn, ingest_run_id, capsys):
    """THE FOUND-AND-ORPHANED CASE: a visible "major" must not read as the whole
    answer while a contraindicated grade of the same drug has dropped out. Pins that
    the banner is not tucked inside the `if not found` branch, in both forms."""
    subject, partner = _a_graded_pair(
        conn, ingest_run_id, subject_code="N0000012104", object_code="N0000012105",
        severity="major", subject_unii="TESTUNI125", partner_unii="TESTUNI126",
        subject_name="a subject graded twice", partner_name="its visible partner")
    lost_class = _a_class(conn, ingest_run_id, code="N0000012106", name="a lost class")
    curation.record_interaction_judgement(
        conn, subject, lost_class, "CI_MoA", True, severity="contraindicated",
        evidence_grade="established", reviewed_by="a curator",
        reviewed_against="2026.07.06")  # no candidate row: orphaned from the start

    for args in (_args(subject), _args(subject, partner)):
        assert cli_interactions._handle_interactions(
            conn, args) == cli_interactions.EXIT_INCOMPLETE
        out = capsys.readouterr().out
        assert "major" in out
        assert "INCOMPLETE" in out


def test_a_class_grain_orphan_is_bannered_too(conn, ingest_run_id, capsys):
    """db/038's class half INNER JOINs `class_pair_contraindication`, so a class rule
    whose candidate a re-ingest dropped vanishes for every member pair. The banner
    must say so, and the pair form must not call the pair an ABSENT RULE."""
    subject_class, _o, (s,), (o,) = _a_graded_class_rule(
        conn, ingest_run_id, subject_code="N0000012107", object_code="N0000012108",
        subject_members=[("TESTUNI127", "a class-rule subject")],
        object_members=[("TESTUNI128", "a class-rule partner")],
        severity="contraindicated", evidence_grade="established",
        reviewed_by="a curator", reviewed_against="2026.07.06")
    conn.execute("DELETE FROM drugref.class_pair_contraindication "
                 "WHERE subject_class_uuid = %s", (subject_class,))

    assert cli_interactions._handle_interactions(
        conn, _args(s, o)) == cli_interactions.EXIT_INCOMPLETE
    out = capsys.readouterr().out
    assert "INCOMPLETE" in out
    assert f"members of class {subject_class}" in out
    assert "ABSENT RULE" not in out


def _orphaned_class_rule(conn, ingest_run_id, n, subject_members, object_members):
    """A graded class x class rule whose candidate a re-ingest then dropped (#255).
    `n` keeps each test's class codes apart."""
    subject_class, _o, subjects, objects = _a_graded_class_rule(
        conn, ingest_run_id, subject_code=f"N00000255{n}0",
        object_code=f"N00000255{n}1", subject_members=subject_members,
        object_members=object_members, severity="contraindicated")
    conn.execute("DELETE FROM drugref.class_pair_contraindication "
                 "WHERE subject_class_uuid = %s", (subject_class,))
    return subject_class, subjects, objects


def test_an_orphaned_class_rule_still_expands_to_its_members(conn, ingest_run_id):
    """db/079 §1: `ci_class_pair_subtree` is seeded from the overlay too, so a rule
    whose candidate is gone still names whom it concerned. Without that seed the rule
    reaches nobody and the narrowing has nothing to narrow with -- the partner-side test
    below would still pass, through the reaches-nobody fallback, so this pins it."""
    _c, (s_,), (o,) = _orphaned_class_rule(
        conn, ingest_run_id, 5, [("TESTUN2559", "an orphan's subject")],
        [("TESTUN2560", "an orphan's partner")])

    assert set(conn.execute(
        "SELECT side, moiety_uuid FROM drugref.class_rule_member").fetchall()) == {
        ("subject", s_), ("partner", o)}
    assert class_grain_read.class_rules_reaching(conn, {uuid.uuid4()}) == set()


def test_a_class_grain_orphan_banners_its_partner_side_too(conn, ingest_run_id, capsys):
    """Narrowed on subject OR partner members, as the issue asks: the one-drug form for
    the rule's PARTNER still gets the banner."""
    _c, _s, (o,) = _orphaned_class_rule(
        conn, ingest_run_id, 1, [("TESTUN2551", "a class-rule subject")],
        [("TESTUN2552", "a class-rule partner")])

    assert cli_interactions._handle_interactions(
        conn, _args(o)) == cli_interactions.EXIT_INCOMPLETE
    assert "INCOMPLETE" in capsys.readouterr().out


def test_another_drugs_class_grain_orphan_does_not_banner(conn, ingest_run_id, capsys):
    """THE #255 FIX: a class orphan whose members do not include either asked drug
    leaves a healthy pair's answer complete. Before db/079 this printed INCOMPLETE."""
    _orphaned_class_rule(
        conn, ingest_run_id, 2, [("TESTUN2553", "an orphan's subject")],
        [("TESTUN2554", "an orphan's partner")])
    subject, partner = _a_graded_pair(
        conn, ingest_run_id, subject_code="N0000025560", object_code="N0000025561",
        severity="major", subject_unii="TESTUN2555", partner_unii="TESTUN2556",
        subject_name="an unrelated subject", partner_name="an unrelated partner")

    assert cli_interactions._handle_interactions(conn, _args(subject, partner)) == 0
    assert "INCOMPLETE" not in capsys.readouterr().out


def test_a_class_grain_orphan_reaching_nobody_banners_every_drug(
        conn, ingest_run_id, capsys):
    """A rule whose classes now expand to NO drug cannot be narrowed: who it concerned
    is unknowable from here, so it keeps #121's safe direction and banners for all."""
    _orphaned_class_rule(conn, ingest_run_id, 4, [], [])
    subject, partner = _a_graded_pair(
        conn, ingest_run_id, subject_code="N0000025570", object_code="N0000025571",
        severity="major", subject_unii="TESTUN2557", partner_unii="TESTUN2558",
        subject_name="an unrelated subject", partner_name="an unrelated partner")

    assert cli_interactions._handle_interactions(
        conn, _args(subject, partner)) == cli_interactions.EXIT_INCOMPLETE
    assert "INCOMPLETE" in capsys.readouterr().out


def test_no_banner_when_nothing_is_orphaned(conn, ingest_run_id, capsys):
    """The banner is a signal only while it is rare: a healthy read prints none."""
    subject, partner = _a_graded_pair(
        conn, ingest_run_id, subject_code="N0000012102", object_code="N0000012103",
        severity="major", subject_unii="TESTUNI123", partner_unii="TESTUNI124",
        subject_name="a healthy subject", partner_name="a healthy partner")

    assert cli_interactions._handle_interactions(conn, _args(subject, partner)) == 0
    assert "INCOMPLETE" not in capsys.readouterr().out


def test_another_drugs_moiety_orphan_does_not_banner(conn, ingest_run_id, capsys):
    """The call site narrows by subject, not only the pure filter: an orphan of an
    unrelated drug leaves a healthy pair's answer complete."""
    _orphaned_pair(conn, ingest_run_id)
    subject, partner = _a_graded_pair(
        conn, ingest_run_id, subject_code="N0000012109", object_code="N0000012110",
        severity="major", subject_unii="TESTUNI129", partner_unii="TESTUNI130",
        subject_name="an unrelated subject", partner_name="an unrelated partner")

    assert cli_interactions._handle_interactions(conn, _args(subject, partner)) == 0
    assert "INCOMPLETE" not in capsys.readouterr().out


class _UnresolvedViewMissing(_RaisingConn):
    """The grade read succeeds; only `curated_target_unresolved` is gone."""

    def execute(self, sql, params=None):
        self._last = sql
        if "curated_target_unresolved" in sql:
            raise self._exc
        return self


def test_a_missing_unresolved_view_names_its_migration():
    """The orphan read has its own guard: a dropped db/035 view names the migration
    rather than tracebacking at the clinician."""
    with pytest.raises(RuntimeError) as raised:
        cli_interactions._handle_interactions(
            _UnresolvedViewMissing(psycopg.errors.UndefinedTable("gone")),
            _args(_KNOWN_TO_THE_STUB))
    assert "035" in str(raised.value)


class _MemberViewMissing(_RaisingConn):
    """Only db/079's `class_rule_member` is gone."""

    def execute(self, sql, params=None):
        self._last = sql
        if "class_rule_member" in sql:
            raise self._exc
        return self


def test_a_missing_class_rule_member_view_names_its_migration(monkeypatch):
    """The narrowing read has its own guard (one call per guarded block): a class
    orphan on a database without db/079 names the migration, not a traceback."""
    monkeypatch.setattr(curation, "unresolved_targets", lambda conn: [_orphan(
        target_table="curated_class_interaction", subject_class=uuid.uuid4())])
    with pytest.raises(RuntimeError) as raised:
        cli_interactions._handle_interactions(
            _MemberViewMissing(psycopg.errors.UndefinedTable("gone")),
            _args(_KNOWN_TO_THE_STUB))
    assert "079" in str(raised.value)
