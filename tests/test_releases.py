# tests/test_releases.py
"""Release manifests: build, publish, verify (spec 5.5, 7.2, 8). DB-gated."""
import datetime as dt

import pytest

from drugref import curation, keys, release_verification, releases, signatures, signing

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


def _publish_manually(conn, *, release_tag, published_by, key_fingerprint, private_key,
                      entries, row_count=None, manifest_digest=None,
                      published_at=PUBLISHED_AT, signed_at=PUBLISHED_AT):
    """Build, insert and sign a manifest BY HAND rather than through `releases.publish`
    -- shared by every test below that needs to plant a manifest whose stored
    `release_manifest`/`release_manifest_entry` rows disagree with each other, or with
    the live overlay, in some specific way `publish` (correct by construction) can
    never produce. `row_count`/`manifest_digest` default to the CORRECT, recomputed
    values so a caller only has to name the field it wants deliberately wrong.

    Returns `manifest_id`.
    """
    payload = releases.manifest_payload(
        conn, release_tag=release_tag, published_by=published_by,
        published_at=published_at, entries=entries, upstream=[],
        key_fingerprint=key_fingerprint, signed_at=signed_at)
    if row_count is None:
        row_count = len(entries)
    if manifest_digest is None:
        manifest_digest = signing.digest(payload)
    manifest_id = conn.execute(
        "INSERT INTO drugref.release_manifest (release_tag, manifest_digest, "
        "row_count, upstream_releases, published_by, published_at) "
        "VALUES (%s, %s, %s, '[]'::jsonb, %s, %s) RETURNING manifest_id",
        (release_tag, manifest_digest, row_count, published_by,
         published_at)).fetchone()[0]
    for entry in entries:
        conn.execute(
            "INSERT INTO drugref.release_manifest_entry (manifest_id, target_kind, "
            "natural_key, target_id, payload_context, payload_digest) "
            "VALUES (%s, %s, %s, %s, %s, %s)",
            (manifest_id, entry.target_kind, entry.natural_key, entry.target_id,
             entry.payload_context, entry.payload_digest))
    signatures.record(
        conn, target_kind="release_manifest", target_id=manifest_id,
        payload_context="release_manifest/v1", payload=payload,
        key_fingerprint=key_fingerprint,
        signature=signing.sign(private_key, payload), signed_at=signed_at)
    return manifest_id


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
    verdict = release_verification.verify_release(conn, published)
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
    verdict = release_verification.verify_release(conn, published)
    assert len(verdict.dropped) == 1
    assert not verdict.is_intact


def test_a_live_row_the_manifest_omits_is_an_ADDITION(conn, published, a_graded_rule):
    """A node carrying a curated judgement drugref never published. Today there is no
    way at all to tell that from drugref's own data."""
    curation.record_condition_ruling(
        conn, a_graded_rule["subject"], _a_condition_uuid(conn), "spurious",
        reviewed_by="somebody", reviewed_against="MED-RT 2026.07.06")
    verdict = release_verification.verify_release(conn, published)
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
    _publish_manually(
        conn, release_tag="2026.08.12", published_by="an operator",
        key_fingerprint=institutional_key["fingerprint"],
        private_key=institutional_key["private"], entries=entries)
    verdict = release_verification.verify_release(conn, "2026.08.12")
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
    verdict = release_verification.verify_release(conn, "2026.08.08")
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
    assert release_verification.verify_release(conn, "2026.08.07").is_intact


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
    verdict = release_verification.verify_release(conn, published)
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
    assert release_verification.verify_release(
        conn, published).signature == signing.KEY_REVOKED_COMPROMISED


def test_an_unknown_release_tag_raises(conn):
    with pytest.raises(release_verification.UnknownReleaseError):
        release_verification.verify_release(conn, "no such release")


def test_the_upstream_snapshot_is_recorded(conn, published):
    """`reviewed_against` says which release each JUDGEMENT was formed against; this
    says which releases the DATABASE held at publication. Different questions."""
    upstream = conn.execute(
        "SELECT upstream_releases FROM drugref.release_manifest "
        "WHERE release_tag = %s", (published,)).fetchone()[0]
    assert isinstance(upstream, list)


# ---- review round 2 additions -------------------------------------------------


def test_a_rebuilt_nodes_offset_ids_do_not_break_an_unaltered_verification(
        conn, institutional_key, a_graded_rule):
    """C1 (review round 2, Critical). `target_id` is UNSIGNED and "nothing verifies
    against it" (spec 5.5; db/030's own comment on `release_manifest_entry`) -- a node
    that reconstructed its curated overlay independently (a fresh `GENERATED ALWAYS AS
    IDENTITY` sequence, identical content) assigns different `target_id` values than
    the publishing node did, and a manifest entry naming the OLD id must still verify
    as unaltered against the NEW one, since only the natural key and content digest are
    signed -- `target_id` is not even part of the bytes.

    Proved wrong before this fix: the first draft's `verify_release` gated its
    dropped/added-vs-altered decision on `target_id` equality BEFORE ever comparing a
    digest, so this exact byte-identical-content, different-id case reported 100%
    churn (`dropped`/`added` both non-empty, `is_intact` false).

    Simulated by hand (`_publish_manually`) since a real rebuild is out of reach in a
    single-transaction test: the entry is inserted with `target_id` offset by 100000
    from the real live row's, while `natural_key`/`payload_context`/`payload_digest`
    are that row's REAL, correct values -- the manifest's SIGNED bytes never differ
    from an ordinary publish, because `target_id` is not in them.
    """
    target_id = curation.record_interaction_judgement(
        conn, a_graded_rule["subject"], a_graded_rule["class"], "CI_MoA", True,
        severity="major", evidence_grade="established", reviewed_by="a curator",
        reviewed_against="MED-RT 2026.07.06")
    natural_key = releases.natural_key_of(conn, "curated_interaction", target_id)
    _, real_payload = signatures.payload_for(
        conn, "curated_interaction", target_id, key_fingerprint="",
        signed_at=releases.ENTRY_DIGEST_SIGNED_AT)
    entries = [releases.ManifestEntry(
        "curated_interaction", natural_key, target_id + 100000,
        "curated_interaction/v1", signing.digest(real_payload))]
    _publish_manually(
        conn, release_tag="2026.08.20", published_by="an operator",
        key_fingerprint=institutional_key["fingerprint"],
        private_key=institutional_key["private"], entries=entries)
    verdict = release_verification.verify_release(conn, "2026.08.20")
    assert verdict.is_intact


def test_a_rebuilt_nodes_offset_ids_do_not_hide_a_real_alteration(
        conn, institutional_key, a_graded_rule):
    """C1's other half: an offset `target_id` must not let a GENUINELY wrong digest
    escape detection by being misread as "this natural key's row was superseded".
    Proved wrong before this fix: the same `target_id`-equality tie-break that broke
    the unaltered case above (in the opposite direction) silently EXONERATED this one
    -- `dropped`+`added` both fired and `altered` stayed empty, the same shape a
    legitimate correction produces, for a digest that was never true of any real row.

    The fix instead asks whether the manifest's claimed digest matches CONTENT
    anywhere in this natural key's real supersession history
    (`_published_content_is_history`); a fabricated digest matches nothing there,
    regardless of what `target_id` the entry happens to carry.
    """
    target_id = curation.record_interaction_judgement(
        conn, a_graded_rule["subject"], a_graded_rule["class"], "CI_MoA", True,
        severity="major", evidence_grade="established", reviewed_by="a curator",
        reviewed_against="MED-RT 2026.07.06")
    natural_key = releases.natural_key_of(conn, "curated_interaction", target_id)
    wrong = b"\xee" * 32
    entries = [releases.ManifestEntry(
        "curated_interaction", natural_key, target_id + 100000,
        "curated_interaction/v1", wrong)]
    _publish_manually(
        conn, release_tag="2026.08.21", published_by="an operator",
        key_fingerprint=institutional_key["fingerprint"],
        private_key=institutional_key["private"], entries=entries)
    verdict = release_verification.verify_release(conn, "2026.08.21")
    assert len(verdict.altered) == 1
    assert (verdict.dropped, verdict.added) == ([], [])


def test_verify_release_reconstructs_the_manifest_signatures_past_context(
        conn, institutional_key, monkeypatch):
    """C2/C3 (review round 2, Important). `_verify_manifest_signature` must rebuild
    each recorded signature under ITS OWN stored `payload_context`, never assume
    "release_manifest/v1" -- Task 7's C1 defect (verification must reconstruct the
    past, not the present), reintroduced here in hard-coded form: the first draft never
    selected `payload_context` at all.

    `release_manifest/v2` is registered into `signing.FIELD_LISTS` only for the
    duration of this test (`monkeypatch`, auto-reverted) -- there is no real `/v2`
    context today, but a signature genuinely recorded under one must still verify from
    its own stored `payload_context` regardless. Mirrors
    tests/test_signatures_writer.py's `test_verification_reconstructs_the_past_
    context_not_the_present`, one layer up; that test mutates the mutable
    `signature_target_kind` catalog instead, which this module has no equivalent of
    (`release_manifest`'s context is a caller-supplied parameter, not read from a
    catalog row) -- `monkeypatch` on `FIELD_LISTS` is the equivalent lever here.
    """
    monkeypatch.setitem(signing.FIELD_LISTS, "release_manifest/v2",
                        signing.RELEASE_MANIFEST_V1)
    payload = releases.manifest_payload(
        conn, release_tag="2026.08.22", published_by="an operator",
        published_at=PUBLISHED_AT, entries=[], upstream=[],
        key_fingerprint=institutional_key["fingerprint"], signed_at=PUBLISHED_AT,
        payload_context="release_manifest/v2")
    manifest_id = conn.execute(
        "INSERT INTO drugref.release_manifest (release_tag, manifest_digest, "
        "row_count, upstream_releases, published_by, published_at) "
        "VALUES ('2026.08.22', %s, 0, '[]'::jsonb, 'an operator', %s) "
        "RETURNING manifest_id", (signing.digest(payload), PUBLISHED_AT)).fetchone()[0]
    signatures.record(
        conn, target_kind="release_manifest", target_id=manifest_id,
        payload_context="release_manifest/v2", payload=payload,
        key_fingerprint=institutional_key["fingerprint"],
        signature=signing.sign(institutional_key["private"], payload),
        signed_at=PUBLISHED_AT)
    verdict = release_verification.verify_release(conn, "2026.08.22")
    assert verdict.signature == signing.VALID


def test_verify_release_raises_on_an_unsupported_algorithm(conn, published):
    """C2/C3 (review round 2, Important). `algorithm` must be checked -- Task 7's C2,
    mirrored here since `_verify_manifest_signature` cannot literally share
    `signatures.verify_target` (see that function's own docstring). The guard already
    existed in the first draft but had NO test exercising it -- removing it left all
    twelve original tests green.

    UNREACHABLE THROUGH THE CHECK CONSTRAINT TODAY: `assertion_signature_algorithm`
    admits exactly `'Ed25519'`. The CHECK is dropped inside this test's transaction
    (the `conn` fixture rolls back, so nothing survives) -- `test_signatures_writer.
    py`'s own technique, applied here, for reaching a value the schema does not
    otherwise admit. The inserted row's `payload_digest`/`signature` are arbitrary:
    the algorithm check fires before either is ever read.
    """
    conn.execute("ALTER TABLE drugref.assertion_signature "
                 "DROP CONSTRAINT assertion_signature_algorithm")
    manifest_id = conn.execute(
        "SELECT manifest_id FROM drugref.release_manifest WHERE release_tag = %s",
        (published,)).fetchone()[0]
    conn.execute(
        "INSERT INTO drugref.assertion_signature (target_kind, target_id, "
        "payload_context, payload_digest, key_fingerprint, algorithm, signature, "
        "signed_at) VALUES ('release_manifest', %s, 'release_manifest/v1', %s, %s, "
        "'RSA-4096', %s, %s)",
        (manifest_id, b"\x09" * 32, "f" * 64, b"\x09" * 64, PUBLISHED_AT))
    with pytest.raises(signatures.UnsupportedAlgorithmError):
        release_verification.verify_release(conn, published)


def test_an_unsigned_manifest_reports_no_signature(conn):
    """The `NO_SIGNATURE` path -- unreachable through `publish`, which always signs,
    but a real state nonetheless (a manifest row written and never signed, or not yet).
    Constructed by hand: no `assertion_signature` row at all."""
    conn.execute(
        "INSERT INTO drugref.release_manifest (release_tag, manifest_digest, "
        "row_count, upstream_releases, published_by, published_at) "
        "VALUES ('2026.08.23', %s, 0, '[]'::jsonb, 'an operator', %s)",
        (b"\x04" * 32, PUBLISHED_AT))
    verdict = release_verification.verify_release(conn, "2026.08.23")
    assert verdict.signature == signing.NO_SIGNATURE
    assert not verdict.is_intact


def test_a_wrong_row_count_is_reported(conn, institutional_key, a_graded_rule):
    """db/030's own comment on `release_manifest.row_count`: "The release verifier
    ... is what actually checks it, by recomputing the count from
    release_manifest_entry and comparing" -- false until this fix, the column having
    been written at publish time and never read back.

    CONSTRUCTED BY HAND: `release_manifest` is insert-only, so `row_count` cannot be
    corrupted after the fact -- the manifest is inserted with `row_count=5` while
    `release_manifest_entry` gets exactly the one real, correctly-signed entry, the
    shape a truncated INSERT into `release_manifest_entry` leaves behind."""
    target_id = curation.record_interaction_judgement(
        conn, a_graded_rule["subject"], a_graded_rule["class"], "CI_MoA", True,
        severity="major", evidence_grade="established", reviewed_by="a curator",
        reviewed_against="MED-RT 2026.07.06")
    natural_key = releases.natural_key_of(conn, "curated_interaction", target_id)
    _, real_payload = signatures.payload_for(
        conn, "curated_interaction", target_id, key_fingerprint="",
        signed_at=releases.ENTRY_DIGEST_SIGNED_AT)
    entries = [releases.ManifestEntry(
        "curated_interaction", natural_key, target_id, "curated_interaction/v1",
        signing.digest(real_payload))]
    _publish_manually(
        conn, release_tag="2026.08.24", published_by="an operator",
        key_fingerprint=institutional_key["fingerprint"],
        private_key=institutional_key["private"], entries=entries, row_count=5)
    verdict = release_verification.verify_release(conn, "2026.08.24")
    assert not verdict.row_count_ok
    assert not verdict.is_intact


def test_a_wrong_manifest_digest_is_reported(conn, institutional_key):
    """Same promise, `manifest_digest`'s half: "written at publish time and never
    compared" until this fix. CONSTRUCTED BY HAND: the stored `manifest_digest` is
    deliberately wrong at INSERT time (insert-only, so no later corruption is
    possible) while the SIGNATURE is genuinely valid over the real payload -- proving
    the two checks are independent, the same way
    test_a_row_whose_content_changed_is_an_ALTERATION proves `signature` and content
    integrity are independent."""
    _publish_manually(
        conn, release_tag="2026.08.25", published_by="an operator",
        key_fingerprint=institutional_key["fingerprint"],
        private_key=institutional_key["private"], entries=[],
        manifest_digest=b"\xff" * 32)
    verdict = release_verification.verify_release(conn, "2026.08.25")
    assert verdict.signature == signing.VALID
    assert not verdict.manifest_digest_ok
    assert not verdict.is_intact
