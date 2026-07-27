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
    return dict(conn.execute(
        "SELECT source_code, decision FROM drugref.class_expansion_policy "
        "WHERE source = 'MED-RT'").fetchall())


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


def test_a_third_decision_value_is_refused(conn):
    """The view branches on this literal; a row spelled 'denied' or 'no' would read
    as neither deny nor allow and silently expand a bucket somebody meant to stop."""
    with pytest.raises(psycopg.errors.CheckViolation):
        conn.execute(
            "INSERT INTO drugref.class_expansion_policy (source, source_code, decision, "
            "class_name, rationale, reviewed_by, reviewed_against) "
            "VALUES ('MED-RT', 'N0000000999', 'maybe', 'X', 'y', 'test', '2026.07.06')")


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


def test_replaying_the_migrations_neither_errors_nor_duplicates_the_seed(_migrated):
    """Migrations are replayed whole on every apply_migrations, so a seed that is not
    ON CONFLICT DO NOTHING would either raise or double the table."""
    with psycopg.connect(_migrated) as c:
        db.apply_migrations(c)
        assert c.execute("SELECT count(*) FROM drugref.class_expansion_policy"
                         ).fetchone()[0] == len(DENIED) + len(ALLOWED)


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
        "INSERT INTO drugref.ingest_run (source, upstream_release, source_checksum) "
        "VALUES ('MED-RT', 'test', 'deadbeef') RETURNING ingest_run_id").fetchone()[0]
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
