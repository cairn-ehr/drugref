# tests/test_onchigh_read_path.py
"""Task 8 of slice 5c.2: checking the design spec's central claim rather than
assuming it. The spec says the READ PATH needs no change at all once ONCHIGH
exists as a second candidate source -- `curated_ddi_pair` already exposes
`candidate_source`, and `curated_interaction`'s natural key deliberately
omits `source`, so both authorities are supposed to arrive in one result set
for free. NO PRODUCTION CODE IS EXPECTED HERE: every test below is meant to
pass against code slices 5c.1/5c.2/5c.4 already shipped, and a failure is a
finding about the design, not a reason to edit the test.

DB-GATED, and it mixes the two escape hatches earlier ONC test modules each
carry alone. `ingested` (via `onchigh_run.ingest_onchigh` -> `provenance.
open_run`) COMMITS -- test_onchigh_run.py's own docstring explains why.
`curated` (via `cli_curate.curate_onchigh`) never commits -- test_cli_curate.
py's own docstring explains why. Both can be requested by the SAME test here,
so `_clean` below is copied from test_cli_curate.py's version (ROLLBACK
before TRUNCATE), not test_onchigh_run.py's plainer one: rolling back first
is what clears `curated_interaction`'s DEFERRED single-live trigger event
when a test never called `conn.commit()` itself (Postgres refuses to
TRUNCATE a table with a pending trigger event), and it costs nothing on a
test that did commit.

`seeded` and `medrt_rows_present` are copied from test_onchigh_run.py rather
than imported -- this repo's standing rule against cross-file fixture
imports (conftest.py's own comment on `a_graded_rule`/`ingest_run_id`: pytest
resolves fixtures BY NAME with no import at all, so copying is the
established way to share fixture shape across modules here).
"""
import datetime as dt
import pathlib
import uuid
from dataclasses import dataclass

import pytest

from drugref import cli_curate, curation, ids, keys, signatures, signing
from drugref.ingest import onchigh_run

# The real, committed, well-formed fixture (Task 3/7) -- see
# test_onchigh_run.py's own comment on this constant. Only "warfarin-nsaid"
# resolves fully against `seeded` below; "tranylcypromine-cox" does not.
FIXTURE = pathlib.Path(__file__).parent / "fixtures" / "onc_fixture.toml"


@pytest.fixture(autouse=True)
def _clean(conn):
    """Truncate everything a committing `ingest_onchigh` call (or `seeded`,
    staged on the same connection and swept into that commit) could have left
    behind. Copied from test_cli_curate.py's `_clean`, not test_onchigh_run.
    py's: this module's tests can ALSO run `curate_onchigh`, which never
    commits, so `curated_interaction`'s DEFERRED single-live trigger event
    can still be pending when this teardown runs -- ROLLING BACK FIRST clears
    it (a no-op for a test that committed, the fix for one that did not).

    TRUNCATE, not DELETE: substance_moiety/identity_claim sit on the
    append-only floor whose row-level triggers refuse DELETE outright.
    CASCADE reaches class_contraindication, curated_interaction,
    class_membership and every other table this module's tests can write,
    via their own foreign keys into substance_moiety/ingest_run.
    """
    yield
    conn.rollback()
    conn.execute("TRUNCATE drugref.identity_claim, drugref.substance_moiety, "
                 "drugref.ingest_run RESTART IDENTITY CASCADE")
    conn.commit()


@dataclass
class Seeded:
    """The UUIDs this module's tests need to refer back to, once `seeded`
    has written the rows they name."""
    warfarin: uuid.UUID
    warfarin_sodium: uuid.UUID
    nsaid_class: uuid.UUID
    nsaid_partner: uuid.UUID


@pytest.fixture
def seeded(conn, ingest_run_id) -> Seeded:
    """Everything this module's tests resolve against: warfarin and its
    gated-in salt warfarin sodium, one MED-RT EPC class, and a SECOND moiety
    (`nsaid_partner`) filed directly under that class -- copied from
    test_onchigh_run.py's own `seeded`, trimmed to what this module actually
    needs (no ungated-ester row: this module never exercises the salt-gate
    negative case).

    `nsaid_partner` IS LOAD-BEARING HERE, more than in test_onchigh_run.py:
    every test in THIS module reads `curated_ddi_pair`/`ddi_candidate_pair`,
    and `ddi_candidate_pair` excludes a rule's own subject from its own
    partner set (`m.moiety_uuid <> ci.subject_moiety_uuid`) -- without a
    second, genuinely different moiety in the class, a graded rule would
    still expand to ZERO pairs and every read-path assertion below would be
    vacuously true.
    """
    def _moiety(unii, name):
        moiety_uuid = ids.mint_moiety_uuid(unii)
        conn.execute(
            "INSERT INTO drugref.substance_moiety "
            "(moiety_uuid, display_name, first_seen_ingest) "
            "VALUES (%s, %s, %s) ON CONFLICT DO NOTHING",
            (moiety_uuid, name, ingest_run_id))
        conn.execute(
            "INSERT INTO drugref.identity_claim (moiety_uuid, scheme, value, "
            "ingest_run) VALUES (%s, 'UNII', %s, %s) ON CONFLICT DO NOTHING",
            (moiety_uuid, unii, ingest_run_id))
        return moiety_uuid

    warfarin = _moiety("5Q7ZVV76EI", "warfarin")
    warfarin_sodium_unii = "4V2UBU7H8W"
    warfarin_sodium = _moiety(warfarin_sodium_unii, "warfarin sodium")

    nsaid_class = ids.mint_class_uuid("MED-RT", "N0000175722")
    conn.execute(
        "INSERT INTO drugref.substance_class "
        "(class_uuid, source, source_code, class_name, concept_type, "
        "first_seen_ingest) VALUES (%s, 'MED-RT', 'N0000175722', %s, 'EPC', %s) "
        "ON CONFLICT DO NOTHING",
        (nsaid_class, "Nonsteroidal Anti-inflammatory Drug [EPC]", ingest_run_id))

    conn.execute(
        "INSERT INTO drugref.substance_composition "
        "(substance_unii, component_moiety, relation, is_active_component, "
        "ingest_run) VALUES (%s, %s, 'SALT_SOLVATE', true, %s) "
        "ON CONFLICT DO NOTHING",
        (warfarin_sodium_unii, warfarin, ingest_run_id))

    nsaid_partner = _moiety("IBUPROFEN1", "ibuprofen")
    conn.execute(
        "INSERT INTO drugref.class_membership "
        "(moiety_uuid, class_uuid, relationship, ingest_run) "
        "VALUES (%s, %s, 'has_EPC', %s) ON CONFLICT DO NOTHING",
        (nsaid_partner, nsaid_class, ingest_run_id))

    return Seeded(warfarin=warfarin, warfarin_sodium=warfarin_sodium,
                  nsaid_class=nsaid_class, nsaid_partner=nsaid_partner)


@pytest.fixture
def medrt_rows_present(conn, seeded, ingest_run_id):
    """One MED-RT `class_contraindication` row for the IDENTICAL rule ONC's
    warfarin-nsaid entry names -- copied from test_onchigh_run.py's own
    fixture of the same name and same reasoning, inserted directly rather
    than via a real `medrt_run` ingest (this module is about the read path,
    not MED-RT's own ingest)."""
    conn.execute(
        "INSERT INTO drugref.class_contraindication "
        "(subject_moiety_uuid, object_class_uuid, relationship, source, "
        "ingest_run) VALUES (%s, %s, 'CI_EPC', 'MED-RT', %s) "
        "ON CONFLICT DO NOTHING",
        (seeded.warfarin, seeded.nsaid_class, ingest_run_id))


@pytest.fixture
def medrt_curated(medrt_rows_present):
    """Alias for `medrt_rows_present`, named for its use alongside `curated`
    in `test_both_authorities_arrive_in_one_result_set`: MED-RT's own
    candidate row for the SAME rule ONC's warfarin-nsaid entry names, present
    beside the ONCHIGH judgement `curated` writes, so both authorities show
    up in `curated_ddi_pair` together. No body of its own -- the row is
    `medrt_rows_present`'s; this only gives it the name that test reads."""


@pytest.fixture
def ingested(conn, seeded):
    """The candidate tier already populated -- the realistic precondition for
    `curate onchigh`, copied from test_cli_curate.py's own `ingested`.
    COMMITS (`provenance.open_run`'s own early commit, then `ingest_onchigh`'s
    final commit) -- see this module's `_clean` docstring for why that
    matters."""
    onchigh_run.ingest_onchigh(conn, path=FIXTURE, upstream_release="ONCHigh-2015")


@pytest.fixture
def curated(conn, seeded, ingested):
    """drugref's own graded judgement over the candidate tier, written via
    the real `curate onchigh` command function -- the same operator order
    `ingested`'s own docstring describes. DOES NOT COMMIT: `curate_onchigh`'s
    own rule is that the caller owns the transaction, so a test using this
    fixture reads its writes through ordinary same-transaction visibility,
    exactly as test_cli_curate.py's own tests do."""
    return cli_curate.curate_onchigh(
        conn, path=FIXTURE, reviewed_by="Dr X", reviewed_against="ONCHigh-2015")


@pytest.fixture
def test_key():
    """A throwaway Ed25519 keypair, in memory only. Unlike test_cli_signing.
    py's `a_key_file`, this module drives `signatures.record`/`verify_target`
    directly rather than through the CLI's `--key <path>` flag, so there is
    no file on disk to build -- just the raw keypair `signing.generate_
    keypair` returns."""
    return signing.generate_keypair()


def _pairs(conn, source: str) -> int:
    """How many `ddi_candidate_pair` rows `source` currently contributes --
    the READ-TIME expansion `test_the_medrt_pair_count_is_undisturbed` checks
    survives an ONCHIGH-scoped rebuild untouched. Deliberately not
    test_onchigh_run.py's own `_count`, which counts `class_contraindication`
    rows (the WRITE side): this task is about the read path staying
    undisturbed, which is a claim about `ddi_candidate_pair`, one layer
    further downstream.
    """
    return conn.execute(
        "SELECT count(*) FROM drugref.ddi_candidate_pair WHERE source = %s",
        (source,)).fetchone()[0]


def test_a_graded_onc_rule_reaches_curated_ddi_pair(conn, seeded, ingested, curated):
    rows = conn.execute(
        "SELECT candidate_source, severity FROM drugref.curated_ddi_pair "
        "WHERE candidate_source = 'ONCHIGH'").fetchall()
    assert rows and all(r[0] == "ONCHIGH" for r in rows)


def test_an_ungraded_onc_rule_reaches_it_never(conn, seeded, ingested):
    """INNER JOIN by design: a NULL severity beside a real pair reads as
    'reviewed and harmless', which is the one rendering this schema must not
    permit."""
    assert conn.execute(
        "SELECT count(*) FROM drugref.curated_ddi_pair").fetchone()[0] == 0


def test_both_authorities_arrive_in_one_result_set(conn, seeded, ingested,
                                                   curated, medrt_curated):
    sources = {r[0] for r in conn.execute(
        "SELECT DISTINCT candidate_source FROM drugref.curated_ddi_pair")}
    assert sources == {"MED-RT", "ONCHIGH"}


def test_one_judgement_covers_a_rule_both_authorities_assert(conn, seeded):
    """curated_interaction's key OMITS source, so a rule MED-RT and ONC both
    assert takes ONE live judgement -- and the pair appears once per candidate
    source, carrying the same grade."""
    # MED-RT's own version of the SAME rule ONC's warfarin-nsaid entry names,
    # inserted directly (mirroring `medrt_rows_present`) -- this test takes no
    # fixture beyond `seeded`, so it builds both authorities' candidate rows
    # itself rather than pulling in `medrt_rows_present`/`ingested`.
    medrt_run_id = conn.execute(
        "INSERT INTO drugref.ingest_run "
        "(source, upstream_release, source_checksum, writer) "
        "VALUES ('MED-RT', 'test', 'test', 'medrt_run') "
        "RETURNING ingest_run_id").fetchone()[0]
    conn.execute(
        "INSERT INTO drugref.class_contraindication "
        "(subject_moiety_uuid, object_class_uuid, relationship, source, "
        "ingest_run) VALUES (%s, %s, 'CI_EPC', 'MED-RT', %s)",
        (seeded.warfarin, seeded.nsaid_class, medrt_run_id))

    # ONCHIGH's own candidate row for the identical rule, via the real
    # orchestrator -- COMMITS (provenance.open_run), the same trap this
    # module's `_clean` fixture exists for.
    onchigh_run.ingest_onchigh(conn, path=FIXTURE, upstream_release="test")

    # ONE judgement, written directly against warfarin's own natural key --
    # not `curate_onchigh` over the whole file, which would ALSO grade
    # warfarin_sodium's expanded salt form and defeat the "exactly one live
    # row" assertion below.
    curation.record_interaction_judgement(
        conn, seeded.warfarin, seeded.nsaid_class, "CI_EPC", True,
        severity="major", evidence_grade="established", reviewed_by="Dr X",
        reviewed_against="test")

    live_rows = conn.execute(
        "SELECT count(*) FROM drugref.curated_interaction "
        "WHERE superseded_by IS NULL").fetchone()[0]
    assert live_rows == 1

    rows = conn.execute(
        "SELECT candidate_source, severity FROM drugref.curated_ddi_pair "
        "ORDER BY candidate_source").fetchall()
    assert [r[0] for r in rows] == ["MED-RT", "ONCHIGH"]
    assert rows[0][1] == rows[1][1] == "major"


def test_the_medrt_pair_count_is_undisturbed(conn, seeded, medrt_rows_present):
    before = _pairs(conn, "MED-RT")
    onchigh_run.ingest_onchigh(conn, path=FIXTURE, upstream_release="test")
    assert _pairs(conn, "MED-RT") == before


def test_a_signed_onc_judgement_verifies_and_a_tampered_one_does_not(conn, seeded,
                                                                     curated, test_key):
    """5c.4's whole point, exercised on the first content that has provenance
    worth attesting. Follows test_cli_signing's non-committing pattern -- the
    test-isolation debt (issue 2's shape) is carried, not widened."""
    # NEVER COMMITS -- issue 2's shape, test_cli_signing.py's own debt: other
    # modules assert blanket, unfiltered counts over signing_key/
    # assertion_signature, so nothing this test writes to either table may
    # ever reach disk. `_clean`'s own conn.rollback() undoes it all at
    # teardown, exactly as test_cli_signing.py's `_NoCommit` undoes its
    # savepoint -- this module reaches the same result more simply, since it
    # never needs to drive the writes through `cli.main` in the first place.
    keys.register(conn, public_key=test_key.public_key, holder="a curator",
                  registered_by="an operator")

    live_id = conn.execute(
        "SELECT curated_interaction_id FROM drugref.curated_interaction "
        "WHERE subject_moiety_uuid = %s AND object_class_uuid = %s "
        "AND relationship = 'CI_EPC' AND superseded_by IS NULL",
        (seeded.warfarin, seeded.nsaid_class)).fetchone()[0]

    signed_at = dt.datetime(2026, 8, 11, tzinfo=dt.timezone.utc)
    fingerprint = signing.fingerprint(test_key.public_key)
    context, payload = signatures.payload_for(
        conn, "curated_interaction", live_id, key_fingerprint=fingerprint,
        signed_at=signed_at)
    signature = signing.sign(test_key.private_key, payload)
    signatures.record(
        conn, target_kind="curated_interaction", target_id=live_id,
        payload_context=context, payload=payload, key_fingerprint=fingerprint,
        signature=signature, signed_at=signed_at)

    verdicts = signatures.verify_target(conn, "curated_interaction", live_id)
    assert [v.verdict for v in verdicts] == [signing.VALID]

    # TAMPER BY SUPERSESSION: a changed graded field (severity), via a NEW row
    # on the same natural key -- append-only, so `live_id`'s own content never
    # moves (verifying IT ALONE would still report VALID forever). What
    # changes is which row is LIVE for warfarin's rule.
    curation.record_interaction_judgement(
        conn, seeded.warfarin, seeded.nsaid_class, "CI_EPC", True,
        severity="contraindicated", evidence_grade="established",
        reviewed_by="Dr X", reviewed_against="ONCHigh-2015")

    new_live_id = conn.execute(
        "SELECT curated_interaction_id FROM drugref.curated_interaction "
        "WHERE subject_moiety_uuid = %s AND object_class_uuid = %s "
        "AND relationship = 'CI_EPC' AND superseded_by IS NULL",
        (seeded.warfarin, seeded.nsaid_class)).fetchone()[0]
    assert new_live_id != live_id

    # The recorded signature named `live_id`, not `new_live_id` -- it never
    # travelled to the row that is actually live now, so asking "is what's
    # live signed?" finds nothing: the old signature no longer covers the
    # live payload in the most direct sense there is.
    assert signatures.verify_target(
        conn, "curated_interaction", new_live_id) == []

    # And the mathematics itself has moved, not merely the bookkeeping: the
    # OLD signature, checked against what the payload would be if (wrongly)
    # rebuilt over the NOW-LIVE row's content, does not verify.
    _, live_payload = signatures.payload_for(
        conn, "curated_interaction", new_live_id, key_fingerprint=fingerprint,
        signed_at=signed_at)
    assert not signing.verify(test_key.public_key, live_payload, signature)
