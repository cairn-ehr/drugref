# tests/test_onchigh_run.py
"""The resolution half of the ONC orchestrator (slice 5c.2, Task 4): turning a
parsed OncEntry's stable identifiers into drugref UUIDs, and expanding a
resolved subject to its salt forms. DB-gated -- every test here needs a real
`identity_claim` / `substance_class` / `substance_composition` row to resolve
against, unlike test_onchigh_parser.py's pure structural checks.

The WRITE half (rebuilding class_contraindication rows, opening an
ingest_run) is a later task and is deliberately not exercised here.
"""
import uuid
from dataclasses import dataclass

import pytest

from drugref import ids
from drugref.ingest import onchigh, onchigh_run


@dataclass
class Seeded:
    """The UUIDs test_onchigh_run.py's tests need to refer back to, once the
    `seeded` fixture below has written the rows they name."""
    warfarin: uuid.UUID
    warfarin_sodium: uuid.UUID
    ungated_warfarin_ester: uuid.UUID
    nsaid_class: uuid.UUID


def _entry(entry_id="warfarin-nsaid", subject_unii="5Q7ZVV76EI",
          subject_name="warfarin", object_medrt_code="N0000175722",
          object_name="Nonsteroidal Anti-inflammatory Drug [EPC]",
          axis="CI_EPC"):
    """Build one OncEntry in memory -- no TOML, no file. `onchigh.parse` (Task
    3) already owns turning a file into these dataclasses; every test here
    only needs the dataclasses themselves, so building them directly keeps
    each test's intent (which identifier is under test) in the test body
    rather than buried in a temp-file fixture."""
    return onchigh.OncEntry(
        entry_id=entry_id,
        candidate=onchigh.OncCandidate(
            subject_unii=subject_unii, subject_name=subject_name,
            object_medrt_code=object_medrt_code, object_name=object_name,
            axis=axis, citation="test fixture only -- not a real citation"),
        judgement=onchigh.OncJudgement(
            applies=False, severity=None, evidence_grade=None,
            mechanism=None, management=None))


@pytest.fixture
def seeded(conn, ingest_run_id) -> Seeded:
    """Everything the tests below resolve against, inserted directly (never
    via a real ingest, per the task brief): warfarin and its gated-in salt
    warfarin sodium, one MED-RT EPC class, and the composition edges that
    make salt-form expansion exercisable -- including one edge to a UNII that
    was never gated in as its own moiety, so the negative case
    (test_salt_expansion_admits_only_gated_in_moieties) has something real to
    assert against.

    UNIIs are the real, verified ones the design spec names (5c.2 spec §6):
    warfarin 5Q7ZVV76EI, warfarin sodium 4V2UBU7H8W (one of the three measured
    salt forms). The ester UNII is a made-up placeholder -- it must NOT
    resolve to a moiety, which is the whole point of that row.
    """
    def _moiety(unii, name):
        moiety_uuid = ids.mint_moiety_uuid(unii)
        conn.execute(
            "INSERT INTO drugref.substance_moiety "
            "(moiety_uuid, display_name, first_seen_ingest) "
            "VALUES (%s, %s, %s) ON CONFLICT DO NOTHING",
            (moiety_uuid, name, ingest_run_id))
        conn.execute(
            "INSERT INTO drugref.identity_claim (moiety_uuid, scheme, value, ingest_run) "
            "VALUES (%s, 'UNII', %s, %s) ON CONFLICT DO NOTHING",
            (moiety_uuid, unii, ingest_run_id))
        return moiety_uuid

    warfarin = _moiety("5Q7ZVV76EI", "warfarin")
    warfarin_sodium_unii = "4V2UBU7H8W"
    warfarin_sodium = _moiety(warfarin_sodium_unii, "warfarin sodium")

    # NEVER gated in: no substance_moiety row, no identity_claim row -- only a
    # composition edge, exactly the shape a refused moiety leaves behind.
    ester_unii = "TESTESTER1"
    ungated_warfarin_ester = ids.mint_moiety_uuid(ester_unii)

    nsaid_class = ids.mint_class_uuid("MED-RT", "N0000175722")
    conn.execute(
        "INSERT INTO drugref.substance_class "
        "(class_uuid, source, source_code, class_name, concept_type, first_seen_ingest) "
        "VALUES (%s, 'MED-RT', 'N0000175722', %s, 'EPC', %s) "
        "ON CONFLICT DO NOTHING",
        (nsaid_class, "Nonsteroidal Anti-inflammatory Drug [EPC]", ingest_run_id))

    for substance_unii in (warfarin_sodium_unii, ester_unii):
        conn.execute(
            "INSERT INTO drugref.substance_composition "
            "(substance_unii, component_moiety, relation, is_active_component, "
            "ingest_run) VALUES (%s, %s, 'SALT_SOLVATE', true, %s) "
            "ON CONFLICT DO NOTHING",
            (substance_unii, warfarin, ingest_run_id))

    return Seeded(warfarin=warfarin, warfarin_sodium=warfarin_sodium,
                  ungated_warfarin_ester=ungated_warfarin_ester,
                  nsaid_class=nsaid_class)


def test_resolves_a_subject_by_unii_and_an_object_by_medrt_code(conn, seeded):
    entry = _entry(subject_unii="5Q7ZVV76EI", object_medrt_code="N0000175722")
    resolved = onchigh_run.resolve_entry(conn, entry)
    assert resolved.object_class_uuid == seeded.nsaid_class
    assert seeded.warfarin in resolved.subject_moiety_uuids


def test_a_name_disagreeing_with_its_identifier_raises(conn, seeded):
    """The name field is a REVIEW AID: a human reads it in the diff while the
    database reads the identifier. Let them disagree and the reviewer is
    approving a different substance from the one that lands."""
    entry = _entry(subject_unii="5Q7ZVV76EI", subject_name="aspirin")
    with pytest.raises(onchigh_run.EndpointMismatchError, match="warfarin"):
        onchigh_run.resolve_entry(conn, entry)


def test_an_unknown_unii_is_returned_as_unresolved_not_raised(conn, seeded):
    """A well-formed identifier naming a substance drugref does not hold is a
    COVERAGE GAP, not a bug in the file -- so it becomes data (gap kind
    fifteen), not an exception."""
    entry = _entry(subject_unii="ZZZZZZZZZZ", subject_name="notadrug")
    result = onchigh_run.resolve_entry(conn, entry)
    assert [u.endpoint_role for u in result] == ["subject"]
    assert result[0].identifier_value == "ZZZZZZZZZZ"


def test_both_endpoints_unresolved_yields_two_records(conn, seeded):
    entry = _entry(subject_unii="ZZZZZZZZZZ", object_medrt_code="N0000000000")
    assert len(onchigh_run.resolve_entry(conn, entry)) == 2


def test_subject_expands_to_its_salt_forms(conn, seeded):
    """A judgement on warfarin must reach a consumer holding warfarin sodium --
    a real product. MED-RT itself asserts per-form (it carries rules for both
    tranylcypromine and tranylcypromine sulfate)."""
    forms = onchigh_run.subject_forms(conn, seeded.warfarin)
    assert seeded.warfarin in forms
    assert seeded.warfarin_sodium in forms


def test_salt_expansion_admits_only_gated_in_moieties(conn, seeded):
    """A composition edge to a substance the moiety gate refused is not a
    subject drugref can write a rule about: class_contraindication's FK would
    reject it, and reaching the FK is the wrong place to find out."""
    forms = onchigh_run.subject_forms(conn, seeded.warfarin)
    assert seeded.ungated_warfarin_ester not in forms
