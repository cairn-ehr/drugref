# tests/test_accumulation.py
"""The PURE half of the accumulation model (Plan C, spec 10).

Threshold evaluation is a pure function of four integers and group firing is a pure
function of two role sets, so both get table-driven unit tests with no database. They
run everywhere, including without DRUGREF_TEST_DSN.

These functions live in drugref for the same reason ids.canonical_source does: the
rule they encode is stated in a COMMENT ON and would otherwise be re-implemented by
every consumer, each slightly differently. drugref does NOT evaluate on a consumer's
behalf -- spec 8 keeps the global tier stateless and free of patient data -- but it
can hand out the rule as code so that "count the contributors" means one thing.
"""
import pytest

from drugref import accumulation


# ---- threshold evaluation (spec 5.1) ----------------------------------------
#
# The three realistic encodings ARE the table:
#   (0, 2) "any two contributors"
#   (1, 2) "a major plus anything else"
#   (1, 1) "a major alone is worth saying"

@pytest.mark.parametrize("majors,total,t_major,t_total,expected", [
    # (0, 2) -- any two contributors, graded or not
    (0, 1, 0, 2, False),
    (0, 2, 0, 2, True),
    (2, 2, 0, 2, True),
    # (1, 2) -- a major plus anything else
    (0, 5, 1, 2, False),   # five minors is still not a major
    (1, 1, 1, 2, False),   # a major alone is not enough here
    (1, 2, 1, 2, True),
    # (1, 1) -- a major alone is worth saying
    (1, 1, 1, 1, True),
    (0, 1, 1, 1, False),
    # boundary: nothing on board
    (0, 0, 0, 1, False),
])
def test_fires_matches_the_threshold_table(majors, total, t_major, t_total, expected):
    assert accumulation.fires(majors, total, t_major, t_total) is expected


def test_fires_rejects_more_majors_than_contributors():
    """Every major IS a contributor, so majors > total is not a near-miss to be
    silently tolerated -- it means the caller counted two different populations, and a
    threshold answered from miscounted inputs is worse than an error."""
    with pytest.raises(ValueError):
        accumulation.fires(3, 2, 1, 2)


def test_fires_rejects_a_negative_count():
    with pytest.raises(ValueError):
        accumulation.fires(-1, 2, 1, 2)


# ---- group firing (spec 5.3) -------------------------------------------------


def test_a_group_fires_only_when_every_role_is_covered():
    required = {"NSAID", "RAAS blocker", "diuretic"}
    assert accumulation.group_fires(required, {"NSAID", "RAAS blocker", "diuretic"})
    assert not accumulation.group_fires(required, {"NSAID", "diuretic"})


def test_two_drugs_in_one_role_do_not_cover_a_second_role():
    """The whole reason groups exist beside accumulation: a COUNT would fire on three
    NSAIDs, which is a much weaker claim than the triple whammy. Roles are a SET, so
    covering one twice covers one."""
    assert not accumulation.group_fires({"NSAID", "diuretic"}, {"NSAID"})


def test_extra_roles_in_the_regimen_do_not_prevent_firing():
    """A patient on more than the group names still has the group."""
    assert accumulation.group_fires({"NSAID"}, {"NSAID", "statin"})


def test_a_group_with_no_required_roles_never_fires():
    """An empty required set is what a group whose every member has been retired looks
    like. `set() <= anything` is true, so the natural subset test would fire it on
    EVERY regimen -- including an empty one. Spec 5.3 says retiring the last member of
    a role removes the role; it does not say the group then applies to everybody."""
    assert not accumulation.group_fires(set(), {"NSAID"})
    assert not accumulation.group_fires(set(), set())
