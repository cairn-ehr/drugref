# tests/test_mesh_bridge.py
"""Unit tests for the two-key membership resolver (spec 6/C).

WHY THIS FILE EXISTS (#27, #33). The UNII-primary / CAS-fallback rule is pure
logic in mesh_run._resolve_moieties, but its only coverage used to be a DB-level
acceptance test that leaned on a hand-written UNII fixture giving magnesium
sulfate a CAS it does not actually have upstream. When #27 replaced that fixture
with real data the acceptance test correctly stopped joining -- and the fallback
rule was left with NO coverage at all.

Testing the resolver directly is also the better place for it: the rule is about
key PRECEDENCE, not about what any particular substance's identifiers are. The
identifiers below are deliberately synthetic so nothing here can ever be read as
a claim about upstream content -- that job belongs to the extracted fixtures.
"""
import uuid

from drugref.ingest import mesh
from drugref.ingest.mesh_run import _resolve_moieties

M_UNII = uuid.UUID("00000000-0000-5000-8000-000000000001")
M_CAS = uuid.UUID("00000000-0000-5000-8000-000000000002")
M_OTHER = uuid.UUID("00000000-0000-5000-8000-000000000003")

UNII_INDEX = {"AAAAAAAAAA": [M_UNII]}
CAS_INDEX = {"11-11-1": [M_CAS], "22-22-2": [M_CAS, M_OTHER]}


def _keys(unii=(), cas=()):
    return mesh.MemberKeys(unii=set(unii), cas=set(cas))


def test_a_unii_key_resolves_to_its_moiety():
    assert _resolve_moieties(_keys(unii=["AAAAAAAAAA"]), UNII_INDEX, CAS_INDEX) == [M_UNII]


def test_cas_is_used_when_the_member_carries_no_unii():
    # The fallback half of the two-key bridge: MeSH keys many chemical records by
    # CAS alone, and those must still reach a moiety.
    assert _resolve_moieties(_keys(cas=["11-11-1"]), UNII_INDEX, CAS_INDEX) == [M_CAS]


def test_cas_is_used_when_the_members_unii_is_not_in_the_registry():
    # 'else any CAS', not 'also any CAS': an unregistered UNII must not block the
    # fallback, or a substance drugref holds under CAS would be lost because MeSH
    # happened to also name a UNII drugref has not gated in.
    keys = _keys(unii=["ZZZZZZZZZZ"], cas=["11-11-1"])
    assert _resolve_moieties(keys, UNII_INDEX, CAS_INDEX) == [M_CAS]


def test_a_unii_match_wins_outright_over_any_cas():
    # UNII is drugref's own identity key, so a UNII hit is exact and the CAS is
    # not consulted at all -- 'also any CAS' would attach the membership to a
    # second, merely CAS-equivalent moiety.
    keys = _keys(unii=["AAAAAAAAAA"], cas=["11-11-1"])
    assert _resolve_moieties(keys, UNII_INDEX, CAS_INDEX) == [M_UNII]


def test_every_claimant_of_a_shared_cas_is_kept():
    # identity_claim is unique per (moiety, scheme, value) but NOT across
    # moieties, so one CAS may legitimately sit on two moieties. Keeping only the
    # first would silently drop a real membership.
    assert _resolve_moieties(_keys(cas=["22-22-2"]), UNII_INDEX, CAS_INDEX) == [M_CAS, M_OTHER]


def test_a_member_with_no_usable_key_resolves_to_nothing():
    assert _resolve_moieties(_keys(), UNII_INDEX, CAS_INDEX) == []
    assert _resolve_moieties(_keys(unii=["ZZZZZZZZZZ"], cas=["99-99-9"]),
                             UNII_INDEX, CAS_INDEX) == []
