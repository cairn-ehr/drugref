# tests/test_gap_views.py
"""The derived gap views (Plan A, db/008).

Design rule: A GAP IS A QUERY, NEVER A REPORT. Generated documents are stale on
write and nobody trusts them; as views these are always current, shrink visibly as
curation lands, and make "how much do we not know" a number watchable per release.

Two of the three are pure views over tables that already exist. The third is not,
and that is worth stating plainly: the unmatched RxCUIs were only ever COUNTED
(`unmatched_rxcuis=len(unmatched)`) and the identities discarded, so making them
queryable needed a persisted table and a change to the ingest path -- not a view.
"""
import uuid

import pytest

from drugref import ids


@pytest.fixture(autouse=True)
def _isolate(conn):
    """The gap views read the WHOLE registry, so any moiety or class another module
    committed shows up as a gap here and makes these counts non-deterministic. The
    orchestrator tests (test_medrt_run, test_ingest_run) commit internally, so the
    conn fixture's rollback cannot isolate against them -- truncate first, exactly as
    those modules do for the same reason."""
    conn.execute("TRUNCATE drugref.class_contraindication, drugref.class_membership, "
                 "drugref.class_parent, drugref.substance_class, drugref.identity_claim, "
                 "drugref.substance_moiety, drugref.ingest_run RESTART IDENTITY CASCADE")
    conn.commit()
    yield


def _run(conn, source="MED-RT"):
    return conn.execute(
        "INSERT INTO drugref.ingest_run (source, upstream_release, source_checksum) "
        "VALUES (%s, 'test', 'deadbeef') RETURNING ingest_run_id", (source,)).fetchone()[0]


def _class(conn, run_id, code, cty="PE", name=None):
    cu = ids.mint_class_uuid("MED-RT", code)
    conn.execute(
        "INSERT INTO drugref.substance_class (class_uuid, source, source_code, "
        "published_code, class_name, concept_type, first_seen_ingest) "
        "VALUES (%s, 'MED-RT', %s, %s, %s, %s, %s)",
        (cu, code, code, name or f"Class {code} [{cty}]", cty, run_id))
    return cu


def _moiety(conn, run_id, name="testium"):
    m = uuid.uuid4()
    conn.execute("INSERT INTO drugref.substance_moiety "
                 "(moiety_uuid, display_name, first_seen_ingest) VALUES (%s, %s, %s)",
                 (m, name, run_id))
    return m


def _member(conn, run_id, moiety, klass, relationship="has_PE"):
    conn.execute("INSERT INTO drugref.class_membership "
                 "(moiety_uuid, class_uuid, relationship, ingest_run) "
                 "VALUES (%s, %s, %s, %s)", (moiety, klass, relationship, run_id))


def _parent(conn, run_id, child, parent):
    conn.execute("INSERT INTO drugref.class_parent "
                 "(child_class_uuid, parent_class_uuid, ingest_run) VALUES (%s, %s, %s)",
                 (child, parent, run_id))


def _ci(conn, run_id, moiety, klass, relationship="CI_PE"):
    conn.execute(
        "INSERT INTO drugref.class_contraindication (subject_moiety_uuid, "
        "object_class_uuid, relationship, source, ingest_run) "
        "VALUES (%s, %s, %s, 'MED-RT', %s)", (moiety, klass, relationship, run_id))


# ---- gap_unpopulated_contraindication ---------------------------------------


def test_a_contraindication_naming_an_empty_class_is_a_gap(conn):
    """MED-RT asserts the concern and never files a drug under it -- 41 rules across
    13 classes in the 2026.07.06 release. These can never produce a pair under ANY
    expansion policy, which is what makes them the highest-value worklist available:
    upstream authority already vouching that the answer matters."""
    run_id = _run(conn)
    empty = _class(conn, run_id, "N0000000001", name="Renal Arterial Vasoconstriction [PE]")
    _ci(conn, run_id, _moiety(conn, run_id), empty)

    rows = conn.execute("SELECT class_uuid, class_name, ci_rule_count "
                        "FROM drugref.gap_unpopulated_contraindication").fetchall()
    assert rows == [(empty, "Renal Arterial Vasoconstriction [PE]", 1)]


def test_a_populated_class_is_not_a_gap(conn):
    run_id = _run(conn)
    populated = _class(conn, run_id, "N0000000002")
    _member(conn, run_id, _moiety(conn, run_id), populated)
    _ci(conn, run_id, _moiety(conn, run_id, "other"), populated)
    assert conn.execute("SELECT count(*) FROM "
                        "drugref.gap_unpopulated_contraindication").fetchone()[0] == 0


def test_a_member_on_a_DESCENDANT_class_closes_the_gap(conn):
    """'No drug filed under E' means nowhere in E's SUBTREE, not merely directly on E.
    A parent with an empty direct membership but a populated child is not a gap: the
    concern is answerable, just one level down. Getting this wrong would report every
    abstract class in the hierarchy as an open question."""
    run_id = _run(conn)
    parent = _class(conn, run_id, "N0000000003")
    child = _class(conn, run_id, "N0000000004")
    _parent(conn, run_id, child, parent)
    _member(conn, run_id, _moiety(conn, run_id), child)
    _ci(conn, run_id, _moiety(conn, run_id, "other"), parent)

    assert conn.execute("SELECT count(*) FROM "
                        "drugref.gap_unpopulated_contraindication").fetchone()[0] == 0


def test_rules_naming_one_empty_class_are_counted_together(conn):
    """The register is per CLASS, with the rule count as the priority signal --
    Genitourinary Arterial Vasoconstriction carries 7 rules, Renal 6."""
    run_id = _run(conn)
    empty = _class(conn, run_id, "N0000000005")
    for i in range(3):
        _ci(conn, run_id, _moiety(conn, run_id, f"m{i}"), empty)
    assert conn.execute("SELECT ci_rule_count FROM "
                        "drugref.gap_unpopulated_contraindication").fetchone()[0] == 3


# ---- gap_unclassified_moiety ------------------------------------------------


def test_a_moiety_with_no_PE_membership_is_a_gap(conn):
    """Structurally unable to participate in an effect-accumulation model: nothing
    can ever accumulate for a drug no effect class contains."""
    run_id = _run(conn)
    m = _moiety(conn, run_id, "orphanium")
    rows = conn.execute("SELECT moiety_uuid, display_name FROM "
                        "drugref.gap_unclassified_moiety").fetchall()
    assert rows == [(m, "orphanium")]


def test_a_moiety_with_a_PE_membership_is_not_a_gap(conn):
    run_id = _run(conn)
    m = _moiety(conn, run_id)
    _member(conn, run_id, m, _class(conn, run_id, "N0000000006"), "has_PE")
    assert conn.execute("SELECT count(*) FROM "
                        "drugref.gap_unclassified_moiety").fetchone()[0] == 0


def test_a_moiety_with_only_a_MoA_membership_is_still_a_gap(conn):
    """PE is the convergence axis the accumulation model needs; a drug classified on
    mechanism alone still cannot participate in an effect that adds up."""
    run_id = _run(conn)
    m = _moiety(conn, run_id)
    _member(conn, run_id, m, _class(conn, run_id, "N0000000007", "MoA"), "has_MoA")
    assert conn.execute("SELECT count(*) FROM "
                        "drugref.gap_unclassified_moiety").fetchone()[0] == 1


# ---- gap_unmatched_ingredient (needs the persisted table) --------------------


def test_an_unmatched_rxcui_is_queryable(conn):
    """The identities, not merely the count. MED-RT classifies far more ingredients
    than pass drugref's moiety gate, and each one is a drug the registry cannot say
    anything about -- which is a question, not a silent statistic."""
    run_id = _run(conn)
    conn.execute("INSERT INTO drugref.ingest_unmatched_ingredient "
                 "(ingest_run, rxcui, name) VALUES (%s, '5640', 'ibuprofen')", (run_id,))
    rows = conn.execute("SELECT rxcui, name FROM "
                        "drugref.gap_unmatched_ingredient").fetchall()
    assert rows == [("5640", "ibuprofen")]


def test_an_rxcui_the_registry_later_carries_is_no_longer_a_gap(conn):
    """The view is the join, not the stored row: once a moiety claims the RxCUI the
    gap closes without anyone rewriting the ingest table."""
    run_id = _run(conn)
    m = _moiety(conn, run_id)
    conn.execute("INSERT INTO drugref.identity_claim "
                 "(moiety_uuid, scheme, value, ingest_run) "
                 "VALUES (%s, 'RXNORM_IN', '5640', %s)", (m, run_id))
    conn.execute("INSERT INTO drugref.ingest_unmatched_ingredient "
                 "(ingest_run, rxcui, name) VALUES (%s, '5640', 'ibuprofen')", (run_id,))
    assert conn.execute("SELECT count(*) FROM "
                        "drugref.gap_unmatched_ingredient").fetchone()[0] == 0


def test_unmatched_ingredients_are_replaced_per_run_not_accumulated(conn):
    """A rebuildable projection like every other per-source table: a re-ingest must
    replace the previous release's list, or an ingredient that started matching would
    linger as a gap forever."""
    run_id = _run(conn)
    conn.execute("INSERT INTO drugref.ingest_unmatched_ingredient "
                 "(ingest_run, rxcui, name) VALUES (%s, '5640', 'ibuprofen')", (run_id,))
    with pytest.raises(Exception):
        conn.execute("INSERT INTO drugref.ingest_unmatched_ingredient "
                     "(ingest_run, rxcui, name) VALUES (%s, '5640', 'ibuprofen')",
                     (run_id,))
