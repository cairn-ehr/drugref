"""Does this uuid name a moiety at all? (issue 120). DB-gated.

WHY A READ OF ITS OWN, AND WHY IT IS NOT IN `curated_read.py`. `drugref interactions`
printed the same answer -- "no curated grade" -- for a drug drugref knows and holds no
grade for, and for a uuid naming NOTHING IN THE REGISTRY. The pair form was worse: it
made an affirmative claim, "drugref holds no curated grade for this pair in either
direction", about a referent that might not exist.

THE VIEW CANNOT CLOSE THIS, which is why the answer had to come from somewhere else.
`curated_ddi_pair_effective`'s population is GRADES, not drugs, so an empty result is
genuinely ambiguous there -- `effective_grades_for`'s own docstring says so and defers
to "a caller needing that distinction asks `substance_moiety`". True of the view, and no
answer to the objection: a user who has mistyped a uuid does not know they need the
distinction, which is exactly what makes it silent.

REACHABLE WITHOUT ANY TYPO AT ALL: `--with` is documented as "a second moiety_uuid", and
a `class_uuid` parses identically -- both are UUIDv5 identities minted by this project.
So does a uuid from an older canonicalisation, or from another node.
"""
import uuid

from drugref import registry_read
from tests.test_class_subject_read_path import _a_moiety

# A UUIDv5 OF THE RIGHT SHAPE THAT NAMES NOTHING -- literal, not random, so a failure
# names the same value every run. This is what a transposed digit produces: something
# argparse's `type=uuid.UUID` accepts without complaint, because it is a well-formed
# identifier for a drug that does not exist.
_NOT_IN_THE_REGISTRY = uuid.UUID("00000000-0000-5000-8000-000000000000")


def test_it_returns_the_uuid_of_a_moiety_that_exists(conn, ingest_run_id):
    """The affirmative half: a registered moiety comes back."""
    known = _a_moiety(conn, ingest_run_id, "REGREADUNII1", "a registered drug")
    assert registry_read.known_moieties(conn, known) == {known}


def test_it_omits_a_uuid_that_names_nothing(conn):
    """The whole reason the module exists. An absent identity must come back absent."""
    assert registry_read.known_moieties(conn, _NOT_IN_THE_REGISTRY) == set()


def test_it_separates_the_known_from_the_unknown_in_one_call(conn, ingest_run_id):
    """THE SHAPE THE PAIR FORM NEEDS: one round trip, both verdicts.

    `drugref interactions X --with Y` must be able to say WHICH of the two it has never
    heard of -- telling an operator "one of these is unknown" sends them to check both.
    """
    known = _a_moiety(conn, ingest_run_id, "REGREADUNII2", "a registered drug")
    assert registry_read.known_moieties(
        conn, known, _NOT_IN_THE_REGISTRY) == {known}


def test_asking_about_nothing_returns_nothing(conn):
    """The empty call, which must not become `= ANY('{}')` returning every moiety.

    A CONTROL, and not a hypothetical one: the natural SQL for "any of these" degrades
    differently per driver on an empty list, and a reader that answered "everything is
    known" would make the guard above it silently affirmative -- the exact failure
    direction issue 120 is about.
    """
    assert registry_read.known_moieties(conn) == set()
