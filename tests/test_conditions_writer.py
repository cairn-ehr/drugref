"""The condition writer (slice 5b), mirroring classes.py's single-writer role."""
from drugref import conditions, ids
from drugref.ingest.mesh_concepts import MeshRecord

EPILEPSY = MeshRecord(
    concept_ui="M0007751", record_ui="D004827", record_kind="DESCRIPTOR",
    name="Epilepsy", tree_numbers=("C10.228.140.490",),
    unii=frozenset(), cas=frozenset(), is_preferred_concept=True)


def _run(conn, source="MeSH"):
    """Create an ingest_run row under a given source, mirroring the identical
    helper in test_schema_classes.py. The shared `ingest_run_id` fixture always
    inserts source='PBS' -- fine when a test only needs a valid FK target, but
    wrong for anything that must actually be scoped BY source: is_new needs two
    genuinely different runs, and clear_source_condition_edges filters its DELETE
    on ingest_run.source, which the fixture's row would never match 'MeSH'."""
    # The writer implied by each source this module's tests open a run under
    # (db/025): a KeyError on an unlisted source beats a silent NotNullViolation.
    writer = {"MeSH": "mesh_run", "PBS": "pbs_run"}[source]
    return conn.execute(
        "INSERT INTO drugref.ingest_run "
        "(source, upstream_release, source_checksum, writer) "
        "VALUES (%s, 'test', 'deadbeef', %s) RETURNING ingest_run_id",
        (source, writer)).fetchone()[0]


def test_upsert_returns_the_derived_uuid(conn, ingest_run_id):
    cu, is_new = conditions.upsert_condition(conn, EPILEPSY, ingest_run_id, "MeSH")
    assert cu == ids.mint_condition_uuid("MeSH", "D004827")
    assert is_new is True


def test_second_upsert_is_not_new(conn):
    """Conditions ACCUMULATE while edges are rebuilt, so 'in this release' and
    'added by this run' are genuinely different numbers -- as for classes.

    Deliberately TWO different runs: first_seen_ingest is only a meaningful
    newness test when the second call's run id differs from the first's -- reusing
    one ingest_run_id for both calls would make is_new trivially True every time,
    since RETURNING first_seen_ingest would just hand back that same id again."""
    r1, r2 = _run(conn), _run(conn)
    conditions.upsert_condition(conn, EPILEPSY, r1, "MeSH")
    _cu, is_new = conditions.upsert_condition(conn, EPILEPSY, r2, "MeSH")
    assert is_new is False


def test_upsert_refreshes_the_cached_name(conn, ingest_run_id):
    """Upstream renames records; the cache must follow."""
    conditions.upsert_condition(conn, EPILEPSY, ingest_run_id, "MeSH")
    renamed = MeshRecord(**{**EPILEPSY.__dict__, "name": "Epilepsies"})
    conditions.upsert_condition(conn, renamed, ingest_run_id, "MeSH")
    assert conn.execute(
        "SELECT name FROM drugref.condition WHERE source_code = 'D004827'"
    ).fetchone()[0] == "Epilepsies"


def test_stored_source_is_canonical(conn, ingest_run_id):
    """The stored spelling and the UUID key must derive from ONE canonicalisation,
    or a per-source rebuild silently misses rows it owns (ids.canonical_source)."""
    conditions.upsert_condition(conn, EPILEPSY, ingest_run_id, "mesh")
    assert conn.execute(
        "SELECT source FROM drugref.condition WHERE source_code = 'D004827'"
    ).fetchone()[0] == "MeSH"


def test_clear_source_edges_removes_only_that_source(conn):
    """Rebuild semantics: a new MeSH release replaces MeSH edges and leaves any
    other source's edges untouched -- the same guarantee test_schema_classes.py
    pins for classes.clear_source_edges. Needs a run whose ingest_run.source is
    actually 'MeSH' (the fixture's is 'PBS'), since that is the column
    clear_source_condition_edges filters its DELETE on."""
    mesh_run, other_run = _run(conn, source="MeSH"), _run(conn, source="PBS")
    parent, _ = conditions.upsert_condition(conn, EPILEPSY, mesh_run, "MeSH")
    child_rec = MeshRecord(**{**EPILEPSY.__dict__, "record_ui": "D004829",
                              "name": "Epilepsy, Generalized",
                              "tree_numbers": ("C10.228.140.490.360",)})
    child, _ = conditions.upsert_condition(conn, child_rec, mesh_run, "MeSH")
    grandchild_rec = MeshRecord(**{**EPILEPSY.__dict__, "record_ui": "D004831",
                                   "name": "Epilepsy, Frontal Lobe",
                                   "tree_numbers": ("C10.228.140.490.360.150",)})
    grandchild, _ = conditions.upsert_condition(conn, grandchild_rec, mesh_run, "MeSH")

    assert conditions.add_condition_parent_edge(conn, child, parent, mesh_run)
    # Attributed to a DIFFERENT source's run, so clearing "MeSH" must spare it.
    assert conditions.add_condition_parent_edge(conn, grandchild, child, other_run)

    conditions.clear_source_condition_edges(conn, "MeSH")

    remaining = conn.execute(
        "SELECT child_condition_uuid, parent_condition_uuid "
        "FROM drugref.condition_parent").fetchall()
    assert remaining == [(grandchild, child)]
    # Condition rows themselves survive: their UUIDs are immortal.
    assert conn.execute("SELECT count(*) FROM drugref.condition").fetchone()[0] == 3


def test_duplicate_edge_is_harmless(conn, ingest_run_id):
    parent, _ = conditions.upsert_condition(conn, EPILEPSY, ingest_run_id, "MeSH")
    child_rec = MeshRecord(**{**EPILEPSY.__dict__, "record_ui": "D004829"})
    child, _ = conditions.upsert_condition(conn, child_rec, ingest_run_id, "MeSH")
    assert conditions.add_condition_parent_edge(conn, child, parent, ingest_run_id)
    assert not conditions.add_condition_parent_edge(conn, child, parent, ingest_run_id)
