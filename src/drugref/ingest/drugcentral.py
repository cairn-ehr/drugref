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
from collections.abc import Iterable, Iterator, Mapping

from drugref.ingest.drugcentral_dump import iter_copy_rows
from drugref.ingest.drugcentral_resolve import (
    EndpointIndex, Registry, resolve_endpoint,
)

SOURCE = "DRUGCENTRAL"
#: WHICH orchestrator this is, as distinct from SOURCE, the authority it reads
#: (db/025). Declared in provenance.WRITERS and db/049's CHECK -- a pair.
WRITER = "drugcentral_run"

#: THE ONE HOME FOR THE RULE-6 DETERMINATION. The re-measurement's own code
#: review found a SECOND hard-coded `ref_id == "2"` in its renderer, unconnected
#: to the set that filtered the rows, so this is not a hypothetical failure.
BUNDLEABLE_REF_IDS = frozenset({"2"})

#: What the dump must SAY reference 2 is, read from `reference` on 2026-08-23.
#: Compared before a single row is admitted -- see check_reference_identity.
EXPECTED_REFERENCE = {
    "2": ("Veterans Health Administration",
          "Veterans Health Administration (VHA) National Drug File - "
          "Reference Terminology (NDF-RT)"),
}

#: The four tables one pass over the dump decodes. `structures` and `synonyms`
#: are DrugCentral's own name tables and are what make the cascade possible
#: without drugref learning any spelling.
WANTED_TABLES = frozenset({"ddi", "reference", "structures", "synonyms"})


class ReferenceIdentityError(RuntimeError):
    """The dump's `reference` row does not match the one rule 6 was decided on."""


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

    @property
    def resolved(self) -> bool:
        """True when BOTH endpoints reached a moiety -- the pair-yielding case."""
        return self.moiety_1_uuid is not None and self.moiety_2_uuid is not None

    @property
    def self_pair(self) -> bool:
        """True when both endpoints resolved to ONE moiety.

        Its own bucket rather than folded into `resolved`, because it is neither
        an unresolvable row nor a pair: two endpoint names legitimately folding
        onto one moiety asserts nothing about an interaction between two drugs.
        Measured 2026-08-23: 0 of 7,571, and counting it is what would make that
        stop being true visibly.
        """
        return self.resolved and self.moiety_1_uuid == self.moiety_2_uuid


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
    those same 50 rows are the ones whose endpoints are class-named and do not
    resolve anyway (648 unresolvable rows over the whole table against 598 over
    this subset -- a difference of exactly 50).
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
