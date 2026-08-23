# src/drugref/ingest/drugcentral_run.py
"""Orchestrate one DrugCentral ingest: read -> guard -> resolve -> clear -> write.

The ONLY writer of drugref's DrugCentral rows, per the architecture invariant:
parsers are pure, orchestrators own the transaction.

ORDER MATTERS, as for every other feed here:
  1. read the dump, checksum it and RUN THE RULE-6 GUARD before opening the run,
     so a crash -- or a refusal -- leaves no half-written run row;
  2. load the registry under a deterministic order;
  3. clear this source's old rows, so a re-ingest REPLACES rather than accumulates;
  4. write the assertions;
  5. rebuild the question register, finish, commit.

THE DATA DEPENDENCY IS REAL: the cascade joins substance_moiety.display_name and
live INCHIKEY/CAS identity_claim rows, so `unii` and `chebi` must have run. Ingest
this against an empty registry and every endpoint resolves to nothing, quietly.

WHAT THIS MODULE REFUSES TO DO:

* It bridges NO name and resolves NOTHING itself. Every endpoint's resolution --
  display_name, then InChIKey, then CAS -- is `drugcentral.resolve_row`'s call
  (via `drugcentral_resolve`); this module only supplies the registry snapshot and
  counts what came back. A resolution rule duplicated here would be a second place
  for the cascade's careful ordering to drift out of.
* It admits NO reference but the one CLAUDE.md rule 6 clears. `bundleable_rows`
  and `check_reference_identity` decide that in `drugcentral.py`, pure and
  independent of any database state; this module runs the guard and refuses to
  write, but does not re-implement the vocabulary it enforces.
* It does not retry, or fall back to a partial write, on any failure once the run
  is open. Any exception during resolution or writing rolls back the WHOLE run
  and re-raises -- a half-written DrugCentral projection is worse than none, and
  the same choice every sibling orchestrator here makes (onchigh_run, medrt_run,
  mesh_run, fda_cyp_run).
"""
import dataclasses
import gzip
import logging
import pathlib

import psycopg

from drugref import interactions, provenance, questions
from drugref.ingest import drugcentral
from drugref.ingest.checksum import checksum
from drugref.ingest.drugcentral_resolve import (
    Registry, build_endpoint_index, first_wins,
)

log = logging.getLogger(__name__)


@dataclasses.dataclass(frozen=True)
class DrugCentralSummary:
    """What one ingest did, in buckets that RECONCILE.

    `rows_read` = `rows_excluded_by_reference` + `rows_bundleable`, and
    `rows_bundleable` = `rows_resolved` + `rows_self_pair` + `rows_unresolved`.
    __post_init__ refuses any other arithmetic, because a summary whose buckets do
    not sum is a number nobody can check -- curate_onchigh counted entries it had
    silently dropped, and the re-measurement's Measurement guard exists for the
    same reason.

    BOTH IDENTITIES ARE PYTHON-SIDE, so together they only prove the orchestrator
    is self-consistent -- neither can see the table. The third reconciliation, the
    one that reads `drugcentral_ddi_assertion` back and refuses a stored count that
    disagrees with `rows_bundleable`, lives in `ingest_drugcentral` because it needs
    the open transaction; see the comment there.

    `rows_resolved` counts rows whose two endpoints reached TWO DIFFERENT moieties;
    `rows_self_pair` counts those that reached ONE (0 of 7,571 on the 2023 release,
    and a bucket rather than a footnote so it cannot become nonzero unnoticed).
    `pairs` is the DISTINCT UNORDERED pair count from the view, which is smaller
    than `rows_resolved` because 33 pairs are published in both orders.
    """

    rows_read: int
    rows_excluded_by_reference: int
    rows_bundleable: int
    rows_resolved: int
    rows_self_pair: int
    rows_unresolved: int
    pairs: int
    duplicate_keys: int

    def __post_init__(self) -> None:
        if self.rows_excluded_by_reference + self.rows_bundleable != self.rows_read:
            raise ValueError(
                f"excluded ({self.rows_excluded_by_reference}) + bundleable "
                f"({self.rows_bundleable}) do not sum to read ({self.rows_read})")
        landed = self.rows_resolved + self.rows_self_pair + self.rows_unresolved
        if landed != self.rows_bundleable:
            raise ValueError(
                f"resolved ({self.rows_resolved}) + self-pair "
                f"({self.rows_self_pair}) + unresolved ({self.rows_unresolved}) "
                f"do not sum to bundleable ({self.rows_bundleable})")

    def __str__(self) -> str:
        return (f"{self.rows_bundleable} bundleable of {self.rows_read} rows "
                f"({self.rows_excluded_by_reference} excluded by rule 6) -> "
                f"{self.pairs} pairs; {self.rows_unresolved} unresolved, "
                f"{self.rows_self_pair} self-pairs, "
                f"{self.duplicate_keys} colliding registry keys")


def load_registry(conn: psycopg.Connection) -> tuple[Registry, int]:
    """The drugref side of the join: three lookups onto `moiety_uuid`.

    EVERY READ IS ORDERED, and that is not cosmetic. `identity_claim` is unique on
    (moiety_uuid, scheme, value) and deliberately NOT across moieties, so two
    moieties may legitimately carry one CAS number -- measured 2026-08-23, 14
    InChIKeys and 29 CAS numbers are claimed by more than one. An unordered
    single-row read would let the same dump resolve differently on two runs.

    Live claims only (`superseded_by IS NULL`): a corrected-away identifier must
    not resurrect a resolution.

    Returns the registry and the total number of colliding keys, which the summary
    reports rather than discards.
    """
    with conn.cursor() as cur:
        cur.execute("SELECT display_name, moiety_uuid::text "
                    "FROM drugref.substance_moiety "
                    "ORDER BY display_name, moiety_uuid")
        display_name, dup_names = first_wins(cur.fetchall())

        cur.execute("SELECT value, moiety_uuid::text FROM drugref.identity_claim "
                    "WHERE scheme = %s AND superseded_by IS NULL "
                    "ORDER BY value, moiety_uuid", ("INCHIKEY",))
        inchikey, dup_keys = first_wins(cur.fetchall())

        cur.execute("SELECT value, moiety_uuid::text FROM drugref.identity_claim "
                    "WHERE scheme = %s AND superseded_by IS NULL "
                    "ORDER BY value, moiety_uuid", ("CAS",))
        cas, dup_cas = first_wins(cur.fetchall())

    # `Registry` folds its own keys, so the SQL above does not -- the case rule
    # used to live in both places, which is the shape this repo keeps losing to.
    return (Registry(display_name=display_name, inchikey=inchikey, cas=cas),
            dup_names + dup_keys + dup_cas)


def ingest_drugcentral(conn: psycopg.Connection, *,
                       dump_path: str | pathlib.Path,
                       release: str) -> DrugCentralSummary:
    """Read one DrugCentral dump and rebuild this source's projection.

    Owns TWO transactions on one connection, exactly as every other orchestrator
    here (pbs_run/mesh_run's documented convention): `provenance.open_run` commits
    the run record in its own transaction before any work begins, so a crash from
    here on leaves that row standing with `finished_at IS NULL` rather than no
    trace at all. Everything after it is the work, which this function commits on
    success and rolls back -- via the `except` clause below -- on any failure.
    """
    dump_path = pathlib.Path(dump_path)
    digest = checksum(dump_path)

    with gzip.open(dump_path, "rt", encoding="utf-8") as handle:
        tables = drugcentral.read_tables(handle)

    # RULE 6, BEFORE ANY RUN ROW EXISTS. A refusal must leave the database exactly
    # as it was, and an ingest_run with finished_at NULL is not "exactly as it was".
    drugcentral.check_reference_identity(tables.reference)

    bundleable = tuple(drugcentral.bundleable_rows(tables.ddi))
    index = build_endpoint_index(tables.structures, tables.synonyms)

    try:
        run_id = provenance.open_run(conn, source=drugcentral.SOURCE,
                                     upstream_release=release,
                                     source_checksum=digest,
                                     writer=drugcentral.WRITER)

        # The work transaction. REPEATABLE READ so the registry the cascade joins
        # is ONE snapshot: under READ COMMITTED each of the three lookups would
        # get its own, and a concurrent claim landing between them would make the
        # resolution depend on timing. Must be the first statement of the
        # transaction -- open_run's own commit just ended the previous one, so
        # this is the first statement of a fresh one.
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
        registry, duplicate_keys = load_registry(conn)

        interactions.clear_source_drugcentral(conn, drugcentral.SOURCE)

        resolved = self_pair = unresolved = 0
        for row in bundleable:
            record = drugcentral.resolve_row(row, index, registry)
            # The return value (False on an ON CONFLICT DO NOTHING skip) is
            # deliberately IGNORED here, and that is safe rather than sloppy:
            # the table's PRIMARY KEY is (ingest_run, source, upstream_key), so
            # a conflict can only happen BETWEEN TWO ROWS OF THIS SAME RUN, and
            # db/049_drugcentral_ddi.sql's own comment on `upstream_key` records
            # the measured fact that all 7,571 real bundleable rows carry a
            # distinct `source_id` -- so within one run this path is provably
            # dead on THIS release's data. If a future release ever DID repeat a
            # key within one dump, a skipped insert would silently drop a row
            # from `rows_bundleable` while still counting it into
            # resolved/self_pair/unresolved below, drifting the buckets from
            # what `drugcentral_ddi_assertion` actually stores -- exactly the
            # kind of silent miscount CLAUDE.md rule 5 exists to catch.
            #
            # THE TRIGGERS FOR THAT ARE NOT ONLY THE OBVIOUS TWO. An earlier
            # version of this comment scoped them to "widening
            # BUNDLEABLE_REF_IDS or changing upstream_key's source column", and
            # that list was too short: `resolve_row` falls back to `""` when
            # `source_id` is NULL (drugcentral.py's `row.get("source_id") or
            # ""`), so a release with TWO blank source_ids collides on the empty
            # key without either of those two things having changed. That is why
            # the count below reads the table back rather than trusting this
            # comment to be revisited -- a guard that executes, not a promise.
            interactions.add_drugcentral_assertion(
                conn,
                ingest_run_id=run_id,
                source=drugcentral.SOURCE,
                upstream_key=record.upstream_key,
                endpoint_1_name=record.endpoint_1_name,
                endpoint_2_name=record.endpoint_2_name,
                upstream_label=record.upstream_label,
                severity_label=record.severity_label,
                moiety_1_uuid=record.moiety_1_uuid,
                moiety_2_uuid=record.moiety_2_uuid,
                route_1=record.route_1,
                route_2=record.route_2)
            # self_pair is a STRICT SUBSET of resolved (AssertionRecord.self_pair's
            # own docstring), so this MUST be checked first: summing `resolved`
            # directly would double-count every self-pair row into both buckets
            # and DrugCentralSummary.__post_init__ would refuse the result.
            if record.self_pair:
                self_pair += 1
            elif record.resolved:
                resolved += 1
            else:
                unresolved += 1

        # RECONCILE AGAINST WHAT ACTUALLY LANDED, INSIDE THE TRANSACTION.
        # DrugCentralSummary's two identities are both computed in Python from
        # Python counters, so they can only ever prove the orchestrator is
        # self-consistent -- they cannot see the table. This is the one number
        # read back out of it. If a skipped insert (see the ON CONFLICT note
        # above) ever made the stored count disagree with `rows_bundleable`, the
        # summary would report rows the projection does not hold, and every
        # figure derived from it downstream would be quietly wrong. A stored
        # count that contradicts the reported one is a defect worth ABORTING on:
        # raising here rolls the whole run back through the `except` clause, so
        # the database keeps the previous projection rather than a miscounted
        # one. Scoped `WHERE ingest_run = %s` rather than counting the whole
        # table, so a concurrent run's rows could never mask or manufacture a
        # discrepancy in this one.
        stored = conn.execute(
            "SELECT count(*) FROM drugref.drugcentral_ddi_assertion "
            "WHERE ingest_run = %s", (run_id,)).fetchone()[0]
        if stored != len(bundleable):
            raise ValueError(
                f"drugcentral: {stored} assertion row(s) stored for run "
                f"{run_id}, but {len(bundleable)} bundleable row(s) were "
                f"written -- the projection does not hold what the summary "
                f"would report (a repeated or blank `source_id` collapsing two "
                f"rows onto one upstream_key is the known way this happens)")

        # Re-derive the open-question register LAST, exactly as every sibling
        # orchestrator does: it reads the projection this run just rebuilt, and
        # running it any earlier would close, then reopen, every question the
        # gap views feed on -- including gap kinds this run did not itself touch.
        questions.register_from_gaps(conn, run_id)
        pairs = conn.execute(
            "SELECT count(*) FROM drugref.drugcentral_ddi_pair").fetchone()[0]

        # finish_run does NOT commit on its own (see its docstring on why
        # symmetry with open_run would be a bug), so the "finished" stamp and
        # everything written above land in one atomic commit.
        provenance.finish_run(conn, run_id)
        conn.commit()
    except Exception:
        # A programmatic caller must not be left holding a connection in an
        # aborted transaction, and the log line records WHICH release and dump
        # failed -- matching onchigh_run/medrt_run/mesh_run/fda_cyp_run's tail.
        conn.rollback()
        log.exception("DrugCentral ingest failed for release %s (%s); rolled back",
                      release, dump_path)
        raise

    summary = DrugCentralSummary(
        rows_read=len(tables.ddi),
        rows_excluded_by_reference=len(tables.ddi) - len(bundleable),
        rows_bundleable=len(bundleable),
        rows_resolved=resolved,
        rows_self_pair=self_pair,
        rows_unresolved=unresolved,
        pairs=int(pairs),
        duplicate_keys=duplicate_keys)
    log.info("drugcentral: %s", summary)
    return summary
