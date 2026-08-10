# tests/test_signature_read_path.py
"""The read path's registry-level signature status (db/030 section 7, slice 5c.4).

REGISTRY-LEVEL ONLY, and pinned here rather than merely documented in a comment:
Postgres cannot verify an Ed25519 signature, so `signature_status` reports only what
SQL CAN know -- a signature exists, its key is registered, that key has (or has not)
been revoked -- and NEVER whether the mathematics checks out. `drugref verify` is the
only thing that does that; nothing here caches a verification result.

THE LEFT JOIN MUST STAY LEFT, and that is the single most important property this file
pins. Gating curated_ddi_pair/curated_condition_ruling on a valid signature would let a
key revocation silently withdraw contraindication advice from every downstream
consumer -- and FEWER ROWS IS THE HARM DIRECTION for a contraindication, which is Plan
B's central finding in this codebase. test_a_row_signed_by_a_compromised_key_is_still_
served pins that refusal directly. Every "unsigned row still appears" assertion below
is also, structurally, an anti-inner-join test: an INNER JOIN against
curated_signature_status would return zero rows for any curated row nobody has signed,
which is most of them today (assertion_signature ships empty).
"""
import datetime as dt

import pytest

from drugref import curation, keys, signatures, signing

SIGNED_AT = dt.datetime(2026, 8, 9, 4, 33, 52, tzinfo=dt.timezone.utc)
LATER = dt.datetime(2026, 12, 1, tzinfo=dt.timezone.utc)
# AN ABSOLUTE REGISTRATION INSTANT, EARLIER THAN BOTH OF THE ABOVE. `keys.register`
# defaults `status_from` to the DATABASE's `now()`, and a test that pairs a wall-clock
# registration with a FIXED signing literal is a dated time bomb: which side of the
# boundary the signature falls on depends on what day the suite runs. That is exactly
# what test_one_good_signature_outweighs_one_revoked_one was, and it failed OPEN -- see
# its docstring. Passing this explicitly makes both sides of every comparison absolute.
EARLY = dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc)


def _sign(conn, target_kind, target_id, *, holder="a curator", signed_at=SIGNED_AT,
          status_from=EARLY):
    """Register a fresh key and record one valid signature over one target row.

    Returns the fingerprint, so a caller that needs to revoke it afterwards has it.
    Mirrors tests/test_signatures_writer.py's `signed_rule` fixture, inlined here
    rather than imported across test modules -- this repo's established convention
    (conftest.py's own comment on `a_graded_rule`/`a_contradicted_pair` states it).

    `status_from` IS STATED, NEVER LEFT TO `now()`. `signature_backdated`'s two tests
    genuinely need wall-clock time (they measure `signed_at` against the DATABASE's
    `recorded_at`), but every OTHER test in this file measures `signed_at` against a
    KEY's `status_from`, and mixing a wall-clock registration with a fixed signing
    literal is how a mutation-killing test quietly stops killing anything on a
    particular calendar date. Both sides absolute, always.
    """
    _kp = signing.generate_keypair()
    private, public = _kp.private_key, _kp.public_key
    fingerprint = signing.fingerprint(public)
    keys.register(conn, public_key=public, holder=holder, registered_by="an operator",
                  status_from=status_from)
    context, payload = signatures.payload_for(
        conn, target_kind, target_id, key_fingerprint=fingerprint, signed_at=signed_at)
    signatures.record(
        conn, target_kind=target_kind, target_id=target_id, payload_context=context,
        payload=payload, key_fingerprint=fingerprint,
        signature=signing.sign(private, payload), signed_at=signed_at)
    return fingerprint


def _graded(conn, a_graded_rule):
    """Grade a_graded_rule's CI_MoA rule and return its curated_interaction_id."""
    return curation.record_interaction_judgement(
        conn, a_graded_rule["subject"], a_graded_rule["class"], "CI_MoA", True,
        severity="major", evidence_grade="established", reviewed_by="a curator",
        reviewed_against="2026.07.06")


def _a_second_graded_rule(conn, ingest_run_id, subject):
    """A second, independent CI_MoA rule on the SAME subject moiety -- same shape as
    conftest's a_graded_rule fixture, with its own class and partner so the resulting
    curated_interaction row is a genuinely different target_id.

    Needed by test_signing_one_rule_does_not_leak_to_a_sibling_rule: with only ONE
    curated_interaction row in a test's transaction, a read-path join that matched on
    target_kind alone (dropping the target_id predicate) would still happen to report
    the right status by coincidence -- the same shape of gap
    tests/test_signatures_writer.py's I1 finding closed for verify_target's own WHERE
    clause.
    """
    from drugref import ids
    from tests.test_curated_overlay import _a_class
    klass = _a_class(conn, ingest_run_id, code="N0000000099", name="Second MoA [MoA]")
    partner = ids.mint_moiety_uuid("TESTUNII03")
    conn.execute(
        "INSERT INTO drugref.substance_moiety "
        "(moiety_uuid, display_name, first_seen_ingest) "
        "VALUES (%s, 'partnerdrug2', %s) ON CONFLICT DO NOTHING",
        (partner, ingest_run_id))
    conn.execute(
        "INSERT INTO drugref.class_membership "
        "(moiety_uuid, class_uuid, relationship, ingest_run) "
        "VALUES (%s, %s, 'has_MoA', %s) ON CONFLICT DO NOTHING",
        (partner, klass, ingest_run_id))
    conn.execute(
        "INSERT INTO drugref.class_contraindication "
        "(subject_moiety_uuid, object_class_uuid, relationship, source, ingest_run) "
        "VALUES (%s, %s, 'CI_MoA', 'MED-RT', %s)", (subject, klass, ingest_run_id))
    target_id = curation.record_interaction_judgement(
        conn, subject, klass, "CI_MoA", True, severity="minor",
        evidence_grade="theoretical", reviewed_by="a curator",
        reviewed_against="2026.07.06")
    return {"class": klass, "curated_interaction_id": target_id}


# ---- CREATE OR REPLACE VIEW can only APPEND -- pin the column shape first ---------

def test_curated_ddi_pair_columns_are_unchanged_with_signature_status_last(conn):
    """`CREATE OR REPLACE VIEW` cannot reorder or rename an existing column -- only
    append one. This pins BOTH halves: every pre-existing column (db/029 section 3)
    survives in its EXISTING order and name, and `signature_status` is strictly LAST.
    A row-count comparison could not tell a reordered view from an untouched one; this
    can."""
    columns = [row[0] for row in conn.execute(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_schema = 'drugref' AND table_name = 'curated_ddi_pair' "
        "ORDER BY ordinal_position").fetchall()]
    assert columns == [
        "subject_moiety", "partner_moiety", "relationship", "via_class",
        "member_class", "is_direct", "severity", "mechanism", "management",
        "evidence_grade", "question_uuid", "curated_source", "reviewed_by",
        "reviewed_against", "reviewed_at", "upstream_release", "candidate_source",
        "signature_status"]


def test_curated_condition_ruling_columns_are_unchanged_with_signature_status_last(
        conn):
    columns = [row[0] for row in conn.execute(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_schema = 'drugref' AND table_name = 'curated_condition_ruling' "
        "ORDER BY ordinal_position").fetchall()]
    assert columns == [
        "subject_moiety", "object_condition", "ruling", "severity", "mechanism",
        "management", "evidence_grade", "question_uuid", "curated_source",
        "reviewed_by", "reviewed_against", "reviewed_at", "candidate_kind",
        "relationship", "candidate_source", "signature_status"]


# ---- curated_ddi_pair.signature_status ---------------------------------------------

def test_an_unsigned_curated_row_reads_unsigned(conn, a_graded_rule):
    """UNSIGNED IS AN ORDINARY STATE: signing is optional per row and the overlay
    ships empty. Also the anti-inner-join control for the load-bearing test below --
    if the read path's join were INNER, this row would not appear at all."""
    _graded(conn, a_graded_rule)
    assert conn.execute(
        "SELECT signature_status FROM drugref.curated_ddi_pair "
        "WHERE subject_moiety = %s", (a_graded_rule["subject"],)
    ).fetchall() == [("unsigned",)]


def test_a_signed_curated_row_reads_signed(conn, a_graded_rule):
    target_id = _graded(conn, a_graded_rule)
    _sign(conn, "curated_interaction", target_id)
    assert conn.execute(
        "SELECT signature_status FROM drugref.curated_ddi_pair "
        "WHERE subject_moiety = %s", (a_graded_rule["subject"],)
    ).fetchall() == [("signed",)]


def test_a_row_signed_by_a_compromised_key_is_still_served(conn, a_graded_rule):
    """SPEC 9's REFUSAL, pinned rather than documented. Gating the read views on a
    valid signature would let a key-management event silently withdraw
    contraindication advice from every downstream consumer -- and FEWER ROWS IS THE
    HARM DIRECTION for a contraindication, which is Plan B's central finding. The row
    stays; the label changes; the consumer decides."""
    target_id = _graded(conn, a_graded_rule)
    fingerprint = _sign(conn, "curated_interaction", target_id)
    keys.revoke(conn, key_fingerprint=fingerprint, status="compromised",
                revoked_by="an operator", status_from=LATER)
    conn.execute("SET CONSTRAINTS ALL IMMEDIATE")
    assert conn.execute(
        "SELECT signature_status FROM drugref.curated_ddi_pair "
        "WHERE subject_moiety = %s", (a_graded_rule["subject"],)
    ).fetchall() == [("signed_by_revoked_key",)]


def test_a_compromise_is_not_undone_in_the_READ_PATH_either(conn, a_graded_rule):
    """THE SQL HALF OF THE SAME FIX (tests/test_keys_writer.py holds the Python half).

    `curated_signature_status` resolved a key's status with `superseded_by IS NULL` --
    the LIVE row -- exactly as `keys.key_status` did, so one `keys revoke --status
    active` returned a compromised key's rows to `signed` in the served view too. The
    two halves must agree: a consumer reading `signature_status` off `curated_ddi_pair`
    and an operator running `drugref verify` cannot be told different things about the
    same key, or the cheaper of the two answers is the one that gets believed.

    THE ROW IS STILL SERVED, which is the property the whole file exists to protect --
    this asserts the LABEL is right, never that the row disappeared."""
    target_id = _graded(conn, a_graded_rule)
    fingerprint = _sign(conn, "curated_interaction", target_id)
    keys.revoke(conn, key_fingerprint=fingerprint, status="compromised",
                revoked_by="an operator", status_from=LATER)
    keys.revoke(conn, key_fingerprint=fingerprint, status="active",
                revoked_by="an attacker", status_from=LATER)
    conn.execute("SET CONSTRAINTS ALL IMMEDIATE")
    assert conn.execute(
        "SELECT signature_status FROM drugref.curated_ddi_pair "
        "WHERE subject_moiety = %s", (a_graded_rule["subject"],)
    ).fetchall() == [("signed_by_revoked_key",)]


def test_a_time_scoped_revocation_IS_still_reversible_in_the_read_path(conn,
                                                                       a_graded_rule):
    """THE ANTI-VACUITY CONTROL for the test above, in SQL. The permanence must key off
    `invalidates_all_signatures` and nothing else: a key rotated onto a new laptop, its
    signature falling AFTER the boundary (so it really does read
    `signed_by_revoked_key` first), returns to `signed` when the rotation is corrected.

    Without this, the fix above would pass just as well on a view that refused every
    reinstatement -- which would make a mistaken revocation permanent and is the
    opposite defect."""
    boundary = dt.datetime(2026, 6, 1, tzinfo=dt.timezone.utc)   # BEFORE SIGNED_AT
    target_id = _graded(conn, a_graded_rule)
    fingerprint = _sign(conn, "curated_interaction", target_id)
    keys.revoke(conn, key_fingerprint=fingerprint, status="rotated",
                revoked_by="an operator", status_from=boundary)
    conn.execute("SET CONSTRAINTS ALL IMMEDIATE")
    assert conn.execute(
        "SELECT signature_status FROM drugref.curated_ddi_pair "
        "WHERE subject_moiety = %s", (a_graded_rule["subject"],)
    ).fetchall() == [("signed_by_revoked_key",)]          # the boundary really bit

    # BACK TO DEFERRED BEFORE THE NEXT REVOKE. `revoke` INSERTs the new status row and
    # only then supersedes the old one, so it is momentarily two-live by design; with
    # the constraint left IMMEDIATE from the assertion above, the INSERT fires it before
    # supersede can run. Same reason _NoCommit.rollback() restores DEFERRED mode.
    conn.execute("SET CONSTRAINTS ALL DEFERRED")
    keys.revoke(conn, key_fingerprint=fingerprint, status="active",
                revoked_by="an operator", status_from=boundary)
    conn.execute("SET CONSTRAINTS ALL IMMEDIATE")
    assert conn.execute(
        "SELECT signature_status FROM drugref.curated_ddi_pair "
        "WHERE subject_moiety = %s", (a_graded_rule["subject"],)
    ).fetchall() == [("signed",)]


def test_one_good_signature_outweighs_one_revoked_one(conn, a_graded_rule):
    """`signed` means NOTHING IN THE REGISTRY OBJECTS -- so ONE unobjected signature
    is enough, regardless of how many other signatures on the same row are
    compromised.

    THIS IS THE ONLY TEST THAT KILLS REMOVAL OF db/030's `is_revocation` CLAUSE (the
    `NOT (t.is_revocation AND s.signed_at >= k.status_from)` term in
    `curated_signature_status`'s FILTER), and it was a DATED TIME BOMB THAT FAILED OPEN
    -- I1 of the final review, and the second instance of the standing rule in this very
    file. Curator B's key used to register with `status_from = now()` while signing at
    the fixed literal `LATER` (2026-12-01). Before that date, `signed_at >= status_from`
    holds and dropping the clause objects to B's signature, so the mutation is caught;
    ON AND AFTER 2026-12-01, `now()` overtakes `LATER`, the comparison flips, and the
    suite stays green with the clause gone -- silently, with nothing to notice.

    Both sides are now absolute: B registers at `EARLY` (2026-01-01) and signs at
    `LATER`, so `signed_at >= status_from` is true by construction on every run,
    forever. Moving the literal alone would not have fixed it -- one side would still
    have been the wall clock.
    """
    target_id = _graded(conn, a_graded_rule)
    compromised_fp = _sign(conn, "curated_interaction", target_id, holder="curator A")
    keys.revoke(conn, key_fingerprint=compromised_fp, status="compromised",
                revoked_by="an operator", status_from=LATER)
    conn.execute("SET CONSTRAINTS ALL IMMEDIATE")
    _sign(conn, "curated_interaction", target_id, holder="curator B", signed_at=LATER,
          status_from=EARLY)
    assert conn.execute(
        "SELECT signature_status FROM drugref.curated_ddi_pair "
        "WHERE subject_moiety = %s", (a_graded_rule["subject"],)
    ).fetchall() == [("signed",)]


def test_signing_one_rule_does_not_leak_to_a_sibling_rule(
        conn, a_graded_rule, ingest_run_id):
    """The join must match on target_id, not merely target_kind. See
    _a_second_graded_rule's docstring for why a single-row test cannot catch this."""
    signed_id = _graded(conn, a_graded_rule)
    _sign(conn, "curated_interaction", signed_id)
    second = _a_second_graded_rule(conn, ingest_run_id, a_graded_rule["subject"])
    rows = dict(conn.execute(
        "SELECT via_class, signature_status FROM drugref.curated_ddi_pair "
        "WHERE subject_moiety = %s", (a_graded_rule["subject"],)).fetchall())
    assert rows == {a_graded_rule["class"]: "signed", second["class"]: "unsigned"}


# ---- curated_condition_ruling.signature_status -------------------------------------
# ONE ROW PER CANDIDATE (db/029 section 3): a_contradicted_pair's ruling reaches TWO
# rows, one naming may_treat and one naming CI_with, both over the SAME
# curated_condition_id -- so both must carry the SAME signature_status.

def test_an_unsigned_condition_ruling_reads_unsigned_on_both_rows(
        conn, a_contradicted_pair):
    curation.record_condition_ruling(
        conn, a_contradicted_pair["moiety"], a_contradicted_pair["condition"],
        "context_dependent", severity="major", evidence_grade="established",
        reviewed_by="a curator", reviewed_against="2026.07.06")
    rows = conn.execute(
        "SELECT signature_status FROM drugref.curated_condition_ruling "
        "WHERE subject_moiety = %s", (a_contradicted_pair["moiety"],)).fetchall()
    assert rows == [("unsigned",), ("unsigned",)]


def test_a_signed_condition_ruling_reads_signed_on_both_rows(
        conn, a_contradicted_pair):
    """Also proves the join names target_kind = 'curated_condition' (not
    'curated_interaction', its sibling's literal) -- a copy-paste of the wrong literal
    would leave both rows reading 'unsigned' even after this signature is recorded."""
    condition_id = curation.record_condition_ruling(
        conn, a_contradicted_pair["moiety"], a_contradicted_pair["condition"],
        "context_dependent", severity="major", evidence_grade="established",
        reviewed_by="a curator", reviewed_against="2026.07.06")
    _sign(conn, "curated_condition", condition_id)
    rows = conn.execute(
        "SELECT signature_status FROM drugref.curated_condition_ruling "
        "WHERE subject_moiety = %s", (a_contradicted_pair["moiety"],)).fetchall()
    assert rows == [("signed",), ("signed",)]


# ---- signature_backdated ------------------------------------------------------------

def test_signature_backdated_is_empty_for_a_normal_signature(conn, a_graded_rule):
    """"Normal" means SIGNED NOW, not signed at a literal date -- and that is why
    this test's `signed_at` is `dt.datetime.now(dt.timezone.utc)` rather than the
    module's own `SIGNED_AT`. `signature_backdated` compares `signed_at` against
    `recorded_at`, which is the DATABASE's `now()` at the moment this test actually
    runs; a fixed calendar literal is "normal" only until wall-clock carries it more
    than a day behind whatever `recorded_at` turns out to be, and then it silently
    becomes the wrong fixture for the case it is supposed to cover. That is exactly
    what happened here: this test used `SIGNED_AT` (2026-08-09 04:33:52 UTC) until
    real time crossed 2026-08-10 04:33:52 UTC, at which point a signature that was
    never backdated started failing this assertion. Deriving `signed_at` from `now()`
    makes the test's premise -- "this signature is unremarkable" -- true by
    construction, on every run, rather than true only within a one-day calendar
    window. `SIGNED_AT` itself is UNCHANGED and still used elsewhere in this module,
    where what matters is a value's relationship to `LATER` (a revoked key's
    `status_from`), not its distance from today.
    """
    target_id = _graded(conn, a_graded_rule)
    _sign(conn, "curated_interaction", target_id,
          signed_at=dt.datetime.now(dt.timezone.utc))
    assert conn.execute(
        "SELECT count(*) FROM drugref.signature_backdated").fetchone() == (0,)


def test_signature_backdated_reports_a_signed_at_long_before_recorded_at(
        conn, a_graded_rule):
    """An OPERATOR SIGNAL, not a forgery report: signed_at is inside the signed
    payload and cannot be forged without the key, but a legitimate air-gapped signing
    flow also lands here -- see the view's own COMMENT for why this is deliberately
    not a gap kind.

    SIGNED 30 DAYS BEFORE NOW, not before a fixed calendar date -- the sibling test
    above's fix, applied here too, and for the identical reason: the module used to
    derive this from `SIGNED_AT` (`LONG_AGO = SIGNED_AT - timedelta(days=30)`), which
    anchored "backdated" to the same 2026-08-09 literal that drifted out from under
    the OTHER test. `signature_backdated`'s rule only cares that `signed_at` precedes
    `recorded_at` by more than a day, not by any particular calendar distance, so
    `now() - 30 days` sits on the correct side of that one-day threshold FOREVER,
    the same way `now()` alone does for the "not backdated" case -- the two tests are
    opposite sides of one boundary by construction, not by which calendar page
    happens to be showing when the suite runs.
    """
    target_id = _graded(conn, a_graded_rule)
    _sign(conn, "curated_interaction", target_id,
          signed_at=dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=30))
    assert conn.execute(
        "SELECT count(*) FROM drugref.signature_backdated").fetchone() == (1,)


# ---- db/030's SQL and signing.verdict must agree (final review, I9) -----------------
#
# UNAVOIDABLE DUPLICATION, PREVIOUSLY UNPINNED. `curated_signature_status`'s FILTER
# re-types three of `signing.verdict`'s five precedence steps in SQL -- the registered-key
# check, the blanket `invalidates_all_signatures` check and the time-scoped
# `is_revocation AND signed_at >= status_from` check. Postgres cannot call Python, so
# there is no way to make this ONE implementation; what there is a way to do is DRIVE
# BOTH OVER THE SAME INPUTS and assert they answer alike, which is what the table below
# does. Nothing did that before, so the two could have drifted in either direction
# without a single test noticing.
#
# BAD_SIGNATURE IS THE ONE STEP DELIBERATELY ABSENT: SQL has no cryptography, so
# `signature_status` collapses "the mathematics is wrong" into whatever the registry says
# about the key, and every case below therefore drives `signing.verdict` with
# `signature_ok=True`. That is the honest comparison -- db/030's own view comment says
# `signed` never means verified -- rather than a comparison rigged to agree.
#
# EVERY BOUNDARY IS AN ABSOLUTE LITERAL, both sides. See EARLY's comment above.

BOUNDARY = dt.datetime(2026, 6, 1, tzinfo=dt.timezone.utc)
BEFORE = BOUNDARY - dt.timedelta(days=1)
AT_OR_AFTER = BOUNDARY

_VERDICT_CASES = [
    # (status, signed_at, register_the_key)
    ("active", BEFORE, True),
    ("active", AT_OR_AFTER, True),
    ("rotated", BEFORE, True),
    ("rotated", AT_OR_AFTER, True),
    ("retired", BEFORE, True),
    ("retired", AT_OR_AFTER, True),
    ("compromised", BEFORE, True),
    ("compromised", AT_OR_AFTER, True),
    ("active", BEFORE, False),
]


def _sign_under_a_key_with_status(conn, target_id, *, status, signed_at, registered):
    """One signature over `target_id`, by a key in exactly `status` from `BOUNDARY`.

    `registered=False` records the signature under a fingerprint the registry has never
    heard of -- `signing.UNKNOWN_KEY`'s case, and the one the SQL reaches through its
    `k.key_fingerprint IS NOT NULL` term rather than through the status vocabulary.
    Recorded through `signatures.record`, which deliberately stores signatures it cannot
    vouch for (see its docstring), so this needs no floor-bending at all.
    """
    _kp = signing.generate_keypair()
    private, public = _kp.private_key, _kp.public_key
    fingerprint = signing.fingerprint(public)
    if registered:
        keys.register(conn, public_key=public, holder="a curator",
                      registered_by="an operator", status_from=BOUNDARY)
        if status != "active":
            keys.revoke(conn, key_fingerprint=fingerprint, status=status,
                        revoked_by="an operator", status_from=BOUNDARY)
            conn.execute("SET CONSTRAINTS ALL IMMEDIATE")
    context, payload = signatures.payload_for(
        conn, "curated_interaction", target_id, key_fingerprint=fingerprint,
        signed_at=signed_at)
    signatures.record(
        conn, target_kind="curated_interaction", target_id=target_id,
        payload_context=context, payload=payload, key_fingerprint=fingerprint,
        signature=signing.sign(private, payload), signed_at=signed_at)
    return fingerprint


@pytest.mark.parametrize("status,signed_at,registered", _VERDICT_CASES)
def test_the_sql_filter_and_signing_verdict_agree(conn, a_graded_rule, status,
                                                  signed_at, registered):
    """One signature, one key, one moment -- answered twice, in two languages, and the
    two answers must match.

    SQL says `signed` exactly when its FILTER counts the signature as UNOBJECTED; Python
    says `signing.VALID` exactly when nothing in the registry objects either. Any
    disagreement means db/030's re-typing and `signing.verdict` have drifted, and the
    consequence is a read view labelling a row `signed` that `drugref verify` calls
    revoked, or the reverse -- two of drugref's own surfaces contradicting each other
    about the same signature.
    """
    target_id = _graded(conn, a_graded_rule)
    fingerprint = _sign_under_a_key_with_status(
        conn, target_id, status=status, signed_at=signed_at, registered=registered)

    sql_status = conn.execute(
        "SELECT signature_status FROM drugref.curated_signature_status "
        "WHERE target_kind = 'curated_interaction' AND target_id = %s",
        (target_id,)).fetchone()[0]
    python_verdict = signing.verdict(
        keys.key_status(conn, fingerprint), signature_ok=True, signed_at=signed_at)

    assert (sql_status == "signed") == (python_verdict == signing.VALID), (
        f"db/030's SQL says {sql_status!r} while signing.verdict says "
        f"{python_verdict!r} for a {status} key signed at {signed_at}. The two "
        "implementations of spec 7.1's precedence have drifted.")


def test_the_verdict_agreement_table_reaches_both_answers():
    """The control the parametrisation above cannot give itself: if every case happened
    to land on the SAME side, an agreement test would pass under a SQL filter that always
    said `signed` beside a `verdict` that always said VALID. This asserts the table
    genuinely straddles the boundary -- at least one case each way.

    DRIVEN PURELY IN PYTHON, with no database at all: what is being checked is a property
    of the TABLE (does it cover both outcomes), not of either implementation, and
    round-tripping every case through Postgres here would only re-run the test above at
    twice the cost.
    """
    covered = {
        signing.verdict(
            signing.KeyStatus(status=status, is_revocation=status != "active",
                              invalidates_all_signatures=status == "compromised",
                              status_from=BOUNDARY) if registered else None,
            signature_ok=True, signed_at=signed_at)
        for status, signed_at, registered in _VERDICT_CASES}
    assert signing.VALID in covered, (
        "no case in _VERDICT_CASES is expected to verify -- the agreement test above "
        "would pass under an implementation that objects to everything")
    assert covered - {signing.VALID}, (
        "every case in _VERDICT_CASES lands on VALID -- the agreement test above would "
        "pass under an implementation that never objects to anything")


def test_status_reports_a_backdated_signature(conn, a_graded_rule, capsys):
    """REVIEW I7: `signature_backdated` HAD NO CALLER, so the one residual signal
    against a stolen key backdating past a TIME-SCOPED revocation was reachable only by
    an operator who wrote their own SQL. `curated_target_unresolved` -- the view this
    one was modelled on -- got a `drugref status` block from issue 76 for the same
    reason.

    Backdated by 30 days against the DATABASE's own `recorded_at`, which the writer
    stamps with `now()`. That makes this one of the two tests in this file that
    legitimately use wall-clock time on both sides -- the threshold is a one-day lag
    between two values the database itself produces, not a comparison against a fixed
    literal, so it cannot become a dated time bomb."""
    from drugref import cli

    target_id = _graded(conn, a_graded_rule)
    long_ago = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=30)
    fingerprint = _sign(conn, "curated_interaction", target_id, signed_at=long_ago,
                        status_from=EARLY)

    assert cli._handle_status(conn, None) == 0
    out = capsys.readouterr().out
    assert "backdated signatures: 1" in out
    assert fingerprint[:12] in out
    assert "key in the wrong hands" in out


def test_status_does_not_report_a_promptly_recorded_signature(conn, a_graded_rule,
                                                              capsys):
    """THE ANTI-VACUITY CONTROL. Without it the test above would pass just as well on a
    block that reported EVERY signature as backdated, which would train an operator to
    ignore the one line that matters. `drugref sign` records within seconds of signing,
    so the ordinary case must be silent."""
    from drugref import cli

    target_id = _graded(conn, a_graded_rule)
    _sign(conn, "curated_interaction", target_id,
          signed_at=dt.datetime.now(dt.timezone.utc), status_from=EARLY)

    assert cli._handle_status(conn, None) == 0
    assert "backdated signatures: none" in capsys.readouterr().out
