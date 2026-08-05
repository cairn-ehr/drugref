# tests/test_gsrs_parser.py
"""The direction convention is the whole point of this module (slice-3 spec 3.2).

GSRS stores a relationship of type "A->B" on record X pointing at Y, and X plays
role B while Y plays role A -- the stored edge is the INBOUND one. Read naively,
one "salt" in the real release had 124 parents; read correctly, the busiest PARENTS
are Maleic Acid (124 salts) and Tartaric Acid (123).

These tests use SYNTHETIC identifiers on purpose: the rule under test is about
role assignment, not about any substance's real UNII. The real-release properties
are pinned separately, on extracted bytes, in tests/test_gsrs_fixture.py.
"""

import gzip
import json

from drugref.ingest import gsrs


def test_salt_to_parent_puts_the_TARGET_on_the_salt_side():
    # "SALT/SOLVATE->PARENT" stored on X: X is the PARENT, Y is the SALT.
    edge = gsrs.normalise_relationship(
        "PARENT0001", "SALT/SOLVATE->PARENT", "SALT000001"
    )
    assert edge == gsrs.CompositionEdge(
        substance_unii="SALT000001",
        component_unii="PARENT0001",
        relation=gsrs.SALT_SOLVATE,
    )


def test_parent_to_salt_puts_the_RECORD_on_the_salt_side():
    # The mirror encoding of the SAME edge, from the other end.
    edge = gsrs.normalise_relationship(
        "SALT000001", "PARENT->SALT/SOLVATE", "PARENT0001"
    )
    assert edge == gsrs.CompositionEdge(
        substance_unii="SALT000001",
        component_unii="PARENT0001",
        relation=gsrs.SALT_SOLVATE,
    )


def test_the_two_salt_encodings_normalise_to_one_identical_edge():
    """The mirror check, in miniature. On the real release these agree on 15,039
    edges; if the convention were inverted they would agree on essentially none."""
    a = gsrs.normalise_relationship("PARENT0001", "SALT/SOLVATE->PARENT", "SALT000001")
    b = gsrs.normalise_relationship("SALT000001", "PARENT->SALT/SOLVATE", "PARENT0001")
    assert a == b


def test_solvate_axis_puts_the_HYDRATE_on_the_substance_side():
    # "ANHYDROUS->SOLVATE" on X: X is the SOLVATE, Y is the ANHYDROUS form.
    # The hydrate is the composite; the anhydrous form is its component.
    edge = gsrs.normalise_relationship("HYDRATE001", "ANHYDROUS->SOLVATE", "ANHYDROUS1")
    assert edge == gsrs.CompositionEdge(
        substance_unii="HYDRATE001",
        component_unii="ANHYDROUS1",
        relation=gsrs.SOLVATE_ANHYDROUS,
    )
    mirror = gsrs.normalise_relationship(
        "ANHYDROUS1", "SOLVATE->ANHYDROUS", "HYDRATE001"
    )
    assert mirror == edge


def test_active_moiety_is_not_a_composition_edge():
    """ACTIVE MOIETY is the ION level and must never become an edge (spec 3.1).

    Using it as one asserts that levomefolate magnesium is interchangeable with
    magnesium sulfate -- 35 substances share MAGNESIUM CATION, 27 of them drugref
    moieties. It reaches the table only through is_active_component.
    """
    assert (
        gsrs.normalise_relationship("SALT000001", "ACTIVE MOIETY", "ION0000001") is None
    )


def test_unrelated_relationship_types_are_ignored():
    for rel_type in (
        "IMPURITY->PARENT",
        "METABOLITE->PARENT",
        "TARGET->INHIBITOR",
        "BASIS OF STRENGTH->SUBSTANCE",
        "RACEMATE->ENANTIOMER",
    ):
        assert gsrs.normalise_relationship("X000000001", rel_type, "Y000000001") is None


def test_self_edges_are_dropped():
    """A record relating to itself is not a composition; 23,944 ACTIVE MOIETY
    self-edges exist in the release and 12 salt self-edges. Without this filter
    every moiety becomes its own component."""
    assert (
        gsrs.normalise_relationship("SAME000001", "PARENT->SALT/SOLVATE", "SAME000001")
        is None
    )


def _write_dump(tmp_path, records):
    """Write records in the real dump's shape: gzip, JSON-lines, TWO TAB characters
    prefixing each line before the '{'."""
    path = tmp_path / "dump-public-test.gsrs"
    with gzip.open(path, "wt", encoding="utf-8") as fh:
        for rec in records:
            fh.write("\t\t" + json.dumps(rec) + "\n")
    return path


def test_iter_records_reads_the_two_tab_prefixed_json_lines(tmp_path):
    path = _write_dump(
        tmp_path,
        [
            {
                "approvalID": "SALT000001",
                "names": [{"name": "Test salt", "displayName": True}],
                "relationships": [
                    {
                        "type": "PARENT->SALT/SOLVATE",
                        "relatedSubstance": {"approvalID": "PARENT0001"},
                    },
                    {
                        "type": "ACTIVE MOIETY",
                        "relatedSubstance": {"approvalID": "PARENT0001"},
                    },
                ],
            }
        ],
    )
    records = list(gsrs.iter_records(path))
    assert len(records) == 1
    rec = records[0]
    assert rec.unii == "SALT000001"
    assert rec.display_name == "Test salt"
    assert rec.edges == (
        gsrs.CompositionEdge("SALT000001", "PARENT0001", gsrs.SALT_SOLVATE),
    )
    assert rec.active_moieties == frozenset({"PARENT0001"})


def test_a_record_with_no_unii_is_skipped(tmp_path):
    """5,078 of the release's 173,080 records carry no approvalID; they cannot join
    to anything and are dropped here rather than half-way down the writer."""
    path = _write_dump(tmp_path, [{"names": [], "relationships": []}])
    assert list(gsrs.iter_records(path)) == []


def test_a_self_active_moiety_does_not_count_as_a_ruling(tmp_path):
    """23,944 edges are a substance asserting it IS its own active moiety. That says
    nothing about WHICH COMPONENT is active, so active_moieties stays empty and the
    writer will record is_active_component = NULL (unruled), not false."""
    path = _write_dump(
        tmp_path,
        [
            {
                "approvalID": "SALT000001",
                "relationships": [
                    {
                        "type": "PARENT->SALT/SOLVATE",
                        "relatedSubstance": {"approvalID": "PARENT0001"},
                    },
                    {
                        "type": "ACTIVE MOIETY",
                        "relatedSubstance": {"approvalID": "SALT000001"},
                    },
                ],
            }
        ],
    )
    rec = next(iter(gsrs.iter_records(path)))
    assert rec.active_moieties == frozenset()


def test_a_multi_component_salt_keeps_every_component(tmp_path):
    """ZINC GLYCINATE CITRATE has three. 1,089 salts (7.7%) have more than one
    parent, so a single parent_moiety_uuid column would silently truncate them."""
    path = _write_dump(
        tmp_path,
        [
            {
                "approvalID": "TRIPLE0001",
                "relationships": [
                    {
                        "type": "PARENT->SALT/SOLVATE",
                        "relatedSubstance": {"approvalID": "COMP000001"},
                    },
                    {
                        "type": "PARENT->SALT/SOLVATE",
                        "relatedSubstance": {"approvalID": "COMP000002"},
                    },
                    {
                        "type": "PARENT->SALT/SOLVATE",
                        "relatedSubstance": {"approvalID": "COMP000003"},
                    },
                ],
            }
        ],
    )
    rec = next(iter(gsrs.iter_records(path)))
    assert {e.component_unii for e in rec.edges} == {
        "COMP000001",
        "COMP000002",
        "COMP000003",
    }


def test_a_malformed_line_does_not_abort_the_stream(tmp_path):
    """2.05 GB of upstream JSON: one bad line must not lose the other 173,079."""
    path = tmp_path / "dump-public-bad.gsrs"
    with gzip.open(path, "wt", encoding="utf-8") as fh:
        fh.write("\t\t{not json at all\n")
        fh.write("\t\t" + json.dumps({"approvalID": "GOOD000001"}) + "\n")
    assert [r.unii for r in gsrs.iter_records(path)] == ["GOOD000001"]
