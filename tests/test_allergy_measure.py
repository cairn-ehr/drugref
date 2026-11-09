"""Smoke test: tools.allergy_group_measure runs on the Task 3 world and prints its sections."""

import pytest

from tests import allergy_world as world
from tools import allergy_group_measure as tool

pytestmark = pytest.mark.usefixtures("conn")

MEDRT = """<?xml version="1.0"?>
<terminology>
<association><namespace>MED-RT</namespace><name>has_SC</name>
<from_namespace>RxNorm</from_namespace><from_name>x</from_name><from_code>111</from_code>
<to_namespace>MED-RT</to_namespace><to_name>Penicillins</to_name><to_code>N1</to_code></association>
<association><namespace>MED-RT</namespace><name>has_SC</name>
<from_namespace>RxNorm</from_namespace><from_name>y</from_name><from_code>222</from_code>
<to_namespace>MED-RT</to_namespace><to_name>Other</to_name><to_code>N2</to_code></association>
</terminology>
"""


def test_has_sc_penicillin_rxcuis_streams_only_the_penicillins_rows(tmp_path):
    p = tmp_path / "m.xml"
    p.write_text(MEDRT)
    assert tool.has_sc_penicillin_rxcuis(p) == [("111", "x")]


def test_tool_prints_its_sections(conn, tmp_path, capsys):
    conn.execute(
        "TRUNCATE drugref.allergy_group_member, drugref.allergy_group, "
        "drugref.allergy_mesh_placement, drugref.substance_composition, "
        "drugref.class_membership, drugref.class_parent, "
        "drugref.substance_class, drugref.identity_claim, "
        "drugref.substance_moiety, drugref.ingest_run RESTART IDENTITY CASCADE"
    )
    conn.commit()
    world.seed_moieties(conn)
    world.run_allergy_ingest(conn)
    p = tmp_path / "m.xml"
    p.write_text(MEDRT)
    assert tool.main(["--dsn", conn.info.dsn, "--medrt", str(p)]) == 0
    out = capsys.readouterr().out
    for header in ("== members", "== placed", "== -cillin moieties not in the group", "== MED-RT has_SC"):
        assert header in out
