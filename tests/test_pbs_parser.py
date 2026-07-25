"""Pure tests for the PBS parser: no database, no network.

Every expectation here is drawn from the real 2026-07 release (spec 5.3), not
from the PBS data dictionary and not from intuition. Where a case looks odd, it
is odd because the upstream data is.
"""
from drugref.ingest import pbs


def test_splits_on_with():
    """' with ' is PBS's primary combination separator: 208 distinct names."""
    assert pbs.split_components("Abacavir with lamivudine") == ["abacavir", "lamivudine"]


def test_splits_on_and():
    """' and ' is the second: 88 distinct names."""
    assert pbs.split_components("Abiraterone and methylprednisolone") == [
        "abiraterone", "methylprednisolone"]


def test_does_not_split_on_plus():
    """' + ' appears in ZERO of the 1,086 distinct names upstream. A plus sign is
    therefore part of a name, never a separator, and splitting on it would shred
    real names for no gain."""
    assert pbs.split_components("Vitamin B+C complex") == ["vitamin b+c complex"]


def test_splits_multi_component_chains():
    """Real names chain commas and ' and ': 'Allantoin with sulfur, phenol, coal
    tar solution and menthol'."""
    assert pbs.split_components(
        "Allantoin with sulfur, phenol, coal tar solution and menthol") == [
        "allantoin", "sulfur", "phenol", "coal tar solution", "menthol"]


def test_strips_parenthetical_annotations():
    """'Acetic Acid (33 per cent)' must match the INN 'acetic acid'."""
    assert pbs.split_components("Acetic Acid (33 per cent)") == ["acetic acid"]
    assert pbs.split_components("Acetone (use as additive only)") == ["acetone"]


def test_folds_case():
    """PBS is Title-case; INN claims are lower-case."""
    assert pbs.split_components("Rifaximin") == ["rifaximin"]


def test_strip_salt_removes_a_trailing_salt_token():
    suffixes = pbs.load_salt_suffixes(pbs.SALT_SUFFIX_PATH)
    assert pbs.strip_salt("alfuzosin hydrochloride", suffixes) == "alfuzosin"
    assert pbs.strip_salt("metoprolol succinate", suffixes) == "metoprolol"


def test_strip_salt_never_strips_acid():
    """THE TRAP. 'acid' is the last word of real INNs -- alendronic acid, folic
    acid, folinic acid. Stripping it destroys correct matches, so it is not on
    the list and this test pins that."""
    suffixes = pbs.load_salt_suffixes(pbs.SALT_SUFFIX_PATH)
    assert "acid" not in suffixes
    assert pbs.strip_salt("alendronic acid", suffixes) is None
    assert pbs.strip_salt("folic acid", suffixes) is None


def test_strip_salt_returns_none_when_nothing_to_strip():
    """None means 'no fallback to try', distinct from a stripped empty string."""
    suffixes = pbs.load_salt_suffixes(pbs.SALT_SUFFIX_PATH)
    assert pbs.strip_salt("rifaximin", suffixes) is None


def test_strip_salt_never_strips_the_whole_name():
    """'Docusate sodium' strips fine, but a name that IS only a salt token would
    otherwise strip to nothing and then match everything."""
    suffixes = pbs.load_salt_suffixes(pbs.SALT_SUFFIX_PATH)
    assert pbs.strip_salt("sodium", suffixes) is None


def test_dimethyl_fumarate_is_a_regression_case():
    """'Dimethyl fumarate' and 'Diroximel fumarate' are INNs IN THEIR OWN RIGHT,
    even though 'fumarate' is a genuine salt token elsewhere ('Ferrous
    fumarate'). This is why the caller must try the UNSTRIPPED name FIRST and
    only fall back to the stripped one -- strip_salt itself is deliberately
    dumb, so the ordering is the safeguard (spec 5.3)."""
    suffixes = pbs.load_salt_suffixes(pbs.SALT_SUFFIX_PATH)
    assert pbs.strip_salt("dimethyl fumarate", suffixes) == "dimethyl"
