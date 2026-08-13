# tests/test_class_subject_read_path.py
"""The two-grain read path over `curated_ddi_pair` (db/033, Task 11 -- design spec
section 14.3 "One consumer view, not two").

WHAT THIS FILE PINS. Tasks 1-8 built the moiety x class grain
(class_contraindication / curated_interaction, db/004/db/029) and Tasks 9-10 added a
second, class x class grain (class_pair_contraindication / curated_class_interaction,
db/032) -- but until this file's migration (db/033), nothing could READ a class-grain
row: `curated_ddi_pair` only ever joined `curated_interaction`. db/033 widens that one
view to carry both grains rather than adding a second, so a consumer asking for a
subject's interactions gets the whole picture from one place (spec section 14.3's
"fewer rows is the harm direction" argument, one level up from db/029's own INNER JOIN
choice).

THE CLASS GRAIN EXPANDS ON *BOTH* SIDES, unlike the moiety grain (whose subject is
already a single moiety and only the object/partner side expands over
class_membership). A class-subject rule's SUBJECT ALSO expands -- through the same
`ci_class_subtree` + `class_membership` machinery the object side already used,
db/033 widens `ci_class_subtree`'s root set to include `class_pair_contraindication`'s
classes for exactly this reason, since that view was previously scoped only to
`class_contraindication.object_class_uuid`.
"""

from drugref import classes, curation, ids, interactions

# `_a_class` (a single MED-RT class row) and `a_graded_rule` (a moiety-grain rule,
# already graded via its own test) are conftest.py / test_curated_overlay.py
# fixtures/helpers, reused rather than re-defined -- this repo's established way to
# share overlay-test setup (see conftest.py's own comment on `a_graded_rule`).
from tests.test_curated_overlay import _a_class


def _a_moiety(conn, ingest_run_id, code, name):
    """One registered moiety with a distinct UNII-shaped code, for a class's
    membership list. `a_moiety` (conftest.py) always mints the SAME fixed moiety, so
    a test needing several distinct members cannot reuse it."""
    moiety_uuid = ids.mint_moiety_uuid(code)
    conn.execute(
        "INSERT INTO drugref.substance_moiety (moiety_uuid, display_name, first_seen_ingest) "
        "VALUES (%s, %s, %s) ON CONFLICT DO NOTHING",
        (moiety_uuid, name, ingest_run_id),
    )
    return moiety_uuid


def _file_member(conn, moiety_uuid, class_uuid, ingest_run_id, *, relationship="has_MoA"):
    """File one moiety as a member of one class, on the given class_membership axis.
    NO `source` column here -- db/003 made the class registry source-neutral, unlike
    class_contraindication/class_pair_contraindication (mirrors
    tests/conftest.py's a_graded_rule fixture, which hits the identical shape)."""
    conn.execute(
        "INSERT INTO drugref.class_membership "
        "(moiety_uuid, class_uuid, relationship, ingest_run) "
        "VALUES (%s, %s, %s, %s) ON CONFLICT DO NOTHING",
        (moiety_uuid, class_uuid, relationship, ingest_run_id),
    )


def _a_graded_class_rule(conn, ingest_run_id, *, subject_code, object_code,
                          subject_members, object_members, source="ONCHIGH",
                          relationship="CI_MoA", **grade):
    """A class x class rule -- candidate row, membership on both sides, and
    drugref's grade -- returning the (subject_class, object_class, subject moiety
    list, object moiety list) a test needs to assert against.

    `subject_members`/`object_members` are (code, name) pairs; passing the SAME
    class code for subject and object with an OVERLAPPING member list is exactly
    how a test builds the QT-prolonging x QT-prolonging self-pair case (db/032
    DECISION 2): the class legitimately equals itself as a rule subject, and only
    the read-path expansion (this file) excludes the resulting identical-moiety
    pairs.
    """
    subject_class = _a_class(conn, ingest_run_id, code=subject_code,
                              name=f"{subject_code} [MoA]")
    object_class = (subject_class if object_code == subject_code else
                     _a_class(conn, ingest_run_id, code=object_code,
                              name=f"{object_code} [MoA]"))
    subject_moieties = []
    for code, name in subject_members:
        m = _a_moiety(conn, ingest_run_id, code, name)
        _file_member(conn, m, subject_class, ingest_run_id, relationship="has_MoA")
        subject_moieties.append(m)
    object_moieties = []
    for code, name in object_members:
        m = _a_moiety(conn, ingest_run_id, code, name)
        _file_member(conn, m, object_class, ingest_run_id, relationship="has_MoA")
        object_moieties.append(m)
    interactions.add_class_pair_contraindication(
        conn, subject_class, object_class, relationship, source, ingest_run_id)
    grade.setdefault("severity", "major")
    grade.setdefault("evidence_grade", "established")
    grade.setdefault("reviewed_by", "test")
    grade.setdefault("reviewed_against", "Phansalkar 2012")
    curation.record_class_interaction_judgement(
        conn, subject_class, object_class, relationship, True, **grade)
    return subject_class, object_class, subject_moieties, object_moieties


def test_a_graded_class_rule_reaches_curated_ddi_pair(conn, ingest_run_id):
    """One curated_class_interaction row must reach every pair its two classes
    expand to -- that is the whole point of the grain. Two subject-side members x
    two object-side members must produce all four pairs, not just the direct
    (first-filed) one."""
    subject_class, object_class, subjects, objects = _a_graded_class_rule(
        conn, ingest_run_id,
        subject_code="N0000000301", object_code="N0000000302",
        subject_members=[("TESTUNII11", "ssri-a"), ("TESTUNII12", "ssri-b")],
        object_members=[("TESTUNII13", "maoi-a"), ("TESTUNII14", "maoi-b")],
    )
    pairs = set(conn.execute(
        "SELECT subject_moiety, partner_moiety FROM drugref.curated_ddi_pair "
        "WHERE via_subject_class = %s", (subject_class,)).fetchall())
    assert pairs == {(s, o) for s in subjects for o in objects}


def test_the_rule_grain_column_distinguishes_them(conn, a_graded_rule, ingest_run_id):
    """moiety_rule | class_rule, so a consumer can tell which shape produced a row
    without joining back -- and via_subject_class is populated for a class rule,
    NULL for a moiety rule."""
    curation.record_interaction_judgement(
        conn, a_graded_rule["subject"], a_graded_rule["class"], "CI_MoA", True,
        severity="major", evidence_grade="established", reviewed_by="test",
        reviewed_against="2026.07.06")
    subject_class, _, _, _ = _a_graded_class_rule(
        conn, ingest_run_id,
        subject_code="N0000000303", object_code="N0000000304",
        subject_members=[("TESTUNII15", "ssri-c")],
        object_members=[("TESTUNII16", "maoi-c")],
    )

    moiety_row = conn.execute(
        "SELECT rule_grain, via_subject_class FROM drugref.curated_ddi_pair "
        "WHERE subject_moiety = %s", (a_graded_rule["subject"],)).fetchone()
    assert moiety_row == ("moiety_rule", None)

    class_row = conn.execute(
        "SELECT rule_grain, via_subject_class FROM drugref.curated_ddi_pair "
        "WHERE via_subject_class = %s", (subject_class,)).fetchone()
    assert class_row == ("class_rule", subject_class)


def test_a_class_rule_never_pairs_a_moiety_with_itself(conn, ingest_run_id):
    """The self-pair entry (QT x QT) expands to distinct pairs only. This is the
    test that makes the self-pair safe rather than merely permitted: db/014
    forbids a MOIETY pairing with itself, and class_pair_contraindication carries
    no equivalent CHECK (a class legitimately equals itself as a rule subject) --
    so this exclusion has to live here, at the pair grain, or a member would be
    reported taken with itself."""
    qt_class, _, members, _ = _a_graded_class_rule(
        conn, ingest_run_id,
        subject_code="N0000000305", object_code="N0000000305",
        subject_members=[("TESTUNII17", "qt-a"), ("TESTUNII18", "qt-b"),
                          ("TESTUNII19", "qt-c")],
        object_members=[],  # same class as subject -- membership is shared
    )
    rows = conn.execute(
        "SELECT subject_moiety, partner_moiety FROM drugref.curated_ddi_pair "
        "WHERE via_subject_class = %s", (qt_class,)).fetchall()
    assert all(s != p for s, p in rows), "a moiety must never pair with itself"
    # 3 members -> every ORDERED pair of DISTINCT members: 3 * 2 = 6.
    assert len(rows) == 6
    assert {p for p in rows} == {(s, o) for s in members for o in members if s != o}


def test_the_moiety_grain_rows_are_unchanged(conn, a_graded_rule, ingest_run_id):
    """curated_ddi_pair's existing rows keep their exact meaning and remain a
    STRICT SUBSET -- fewer rows is the harm direction, so widening must only ever
    add. Graded alongside a class-grain rule in the SAME transaction, so this
    proves the moiety-grain row is untouched by the class grain's presence, not
    merely correct in isolation."""
    curation.record_interaction_judgement(
        conn, a_graded_rule["subject"], a_graded_rule["class"], "CI_MoA", True,
        severity="major", evidence_grade="established", management="avoid",
        reviewed_by="test", reviewed_against="2026.07.06")
    _a_graded_class_rule(
        conn, ingest_run_id,
        subject_code="N0000000306", object_code="N0000000307",
        subject_members=[("TESTUNII20", "ssri-d")],
        object_members=[("TESTUNII21", "maoi-d")],
    )
    # The exact row db/029/030's own read-path test asserts, byte for byte, plus
    # the two new trailing columns -- unaffected by the class-grain row that now
    # also lives in this view.
    assert conn.execute(
        "SELECT partner_moiety, severity, management, rule_grain, via_subject_class "
        "FROM drugref.curated_ddi_pair WHERE subject_moiety = %s",
        (a_graded_rule["subject"],)).fetchall() == [
            (a_graded_rule["partner"], "major", "avoid", "moiety_rule", None)]


# ============================================================================
# DAG descent and policy gating, on BOTH sides of the class grain (fix round 1)
# ============================================================================
# WHY THIS SECTION EXISTS. Every test above uses DIRECT membership only on both
# sides -- none inserts a `class_parent` edge or a `class_expansion_policy` row.
# That leaves db/033's actually-new behaviour -- the SUBJECT side descending the
# class DAG and being gated by `class_expansion_policy_current`, something Tasks
# 1-8 never needed because their subject was always one fixed moiety -- completely
# unpinned: a broken subtree walk or a dropped deny-gate on either side would not
# fail a single test in this suite. A `deny` policy that stopped being honoured
# would silently resurrect exactly the pairs db/027 exists to withhold.
#
# THE OBJECT SIDE'S OWN CTE (`class_rule_partner_member`) gets the same two tests,
# not only the subject side. It is SHAPED like `ddi_candidate_pair`'s long-tested
# object expansion (test_ddi_pairs.py's test_a_rule_on_a_parent_reaches_a_member_
# of_the_child and test_a_rule_on_a_denied_root_reaches_direct_members_only), but
# it is NOT that code -- `class_rule_partner_member` is a separate CTE over
# `curated_class_interaction`/`class_pair_contraindication`, and a defect local to
# it (a copy-paste slip naming the wrong side's class, say) would not be caught by
# any test of `ddi_candidate_pair`, which never touches these tables at all.
# Duplicating the ASSERTION SHAPE across two already-proven-safe axes is cheap;
# leaving a whole CTE unpinned because its neighbour is well-tested is the exact
# complacency that let the subject side go uncovered.


def _a_descent_and_policy_fixture(conn, ingest_run_id, *, expanding_side):
    """A class-subject rule with ONE class expanding (a direct member plus a
    descendant-only member below it in `class_parent`) and the OTHER class fixed
    to one direct member, so the two mechanisms under test -- DAG descent and
    policy gating -- are isolated to a single, named side.

    Returns a dict: `expanding_class` (the class carrying the parent/child pair),
    `direct`/`descendant_only` (its two members), `fixed_class`/`fixed` (the
    other side, kept to one class and one direct member so a failure can only be
    about the side under test), and `subject_class`/`object_class` (the rule's
    actual two ends, i.e. `expanding_class` relabelled by which side it plays --
    returned explicitly rather than left for the caller to reconstruct, which is
    exactly the mistake an earlier draft of this fixture made).
    """
    tag = "4" if expanding_side == "subject" else "5"
    parent = _a_class(conn, ingest_run_id, code=f"N0000000{tag}01",
                       name=f"{expanding_side.title()} Parent [MoA]")
    child = _a_class(conn, ingest_run_id, code=f"N0000000{tag}02",
                      name=f"{expanding_side.title()} Child [MoA]")
    classes.add_parent_edge(conn, child, parent, ingest_run_id)
    fixed_class = _a_class(conn, ingest_run_id, code=f"N0000000{tag}03",
                            name=f"{expanding_side.title()} Fixed [MoA]")

    direct = _a_moiety(conn, ingest_run_id, f"TESTUNII{tag}0", "direct-member")
    _file_member(conn, direct, parent, ingest_run_id)
    descendant_only = _a_moiety(conn, ingest_run_id, f"TESTUNII{tag}1",
                                 "descendant-only-member")
    _file_member(conn, descendant_only, child, ingest_run_id)  # NOT filed on `parent`
    fixed = _a_moiety(conn, ingest_run_id, f"TESTUNII{tag}2", "fixed-side-member")
    _file_member(conn, fixed, fixed_class, ingest_run_id)

    subject_class, object_class = (
        (parent, fixed_class) if expanding_side == "subject" else (fixed_class, parent))
    interactions.add_class_pair_contraindication(
        conn, subject_class, object_class, "CI_MoA", "ONCHIGH", ingest_run_id)
    curation.record_class_interaction_judgement(
        conn, subject_class, object_class, "CI_MoA", True,
        severity="major", evidence_grade="established", reviewed_by="test",
        reviewed_against="Phansalkar 2012")
    return dict(expanding_class=parent, direct=direct, descendant_only=descendant_only,
                fixed_class=fixed_class, fixed=fixed,
                subject_class=subject_class, object_class=object_class)


def _rows_for_rule(conn, subject_class_uuid, object_class_uuid):
    """(subject_moiety, partner_moiety) pairs for the class-grain rule identified
    by its (subject_class, object_class) natural key -- unambiguous regardless of
    which side is expanding, unlike filtering on `via_subject_class` alone (which
    only narrows to the rule when the SUBJECT is the expanding class)."""
    return set(conn.execute(
        "SELECT subject_moiety, partner_moiety FROM drugref.curated_ddi_pair "
        "WHERE via_subject_class = %s AND via_class = %s",
        (subject_class_uuid, object_class_uuid)).fetchall())


def _deny(conn, class_uuid, name):
    """File a `deny` decision against the (source, source_code) `class_uuid`
    names -- the natural key `class_expansion_policy_current` is keyed on, not
    the UUID. Mirrors tests/test_expansion_policy.py's own INSERT shape."""
    source, source_code = conn.execute(
        "SELECT source, source_code FROM drugref.substance_class "
        "WHERE class_uuid = %s", (class_uuid,)).fetchone()
    conn.execute(
        "INSERT INTO drugref.class_expansion_policy "
        "(source, source_code, decision, class_name, rationale, reviewed_by, "
        " reviewed_against) VALUES (%s, %s, 'deny', %s, 'test deny', 'test', "
        "'2026.07.06')", (source, source_code, name))


def test_a_class_rule_descends_the_subject_side_dag(conn, ingest_run_id):
    """DAG descent, SUBJECT side. A rule naming a PARENT subject class must reach a
    moiety filed only under a CHILD of it -- the subject-side mirror of
    test_ddi_pairs.py's test_a_rule_on_a_parent_reaches_a_member_of_the_child, and
    the exact behaviour Tasks 1-8 never needed to build because their subject was
    always a single fixed moiety."""
    f = _a_descent_and_policy_fixture(conn, ingest_run_id, expanding_side="subject")
    pairs = _rows_for_rule(conn, f["subject_class"], f["object_class"])
    assert pairs == {(f["direct"], f["fixed"]), (f["descendant_only"], f["fixed"])}


def test_a_deny_policy_blocks_subject_side_descent_but_not_the_direct_member(
        conn, ingest_run_id):
    """Policy gating, SUBJECT side. A `deny` decision on the class the rule NAMES
    must stop the descendant-only member from reaching the pair while leaving the
    direct member untouched -- test_ddi_pairs.py's test_a_rule_on_a_denied_root_
    reaches_direct_members_only, mirrored to the subject. If this regressed, a
    curator's `deny` would silently stop being honoured on exactly the axis db/033
    adds -- resurrecting the pairs db/027 exists to withhold."""
    f = _a_descent_and_policy_fixture(conn, ingest_run_id, expanding_side="subject")
    _deny(conn, f["expanding_class"], "Subject Parent [MoA]")
    pairs = _rows_for_rule(conn, f["subject_class"], f["object_class"])
    assert pairs == {(f["direct"], f["fixed"])}


def test_a_class_rule_descends_the_object_side_dag(conn, ingest_run_id):
    """DAG descent, OBJECT side, for THIS grain's own CTE
    (`class_rule_partner_member`) -- not a re-test of `ddi_candidate_pair`, which
    never touches `class_pair_contraindication`/`curated_class_interaction` and so
    provides no coverage of this code at all, however similar the shape."""
    f = _a_descent_and_policy_fixture(conn, ingest_run_id, expanding_side="object")
    pairs = _rows_for_rule(conn, f["subject_class"], f["object_class"])
    assert pairs == {(f["fixed"], f["direct"]), (f["fixed"], f["descendant_only"])}


def test_a_deny_policy_blocks_object_side_descent_but_not_the_direct_member(
        conn, ingest_run_id):
    """Policy gating, OBJECT side, for `class_rule_partner_member` specifically --
    see the docstring above on why this is not redundant with `ddi_candidate_pair`'s
    own long-standing coverage of the identically-shaped predicate."""
    f = _a_descent_and_policy_fixture(conn, ingest_run_id, expanding_side="object")
    _deny(conn, f["expanding_class"], "Object Parent [MoA]")
    pairs = _rows_for_rule(conn, f["subject_class"], f["object_class"])
    assert pairs == {(f["fixed"], f["direct"])}


# ============================================================================
# ci_class_subtree / ci_class_pair_subtree stay TWO walks, not one (db/034, Task
# 11B -- the moiety-grain hot-path recovery)
# ============================================================================
# WHY THIS SECTION EXISTS. db/033 widened `ci_class_subtree`'s roots to also cover
# `class_pair_contraindication` (the class grain's own candidate tier), so a
# class-grain SUBJECT could expand through the ONE view the object side already
# used. Task 11 MEASURED what that widening cost: Postgres's row-estimate for the
# shared recursive CTE inflated roughly 5x, which tipped its join plan from a cheap
# Hash Join to a slower Merge Join -- a tax paid by EVERY reader of
# `ci_class_subtree` (ddi_candidate_pair and both gap views), not only class-grain
# queries. Measured at ~3.6x baseline even with an EMPTY class-grain overlay.
#
# db/034 (this task) reverts the widening and gives the class grain a SEPARATE
# view, `ci_class_pair_subtree`, seeded only from `class_pair_contraindication`'s
# own classes. The eight tests above already prove the class grain still works
# end to end through whichever view backs it -- they do not care which one. These
# two tests pin the SEPARATION itself: that a class named only by one candidate
# table does not leak into the other table's walk. Without this, a well-meaning
# "these two views look identical, merge them" edit later would silently
# reintroduce exactly the regression Task 11B exists to remove, and nothing above
# would notice -- both walks would still produce correct RESULTS, just slower ones.

def test_ci_class_subtree_does_not_widen_for_the_class_grain(conn, ingest_run_id):
    """A class named ONLY by a class x class rule (class_pair_contraindication,
    db/032) must not appear as its own root in `ci_class_subtree` -- that view is
    restored to db/012's original seed, `class_contraindication.object_class_uuid`
    alone. If this regressed back to db/033's widened form, every moiety-grain
    query would pay the ~3.6x structural tax Task 11 measured, again."""
    subject_class, object_class, _, _ = _a_graded_class_rule(
        conn, ingest_run_id,
        subject_code="N0000000308", object_code="N0000000309",
        subject_members=[("TESTUNII22", "class-only-subject")],
        object_members=[("TESTUNII23", "class-only-object")],
    )
    assert conn.execute(
        "SELECT count(*) FROM drugref.ci_class_subtree "
        "WHERE root_uuid IN (%s, %s)", (subject_class, object_class)
    ).fetchone()[0] == 0


def test_ci_class_pair_subtree_is_the_class_grains_own_separate_walk(
        conn, ingest_run_id):
    """`ci_class_pair_subtree` (db/034) descends the DAG below a
    class_pair_contraindication root exactly as `ci_class_subtree` does below its
    own roots -- but it is a DIFFERENT walk over a DIFFERENT root set: a class
    named only by a moiety x class rule (class_contraindication, db/004) must not
    appear in it. Both halves of this test matter -- descent proves the new view
    actually walks the DAG rather than returning only direct roots, and the
    absence proves it is not secretly reading the other table too."""
    root = _a_class(conn, ingest_run_id, code="N0000000310", name="Pair Root [MoA]")
    child = _a_class(conn, ingest_run_id, code="N0000000311", name="Pair Child [MoA]")
    classes.add_parent_edge(conn, child, root, ingest_run_id)
    other = _a_class(conn, ingest_run_id, code="N0000000312", name="Pair Other [MoA]")
    interactions.add_class_pair_contraindication(
        conn, root, other, "CI_MoA", "MED-RT", ingest_run_id)

    moiety_only_root = _a_class(conn, ingest_run_id, code="N0000000313",
                                 name="Moiety-Only Root [MoA]")
    subject = _a_moiety(conn, ingest_run_id, "TESTUNII24", "subject-drug")
    interactions.add_contraindication(
        conn, subject, moiety_only_root, "CI_MoA", "MED-RT", ingest_run_id)

    reached = {r[0] for r in conn.execute(
        "SELECT class_uuid FROM drugref.ci_class_pair_subtree WHERE root_uuid = %s",
        (root,)).fetchall()}
    assert reached == {root, child}
    assert conn.execute(
        "SELECT count(*) FROM drugref.ci_class_pair_subtree WHERE root_uuid = %s",
        (moiety_only_root,)).fetchone()[0] == 0


# ---- the class half's own WHERE clause, and the walk's depth (review findings) ----
#
# db/034's class half is gated on `cci.superseded_by IS NULL AND cci.applies`, and
# expands through a recursive walk whose comment claims cycle-safety and linearity
# under a multi-parent DAG. Neither predicate nor either claim was exercised: the
# whole file tested the SHAPE of expansion, never the conditions under which a rule
# stops expanding at all.


def test_a_superseded_class_grade_stops_reaching_pairs(conn, ingest_run_id):
    """A corrected rule must emit its NEW grade only, never both.

    `curated_class_interaction` is append-only, so a regrade leaves the old row
    in place forever with `superseded_by` set. Drop the view's
    `superseded_by IS NULL` and every corrected rule renders each of its pairs
    TWICE, under two different severities -- which is worse than either grade
    alone, because a consumer taking the first row gets an arbitrary answer.
    """
    subject_class, object_class, _subjects, _partners = _a_graded_class_rule(
        conn, ingest_run_id, subject_code="N0000000401", object_code="N0000000402",
        subject_members=[("TESTUNII40", "subject-a")],
        object_members=[("TESTUNII41", "partner-a")],
        severity="major")
    assert len(_rows_for_rule(conn, subject_class, object_class)) == 1

    # The correction: same natural key, a different severity.
    curation.record_class_interaction_judgement(
        conn, subject_class, object_class, "CI_MoA", True,
        severity="contraindicated", evidence_grade="established",
        reviewed_by="test", reviewed_against="Phansalkar 2012")

    severities = [r[0] for r in conn.execute(
        "SELECT severity FROM drugref.curated_ddi_pair "
        "WHERE via_subject_class = %s AND via_class = %s",
        (subject_class, object_class)).fetchall()]
    assert severities == ["contraindicated"]


def test_a_retired_class_rule_reaches_no_pairs(conn, ingest_run_id):
    """`applies = false` is a RULING -- "reviewed, and this is not a real
    interaction" -- and it is how a curator retires a class rule without
    deleting anything from an append-only table. The schema already accepted
    the value; nothing checked that the read path honoured it, so a regression
    would have kept firing alerts across every pair the rule expands to for a
    rule a curator had explicitly ruled out."""
    subject_class, object_class, _s, _p = _a_graded_class_rule(
        conn, ingest_run_id, subject_code="N0000000403", object_code="N0000000404",
        subject_members=[("TESTUNII42", "subject-b")],
        object_members=[("TESTUNII43", "partner-b")],
        severity="major")
    assert len(_rows_for_rule(conn, subject_class, object_class)) == 1

    curation.record_class_interaction_judgement(
        conn, subject_class, object_class, "CI_MoA", False,
        severity=None, evidence_grade=None,
        reviewed_by="test", reviewed_against="Phansalkar 2012")

    assert _rows_for_rule(conn, subject_class, object_class) == set()


def test_ci_class_pair_subtree_handles_depth_and_a_diamond(conn, ingest_run_id):
    """The walk's own claims, tested past one level.

    db/034 states this view is deduped on (root, class) rather than on paths
    "so it terminates under a cycle and stays linear in a multi-parent DAG",
    and it deliberately rewrote the base term's shape rather than copying
    ci_class_subtree's. Existing coverage stopped at a single parent edge,
    which cannot tell a correct recursive walk from one that only ever returns
    its direct children.

    The diamond is the case that distinguishes dedup-on-(root,class) from
    dedup-on-paths: `grandchild` is reachable by TWO routes, and must appear
    exactly once.
    """
    root = _a_class(conn, ingest_run_id, code="N0000000410", name="Root [MoA]")
    left = _a_class(conn, ingest_run_id, code="N0000000411", name="Left [MoA]")
    right = _a_class(conn, ingest_run_id, code="N0000000412", name="Right [MoA]")
    grandchild = _a_class(conn, ingest_run_id, code="N0000000413", name="Grand [MoA]")
    for child in (left, right):
        classes.add_parent_edge(conn, child, root, ingest_run_id)
        classes.add_parent_edge(conn, grandchild, child, ingest_run_id)

    other = _a_class(conn, ingest_run_id, code="N0000000414", name="Other [MoA]")
    interactions.add_class_pair_contraindication(
        conn, root, other, "CI_MoA", "MED-RT", ingest_run_id)

    rows = conn.execute(
        "SELECT class_uuid FROM drugref.ci_class_pair_subtree WHERE root_uuid = %s",
        (root,)).fetchall()
    # Two levels down, both branches, root included in its own subtree...
    assert {r[0] for r in rows} == {root, left, right, grandchild}
    # ...and the diamond's shared descendant exactly ONCE, not once per path.
    assert len(rows) == 4
