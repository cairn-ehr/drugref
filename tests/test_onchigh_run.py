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
    rather than buried in a temp-file fixture. Always a MOIETY subject
    (subject_medrt_code=None) -- see `_class_entry` below for the class-subject
    twin Task 10 adds."""
    return onchigh.OncEntry(
        entry_id=entry_id,
        candidate=onchigh.OncCandidate(
            subject_unii=subject_unii, subject_medrt_code=None,
            subject_name=subject_name,
            object_medrt_code=object_medrt_code, object_name=object_name,
            axis=axis, citation="test fixture only -- not a real citation"),
        judgement=onchigh.OncJudgement(
            applies=False, severity=None, evidence_grade=None,
            mechanism=None, management=None))


def _class_entry(entry_id="maoi-nsaid", subject_medrt_code="N0000175724",
                 subject_name="Monoamine Oxidase Inhibitors [MoA]",
                 object_medrt_code="N0000175722",
                 object_name="Nonsteroidal Anti-inflammatory Drug [EPC]",
                 axis="CI_MoA"):
    """The class-subject twin of `_entry` (Task 10, design spec section 14):
    subject_unii=None, subject_medrt_code set instead. Defaults resolve
    against `class_seeded` below (the subject) and `seeded.nsaid_class` (the
    object) -- a genuinely different MED-RT class from the subject, since a
    real ONC entry (SSRIs x MAOIs, ...) never pairs a class with itself
    except the one deliberate self-pair (QT-prolonging), which is Task 11's
    read-path concern, not this resolver's."""
    return onchigh.OncEntry(
        entry_id=entry_id,
        candidate=onchigh.OncCandidate(
            subject_unii=None, subject_medrt_code=subject_medrt_code,
            subject_name=subject_name,
            object_medrt_code=object_medrt_code, object_name=object_name,
            axis=axis, citation="test fixture only -- not a real citation"),
        judgement=onchigh.OncJudgement(
            applies=False, severity=None, evidence_grade=None,
            mechanism=None, management=None))


@pytest.fixture
def class_seeded(conn, seeded, ingest_run_id) -> uuid.UUID:
    """One extra MED-RT class beyond `seeded`'s own nsaid_class, standing in
    for a CLASS SUBJECT (design spec section 14): Monoamine Oxidase
    Inhibitors, resolved against `_class_entry`'s default subject_medrt_code
    above. A separate fixture from `seeded`, not folded into it, because most
    of this module's tests never exercise the class-subject grain and would
    otherwise carry a row they never ask about."""
    maoi_class = ids.mint_class_uuid("MED-RT", "N0000175724")
    conn.execute(
        "INSERT INTO drugref.substance_class "
        "(class_uuid, source, source_code, class_name, concept_type, first_seen_ingest) "
        "VALUES (%s, 'MED-RT', 'N0000175724', %s, 'MoA', %s) "
        "ON CONFLICT DO NOTHING",
        (maoi_class, "Monoamine Oxidase Inhibitors [MoA]", ingest_run_id))
    return maoi_class


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


# ---- Task 10: the class-subject resolver (design spec section 14) -----------


def test_resolves_a_class_subject_by_medrt_code(conn, seeded, class_seeded):
    """The class-subject twin of test_resolves_a_subject_by_unii_and_an_
    object_by_medrt_code: BOTH endpoints resolve as classes, through the same
    MED-RT lookup."""
    entry = _class_entry()
    resolved = onchigh_run.resolve_entry(conn, entry)
    assert isinstance(resolved, onchigh_run.ResolvedClassEndpoint)
    assert resolved.subject_class_uuid == class_seeded
    assert resolved.object_class_uuid == seeded.nsaid_class


def test_a_class_subject_name_mismatch_raises(conn, seeded, class_seeded):
    """The name<->identifier check applies to a class subject exactly as to a
    UNII subject (task-10 brief) -- same exception, same reasoning."""
    entry = _class_entry(subject_name="an entirely different class")
    with pytest.raises(onchigh_run.EndpointMismatchError, match="maoi-nsaid"):
        onchigh_run.resolve_entry(conn, entry)


def test_an_unresolvable_class_subject_is_returned_as_unresolved(conn, seeded):
    """Mirrors test_an_unknown_unii_is_returned_as_unresolved_not_raised, one
    grain over: a well-formed MED-RT code naming a class drugref does not
    hold is a coverage gap, not a bug -- and its identifier_scheme is
    OBJECT_SCHEME ('MED-RT'), the SAME spelling an unresolved object already
    uses, never a distinct 'MEDRT' (onchigh_run.OBJECT_SCHEME's own
    docstring)."""
    entry = _class_entry(subject_medrt_code="N9999999999")
    result = onchigh_run.resolve_entry(conn, entry)
    assert [u.endpoint_role for u in result] == ["subject"]
    assert result[0].identifier_scheme == "MED-RT"
    assert result[0].identifier_value == "N9999999999"


def test_class_subject_gets_no_salt_form_expansion(conn, seeded, class_seeded):
    """A class has no salt forms (design spec section 14.3) -- pinned
    explicitly, per the task-10 brief, rather than left implied by the
    dataclass shape. ResolvedClassEndpoint carries a single
    subject_class_uuid, never a subject_moiety_uuids tuple to expand."""
    entry = _class_entry()
    resolved = onchigh_run.resolve_entry(conn, entry)
    assert not hasattr(resolved, "subject_moiety_uuids")
    assert resolved.subject_class_uuid == class_seeded


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


# ---- Task 10: the class-subject write half -----------------------------------


@pytest.fixture
def CLASS_FIXTURE(tmp_path) -> pathlib.Path:
    """One ONC entry whose subject is a CLASS, not a moiety (design spec
    section 14) -- isolated in its own tmp_path file rather than added to
    the committed onc_fixture.toml, which test_cli_curate.py's entry-count
    assertions already pin to exactly two moiety-subject entries (task-10
    brief: 'update every assertion that counts entries' if the shared
    fixture grows a third). Resolves fully against `seeded` + `class_seeded`.
    """
    path = tmp_path / "onc_class_subject.toml"
    path.write_text(
        '[[entry]]\n'
        'entry_id = "maoi-nsaid"\n'
        '\n'
        '[entry.candidate]\n'
        'subject_medrt_code = "N0000175724"\n'
        'subject_name = "Monoamine Oxidase Inhibitors [MoA]"\n'
        'object_medrt_code = "N0000175722"\n'
        'object_name = "Nonsteroidal Anti-inflammatory Drug [EPC]"\n'
        'axis = "CI_MoA"\n'
        'citation = "test fixture only -- not a real citation"\n'
        '\n'
        '[entry.judgement]\n'
        'applies = false\n')
    return path


@pytest.fixture
def CLASS_FIXTURE_UNRESOLVED(tmp_path) -> pathlib.Path:
    """One ONC entry whose CLASS SUBJECT resolves to nothing -- the
    class-subject twin of FIXTURE_WITH_UNKNOWN above, so the same worklist
    proves it covers a class subject exactly as it covers a moiety subject
    or an object."""
    path = tmp_path / "onc_class_subject_unknown.toml"
    path.write_text(
        '[[entry]]\n'
        'entry_id = "unknown-class-subject"\n'
        '\n'
        '[entry.candidate]\n'
        'subject_medrt_code = "N9999999999"\n'
        'subject_name = "Not A Real Class"\n'
        'object_medrt_code = "N0000175722"\n'
        'object_name = "Nonsteroidal Anti-inflammatory Drug [EPC]"\n'
        'axis = "CI_MoA"\n'
        'citation = "test fixture only -- not a real citation"\n'
        '\n'
        '[entry.judgement]\n'
        'applies = false\n')
    return path


def _class_pair_count(conn, source: str) -> int:
    """How many class_pair_contraindication rows `source` currently owns --
    the class-subject twin of this module's own `_count`."""
    return conn.execute(
        "SELECT count(*) FROM drugref.class_pair_contraindication "
        "WHERE source = %s", (source,)).fetchone()[0]


def test_a_class_subject_entry_writes_a_class_pair_contraindication_row(
        conn, seeded, class_seeded, CLASS_FIXTURE):
    """The orchestrator-level twin of test_resolves_a_class_subject_by_medrt_
    code: a class-subject entry reaches the database as ONE
    class_pair_contraindication row, counted in OncSummary's own
    class_rules_written field (Task 10), separate from the moiety-grain
    rules_written/salt_forms_expanded fields."""
    summary = onchigh_run.ingest_onchigh(
        conn, path=CLASS_FIXTURE, upstream_release="test")
    assert summary.class_rules_written == 1
    assert summary.rules_written == 0
    assert summary.salt_forms_expanded == 0
    assert _class_pair_count(conn, "ONCHIGH") == 1


def test_a_class_pair_rebuild_replaces_only_this_sources_rows(
        conn, seeded, class_seeded, CLASS_FIXTURE):
    """Mirrors test_a_rebuild_replaces_only_this_sources_rows, one grain over:
    run TWICE, so a rebuild that merely appended (rather than
    clear-then-rewrite) would double the count on the second call."""
    onchigh_run.ingest_onchigh(conn, path=CLASS_FIXTURE, upstream_release="test-1")
    onchigh_run.ingest_onchigh(conn, path=CLASS_FIXTURE, upstream_release="test-2")
    assert _class_pair_count(conn, "ONCHIGH") == 1


def test_an_unresolvable_class_subject_becomes_a_question(
        conn, seeded, CLASS_FIXTURE_UNRESOLVED):
    """The class-subject twin of test_an_unresolved_endpoint_becomes_a_
    question: a class subject that fails to resolve lands on the SAME
    worklist (gap kind fifteen) a moiety subject or an object already does,
    with identifier_scheme = 'MED-RT' -- OBJECT_SCHEME's exact spelling, per
    the task-10 brief, never the hyphen-less 'MEDRT'."""
    summary = onchigh_run.ingest_onchigh(
        conn, path=CLASS_FIXTURE_UNRESOLVED, upstream_release="test")
    assert summary.endpoints_unresolved == 1
    row = conn.execute(
        "SELECT identifier_scheme FROM drugref.ingest_unresolved_onc_endpoint "
        "WHERE entry_id = 'unknown-class-subject' AND endpoint_role = 'subject'"
    ).fetchone()
    assert row == ("MED-RT",)
    assert conn.execute(
        "SELECT count(*) FROM drugref.open_question "
        "WHERE gap_kind = 'unresolved_onc_endpoint' AND is_current").fetchone()[0] == 1


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
