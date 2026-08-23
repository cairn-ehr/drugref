# tests/test_drugcentral_parser.py
"""The pure half of the DrugCentral ingest: rule 6, and row -> record.

NO DATABASE. Every function here takes plain mappings, which is the architecture
invariant (parsers are pure; orchestrators own the transaction) and is also what
lets the rule-6 guard be tested by executing it rather than by reading it.
"""
import pytest

from drugref.ingest import drugcentral
from drugref.ingest.drugcentral_resolve import EndpointIndex, Registry

VHA = {
    "authors": "Veterans Health Administration",
    "title": ("Veterans Health Administration (VHA) National Drug File - "
              "Reference Terminology (NDF-RT)"),
}


def test_only_reference_2_is_bundleable():
    """CLAUDE.md rule 6. 1 is Stockley's (a copyrighted book) and 3 is Lexicomp
    (a commercial compendium); DrugCentral's own CC BY-SA on the compilation is
    not evidence of a right to relicense either."""
    rows = [{"id": "1", "ddi_ref_id": "1"}, {"id": "2", "ddi_ref_id": "2"},
            {"id": "3", "ddi_ref_id": "3"}]
    assert [row["id"] for row in drugcentral.bundleable_rows(rows)] == ["2"]


def test_the_bundleable_set_has_one_home():
    assert drugcentral.BUNDLEABLE_REF_IDS == frozenset({"2"})


def test_a_matching_reference_row_is_accepted():
    drugcentral.check_reference_identity({"2": VHA})


def test_a_renumbered_reference_aborts_rather_than_bundling_it():
    """`2` is a SURROGATE key in a dump published once. A re-publication is free
    to renumber its references, and a silent renumber would bundle Lexicomp under
    a constant that still reads 2. Licensing is a blocker, not a cleanup item."""
    lexicomp = {"authors": "Wolters Kluwer Health", "title": "Lexicomp Online"}
    with pytest.raises(drugcentral.ReferenceIdentityError) as caught:
        drugcentral.check_reference_identity({"2": lexicomp})
    assert "Lexicomp Online" in str(caught.value)
    assert "National Drug File" in str(caught.value)


def test_a_missing_reference_row_aborts():
    """Absence is not agreement. A dump whose reference table lost the row cannot
    be shown to be the one rule 6 was determined against."""
    with pytest.raises(drugcentral.ReferenceIdentityError):
        drugcentral.check_reference_identity({})


def test_a_row_resolves_both_endpoints_to_a_record():
    index = EndpointIndex(names={}, structural_keys={})
    registry = Registry(display_name={"warfarin": "u-1", "aspirin": "u-2"},
                        inchikey={}, cas={})
    record = drugcentral.resolve_row(
        {"source_id": "C56.1", "drug_class1": "Warfarin", "drug_class2": "aspirin",
         "description": "WARFARIN/ASPIRIN [VA Drug Interaction]",
         "ddi_risk": "Critical"},
        index, registry)
    assert record.upstream_key == "C56.1"
    assert record.endpoint_1_name == "Warfarin"      # VERBATIM, not folded
    assert record.moiety_1_uuid == "u-1"
    assert record.route_1 == "display_name"
    assert record.moiety_2_uuid == "u-2"
    assert record.severity_label == "Critical"


def test_an_unresolvable_endpoint_becomes_a_record_with_a_null_uuid():
    """Not a drop and not an error: a worklist entry, per db/039's precedent."""
    index = EndpointIndex(names={}, structural_keys={})
    registry = Registry(display_name={"warfarin": "u-1"}, inchikey={}, cas={})
    record = drugcentral.resolve_row(
        {"source_id": "C56.2", "drug_class1": "warfarin",
         "drug_class2": "phytomenadione",
         "description": "WARFARIN/PHYTONADIONE [VA Drug Interaction]",
         "ddi_risk": "Critical"},
        index, registry)
    assert record.moiety_2_uuid is None
    assert record.route_2 == "not_a_substance"


def test_read_tables_streams_the_four_tables_it_needs():
    # Field separators are REAL tab characters (single-backslash `\t` escapes,
    # decoding to one tab byte each) and the block terminator is a single
    # backslash followed by a dot (`"\\."` in source, matching
    # drugcentral_dump._COPY_TERMINATOR) -- the same convention
    # test_drugcentral_dump_parser.py's fixtures already use. A doubled
    # escape (`\\t`, `\\\\.`) would leave no real tab byte to split fields on
    # and a terminator that never matches, so iter_copy_rows would either
    # misparse every row or never see the block close.
    dump = [
        "COPY public.reference (id, authors, title) FROM stdin;",
        "2\tVeterans Health Administration\tNDF-RT",
        "\\.",
        "COPY public.ddi (id, drug_class1, drug_class2, ddi_ref_id) FROM stdin;",
        "1\twarfarin\taspirin\t2",
        "\\.",
        "COPY public.ignored (x) FROM stdin;",
        "9",
        "\\.",
    ]
    tables = drugcentral.read_tables(dump)
    assert tables.reference["2"]["authors"] == "Veterans Health Administration"
    assert len(tables.ddi) == 1
    assert tables.ddi[0]["drug_class1"] == "warfarin"
