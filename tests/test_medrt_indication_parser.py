# tests/test_medrt_indication_parser.py
"""The four indication predicates (slice 5b.2).

Mirrors test_medrt_mesh_ci_parser.py, because the parsing problem is identical: the
object is a MeSH ConceptUI this module must NOT resolve, and the endpoint pair is the
only scoping that keeps unlicensed namespaces out.
"""
import pathlib

import pytest

from drugref.ingest import medrt

FIX = pathlib.Path(__file__).parent / "fixtures" / "medrt_subset.xml"


@pytest.fixture(scope="module")
def parsed():
    return medrt.parse(FIX)


def write_medrt(tmp_path, associations: str) -> pathlib.Path:
    """One MED-RT file holding a single MoA class plus the given associations."""
    path = tmp_path / "medrt.xml"
    path.write_text(
        '<?xml version="1.0"?>\n<terminology>\n'
        "<concept><namespace>MED-RT</namespace><code>C-MOA</code>"
        "<name>Real Mechanism [MoA]</name><status>A</status>"
        "<property><name>CTY</name><value>MoA</value></property>"
        "<property><name>NUI</name><value>N-MOA-9</value></property></concept>\n"
        + associations + "\n</terminology>\n", encoding="utf-8")
    return path


def assoc(name: str, fns: str, fc: str, tns: str, tc: str) -> str:
    return (f"<association><name>{name}</name>"
            f"<from_namespace>{fns}</from_namespace><from_code>{fc}</from_code>"
            f"<to_namespace>{tns}</to_namespace><to_code>{tc}</to_code></association>")


@pytest.mark.parametrize("predicate",
                         ["may_treat", "may_prevent", "may_diagnose", "induces"])
def test_each_predicate_is_parsed_with_its_raw_mesh_code(tmp_path, predicate):
    path = write_medrt(tmp_path, assoc(predicate, "RxNorm", "161", "MeSH", "M0001"))
    result = medrt.parse(path)
    assert len(result.mesh_indications) == 1
    got = result.mesh_indications[0]
    assert (got.rxcui, got.mesh_code, got.relationship) == ("161", "M0001", predicate)


def test_a_class_subject_is_refused_and_counted(tmp_path):
    """193 assertions in the real release run MED-RT -> MeSH: the subject is a
    pharmacologic CLASS, not an ingredient, so there is no RxCUI to bridge. Refused
    and COUNTED -- the posture non_mesh_ci_objects takes -- never dropped."""
    path = write_medrt(tmp_path, assoc("may_treat", "MED-RT", "C-MOA", "MeSH", "M0001"))
    result = medrt.parse(path)
    assert result.mesh_indications == []
    assert result.class_subject_indications == 1


def test_an_object_outside_mesh_is_refused_and_counted(tmp_path):
    """The counter is named for the shape the release contains, but it increments for
    ANY endpoint pair other than RxNorm -> MeSH -- which is what keeps SNOMED out."""
    path = write_medrt(tmp_path,
                       assoc("may_treat", "RxNorm", "161", "SNOMED CT", "12345"))
    result = medrt.parse(path)
    assert result.mesh_indications == []
    assert result.class_subject_indications == 1


def test_indications_do_not_leak_into_the_other_lists(tmp_path):
    """Slice 5a's class_contraindication rows are load-bearing for ddi_candidate_pair,
    and 5b's mesh_contraindications for the CI relations. Neither may gain a row here."""
    path = write_medrt(tmp_path,
                       assoc("may_treat", "RxNorm", "161", "MeSH", "M0001")
                       + assoc("CI_with", "RxNorm", "161", "MeSH", "M0002"))
    result = medrt.parse(path)
    assert [a.relationship for a in result.mesh_indications] == ["may_treat"]
    assert [a.relationship for a in result.mesh_contraindications] == ["CI_with"]
    assert result.contraindications == []
    assert result.memberships == []


def test_indication_predicates_left_the_skipped_list(parsed):
    """skipped_predicates is the release-to-release change detector: a predicate
    drugref now INGESTS must leave it, or an upstream rename stops being visible."""
    for predicate in ("may_treat", "may_prevent", "may_diagnose", "induces"):
        assert predicate not in parsed.skipped_predicates


def test_the_committed_fixture_exercises_indications(parsed):
    """Asserted against the fixture so it cannot pass by the fixture quietly losing
    the assertions it exists to exercise (test_has_sc_into_mesh_is_dropped's idiom)."""
    assert parsed.mesh_indications, "fixture carries no indication assertions"
