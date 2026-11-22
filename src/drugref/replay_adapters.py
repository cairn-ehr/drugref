"""One replay adapter per ingest orchestrator (split out of `release_replay`).

`release_replay` re-runs every ingest run of a release from its archived input
files. Each orchestrator takes its files under its own argument names, so each
needs a small adapter here that maps the run record's file list back onto them.
`ADAPTERS` is keyed by the run's `writer`; a writer missing from it makes a
replay refuse before writing anything.
"""
import fnmatch
import pathlib

from drugref.ingest import (allergy_group_run, chebi, drugcentral_run, fda_cyp_run,
                            gsrs_run, medrt_run, mesh_rel_run, mesh_run,
                            mesh_synonym_run, moiety_name_run, onchigh_run, pbs_run,
                            run, spl_run)

# Each takes (conn, files in run-record order, upstream_release) and maps the files
# to the orchestrator's arguments in the order it passed them to `archived_checksum`.


def _unii(conn, f, release):
    from drugref import cli    # lazy: cli imports cli_audit, which imports us
    run.ingest_unii(conn, unii_path=f[0], crosswalk_path=cli.CROSSWALK,
                    allowlist_path=cli.ALLOWLIST, upstream_release=release)


def split_spl_inputs(files: list[pathlib.Path]):
    """(openFDA partitions, DailyMed parts, UNII_Names) from an SPL run's files.

    The record lists them sorted by name, then two packaged lists the orchestrator
    reads itself (`spl_run.checksummed_inputs`), so the last two are dropped. The
    partitions are named by the glob the orchestrator uses; UNII_Names is the one
    text file; the rest are DailyMed zips.
    """
    corpora = files[:-2]
    partitions = [p for p in corpora
                  if fnmatch.fnmatch(p.name, "drug-label-*.json.zip")]
    names = [p for p in corpora if p.suffix == ".txt"]
    parts = [p for p in corpora if p not in partitions and p not in names]
    return partitions, parts, (names[0] if names else None)


def _spl(conn, f, release):
    partitions, parts, names = split_spl_inputs(f)
    # Every file is staged in one directory, so it doubles as the openFDA directory:
    # the orchestrator globs only the partitions out of it.
    spl_run.ingest_spl(conn, openfda_dir=f[0].parent, dailymed_parts=parts,
                       release=release, unii_names=names)


ADAPTERS = {
    "unii_run": _unii,
    "chebi": lambda c, f, r: chebi.enrich_from_chebi(
        c, chebi_path=f[0], upstream_release=r),
    "medrt_run": lambda c, f, r: medrt_run.ingest_medrt(
        c, medrt_path=f[0], upstream_release=r),
    "mesh_run": lambda c, f, r: mesh_run.ingest_mesh(
        c, pa_path=f[0], desc_path=f[1], supp_path=f[2], upstream_release=r),
    "mesh_rel_run": lambda c, f, r: mesh_rel_run.ingest_mesh_relations(
        c, medrt_path=f[0], desc_path=f[1], supp_path=f[2], upstream_release=r),
    "pbs_run": lambda c, f, r: pbs_run.ingest_pbs(c, f[0], r),
    "gsrs_run": lambda c, f, r: gsrs_run.ingest_gsrs(c, dump_path=f[0],
                                                     upstream_release=r),
    "onchigh_run": lambda c, f, r: onchigh_run.ingest_onchigh(
        c, path=f[0], upstream_release=r),
    "fda_cyp_run": lambda c, f, r: fda_cyp_run.ingest_fda_cyp(
        c, page_path=f[0], upstream_release=r, allow_shrink=True,
        allow_resolution_drop=True),
    "drugcentral_run": lambda c, f, r: drugcentral_run.ingest_drugcentral(
        c, dump_path=f[0], release=r),
    "spl_run": _spl,
    "allergy_group_run": lambda c, f, r: allergy_group_run.ingest_allergy_groups(
        c, allow_list_path=f[0], desc_path=f[1], supp_path=f[2], upstream_release=r),
    "moiety_name_run": lambda c, f, r: moiety_name_run.ingest_moiety_names(
        c, unii_names_path=f[0], upstream_release=r),
    "mesh_synonym_run": lambda c, f, r: mesh_synonym_run.ingest_mesh_synonyms(
        c, desc_path=f[0], supp_path=f[1], upstream_release=r),
}
