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
members include an asked drug, or when either side expands to nobody (who it concerns
is then unknowable, so it is kept). Before db/079 (#255) it was bannered for every drug,
because the member expansion lived only inside `curated_ddi_pair`. DB-gated except the
pure-filter test.
"""
import uuid

import psycopg
import pytest

from drugref import (class_grain_read, class_ruling_reach, classes,
                     cli_interactions, curation, graded_pairs)
from tests.test_class_subject_read_path import (_a_graded_class_rule, _a_moiety,
                                                 _file_member)
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


def _an_unrelated_pair(conn, ingest_run_id, n):
    """A healthy graded pair that no class orphan in these tests concerns."""
    return _a_graded_pair(
        conn, ingest_run_id, subject_code=f"N00000257{n}0", object_code=f"N00000257{n}1",
        severity="major", subject_unii=f"TESTUN27{n}0", partner_unii=f"TESTUN27{n}1",
        subject_name="an unrelated subject", partner_name="an unrelated partner")


@pytest.mark.parametrize("emptied", ["subject", "partner"])
def test_a_class_grain_orphan_with_one_side_emptied_banners_that_sides_former_members(
        conn, ingest_run_id, capsys, emptied):
    """THE LIKELIEST ORPHAN: upstream retires one class, so the same re-ingest drops
    the candidate AND that class's memberships. The emptied side's members are then
    unknowable, exactly as for a rule reaching nobody, so the rule is kept -- per
    SIDE, not only when both sides are empty. Before this the former member was told
    `no curated grade` and exit 0."""
    _c, (s,), (o,) = _orphaned_class_rule(
        conn, ingest_run_id, 6, [("TESTUN2561", "a retired class's subject")],
        [("TESTUN2562", "a retired class's partner")])
    former = s if emptied == "subject" else o
    conn.execute("DELETE FROM drugref.class_membership WHERE moiety_uuid = %s",
                 (former,))

    assert cli_interactions._handle_interactions(
        conn, _args(former)) == cli_interactions.EXIT_INCOMPLETE
    assert "INCOMPLETE" in capsys.readouterr().out


def test_the_pair_form_banners_when_only_one_asked_drug_is_a_member(
        conn, ingest_run_id, capsys):
    """SUBJECT-LEVEL, NOT PAIR-LEVEL: one asked drug in the rule is enough, in either
    argument order. Requiring both would under-warn and pass every other test here."""
    _c, (s,), _o = _orphaned_class_rule(
        conn, ingest_run_id, 7, [("TESTUN2563", "an orphan's subject")],
        [("TESTUN2564", "an orphan's partner")])
    u, _ = _an_unrelated_pair(conn, ingest_run_id, 1)

    for a, b in ((s, u), (u, s)):
        assert cli_interactions._handle_interactions(
            conn, _args(a, b)) == cli_interactions.EXIT_INCOMPLETE
        assert "INCOMPLETE" in capsys.readouterr().out


def test_an_orphans_member_expansion_descends_the_class_dag(conn, ingest_run_id):
    """An orphan reaches a drug filed only under a CHILD of its partner class, and a
    partner filed both directly and on the child keeps the direct route
    (`is_direct`, `member_class` = the named class): db/038's tiebreak, moved into
    `class_rule_member` verbatim by db/079."""
    _c, (s,), (o,) = _orphaned_class_rule(
        conn, ingest_run_id, 8, [("TESTUN2565", "an orphan's subject")],
        [("TESTUN2566", "an orphan's direct partner")])
    object_class = conn.execute(
        "SELECT class_uuid FROM drugref.class_membership WHERE moiety_uuid = %s",
        (o,)).fetchone()[0]
    child = _a_class(conn, ingest_run_id, code="N0000025582", name="a child class")
    classes.add_parent_edge(conn, child, object_class, ingest_run_id)
    descendant = _a_moiety(conn, ingest_run_id, "TESTUN2567", "a descendant partner")
    _file_member(conn, descendant, child, ingest_run_id)
    _file_member(conn, o, child, ingest_run_id)

    partners = {r[0]: r[1:] for r in conn.execute(
        "SELECT moiety_uuid, member_class, is_direct FROM drugref.class_rule_member "
        "WHERE side = 'partner'").fetchall()}
    assert partners == {o: (object_class, True), descendant: (child, False)}
    assert len(class_grain_read.class_rules_reaching(conn, {descendant})) == 1


def test_superseded_history_beside_an_orphan_does_not_banner_other_drugs(
        conn, ingest_run_id, capsys):
    """A re-graded orphan leaves a superseded row with no members of its own; only
    the LIVE row is matched, so that history must not read as "reaches nobody"."""
    subject_class, _s, (o,) = _orphaned_class_rule(
        conn, ingest_run_id, 9, [("TESTUN2568", "an orphan's subject")],
        [("TESTUN2569", "an orphan's partner")])
    object_class = conn.execute(
        "SELECT class_uuid FROM drugref.class_membership WHERE moiety_uuid = %s",
        (o,)).fetchone()[0]
    curation.record_class_interaction_judgement(
        conn, subject_class, object_class, "CI_MoA", True, severity="major",
        evidence_grade="established", reviewed_by="a curator",
        reviewed_against="2026.11.16")
    subject, partner = _an_unrelated_pair(conn, ingest_run_id, 2)

    assert cli_interactions._handle_interactions(conn, _args(subject, partner)) == 0
    assert "INCOMPLETE" not in capsys.readouterr().out


def test_an_applies_false_orphan_still_expands_to_its_members(conn, ingest_run_id):
    """`class_rule_member` carries `applies = false` rulings too (db/079): they are
    orphans the banner names, and without members they would reach nobody and banner
    every drug, undoing the narrowing for them."""
    subject_class, (s,), (o,) = _orphaned_class_rule(
        conn, ingest_run_id, 3, [("TESTUN2570", "a retired rule's subject")],
        [("TESTUN2575", "a retired rule's partner")])
    object_class = conn.execute(
        "SELECT class_uuid FROM drugref.class_membership WHERE moiety_uuid = %s",
        (o,)).fetchone()[0]
    curation.record_class_interaction_judgement(
        conn, subject_class, object_class, "CI_MoA", False, reviewed_by="a curator",
        reviewed_against="2026.11.16")

    key = (subject_class, object_class, "CI_MoA")
    assert class_grain_read.class_rules_reaching(conn, {s}) == {key}
    assert class_grain_read.class_rules_reaching(conn, {uuid.uuid4()}) == set()


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
    """Only db/079's `class_rule_member` is gone: `to_regclass` finds no such view."""

    def execute(self, sql, params=None):
        self._last, self._params = sql, params
        if "class_rule_member" in sql:
            raise self._exc
        return self

    def fetchone(self):
        # The banner's own probe (db/083's table) also finds nothing on this stub,
        # so it says "cannot tell" instead of reading through the fake connection.
        if "to_regclass" in self._last and {
                class_grain_read.RULE_MEMBER_VIEW,
                graded_pairs.OBSERVATION_TABLE} & set(self._params or ()):
            return (None,)
        return super().fetchone()


def test_a_missing_class_rule_member_view_banners_every_class_orphan(
        monkeypatch, capsys):
    """Without db/079 the narrowing cannot run, so the command falls back to the
    pre-#255 behaviour -- every class orphan bannered, EXIT_INCOMPLETE -- and says why
    on stderr. Aborting instead would exit 2 after the list had printed, losing the
    banner (moiety-grain orphans included) for the clinician it exists for."""
    monkeypatch.setattr(curation, "unresolved_targets", lambda conn: [_orphan(
        target_table="curated_class_interaction", subject_class=uuid.uuid4())])

    assert cli_interactions._handle_interactions(
        _MemberViewMissing(psycopg.errors.UndefinedTable("gone")),
        _args(_KNOWN_TO_THE_STUB)) == cli_interactions.EXIT_INCOMPLETE
    captured = capsys.readouterr()
    assert "INCOMPLETE" in captured.out
    assert "079" in captured.err


class _BothMissing(_MemberViewMissing):
    """db/079's view AND db/080's reach table are gone."""

    def fetchone(self):
        if "to_regclass" in self._last and {
                class_grain_read.RULE_MEMBER_VIEW,
                class_ruling_reach.REACH_TABLE} & set(self._params or ()):
            return (None,)
        return super().fetchone()


def test_both_missing_relations_are_named_in_one_sentence(monkeypatch, capsys):
    monkeypatch.setattr(curation, "unresolved_targets", lambda conn: [_orphan(
        target_table="curated_class_interaction", subject_class=uuid.uuid4())])
    assert cli_interactions._handle_interactions(
        _BothMissing(psycopg.errors.UndefinedTable("gone")),
        _args(_KNOWN_TO_THE_STUB)) == cli_interactions.EXIT_INCOMPLETE
    # The stub has no db/083 either, so its table is named too.
    assert (f"note: {class_grain_read.RULE_MEMBER_VIEW} (db/079), "
            f"{class_ruling_reach.REACH_TABLE} (db/080) and "
            f"{graded_pairs.OBSERVATION_TABLE} (db/083) are missing, so every "
            "class-rule orphan is shown") in capsys.readouterr().err


def _left_the_class_orphan(conn, ingest_run_id, n, correct=False):
    """Rule A x B graded while X is in A beside Z; then X alone leaves A (and, with
    `correct`, the ruling is re-recorded AFTER X left, so only the FIRST version's
    reach holds X); then the candidate is dropped. Returns X (#258)."""
    sc, oc, (x, _z), _o = _a_graded_class_rule(
        conn, ingest_run_id, subject_code=f"N00000258{n}0",
        object_code=f"N00000258{n}1",
        subject_members=[(f"TESTUN258{n}A", "leaver"), (f"TESTUN258{n}B", "stayer")],
        object_members=[(f"TESTUN258{n}C", "partner")], severity="contraindicated")
    conn.execute("DELETE FROM drugref.class_membership WHERE moiety_uuid = %s", (x,))
    if correct:
        curation.record_class_interaction_judgement(
            conn, sc, oc, "CI_MoA", True, severity="major",
            evidence_grade="established", reviewed_by="test", reviewed_against="2026")
    conn.execute("DELETE FROM drugref.class_pair_contraindication "
                 "WHERE subject_class_uuid = %s", (sc,))
    return x


def test_a_drug_that_alone_left_the_class_is_still_bannered(
        conn, ingest_run_id, capsys):
    """#258's exact case: X left A, A keeps Z, so live membership no longer names X."""
    x = _left_the_class_orphan(conn, ingest_run_id, 1)
    assert cli_interactions._handle_interactions(
        conn, _args(x)) == cli_interactions.EXIT_INCOMPLETE
    assert "INCOMPLETE" in capsys.readouterr().out


def test_a_superseded_versions_reach_still_counts(conn, ingest_run_id, capsys):
    """Premise: the ruling was corrected AFTER X left the class, so only the first
    version's recorded reach holds X; the banner must still match X by natural key."""
    x = _left_the_class_orphan(conn, ingest_run_id, 2, correct=True)
    assert cli_interactions._handle_interactions(
        conn, _args(x)) == cli_interactions.EXIT_INCOMPLETE
    assert "INCOMPLETE" in capsys.readouterr().out


def test_without_db080_every_class_orphan_is_shown(conn, ingest_run_id, capsys):
    sc, _o, (s,), (o,) = _a_graded_class_rule(
        conn, ingest_run_id, subject_code="N0000025830", object_code="N0000025831",
        subject_members=[("TESTUN2583A", "subject")],
        object_members=[("TESTUN2583B", "partner")], severity="contraindicated")
    conn.execute("DELETE FROM drugref.class_pair_contraindication "
                 "WHERE subject_class_uuid = %s", (sc,))
    conn.execute("DROP TABLE drugref.curated_class_interaction_reach")
    assert cli_interactions._handle_interactions(
        conn, _args(s, o)) == cli_interactions.EXIT_INCOMPLETE
    captured = capsys.readouterr()
    assert "INCOMPLETE" in captured.out
    assert "080" in captured.err and "079" not in captured.err
    # "Every" orphan: a drug unrelated to the orphan's classes sees it too.
    stranger = _a_moiety(conn, ingest_run_id, "TESTUN2583C", "stranger")
    assert cli_interactions._handle_interactions(
        conn, _args(stranger)) == cli_interactions.EXIT_INCOMPLETE
    assert "INCOMPLETE" in capsys.readouterr().out


def test_a_wrong_shaped_reach_read_names_db080_not_a_traceback(
        conn, ingest_run_id, monkeypatch):
    """Both relations exist, but the read itself is the wrong shape. The guard must
    produce its operator sentence (RuntimeError), not a ValueError from passing it a
    migration that is not one three-digit prefix (the bug was "079 or 080"); the
    prefix is db/083's since the read gained its observation arm."""
    _orphaned_class_rule(conn, ingest_run_id, 6, [("TESTUN2561", "subject")],
                         [("TESTUN2562", "partner")])

    def _wrong_shape(conn, subjects):
        raise psycopg.errors.UndefinedColumn("no such column")
    monkeypatch.setattr(class_grain_read, "class_rules_reaching", _wrong_shape)

    with pytest.raises(RuntimeError) as raised:
        cli_interactions._handle_interactions(conn, _args(_a_moiety(
            conn, ingest_run_id, "TESTUN2563", "asked")))
    assert "083" in str(raised.value)


def test_a_drug_that_left_the_partner_class_is_still_bannered(
        conn, ingest_run_id, capsys):
    """The mirror of #258's case on the PARTNER side: P left B, B keeps W."""
    sc, _oc, _s, (p, _w) = _a_graded_class_rule(
        conn, ingest_run_id, subject_code="N0000025840", object_code="N0000025841",
        subject_members=[("TESTUN2584A", "subject")],
        object_members=[("TESTUN2584B", "leaver"), ("TESTUN2584C", "stayer")],
        severity="contraindicated")
    conn.execute("DELETE FROM drugref.class_membership WHERE moiety_uuid = %s", (p,))
    conn.execute("DELETE FROM drugref.class_pair_contraindication "
                 "WHERE subject_class_uuid = %s", (sc,))
    assert cli_interactions._handle_interactions(
        conn, _args(p)) == cli_interactions.EXIT_INCOMPLETE
    assert "INCOMPLETE" in capsys.readouterr().out


def test_another_live_rulings_reach_does_not_banner_its_members(
        conn, ingest_run_id, capsys):
    """Reach rows count only for versions of the ORPHANED rule: a member of a separate,
    still-live class rule C x D (which has reach rows of its own) is not bannered."""
    _orphaned_class_rule(conn, ingest_run_id, 7, [("TESTUN2571", "orphan subject")],
                         [("TESTUN2572", "orphan partner")])
    _c, _d, (c_member,), _dm = _a_graded_class_rule(
        conn, ingest_run_id, subject_code="N0000025850", object_code="N0000025851",
        subject_members=[("TESTUN2585A", "live subject")],
        object_members=[("TESTUN2585B", "live partner")], severity="major")
    assert cli_interactions._handle_interactions(conn, _args(c_member)) == 0
    assert "INCOMPLETE" not in capsys.readouterr().out
