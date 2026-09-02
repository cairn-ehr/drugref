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
    _kp = signing.generate_keypair()
    private, public = _kp.private_key, _kp.public_key
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
    assert (verdict.dropped, verdict.added, verdict.altered) == ((), (), ())


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
    # The complement, as its ADDITION and ALTERATION siblings already assert: a verifier
    # that reported every finding in every list would satisfy the line above alone.
    assert (verdict.added, verdict.altered) == ((), ())
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
    assert (verdict.dropped, verdict.added) == ((), ())
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
    assert verdict.altered == ()


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
    says which releases the DATABASE held at publication. Different questions.

    ASSERTS THE SHAPE, NOT MERELY THE TYPE (review I3). This read
    `assert isinstance(upstream, list)`, which is TRUE FOR `[]` -- and `[]` is what it
    always got, because `drugref.loaded_release` filters `finished_at IS NOT NULL` and
    conftest's `ingest_run_id` fixture never sets it. The writer/reader key contract was
    therefore exercised only by committed rows another test file happened to leave
    behind, so renaming a jsonb key passed under `pytest tests/test_releases.py` and
    failed only under some collection orders. `test_the_upstream_snapshot_round_trips`
    below is the real gate; this one keeps the empty case honest."""
    upstream = conn.execute(
        "SELECT upstream_releases FROM drugref.release_manifest "
        "WHERE release_tag = %s", (published,)).fetchone()[0]
    assert isinstance(upstream, list)


def test_the_upstream_snapshot_round_trips(conn, institutional_key, a_graded_rule):
    """REVIEW I3: THE WRITER AND THE READER MUST AGREE ON THE JSONB KEYS.

    `releases.publish` writes `{source, writer, release}` objects;
    `_verify_manifest_signature` reads exactly those three keys back, and the members
    are SIGNED (they form the `--upstream--` group). Renaming a key on either side
    produced `KeyError` at verify time -- not a `RuntimeError`, so `cli.main` printed a
    traceback -- and nothing in this file noticed, because every manifest here was
    published against an empty `loaded_release`.

    A FINISHED ingest_run is what makes the view return anything at all: it filters
    `finished_at IS NOT NULL`, which the shared fixture deliberately leaves unset. This
    test sets it explicitly rather than depending on whatever another module committed.
    """
    conn.execute(
        "INSERT INTO drugref.ingest_run (source, upstream_release, source_checksum, "
        # BOTH STAMPS NAMED, rather than letting started_at take its default. See
        # db/053's CHECK and the note in tests/test_ingest_observability.py's _run:
        # now() is the transaction's start and would land BEFORE a clock_timestamp()
        # default. Naming only finished_at fixed that, but left the row depending on
        # Postgres evaluating the column default before the target-list expression in
        # the same INSERT -- true today, unspecified, and the constraint would fail the
        # test loudly the day it stopped being true.
        "writer, started_at, finished_at) "
        "VALUES ('UNII', '2026.08.01', 'abc123', 'unii_run', clock_timestamp(), "
        "        clock_timestamp() + interval '1 second')")
    curation.record_interaction_judgement(
        conn, a_graded_rule["subject"], a_graded_rule["class"], "CI_MoA", True,
        severity="major", evidence_grade="established", reviewed_by="a curator",
        reviewed_against="MED-RT 2026.07.06")
    releases.publish(
        conn, release_tag="2026.08.21", published_by="an operator",
        private_key=institutional_key["private"],
        key_fingerprint=institutional_key["fingerprint"],
        published_at=PUBLISHED_AT, signed_at=PUBLISHED_AT)

    stored = conn.execute(
        "SELECT upstream_releases FROM drugref.release_manifest "
        "WHERE release_tag = '2026.08.21'").fetchone()[0]
    assert {"source": "UNII", "writer": "unii_run",
            "release": "2026.08.01"} in stored, (
        "publish must record each loaded release under exactly these three keys -- "
        "they are what _verify_manifest_signature reads back into signed bytes")

    # AND THE READER SURVIVES THE ROUND TRIP. Asserting the stored shape alone would
    # still pass if the verifier read different keys; this drives the real path.
    verdict = release_verification.verify_release(conn, "2026.08.21")
    assert verdict.signature == signing.VALID
    assert verdict.is_intact


def test_a_malformed_upstream_member_is_reported_not_a_traceback(
        conn, institutional_key, published):
    """The other half of I3. `upstream_releases` is CHECKed only as an ARRAY, so its
    members are unconstrained -- a hand-written manifest holding a scalar, or objects
    with different keys, reached a bare `KeyError`/`TypeError` that `cli.main` does not
    catch, on an insert-only table with no correction path."""
    conn.execute(
        "INSERT INTO drugref.release_manifest (release_tag, manifest_digest, "
        "row_count, upstream_releases, published_by, published_at) "
        "VALUES ('2026.08.22', %s, 0, '[{\"src\": \"UNII\"}]'::jsonb, 'op', %s)",
        (b"\x09" * 32, PUBLISHED_AT))
    with pytest.raises(release_verification.MalformedManifestError,
                       match="upstream_releases member"):
        release_verification.verify_release(conn, "2026.08.22")


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
    assert (verdict.dropped, verdict.added) == ((), ())


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
    Constructed by hand: no `assertion_signature` row at all.

    `manifest_digest_ok is None` (review round 3, R2): there is no signed payload to
    recompute a digest from at all, which is a different claim from "recomputed and it
    was wrong" (`False`). `is_intact` still reports `False` regardless -- via
    `signature`, not via this field -- so the distinction is real but was, until this
    test, unobserved from outside the function."""
    conn.execute(
        "INSERT INTO drugref.release_manifest (release_tag, manifest_digest, "
        "row_count, upstream_releases, published_by, published_at) "
        "VALUES ('2026.08.23', %s, 0, '[]'::jsonb, 'an operator', %s)",
        (b"\x04" * 32, PUBLISHED_AT))
    verdict = release_verification.verify_release(conn, "2026.08.23")
    assert verdict.signature == signing.NO_SIGNATURE
    assert verdict.manifest_digest_ok is None
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


def test_a_manifest_signed_by_a_different_key_reports_bad_signature(
        conn, institutional_key, a_graded_rule):
    """C1: THE RELEASE LAYER'S ED25519 CHECK, PROVED NEGATIVELY.

    MEASURED GAP, not a hypothetical: replacing `_verify_manifest_signature`'s
    `signature_ok = key is not None and signing.verify(...)` with
    `signature_ok = key is not None` -- deleting the cryptography from the release layer
    outright -- left the whole suite green at 1260 passed. The row layer had
    `test_a_forged_signature_reports_bad_signature`; this layer had nothing, and the
    only test that mentioned BAD_SIGNATURE here called `_worst_verdict` directly or
    hand-built a `ManifestVerdict`, never reaching the production call site.

    THE ATTACK THIS IS: an attacker with SQL write access plants a manifest, its
    entries, and an `assertion_signature` row naming the institution's REGISTERED
    fingerprint but signed with their own private key. The key is known, so this must
    report BAD_SIGNATURE rather than UNKNOWN_KEY -- and `drugref verify --release` must
    not report the release intact.
    """
    curation.record_interaction_judgement(
        conn, a_graded_rule["subject"], a_graded_rule["class"], "CI_MoA", True,
        severity="major", evidence_grade="established", reviewed_by="a curator",
        reviewed_against="MED-RT 2026.07.06")
    forger_private = signing.generate_keypair().private_key
    _publish_manually(
        conn, release_tag="2026.08.13", published_by="an operator",
        key_fingerprint=institutional_key["fingerprint"],   # the INSTITUTION's name...
        private_key=forger_private,                         # ...the ATTACKER's key
        entries=releases.enumerate_live(conn))

    verdict = release_verification.verify_release(conn, "2026.08.13")
    assert verdict.signature == signing.BAD_SIGNATURE
    assert verdict.is_intact is False


def test_a_manifest_body_tampered_after_signing_reports_bad_signature(
        conn, institutional_key, published, a_graded_rule):
    """C1's second half: THE SIGNATURE COVERS THE STORED ENTRIES, not the digest column.

    `release_manifest_entry` permits INSERT (it refuses only UPDATE and DELETE), so an
    attacker can append a row to a manifest that was already published and signed. The
    payload `_verify_manifest_signature` rebuilds is built FROM those stored entries, so
    an appended one changes the bytes and the signature over the old bytes must stop
    verifying.

    This is the case that proves the verifier rebuilds rather than trusting
    `release_manifest.manifest_digest`: a verifier that compared the stored digest
    against itself, or checked nothing, would call this release intact.
    """
    manifest_id = conn.execute(
        "SELECT manifest_id FROM drugref.release_manifest WHERE release_tag = %s",
        (published,)).fetchone()[0]
    conn.execute(
        "INSERT INTO drugref.release_manifest_entry (manifest_id, target_kind, "
        "natural_key, target_id, payload_context, payload_digest) "
        "VALUES (%s, 'curated_interaction', 'a/forged/key', 9999, "
        "'curated_interaction/v1', %s)",
        (manifest_id, b"\xab" * 32))

    verdict = release_verification.verify_release(conn, published)
    assert verdict.signature == signing.BAD_SIGNATURE
    assert verdict.is_intact is False


def test_an_unusable_manifest_signature_context_is_a_verdict_not_a_crash(
        conn, institutional_key, published):
    """C3, the manifest half. `assertion_signature.payload_context` is unconstrained
    beyond its regex on this path too, and `manifest_payload` subscripted
    `signing.FIELD_LISTS` with it -- so one planted row made `drugref verify --release`
    raise `KeyError` (not a `RuntimeError`, so `cli.main` printed a traceback) for the
    life of the database, `release_manifest` being insert-only.

    The genuine signature recorded by `publish` still reports VALID, and `_worst_verdict`
    reports the worst of the two -- so the release is correctly no longer intact, but
    the operator gets a verdict to act on instead of a stack trace."""
    manifest_id = conn.execute(
        "SELECT manifest_id FROM drugref.release_manifest WHERE release_tag = %s",
        (published,)).fetchone()[0]
    conn.execute(
        "INSERT INTO drugref.assertion_signature (target_kind, target_id, algorithm, "
        "key_fingerprint, signature, payload_digest, payload_context, signed_at) "
        "VALUES ('release_manifest', %s, %s, %s, %s, %s, 'bogus/v9', %s)",
        (manifest_id, signing.ED25519, institutional_key["fingerprint"],
         b"\x00" * 64, b"\x00" * 32, PUBLISHED_AT))

    verdict = release_verification.verify_release(conn, published)
    assert verdict.signature == signing.BAD_SIGNATURE
    assert verdict.is_intact is False


def test_a_wrong_manifest_digest_is_not_excused_by_a_later_counter_signature(
        conn, institutional_key):
    """R2 (review round 3, decided now rather than deferred): `manifest_digest_ok`
    must key on the EARLIEST recorded signature, not "any" recorded signature.
    `release_manifest.manifest_digest` is written exactly once, by `publish`, over the
    payload of the ONE signature it records at that moment. "Any" would let a LATER,
    entirely legitimate counter-signature (a genuinely different payload --
    `signed_at` is inside the signed bytes, spec 4.4) vouch for a digest the ORIGINAL
    signature no longer matches -- reachable only through a writer bug, which is
    precisely what this column exists to catch.

    Constructed so "any" and "earliest" would disagree: `manifest_digest` is set to
    the SECOND signature's payload digest, not the first's -- the shape a bug that
    wrote the wrong signature's digest into `release_manifest.manifest_digest` would
    leave behind. Under "any", the second signature's match would have hidden the
    first's mismatch entirely; under "earliest", it does not.
    """
    early, later = PUBLISHED_AT, PUBLISHED_AT + dt.timedelta(days=1)
    payload_first = releases.manifest_payload(
        conn, release_tag="2026.08.26", published_by="an operator",
        published_at=PUBLISHED_AT, entries=[], upstream=[],
        key_fingerprint=institutional_key["fingerprint"], signed_at=early)
    payload_second = releases.manifest_payload(
        conn, release_tag="2026.08.26", published_by="an operator",
        published_at=PUBLISHED_AT, entries=[], upstream=[],
        key_fingerprint=institutional_key["fingerprint"], signed_at=later)
    manifest_id = conn.execute(
        "INSERT INTO drugref.release_manifest (release_tag, manifest_digest, "
        "row_count, upstream_releases, published_by, published_at) "
        "VALUES ('2026.08.26', %s, 0, '[]'::jsonb, 'an operator', %s) "
        "RETURNING manifest_id",
        (signing.digest(payload_second), PUBLISHED_AT)).fetchone()[0]
    signatures.record(
        conn, target_kind="release_manifest", target_id=manifest_id,
        payload_context="release_manifest/v1", payload=payload_first,
        key_fingerprint=institutional_key["fingerprint"],
        signature=signing.sign(institutional_key["private"], payload_first),
        signed_at=early)
    signatures.record(
        conn, target_kind="release_manifest", target_id=manifest_id,
        payload_context="release_manifest/v1", payload=payload_second,
        key_fingerprint=institutional_key["fingerprint"],
        signature=signing.sign(institutional_key["private"], payload_second),
        signed_at=later)
    verdict = release_verification.verify_release(conn, "2026.08.26")
    assert verdict.signature == signing.VALID          # both signatures are genuine
    assert not verdict.manifest_digest_ok              # but the EARLIEST is checked


def test_worst_verdict_picks_the_most_severe():
    """`_worst_verdict` has no coverage through `verify_release` at all today -- no
    test builds a manifest signed more than once with genuinely differing verdicts, so
    the single-element lists every other test in this file produces would pass under
    ANY reduction rule, including `verdicts[0]`. Driven directly rather than through a
    manifest, since building a real multi-signature scenario for this alone would
    exercise `signatures.record`'s dedupe guard as much as this function.

    Includes an UNKNOWN VERDICT -- a future sixth constant this module has not been
    taught about -- to pin the deliberate `-1` ranking (review round 2's design, the
    fix for C4's `KeyError` crash): it must rank WORST, beating even
    `signing.UNKNOWN_KEY`, rather than raising or being silently treated as best.
    """
    assert release_verification._worst_verdict(
        [signing.VALID, signing.KEY_EXPIRED]) == signing.KEY_EXPIRED
    assert release_verification._worst_verdict(
        [signing.VALID, signing.BAD_SIGNATURE,
         signing.KEY_EXPIRED]) == signing.BAD_SIGNATURE
    assert release_verification._worst_verdict(
        [signing.UNKNOWN_KEY, "some_future_verdict"]) == "some_future_verdict"


# ---- final review: C1 -- the natural key is FROZEN, not re-derived ----------------


def test_a_widened_natural_key_trigger_does_not_re_key_a_published_release(
        conn, published):
    """C1 (final review, Critical). A published entry's `natural_key` is a RENDERED
    STRING recorded at publish time AND a signed member of the entry group -- so which
    COLUMNS produced it must be reconstructed from the PAST, exactly as `payload_context`
    and `algorithm` already are, never re-derived from the schema standing today.

    MEASURED BEFORE THE FIX: `releases._natural_key_columns` read the column list out of
    `pg_trigger.tgargs` on every call, including at verify time. Widening
    `curated_interaction`'s single-live trigger -- the additive migration db/029 says in
    as many words will one day happen ("If a real case ever needs per-relationship
    grades it is an additive migration on a table that ships empty") -- re-rendered every
    live natural key, so not one paired with a published entry: `dropped` and `added`
    both non-empty, `is_intact` false, on a database in which NOTHING had changed. That
    is C1-of-Task-8's failure mode reached through schema evolution instead of through
    offset ids, and it is why `signing.NATURAL_KEY_COLUMNS` is frozen.

    THE TRIGGER IS GENUINELY REPLACED HERE, not stubbed: the DDL runs inside this test's
    own transaction (Postgres DDL is transactional and the `conn` fixture rolls back),
    and the assertion below confirms the catalog really reports the wider key before the
    verification is attempted -- without that, a test whose DDL silently failed would
    pass for the wrong reason.

    NO `SET CONSTRAINTS ALL IMMEDIATE` IS NEEDED FIRST, and an earlier version of this
    test both called it and explained it wrongly ("Postgres refuses to drop a trigger on a
    table that still has one [pending deferred event]"). Measured false in both
    directions: with `published`'s pending single-live event outstanding, `DROP TRIGGER`
    succeeds, and removing the call left this test passing. What Postgres actually refuses
    with pending trigger events is `ALTER TABLE` ("cannot ALTER TABLE ... because it has
    pending trigger events") and `TRUNCATE` -- the latter measured true, which is why
    `test_a_row_the_manifest_lists_but_the_database_lacks_is_a_DROP` legitimately does
    call it. The line is deleted rather than re-justified: a call kept for a reason that
    does not hold is a call the next reader has to disprove again.
    """
    from tests.test_live_key_index_guard import _single_live_tables

    conn.execute("DROP TRIGGER curated_interaction_single_live "
                 "ON drugref.curated_interaction")
    conn.execute(
        "CREATE CONSTRAINT TRIGGER curated_interaction_single_live "
        "AFTER INSERT OR UPDATE ON drugref.curated_interaction "
        "DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION "
        "drugref.forbid_multiple_live_assertions("
        "'subject_moiety_uuid', 'object_class_uuid', 'relationship', 'severity')")
    assert dict(_single_live_tables(conn))["curated_interaction"] == (
        "subject_moiety_uuid, object_class_uuid, relationship, severity"), (
        "the widened trigger did not take -- this test would then pass vacuously")

    verdict = release_verification.verify_release(conn, published)
    assert (verdict.dropped, verdict.added, verdict.altered) == ((), (), ())
    assert verdict.is_intact


def test_verification_re_renders_a_natural_key_under_the_entrys_stored_context(
        conn, institutional_key, a_graded_rule, monkeypatch):
    """C1's other half: it is the ENTRY'S OWN `payload_context` that decides which frozen
    key list applies, not today's catalog row -- the same read-back discipline
    `signatures.payload_fields` applies to the field list one layer down.

    A `curated_interaction/v2` whose natural key is the SAME triple would prove nothing,
    so the `/v2` registered here (for this test only, `monkeypatch`, auto-reverted) keys
    on a DIFFERENT pair. The manifest entry is written under `/v2` with the `/v2`-shaped
    key; if `verify_release` rendered the live side under the catalog's current `/v1`
    triple instead, the two would not pair and the release would report one drop and one
    addition.
    """
    v2_key = ("subject_moiety_uuid", "object_class_uuid")
    monkeypatch.setitem(signing.FIELD_LISTS, "curated_interaction/v2",
                        signing.CURATED_INTERACTION_V1)
    monkeypatch.setitem(signing.NATURAL_KEY_COLUMNS, "curated_interaction/v2", v2_key)

    target_id = curation.record_interaction_judgement(
        conn, a_graded_rule["subject"], a_graded_rule["class"], "CI_MoA", True,
        severity="major", evidence_grade="established", reviewed_by="a curator",
        reviewed_against="MED-RT 2026.07.06")
    v2_natural_key = releases.natural_key_of(
        conn, "curated_interaction", target_id, payload_context="curated_interaction/v2")
    assert v2_natural_key.count("/") == 1, "the /v2 key must differ from /v1's triple"
    _, payload = signatures.payload_for(
        conn, "curated_interaction", target_id, key_fingerprint="",
        signed_at=releases.ENTRY_DIGEST_SIGNED_AT,
        payload_context="curated_interaction/v2")
    entries = [releases.ManifestEntry(
        "curated_interaction", v2_natural_key, target_id, "curated_interaction/v2",
        signing.digest(payload))]
    _publish_manually(
        conn, release_tag="2026.08.30", published_by="an operator",
        key_fingerprint=institutional_key["fingerprint"],
        private_key=institutional_key["private"], entries=entries)

    verdict = release_verification.verify_release(conn, "2026.08.30")
    assert (verdict.dropped, verdict.added, verdict.altered) == ((), (), ())
    assert verdict.is_intact


# ---- final review: C2 -- the mutation gate for the MANIFEST's signed members ------
#
# THE ROW LAYER HAD THIS AND THIS LAYER DID NOT. tests/test_signatures_writer.py runs one
# mutation case per field of `curated_interaction/v1`, per spec 12's rule ("one mutation
# test per signed field"), and the manifest's own signed members -- seven scalars plus
# four fields on every `--entries--` member and three on every `--upstream--` one -- had
# none at all: dropping `payload_digest`, `natural_key` or `entry_count` from
# `manifest_payload`'s output left the whole 210-test signing suite green.
#
# `release_manifest_entry` IS INSERT-ONLY, so the row-layer trick (edit the row, rebuild)
# is unavailable and unnecessary: the test rebuilds the PAYLOAD with one member changed
# and checks the recorded signature against those bytes, which is what
# test_signatures_writer.py does one layer down for the same reason.

_MANIFEST_SCALARS = {
    "release_tag": "2026.08.31",
    "published_by": "an operator",
    "published_at": PUBLISHED_AT,
    "signed_at": PUBLISHED_AT,
}


def _manifest_parts(entries, upstream, *, key_fingerprint):
    """The `(fields, groups)` pair `releases.manifest_payload` builds internally, rebuilt
    here so ONE member can be mutated in isolation.

    A LOCAL REBUILD IS ONLY SAFE BECAUSE IT IS CHECKED. Every caller below asserts the
    UNMUTATED rebuild is byte-identical to `manifest_payload`'s own output before
    applying any mutation -- tests/test_signatures_writer.py's M2 baseline discipline:
    without it, a rebuild that had drifted from production for some unrelated reason
    would make every case pass vacuously, both payloads merely differing for reasons
    having nothing to do with the field under test.
    """
    scalar_values = {
        "release_tag": _MANIFEST_SCALARS["release_tag"],
        "published_by": _MANIFEST_SCALARS["published_by"],
        "published_at": signing.render(_MANIFEST_SCALARS["published_at"]),
        "entry_count": str(len(entries)),
        "upstream_count": str(len(upstream)),
        "signer_key_fingerprint": key_fingerprint,
        "signed_at": signing.render(_MANIFEST_SCALARS["signed_at"]),
    }
    fields = [(name, scalar_values[name])
              for name in signing.FIELD_LISTS["release_manifest/v1"]]
    entry_members = [
        [("target_kind", e.target_kind), ("natural_key", e.natural_key),
         ("payload_context", e.payload_context),
         ("payload_digest", signing.render(e.payload_digest))]
        for e in entries]
    upstream_members = [[("source", s), ("writer", w), ("release", r)]
                        for s, w, r in upstream]
    return fields, [("entries", entry_members), ("upstream", upstream_members)]


def _two_entries():
    """Two manifest entries and two upstream releases -- enough that a mutation to ONE
    member cannot be confused with a change to the group's cardinality, and enough for
    `entry_count`/`upstream_count` to be a number other than 0 or 1."""
    entries = [
        releases.ManifestEntry("curated_interaction", "aaa/bbb/CI_MoA", 1,
                               "curated_interaction/v1", b"\x11" * 32),
        releases.ManifestEntry("curated_condition", "ccc/ddd", 2,
                               "curated_condition/v1", b"\x22" * 32),
    ]
    upstream = [("MED-RT", "association", "2026.07.06"), ("GSRS", "load", "2026-02-26")]
    return entries, upstream


# (group_or_None, field_name, mutated_value). `None` names a top-level scalar.
_MANIFEST_MUTATIONS = [
    (None, "release_tag", "2026.09.99"),
    (None, "published_by", "somebody else"),
    (None, "published_at", "2000-01-01T00:00:00.000000Z"),
    (None, "entry_count", "1"),
    (None, "upstream_count", "1"),
    (None, "signer_key_fingerprint", "b" * 64),
    (None, "signed_at", "2000-01-01T00:00:00.000000Z"),
    ("entries", "target_kind", "curated_condition"),
    ("entries", "natural_key", "zzz/yyy/CI_ChemClass"),
    ("entries", "payload_context", "curated_condition/v1"),
    ("entries", "payload_digest", ("ff" * 32)),
    ("upstream", "source", "SOMETHING-ELSE"),
    ("upstream", "writer", "another_writer"),
    ("upstream", "release", "1999.01.01"),
]


def test_the_manifest_mutation_gate_covers_every_signed_member(conn,
                                                               institutional_key):
    """M1's discipline, applied to this gate: guards the parametrisation above against
    becoming a second, unenforced copy of what `manifest_payload` actually signs. A
    member added to the payload with no case here would otherwise be invisible -- which
    is precisely how `entry_count`, `natural_key` and `payload_digest` came to be
    uncovered in the first place."""
    entries, upstream = _two_entries()
    fields, groups = _manifest_parts(
        entries, upstream, key_fingerprint=institutional_key["fingerprint"])
    covered = {(group, name) for group, name, _ in _MANIFEST_MUTATIONS}
    expected = {(None, name) for name, _ in fields}
    for group_name, members in groups:
        expected |= {(group_name, name) for name, _ in members[0]}
    assert covered == expected


@pytest.mark.parametrize("group,field,mutated", _MANIFEST_MUTATIONS)
def test_changing_any_signed_manifest_member_breaks_the_signature(
        conn, institutional_key, group, field, mutated):
    """ONE TEST PER SIGNED MEMBER of `release_manifest/v1`, spec 12 item 2 -- the rule
    the row layer already followed and this layer did not.

    A member silently missing from the payload is the one defect this layer cannot
    survive: the manifest signature would keep verifying while the omitted member was
    free to be anything. Drop `payload_digest` and a manifest attests only that some rows
    with these natural keys existed; drop `natural_key` and it attests only how many;
    drop `entry_count` and a group truncated at its end -- the exact failure spec 5.5
    says the scalar count exists to make nameable -- stops being nameable.
    """
    entries, upstream = _two_entries()
    fingerprint = institutional_key["fingerprint"]
    payload = releases.manifest_payload(
        conn, entries=entries, upstream=upstream, key_fingerprint=fingerprint,
        **_MANIFEST_SCALARS)
    signature = signing.sign(institutional_key["private"], payload)

    fields, groups = _manifest_parts(entries, upstream, key_fingerprint=fingerprint)
    baseline = signing.canonical_payload("release_manifest/v1", fields, groups)
    assert baseline == payload, (
        "the unmutated rebuild does not match manifest_payload's own bytes -- this "
        "test's premise (that a difference below is caused by the mutated member "
        "alone) does not hold, and every case would pass vacuously")

    if group is None:
        fields = [(n, mutated if n == field else v) for n, v in fields]
    else:
        groups = [
            (g, [[(n, mutated if (g == group and n == field and i == 0) else v)
                  for n, v in member]
                 for i, member in enumerate(members)])
            for g, members in groups]
    rebuilt = signing.canonical_payload("release_manifest/v1", fields, groups)
    assert rebuilt != payload, (
        f"changing {group or 'scalar'}.{field} did not change the payload -- it is not "
        "covered by the manifest's signed bytes, so a signature says nothing about it")
    assert signing.verify(institutional_key["public"], rebuilt, signature) is False


# ---- final review: C3 -- is_intact really does need a VALID signature -------------


@pytest.mark.parametrize("signature", [
    signing.BAD_SIGNATURE, signing.UNKNOWN_KEY, signing.KEY_REVOKED_COMPROMISED,
    signing.KEY_EXPIRED])
def test_a_non_valid_signature_alone_makes_a_release_not_intact(signature):
    """C3 (final review, Critical). `is_intact`'s FIRST clause -- `signature ==
    signing.VALID` -- had no test that killed its removal: deleting it left the whole
    suite green, because the only test exercising a non-VALID release signature
    (`test_a_compromised_publishing_key_flags_the_release`) asserted `.signature` and
    never `.is_intact`.

    THE OTHER FOUR CLAUSES ARE HELD TRUE HERE ON PURPOSE, so nothing but the signature
    can be what fails. `NO_SIGNATURE` is deliberately NOT a case: on that path
    `manifest_digest_ok` is `None` and independently sinks the property, so it could
    never distinguish the clause -- which is the coincidence
    `_verify_manifest_signature`'s own docstring already warns is not a guarantee.

    `drugref verify --release`'s exit code rides entirely on this property
    (`cli_signing_release._verify_release`), so an unregistered or revoked institutional
    key must fail a deploy gate, and this is the test that says so.
    """
    verdict = release_verification.ManifestVerdict(
        release_tag="2026.08.09", signature=signature, dropped=[], added=[], altered=[],
        row_count_ok=True, manifest_digest_ok=True)
    assert not verdict.is_intact


def test_a_valid_signature_with_nothing_else_wrong_is_intact():
    """The control for the four cases above. Without it, an `is_intact` hard-wired to
    `False` would satisfy every one of them and report every real release as broken."""
    verdict = release_verification.ManifestVerdict(
        release_tag="2026.08.09", signature=signing.VALID, dropped=[], added=[],
        altered=[], row_count_ok=True, manifest_digest_ok=True)
    assert verdict.is_intact


# ---- final review: I7/I8 and the earliest-signature digest -----------------------


def test_publish_signs_under_the_catalogs_context_not_a_literal(
        conn, institutional_key, monkeypatch):
    """I7 (final review). `publish` used to hard-code `'release_manifest/v1'` twice --
    once as the payload's context, once on the `assertion_signature` row -- which made
    `signature_target_kind`'s value for that kind DEAD: minting a `/v2` there changed
    nothing at all, and `publish` would have gone on signing `/v1` bytes while every
    reader that consults the catalog believed `/v2`. The other two target kinds have
    always taken their context from the catalog (`signatures.payload_fields` does it);
    this is the third doing the same.

    `signature_target_kind` CARRIES NO APPEND-ONLY FLOOR, deliberately -- it is designed
    to move to a `/v2` (that is the whole reason `payload_context` is read back per
    signature), so an UPDATE here is the ordinary migration path, not a violation. The
    `conn` fixture rolls it back regardless.
    """
    monkeypatch.setitem(signing.FIELD_LISTS, "release_manifest/v2",
                        signing.RELEASE_MANIFEST_V1)
    conn.execute("UPDATE drugref.signature_target_kind "
                 "SET payload_context = 'release_manifest/v2' "
                 "WHERE target_kind = 'release_manifest'")

    manifest_id = releases.publish(
        conn, release_tag="2026.08.28", published_by="an operator",
        private_key=institutional_key["private"],
        key_fingerprint=institutional_key["fingerprint"],
        published_at=PUBLISHED_AT, signed_at=PUBLISHED_AT)

    assert conn.execute(
        "SELECT payload_context FROM drugref.assertion_signature "
        "WHERE target_kind = 'release_manifest' AND target_id = %s",
        (manifest_id,)).fetchone()[0] == "release_manifest/v2"
    # AND THE BYTES AGREE WITH THE ROW: a signature recorded as /v2 over /v1 bytes would
    # pass the assertion above and fail here, which is the half that actually matters.
    assert release_verification.verify_release(
        conn, "2026.08.28").signature == signing.VALID


def test_enumerate_live_omits_a_superseded_row(conn, a_graded_rule):
    """I8 (final review). `enumerate_live`'s `WHERE superseded_by IS NULL` had no test
    that killed its removal: without it a corrected natural key yields TWO entries, and
    every caller that indexes the list BY natural key -- `verify_release` does -- silently
    keeps whichever the SELECT happened to return last, so the existing correction test
    passed either way. A manifest is a snapshot of what drugref asserts NOW, and a
    superseded row is exactly what drugref no longer asserts.
    """
    first = curation.record_interaction_judgement(
        conn, a_graded_rule["subject"], a_graded_rule["class"], "CI_MoA", True,
        severity="major", evidence_grade="established", reviewed_by="a curator",
        reviewed_against="MED-RT 2026.07.06")
    correction = curation.record_interaction_judgement(
        conn, a_graded_rule["subject"], a_graded_rule["class"], "CI_MoA", True,
        severity="contraindicated", evidence_grade="established",
        reviewed_by="a curator", reviewed_against="MED-RT 2026.07.06")
    conn.execute("SET CONSTRAINTS ALL IMMEDIATE")
    assert conn.execute(
        "SELECT count(*) FROM drugref.curated_interaction").fetchone()[0] == 2, (
        "the correction did not create a second row -- this test needs a real "
        "supersession to have anything to omit")

    entries = releases.enumerate_live(conn)
    assert [e.target_id for e in entries] == [correction], (
        f"enumerate_live returned {[e.target_id for e in entries]}; only the live row "
        f"{correction} belongs in a manifest, never its superseded predecessor {first}")


def test_a_matching_context_reuses_the_digest_enumerate_live_already_built(
        conn, published, monkeypatch):
    """I8's other half. `verify_release` reuses `enumerate_live`'s ALREADY-COMPUTED
    digest when the entry's stored `payload_context` matches the live one, and only falls
    back to `signatures.payload_for` when it does not -- review round 2's C5, which
    measured the recompute as doubling this function's query count for nothing.

    FORCING THE SLOW PATH ALWAYS IS BEHAVIOURALLY INVISIBLE (recomputing under the SAME
    context yields the same digest), so no assertion about a verdict can catch it. The
    property is therefore pinned where it actually lives: `payload_for` must not be
    called at all on the ordinary path. `test_verification_re_renders_a_natural_key_
    under_the_entrys_stored_context` and its sibling cover the other direction -- that
    the fallback is really taken when the contexts DIFFER.
    """
    calls = []
    real_payload_for = signatures.payload_for

    def counting_payload_for(*args, **kwargs):
        calls.append(kwargs.get("payload_context"))
        return real_payload_for(*args, **kwargs)

    monkeypatch.setattr(signatures, "payload_for", counting_payload_for)
    assert release_verification.verify_release(conn, published).is_intact
    assert calls == [], (
        f"verify_release recomputed {len(calls)} entry digest(s) that enumerate_live "
        "had already built one line earlier, under the identical context")


def test_a_matching_earliest_digest_is_not_condemned_by_a_later_counter_signature(
        conn, institutional_key):
    """The DISTINGUISHING case for `digest_matches[0]`, which `all(...)` fails and the
    existing "any" test could not reach: there, the two answers were `[False, True]`, on
    which `[0]` and `all(...)` agree, so replacing one with the other left the suite
    green.

    Here the EARLIEST signature's payload is exactly what `manifest_digest` records --
    the correct, ordinary state `publish` produces -- and a later counter-signature
    covers genuinely different bytes (`signed_at` is inside them, spec 4.4) and so can
    never match. `all(...)` would report every legitimately counter-signed release as
    carrying a wrong digest; `[0]` reports the truth.
    """
    early, later = PUBLISHED_AT, PUBLISHED_AT + dt.timedelta(days=1)
    kwargs = dict(release_tag="2026.08.29", published_by="an operator",
                  published_at=PUBLISHED_AT, entries=[], upstream=[],
                  key_fingerprint=institutional_key["fingerprint"])
    payload_first = releases.manifest_payload(conn, signed_at=early, **kwargs)
    payload_second = releases.manifest_payload(conn, signed_at=later, **kwargs)
    assert payload_first != payload_second, (
        "the two signatures must cover different bytes for this test to distinguish "
        "anything")
    manifest_id = conn.execute(
        "INSERT INTO drugref.release_manifest (release_tag, manifest_digest, "
        "row_count, upstream_releases, published_by, published_at) "
        "VALUES ('2026.08.29', %s, 0, '[]'::jsonb, 'an operator', %s) "
        "RETURNING manifest_id",
        (signing.digest(payload_first), PUBLISHED_AT)).fetchone()[0]
    for payload, moment in ((payload_first, early), (payload_second, later)):
        signatures.record(
            conn, target_kind="release_manifest", target_id=manifest_id,
            payload_context="release_manifest/v1", payload=payload,
            key_fingerprint=institutional_key["fingerprint"],
            signature=signing.sign(institutional_key["private"], payload),
            signed_at=moment)
    verdict = release_verification.verify_release(conn, "2026.08.29")
    assert verdict.signature == signing.VALID
    assert verdict.manifest_digest_ok
    assert verdict.is_intact


def test_the_entry_digest_sentinel_is_pinned_by_a_published_vector(conn):
    """I3 (final review). `releases.ENTRY_DIGEST_SIGNED_AT` is a frozen WIRE constant --
    every manifest entry digest ever computed, at publish time and at every later
    verification, is built under it -- and nothing read its value: changing
    `1970-01-01` left the whole suite green while every previously published manifest
    silently stopped verifying.

    PINNED AGAINST THE COMMITTED VECTOR rather than against a literal repeated here, so
    the constant is tied to bytes a third party can check with `sha256sum` (fixture case
    4). A second Python literal would only be a second home for the same value.
    """
    import json
    import pathlib
    vectors = json.loads(
        (pathlib.Path(__file__).parent / "fixtures" / "signing_vectors.json").read_text())
    case = next(c for c in vectors["cases"] if c["context"] == "curated_interaction/v1"
                and dict((n, v) for n, v in c["fields"])["signer_key_fingerprint"] == "")
    fields = dict((n, v) for n, v in case["fields"])
    assert fields["signed_at"] == signing.render(releases.ENTRY_DIGEST_SIGNED_AT)
    assert fields["signer_key_fingerprint"] == "", (
        "an entry digest names no signer -- see enumerate_live's docstring")


# ---- final re-review: R1 -- an unusable entry context must not crash the verifier ----


@pytest.mark.parametrize("bogus_context,why", [
    ("bogus/v9", "no frozen field list has ever known this context"),
    ("curated_condition/v1", "a real context, but for the OTHER target kind"),
])
def test_an_unreproducible_entry_context_fails_to_pair_rather_than_raising(
        conn, institutional_key, a_graded_rule, bogus_context, why):
    """R1 (final re-review, Important) -- a defect the C1 fix INTRODUCED.

    `release_manifest_entry.payload_context` carries a regex CHECK and, deliberately, no
    foreign key, so both values below are storable with one INSERT. The first version of
    the C1 fix subscripted `signing.NATURAL_KEY_COLUMNS` / `signing.FIELD_LISTS` directly
    and raised `KeyError` -- which is not a `RuntimeError`, so `cli.main` does not catch
    it and `drugref verify --release` printed a raw traceback.

    THAT WAS A REGRESSION, NOT MERELY A GAP: the `pg_trigger` code the fix replaced
    reported drop+add here. It also contradicted two comments in the fix's own diff --
    `_worst_verdict`'s stated principle that a crash at the verification core is strictly
    worse than treating an unknown outcome with maximum suspicion, and `verify_release`'s
    own claim that an unmatched context is reported "and never a crash".

    THE ANSWER IS THE ONE THOSE COMMENTS ALREADY PROMISED: the entry fails to pair. It is
    reported `dropped` (its claim can be checked against nothing) and the live row it
    might have described is reported `added` (nothing matched it), the verdict is not
    intact, and no exception escapes. The second case matters separately from the first:
    `curated_condition/v1` IS a known context, so a mere `in FIELD_LISTS` test would let
    it through to `signatures.payload_for`, which would SELECT `object_condition_uuid`
    from `curated_interaction` and raise `psycopg.errors.UndefinedColumn` instead -- the
    same crash, one layer over.
    """
    target_id = curation.record_interaction_judgement(
        conn, a_graded_rule["subject"], a_graded_rule["class"], "CI_MoA", True,
        severity="major", evidence_grade="established", reviewed_by="a curator",
        reviewed_against="MED-RT 2026.07.06")
    # THE NATURAL KEY IS THE REAL ONE, so the entry would otherwise PAIR -- that is what
    # drives the bogus context all the way into the digest rebuild. An entry that failed
    # to pair on its key alone would never reach the code under test.
    natural_key = releases.natural_key_of(conn, "curated_interaction", target_id)
    entries = [releases.ManifestEntry(
        "curated_interaction", natural_key, target_id, bogus_context, b"\x33" * 32)]
    _publish_manually(
        conn, release_tag=f"2026.09.10-{bogus_context.replace('/', '-')}",
        published_by="an operator", key_fingerprint=institutional_key["fingerprint"],
        private_key=institutional_key["private"], entries=entries)

    verdict = release_verification.verify_release(
        conn, f"2026.09.10-{bogus_context.replace('/', '-')}")
    assert verdict.signature == signing.VALID       # the manifest really is drugref's
    assert verdict.dropped == (("curated_interaction", natural_key),), why
    assert verdict.added == (("curated_interaction", natural_key),), why
    assert verdict.altered == ()
    assert not verdict.is_intact


def test_an_unreproducible_context_does_not_hide_the_rest_of_the_manifest(
        conn, institutional_key, a_graded_rule, ingest_run_id):
    """The control for the pair above, and the reason `enumerate_live` FALLS BACK to the
    current context rather than skipping the kind: one unusable entry must not take the
    rest of the enumeration down with it.

    Skipping the kind would empty the live side for `curated_interaction`, so a second,
    perfectly good live row would silently stop being reported as `added` -- FEWER
    FINDINGS, which is the wrong direction for a verifier exactly as fewer rows is the
    wrong direction for a contraindication. Here the manifest holds one unusable entry and
    the database holds a second, genuinely unpublished rule; both must be reported.
    """
    from tests.test_signature_read_path import _a_second_graded_rule

    target_id = curation.record_interaction_judgement(
        conn, a_graded_rule["subject"], a_graded_rule["class"], "CI_MoA", True,
        severity="major", evidence_grade="established", reviewed_by="a curator",
        reviewed_against="MED-RT 2026.07.06")
    natural_key = releases.natural_key_of(conn, "curated_interaction", target_id)
    entries = [releases.ManifestEntry(
        "curated_interaction", natural_key, target_id, "bogus/v9", b"\x44" * 32)]
    _publish_manually(
        conn, release_tag="2026.09.11", published_by="an operator",
        key_fingerprint=institutional_key["fingerprint"],
        private_key=institutional_key["private"], entries=entries)
    second = _a_second_graded_rule(conn, ingest_run_id, a_graded_rule["subject"])
    second_key = releases.natural_key_of(
        conn, "curated_interaction", second["curated_interaction_id"])

    verdict = release_verification.verify_release(conn, "2026.09.11")
    assert second_key in [k for _kind, k in verdict.added], (
        "the second live rule vanished from `added` -- an unusable entry must not empty "
        "the live enumeration for its whole target kind")
    assert natural_key in [k for _kind, k in verdict.dropped]
    assert not verdict.is_intact


# ---- review round 4 additions: the two defensive branches, tested ---------------

def test_two_predecessors_pointing_at_one_successor_are_refused_not_guessed(
        conn, institutional_key, a_graded_rule):
    """REVIEW I9: THIS BRANCH WAS DOCUMENTED AS REACHED, AND NEVER TESTED.

    `_published_content_is_history`'s own comment records that round 3 committed two
    rows pointing `superseded_by` at one successor, with `SET CONSTRAINTS ALL IMMEDIATE`
    confirming NO TRIGGER OBJECTED -- db/020's deferred single-live check counts LIVE
    rows per natural key and says nothing about how many rows point AT one successor.
    That measurement was never preserved as a test, so deleting the guard and taking
    `predecessors[0]` left the suite green while the verifier silently picked whichever
    row the planner returned first when deciding `altered` vs `dropped`+`added`.

    ALSO PINS THE CLASS. It raised a bare `ValueError`, which `cli.main`'s
    `(RuntimeError, ...)` catch misses, so `drugref verify --release` printed a raw
    traceback on a database in this state rather than one sentence.
    """
    # A THREE-LINK CHAIN on ONE natural key: first -> second -> third. Every row here
    # is a legitimate correction, so the floor's two rules both hold -- `superseded_by`
    # points at a LATER row, and a correction keeps the same natural key.
    first = curation.record_interaction_judgement(
        conn, a_graded_rule["subject"], a_graded_rule["class"], "CI_MoA", True,
        severity="major", evidence_grade="established", reviewed_by="a curator",
        reviewed_against="MED-RT 2026.07.06")
    second = curation.record_interaction_judgement(
        conn, a_graded_rule["subject"], a_graded_rule["class"], "CI_MoA", True,
        severity="moderate", evidence_grade="established", reviewed_by="a curator",
        reviewed_against="MED-RT 2026.07.06")
    third = curation.record_interaction_judgement(
        conn, a_graded_rule["subject"], a_graded_rule["class"], "CI_MoA", True,
        severity="contraindicated", evidence_grade="established",
        reviewed_by="a curator", reviewed_against="MED-RT 2026.07.06")
    conn.execute("SET CONSTRAINTS ALL IMMEDIATE")
    assert first < second < third

    # `first` BENT to point past `second`, straight at `third`, so `third` has TWO
    # predecessors. THE FLOOR IS SUSPENDED FOR THAT ONE STATEMENT, deliberately and
    # narrowly: `superseded_by` is ONE-WAY (NULL -> an id, exactly once), which is what
    # makes this state unreachable through any ordinary path -- and therefore what makes
    # the guard under test a defence against a database somebody has already got at,
    # not against drugref's own writers. Review round 3 reached the same branch the same
    # way. Re-enabled immediately, and `conn` rolls the whole thing back regardless.
    conn.execute("ALTER TABLE drugref.curated_interaction DISABLE TRIGGER USER")
    conn.execute(
        "UPDATE drugref.curated_interaction SET superseded_by = %s "
        "WHERE curated_interaction_id = %s", (third, first))
    conn.execute("ALTER TABLE drugref.curated_interaction ENABLE TRIGGER USER")
    assert conn.execute(
        "SELECT count(*) FROM drugref.curated_interaction WHERE superseded_by = %s",
        (third,)).fetchone()[0] == 2, "the ambiguous state must really exist"

    with pytest.raises(release_verification.AmbiguousSupersessionError,
                       match="superseded_by values"):
        release_verification._published_content_is_history(
            conn, "curated_interaction", third, "curated_interaction/v1",
            b"\x00" * 32)


def test_the_curated_signature_status_counts_are_real(conn, a_graded_rule):
    """REVIEW S2: `signature_count` and `unobjected_count` are the published companion
    columns spec 9 promises at the curated-row grain, and nothing asserted either. With
    `count(*) AS signature_count` mutated to `1 AS signature_count` the suite stayed
    green -- so a consumer reading "how many curators attested this?" had no gate behind
    it at all. Two signatures by two different curators, both unobjected."""
    from drugref import keys, signatures as sigs

    target_id = curation.record_interaction_judgement(
        conn, a_graded_rule["subject"], a_graded_rule["class"], "CI_MoA", True,
        severity="major", evidence_grade="established", reviewed_by="a curator",
        reviewed_against="MED-RT 2026.07.06")
    for holder, when in (("a curator", PUBLISHED_AT),
                         ("a second curator", PUBLISHED_AT + dt.timedelta(days=1))):
        keypair = signing.generate_keypair()
        fingerprint = signing.fingerprint(keypair.public_key)
        keys.register(conn, public_key=keypair.public_key, holder=holder,
                      registered_by="an operator",
                      status_from=PUBLISHED_AT - dt.timedelta(days=1))
        context, payload = sigs.payload_for(
            conn, "curated_interaction", target_id,
            key_fingerprint=fingerprint, signed_at=when)
        sigs.record(conn, target_kind="curated_interaction", target_id=target_id,
                    payload_context=context, payload=payload,
                    key_fingerprint=fingerprint,
                    signature=signing.sign(keypair.private_key, payload),
                    signed_at=when)

    assert conn.execute(
        "SELECT signature_count, unobjected_count "
        "FROM drugref.curated_signature_status "
        "WHERE target_kind = 'curated_interaction' AND target_id = %s",
        (target_id,)).fetchone() == (2, 2)
