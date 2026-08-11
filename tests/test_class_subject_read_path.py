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

from drugref import curation, ids, interactions

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
