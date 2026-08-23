# src/drugref/ingest/drugcentral.py
"""The PURE half of the DrugCentral `ddi` ingest: rule 6, and row -> record.

No database access of any kind, per the architecture invariant. Everything here
takes plain mappings, which is also what lets the rule-6 guard be tested by
EXECUTING it rather than by reading a comment that claims it exists.

WHAT THIS MODULE REFUSES TO DO:

* It admits ONE reference. `ddi_ref_id = 2` is the VHA's NDF-RT, a US federal
  work; `1` is Stockley's Drug Interactions (a copyrighted book) and `3` is
  Lexicomp Online (a commercial compendium). DrugCentral publishes the
  compilation under CC BY-SA 4.0, WHICH IS NOT EVIDENCE OF A RIGHT TO RELICENSE
  A THIRD-PARTY COMPENDIUM INSIDE IT.
* It does not trust the number `2` on its own. See check_reference_identity.
* It bridges no name. The cascade in drugcentral_resolve keys on STRUCTURE --
  display_name, then InChIKey, then CAS -- which took resolution from 857 of 924
  endpoint names to 914 with no hand-maintained synonym list at all.
"""
import dataclasses
import enum
from collections.abc import Iterable, Iterator, Mapping, Sequence

from drugref.ingest.drugcentral_dump import iter_copy_rows
from drugref.ingest.drugcentral_resolve import (
    EndpointIndex, Registry, Resolution, resolve_endpoint,
)

SOURCE = "DRUGCENTRAL"
#: WHICH orchestrator this is, as distinct from SOURCE, the authority it reads
#: (db/025). Declared in provenance.WRITERS and db/049's CHECK -- a pair.
WRITER = "drugcentral_run"

#: What the dump must SAY each admitted reference is, read from `reference` on
#: 2026-08-23. Compared before a single row is admitted -- see
#: check_reference_identity.
EXPECTED_REFERENCE = {
    "2": ("Veterans Health Administration",
          "Veterans Health Administration (VHA) National Drug File - "
          "Reference Terminology (NDF-RT)"),
}

#: THE ONE HOME FOR THE RULE-6 DETERMINATION -- now literally one, DERIVED from
#: the identities above rather than restated beside them. The re-measurement's own
#: code review found a SECOND hard-coded `ref_id == "2"` in its renderer,
#: unconnected to the set that filtered the rows, so this is not a hypothetical
#: failure -- and while this was a second frozenset, widening it alone made
#: `check_reference_identity` die on a bare `KeyError` from the missing identity
#: instead of refusing the reference. A file whose thesis is that a rule kept in
#: two places is a rule this repo loses does not get to keep this one in two.
BUNDLEABLE_REF_IDS = frozenset(EXPECTED_REFERENCE)

#: The columns `resolve_row` and `bundleable_rows` actually read out of `ddi`.
#: Checked for PRESENCE before any row is admitted, because a renamed column is
#: silent otherwise: `.get()` returns None for every row, every row fails the
#: rule-6 test, and the ingest clears the projection and reports success. See
#: check_dump_is_readable.
REQUIRED_DDI_COLUMNS = frozenset({
    "ddi_ref_id", "source_id", "drug_class1", "drug_class2",
    "description", "ddi_risk",
})

#: The four tables one pass over the dump decodes. `structures` and `synonyms`
#: are DrugCentral's own name tables and are what make the cascade possible
#: without drugref learning any spelling.
WANTED_TABLES = frozenset({"ddi", "reference", "structures", "synonyms"})


class ReferenceIdentityError(RuntimeError):
    """The dump's `reference` row does not match the one rule 6 was decided on."""


class DumpShapeError(RuntimeError):
    """The dump does not carry the tables and columns this ingest reads.

    ITS OWN TYPE, and not a subclass of ReferenceIdentityError, because the two
    refusals mean opposite things to an operator: a reference-identity failure
    says the dump is a DIFFERENT publication than rule 6 cleared, and this says
    the dump is UNREADABLE by this code. Only the first is a licensing question.
    """


@dataclasses.dataclass(frozen=True)
class DumpTables:
    """The four tables one streaming pass collects. All four are small.

    `reference` is keyed by id because that is how it is looked up; the other
    three are read in order and stay tuples.
    """

    ddi: tuple[Mapping[str, str | None], ...]
    reference: Mapping[str, Mapping[str, str | None]]
    structures: tuple[Mapping[str, str | None], ...]
    synonyms: tuple[Mapping[str, str | None], ...]


class Outcome(enum.Enum):
    """The four ways one published row can land. DISJOINT AND TOTAL, by construction.

    A row is exactly one of these, so the counting loop cannot double-count and
    `DrugCentralSummary`'s bucket identity cannot be satisfied by a miscount that
    happens to sum. The order of `AssertionRecord.outcome`'s tests is part of the
    definition, not a convention a caller has to remember:

    * `BLANK_ENDPOINT` first -- a malformed upstream row is not a failure to
      resolve, and it is invisible everywhere else (the question view must drop
      blank names), so if it is not counted here it is counted nowhere.
    * `UNRESOLVED` next -- at least one endpoint drugref cannot key.
    * `SELF_PAIR` before `PAIR` -- both endpoints reached ONE moiety, which
      asserts nothing about an interaction between two drugs. Measured
      2026-08-23: 0 of 7,571, and it is a bucket rather than a footnote so that
      cannot stop being true unnoticed.
    * `PAIR` -- two endpoints, two different moieties. The only one that reaches
      `drugcentral_ddi_pair`.
    """

    BLANK_ENDPOINT = "blank_endpoint"
    UNRESOLVED = "unresolved"
    SELF_PAIR = "self_pair"
    PAIR = "pair"


@dataclasses.dataclass(frozen=True, kw_only=True)
class AssertionRecord:
    """One published row, with both endpoints resolved or explained.

    KEYWORD-ONLY for the reason interactions.add_drugcentral_assertion is: the
    two endpoints are UNORDERED, so a positional swap would be undetectable
    downstream.
    """

    upstream_key: str
    endpoint_1_name: str
    endpoint_2_name: str
    upstream_label: str
    severity_label: str
    moiety_1_uuid: str | None
    moiety_2_uuid: str | None
    route_1: str
    route_2: str

    def __post_init__(self) -> None:
        """Re-assert the invariant `Resolution` enforces and this record discarded.

        `resolve_row` destructures two `Resolution`s -- each of which has already
        refused any disagreement between `moiety_uuid is None` and the route's
        resolved-ness -- into four bare fields, and this type used to re-admit
        every state they refuse: `moiety_1_uuid="u-1", route_1="not_a_substance"`
        constructed cleanly, as did `route_1="banana"`. The next thing that
        objected was Postgres, mid-transaction, inside the write loop: an abort of
        the whole run rather than a refusal in the pure layer, which is the
        opposite of the line `check_reference_identity` takes in this same file.

        Constructing the two `Resolution`s is the whole check -- the vocabulary and
        the biconditional both live there, so this adds no second home for either.
        """
        Resolution(self.moiety_1_uuid, self.route_1)
        Resolution(self.moiety_2_uuid, self.route_2)

    @property
    def outcome(self) -> "Outcome":
        """WHICH of the four disjoint buckets this row lands in. Total, and ordered.

        REPLACES A PAIR OF OVERLAPPING BOOLEANS. `resolved` and `self_pair` were
        both true for a self-pair (`self_pair` was a STRICT SUBSET of `resolved`),
        so every caller had to test `self_pair` FIRST or double-count -- an
        ordering no type could enforce and that cost 21 lines of docstring here, a
        4-line comment at the one call site, three tests, and a ~50-line database
        fixture built solely so that swapping two branches would fail. A caller
        cannot get an enum's branches in the wrong order.

        The measurement instrument reached the same shape by a different route:
        `tools/drugcentral_ddi_measure` discriminates through `unordered_pair`
        returning None, so its buckets are disjoint BY CONSTRUCTION. This is that,
        as a type.
        """
        if not self.endpoint_1_name.strip() or not self.endpoint_2_name.strip():
            return Outcome.BLANK_ENDPOINT
        if self.moiety_1_uuid is None or self.moiety_2_uuid is None:
            return Outcome.UNRESOLVED
        if self.moiety_1_uuid == self.moiety_2_uuid:
            return Outcome.SELF_PAIR
        return Outcome.PAIR

def read_tables(lines: Iterable[str]) -> DumpTables:
    """Collect the four wanted tables in ONE streaming pass over the dump.

    The dump is ~1.4 GB gzipped and ~5 GB of text; `iter_copy_rows` skips a block
    for an unwanted table without decoding a field, which is what makes one pass
    for four tables cheap. All four fit in memory comfortably -- `ddi` is 7,621
    rows, `reference` 1,195, `structures` 4,995 and `synonyms` 23,369.
    """
    ddi: list[Mapping[str, str | None]] = []
    reference: dict[str, Mapping[str, str | None]] = {}
    structures: list[Mapping[str, str | None]] = []
    synonyms: list[Mapping[str, str | None]] = []

    for table, row in iter_copy_rows(lines, WANTED_TABLES):
        if table == "ddi":
            ddi.append(row)
        elif table == "reference":
            row_id = row.get("id")
            if row_id:
                reference[row_id] = row
        elif table == "structures":
            structures.append(row)
        elif table == "synonyms":
            synonyms.append(row)

    return DumpTables(ddi=tuple(ddi), reference=reference,
                      structures=tuple(structures), synonyms=tuple(synonyms))


def check_dump_is_readable(tables: DumpTables) -> None:
    """Refuse a dump this code cannot actually read, BEFORE anything is cleared.

    WHY THIS EXISTS. Every reconciliation in this slice proves the orchestrator is
    SELF-CONSISTENT and none proves it published anything, so the all-zeros run
    satisfies all of them: `rows_read = excluded + bundleable` holds at 0 = 0 + 0,
    `bundleable = resolved + self_pair + unresolved` holds at 0 = 0 + 0 + 0, and
    the orchestrator's read-back holds because `stored (0) == len(bundleable) (0)`.
    Meanwhile `clear_source_drugcentral` has already deleted the previous release's
    rows and `register_from_gaps` has deleted its questions. Measured: renaming one
    column in the fixture -- `ddi_ref_id` -> `reference_id`, exactly what a
    re-publication is free to do -- took the projection from 4 rows to 0, reported
    `0 bundleable of 8 rows (8 excluded by rule 6)`, and exited 0.

    The summary line was the worst part of it: it BLAMED RULE 6 for a loss rule 6
    had no part in, because `bundleable_rows` reads a column that is no longer
    there and every row fails the test for the same wrong reason.

    Three distinct failures, three distinct messages -- they are not the same
    problem and an operator has to be told which one happened:

    1. a table that decoded to nothing (renamed or dropped upstream);
    2. `ddi` missing a column this code reads (renamed upstream);
    3. every `ddi` row excluded, so the ingest would publish nothing.

    Case 3 is the one that is not obviously an error -- it is what a genuinely
    all-Lexicomp release would look like. It is still refused: rebuilding a source
    to empty is a decision an operator makes deliberately, not one an ingest makes
    on their behalf while reporting success. `fda_cyp.py` takes the same line
    ("the data table carries no rows").

    Raises:
        DumpShapeError: naming which of the three it is, and what was expected.
    """
    empty = sorted(name for name in WANTED_TABLES if not getattr(tables, name))
    if empty:
        raise DumpShapeError(
            f"the dump decoded no rows at all for {', '.join(empty)} -- this "
            f"code reads {', '.join(sorted(WANTED_TABLES))}, so a renamed or "
            f"dropped table would look exactly like this. Refusing to ingest: "
            f"continuing would clear the previous release's projection and "
            f"report success.")

    missing = sorted(REQUIRED_DDI_COLUMNS - set(tables.ddi[0]))
    if missing:
        raise DumpShapeError(
            f"the dump's `ddi` table carries no column(s) {', '.join(missing)}; "
            f"it has {', '.join(sorted(tables.ddi[0]))}. Every row would fail the "
            f"rule-6 test for the wrong reason and the ingest would report them "
            f"as excluded. Refusing to ingest.")


def check_something_is_bundleable(bundleable: Sequence[object],
                                  rows_read: int) -> None:
    """Refuse a dump every row of which rule 6 excludes. See check_dump_is_readable.

    SEPARATE FROM THE SHAPE CHECK because it can only run after the filter, and
    because it is the one refusal here that a well-formed dump could legitimately
    trigger -- a release that dropped NDF-RT entirely. The operator is told the
    difference rather than left to infer it from a count.
    """
    if not bundleable:
        raise DumpShapeError(
            f"none of the {rows_read} `ddi` row(s) read cite a reference rule 6 "
            f"admits ({', '.join(sorted(BUNDLEABLE_REF_IDS))}), so this ingest "
            f"would publish nothing while clearing what the previous release "
            f"published. Refusing: rebuilding this source to empty is a decision "
            f"to make deliberately, not one to discover from a summary line.")


def check_reference_identity(
        reference: Mapping[str, Mapping[str, str | None]]) -> None:
    """Refuse the dump unless every bundleable id IS the reference rule 6 cleared.

    WHY THE CONSTANT IS NOT ENOUGH. `2` is a surrogate key in a table of 1,195
    rows, in a database that has been published exactly once. A re-publication is
    free to renumber its references, and a silent renumber would bundle Lexicomp
    under a constant that still reads `2` -- with nothing anywhere raising. This
    is the one place in the slice where being wrong is unrecoverable after
    distribution, so the check is an abort rather than a warning or a skip.

    Raises:
        ReferenceIdentityError: with BOTH strings printed, so the operator can see
            what the dump claims beside what drugref expected.
    """
    for ref_id in sorted(BUNDLEABLE_REF_IDS):
        expected_authors, expected_title = EXPECTED_REFERENCE[ref_id]
        row = reference.get(ref_id)
        if row is None:
            raise ReferenceIdentityError(
                f"the dump's `reference` table has no row {ref_id!r}, so it "
                f"cannot be shown to be the release rule 6 was determined "
                f"against (expected {expected_title!r})")
        authors, title = (row.get("authors") or ""), (row.get("title") or "")
        if authors.strip() != expected_authors or title.strip() != expected_title:
            raise ReferenceIdentityError(
                f"reference {ref_id!r} in this dump is "
                f"{authors!r} / {title!r}, but rule 6 admits only "
                f"{expected_authors!r} / {expected_title!r}. Refusing to ingest: "
                f"a renumbered reference would bundle a source drugref may not "
                f"redistribute.")


def bundleable_rows(
        ddi: Iterable[Mapping[str, str | None]],
) -> Iterator[Mapping[str, str | None]]:
    """Yield only the rows CLAUDE.md rule 6 permits drugref to bundle.

    Excluding 37 Lexicomp rows and 13 Stockley's rows costs nothing measurable:
    every one of those 50 rows has at least ONE class-named endpoint, so not one
    of them could have become a pair. Measured 2026-08-23 through the cascade this
    module actually uses: 87 rows with an unresolvable endpoint over the whole
    table against 37 over this subset -- a difference of exactly 50.

    BOTH FIGURES IN THAT SENTENCE WERE WRONG AND ARE WORTH THE CORRECTION.

    * It said "648 ... against 598". Those are the same 50 apart, which is what
      made them look right, but they are the `name matching (issue #101)` column
      of the re-measurement -- the display_name-only approach this module's own
      header says the cascade replaced. As written the docstring contradicted the
      shipped log line, db/049's view comment and the measurement's own read-back,
      all three of which say 37.
    * It said "those same 50 rows are the ones whose endpoints are class-named",
      which reads as BOTH endpoints. 21 of the 50 carry an ordinary drug name at
      one end -- buspirone, tramadol, clopidogrel, dextromethorphan and 17 more;
      only 29 are class-named at both. PROJECT-NOTES had already retired this
      exact phrasing once and it came back.
    """
    for row in ddi:
        if (row.get("ddi_ref_id") or "") in BUNDLEABLE_REF_IDS:
            yield row


def resolve_row(row: Mapping[str, str | None],
                index: EndpointIndex,
                registry: Registry) -> AssertionRecord:
    """Turn one published `ddi` row into a record, resolved or explained.

    The endpoint NAMES are carried through VERBATIM. Folding is the resolver's
    rule (`fold_name`) and belongs in one place; storing the folded form here
    would lose the spelling a later release has to be diffed against.

    `source_id` rather than `id` is the key: it is the VA's own identifier for
    the interaction record, and all 7,571 bundleable rows carry a distinct one.
    """
    endpoint_1 = row.get("drug_class1") or ""
    endpoint_2 = row.get("drug_class2") or ""
    first = resolve_endpoint(endpoint_1, index, registry)
    second = resolve_endpoint(endpoint_2, index, registry)
    return AssertionRecord(
        upstream_key=row.get("source_id") or "",
        endpoint_1_name=endpoint_1,
        endpoint_2_name=endpoint_2,
        upstream_label=row.get("description") or "",
        severity_label=row.get("ddi_risk") or "",
        moiety_1_uuid=first.moiety_uuid,
        moiety_2_uuid=second.moiety_uuid,
        route_1=first.route,
        route_2=second.route,
    )
