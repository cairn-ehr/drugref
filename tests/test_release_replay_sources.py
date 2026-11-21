"""A replay through the REAL orchestrators rebuilds the same tables (#259).

`test_release_replay.py` tests the engine with a stand-in writer. This file tests
the per-writer adapters: the origin is built by the CLI (the production mapping
of files to arguments), the target by `release_replay.ADAPTERS`, and every table
must end with the same row count. A file mapped to the wrong argument either
fails to parse or rebuilds a different projection, and shows here.

All 14 writers in `ADAPTERS` are covered (#272). The SPL corpus is zipped into
openFDA's and DailyMed's shapes at test time by `test_spl_run`'s helpers, so no
binary archive is committed; the CLI then reads it from disk like any other.
"""
import pathlib

import psycopg

from drugref import archive, cli, release_archive, release_replay, signing
from tests.test_release_replay import SHA, databases  # noqa: F401  (fixture)
from tests.test_spl_run import LABELS, WORDING, _dailymed_part, _openfda_partition

FIX = pathlib.Path(__file__).parent / "fixtures"
TAG = "2026.11.18-sources"

# One CLI call per writer: chain steps in chain order (unii first, onchigh last),
# the non-chain writers slotted in after the registry they read.
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
    # Like the three above, not chain steps: both read the registry `unii` built,
    # and moiety-names refuses a release other than the registry's own ("u1").
    ["moiety-names", "--release", "u1", "--unii-names", FIX / "unii_names_subset.txt"],
    ["mesh-synonyms", "--release", "2026", "--desc", FIX / "mesh_synonym_desc_subset.xml",
     "--supp", FIX / "mesh_synonym_supp_subset.xml"],
    ["onchigh", "--release", "o1", "--onc", FIX / "onc_fixture.toml"],
]

# Rows that record WHEN or WHERE, not WHAT: publish bookkeeping and the ledger.
_SKIP = {"schema_migration", "release_manifest", "release_manifest_entry",
         "release_build", "assertion_signature"}


def _spl_argv(where: pathlib.Path) -> list:
    """`ingest spl` over a corpus zipped into `where`, in the shapes the CLI reads.

    Named as production names them, because the replay adapter tells the files
    apart by name (`release_replay.split_spl_inputs`). The floors are off: the
    fixture is a deliberately partial corpus.
    """
    openfda = where / "OPENFDA"
    openfda.mkdir(parents=True)
    # Every label keyed to a UNII the registry holds (the SPL fixture's own are
    # not in `unii_subset.tsv`), and the wording names two registry moieties, so
    # subjects resolve and pairs are written: an empty read is refused.
    labels = [dict(label, uniis=["362O9ITL9D"]) for label in LABELS]
    wording = WORDING.replace("warfarin", "amlodipine").replace("Rifampin", "Pimozide")
    _openfda_partition(openfda / "drug-label-0001-of-0001.json.zip", labels,
                       wording_for=lambda _label: wording)
    part = where / "dm_spl_release_human_rx_part1.zip"
    _dailymed_part(part, [label["set_id"] for label in LABELS])
    return ["spl", "--release", "s1", "--openfda", openfda, "--dailymed", part,
            "--unii-names", FIX / "unii_names_subset.txt", "--no-pair-floor"]


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
    ingests = INGESTS + [_spl_argv(tmp_path / "spl")]
    for argv in ingests:
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
    assert report.runs == len(ingests)
    want, got = _counts(origin), _counts(target)
    # Equal counts prove nothing if a writer wrote nothing: the three #272 added
    # must each have landed rows.
    assert all(want[t] for t in ("moiety_official_name", "moiety_mesh_synonym",
                                 "spl_label_subject", "spl_entity_occurrence")), want
    assert {t: (want[t], got[t]) for t in want if want[t] != got[t]} == {}

