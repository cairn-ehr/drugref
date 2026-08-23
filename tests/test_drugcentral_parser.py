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
    """DERIVED from EXPECTED_REFERENCE, so the two constants cannot disagree.

    They were two frozensets that had to agree by hand, and widening only the
    admitted set made check_reference_identity die on a bare KeyError from the
    missing identity rather than refuse the reference -- in the file whose stated
    thesis is that a rule kept in two places is a rule this repo loses.
    """
    assert drugcentral.BUNDLEABLE_REF_IDS == frozenset({"2"})
    assert drugcentral.BUNDLEABLE_REF_IDS == frozenset(drugcentral.EXPECTED_REFERENCE)


DDI_ROW = {"ddi_ref_id": "2", "source_id": "C56^1^", "drug_class1": "warfarin",
           "drug_class2": "aspirin", "description": "W/A [VA Drug Interaction]",
           "ddi_risk": "Critical"}


def _tables(**overrides):
    fields = dict(ddi=(DDI_ROW,), reference={"2": VHA},
                  structures=({"id": "1"},), synonyms=({"id": "1"},))
    return drugcentral.DumpTables(**(fields | overrides))


def test_a_readable_dump_is_accepted():
    assert drugcentral.check_dump_is_readable(_tables()) is None


@pytest.mark.parametrize("table", ["ddi", "reference", "structures", "synonyms"])
def test_a_table_that_decoded_to_nothing_is_refused(table):
    """A renamed or dropped upstream table looks EXACTLY like this.

    The rule-6 guard reads `reference` and so cannot see it, and every
    reconciliation downstream is satisfied by zero: the ingest clears the previous
    release's projection, writes nothing, and reports success.
    """
    empty = {} if table == "reference" else ()
    with pytest.raises(drugcentral.DumpShapeError, match=table):
        drugcentral.check_dump_is_readable(_tables(**{table: empty}))


@pytest.mark.parametrize("column", sorted(drugcentral.REQUIRED_DDI_COLUMNS))
def test_a_renamed_ddi_column_is_refused_by_name(column):
    """MEASURED as the worst version of this failure.

    Renaming `ddi_ref_id` alone took the fixture's projection from 4 rows to 0 and
    reported '0 bundleable of 8 rows (8 EXCLUDED BY RULE 6)' with exit code 0 --
    blaming rule 6 for a loss rule 6 had no part in, because every row failed a
    test that reads a column no longer there.
    """
    renamed = {k: v for k, v in DDI_ROW.items() if k != column}
    renamed["renamed_" + column] = "x"
    with pytest.raises(drugcentral.DumpShapeError, match=column):
        drugcentral.check_dump_is_readable(_tables(ddi=(renamed,)))


def test_a_dump_with_nothing_bundleable_is_refused():
    """A release that dropped NDF-RT is well-formed and still must not run.

    Rebuilding a source to empty is a decision an operator makes deliberately, not
    one an ingest makes on their behalf while reporting success.
    """
    with pytest.raises(drugcentral.DumpShapeError, match="would publish nothing"):
        drugcentral.check_something_is_bundleable((), 7621)


def test_the_two_refusals_say_different_things():
    """They are different failures and an operator has to be told which happened."""
    shape = pytest.raises(drugcentral.DumpShapeError)
    with shape as unreadable:
        drugcentral.check_dump_is_readable(_tables(ddi=()))
    with shape as unbundleable:
        drugcentral.check_something_is_bundleable((), 10)
    assert str(unreadable.value) != str(unbundleable.value)


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


def test_the_guard_checks_AUTHORS_and_not_only_the_title():
    """Half of the rule-6 guard was untested, and it is the unrecoverable half.

    The dump-level forgery this suite already had replaces "Veterans Health
    Administration" -- a string that appears in BOTH `authors` and `title` -- so
    the title comparison fired alone and dropping the authors comparison entirely
    left the suite green. This forgery changes ONLY the authors.
    """
    forged = {"2": {"authors": "Wolters Kluwer Health", "title": VHA["title"]}}
    with pytest.raises(drugcentral.ReferenceIdentityError, match="Wolters Kluwer"):
        drugcentral.check_reference_identity(forged)


def test_the_guard_checks_the_TITLE_and_not_only_the_authors():
    forged = {"2": {"authors": VHA["authors"], "title": "Lexicomp Online"}}
    with pytest.raises(drugcentral.ReferenceIdentityError, match="Lexicomp"):
        drugcentral.check_reference_identity(forged)


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


# ---- AssertionRecord.outcome: FOUR DISJOINT BUCKETS, chosen by the record -----
#
# These replace three tests that pinned the strict-subset relationship between
# `resolved` and `self_pair`. That relationship is gone: a row now reports ONE
# `Outcome`, so the branch ordering a caller used to have to remember is not
# expressible. Building AssertionRecord directly, not through resolve_row: what is
# under test is the record's own classification, not the resolver.


def _record(**overrides):
    """One valid record, with the field(s) under test overridden."""
    fields = dict(
        upstream_key="C56.1", endpoint_1_name="warfarin", endpoint_2_name="aspirin",
        upstream_label="WARFARIN/ASPIRIN [VA Drug Interaction]",
        severity_label="Critical",
        moiety_1_uuid="u-1", moiety_2_uuid="u-2",
        route_1="display_name", route_2="display_name")
    return drugcentral.AssertionRecord(**(fields | overrides))


def test_two_endpoints_on_two_moieties_is_a_pair():
    assert _record().outcome is drugcentral.Outcome.PAIR


def test_two_endpoints_on_ONE_moiety_is_a_self_pair_not_a_pair():
    """The case that used to be BOTH `resolved` and `self_pair`.

    Two endpoint names legitimately folding onto one moiety asserts nothing about
    an interaction between two drugs. Measured 2026-08-23: 0 of 7,571 -- which is
    exactly why a live-data spot check would never catch a regression here.
    """
    assert _record(endpoint_2_name="Warfarin",
                   moiety_2_uuid="u-1").outcome is drugcentral.Outcome.SELF_PAIR


def test_an_endpoint_that_reached_no_moiety_is_unresolved():
    assert _record(endpoint_2_name="phytomenadione", moiety_2_uuid=None,
                   route_2="unresolved").outcome is drugcentral.Outcome.UNRESOLVED


def test_a_blank_endpoint_outranks_unresolved_and_gets_its_own_bucket():
    """A malformed row must not be counted as a failure to resolve.

    A blank endpoint reaches NO other layer that could report it: it is excluded
    from drugcentral_ddi_pair by the NULL-uuid filter and from
    gap_unresolved_ddi_endpoint by the `<> ''` filter that has to be there. If it
    is not counted here it is counted nowhere, which is why BLANK_ENDPOINT is
    tested before UNRESOLVED even though such a row is also unresolved.
    """
    assert _record(endpoint_2_name="", moiety_2_uuid=None,
                   route_2="blank_endpoint").outcome is (
        drugcentral.Outcome.BLANK_ENDPOINT)
    assert _record(endpoint_1_name="   ", moiety_1_uuid=None,
                   route_1="blank_endpoint").outcome is (
        drugcentral.Outcome.BLANK_ENDPOINT)


def test_every_outcome_is_reachable_so_the_partition_is_total():
    """No bucket is unreachable -- an enum member nothing produces is a lie."""
    produced = {
        _record().outcome,
        _record(endpoint_2_name="Warfarin", moiety_2_uuid="u-1").outcome,
        _record(moiety_2_uuid=None, route_2="unresolved").outcome,
        _record(endpoint_2_name="", moiety_2_uuid=None,
                route_2="blank_endpoint").outcome,
    }
    assert produced == set(drugcentral.Outcome)


def test_a_record_refuses_a_uuid_that_disagrees_with_its_route():
    """The invariant `Resolution` enforces and this record used to DISCARD.

    resolve_row flattens two Resolutions into four bare fields, and this type
    re-admitted every state they refuse -- so the next thing to object was a
    CHECK constraint, mid-transaction, inside the write loop.
    """
    with pytest.raises(ValueError, match="disagrees with"):
        _record(route_2="not_a_substance")          # a uuid on an unresolved route
    with pytest.raises(ValueError, match="disagrees with"):
        _record(moiety_1_uuid=None)                 # a resolved route with no uuid


def test_a_record_refuses_a_route_outside_the_closed_vocabulary():
    with pytest.raises(ValueError, match="is not one of"):
        _record(route_1="banana")


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
