# tests/test_onchigh_run.py
"""The ONC orchestrator (slice 5c.2): both the resolution half (Task 4) --
turning a parsed OncEntry's stable identifiers into drugref UUIDs, and
expanding a resolved subject to its salt forms -- and the write half (Task 5)
-- `ingest_onchigh`, the rebuildable candidate-tier ingest that owns the
transaction. DB-gated -- every test here needs a real `identity_claim` /
`substance_class` / `substance_composition` row to resolve against, unlike
test_onchigh_parser.py's pure structural checks.

`ingest_onchigh` COMMITS (provenance.open_run's own early commit, then its
final commit), so it -- and everything `seeded` stages on the same connection
before it runs -- escapes the `conn` fixture's rollback-based isolation. The
`_clean` autouse fixture below is this module's own explicit cleanup, the
same trap and the same fix as test_gsrs_run.py's `_clean`.
"""
import pathlib
import uuid
from dataclasses import dataclass

import pytest

from drugref import ids, provenance
from drugref.ingest import onchigh, onchigh_run

# The real, committed, well-formed fixture (Task 3) -- both its entries use
# real, verified identifiers, but only "warfarin-nsaid" resolves fully against
# `seeded` below. "tranylcypromine-cox" is left deliberately unresolved by
# `seeded` (neither tranylcypromine's UNII nor MED-RT class N0000000160 is
# registered) -- a realistic, mixed-resolution file is the ordinary case for
# this ingest, not an edge case, so the rebuild/worklist tests below exercise
# it rather than a fixture engineered to resolve everything.
FIXTURE = pathlib.Path(__file__).parent / "fixtures" / "onc_fixture.toml"

# warfarin-nsaid resolves and expands to warfarin's two GATED-IN forms
# (`seeded`: warfarin itself, warfarin sodium) -- two class_contraindication
# rows. tranylcypromine-cox contributes none: both its endpoints are
# unresolved against `seeded`, so it lands in ingest_unresolved_onc_endpoint
# instead.
_expected_onchigh_rows = 2


@pytest.fixture(autouse=True)
def _clean(conn):
    """Truncate everything a committing ingest_onchigh call (or `seeded`,
    staged on the same connection and swept into that commit) could have left
    behind, so this module's tests cannot pollute -- or be polluted by --
    whichever other test module the shared, session-scoped database last ran.

    TRUNCATE, not DELETE: substance_moiety/identity_claim sit on slice 1's
    append-only floor, whose row-level triggers refuse DELETE outright
    (test_gsrs_run.py's `_clean` hit this first). CASCADE via ingest_run also
    reaches class_contraindication, class_membership,
    ingest_unresolved_onc_endpoint and open_question -- everything this
    module's committing tests can write -- so listing only these three tables
    is not under-cleaning: it is the same "let CASCADE do the rest" contract
    test_gsrs_run.py's own comment argues for, and the narrower alternative
    (scoping by gap_kind or by source alone) is the exact trap that comment
    describes hitting once already.
    """
    yield
    conn.execute("TRUNCATE drugref.identity_claim, drugref.substance_moiety, "
                 "drugref.ingest_run RESTART IDENTITY CASCADE")
    conn.commit()


def _count(conn, source: str) -> int:
    """How many class_contraindication rows `source` currently owns."""
    return conn.execute(
        "SELECT count(*) FROM drugref.class_contraindication WHERE source = %s",
        (source,)).fetchone()[0]


def _question_uuids(conn) -> set[uuid.UUID]:
    """Every currently-registered gap-fifteen question_uuid -- for the
    stable-across-reruns test, which must see the SAME set twice."""
    return {row[0] for row in conn.execute(
        "SELECT question_uuid FROM drugref.open_question "
        "WHERE gap_kind = 'unresolved_onc_endpoint'").fetchall()}


@dataclass
class Seeded:
    """The UUIDs test_onchigh_run.py's tests need to refer back to, once the
    `seeded` fixture below has written the rows they name."""
    warfarin: uuid.UUID
    warfarin_sodium: uuid.UUID
    ungated_warfarin_ester: uuid.UUID
    nsaid_class: uuid.UUID
    nsaid_partner: uuid.UUID


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

    # A SECOND NSAID, filed DIRECTLY under nsaid_class on has_EPC -- the
    # ddi_candidate_pair PARTNER test_a_resolved_rule_reaches_the_worklist
    # (Task 5) needs. Neither warfarin nor warfarin sodium can serve as their
    # own partner: ddi_candidate_pair excludes a rule's own subject from its
    # own partner set (`m.moiety_uuid <> ci.subject_moiety_uuid`), so a
    # genuinely different moiety has to sit in the class for the rule to
    # yield any pair at all, and gap_uncurated_interaction_rule only counts
    # rules that do (its own INNER JOIN to ddi_candidate_pair).
    nsaid_partner = _moiety("IBUPROFEN1", "ibuprofen")
    conn.execute(
        "INSERT INTO drugref.class_membership "
        "(moiety_uuid, class_uuid, relationship, ingest_run) "
        "VALUES (%s, %s, 'has_EPC', %s) ON CONFLICT DO NOTHING",
        (nsaid_partner, nsaid_class, ingest_run_id))

    return Seeded(warfarin=warfarin, warfarin_sodium=warfarin_sodium,
                  ungated_warfarin_ester=ungated_warfarin_ester,
                  nsaid_class=nsaid_class, nsaid_partner=nsaid_partner)


@pytest.fixture
def medrt_rows_present(conn, seeded, ingest_run_id):
    """One MED-RT class_contraindication row that must SURVIVE an
    ONCHIGH-scoped rebuild -- the invariant
    test_a_rebuild_replaces_only_this_sources_rows leans on hardest. Written
    directly, exactly as `seeded` writes its own rows, rather than through a
    real medrt_run ingest: this module is about the ONCHIGH write half, not
    MED-RT's own.
    """
    conn.execute(
        "INSERT INTO drugref.class_contraindication "
        "(subject_moiety_uuid, object_class_uuid, relationship, source, ingest_run) "
        "VALUES (%s, %s, 'CI_EPC', 'MED-RT', %s) ON CONFLICT DO NOTHING",
        (seeded.warfarin, seeded.nsaid_class, ingest_run_id))


@pytest.fixture
def FIXTURE_WITH_UNKNOWN(tmp_path) -> pathlib.Path:
    """One ONC entry whose OBJECT identifier resolves to nothing.

    Generated into tmp_path rather than committed under tests/fixtures/:
    onc_fixture.toml (Task 3) is hand-reviewed, real-identifier content and
    must never carry a deliberately broken row (task-5 brief).

    The SUBJECT resolves -- it is `seeded`'s own warfarin -- so exactly ONE
    endpoint fails, which is what
    test_an_unresolved_endpoint_becomes_a_question and
    test_the_question_uuid_is_stable_across_reruns both need: a single,
    unambiguous unresolved row to become one question.
    """
    path = tmp_path / "onc_with_unknown.toml"
    path.write_text(
        '[[entry]]\n'
        'entry_id = "warfarin-unknown-class"\n'
        '\n'
        '[entry.candidate]\n'
        'subject_unii = "5Q7ZVV76EI"\n'
        'subject_name = "warfarin"\n'
        'object_medrt_code = "N9999999999"\n'
        'object_name = "Not A Real Class"\n'
        'axis = "CI_EPC"\n'
        'citation = "test fixture only -- not a real citation"\n'
        '\n'
        '[entry.judgement]\n'
        'applies = false\n')
    return path


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


# ---- the write half (Task 5): ingest_onchigh ---------------------------------


def test_a_rebuild_replaces_only_this_sources_rows(conn, seeded, medrt_rows_present):
    """THE INVARIANT THIS SLICE LEANS ON HARDEST. A per-source rebuild that
    disturbed another source's rows would break the architecture invariant
    that makes multi-source candidates safe at all. Run TWICE, deliberately:
    a rebuild that merely APPENDED would double `_expected_onchigh_rows` on
    the second call and this would still pass a single-run version of itself.
    """
    before = _count(conn, "MED-RT")
    onchigh_run.ingest_onchigh(conn, path=FIXTURE, upstream_release="test-1")
    onchigh_run.ingest_onchigh(conn, path=FIXTURE, upstream_release="test-2")
    assert _count(conn, "MED-RT") == before
    assert _count(conn, "ONCHIGH") == _expected_onchigh_rows


def test_an_unresolved_endpoint_becomes_a_question(conn, seeded, FIXTURE_WITH_UNKNOWN):
    """Issue 71's lesson: a dropped row counted into a transient integer is a
    number nobody can act on. This one is a queryable, citable question."""
    summary = onchigh_run.ingest_onchigh(
        conn, path=FIXTURE_WITH_UNKNOWN, upstream_release="test")
    assert summary.endpoints_unresolved == 1
    assert conn.execute(
        "SELECT count(*) FROM drugref.open_question "
        "WHERE gap_kind = 'unresolved_onc_endpoint' AND is_current").fetchone()[0] == 1


def test_the_question_uuid_is_stable_across_reruns(conn, seeded, FIXTURE_WITH_UNKNOWN):
    """question_uuid is uuid5(gap_kind, gap_key) and external tooling cites it,
    so a second run must re-derive the SAME uuid, not mint a new one."""
    onchigh_run.ingest_onchigh(conn, path=FIXTURE_WITH_UNKNOWN, upstream_release="a")
    first = _question_uuids(conn)
    onchigh_run.ingest_onchigh(conn, path=FIXTURE_WITH_UNKNOWN, upstream_release="b")
    assert _question_uuids(conn) == first


def test_the_ingest_run_records_source_and_writer(conn, seeded):
    onchigh_run.ingest_onchigh(conn, path=FIXTURE, upstream_release="ONCHigh-2015")
    row = conn.execute(
        "SELECT source, writer, upstream_release, finished_at IS NOT NULL "
        "FROM drugref.ingest_run WHERE source = 'ONCHIGH'").fetchone()
    assert row == ("ONCHIGH", "onchigh_run", "ONCHigh-2015", True)


def test_a_resolved_rule_reaches_the_worklist(conn, seeded):
    """An ONC rule is ungraded on arrival, so it must appear on the SAME
    worklist MED-RT's rules use -- no new view. That the worklist works
    unchanged for a second authority is the evidence the candidate tier really
    was designed for one."""
    onchigh_run.ingest_onchigh(conn, path=FIXTURE, upstream_release="test")
    assert conn.execute(
        "SELECT count(*) FROM drugref.gap_uncurated_interaction_rule").fetchone()[0] > 0


def test_onchigh_is_a_declared_writer_and_source():
    """provenance.WRITERS and ingest_run's already-widened `writer` CHECK
    (db/031, schema only) are a PAIR (provenance.py's own docstring; db/020's
    source-trio lesson one table over) -- and this task is the first to
    actually CALL provenance.open_run(writer='onchigh_run'), so it is where
    that pairing gets completed. Mirrors test_gsrs_run.py's
    test_gsrs_is_a_declared_writer_and_source, added when GSRS's own
    orchestrator task closed the identical gap.
    """
    assert "onchigh_run" in provenance.WRITERS
