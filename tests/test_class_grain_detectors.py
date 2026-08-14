# tests/test_class_grain_detectors.py
"""The class grain's DETECTORS (`db/035`, issues #90, #96-#99).

WHAT THIS FILE PINS, AND WHY IT IS ONE FILE. `db/032`-`db/034` gave the class x class
grain slice 5c.1's WRITE path -- a candidate tier, an append-only overlay, and a
two-grain read view -- and none of the moiety grain's DETECTORS. The moiety grain has a
view for every way a rule can fail (ungraded, unpopulated, orphaned, unreviewed root,
unsigned) and the class grain had none, so a class-grain contraindication could be
ingested, graded, committed and reported successful while reaching zero patients, with
`drugref status` printing health. The five issues are one shape and so are their tests.

THE ONE RULE THAT DECIDES SEVERAL OF THESE. A detector's grain must equal the grain of
the thing a curator can actually answer. `gap_uncurated_class_interaction_rule` is per
RULE (three columns a curator supersedes as a unit), `gap_unreviewed_expansion_root`
stays per CLASS (the policy row is per class, so both grains asking about one class
must raise ONE question), and `curated_grain_disagreement` is per RULE PAIR rather than
per drug pair -- two rules can overlap on thousands of pairs, and a per-pair detector
would report one disagreement thousands of times.
"""
import uuid

import pytest

from drugref import curation, ids, interactions, keys, questions, releases, signing
from tests.test_class_subject_read_path import (_a_graded_class_rule, _a_moiety,
                                                _file_member)
from tests.test_curated_overlay import _a_class


@pytest.fixture
def institutional_key(conn):
    """A registered Ed25519 key, for the one test that publishes a release.

    Re-declared here rather than imported: pytest resolves fixtures by NAME through
    conftest.py, and this one lives in tests/test_releases.py, which is a peer module
    rather than a conftest -- so importing the function would work only by accident of
    decorator ordering. Three lines duplicated beats a cross-module fixture import
    this repo has already rejected once (see conftest.py's own note on `a_graded_rule`).
    """
    keypair = signing.generate_keypair()
    keys.register(conn, public_key=keypair.public_key, holder="drugref.org",
                  registered_by="an operator")
    return {"private": keypair.private_key, "public": keypair.public_key,
            "fingerprint": signing.fingerprint(keypair.public_key)}

# The four levels every curated table grades on, in the clinical order db/035 makes
# data. Written here as the TEST's own statement of the order rather than read from
# the table, so a migration that renumbered the ranks fails instead of agreeing with
# itself.
EXPECTED_SEVERITY_RANK = {"contraindicated": 1, "major": 2, "moderate": 3, "minor": 4}


def _wide_class(conn, run_id, code, descendants):
    """One class with `descendants` child classes beneath it, and no policy row.

    The shape `gap_unreviewed_expansion_root` exists to find: a root abstract enough
    that expanding a contraindication over its whole subtree is fan-out rather than
    recall. Local to this file rather than imported from tests/test_gap_views.py's
    `_wide_root`, which also plants a MOIETY-grain rule on the root -- the one thing a
    class-grain test must not do, since it would make the row appear for the reason
    that already worked.
    """
    root = _a_class(conn, run_id, code=code, name=f"Wide {code} [MoA]")
    base = int(code[1:])
    for i in range(descendants):
        child = _a_class(conn, run_id, code=f"N{base + i + 1:010d}",
                         name=f"Child {i} [MoA]")
        conn.execute(
            "INSERT INTO drugref.class_parent "
            "(child_class_uuid, parent_class_uuid, ingest_run) VALUES (%s, %s, %s)",
            (child, root, run_id))
    return root


def _an_ungraded_class_rule(conn, run_id, *, subject_code="N0000000401",
                            object_code="N0000000402", subject_members=1,
                            object_members=1, object_axis="has_MoA"):
    """A class x class CANDIDATE with members on both sides and NO curated grade.

    `_a_graded_class_rule` (imported above) always grades; the worklist's whole
    subject is the row nobody has graded yet, so this file needs the ungraded half.
    `object_axis` files the object side's members on a DIFFERENT class_membership
    relationship when asked, which is how a test builds a rule that expands to zero
    pairs without any error anywhere.
    """
    subject_class = _a_class(conn, run_id, code=subject_code,
                             name=f"{subject_code} [MoA]")
    object_class = _a_class(conn, run_id, code=object_code,
                            name=f"{object_code} [MoA]")
    for i in range(subject_members):
        m = _a_moiety(conn, run_id, f"SUBJUNII{i:02d}", f"subject-drug-{i}")
        _file_member(conn, m, subject_class, run_id, relationship="has_MoA")
    for i in range(object_members):
        m = _a_moiety(conn, run_id, f"OBJUNII{i:03d}", f"object-drug-{i}")
        _file_member(conn, m, object_class, run_id, relationship=object_axis)
    interactions.add_class_pair_contraindication(
        conn, subject_class, object_class, "CI_MoA", "ONCHIGH", run_id)
    return subject_class, object_class


# ============================================================================
# 1. severity_kind -- the vocabulary AND its order, in one place
# ============================================================================


def test_severity_kind_holds_the_four_levels_in_clinical_order(conn):
    """Precedence needs an ORDINAL, and until db/035 the four levels existed only as
    five identical CHECK constraints -- a vocabulary with no order at all. Rank 1 is
    the most severe, so `ORDER BY severity_rank` is most-severe-first with no DESC to
    forget."""
    rows = dict(conn.execute(
        "SELECT severity, severity_rank FROM drugref.severity_kind").fetchall())
    assert rows == EXPECTED_SEVERITY_RANK


@pytest.mark.parametrize("table", [
    "curated_interaction", "curated_condition", "curated_class_interaction",
    "additive_effect", "interaction_group_assertion"])
def test_every_severity_column_is_a_foreign_key_into_severity_kind(conn, table):
    """db/006's finding 1, applied to the fifth vocabulary this project has had to
    consolidate: five hardcoded CHECKs are five things to widen, and widening four of
    them is a value one table admits and the others refuse. A foreign key cannot
    disagree with itself.
    """
    fks = {row[0] for row in conn.execute(
        "SELECT conname FROM pg_constraint "
        "WHERE conrelid = %s::regclass AND contype = 'f' "
        "AND confrelid = 'drugref.severity_kind'::regclass",
        (f"drugref.{table}",)).fetchall()}
    assert fks, (
        f"drugref.{table}.severity must be a FOREIGN KEY into severity_kind, not a "
        "CHECK -- otherwise the vocabulary and its order live in two places that can "
        "disagree")


def test_an_unknown_severity_is_still_refused(conn, a_moiety, ingest_run_id):
    """Replacing a CHECK with a foreign key must not WEAKEN the guard. The whole
    point of the CHECK was that a severity cannot drift one curator at a time."""
    klass = _a_class(conn, ingest_run_id)
    with pytest.raises(Exception) as excinfo:
        curation.record_interaction_judgement(
            conn, a_moiety, klass, "CI_MoA", True, severity="catastrophic",
            evidence_grade="established", reviewed_by="test",
            reviewed_against="2026.07.06")
    assert "severity" in str(excinfo.value).lower()


# ============================================================================
# 2. class_pair_rule_reach -- the class grain states its own reach (#99, #96)
# ============================================================================


def test_class_pair_rule_reach_counts_both_sides(conn, ingest_run_id):
    """`ci_rule_partner_reach` one grain over. The moiety grain counts ONE side --
    its subject is already a single drug -- while a class x class rule expands on
    BOTH, so its reach is a product and both factors have to be visible."""
    subject_class, object_class = _an_ungraded_class_rule(
        conn, ingest_run_id, subject_members=2, object_members=3)
    row = conn.execute(
        "SELECT subject_subtree_member_count, object_subtree_member_count, "
        "max_pair_count FROM drugref.class_pair_rule_reach "
        "WHERE subject_class_uuid = %s AND object_class_uuid = %s",
        (subject_class, object_class)).fetchone()
    assert row == (2, 3, 6)


def test_a_rule_whose_object_side_has_no_member_on_its_axis_reaches_zero(
        conn, ingest_run_id):
    """ISSUE #92's SHAPE, MADE VISIBLE. A class-pair rule selects ONE
    `class_membership.relationship` (its axis), so a rule whose object class is
    populated on a DIFFERENT axis expands to zero pairs forever with no error
    anywhere. The reach view is where that becomes a number an operator can see."""
    subject_class, object_class = _an_ungraded_class_rule(
        conn, ingest_run_id, subject_members=2, object_members=3,
        object_axis="has_PE")
    row = conn.execute(
        "SELECT object_subtree_member_count, max_pair_count "
        "FROM drugref.class_pair_rule_reach "
        "WHERE subject_class_uuid = %s AND object_class_uuid = %s",
        (subject_class, object_class)).fetchone()
    assert row == (0, 0)


def test_reach_counts_members_below_the_class_too(conn, ingest_run_id):
    """SUBTREE, not direct membership -- the rule expands over the class DAG, so a
    reach count that stopped at direct members would under-report every real rule."""
    subject_class, object_class = _an_ungraded_class_rule(conn, ingest_run_id)
    child = _a_class(conn, ingest_run_id, code="N0000000499", name="child [MoA]")
    conn.execute(
        "INSERT INTO drugref.class_parent "
        "(child_class_uuid, parent_class_uuid, ingest_run) VALUES (%s, %s, %s)",
        (child, object_class, ingest_run_id))
    deep = _a_moiety(conn, ingest_run_id, "DEEPUNII01", "deep-drug")
    _file_member(conn, deep, child, ingest_run_id, relationship="has_MoA")
    row = conn.execute(
        "SELECT object_subtree_member_count, object_direct_member_count "
        "FROM drugref.class_pair_rule_reach WHERE subject_class_uuid = %s",
        (subject_class,)).fetchone()
    assert row == (2, 1)


# ============================================================================
# 3. gap_uncurated_class_interaction_rule -- the grain's PRIMARY question (#96)
# ============================================================================


def test_an_ungraded_class_pair_rule_is_queued(conn, ingest_run_id):
    """The failure #96 was filed about: `drugref ingest chain` reports
    `class_rules_written=9`, the operator never runs the separate `drugref curate`,
    and nine ONC high-priority rules sit permanently ungraded while
    `question_worklist` shows nothing to do."""
    subject_class, object_class = _an_ungraded_class_rule(
        conn, ingest_run_id, subject_members=2, object_members=3)
    rows = conn.execute(
        "SELECT subject_class, object_class, relationship, max_pair_count "
        "FROM drugref.gap_uncurated_class_interaction_rule").fetchall()
    assert rows == [(subject_class, object_class, "CI_MoA", 6)]


def test_a_graded_class_pair_rule_leaves_the_queue(conn, ingest_run_id):
    """Every ruling means a curator LOOKED -- db/029 section 4's rule, unchanged one
    grain over. A rule graded `applies = false` must leave too, or it is asked about
    every release forever."""
    _a_graded_class_rule(
        conn, ingest_run_id, subject_code="N0000000411", object_code="N0000000412",
        subject_members=[("TESTUNII31", "s-a")], object_members=[("TESTUNII32", "o-a")])
    assert conn.execute(
        "SELECT count(*) FROM drugref.gap_uncurated_class_interaction_rule"
    ).fetchone() == (0,)


def test_a_class_pair_rule_reaching_no_pair_is_not_queued(conn, ingest_run_id):
    """#36's lesson, one grain over: a review gate must only ask what an answer could
    change. Grading a rule that expands to nobody is a provable no-op, so it is
    reported to the OPERATOR (through class_pair_rule_reach) and not to a curator."""
    _an_ungraded_class_rule(conn, ingest_run_id, object_axis="has_PE")
    assert conn.execute(
        "SELECT count(*) FROM drugref.gap_uncurated_class_interaction_rule"
    ).fetchone() == (0,)


def test_the_class_grain_worklist_registers_an_open_question(conn, ingest_run_id):
    """A view nobody calls reports nothing to nobody (issue 76). The kind must be
    registered in `questions._GAP_SOURCES`, or the view exists and the registry
    still says there is nothing to do."""
    subject_class, object_class = _an_ungraded_class_rule(conn, ingest_run_id)
    questions.register_from_gaps(conn, ingest_run_id)
    rows = conn.execute(
        "SELECT gap_key, question_text FROM drugref.open_question "
        "WHERE gap_kind = 'uncurated_class_interaction_rule'").fetchall()
    assert len(rows) == 1
    gap_key, text = rows[0]
    assert gap_key == (f"CLASS:{subject_class}/CLASS:{object_class}/CI_AXIS:CI_MoA")
    assert "grade" in text.lower() or "interact" in text.lower()


def test_the_gap_key_carries_the_rules_whole_natural_key(conn, ingest_run_id):
    """`question_uuid` is `uuid5(gap_kind, gap_key)` and is IMMORTAL and externally
    cited, so the key must have the same grain as the thing it names. A key omitting
    the relationship would fold two rules on one axis-pair into ONE permanent
    question -- exactly the defect the 5c.2 review found in
    `unresolved_onc_endpoint`'s own key, which omitted `endpoint_role`.
    """
    subject_class, object_class = _an_ungraded_class_rule(conn, ingest_run_id)
    interactions.add_class_pair_contraindication(
        conn, subject_class, object_class, "CI_PE", "ONCHIGH", ingest_run_id)
    # The CI_PE rule reaches no pair (its members are filed has_MoA), so only the
    # CI_MoA rule is queued -- but the key must still name the axis it belongs to.
    keys = {row[0] for row in conn.execute(
        "SELECT 'CLASS:' || subject_class || '/CLASS:' || object_class || "
        "'/CI_AXIS:' || relationship FROM drugref.gap_uncurated_class_interaction_rule"
    ).fetchall()}
    assert keys == {f"CLASS:{subject_class}/CLASS:{object_class}/CI_AXIS:CI_MoA"}


# ============================================================================
# 4. gap_unreviewed_expansion_root -- widened, NOT duplicated (#99)
# ============================================================================


def test_a_class_grain_root_reaches_the_expansion_review_gate(conn, ingest_run_id):
    """db/034 gave the class grain its own subtree walk, which left it outside the
    gate that makes `COALESCE(policy, 'allow')` safe: a class-grain rule naming a
    sprawling abstract root expanded over its whole subtree by default, permanently,
    invisible to the review gate that exists to catch precisely that."""
    root = _wide_class(conn, ingest_run_id, "N0000000600", 21)
    subject = _a_class(conn, ingest_run_id, code="N0000000700", name="subj [MoA]")
    interactions.add_class_pair_contraindication(
        conn, subject, root, "CI_MoA", "ONCHIGH", ingest_run_id)
    rows = conn.execute(
        "SELECT class_uuid, descendant_class_count FROM "
        "drugref.gap_unreviewed_expansion_root WHERE class_uuid = %s",
        (root,)).fetchone()
    assert rows == (root, 21)


def test_the_class_grains_SUBJECT_side_is_reviewed_too(conn, ingest_run_id):
    """A class-pair rule expands on BOTH sides (db/034's `ci_class_pair_subtree` is
    seeded from both columns for that reason), so a sprawling SUBJECT root fans out
    exactly as a sprawling object root does."""
    root = _wide_class(conn, ingest_run_id, "N0000000800", 21)
    obj = _a_class(conn, ingest_run_id, code="N0000000900", name="obj [MoA]")
    interactions.add_class_pair_contraindication(
        conn, root, obj, "CI_MoA", "ONCHIGH", ingest_run_id)
    assert conn.execute(
        "SELECT count(*) FROM drugref.gap_unreviewed_expansion_root "
        "WHERE class_uuid = %s", (root,)).fetchone() == (1,)


def test_one_class_named_by_both_grains_raises_ONE_question(conn, ingest_run_id):
    """THE REASON THIS VIEW IS WIDENED RATHER THAN COPIED. The question is "may this
    class expand?", the answer is ONE `class_expansion_policy` row, and
    `question_uuid = uuid5(gap_kind, 'CLASS:' || class_uuid)` is immortal. A second
    gap kind over the same class would mint a SECOND permanent question that one
    policy decision answers -- two immortal identifiers for one fact."""
    root = _wide_class(conn, ingest_run_id, "N0000001000", 21)
    subject_moiety = _a_moiety(conn, ingest_run_id, "MOIETY0001", "moiety-subject")
    conn.execute(
        "INSERT INTO drugref.class_contraindication (subject_moiety_uuid, "
        "object_class_uuid, relationship, source, ingest_run) "
        "VALUES (%s, %s, 'CI_MoA', 'MED-RT', %s)",
        (subject_moiety, root, ingest_run_id))
    other = _a_class(conn, ingest_run_id, code="N0000001100", name="other [MoA]")
    interactions.add_class_pair_contraindication(
        conn, other, root, "CI_MoA", "ONCHIGH", ingest_run_id)

    # ONE row, and `ci_rule_count` counting BOTH grains' rules. The count is what
    # makes this test able to fail: one row is what the moiety arm alone already
    # produced, so asserting only that would pass whether or not the class arm was
    # ever added -- the vacuous shape this project has now filed four issues about.
    assert conn.execute(
        "SELECT count(*), max(ci_rule_count) FROM "
        "drugref.gap_unreviewed_expansion_root WHERE class_uuid = %s",
        (root,)).fetchone() == (1, 2)
    questions.register_from_gaps(conn, ingest_run_id)
    assert conn.execute(
        "SELECT count(*) FROM drugref.open_question "
        "WHERE gap_kind = 'unreviewed_expansion_root' AND gap_key = %s",
        (f"CLASS:{root}",)).fetchone() == (1,)


def test_a_ruled_class_grain_root_leaves_the_gate(conn, ingest_run_id):
    """Either decision retires the question -- the widened arm must honour the
    policy table exactly as the moiety arm does, or a curator answers and is asked
    again next release."""
    root = _wide_class(conn, ingest_run_id, "N0000001200", 21)
    subject = _a_class(conn, ingest_run_id, code="N0000001300", name="subj [MoA]")
    interactions.add_class_pair_contraindication(
        conn, subject, root, "CI_MoA", "ONCHIGH", ingest_run_id)
    # ASSERTED PRESENT FIRST. Asserting only the disappearance would pass vacuously
    # while the class arm did not exist at all -- a gate that cannot fail is the
    # thing issues 74, 66 and 76 were each filed about.
    assert conn.execute(
        "SELECT count(*) FROM drugref.gap_unreviewed_expansion_root "
        "WHERE class_uuid = %s", (root,)).fetchone() == (1,)
    interactions.record_expansion_decision(
        conn, "MED-RT", "N0000001200", "deny", class_name="Wide", rationale="abstract",
        reviewed_by="test", reviewed_against="2026.07.06")
    assert conn.execute(
        "SELECT count(*) FROM drugref.gap_unreviewed_expansion_root "
        "WHERE class_uuid = %s", (root,)).fetchone() == (0,)


# ============================================================================
# 5. curated_target_unresolved -- the third arm (#90)
# ============================================================================


def test_a_vanished_class_pair_candidate_is_reported(conn, ingest_run_id):
    """The operator check that catches a rebuild dropping something a curator had
    already graded. The class grain had the same failure mode and none of the
    protection: a live `curated_class_interaction` whose candidate disappeared was
    reported to nobody, while the equivalent moiety row was reported."""
    subject_class, object_class, _s, _o = _a_graded_class_rule(
        conn, ingest_run_id, subject_code="N0000001400", object_code="N0000001500",
        subject_members=[("TESTUNII41", "s")], object_members=[("TESTUNII42", "o")])
    conn.execute("DELETE FROM drugref.class_pair_contraindication "
                 "WHERE subject_class_uuid = %s", (subject_class,))
    rows = conn.execute(
        "SELECT target_table, subject_class, object_uuid, relationship "
        "FROM drugref.curated_target_unresolved").fetchall()
    assert rows == [("curated_class_interaction", subject_class, object_class,
                     "CI_MoA")]


def test_the_moiety_arms_keep_their_own_columns(conn, a_graded_rule):
    """`subject_class` is a TRAILING ADD (db/030's precedent), so the two existing
    arms must be unchanged: `subject_moiety` still carries their subject and the new
    column is NULL. `target_table` is the discriminator and always was."""
    curation.record_interaction_judgement(
        conn, a_graded_rule["subject"], a_graded_rule["class"], "CI_MoA", True,
        severity="major", evidence_grade="established", reviewed_by="test",
        reviewed_against="2026.07.06")
    conn.execute("DELETE FROM drugref.class_contraindication "
                 "WHERE subject_moiety_uuid = %s", (a_graded_rule["subject"],))
    rows = conn.execute(
        "SELECT target_table, subject_moiety, subject_class "
        "FROM drugref.curated_target_unresolved").fetchall()
    assert rows == [("curated_interaction", a_graded_rule["subject"], None)]


def test_the_status_reader_carries_the_class_subject(conn, ingest_run_id):
    """`curation.unresolved_targets` is the view's only consumer (issue 76). A third
    arm the reader does not project is a detector that still reports nothing."""
    subject_class, object_class, _s, _o = _a_graded_class_rule(
        conn, ingest_run_id, subject_code="N0000001600", object_code="N0000001700",
        subject_members=[("TESTUNII51", "s")], object_members=[("TESTUNII52", "o")])
    conn.execute("DELETE FROM drugref.class_pair_contraindication "
                 "WHERE subject_class_uuid = %s", (subject_class,))
    orphans = curation.unresolved_targets(conn)
    assert len(orphans) == 1
    assert orphans[0].target_table == "curated_class_interaction"
    assert orphans[0].subject_class == subject_class
    assert orphans[0].subject_moiety is None


# ============================================================================
# 6. curated_ddi_pair -- severity_rank and the stated precedence (#97)
# ============================================================================


def _a_pair_graded_by_both_grains(conn, ingest_run_id, *, moiety_severity,
                                  class_severity):
    """ONE drug pair reachable by BOTH grains, graded differently -- #97's
    reproduction, in miniature.

    The moiety-grain rule names the subject drug directly; the class-grain rule names
    the class that same drug is filed under. Both single-live guards are satisfied
    (the rows live in different tables, one live row each), so nothing in the floor
    catches it -- which is exactly why a precedence rule is needed rather than a
    constraint.
    """
    subject_class, object_class, subjects, objects = _a_graded_class_rule(
        conn, ingest_run_id, subject_code="N0000002000", object_code="N0000002100",
        subject_members=[("TESTUNII61", "subject-drug")],
        object_members=[("TESTUNII62", "partner-drug")],
        severity=class_severity)
    conn.execute(
        "INSERT INTO drugref.class_contraindication (subject_moiety_uuid, "
        "object_class_uuid, relationship, source, ingest_run) "
        "VALUES (%s, %s, 'CI_MoA', 'MED-RT', %s)",
        (subjects[0], object_class, ingest_run_id))
    curation.record_interaction_judgement(
        conn, subjects[0], object_class, "CI_MoA", True, severity=moiety_severity,
        evidence_grade="established", reviewed_by="test",
        reviewed_against="2026.07.06")
    return subjects[0], objects[0], subject_class, object_class


@pytest.mark.parametrize("severity,rank", sorted(EXPECTED_SEVERITY_RANK.items()))
def test_curated_ddi_pair_carries_the_severity_rank(conn, ingest_run_id, severity,
                                                    rank):
    """The precedence rule has to be IMPLEMENTABLE by a consumer. `severity` is text,
    so `ORDER BY severity` is alphabetical nonsense ('contraindicated' < 'major' <
    'minor' < 'moderate') -- which would put `minor` above `moderate`."""
    _a_graded_class_rule(
        conn, ingest_run_id, subject_code="N0000002200", object_code="N0000002300",
        subject_members=[("TESTUNII71", "s")], object_members=[("TESTUNII72", "o")],
        severity=severity)
    assert conn.execute(
        "SELECT DISTINCT severity_rank FROM drugref.curated_ddi_pair "
        "WHERE rule_grain = 'class_rule'").fetchone() == (rank,)


def test_the_most_severe_row_wins_when_the_grains_disagree(conn, ingest_run_id):
    """#97's failure, answered. A prescribing client doing `SELECT severity ... LIMIT
    1` got an arbitrary answer between the two grains, and WHICHEVER IT TOOK MIGHT BE
    THE LOWER ONE -- which inverts the "fewer rows is the harm direction" reasoning
    `UNION ALL` was chosen for. The documented order must put the more severe first.
    """
    subject, partner, _sc, _oc = _a_pair_graded_by_both_grains(
        conn, ingest_run_id, moiety_severity="moderate",
        class_severity="contraindicated")
    row = conn.execute(
        "SELECT severity, rule_grain FROM drugref.curated_ddi_pair "
        "WHERE subject_moiety = %s AND partner_moiety = %s "
        "ORDER BY severity_rank, (rule_grain = 'moiety_rule') DESC LIMIT 1",
        (subject, partner)).fetchone()
    assert row == ("contraindicated", "class_rule")


def test_a_tie_on_severity_prefers_the_moiety_grain(conn, ingest_run_id):
    """The tiebreak is SPECIFICITY: with nothing to choose on severity, the rule that
    names an actual drug carries the better `mechanism`/`management` text than the
    rule that names its whole class."""
    subject, partner, _sc, _oc = _a_pair_graded_by_both_grains(
        conn, ingest_run_id, moiety_severity="major", class_severity="major")
    row = conn.execute(
        "SELECT rule_grain FROM drugref.curated_ddi_pair "
        "WHERE subject_moiety = %s AND partner_moiety = %s "
        "ORDER BY severity_rank, (rule_grain = 'moiety_rule') DESC LIMIT 1",
        (subject, partner)).fetchone()
    assert row == ("moiety_rule",)


def test_both_rows_still_appear(conn, ingest_run_id):
    """PRECEDENCE IS AN ORDER, NOT A FILTER. Dropping the losing row would make the
    view state less than it knows, and fewer rows is the harm direction for a
    contraindication -- the reason `curated_ddi_pair` is a `UNION ALL` at all."""
    subject, partner, _sc, _oc = _a_pair_graded_by_both_grains(
        conn, ingest_run_id, moiety_severity="moderate",
        class_severity="contraindicated")
    assert conn.execute(
        "SELECT count(*) FROM drugref.curated_ddi_pair "
        "WHERE subject_moiety = %s AND partner_moiety = %s",
        (subject, partner)).fetchone() == (2,)


# ============================================================================
# 7. curated_grain_disagreement -- the reconciliation worklist (#97)
# ============================================================================


def test_two_grains_grading_one_pair_differently_are_reported(conn, ingest_run_id):
    """db/032's own preamble argues that avoiding "two rows stating one fact to
    disagree" is why the class grain exists at all, so leaving cross-grain
    disagreement unreconciled is the same defect one tier up. An ORDER makes the read
    deterministic; only a detector makes the disagreement finite work."""
    subject, _partner, subject_class, object_class = _a_pair_graded_by_both_grains(
        conn, ingest_run_id, moiety_severity="moderate",
        class_severity="contraindicated")
    rows = conn.execute(
        "SELECT moiety_rule_subject, class_rule_subject_class, relationship, "
        "moiety_severity, class_severity, overlapping_pair_count "
        "FROM drugref.curated_grain_disagreement").fetchall()
    assert rows == [(subject, subject_class, "CI_MoA", "moderate",
                     "contraindicated", 1)]


def test_agreeing_grains_are_not_reported(conn, ingest_run_id):
    """Two grains reaching one pair is NORMAL -- a class rule exists precisely to
    cover drugs a moiety rule also names. Only a DISAGREEMENT is a worklist item, or
    the queue is every overlap forever."""
    _a_pair_graded_by_both_grains(conn, ingest_run_id, moiety_severity="major",
                                  class_severity="major")
    assert conn.execute(
        "SELECT count(*) FROM drugref.curated_grain_disagreement").fetchone() == (0,)


def test_a_differing_evidence_grade_is_a_disagreement_too(conn, ingest_run_id):
    """Severity is not the only field a consumer reads. Two live drugref judgements
    calling one interaction `established` and `theoretical` disagree about the thing
    a prescriber weighs, even at equal severity."""
    subject_class, object_class, subjects, _objects = _a_graded_class_rule(
        conn, ingest_run_id, subject_code="N0000002400", object_code="N0000002500",
        subject_members=[("TESTUNII81", "s")], object_members=[("TESTUNII82", "o")],
        severity="major", evidence_grade="theoretical")
    conn.execute(
        "INSERT INTO drugref.class_contraindication (subject_moiety_uuid, "
        "object_class_uuid, relationship, source, ingest_run) "
        "VALUES (%s, %s, 'CI_MoA', 'MED-RT', %s)",
        (subjects[0], object_class, ingest_run_id))
    curation.record_interaction_judgement(
        conn, subjects[0], object_class, "CI_MoA", True, severity="major",
        evidence_grade="established", reviewed_by="test",
        reviewed_against="2026.07.06")
    assert conn.execute(
        "SELECT count(*) FROM drugref.curated_grain_disagreement").fetchone() == (1,)


def test_the_disagreement_grain_is_the_RULE_PAIR_not_the_drug_pair(conn,
                                                                   ingest_run_id):
    """Two rules can overlap on thousands of drug pairs -- SSRIs x MAOIs alone is
    ~2,263. A per-pair detector would report ONE curator decision thousands of times,
    and if it were ever promoted to a gap kind it would mint thousands of immortal
    question UUIDs for it."""
    subject_class, object_class, subjects, objects = _a_graded_class_rule(
        conn, ingest_run_id, subject_code="N0000002600", object_code="N0000002700",
        subject_members=[("TESTUNII91", "s-a")],
        object_members=[("TESTUNII92", "o-a"), ("TESTUNII93", "o-b")],
        severity="contraindicated")
    conn.execute(
        "INSERT INTO drugref.class_contraindication (subject_moiety_uuid, "
        "object_class_uuid, relationship, source, ingest_run) "
        "VALUES (%s, %s, 'CI_MoA', 'MED-RT', %s)",
        (subjects[0], object_class, ingest_run_id))
    curation.record_interaction_judgement(
        conn, subjects[0], object_class, "CI_MoA", True, severity="minor",
        evidence_grade="established", reviewed_by="test",
        reviewed_against="2026.07.06")
    rows = conn.execute(
        "SELECT overlapping_pair_count FROM drugref.curated_grain_disagreement"
    ).fetchall()
    assert rows == [(2,)], "two drug pairs, ONE rule-pair disagreement"


# ============================================================================
# 8. the class grain enters a signed release (#98)
# ============================================================================


def test_curated_class_interaction_is_a_signature_target_kind(conn):
    """A silently incomplete signed release is worse than a failed one: the signature
    attests to a set that does not contain what the operator believes it does."""
    row = conn.execute(
        "SELECT target_table, pk_column, payload_context "
        "FROM drugref.signature_target_kind "
        "WHERE target_kind = 'curated_class_interaction'").fetchone()
    assert row == ("curated_class_interaction", "curated_class_interaction_id",
                   "curated_class_interaction/v1")


def test_a_release_enumerates_the_class_grain(conn, ingest_run_id):
    """Registering the kind is half the fix. `releases.enumerate_live` iterates
    `_CURATED_KINDS`, and a kind absent from THAT list is absent from the manifest
    AND from the live side of the comparison -- so `verify_release` would call an
    incomplete release intact, which is #98's whole failure."""
    _a_graded_class_rule(
        conn, ingest_run_id, subject_code="N0000003000", object_code="N0000003100",
        subject_members=[("TESTUNIIA1", "s")], object_members=[("TESTUNIIA2", "o")])
    entries = releases.enumerate_live(conn)
    assert any(e.target_kind == "curated_class_interaction" for e in entries)


def test_the_class_grains_natural_key_renders_all_three_columns(conn, ingest_run_id):
    """The manifest PAIRS on `natural_key`, so it must identify the row across
    databases. Two of the three columns would fold every axis of one class pair onto
    one entry."""
    subject_class, object_class, _s, _o = _a_graded_class_rule(
        conn, ingest_run_id, subject_code="N0000003200", object_code="N0000003300",
        subject_members=[("TESTUNIIB1", "s")], object_members=[("TESTUNIIB2", "o")])
    target_id = conn.execute(
        "SELECT curated_class_interaction_id FROM drugref.curated_class_interaction "
        "WHERE subject_class_uuid = %s", (subject_class,)).fetchone()[0]
    assert releases.natural_key_of(
        conn, "curated_class_interaction", target_id) == (
            f"{subject_class}/{object_class}/CI_MoA")


def test_a_manifest_without_the_class_grain_reports_it_as_ADDED(
        conn, institutional_key, ingest_run_id):
    """#98's failure scenario, now honest. A manifest built before the grain was
    registered enumerated `curated_interaction` and `curated_condition` only, its
    `row_count` matched its own entries, and `verify_release` PASSED. It must now
    report the omitted grain rather than certify a set that does not contain it."""
    from drugref import release_verification

    releases.publish(conn, release_tag="v-empty",
                     published_by="test", private_key=institutional_key["private"],
                     key_fingerprint=institutional_key["fingerprint"])
    _a_graded_class_rule(
        conn, ingest_run_id, subject_code="N0000003400", object_code="N0000003500",
        subject_members=[("TESTUNIIC1", "s")], object_members=[("TESTUNIIC2", "o")])
    verdict = release_verification.verify_release(conn, "v-empty")
    assert any(kind == "curated_class_interaction" for kind, _key in verdict.added)
    assert not verdict.is_intact


# ============================================================================
# 9. drugref status -- the detectors reach a human (issue 76's standing rule)
# ============================================================================


def test_status_reports_the_class_grain(conn, ingest_run_id, capsys):
    """Every view above is half a feature. The other half is a consumer -- this
    project has now shipped three detectors with none (`expansion_policy_unresolved`,
    `curated_target_unresolved`, and the class grain's whole set)."""
    from drugref import cli_status

    _an_ungraded_class_rule(conn, ingest_run_id, subject_members=2, object_members=3)
    _an_ungraded_class_rule(conn, ingest_run_id, subject_code="N0000004000",
                            object_code="N0000004100", object_axis="has_PE")
    cli_status.print_class_grain_block(conn)
    out = capsys.readouterr().out
    assert "ungraded 1" in out
    assert "reaching no pair 1" in out


def test_an_emptied_tier_does_not_render_like_a_healthy_one(conn, ingest_run_id,
                                                            capsys):
    """ISSUE 111: the block's zeros need a DENOMINATOR, or they say nothing.

    THE SCENARIO. Per-source rebuilds are delete-and-rebuild (CLAUDE.md architecture
    invariants). An ONCHIGH re-ingest whose parser yields nothing -- upstream format
    change, truncated download, resolver regression -- empties the class tier.
    `loaded_release` still shows ONCHIGH loaded, `drugref status` exits 0, and before
    this round the class-grain block printed three zeros: BYTE-IDENTICAL to a healthy,
    fully-curated registry. The block reported only on rules that EXIST, so wiping every
    rule out silenced every detector at once and the silence read as health.

    The `class_rules_written` signal exists only in the ingest summary, at ingest time.
    `status` is the DURABLE check -- the one an operator runs afterwards, or tomorrow --
    and it had no equivalent.

    WHY `!=` IS THE ASSERTION and not a substring. The defect was literally that two
    states rendered the same, so the requirement is literally that they render
    differently; a substring check on the new wording would pass just as well if the two
    outputs had been made identical in some NEW way. The three `== 0` assertions above it
    are what stop that being trivially satisfiable: they establish that both states are
    all-zero on every detector, which is the premise that made the old output ambiguous.
    Without them a future change could satisfy `!=` by moving a detector off zero, which
    would be a different block, not a fixed one.

    THE COMPENSATING CONTROL DOES NOT COVER THIS: `curated_target_unresolved` fires on an
    emptied tier, but only if curated rows already exist. With none -- today's state, and
    the state of any node that has not begun curating -- an emptied tier is invisible.
    """
    from drugref import cli_status

    before = curation.class_grain_counts(conn)
    assert (before.ungraded, before.dead, before.disagreements) == (0, 0, 0)
    cli_status.print_class_grain_block(conn)
    emptied = capsys.readouterr().out

    _a_graded_class_rule(
        conn, ingest_run_id, subject_code="N0000006400", object_code="N0000006500",
        subject_members=[("TESTUNIID1", "s")], object_members=[("TESTUNIID2", "o")])

    after = curation.class_grain_counts(conn)
    assert (after.ungraded, after.dead, after.disagreements) == (0, 0, 0), (
        "the premise: a graded rule reaching a pair trips no detector, so both states "
        "are all-zero and only a denominator can tell them apart")
    cli_status.print_class_grain_block(conn)
    healthy = capsys.readouterr().out

    assert emptied != healthy, (
        "an emptied tier still renders identically to a healthy one")
    assert "class rules: 0" in emptied
    assert "class rules: 1" in healthy


def test_the_denominator_counts_a_rule_once_however_many_authorities_assert_it(
        conn, ingest_run_id):
    """The denominator has to be counted at the SAME grain as the numerators beside it.

    `class_pair_rule_reach` is keyed per source, so one clinical rule asserted by two
    authorities is two rows -- which is why `dead` already groups on the three
    natural-key columns (see the test below it). A denominator taken as a bare
    `count(*)` would read 2 while `ungraded`/`reaching no pair` read 1, and the operator
    diffing them would see a rule appear and disappear on a re-ingest that changed
    nothing but which authority happened to file it.

    This is db/018's own lesson ("one quantity stated twice is a quantity that will
    disagree") reaching the operator surface rather than the schema.
    """
    subject_class, object_class = _an_ungraded_class_rule(
        conn, ingest_run_id, subject_code="N0000006600", object_code="N0000006700")
    interactions.add_class_pair_contraindication(
        conn, subject_class, object_class, "CI_MoA", "MED-RT", ingest_run_id)

    counts = curation.class_grain_counts(conn)
    assert counts.total == 1, (
        f"one rule asserted by two authorities is ONE rule, got {counts.total}")
    assert counts.ungraded == 1


def test_an_unknown_uuid_is_not_a_class_grain_row(conn):
    """A guard on the fixtures above rather than on the schema: every assertion in
    this file scopes by a UUID the test itself minted, so a stale row from another
    test leaking through the session-scoped schema would show up as a failure here
    first."""
    assert conn.execute(
        "SELECT count(*) FROM drugref.class_pair_rule_reach "
        "WHERE subject_class_uuid = %s", (uuid.UUID(int=0),)).fetchone() == (0,)
    assert ids.mint_class_uuid("MED-RT", "N0000000401") is not None


# ============================================================================
# 10. the gaps a review round found -- three shapes no test reached
# ============================================================================


def test_a_superseded_CLASS_judgement_is_not_an_orphan(conn, ingest_run_id):
    """THE THIRD ARM'S LIVENESS FILTER, which shipped with no test at all.

    `test_a_superseded_judgement_is_not_an_orphan` pins this for db/029's two arms and
    its docstring records why it had to: deleting `WHERE c.superseded_by IS NULL` from
    both of them "survived the entire suite". db/035 added a third arm with the same
    clause and no equivalent test -- and deleting the clause from THAT arm survived the
    suite too, measured by mutation in this round's review.

    The consequence is not cosmetic. The overlay is append-only, so a curator CORRECTING
    a class ruling leaves its predecessor behind forever; without the filter every
    correction would raise a permanent phantom orphan, and `drugref status` would report
    a curator doing their job as a rebuild having broken something.

    THE CANDIDATE IS DELETED FOR THE REASON THE MOIETY TEST GIVES: with it still
    projected both rows resolve, the empty result is over-determined, and the assertion
    would hold whether or not the view filtered on anything.
    """
    subject_class, object_class, _s, _o = _a_graded_class_rule(
        conn, ingest_run_id, subject_code="N0000005000", object_code="N0000005100",
        subject_members=[("SUPUNII01", "s")], object_members=[("SUPUNII02", "o")],
        reviewed_against="2026.07.06")
    conn.execute("SET CONSTRAINTS ALL DEFERRED")
    curation.record_class_interaction_judgement(
        conn, subject_class, object_class, "CI_MoA", True, severity="minor",
        evidence_grade="probable", reviewed_by="test",
        reviewed_against="2026.08.01")
    conn.execute("SET CONSTRAINTS ALL IMMEDIATE")
    conn.execute("DELETE FROM drugref.class_pair_contraindication "
                 "WHERE subject_class_uuid = %s", (subject_class,))

    # THE GUARD THAT MAKES THE ASSERTION BELOW DISCRIMINATING, and without it this test
    # would pass on a fixture that never superseded anything. TWO rows must name the
    # vanished candidate for "exactly one is live" to be a claim about the filter rather
    # than about the row count. The schema is rebuilt from the migrations each session,
    # so the alternative -- mutating the view to watch this fail -- would mean editing
    # an applied migration, which the ledger forbids; stating the precondition in the
    # test is how this file gets the same evidence without that.
    assert conn.execute(
        "SELECT count(*) FROM drugref.curated_class_interaction "
        "WHERE subject_class_uuid = %s", (subject_class,)).fetchone() == (2,), (
        "the correction must have SUPERSEDED rather than replaced the predecessor")

    orphans = [o for o in curation.unresolved_targets(conn)
               if o.subject_class == subject_class]
    assert len(orphans) == 1, (
        "both rows name the vanished candidate, but only the LIVE one is an orphan -- "
        "reporting the superseded predecessor as well would make every correction look "
        "like breakage")
    assert orphans[0].reviewed_against == "2026.08.01", (
        "the row reported must be the CORRECTION, not the predecessor it replaced")


def test_one_class_rule_asserted_by_TWO_authorities_counts_once(conn, ingest_run_id):
    """THE MULTI-SOURCE GRAIN, and nothing in this file reached it.

    `class_pair_contraindication`'s primary key includes `source` and its CHECK admits
    both MED-RT and ONCHIGH, so one clinical rule asserted by two authorities is TWO
    candidate rows TODAY -- not hypothetically. Every other fixture here passes a single
    source, which makes `count(*)` and `count(DISTINCT ...)` coincide and hides three
    separate defects at once. All three were measured to survive a green suite in this
    round's review:

      * dropping `source` from the gap view's GROUP BY -- which the migration calls
        "load-bearing rather than tidy", because two rows on ONE gap_key mint one
        immortal question_uuid and the second silently overwrites the first's text;
      * `count(DISTINCT m.partner_moiety)` -> `count(*)` in curated_grain_disagreement;
      * the `SELECT DISTINCT` in the status block's dead-rule count.

    THIS TEST PINS THE FIRST AND THE THIRD; the second needs a graded pair and is
    covered by the disagreement tests above. Asserting BOTH the candidate count (2) and
    the question count (1) is what makes it non-vacuous -- asserting 1 alone would pass
    on a fixture that never wrote the second source at all.
    """
    subject_class, object_class = _an_ungraded_class_rule(
        conn, ingest_run_id, subject_code="N0000005200", object_code="N0000005300")
    interactions.add_class_pair_contraindication(
        conn, subject_class, object_class, "CI_MoA", "MED-RT", ingest_run_id)

    assert conn.execute(
        "SELECT count(*) FROM drugref.class_pair_contraindication "
        "WHERE subject_class_uuid = %s", (subject_class,)).fetchone() == (2,), (
        "the fixture must actually write two sources, or everything below is vacuous")
    assert conn.execute(
        "SELECT count(*) FROM drugref.gap_uncurated_class_interaction_rule "
        "WHERE subject_class = %s", (subject_class,)).fetchone() == (1,), (
        "one clinical rule is ONE question however many authorities assert it -- a "
        "per-source grain would mint one question_uuid and overwrite its own text")


def test_the_status_block_counts_a_two_source_dead_rule_once(conn, ingest_run_id,
                                                             capsys):
    """The operator half of the test above, and the reason the status block carries a
    `SELECT DISTINCT` its own comment has to justify.

    `class_pair_rule_reach` is keyed per source, so a dead rule asserted by two
    authorities is two rows there. A bare `count(*)` would tell the operator two rules
    reach nobody when one does -- and the line beside it (`ungraded class rules`) reads
    off a view that DOES group, so the two numbers would silently disagree about what a
    rule is.
    """
    from drugref import cli_status

    subject_class, object_class = _an_ungraded_class_rule(
        conn, ingest_run_id, subject_code="N0000005400", object_code="N0000005500",
        object_axis="has_PE")
    interactions.add_class_pair_contraindication(
        conn, subject_class, object_class, "CI_MoA", "MED-RT", ingest_run_id)

    counts = curation.class_grain_counts(conn)
    assert counts.dead == 1, (
        f"one dead rule asserted twice is ONE dead rule, got {counts.dead}")
    cli_status.print_class_grain_block(conn)
    out = capsys.readouterr().out
    # The denominator (issue 111) is counted the SAME way, so it too reads 1: a rule
    # asserted by two authorities is one rule on every number in the block.
    #
    # AND `ungraded` READS 0 ON THE SAME RULE, which is not a contradiction and is worth
    # spelling out because the reading looks wrong at a glance. This rule reaches no pair,
    # and `gap_uncurated_class_interaction_rule` deliberately withholds such a rule from
    # the worklist -- grading it could change nothing, which is #36's measured lesson
    # about a review gate asking a question no answer can change. So a dead rule counts
    # in the denominator and in `reaching no pair`, and NOT in `ungraded`. Before the
    # denominator existed there was nothing in the block to reconcile those two against.
    assert "class rules: 1 (ungraded 0, reaching no pair 1)" in out


def test_the_status_block_reports_a_cross_grain_disagreement(conn, ingest_run_id,
                                                             capsys):
    """THE THIRD LINE OF THE BLOCK, never once driven non-zero.

    `test_status_reports_the_class_grain` asserts the other two lines and the stub-driven
    CLI test asserts the zero text, so both the live `curated_grain_disagreement` query
    and its `**` warning branch were unexercised -- hardcoding `disagreements = 0`
    survived the whole suite, measured in this round's review.

    THE BANNER IS ASSERTED, not just the count, for the reason the orphan block's own
    test gives: "loudly" is the requirement, and an earlier version of that test passed
    with the loudness removed. The wording matters here specifically because it is what
    tells an operator that consumers take the MORE SEVERE grade -- a disagreement left
    standing is a broad class rule over-warning where a curator graded one drug milder.
    """
    from drugref import cli_status

    _a_pair_graded_by_both_grains(conn, ingest_run_id, moiety_severity="moderate",
                                  class_severity="contraindicated")
    counts = curation.class_grain_counts(conn)
    assert counts.disagreements == 1

    cli_status.print_class_grain_block(conn)
    out = capsys.readouterr().out
    assert "cross-grain disagreements: 1" in out
    assert "MORE SEVERE" in out, (
        "the count alone does not tell an operator which grade a consumer will take")


# ============================================================================
# 11. db/037 -- the arithmetic and the orientation the review round found
# ============================================================================


def _an_ungraded_self_pair_rule(conn, run_id, *, code, members):
    """A class x class rule whose two sides are the SAME class, ungraded.

    db/032 DECISION 2 deliberately permits this: QT-prolonging x QT-prolonging is a real
    ONC entry, and the class legitimately equals itself as a rule subject. Only the
    read-path expansion excludes the resulting identical-moiety pairs, which is exactly
    the exclusion `max_pair_count` could not see before db/037.

    `_a_graded_class_rule` can build the shape but always grades it, and the worklist's
    whole subject is the row nobody has graded yet.
    """
    klass = _a_class(conn, run_id, code=code, name=f"{code} [MoA]")
    for member_code, name in members:
        moiety = _a_moiety(conn, run_id, member_code, name)
        _file_member(conn, moiety, klass, run_id, relationship="has_MoA")
    interactions.add_class_pair_contraindication(
        conn, klass, klass, "CI_MoA", "ONCHIGH", run_id)
    return klass


def _reach(conn, subject_class, object_class):
    """(max_pair_count, shared_effective_member_count) for one rule."""
    return conn.execute(
        "SELECT max_pair_count, shared_effective_member_count "
        "FROM drugref.class_pair_rule_reach "
        "WHERE subject_class_uuid = %s AND object_class_uuid = %s",
        (subject_class, object_class)).fetchone()


def test_a_self_pair_rule_over_a_one_member_class_reaches_nobody(conn, ingest_run_id):
    """ISSUE 108, THE HEADLINE CASE: max_pair_count read 1 and the read path yields 0.

    `max_pair_count` was `subject_effective * object_effective`, while `curated_ddi_pair`
    additionally requires `subject_moiety <> partner_moiety`. For a self-pair rule over a
    class with N effective members the true reach is N*(N-1), so at N=1 the product says
    one pair and the expansion yields none.

    db/035's preamble defended the wrong direction -- "the exclusion cannot change 0 into
    non-zero, which is the only threshold anything downstream tests". True, and harmless.
    The hazardous direction is non-zero into zero, and it is this row.
    """
    klass = _an_ungraded_self_pair_rule(
        conn, ingest_run_id, code="N0000008200", members=[("TESTUNIIG1", "only-drug")])
    assert _reach(conn, klass, klass) == (0, 1), (
        "one member on both sides is one self-pair and no reachable pair")


def test_both_detectors_inverted_on_that_row_and_now_agree(conn, ingest_run_id):
    """The reason issue 108 is a migration and not a caveat: the SAME wrong number made
    the two detectors disagree with each other, each in its own harmful direction.

    * `gap_uncurated_class_interaction_rule` filters `HAVING max(max_pair_count) > 0`, so
      it ADMITTED this rule to the curator worklist and minted it an immortal
      `question_uuid` asking about "up to 1 drug pair(s)" -- #36's measured mistake, a
      review gate asking what no answer could change.
    * `drugref status` filters `WHERE max_pair_count = 0`, so it OMITTED the same rule --
      leaving it "ingested, graded, committed and reported successful while reaching zero
      patients", the exact failure db/035 is named for, reproduced by the detector built
      to catch it.

    Both are filters over the one column, which is why correcting the column corrects
    both and why db/035 called this view "THE ONE PLACE the class grain states a rule's
    reach". Asserted together so the pair cannot drift apart again.
    """
    klass = _an_ungraded_self_pair_rule(
        conn, ingest_run_id, code="N0000008300", members=[("TESTUNIIG2", "only-drug")])

    queued = conn.execute(
        "SELECT count(*) FROM drugref.gap_uncurated_class_interaction_rule "
        "WHERE subject_class = %s", (klass,)).fetchone()[0]
    assert queued == 0, "a rule no answer can change must not reach a curator"

    counts = curation.class_grain_counts(conn)
    assert counts.dead == 1, "and it must reach the operator, who can act on it"
    assert counts.ungraded == 0


@pytest.mark.parametrize("subject_code,object_code,subject_members,object_members,"
                         "expected_pairs,expected_shared", [
    # Disjoint classes: the product is already right, and this is the case that stops
    # the fix being a blanket subtraction.
    ("N0000008400", "N0000008500", [("TESTUNIIG3", "s1"), ("TESTUNIIG4", "s2")],
     [("TESTUNIIG5", "o1"), ("TESTUNIIG6", "o2"), ("TESTUNIIG7", "o3")], 6, 0),
    # Self-pair, N=3: 3*3 - 3 = 6, the shape db/032 DECISION 2 permits.
    ("N0000008600", "N0000008600",
     [("TESTUNIIG8", "a"), ("TESTUNIIG9", "b"), ("TESTUNIIH1", "c")], [], 6, 3),
    # Self-pair, N=1: the headline. 1*1 - 1 = 0.
    ("N0000008700", "N0000008700", [("TESTUNIIH2", "only")], [], 0, 1),
    # TWO DIFFERENT classes sharing one member -- 2*2 - 1 = 3. The general case the
    # self-pair is a special case OF, and not exotic: MED-RT files one drug under many
    # classes, so two related classes overlapping is ordinary.
    ("N0000008800", "N0000008900", [("TESTUNIIH3", "shared"), ("TESTUNIIH4", "s-only")],
     [("TESTUNIIH3", "shared"), ("TESTUNIIH5", "o-only")], 3, 1),
])
def test_max_pair_count_equals_what_the_read_path_actually_yields(
        conn, ingest_run_id, subject_code, object_code, subject_members,
        object_members, expected_pairs, expected_shared):
    """THE EQUIVALENCE, driven against the read path rather than against a restated
    formula -- which is the only way this can be evidence rather than a second copy.

    `class_pair_rule_reach` and `curated_ddi_pair` compute reach from opposite ends: one
    from membership cardinalities, the other by actually expanding both sides and
    excluding self-pairs. Asserting the column against a literal would pin arithmetic;
    asserting it against the expansion pins the CLAIM, which is that the number tells an
    operator how many drug pairs the rule reaches.

    The four cases are chosen so no single wrong implementation passes all of them: a
    bare product fails cases 2-4, a blanket `N*(N-1)` fails case 1, and subtracting only
    when the two classes are IDENTICAL -- the tempting special-case fix -- fails case 4.
    """
    subject_class, object_class, _s, _o = _a_graded_class_rule(
        conn, ingest_run_id, subject_code=subject_code, object_code=object_code,
        subject_members=subject_members, object_members=object_members)

    yielded = conn.execute(
        "SELECT count(*) FROM (SELECT DISTINCT subject_moiety, partner_moiety "
        "FROM drugref.curated_ddi_pair WHERE via_subject_class = %s AND via_class = %s "
        "AND relationship = 'CI_MoA') z", (subject_class, object_class)).fetchone()[0]

    assert yielded == expected_pairs, "the read path is the authority on reach"
    assert _reach(conn, subject_class, object_class) == (yielded, expected_shared)


def test_the_uncorrected_product_is_what_this_replaces(conn, ingest_run_id):
    """MUTATION, stated as arithmetic rather than by rewriting the view.

    Every assertion above is compatible with a view that computed the right answer by
    some other route, so this names the OLD number explicitly and requires it to differ.
    Without it, the four equivalence cases would still pass if a later change quietly
    reverted to the product for some inputs -- the shape db/034's regression took, where
    results stayed correct and only a plan changed.
    """
    klass = _an_ungraded_self_pair_rule(
        conn, ingest_run_id, code="N0000009100",
        members=[("TESTUNIIH6", "a"), ("TESTUNIIH7", "b")])
    row = conn.execute(
        "SELECT subject_effective_member_count, object_effective_member_count, "
        "max_pair_count, shared_effective_member_count "
        "FROM drugref.class_pair_rule_reach WHERE subject_class_uuid = %s",
        (klass,)).fetchone()
    subject_n, object_n, reach, shared = row
    assert (subject_n, object_n) == (2, 2)
    assert subject_n * object_n == 4, "db/035's number"
    assert (reach, shared) == (2, 2), "db/037's: 2*2 - 2, i.e. N*(N-1)"


def _a_pair_graded_in_opposite_orientations(conn, run_id, *, moiety_severity,
                                            class_severity):
    """ONE clinical drug pair, graded by both grains, stated the OPPOSITE way round.

    The class rule names (class-of-B, class-of-A) and expands to `(b, a)`; the moiety
    rule names (a, class-of-B) and expands to `(a, b)`. Same axis, same two drugs, two
    grades, two orientations -- and before db/037 the disagreement detector's join on
    (subject_moiety, partner_moiety) returned nothing for them.

    Nothing normalises orientation between the two candidate tiers and they come from
    DIFFERENT upstreams (MED-RT vs ONCHigh), so agreement was coincidence rather than
    invariant. That is what makes this an under-report rather than a stated limit.
    """
    subject_class, object_class, subjects, objects = _a_graded_class_rule(
        conn, run_id, subject_code="N0000009200", object_code="N0000009300",
        subject_members=[("TESTUNIIH8", "b-drug")],
        object_members=[("TESTUNIIH9", "a-drug")],
        severity=class_severity)
    b_drug, a_drug = subjects[0], objects[0]
    # The moiety rule points the other way: drug A against the class holding drug B.
    conn.execute(
        "INSERT INTO drugref.class_contraindication (subject_moiety_uuid, "
        "object_class_uuid, relationship, source, ingest_run) "
        "VALUES (%s, %s, 'CI_MoA', 'MED-RT', %s)",
        (a_drug, subject_class, run_id))
    curation.record_interaction_judgement(
        conn, a_drug, subject_class, "CI_MoA", True, severity=moiety_severity,
        evidence_grade="established", reviewed_by="test",
        reviewed_against="2026.07.06")
    return a_drug, b_drug, subject_class, object_class


def test_a_mirror_oriented_disagreement_is_reported(conn, ingest_run_id):
    """ISSUE 109. These rows are DIRECTIONAL -- db/006's own COMMENT on
    `ddi_candidate_pair` says so: *"A consumer asking 'do X and Y interact' MUST query
    both directions."* So a moiety rule on (a,b) and a class rule on (b,a) are ONE
    clinical pair on ONE axis with two grades, and the old join found nothing.

    THE READ PATH WAS NEVER AT RISK, which is why this is a worklist defect rather than
    a safety one: a consumer unioning both directions still gets most-severe-first. What
    under-reported is the reconciliation queue -- and db/035's own comment calls that
    queue "what keeps most-severe-wins from becoming permanent over-warning", so a
    disagreement nobody is ever asked to reconcile is the failure the view exists to
    prevent, arriving through the view itself.
    """
    a_drug, _b_drug, _sc, _oc = _a_pair_graded_in_opposite_orientations(
        conn, ingest_run_id, moiety_severity="minor",
        class_severity="contraindicated")

    rows = conn.execute(
        "SELECT moiety_severity, class_severity, overlapping_pair_count "
        "FROM drugref.curated_grain_disagreement WHERE moiety_rule_subject = %s",
        (a_drug,)).fetchall()
    assert rows == [("minor", "contraindicated", 1)]


def test_the_mirrored_case_is_invisible_to_the_old_join(conn, ingest_run_id):
    """THE ANTI-VACUITY CONTROL for the test above, and it is not optional.

    A row in `curated_grain_disagreement` is evidence of the new arm only if the OLD arm
    could not have produced it. Here the old join -- subject to subject, partner to
    partner -- is written out and required to return NOTHING for the same fixture, so
    the reported disagreement can only have come from the orientation normalisation.

    This project has shipped a negative assertion satisfiable two ways before (the
    superseded-orphan test, which held whether or not the view filtered), and the rule it
    bought is: before trusting a result, name every reason it could occur.
    """
    _a_pair_graded_in_opposite_orientations(
        conn, ingest_run_id, moiety_severity="minor",
        class_severity="contraindicated")

    same_orientation = conn.execute(
        "SELECT count(*) FROM drugref.curated_ddi_pair m "
        "JOIN drugref.curated_ddi_pair c "
        "  ON c.subject_moiety = m.subject_moiety "
        " AND c.partner_moiety = m.partner_moiety "
        " AND c.relationship = m.relationship "
        "WHERE m.rule_grain = 'moiety_rule' AND c.rule_grain = 'class_rule'"
    ).fetchone()[0]
    assert same_orientation == 0, (
        "db/035's join must find nothing here, or the test above proves nothing")
    assert conn.execute(
        "SELECT count(*) FROM drugref.curated_grain_disagreement").fetchone()[0] == 1


def test_agreeing_grades_are_not_a_disagreement_in_either_orientation(conn,
                                                                      ingest_run_id):
    """The normalisation must not turn the view into "every pair both grains touch".

    `curated_grain_disagreement` is EXPECTED EMPTY on a healthy registry, so a widening
    that made it report agreement as well would flood the worklist it exists to keep
    actionable -- and the flood would look like the feature working.
    """
    _a_pair_graded_in_opposite_orientations(
        conn, ingest_run_id, moiety_severity="major", class_severity="major")
    assert conn.execute(
        "SELECT count(*) FROM drugref.curated_grain_disagreement").fetchone()[0] == 0
