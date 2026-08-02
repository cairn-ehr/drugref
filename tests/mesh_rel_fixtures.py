# tests/mesh_rel_fixtures.py
"""Shared setup for the TWO test modules that exercise ONE MeSH-keyed relation run.

`ingest/mesh_rel_run.py` runs both relation families over one condition registry
(spec 6.1), so tests/test_mesh_rel_run_ci.py and tests/test_mesh_rel_run_ind.py drive
the SAME entry point over the SAME fixtures and differ only in what they assert. What
they must not each own a copy of is written down here: the list of tables one run
touches, the registry ingest's arguments, and the file set the entry point is called
with. A second copy of any of those is the "one quantity stated twice" trap -- db/018's
round is the standing evidence that only one copy ever learns the next correction.

BOTH MODULES ARE NAMED AFTER `mesh_rel_run`, the orchestrator they actually drive. They
were `test_mesh_ci_run.py` / `test_mesh_ind_run.py` until slice 5b.2 finished, naming a
`mesh_ci_run.py` that the one-orchestrator refactor had already deleted and a
`mesh_ind_run.py` that never existed at all.

PLAIN FUNCTIONS, NOT PYTEST FIXTURES, and the reason is mechanical rather than a
preference: a fixture imported into a second module and then named as a test parameter
shadows its own import. So each module wraps these in its own one-line fixture, which
also keeps `autouse` a decision each module makes for itself.

Not named test_*.py, so pytest does not collect it.
"""
import pathlib

from drugref import ids
from drugref.ingest import mesh_rel_run, run

FIXTURES = pathlib.Path(__file__).parent / "fixtures"
UNII_FIX = FIXTURES / "unii_subset.tsv"
DATA = pathlib.Path("src/drugref/data")


def truncate(conn) -> None:
    """Empty every table one MeSH-keyed run writes, plus the registry it joins against.

    Needed because ingest_mesh_relations COMMITS internally, so it escapes conftest's
    rollback-based isolation -- the same reason tests/test_ingest_run.py has one.

    db/019's two relations are named EXPLICITLY even though TRUNCATE ... CASCADE would
    reach them through `condition`: this list is what a reader consults to learn which
    tables one run touches, and a table that is only cleared by accident is one nobody
    knows is cleared.

    ingest_unmatched_ingredient is named for that same reason, and it had been left to
    the CASCADE from `ingest_run` while three tests asserted on it directly -- the exact
    shape the paragraph above forbids. Naming it is also what keeps the list honest
    about `indication`, the SECOND of the TWO `reason` buckets this run owns (db/019
    section 7 added it beside `contraindication`).

    Not "the third bucket": that counts the run's buckets off the VOCABULARY's size,
    which is the conflation classes.py's REASON constants warn against in as many
    words -- values and writers are counted separately, and db/026's fourth value
    (`contraindication_class`) belongs to medrt_run, not to this run at all.
    """
    conn.execute(
        "TRUNCATE drugref.moiety_condition_indication, "
        "drugref.moiety_induced_condition, "
        "drugref.moiety_condition_contraindication, "
        "drugref.moiety_contraindication, drugref.ingest_unresolved_ci_object, "
        "drugref.ingest_unmatched_ingredient, "
        "drugref.condition_parent, drugref.condition, "
        "drugref.open_question, drugref.class_contraindication, "
        "drugref.class_membership, drugref.class_parent, drugref.substance_class, "
        "drugref.identity_claim, drugref.substance_moiety, drugref.ingest_run "
        "RESTART IDENTITY CASCADE")
    conn.commit()


def seed_moieties(conn):
    """The slice-1 moiety registry this slice joins against.

    Built by running the REAL identity ingest over unii_subset.tsv, exactly as
    tests/test_medrt_run.py's `seeded` does, rather than by hand-inserting rows: the
    subject bridge reads RXNORM_IN claims and the object bridge reads UNII claims, and
    both are things ingest_unii produces. Seeding them directly would test the
    orchestrator against a registry no ingest could actually build.

    It carries every subject RxCUI the MED-RT subset asserts against except ibuprofen
    (5640), which is deliberately absent so the unmatched-subject path is exercised,
    and it carries pimozide, which is the CI_ChemClass OBJECT.
    """
    run.ingest_unii(conn, unii_path=UNII_FIX,
                    crosswalk_path=DATA / "usan_inn_crosswalk.tsv",
                    allowlist_path=DATA / "legacy_allowlist.tsv",
                    upstream_release="2026-07")
    return conn


def ingest(conn):
    """One MeSH-keyed relation run over the committed subsets. Returns the summary."""
    return mesh_rel_run.ingest_mesh_relations(
        conn,
        medrt_path=FIXTURES / "medrt_subset.xml",
        desc_path=FIXTURES / "mesh_ci_desc_subset.xml",
        supp_path=FIXTURES / "mesh_ci_supp_subset.xml",
        upstream_release="test")


def condition_uuid(source_code: str):
    """The condition_uuid a MeSH record ui is registered under.

    Re-derived rather than looked up, which is the property worth testing: a
    condition_uuid is a pure function of (source, code), so a rebuild brings every one
    of them back unchanged.
    """
    return ids.mint_condition_uuid("MeSH", source_code)
