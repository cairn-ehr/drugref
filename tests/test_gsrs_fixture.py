# tests/test_gsrs_fixture.py
"""The direction convention, pinned on REAL RELEASE BYTES (slice-3 spec 3.2, 9.1).

tests/test_gsrs_parser.py proves the rule on synthetic input. This module proves
it is the rule the 2026-02-26 release actually needs, using the two checks that
distinguish the correct reading from the inverted one:

  * THE MIRROR CHECK -- GSRS stores most edges from both ends, and under the
    correct convention the two encodings normalise to the SAME edge. Under the
    inverted one they would produce two different, both-wrong edges.
  * THE FUNCTIONAL CHECK -- every solvate has exactly ONE anhydrous parent. Under
    the inverted reading the cardinality is many-to-many and meaningless.

DO NOT DELETE EITHER. Inverted, the convention produces a fully populated,
entirely wrong table that no aggregate count would flag.
"""
import collections
import pathlib

from drugref.ingest import gsrs

FIXTURE = pathlib.Path(__file__).parent / "fixtures" / "gsrs_subset.gsrs"


def _records():
    return list(gsrs.iter_records(FIXTURE))


def test_the_fixture_carries_every_role_the_slice_depends_on():
    uniis = {r.unii for r in _records()}
    for unii in ("H3472PJ7YA", "13S1S8SF37", "1D06KZ672I", "WCK1KIQ23Q",
                 "SK47B8698T", "ML30MJ2U7I", "DE08037SAB", "T6V3LHY838",
                 "88496G1ERL"):
        assert unii in uniis, f"{unii} missing -- regenerate with make_gsrs_subset.py"


def test_the_mirror_encodings_agree_on_real_bytes():
    """Both ends of one edge normalise to one identical CompositionEdge."""
    edges = collections.Counter()
    for record in _records():
        for edge in record.edges:
            edges[edge] += 1
    # The bisulfate/chlortetracycline edge is stored from both ends in the release.
    mirrored = gsrs.CompositionEdge("1D06KZ672I", "WCK1KIQ23Q", gsrs.SALT_SOLVATE)
    assert mirrored in edges, (
        "the salt->parent edge did not normalise as expected -- the direction "
        "convention is inverted, and every row of substance_composition is wrong")


def test_every_solvate_has_exactly_one_anhydrous_parent():
    """The functional check. On the full release: {1: 1635}, MAX = 1."""
    parents = collections.defaultdict(set)
    for record in _records():
        for edge in record.edges:
            if edge.relation == gsrs.SOLVATE_ANHYDROUS:
                parents[edge.substance_unii].add(edge.component_unii)
    assert parents, "the fixture carries no solvate edge -- regenerate it"
    assert max(len(v) for v in parents.values()) == 1


def test_the_heptahydrate_is_the_composite_not_the_component():
    """The concrete direction case: magnesium sulfate HEPTAHYDRATE is composed of
    the ANHYDROUS form, never the reverse."""
    edges = {e for r in _records() for e in r.edges
             if e.relation == gsrs.SOLVATE_ANHYDROUS}
    assert gsrs.CompositionEdge("SK47B8698T", "ML30MJ2U7I", gsrs.SOLVATE_ANHYDROUS) in edges
    assert gsrs.CompositionEdge("ML30MJ2U7I", "SK47B8698T", gsrs.SOLVATE_ANHYDROUS) not in edges


def test_zinc_glycinate_citrate_keeps_all_three_components():
    """The case a single parent_moiety_uuid column truncates silently."""
    record = next(r for r in _records() if r.unii == "H3472PJ7YA")
    components = {e.component_unii for e in record.edges
                  if e.relation == gsrs.SALT_SOLVATE}
    assert components == {"13S1S8SF37", "TE7660XO1C", "XF417D3PSL"}


def test_the_active_component_is_distinguished_from_the_counterions():
    """ZINC CATION is active; glycine and citric acid are not. This is what stops
    a rule on citric acid reaching every salt containing it."""
    record = next(r for r in _records() if r.unii == "H3472PJ7YA")
    assert "13S1S8SF37" in record.active_moieties
    assert "TE7660XO1C" not in record.active_moieties
    assert "XF417D3PSL" not in record.active_moieties


def test_phytate_sodium_is_a_composite_with_no_active_moiety_ruling():
    """The genuine case-6 gap: a real composite edge exists, but GSRS makes no
    ACTIVE MOIETY ruling for it. Non-vacuous -- this asserts the edge is
    present, not merely that the active set is empty, so the test cannot pass
    by accident on a record with no edges at all."""
    record = next(r for r in _records() if r.unii == "88496G1ERL")
    assert gsrs.CompositionEdge("88496G1ERL", "7IGF0S7R8I", gsrs.SALT_SOLVATE) in record.edges
    assert record.active_moieties == frozenset()


def test_nothing_points_at_drugrefs_magnesium_moiety():
    """Issue 33's own proposed fix, refuted on the bytes (spec 8).

    It predicted ML30MJ2U7I -> DE08037SAB and SK47B8698T -> DE08037SAB. Neither
    exists: DE08037SAB has ZERO inbound references across the whole release.
    """
    targets = {e.component_unii for r in _records() for e in r.edges}
    targets |= {a for r in _records() for a in r.active_moieties}
    assert "DE08037SAB" not in targets


def test_the_magnesium_family_shares_an_active_moiety_and_that_is_not_a_composition():
    """The merge this slice refuses. Magnesium sulfate, magnesium chloride and
    LEVOMEFOLATE MAGNESIUM all name MAGNESIUM CATION as their active moiety. If a
    future change turns ACTIVE MOIETY into a composition edge, this fails.
    """
    by_unii = {r.unii: r for r in _records()}
    for unii in ("DE08037SAB", "02F3473H9O", "1VZZ62R081"):
        assert "T6V3LHY838" in by_unii[unii].active_moieties
    # ...and none of them is composed of the others.
    edges = {e for r in _records() for e in r.edges}
    assert not any(e.substance_unii == "1VZZ62R081" and e.component_unii == "DE08037SAB"
                   for e in edges)
