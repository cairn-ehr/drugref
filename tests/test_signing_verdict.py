# tests/test_signing_verdict.py
"""The verdict rule (spec 7.1). PURE -- no database.

ONE TEST PER BOUNDARY, per the standing rule slice 5c.1's PR review produced. The
precedence is the part that is easy to get subtly wrong, and every ordering mistake
produces a plausible-looking verdict rather than an error.
"""
import datetime as dt

from drugref import signing

EARLY = dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc)
REVOKED_AT = dt.datetime(2026, 6, 1, tzinfo=dt.timezone.utc)
LATE = dt.datetime(2026, 9, 1, tzinfo=dt.timezone.utc)

ACTIVE = signing.KeyStatus("active", is_revocation=False,
                           invalidates_all_signatures=False, status_from=EARLY)
ROTATED = signing.KeyStatus("rotated", is_revocation=True,
                            invalidates_all_signatures=False, status_from=REVOKED_AT)
COMPROMISED = signing.KeyStatus("compromised", is_revocation=True,
                                invalidates_all_signatures=True,
                                status_from=REVOKED_AT)


def test_a_good_signature_by_an_active_key_is_valid():
    assert signing.verdict(ACTIVE, signature_ok=True, signed_at=LATE) == signing.VALID


def test_an_active_key_does_not_expire_its_own_signatures():
    """THE REASON `is_revocation` EXISTS, and the test that kills its removal.

    An active key's status_from is its REGISTRATION time, and every signature it makes
    is necessarily after that. So a rule that expired any signature at or after
    status_from would expire EVERY signature ever made -- the layer would report
    key_expired universally, and nobody would notice until a consumer asked why nothing
    was ever valid.

    The alternative to the column is a Python-side `status == 'active'` test, which puts
    a member of db/030's vocabulary in a second place. Four rounds of this project have
    paid for that mistake already.
    """
    assert signing.verdict(ACTIVE, signature_ok=True,
                           signed_at=LATE) != signing.KEY_EXPIRED


def test_an_unknown_key_is_not_reported_as_a_bad_signature():
    """You cannot check the mathematics without the public key, so 'unknown' outranks
    'bad'. Conflating them reports a routine registry gap -- a key nobody has registered
    yet -- as an attack, which is the wrong alarm to raise at 3am."""
    assert signing.verdict(None, signature_ok=False,
                           signed_at=LATE) == signing.UNKNOWN_KEY
    assert signing.verdict(None, signature_ok=True,
                           signed_at=LATE) == signing.UNKNOWN_KEY


def test_a_bad_signature_outranks_a_revoked_key():
    """A forged signature under a revoked key is a forgery first. Reporting it as
    'revoked' would file an attack as a key-management event."""
    assert signing.verdict(COMPROMISED, signature_ok=False,
                           signed_at=EARLY) == signing.BAD_SIGNATURE


def test_a_compromised_key_invalidates_a_signature_made_long_before_the_revocation():
    """BLANKET, and that is the whole content of invalidates_all_signatures: after a
    compromise you cannot tell which signatures were the curator's and which the
    attacker's, so signed_at proves nothing."""
    assert signing.verdict(COMPROMISED, signature_ok=True,
                           signed_at=EARLY) == signing.KEY_REVOKED_COMPROMISED


def test_a_rotated_key_leaves_an_earlier_signature_valid():
    """TIME-SCOPED. A curator changing laptop must not unsign years of sound work; that
    is the case blanket-only revocation gets wrong, and it is the common one."""
    assert signing.verdict(ROTATED, signature_ok=True,
                           signed_at=EARLY) == signing.VALID


def test_a_rotated_key_expires_a_signature_made_after_the_rotation():
    assert signing.verdict(ROTATED, signature_ok=True,
                           signed_at=LATE) == signing.KEY_EXPIRED


def test_the_revocation_boundary_is_inclusive():
    """A signature made AT the revocation instant is expired, not valid. Either choice
    is defensible; the point is that one of them is written down, because an unstated
    boundary is where two implementations of this rule diverge."""
    assert signing.verdict(ROTATED, signature_ok=True,
                           signed_at=REVOKED_AT) == signing.KEY_EXPIRED


def test_no_signature_is_not_produced_by_the_rule_itself():
    """NO_SIGNATURE is the CALLER's verdict -- there is no signature to pass in. It
    lives in this module so all six spellings have one home, not because verdict()
    returns it."""
    assert signing.NO_SIGNATURE == "no_signature"


def test_every_verdict_constant_is_distinct():
    values = [signing.NO_SIGNATURE, signing.UNKNOWN_KEY, signing.BAD_SIGNATURE,
              signing.KEY_REVOKED_COMPROMISED, signing.KEY_EXPIRED, signing.VALID]
    assert len(set(values)) == 6


def test_verdict_precedence_matches_verdicts_actual_behaviour():
    """`signing.VERDICT_PRECEDENCE` (added for review round 2's C4, so a caller
    combining several `assertion_signature` rows -- the release verifier folding
    several signatures over one manifest into one answer -- has a single ranking
    rather than a second hand-typed copy of this file's own order) is a RESTATEMENT of
    `verdict`'s if-chain, co-located but not DERIVED from it. Nothing pinned the two
    together before this test: round 3's review reversed the tuple and 177 tests
    stayed green.

    Each line below builds inputs that satisfy TWO of `verdict`'s conditions AT ONCE,
    so which verdict comes back is the precedence in ACTION, not a second copy of the
    constant read back at itself:

    - `key_status=None` always wins over whatever `signature_ok` would otherwise say
      (UNKNOWN_KEY beats BAD_SIGNATURE -- a signature cannot even be checked without
      the public key).
    - A signature that is actually forged (`signature_ok=False`) on a COMPROMISED key
      still reports the forgery, not the revocation (BAD_SIGNATURE beats
      KEY_REVOKED_COMPROMISED).
    - `compromised` is `is_revocation=True` as well as `invalidates_all_signatures=True`
      in db/030's own seed data, so ONE call to a compromised key with a signature made
      after its own `status_from` satisfies both the blanket-revocation condition and
      the time-scoped-expiry condition simultaneously; the blanket one wins
      (KEY_REVOKED_COMPROMISED beats KEY_EXPIRED).
    - A rotated key's signature made after `status_from` is EXPIRED, never VALID
      (KEY_EXPIRED beats VALID -- the fallback, reached only when nothing else fired).

    Asserting the observed sequence equals `list(VERDICT_PRECEDENCE)` is what makes a
    reversed (or merely reordered) tuple fail this test, rather than one that happens
    to still contain the same five members.
    """
    observed = [
        signing.verdict(None, signature_ok=False, signed_at=LATE),
        signing.verdict(COMPROMISED, signature_ok=False, signed_at=EARLY),
        signing.verdict(COMPROMISED, signature_ok=True, signed_at=LATE),
        signing.verdict(ROTATED, signature_ok=True, signed_at=LATE),
        signing.verdict(ACTIVE, signature_ok=True, signed_at=LATE),
    ]
    assert observed == [signing.UNKNOWN_KEY, signing.BAD_SIGNATURE,
                        signing.KEY_REVOKED_COMPROMISED, signing.KEY_EXPIRED,
                        signing.VALID]
    assert observed == list(signing.VERDICT_PRECEDENCE)
