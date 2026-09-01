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
    Registry, build_endpoint_index, first_wins, fold_key, fold_name,
)

log = logging.getLogger(__name__)


@dataclasses.dataclass(frozen=True)
class DrugCentralSummary:
    """What one ingest did, in buckets that RECONCILE.

    `rows_read` = `rows_excluded_by_reference` + `rows_bundleable`, and
    `rows_bundleable` = `rows_resolved` + `rows_self_pair` + `rows_unresolved` +
    `rows_blank_endpoint`. __post_init__ refuses any other arithmetic, because a
    summary whose buckets do not sum is a number nobody can check -- curate_onchigh
    counted entries it had silently dropped, and the re-measurement's Measurement
    guard exists for the same reason.

    BUT BE CLEAR ABOUT WHAT THOSE TWO IDENTITIES CAN AND CANNOT CATCH. Both are
    computed in Python from Python counters, and at the one call site both are
    satisfied BY CONSTRUCTION: `rows_excluded_by_reference` is computed as
    `rows_read - rows_bundleable`, and the counting loop dispatches on
    `drugcentral.Outcome`, which is total, so every row increments exactly one
    bucket. They are a contract on the type for future callers, not a guard that
    can fail where it is currently used -- an earlier comment credited
    __post_init__ with catching a swapped-branch miscount, which it never could.
    The two checks that CAN fail are `pairs > rows_resolved` here, and the
    read-back in `ingest_drugcentral` that compares the stored row count against
    `rows_bundleable`; that one needs the open transaction, so it lives there.

    `rows_resolved` counts rows whose two endpoints reached TWO DIFFERENT moieties;
    `rows_self_pair` counts those that reached ONE (0 of 7,571 on the 2023 release,
    and a bucket rather than a footnote so it cannot become nonzero unnoticed);
    `rows_blank_endpoint` counts MALFORMED rows whose endpoint text is empty (0 of
    7,571, and its own bucket because a blank endpoint reaches no other layer that
    could report it -- the question view has to drop blank names). `pairs` is the
    DISTINCT UNORDERED pair count from the view, which is smaller than
    `rows_resolved` because 33 pairs are published in both orders.
    """

    rows_read: int
    rows_excluded_by_reference: int
    rows_bundleable: int
    rows_resolved: int
    rows_self_pair: int
    rows_unresolved: int
    rows_blank_endpoint: int
    pairs: int
    duplicate_keys: int

    def __post_init__(self) -> None:
        if self.rows_excluded_by_reference + self.rows_bundleable != self.rows_read:
            raise ValueError(
                f"excluded ({self.rows_excluded_by_reference}) + bundleable "
                f"({self.rows_bundleable}) do not sum to read ({self.rows_read})")
        landed = (self.rows_resolved + self.rows_self_pair + self.rows_unresolved
                  + self.rows_blank_endpoint)
        if landed != self.rows_bundleable:
            raise ValueError(
                f"resolved ({self.rows_resolved}) + self-pair "
                f"({self.rows_self_pair}) + unresolved ({self.rows_unresolved}) "
                f"+ blank-endpoint ({self.rows_blank_endpoint}) do not sum to "
                f"bundleable ({self.rows_bundleable})")
        # THE ONLY CHECK HERE THAT IS NOT TAUTOLOGICAL AT THE CALL SITE, which is
        # why it earns its place. Both identities above are satisfied by
        # construction where this type is built -- `rows_excluded` is computed as
        # `read - bundleable`, and the counting loop's `Outcome` is total, so each
        # row increments exactly one bucket whatever order the branches are in.
        # `pairs` is the one field read back out of the DATABASE, and it was
        # checked by nothing. Each PAIR row yields at most one distinct unordered
        # pair (fewer, when both orientations of one pair are published), so
        # `pairs > rows_resolved` means the count came from the wrong relation or a
        # previous run's rows are still resident.
        if self.pairs > self.rows_resolved:
            raise ValueError(
                f"pairs ({self.pairs}) exceeds resolved rows "
                f"({self.rows_resolved}); each resolved row yields at most one "
                f"distinct unordered pair, so this count did not come from "
                f"drugcentral_ddi_pair for this run alone")

    def __str__(self) -> str:
        return (f"{self.rows_bundleable} bundleable of {self.rows_read} rows "
                f"({self.rows_excluded_by_reference} excluded by rule 6) -> "
                f"{self.pairs} pairs; {self.rows_unresolved} unresolved, "
                f"{self.rows_self_pair} self-pairs, "
                f"{self.rows_blank_endpoint} blank endpoints, "
                f"{self.duplicate_keys} colliding registry keys")


def load_registry(conn: psycopg.Connection) -> tuple[Registry, int]:
    """The drugref side of the join: three lookups onto `moiety_uuid`, ONE snapshot.

    ONE STATEMENT, NOT THREE, AND THAT IS THE WHOLE POINT. A single statement always
    sees a single snapshot under any isolation level, so this reads consistently
    without the transaction having to be REPEATABLE READ. It used to be three
    separate SELECTs, which is why the caller raised the isolation level -- and
    that snapshot then covered the ENTIRE run, including `questions.register_from_gaps`,
    which upserts `open_question` rows for all eighteen gap kinds, most of which this
    run never touches. Under REPEATABLE READ an upsert onto a row a concurrent
    transaction has updated raises SerializationFailure immediately rather than
    blocking and proceeding, so any other writer touching any question row aborted the
    whole DrugCentral ingest -- and nothing in this codebase retries. Measured
    directly: the same upsert against the same concurrent update raises
    SerializationFailure under REPEATABLE READ and succeeds under READ COMMITTED.
    This orchestrator was the only one in the repo that raised isolation at all.

    EVERY READ IS ORDERED, and that is not cosmetic. `identity_claim` is unique on
    (moiety_uuid, scheme, value) and deliberately NOT across moieties, so two
    moieties may legitimately carry one CAS number -- measured 2026-08-23, 14
    InChIKeys and 29 CAS numbers are claimed by more than one. An unordered
    single-row read would let the same dump resolve differently on two runs. The
    ORDER BY is stated once, over the union, and `lookup` is the first sort key so
    each lookup's rows stay contiguous and internally ordered.

    Live claims only (`superseded_by IS NULL`): a corrected-away identifier must
    not resurrect a resolution.

    Returns the registry and the number of DISTINCT keys claimed more than once,
    which the summary reports rather than discards.
    """
    rows = conn.execute(
        "  SELECT 'display_name' AS lookup, display_name AS key, "
        "         moiety_uuid::text AS moiety_uuid "
        "    FROM drugref.substance_moiety "
        "   UNION ALL "
        "  SELECT scheme, value, moiety_uuid::text "
        "    FROM drugref.identity_claim "
        "   WHERE scheme IN ('INCHIKEY', 'CAS') AND superseded_by IS NULL "
        "   ORDER BY lookup, key, moiety_uuid").fetchall()

    by_lookup: dict[str, list[tuple[str, str]]] = {
        "display_name": [], "INCHIKEY": [], "CAS": []}
    for lookup, key, moiety_uuid in rows:
        by_lookup[lookup].append((key, moiety_uuid))

    # `first_wins` folds with the SAME rule Registry looks up under, so
    # de-duplication, first-wins and the collision count all happen in one key
    # space. They did not: this used to de-duplicate on the raw key and let
    # Registry re-key the survivors last-wins, silently and uncounted.
    display_name, dup_names = first_wins(by_lookup["display_name"], fold_name)
    inchikey, dup_keys = first_wins(by_lookup["INCHIKEY"], fold_key)
    cas, dup_cas = first_wins(by_lookup["CAS"], fold_key)

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
    clock = provenance.start_clock()  # FIRST: see provenance.start_clock (#159)
    # AUTOCOMMIT VOIDS EVERY GUARANTEE BELOW, AND POSTGRES ONLY WHISPERS ABOUT IT.
    # Under autocommit each statement is its own transaction, so `conn.rollback()`
    # in the `except` rolls back nothing and a failure anywhere between the clear
    # and `finish_run` leaves the projection cleared and half-rewritten -- the
    # "worse than none" outcome this module's docstring says it refuses. Measured:
    # the server answers a mis-placed SET TRANSACTION with a NOTICE, not an error,
    # and psycopg discards notices unless a handler is installed, so the ingest
    # reported success having silently lost its atomicity. `db.connect` does not
    # set autocommit, so the CLI path was always safe; the `except` clause's own
    # comment contemplates "a programmatic caller", and this is the line that makes
    # that caller's mistake loud instead of invisible.
    if conn.autocommit:
        raise ValueError(
            "drugcentral: this ingest owns its transactions and must not be handed "
            "an autocommit connection -- the per-source clear and the rows that "
            "replace it have to commit together or not at all.")

    dump_path = pathlib.Path(dump_path)
    digest = checksum(dump_path)

    with gzip.open(dump_path, "rt", encoding="utf-8") as handle:
        tables = drugcentral.read_tables(handle)

    # THE FLOOR CHECKS, AND RULE 6, ALL BEFORE ANY RUN ROW EXISTS. A refusal must
    # leave the database exactly as it was, and an ingest_run with finished_at NULL
    # is not "exactly as it was". Shape first: a dump this code cannot read makes
    # every later question meaningless, and the rule-6 guard reads a DIFFERENT
    # table (`reference`), so it passes cheerfully on a dump whose `ddi` table has
    # been renamed out from under it.
    drugcentral.check_dump_is_readable(tables)
    drugcentral.check_reference_identity(tables.reference)

    bundleable = tuple(drugcentral.bundleable_rows(tables.ddi))
    # AFTER the filter, because it is the filter's result that decides it: an
    # ingest that would publish nothing must not silently clear what the last one
    # published. See check_dump_is_readable for the measurement behind both.
    drugcentral.check_something_is_bundleable(bundleable, len(tables.ddi))
    index = build_endpoint_index(tables.structures, tables.synonyms)

    try:
        run_id = provenance.open_run(conn, source=drugcentral.SOURCE,
                                     upstream_release=release,
                                     source_checksum=digest,
                                     writer=drugcentral.WRITER, clock=clock)

        # The work transaction. NO ISOLATION BUMP: `load_registry` is a single
        # statement, and a single statement sees a single snapshot at any isolation
        # level, so the consistency the cascade needs costs nothing here. This used
        # to raise the whole transaction to REPEATABLE READ for three separate
        # reads, which then also covered `register_from_gaps` below and made any
        # concurrent question write abort the run with SerializationFailure -- see
        # load_registry's docstring for the measurement.
        registry, duplicate_keys = load_registry(conn)

        interactions.clear_source_drugcentral(conn, drugcentral.SOURCE)

        counts: dict[drugcentral.Outcome, int] = {
            outcome: 0 for outcome in drugcentral.Outcome}
        for row in bundleable:
            record = drugcentral.resolve_row(row, index, registry)
            # The return value (False on an ON CONFLICT DO NOTHING skip) is
            # deliberately IGNORED here, and that is safe rather than sloppy:
            # the table's PRIMARY KEY is (ingest_run, source, upstream_key), so
            # a conflict can only happen BETWEEN TWO ROWS OF THIS SAME RUN. It
            # is not relied on: the count below reads the table back, and
            # db/050 additionally refuses a blank `upstream_key` at INSERT, so
            # the one way two rows of one run could collide on this release's
            # data -- two NULL `source_id`s folding onto the empty key -- now
            # aborts on the FIRST one rather than needing a second to collide.
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
            # ONE disjoint bucket per row, chosen by the record itself. This was
            # an `if record.self_pair: ... elif record.resolved: ...` chain whose
            # ORDER was load-bearing and enforceable by nothing -- self_pair was a
            # strict subset of resolved, so the branches read in the wrong order
            # silently folded every self-pair into the resolved bucket while the
            # summary's identities still summed perfectly. `Outcome` has no order
            # for a caller to get wrong.
            counts[record.outcome] += 1

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

        # BUILT INSIDE THE TRANSACTION, BEFORE THE COMMIT. Every guard in this
        # function raises where a rollback can still undo the run; the summary was
        # the one exception, constructed after `conn.commit()` -- so a bucket
        # identity that failed would have reported a ValueError with the
        # projection already published and the run already stamped finished, the
        # exact reverse of the harm direction everything else here chooses.
        summary = DrugCentralSummary(
            rows_read=len(tables.ddi),
            rows_excluded_by_reference=len(tables.ddi) - len(bundleable),
            rows_bundleable=len(bundleable),
            rows_resolved=counts[drugcentral.Outcome.PAIR],
            rows_self_pair=counts[drugcentral.Outcome.SELF_PAIR],
            rows_unresolved=counts[drugcentral.Outcome.UNRESOLVED],
            rows_blank_endpoint=counts[drugcentral.Outcome.BLANK_ENDPOINT],
            pairs=int(pairs),
            duplicate_keys=duplicate_keys)

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

    log.info("drugcentral: %s", summary)
    return summary
