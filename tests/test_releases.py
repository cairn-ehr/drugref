# tests/test_releases.py
"""Release manifests: build, publish, verify (spec 5.5, 7.2, 8). DB-gated."""
import datetime as dt

import pytest

from drugref import curation, keys, releases, signatures, signing

PUBLISHED_AT = dt.datetime(2026, 8, 9, 6, 0, 0, tzinfo=dt.timezone.utc)


def _a_condition_uuid(conn):
    """A fresh, live MeSH condition -- for the ADDITION test's spurious ruling.

    Self-contained (mints its own ingest_run) rather than reusing the session
    `ingest_run_id`/`a_moiety` fixtures already in play in the calling test: those
    fixtures build a CANDIDATE (class_contraindication) for `a_graded_rule`'s
    interaction rule, and this helper needs an entirely separate FK target -- a
    condition -- for a `curated_condition` row that has nothing to do with that
    candidate. tests/test_curated_overlay.py's `_a_condition` is the same idea, but
    takes an `ingest_run_id` the caller must already have; this one is usable from
    anywhere a bare `conn` is in scope, the same shape as `_a_run_and_moiety` there.
    """
    from drugref import ids

    run = conn.execute(
        "INSERT INTO drugref.ingest_run (source, upstream_release, source_checksum, "
        "writer) VALUES ('MeSH', 'test', 'test', 'mesh_run') "
        "RETURNING ingest_run_id").fetchone()[0]
    condition_uuid = ids.mint_condition_uuid("MeSH", "D006333")
    conn.execute(
        "INSERT INTO drugref.condition (condition_uuid, source, source_code, name, "
        "record_kind, first_seen_ingest) VALUES (%s, 'MeSH', 'D006333', "
        "'Heart Failure', 'DESCRIPTOR', %s) ON CONFLICT DO NOTHING",
        (condition_uuid, run))
    return condition_uuid


@pytest.fixture
def institutional_key(conn):
    private, public = signing.generate_keypair()
    keys.register(conn, public_key=public, holder="drugref.org",
                  registered_by="an operator")
    return {"private": private, "public": public,
            "fingerprint": signing.fingerprint(public)}


@pytest.fixture
def published(conn, institutional_key, a_graded_rule):
    curation.record_interaction_judgement(
        conn, a_graded_rule["subject"], a_graded_rule["class"], "CI_MoA", True,
        severity="major", evidence_grade="established", reviewed_by="a curator",
        reviewed_against="MED-RT 2026.07.06")
    releases.publish(
        conn, release_tag="2026.08.09", published_by="an operator",
        private_key=institutional_key["private"],
        key_fingerprint=institutional_key["fingerprint"],
        published_at=PUBLISHED_AT, signed_at=PUBLISHED_AT)
    return "2026.08.09"


def test_a_published_release_verifies_intact(conn, published):
    verdict = releases.verify_release(conn, published)
    assert verdict.signature == signing.VALID
    assert verdict.is_intact
    assert (verdict.dropped, verdict.added, verdict.altered) == ([], [], [])


def test_the_manifest_enumerates_every_live_curated_row(conn, published):
    assert conn.execute(
        "SELECT row_count FROM drugref.release_manifest "
        "WHERE release_tag = %s", (published,)).fetchone()[0] == 1
    assert conn.execute(
        "SELECT count(*) FROM drugref.release_manifest_entry e "
        "JOIN drugref.release_manifest m USING (manifest_id) "
        "WHERE m.release_tag = %s", (published,)).fetchone()[0] == 1


def test_a_row_the_manifest_lists_but_the_database_lacks_is_a_DROP(conn, published):
    """The direction a transport signature cannot catch at all: a node that received a
    subset. Simulated by deleting the manifest ENTRY's target from the curated table --
    which the floor forbids, so the test TRUNCATEs, the only tool the floor leaves (see
    the PR #72 round's note: a committed row on the append-only floor cannot be
    unpicked with DELETE).

    `SET CONSTRAINTS ALL IMMEDIATE` first: `published`'s INSERT left the deferred
    single-live trigger with a pending event on this table (db/020's floor -- it is
    `DEFERRABLE INITIALLY DEFERRED` so a correction's brief two-live-rows moment is
    legal), and Postgres refuses to TRUNCATE a table that still has one pending,
    regardless of whether it would ultimately pass. The other tests in this file that
    need the deferred check resolved before their own next statement already do this;
    this one needs it for a different reason (TRUNCATE itself, not a correctness
    assertion) but the same call."""
    conn.execute("SET CONSTRAINTS ALL IMMEDIATE")
    conn.execute("TRUNCATE drugref.curated_interaction CASCADE")
    verdict = releases.verify_release(conn, published)
    assert len(verdict.dropped) == 1
    assert not verdict.is_intact


def test_a_live_row_the_manifest_omits_is_an_ADDITION(conn, published, a_graded_rule):
    """A node carrying a curated judgement drugref never published. Today there is no
    way at all to tell that from drugref's own data."""
    curation.record_condition_ruling(
        conn, a_graded_rule["subject"], _a_condition_uuid(conn), "spurious",
        reviewed_by="somebody", reviewed_against="MED-RT 2026.07.06")
    verdict = releases.verify_release(conn, published)
    assert len(verdict.added) == 1
    assert not verdict.is_intact


def test_a_row_whose_content_changed_is_an_ALTERATION(conn, institutional_key,
                                                      a_graded_rule):
    """The case an operator most wants to catch: a curated row that no longer matches
    what drugref published.

    CONSTRUCTED BY HAND rather than by editing a row, because the floor forbids editing
    either side: the curated table refuses UPDATE, and so does release_manifest_entry.
    So the test INSERTs a manifest whose entry digest for a live row is deliberately
    wrong -- which is byte-for-byte the state a consumer is in when their copy of the
    row was altered, and the only state verification can actually observe.

    The two assertions at the end are the point of the whole release layer: the manifest
    signature is VALID -- it really is drugref's -- while its content claim is FALSE.
    Authenticity and integrity are different questions, and a verifier that collapsed
    them would report a tampered database as a bad signature.
    """
    target_id = curation.record_interaction_judgement(
        conn, a_graded_rule["subject"], a_graded_rule["class"], "CI_MoA", True,
        severity="major", evidence_grade="established", reviewed_by="a curator",
        reviewed_against="MED-RT 2026.07.06")
    wrong = b"\xff" * 32
    natural_key = releases.natural_key_of(conn, "curated_interaction", target_id)
    entries = [releases.ManifestEntry("curated_interaction", natural_key, target_id,
                                      "curated_interaction/v1", wrong)]
    payload = releases.manifest_payload(
        conn, release_tag="2026.08.12", published_by="an operator",
        published_at=PUBLISHED_AT, entries=entries, upstream=[],
        key_fingerprint=institutional_key["fingerprint"], signed_at=PUBLISHED_AT)
    manifest_id = conn.execute(
        "INSERT INTO drugref.release_manifest (release_tag, manifest_digest, "
        "row_count, upstream_releases, published_by, published_at) "
        "VALUES ('2026.08.12', %s, 1, '[]'::jsonb, 'an operator', %s) "
        "RETURNING manifest_id", (signing.digest(payload), PUBLISHED_AT)).fetchone()[0]
    conn.execute(
        "INSERT INTO drugref.release_manifest_entry (manifest_id, target_kind, "
        "natural_key, target_id, payload_context, payload_digest) "
        "VALUES (%s, 'curated_interaction', %s, %s, 'curated_interaction/v1', %s)",
        (manifest_id, natural_key, target_id, wrong))
    signatures.record(
        conn, target_kind="release_manifest", target_id=manifest_id,
        payload_context="release_manifest/v1", payload=payload,
        key_fingerprint=institutional_key["fingerprint"],
        signature=signing.sign(institutional_key["private"], payload),
        signed_at=PUBLISHED_AT)
    verdict = releases.verify_release(conn, "2026.08.12")
    assert verdict.signature == signing.VALID
    assert len(verdict.altered) == 1
    assert (verdict.dropped, verdict.added) == ([], [])
    assert not verdict.is_intact


def test_an_empty_manifest_does_not_verify_a_database_that_has_rows(
        conn, institutional_key, a_graded_rule):
    """THE VACUOUS-PASS TEST. A manifest over zero rows is a MEANINGFUL statement --
    'drugref published nothing' -- not a wildcard, and verifying a database that does
    hold curated rows against it must FAIL with an `added` finding.

    This is exactly the shape this project keeps finding: an empty result that is
    over-determined, and a check that passes because there was nothing to check.
    """
    releases.publish(
        conn, release_tag="2026.08.08", published_by="an operator",
        private_key=institutional_key["private"],
        key_fingerprint=institutional_key["fingerprint"],
        published_at=PUBLISHED_AT, signed_at=PUBLISHED_AT)
    curation.record_interaction_judgement(
        conn, a_graded_rule["subject"], a_graded_rule["class"], "CI_MoA", True,
        severity="major", evidence_grade="established", reviewed_by="a curator",
        reviewed_against="MED-RT 2026.07.06")
    verdict = releases.verify_release(conn, "2026.08.08")
    assert len(verdict.added) == 1
    assert not verdict.is_intact


def test_an_empty_manifest_verifies_an_empty_overlay(conn, institutional_key):
    """The control for the test above. Without it, a verifier that ALWAYS reported an
    addition would pass that test and be useless."""
    releases.publish(
        conn, release_tag="2026.08.07", published_by="an operator",
        private_key=institutional_key["private"],
        key_fingerprint=institutional_key["fingerprint"],
        published_at=PUBLISHED_AT, signed_at=PUBLISHED_AT)
    assert releases.verify_release(conn, "2026.08.07").is_intact


def test_a_correction_is_an_addition_not_an_alteration(conn, published, a_graded_rule):
    """A superseded row leaves the live set and its successor joins it, so a curated
    correction made after publication shows as ONE drop and ONE addition -- which is
    the truth. Reading it as an 'alteration' would imply the published row had been
    edited, and on an append-only floor nothing ever is."""
    curation.record_interaction_judgement(
        conn, a_graded_rule["subject"], a_graded_rule["class"], "CI_MoA", True,
        severity="contraindicated", evidence_grade="established",
        reviewed_by="a curator", reviewed_against="MED-RT 2026.07.06")
    conn.execute("SET CONSTRAINTS ALL IMMEDIATE")
    verdict = releases.verify_release(conn, published)
    assert len(verdict.dropped) == 1 and len(verdict.added) == 1
    assert verdict.altered == []


def test_the_manifest_signature_is_an_ordinary_assertion_signature_row(conn, published):
    """ONE MECHANISM, BOTH LAYERS -- the payoff of detaching the signature from the row.
    A manifest is signed by exactly the same table and the same code path as a curated
    judgement."""
    assert conn.execute(
        "SELECT count(*) FROM drugref.assertion_signature "
        "WHERE target_kind = 'release_manifest'").fetchone()[0] == 1


def test_a_compromised_publishing_key_flags_the_release(conn, published,
                                                        institutional_key):
    keys.revoke(conn, key_fingerprint=institutional_key["fingerprint"],
                status="compromised", revoked_by="an operator",
                status_from=dt.datetime(2027, 1, 1, tzinfo=dt.timezone.utc))
    conn.execute("SET CONSTRAINTS ALL IMMEDIATE")
    assert releases.verify_release(
        conn, published).signature == signing.KEY_REVOKED_COMPROMISED


def test_an_unknown_release_tag_raises(conn):
    with pytest.raises(releases.UnknownReleaseError):
        releases.verify_release(conn, "no such release")


def test_the_upstream_snapshot_is_recorded(conn, published):
    """`reviewed_against` says which release each JUDGEMENT was formed against; this
    says which releases the DATABASE held at publication. Different questions."""
    upstream = conn.execute(
        "SELECT upstream_releases FROM drugref.release_manifest "
        "WHERE release_tag = %s", (published,)).fetchone()[0]
    assert isinstance(upstream, list)
