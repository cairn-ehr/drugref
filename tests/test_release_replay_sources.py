"""A replay through the REAL orchestrators rebuilds the same tables (#259).

`test_release_replay.py` tests the engine with a stand-in writer. This file tests
the per-writer adapters: the origin is built by the CLI (the production mapping
of files to arguments), the target by `release_replay.ADAPTERS`, and every table
must end with the same row count. A file mapped to the wrong argument either
fails to parse or rebuilds a different projection, and shows here.

Not covered: `spl_run` (its fixture is assembled per test, not a file on disk;
`split_spl_inputs` is unit-tested), `moiety_name_run` and `mesh_synonym_run` (their
tests build their input inline; the shared MeSH subset links no synonyms, which
that writer refuses).
"""
import pathlib

import psycopg

from drugref import archive, cli, release_archive, release_replay, signing
from tests.test_release_replay import SHA, databases  # noqa: F401  (fixture)

FIX = pathlib.Path(__file__).parent / "fixtures"
TAG = "2026.11.18-sources"

# One CLI call per writer, in chain order (unii first, onchigh last).
INGESTS = [
    ["unii", "--release", "u1", "--unii", FIX / "unii_subset.tsv"],
    ["chebi", "--release", "c1", "--chebi", FIX / "chebi_subset.tsv"],
    ["medrt", "--release", "m1", "--medrt", FIX / "medrt_subset.xml"],
    ["mesh", "--release", "2026", "--pa", FIX / "mesh_pa_subset.xml",
     "--desc", FIX / "mesh_desc_subset.xml", "--supp", FIX / "mesh_supp_subset.xml"],
    ["mesh-relations", "--release", "2026", "--medrt", FIX / "medrt_subset.xml",
     "--desc", FIX / "mesh_ci_desc_subset.xml", "--supp", FIX / "mesh_ci_supp_subset.xml"],
    ["gsrs", "--release", "g1", "--dump", FIX / "gsrs_subset.gsrs"],
    ["pbs", "--release", "p1", "--items", FIX / "pbs_items_subset.csv"],
    ["fda-cyp", "--page", FIX / "fda_cyp_table.html", "--allow-resolution-drop"],
    ["drugcentral", "--release", "d1", "--dump", FIX / "drugcentral_ddi_subset.sql.gz"],
    ["allergy-groups", "--release", "2026", "--desc", FIX / "allergy_desc_subset.xml",
     "--supp", FIX / "allergy_supp_subset.xml"],
    ["onchigh", "--release", "o1", "--onc", FIX / "onc_fixture.toml"],
]

# Rows that record WHEN or WHERE, not WHAT: publish bookkeeping and the ledger.
_SKIP = {"schema_migration", "release_manifest", "release_manifest_entry",
         "release_build", "assertion_signature"}


def _counts(dsn):
    with psycopg.connect(dsn) as c:
        tables = [t for (t,) in c.execute(
            "SELECT tablename FROM pg_tables WHERE schemaname = 'drugref' "
            "ORDER BY tablename") if t not in _SKIP]
        return {t: c.execute(f"SELECT count(*) FROM drugref.{t}").fetchone()[0]
                for t in tables}


def test_real_orchestrators_replay_to_the_same_tables(databases, tmp_path,  # noqa: F811
                                                       monkeypatch):
    origin, target = databases
    root = tmp_path / "archive"
    monkeypatch.setenv(archive.ENV_VAR, str(root))
    monkeypatch.setattr(release_archive, "git_commit", lambda: SHA)
    for argv in INGESTS:
        assert cli.main(["--dsn", origin, "ingest", *map(str, argv)]) == 0, argv
    kp = signing.generate_keypair()
    with psycopg.connect(origin) as c:
        release_archive.publish_archived(
            c, root=root, release_tag=TAG, published_by="op",
            private_key=kp.private_key, key_fingerprint=signing.fingerprint(kp.public_key))
        c.commit()
    with psycopg.connect(target) as c:
        report = release_replay.replay(c, root, TAG)
    assert report.differences == []
    assert report.runs == len(INGESTS)
    want, got = _counts(origin), _counts(target)
    assert {t: (want[t], got[t]) for t in want if want[t] != got[t]} == {}

