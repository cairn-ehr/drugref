"""curated_property_ruling (db/058) and its writer, curation_property.

DB-gated. One test per clause of the table's contract: round trip, idempotence,
supersession, the class-subject restrictions, relationship/object coherence, the
append-only floor, and registration as a signed, released kind.
"""
import psycopg
import pytest

from drugref import curation_property, releases, signing_contexts
from tests import class_profile_world as w


@pytest.fixture
def world(conn):
    r = w.run(conn)
    return conn, w.moiety(conn, r, "drugA"), w.klass(conn, r, "ClassA"), \
        w.condition(conn, r, "Epilepsy"), w.moiety(conn, r, "drugB")


def _rule(conn, *, verdict="holds", relationship="CI_with", object_kind="condition",
          **kw):
    return curation_property.record_property_ruling(
        conn, relationship=relationship, object_kind=object_kind, verdict=verdict,
        reviewed_by="Dr X", reviewed_against="test", **kw)[0]


def test_a_moiety_holds_ruling_round_trips(world):
    conn, m, _k, c, _b = world
    rid = _rule(conn, subject_moiety_uuid=m, object_uuid=c)
    assert conn.execute(
        "SELECT subject_kind, subject_uuid, object_kind, object_uuid, verdict "
        "FROM drugref.curated_property_ruling WHERE superseded_by IS NULL "
        "AND curated_property_ruling_id = %s", (rid,)).fetchall() == [
        ("moiety", m, "condition", c, "holds")]


def test_re_recording_the_same_verdict_writes_nothing(world):
    conn, m, _k, c, _b = world
    first = _rule(conn, subject_moiety_uuid=m, object_uuid=c)
    assert _rule(conn, subject_moiety_uuid=m, object_uuid=c) == first
    assert conn.execute("SELECT count(*) FROM drugref.curated_property_ruling "
                        "WHERE subject_uuid = %s", (m,)).fetchone()[0] == 1


def test_the_writer_says_whether_it_wrote(world):
    """The CLI must not print "recorded" for an identical re-answer that wrote nothing."""
    conn, m, _k, c, _b = world
    kw = dict(subject_moiety_uuid=m, relationship="CI_with", object_kind="condition",
              object_uuid=c, verdict="holds", reviewed_by="t", reviewed_against="t")
    rid, written = curation_property.record_property_ruling(conn, **kw)
    assert written is True
    assert curation_property.record_property_ruling(conn, **kw) == (rid, False)


def test_a_changed_verdict_supersedes(world):
    conn, m, _k, c, _b = world
    old = _rule(conn, subject_moiety_uuid=m, object_uuid=c)
    new = _rule(conn, verdict="does_not_hold", subject_moiety_uuid=m, object_uuid=c)
    assert new != old
    rows = conn.execute(
        "SELECT curated_property_ruling_id, superseded_by FROM "
        "drugref.curated_property_ruling WHERE subject_uuid = %s ORDER BY 1",
        (m,)).fetchall()
    assert rows == [(old, new), (new, None)]


def test_a_class_subject_may_rule_a_condition_relationship(world):
    conn, _m, k, c, _b = world
    _rule(conn, subject_class_uuid=k, object_uuid=c)  # control: the allowed shape


def test_a_class_subject_may_not_rule_an_interaction(world):
    conn, _m, k, _c, b = world
    with pytest.raises(psycopg.errors.CheckViolation):
        _rule(conn, relationship="interacts_with", object_kind="moiety",
              subject_class_uuid=k, object_uuid=b)


def test_a_class_subject_may_not_answer_cannot_establish(world):
    conn, _m, k, c, _b = world
    with pytest.raises(psycopg.errors.CheckViolation):
        _rule(conn, verdict="cannot_establish", subject_class_uuid=k, object_uuid=c)


def test_relationship_must_match_object_kind(world):
    conn, m, _k, c, _b = world
    with pytest.raises(psycopg.errors.CheckViolation):
        _rule(conn, relationship="interacts_with", subject_moiety_uuid=m,
              object_uuid=c)


def test_a_moiety_interaction_ruling_is_allowed(world):
    conn, m, _k, _c, b = world
    _rule(conn, relationship="interacts_with", object_kind="moiety",
          verdict="cannot_establish", subject_moiety_uuid=m, object_uuid=b)


def test_a_drug_pair_is_stored_in_one_orientation(world):
    """Interactions are symmetric, so (X, Y) and (Y, X) are ONE natural key: the writer
    stores the lower uuid as the subject. Otherwise both orientations could be live at
    once, and a later correction recorded from the other side would be outvoted by the
    earlier does_not_hold without anything saying so."""
    conn, m, _k, _c, b = world
    lo, hi = sorted((m, b))
    first = _rule(conn, relationship="interacts_with", object_kind="moiety",
                  subject_moiety_uuid=lo, object_uuid=hi, verdict="does_not_hold")
    second = _rule(conn, relationship="interacts_with", object_kind="moiety",
                   subject_moiety_uuid=hi, object_uuid=lo)
    assert conn.execute(
        "SELECT subject_uuid, object_uuid, verdict, superseded_by "
        "FROM drugref.curated_property_ruling WHERE relationship = 'interacts_with' "
        "ORDER BY curated_property_ruling_id").fetchall() == [
        (lo, hi, "does_not_hold", second), (lo, hi, "holds", None)]
    assert first != second


def test_the_table_refuses_an_unordered_drug_pair(world):
    conn, m, _k, _c, b = world
    lo, hi = sorted((m, b))
    with pytest.raises(psycopg.errors.CheckViolation):
        conn.execute(
            "INSERT INTO drugref.curated_property_ruling (subject_moiety_uuid, "
            "relationship, object_moiety_uuid, verdict, source, reviewed_by, "
            "reviewed_against) VALUES (%s, 'interacts_with', %s, 'holds', 'DRUGREF', "
            "'x', 'x')", (hi, lo))


def test_a_drug_cannot_be_ruled_to_interact_with_itself(world):
    conn, m, _k, _c, _b = world
    with pytest.raises(psycopg.errors.CheckViolation):
        _rule(conn, relationship="interacts_with", object_kind="moiety",
              subject_moiety_uuid=m, object_uuid=m)


@pytest.mark.parametrize("both", [True, False])
def test_exactly_one_subject(world, both):
    conn, m, k, c, _b = world
    kw = {"subject_moiety_uuid": m, "subject_class_uuid": k} if both else {}
    with pytest.raises(ValueError):
        _rule(conn, object_uuid=c, **kw)


def test_rows_are_append_only(world):
    conn, m, _k, c, _b = world
    _rule(conn, subject_moiety_uuid=m, object_uuid=c)
    with pytest.raises(psycopg.errors.RaiseException, match="append-only"):
        conn.execute("UPDATE drugref.curated_property_ruling SET verdict = 'does_not_hold'")


def test_two_live_rows_for_one_key_are_refused_at_commit(world):
    """The deferred single-live guard sees the GENERATED key columns (non-NULL)."""
    conn, m, _k, c, _b = world
    for v in ("holds", "does_not_hold"):
        conn.execute(
            "INSERT INTO drugref.curated_property_ruling (subject_moiety_uuid, "
            "relationship, object_condition_uuid, verdict, source, reviewed_by, "
            "reviewed_against) VALUES (%s, 'CI_with', %s, %s, 'DRUGREF', 'x', 'x')",
            (m, c, v))
    with pytest.raises(psycopg.errors.RaiseException, match="live rows"):
        conn.execute("SET CONSTRAINTS ALL IMMEDIATE")


def test_the_table_is_a_signature_target_and_in_every_release(conn):
    assert conn.execute(
        "SELECT target_table, pk_column, payload_context FROM "
        "drugref.signature_target_kind WHERE target_kind = 'curated_property_ruling'"
    ).fetchone() == ("curated_property_ruling", "curated_property_ruling_id",
                     "curated_property_ruling/v1")
    assert "curated_property_ruling" in releases._CURATED_KINDS
    assert "curated_property_ruling/v1" in signing_contexts.FIELD_LISTS
    assert "curated_property_ruling/v1" in signing_contexts.NATURAL_KEY_COLUMNS


def test_a_closed_gap_carrying_a_ruling_is_retained_not_deleted(conn):
    """The retention guard in questions.register_from_gaps must list this table: its
    question FK is ON DELETE CASCADE into an append-only table, so an omission would
    abort every later ingest."""
    from drugref import questions
    r = w.run(conn)
    m = w.moiety(conn, r, "drugQ")
    c = w.condition(conn, r, "CondQ")
    questions.register_from_gaps(conn, r)
    # Keyed on THIS moiety: other tests commit questions of their own.
    qu = conn.execute("SELECT question_uuid FROM drugref.open_question "
                      "WHERE gap_key = %s", (f"MOIETY:{m}",)).fetchone()[0]
    _rule(conn, subject_moiety_uuid=m, object_uuid=c, question_uuid=qu)
    w.member(conn, r, m, w.klass(conn, r, "PEClass", concept_type="PE"),
             relationship="has_PE")  # closes gap_unclassified_moiety
    questions.register_from_gaps(conn, r)  # must not abort
    assert conn.execute("SELECT is_current FROM drugref.open_question "
                        "WHERE question_uuid = %s", (qu,)).fetchall() == [(False,)]


def _question(conn, key):
    import uuid
    q = uuid.uuid4()
    r = w.run(conn)
    conn.execute(
        "INSERT INTO drugref.open_question (question_uuid, gap_kind, gap_key, "
        "question_text, first_derived_ingest, last_derived_ingest) "
        "VALUES (%s, 'unclassified_moiety', %s, 'q', %s, %s)", (q, key, r, r))
    return q


def test_the_same_verdict_answering_a_different_question_supersedes(world):
    """Idempotence compares EVERY written field, not only the verdict: a re-answer that
    cites another question (or another reviewer or reviewed_against) is new provenance, and a
    no-op would silently drop it."""
    conn, m, _k, c, _b = world
    q1, q2 = _question(conn, "TEST:q1"), _question(conn, "TEST:q2")
    old = _rule(conn, subject_moiety_uuid=m, object_uuid=c, question_uuid=q1)
    assert _rule(conn, subject_moiety_uuid=m, object_uuid=c, question_uuid=q1) == old
    new = _rule(conn, subject_moiety_uuid=m, object_uuid=c, question_uuid=q2)
    assert new != old
    assert conn.execute(
        "SELECT question_uuid FROM drugref.curated_property_ruling "
        "WHERE subject_uuid = %s AND superseded_by IS NULL", (m,)).fetchall() == [(q2,)]


def test_a_signed_ruling_publishes_and_verifies_with_its_generated_key(world):
    """The natural key is five columns, four of them GENERATED (subject_kind, ...): they
    must render in the manifest entry, and the release must verify intact."""
    import datetime as dt

    from drugref import keys, release_verification, signatures, signing
    conn, _m, k, c, _b = world
    at = dt.datetime(2026, 11, 7, tzinfo=dt.timezone.utc)
    kp = signing.generate_keypair()
    fp = signing.fingerprint(kp.public_key)
    keys.register(conn, public_key=kp.public_key, holder="drugref.org",
                  registered_by="an operator")
    rid = _rule(conn, subject_class_uuid=k, object_uuid=c)
    context, payload = signatures.payload_for(conn, "curated_property_ruling", rid,
                                              key_fingerprint=fp, signed_at=at)
    signatures.record(conn, target_kind="curated_property_ruling", target_id=rid,
                      payload_context=context, payload=payload, key_fingerprint=fp,
                      signature=signing.sign(kp.private_key, payload), signed_at=at)
    assert [v.verdict for v in signatures.verify_target(
        conn, "curated_property_ruling", rid)] == [signing.VALID]
    manifest_id = releases.publish(conn, release_tag="v-ruling", published_by="test",
                                   private_key=kp.private_key, key_fingerprint=fp)
    assert conn.execute(
        "SELECT natural_key FROM drugref.release_manifest_entry WHERE manifest_id = %s "
        "AND target_kind = 'curated_property_ruling' AND target_id = %s",
        (manifest_id, rid)).fetchone() == (f"class/{k}/CI_with/condition/{c}",)
    assert release_verification.verify_release(conn, "v-ruling").is_intact
