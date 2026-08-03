# tests/test_expansion_policy.py
"""The descendant-expansion policy (Plan B, db/010).

`ddi_candidate_pair` expands a class-level contraindication over the class DAG,
because for a contraindication FEWER ROWS IS THE UNSAFE DIRECTION -- a rule saying
"not with anything that decreases coagulation activity" reached dabigatran and
missed warfarin, apixaban, aspirin, every heparin and every thrombolytic.

But a handful of MED-RT PE classes are abstract organ-system buckets ("Hematologic
Activity Alteration") that assert no specific effect, and expanding those produces
fan-out rather than recall. Which ones they are is a CLINICAL JUDGEMENT, so it is
DATA in this table -- reviewable by a pharmacist, diffable, and per-release
auditable -- rather than a constant buried in a view.

TWO THINGS THIS FILE PINS THAT ARE EASY TO GET WRONG:

1. `allow` is NOT the same as no row. Absent means UNREVIEWED, and an unreviewed
   large root is reported by gap_unreviewed_expansion_root. Three of the fourteen
   roots the discovery threshold found are large AND legitimate, so they carry an
   explicit `allow` -- otherwise they would sit on the worklist forever.
2. The table is NOT a rebuildable projection. Every other MED-RT-keyed table in
   drugref is dropped and rebuilt per release; this one holds curator judgement and
   an ingest must never wipe it.
"""
import re

import pytest
import psycopg

from drugref import db, ids

# MED-RT NUIs are 'N' + 10 digits. Checked because the seed carries them as
# literals seen by a human once, and a typo would silently disable one deny.
MEDRT_NUI = re.compile(r"^N\d{10}$")

# The ten unambiguous "<system> Activity Alteration" buckets: each names a system
# that is affected, never an effect that accumulates. A contraindication against
# "anything that alters hematologic activity" is not something a prescriber can act
# on. Frozen here as a literal set so a silent edit to the seed fails this test.
ABSTRACT_BUCKETS = {
    "N0000009036",  # Endocrine Activity Alteration [PE]
    "N0000009832",  # Renal/Urological Activity Alteration [PE]
    "N0000009069",  # Hemic/Lymphatic Activity Alteration [PE]
    "N0000009065",  # Hematologic Activity Alteration [PE]
    "N0000008331",  # Cardiovascular Activity Alteration [PE]
    "N0000009027",  # Electrical Activity Alteration [PE]
    "N0000009839",  # Respiratory/Pulmonary Activity Alteration [PE]
    "N0000009070",  # Hemostasis Alteration [PE]
    "N0000009739",  # Lipid Metabolism Alteration [PE]
    "N0000009020",  # Dermatologic Activity Alteration [PE]
}
# Denied on the evidence of its SUBTREE rather than of its name: it does name a
# direction and a function, but 'Acquired Immunity [PE]' (1,109 drugs -- in effect
# every vaccine) sits beneath it, which is not "increased immunologic activity" in
# the additive-harm sense. 33 direct members fan out to 1,313.
IMMUNOLOGIC = "N0000175551"
DENIED = ABSTRACT_BUCKETS | {IMMUNOLOGIC}

# Large enough to be FOUND by the >20-descendant-class discovery heuristic, and
# legitimate under the qualitative test ("does it name the direction and the
# function?"), so reviewed and explicitly allowed.
ALLOWED = {
    "N0000008663",  # Decreased Immunologically Active Molecule Activity: 35 -> 327
    "N0000009908",  # Vasoconstriction: 54 -> 119, only Arterial/Venous beneath it
    "N0000175651",  # Increased Sympathetic Activity: 16 -> 16, all 21 children empty
}


def _decisions(conn) -> dict[str, str]:
    """The MED-RT decisions that are LIVE. Since db/027 the table holds history too,
    and a dict over every row would silently keep whichever one came last."""
    return dict(conn.execute(
        "SELECT source_code, decision FROM drugref.class_expansion_policy "
        "WHERE source = 'MED-RT' AND superseded_by IS NULL").fetchall())


def test_the_seed_holds_the_fourteen_roots_the_measurement_found(conn):
    """Exactly the 14 CI object classes with more than 20 descendant classes in the
    2026.07.06 release -- all PE, not one MoA, which is the finding that made a named
    list rather than a size threshold the right mechanism."""
    assert _decisions(conn) == ({nui: "deny" for nui in DENIED}
                                | {nui: "allow" for nui in ALLOWED})


def test_every_seeded_root_carries_a_reviewable_justification(conn):
    """The whole reason this is a table: a pharmacist must be able to read a row and
    judge it. A NUI alone is unreviewable, so the class name and the rationale are
    part of the row, not of a commit message."""
    for code, name, rationale, by in conn.execute(
            "SELECT source_code, class_name, rationale, reviewed_by "
            "FROM drugref.class_expansion_policy").fetchall():
        assert MEDRT_NUI.match(code), f"{code} is not a MED-RT NUI"
        assert name.strip() and rationale.strip() and by.strip()


def test_allow_is_a_decision_and_not_merely_the_absence_of_a_deny(conn):
    """Absent means UNREVIEWED and lands on the worklist; `allow` means a human
    looked and said expand. Vasoconstriction is the case: large enough for the
    discovery heuristic to flag, but it names a direction and a function and has only
    Arterial/Venous beneath it."""
    assert _decisions(conn)["N0000009908"] == "allow"


def test_an_unrecognised_decision_value_is_refused(conn):
    """The views branch on these literals. A row spelled 'denied' or 'no' would read as
    neither deny nor allow and silently expand a bucket somebody meant to stop.
    THREE values are legal since db/027 -- `withdrawn` joined them (#35) -- and this
    test is about the closed vocabulary, not about how many members it has."""
    with pytest.raises(psycopg.errors.CheckViolation):
        conn.execute(
            "INSERT INTO drugref.class_expansion_policy (source, source_code, decision, "
            "class_name, rationale, reviewed_by, reviewed_against) "
            "VALUES ('MED-RT', 'N0000000999', 'maybe', 'X', 'y', 'test', '2026.07.06')")


def test_a_policy_row_cannot_name_an_unknown_authority(conn):
    """db/012. Every other `source` column in the schema is CHECK-constrained to the
    known authority spellings (substance_class in db/003, class_contraindication in
    db/004); db/010 left this one free text. The join is on (source, source_code), so
    'MEDRT' inserts cleanly and then matches no class for ever -- a deny that reads as
    working and denies nothing. expansion_policy_unresolved would list it, but a
    constraint refuses it at the point the typo is made."""
    with pytest.raises(psycopg.errors.CheckViolation):
        conn.execute(
            "INSERT INTO drugref.class_expansion_policy (source, source_code, decision, "
            "class_name, rationale, reviewed_by, reviewed_against) "
            "VALUES ('MEDRT', 'N0000000997', 'deny', 'X', 'y', 'test', '2026.07.06')")


def test_a_policy_row_cannot_be_filed_without_a_rationale(conn):
    """Every other column is supplied, so this isolates the rationale constraint
    rather than tripping over whichever NOT NULL happens to be checked first."""
    with pytest.raises(psycopg.errors.NotNullViolation, match="rationale"):
        conn.execute(
            "INSERT INTO drugref.class_expansion_policy (source, source_code, decision, "
            "class_name, reviewed_by, reviewed_against) "
            "VALUES ('MED-RT', 'N0000000998', 'deny', 'X', 'test', '2026.07.06')")


def test_ci_predicates_expand_descendants_unless_told_otherwise(conn):
    """The per-predicate switch db/010 adds to ci_axis. Slice 5b lands predicates
    over a MeSH disease vocabulary whose tree has a very different shape, so WHETHER
    a predicate expands is declared beside WHAT it expands over -- the same
    one-place-to-declare-it discipline db/006 established."""
    assert conn.execute(
        "SELECT relationship, expands_descendants FROM drugref.ci_axis "
        "ORDER BY relationship").fetchall() == [("CI_MoA", True), ("CI_PE", True)]


def test_a_second_apply_does_not_stomp_a_locally_revised_decision(_migrated, conn):
    """The property a node operator depends on: CURATOR JUDGEMENT SURVIVES A DEPLOY.

    This is the table no ingest clears, and the seed is drugref's opinion at FIRST
    INSTALL rather than a value re-imposed on every startup -- so an operator who
    reviews `Vasoconstriction`, disagrees, and denies it must still find it denied
    after the next migration run. Two mechanisms hold it up -- the ledger
    (db.apply_migrations never re-runs an applied file) and the seed's own ON CONFLICT
    DO NOTHING -- so what this test catches is a future migration that RE-SEEDS the
    table unconditionally, or one that upgrades that clause to DO UPDATE. Either
    would silently reinstate drugref's opinion over the operator's.

    Note db/010's comment justifies the ON CONFLICT by saying migrations are "replayed
    whole", which the ledger has since made untrue -- the clause is still right, for
    the reason above rather than the one stated.

    Since db/027 a revision is an INSERT that supersedes, not an in-place UPDATE, so
    this drops the before/after row-count assertion the old version carried: a
    legitimate supersession now legitimately ADDS a row, and the assertion it was
    standing in for -- a re-seed silently reinstating drugref's opinion -- is exactly
    what `_decisions(conn)[revised] == "deny"` already tests (a re-seed could only win
    by superseding the operator's live row, which would show up right there). The
    seed's exact contents are pinned by
    test_the_seed_holds_the_fourteen_roots_the_measurement_found instead.

    apply_migrations commits, so both the revision and the restore go through
    _revise(), which commits too -- the same reason test_db.py's replay tests clean up
    after themselves. The restore is a THIRD row, not a rollback or an UPDATE back to
    `allow`: nothing can be deleted or revised in place any more, so undoing this
    test's revision means recording a further correction, not erasing the one it made.
    """
    revised = "N0000009908"                    # Vasoconstriction, seeded as `allow`

    def _revise(decision, rationale):
        """Express an operator's revision the only way db/027 allows: insert, then
        point whatever was live at the new row. Task 3 replaces this with
        interactions.record_expansion_decision -- the point of having a writer."""
        new_id = conn.execute(
            "INSERT INTO drugref.class_expansion_policy (source, source_code, "
            "decision, class_name, rationale, reviewed_by, reviewed_against) VALUES "
            "('MED-RT', %s, %s, 'Vasoconstriction [PE]', %s, 'test', '2026.07.06') "
            "RETURNING policy_id", (revised, decision, rationale)).fetchone()[0]
        conn.execute(
            "UPDATE drugref.class_expansion_policy SET superseded_by = %s "
            "WHERE source = 'MED-RT' AND source_code = %s AND superseded_by IS NULL "
            "AND policy_id <> %s", (new_id, revised, new_id))
        conn.commit()

    _revise("deny", "an operator disagrees with the seed")
    try:
        with psycopg.connect(_migrated) as c:
            db.apply_migrations(c)
        assert _decisions(conn)[revised] == "deny", "a deploy overwrote curator judgement"
    finally:
        _revise("allow", "restoring the seeded judgement after the test")


def test_a_root_the_release_no_longer_defines_is_reported_not_silent(conn):
    """The other half of the rot problem. gap_unreviewed_expansion_root catches a NEW
    abstract root; this view catches the opposite -- a policy row whose class upstream
    re-keyed or withdrew. Left silent, a deny that matches nothing looks exactly like
    a deny that is working.

    Both directions are asserted on rows this test controls. Which of the 14 SEEDED
    roots resolve depends on what the orchestrator modules have committed into the
    shared test database (the MED-RT fixture happens to carry two of them), and a test
    whose outcome depends on module ordering is worse than no test.
    """
    run_id = conn.execute(
        "INSERT INTO drugref.ingest_run "
        "(source, upstream_release, source_checksum, writer) "
        "VALUES ('MED-RT', 'test', 'deadbeef', 'medrt_run') RETURNING ingest_run_id"
    ).fetchone()[0]
    # A decision about a class the registry does not hold: reported.
    conn.execute(
        "INSERT INTO drugref.class_expansion_policy (source, source_code, decision, "
        "class_name, rationale, reviewed_by, reviewed_against) VALUES "
        "('MED-RT', 'N0000999999', 'deny', 'Withdrawn Bucket [PE]', 'x', 'test', 'r')")
    # A seeded decision whose class IS present: not reported.
    hematologic = ids.mint_class_uuid("MED-RT", "N0000009065")
    conn.execute(
        "INSERT INTO drugref.substance_class (class_uuid, source, source_code, "
        "published_code, class_name, concept_type, first_seen_ingest) "
        "VALUES (%s, 'MED-RT', 'N0000009065', 'N0000009065', "
        "'Hematologic Activity Alteration [PE]', 'PE', %s) ON CONFLICT DO NOTHING",
        (hematologic, run_id))

    unresolved = {r[0] for r in conn.execute(
        "SELECT source_code FROM drugref.expansion_policy_unresolved").fetchall()}
    assert "N0000999999" in unresolved
    assert "N0000009065" not in unresolved


# ---- the append-only floor (db/027, #35) ------------------------------------
#
# The table gates recall: one UPDATE of `decision` removes thousands of candidate
# pairs with no audit row and nothing reporting it. Since db/027 it carries Plan C's
# overlay floor, so a revision is an INSERT that supersedes -- history survives, and
# what drugref believed when a pair was withheld stays answerable.


def _own_row(conn, code, decision="deny", rationale="seeded by a test"):
    """Insert a policy row this test owns, and return its policy_id.

    Never revise a SEEDED row here: the conn fixture rolls back, but a row committed
    by accident could not be deleted afterwards -- that is the point of the floor.
    """
    return conn.execute(
        "INSERT INTO drugref.class_expansion_policy (source, source_code, decision, "
        "class_name, rationale, reviewed_by, reviewed_against) "
        "VALUES ('MED-RT', %s, %s, 'Test Bucket [PE]', %s, 'test', '2026.07.06') "
        "RETURNING policy_id", (code, decision, rationale)).fetchone()[0]


def test_a_policy_decision_cannot_be_deleted(conn):
    """#35's second asymmetry. Every other clinically-consequential curated table
    refuses DELETE; this one gated recall with no floor at all."""
    _own_row(conn, "N0000100001")
    with pytest.raises(psycopg.errors.RaiseException, match="append-only"):
        conn.execute("DELETE FROM drugref.class_expansion_policy "
                     "WHERE source_code = 'N0000100001'")


def test_a_decision_cannot_be_revised_in_place(conn):
    """#35's first asymmetry, and the whole point of the round: flipping `decision`
    used to overwrite the rationale that justified the previous judgement."""
    _own_row(conn, "N0000100002", decision="deny")
    with pytest.raises(psycopg.errors.RaiseException, match="only superseded_by may change"):
        conn.execute("UPDATE drugref.class_expansion_policy SET decision = 'allow' "
                     "WHERE source_code = 'N0000100002'")


def test_supersession_is_one_way_and_set_once(conn):
    """Un-setting would resurrect a corrected-away judgement as live; re-pointing
    would rewrite history a consumer may already have acted on."""
    old = _own_row(conn, "N0000100003")
    new = _own_row(conn, "N0000100003", decision="allow")
    conn.execute("UPDATE drugref.class_expansion_policy SET superseded_by = %s "
                 "WHERE policy_id = %s", (new, old))
    with pytest.raises(psycopg.errors.RaiseException, match="one-way"):
        conn.execute("UPDATE drugref.class_expansion_policy SET superseded_by = NULL "
                     "WHERE policy_id = %s", (old,))


def test_a_correction_must_point_at_a_later_row(conn):
    """The chain strictly increases, so it can never close into a cycle -- which would
    make BOTH judgements vanish from every live read at once, silently."""
    first = _own_row(conn, "N0000100004")
    second = _own_row(conn, "N0000100004", decision="allow")
    with pytest.raises(psycopg.errors.RaiseException, match="LATER row"):
        conn.execute("UPDATE drugref.class_expansion_policy SET superseded_by = %s "
                     "WHERE policy_id = %s", (first, second))


def test_a_correction_must_keep_the_same_class(conn):
    """A correction replaces a judgement about THIS class, not a different one.
    Pointing across classes is a merge, and there are no merge semantics here."""
    old = _own_row(conn, "N0000100005")
    other = _own_row(conn, "N0000100006")
    with pytest.raises(psycopg.errors.RaiseException, match="same source_code"):
        conn.execute("UPDATE drugref.class_expansion_policy SET superseded_by = %s "
                     "WHERE policy_id = %s", (other, old))


def test_two_live_decisions_for_one_class_are_refused_at_commit(conn):
    """The natural key stopped being unique in db/027 -- history rows carry it by
    definition -- so 'at most one LIVE row per class' is a DEFERRED trigger instead.

    SET CONSTRAINTS ALL IMMEDIATE forces the check that would otherwise fire at COMMIT,
    which the conn fixture never reaches: A TEST THAT NEVER COMMITS PROVES NOTHING.
    Note that statement switches the mode for the REST of this transaction.
    """
    _own_row(conn, "N0000100007")
    _own_row(conn, "N0000100007", decision="allow")   # nothing superseded
    with pytest.raises(psycopg.errors.RaiseException, match="live rows for natural key"):
        conn.execute("SET CONSTRAINTS ALL IMMEDIATE")


def test_the_live_key_index_exists(conn):
    """db/023 measured that this partial index is what keeps the single-live trigger
    linear rather than quadratic (2,000 rows: 5,773 ms -> 42 ms). NOTHING BUT THE
    TRIGGER READS IT, so it looks unused to a catalog sweep and is asserted by name."""
    assert conn.execute(
        "SELECT count(*) FROM pg_indexes WHERE schemaname = 'drugref' "
        "AND indexname = 'class_expansion_policy_live_key'").fetchone()[0] == 1
