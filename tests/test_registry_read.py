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

import pytest

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


@pytest.fixture
def an_uningested_registry(conn):
    """A registry with nothing in it -- ESTABLISHED, not inherited from the run order.

    ⇒ WHY THIS EXISTS. The test below asserts a GLOBAL precondition (the registry holds
    no moieties) that it did not create. Half this suite's orchestrator tests commit --
    `test_cli.py::test_ingest_unii_end_to_end` registers real moieties -- so the only
    reason the assertion held was that twenty later modules happen to TRUNCATE in an
    autouse fixture and this file happens to sort after several of them. Reproduce the
    accident with `uv run pytest tests/test_cli.py tests/test_registry_read.py`, or with
    `uv run pytest --lf` against a cache that hoists test_cli.py: the test fails, and it
    fails for a reason that has nothing to do with what it is checking.

    **THE TRUNCATE IS NOT COMMITTED, which is what makes it safe here.** TRUNCATE is
    transactional in PostgreSQL, and the `conn` fixture rolls back after every test, so
    every committed row this suite has accumulated is still there for the next module.
    That is the difference between this fixture and the autouse ones in test_cli.py and
    test_ingest_run.py, which commit because the code THEY exercise commits.

    It has to be TRUNCATE rather than DELETE: the append-only floor's row-level triggers
    refuse a DELETE outright, and not covering TRUNCATE is precisely the documented
    bypass (see ROADMAP § "Floor hardening").
    """
    conn.execute("TRUNCATE drugref.identity_claim, drugref.substance_moiety, "
                 "drugref.ingest_run RESTART IDENTITY CASCADE")
    return conn


def test_registry_is_empty_on_a_migrated_but_uningested_database(an_uningested_registry):
    """The fact that stops #120's banner blaming the operator's typing.

    A migrated database with no ingest holds no moieties, so EVERY uuid is unknown and
    none of the banner's three causes -- a class_uuid, a uuid from another node, a
    transposed digit -- applies to any of them. Without this read the command asserted a
    cause it had not confirmed, which is the defect issue 122 is about, in the message
    issue 120 added.
    """
    assert registry_read.registry_is_empty(an_uningested_registry) is True


def test_a_registry_with_one_moiety_is_not_empty(conn, ingest_run_id):
    """The acceptance half: one row is enough, and the answer must flip.

    A function that returned True unconditionally would satisfy the test above, and the
    banner would then tell an operator with a fully loaded registry that drugref holds
    nothing -- the same under-informing failure, pointed the other way.
    """
    _a_moiety(conn, ingest_run_id, "TESTUNIIR1", "a registered drug")
    assert registry_read.registry_is_empty(conn) is False


def test_known_moieties_collapses_a_repeated_identifier(conn, ingest_run_id):
    """The duplicate-collapsing claim in the docstring, which nothing exercised.

    The CLI dedupes before calling, so this property is the function's own rather than
    something the caller relies on -- and a rewrite returning a list would break it
    silently, double-counting the self-pair form.
    """
    known = _a_moiety(conn, ingest_run_id, "TESTUNIIR2", "a registered drug")
    assert registry_read.known_moieties(conn, known, known) == {known}


def test_the_moiety_table_constant_names_the_relation_the_reads_use(conn):
    """One home per relation name (issue 122): the guard probes what the SELECT reads.

    A guard carrying its own copy of the name would survive a rename and then report a
    healthy database's spine permanently absent. `to_regclass` is what the guard itself
    uses, so this is the same question asked the same way.
    """
    assert conn.execute("SELECT to_regclass(%s)",
                        (registry_read.MOIETY_TABLE,)).fetchone()[0] is not None
