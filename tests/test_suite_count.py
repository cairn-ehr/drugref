# tests/test_suite_count.py
"""Issue 146, as a gate rather than as a paragraph.

⇒ WHAT THIS REPLACES. `docs/PROJECT-NOTES.md` § "How to run / test" carries a comment
that calls itself **THE ONE HOME FOR THIS NUMBER** -- the count of collected Python
tests -- and that comment has now drifted from the real suite **nine** times. It has
been rewritten three times specifically to prevent that: once to name the failure mode,
once to add "CHECK THE COUNT AT THE START OF A SESSION", once to record that a commit
message is not a home. It drifted again after each rewrite, twice in the same place,
and the ninth occurrence landed on the very branch whose diff added the sentence saying
a commit message is not a home.

**A gate that is prose is a gate that does not fire** (issues 74, 66, 76). This file is
the gate: it reads the number out of PROJECT-NOTES and compares it with what pytest
actually collected, so the stale line fails the suite that made it stale instead of
being noticed a round later by a human re-reading a comment.

**IT DOES NOT RESTATE THE NUMBER**, which is the property issue 146 asks for out loud.
The numerals in this file are fixtures for the pure helpers -- 3, 4, 7, 12, 40 -- chosen
to look nothing like a suite size. PROJECT-NOTES stays the single home.

**WHY THE COUNT IS TAKEN IN-PROCESS.** A `--collect-only` subprocess would measure the
same thing, and issue 146 rejected it as slow and fragile. `tests/conftest.py` records
the total during collection instead (see its `pytest_collection_finish`), which costs
nothing and cannot disagree with the run it is part of.

⇒ WHAT THE FIRST DRAFT OF THIS FILE GOT WRONG, because it is the reason three of the
tests below exist. Its "negative control" built the options it tested FROM
`COLLECTION_NARROWING_OPTIONS`, so the comparison in `collection_narrowing` reduced to
`neutral != neutral` and could never fire. A ledger with a wrong VALUE -- not a wrong
name, which was checked -- turned every real drift into a skip while all twenty-two
sibling tests stayed green. That is the issues 74/66/76 shape, rebuilt inside the file
written to prevent it. A control derived from its subject is not a control, so the
value check below reads the installed pytest instead.
"""
import sys

import pytest

from tests.suite_count import (COLLECTED_TESTS, COLLECTION_NARROWING_OPTIONS, NOTES,
                               SuiteCountUnreadable, UnknownPytestOptions,
                               collection_narrowing, stated_suite_count,
                               suite_count_verdict)

pytest_plugins = ["pytester"]

#: Two invented absolute paths for the pure-function tests. Absolute because
#: `collection_narrowing` now normalises against a base directory, and inventing them
#: keeps these tests independent of where the checkout happens to live.
_ROOT = "/repo"
_TESTS = "/repo/tests"

#: Command-line spellings of each ledgered dest. Used ONLY to notice that this
#: invocation typed one of them, so the value check below does not mistake a
#: deliberately narrowed run for a pytest whose defaults have changed. Pinned against
#: the ledger inside that test, so a new dest cannot arrive without its flags.
_LEDGER_FLAGS = {
    "ignore": ("--ignore",),
    "ignore_glob": ("--ignore-glob",),
    "lf": ("--lf", "--last-failed"),
}


def _neutral_options(**overrides):
    """Every narrowing option at its not-in-use value, with any of them overridden.

    ⇒ THIS IS A DRIVER, NOT A CONTROL, and the distinction is the whole lesson of this
    file. It is built FROM the ledger, so it can drive `collection_narrowing` through
    each branch without a live pytest -- but for that same reason an assertion that it
    produces no reasons is a tautology. The claim it cannot make (that these values are
    what pytest actually defaults to) is made by
    `test_the_ledgered_neutrals_are_the_installed_pytests_own_defaults`, which reads
    `config.option` instead.
    """
    return dict(COLLECTION_NARROWING_OPTIONS) | overrides


# --- the gate itself -------------------------------------------------------------

def test_the_stated_suite_count_matches_what_this_run_collected(request):
    """THE POINT OF THE FILE. Nine drifts, none of which a test could have missed.

    The parse happens BEFORE the partial-run skip below, deliberately: a PROJECT-NOTES
    that no longer states the number at all is a failure in every mode, including the
    single-file runs a developer does during TDD. Only the COMPARISON is conditional.
    """
    stated = stated_suite_count(NOTES.read_text())
    assert COLLECTED_TESTS in request.config.stash, (
        "tests/conftest.py's pytest_collection_finish hook did not run, so nothing "
        "counted this run's collection. That hook IS this gate; without it the test "
        "below would pass vacuously.")
    verdict, message = suite_count_verdict(
        stated,
        request.config.stash[COLLECTED_TESTS],
        collection_narrowing(
            request.config.option.file_or_dir,
            # testpaths are relative to the ROOTDIR; path arguments are relative to the
            # INVOCATION directory. The two differ whenever pytest is started from a
            # subdirectory, so both bases are handed over rather than one guessed.
            [request.config.rootpath / path
             for path in request.config.getini("testpaths")],
            vars(request.config.option),
            request.config.invocation_params.dir))
    if verdict == "partial":
        # A single-file or --lf run collected a subset on purpose. Skipping is right
        # here and is not a hole: CI runs the whole suite with no path arguments (see
        # .github/workflows/ci.yml), and its SECOND pytest step fails on any skip,
        # which pins this branch shut there.
        pytest.skip(message)
    assert verdict == "ok", message


def test_the_gate_fires_on_every_spelling_of_a_whole_suite_run():
    """The negative control for the skip above -- the shape of issues 74/66/76.

    A narrowing-detector that is too eager turns this whole file into a permanent
    skip, which is exactly the vacuous green it exists to prevent, and nothing else
    would ever say so. These are the invocations that collect the whole suite and must
    therefore be compared, not skipped: a bare `uv run pytest` (no path arguments;
    testpaths supplies `tests`), CI's `uv run pytest -q -m "not livepage"`, and the
    four spellings of the testpaths directory itself.

    `-m` is NOT narrowing, and that is the subtle half: it DESELECTS, and conftest
    adds deselected items back, so the count is the same either way.

    NOTE what this test can and cannot say. The PATH assertions are real: nothing here
    is derived from the code under test. The two `_neutral_options()` calls are not --
    see that helper's docstring, and the value check further down.
    """
    assert collection_narrowing([], [_TESTS], _neutral_options(), _ROOT) == []
    assert collection_narrowing(
        [], [_TESTS], _neutral_options(markexpr="not livepage"), _ROOT) == []
    # Four spellings of one directory. `./tests` and the absolute path an IDE's "run
    # all tests" button emits were both misreported as narrowing by the first draft.
    for spelling in ["tests", "tests/", "./tests", _TESTS]:
        assert collection_narrowing([spelling], [_TESTS], _neutral_options(),
                                    _ROOT) == [], spelling


def test_the_ledgered_neutrals_are_the_installed_pytests_own_defaults(request):
    """THE VALUE HALF OF THE LEDGER CHECK, and the one the first draft did not have.

    `test_every_ledgered_option_is_a_real_pytest_dest` pins the NAMES against live
    pytest. Nothing pinned the VALUES, and `_neutral_options` could not: it is built
    from the ledger. So a pytest release that changed `--ignore`'s default from `None`
    to `[]` -- an entirely ordinary argparse modernisation -- would make every bare run
    report narrowing, and every real drift would come back as a skip instead of a
    failure, with the whole file still green.

    This reads `config.option` instead, which is the installed pytest's own answer.

    ⇒ IT ALSO CLOSES THE `addopts` HOLE. A single `addopts = "--ignore=..."` line in
    pyproject.toml would disable the gate the same way and never touch this file; it
    lands here as a loud failure, because `config.option` carries what addopts set.
    The argv guard below deliberately does NOT look at addopts, only at what the
    developer typed.
    """
    assert set(_LEDGER_FLAGS) == set(COLLECTION_NARROWING_OPTIONS), (
        "_LEDGER_FLAGS must name every ledgered dest and no other, or the guard below "
        "silently stops covering one of them")
    live = vars(request.config.option)
    typed = {dest for dest, flags in _LEDGER_FLAGS.items()
             if any(arg.split("=")[0] in flags for arg in sys.argv[1:])}
    checkable = {dest: neutral for dest, neutral in COLLECTION_NARROWING_OPTIONS.items()
                 if dest not in typed and dest in live}
    if not checkable:
        pytest.skip(f"this invocation typed {sorted(typed)}, so none of the ledgered "
                    f"options is at its default and there is nothing to compare")
    assert {dest: live[dest] for dest in checkable} == checkable, (
        "the installed pytest does not park these values on `config.option` when the "
        "flag is absent. COLLECTION_NARROWING_OPTIONS records what 'not in use' looks "
        "like; if pytest changed a default (or pyproject's addopts now sets one), the "
        "gate reports every full run as narrowed and skips instead of failing.")


# --- reading the one home --------------------------------------------------------

def test_project_notes_states_the_count_exactly_once(request):
    """The file is where this module says it is, and states the count once.

    NAMED FOR WHAT IT CHECKS. It was called `..._and_nowhere_else`, which it never
    tested -- and could not: PROJECT-NOTES' round narrative legitimately records past
    totals as `2538 -> 2561` pairs. What the gate needs is that exactly one line
    carries the MARKER, and that is what `stated_suite_count` refuses to guess about.
    """
    assert NOTES.exists(), f"{NOTES} is missing; the gate reads the count from there"
    assert stated_suite_count(NOTES.read_text()) > 0


@pytest.mark.parametrize("line, expected", [
    ("# 7 tests (THE ONE HOME FOR THIS NUMBER -- it said 3 while the suite was at 4,", 7),
    ("#   12 tests   (THE ONE HOME FOR THIS NUMBER, spaced differently", 12),
])
def test_a_stated_count_is_read_off_the_marked_line(line, expected):
    """Surrounding prose is full of other numerals; only the marked line counts."""
    text = f"# 40 tests were added in 2026\n{line}\n# and 40 more numbers below\n"
    assert stated_suite_count(text) == expected


@pytest.mark.parametrize("text, why", [
    ("nothing marked here at all\n", "no marked line"),
    ("# 7 tests (THE ONE HOME FOR THIS NUMBER\n# 12 tests (THE ONE HOME FOR THIS NUMBER\n",
     "two marked lines"),
    ("# tests (THE ONE HOME FOR THIS NUMBER -- no numeral\n", "marked but no numeral"),
])
def test_a_file_that_does_not_state_the_count_exactly_once_is_a_loud_failure(text, why):
    """The trap `test_spl_tools_smoke.py` fell into: a scan that matches nothing and passes.

    If the marked line is reworded, deleted or duplicated, this raises instead of
    quietly measuring nothing -- and the message says which of the three happened.
    """
    with pytest.raises(SuiteCountUnreadable) as caught:
        stated_suite_count(text)
    assert "found" in str(caught.value), why


# --- what counts as a partial run ------------------------------------------------

@pytest.mark.parametrize("options", [
    {"ignore": ["tests/test_db.py"]},
    {"ignore_glob": ["*_writer.py"]},
    {"lf": True},
])
def test_an_option_that_shrinks_collection_is_reported_as_narrowing(options):
    """Every name in the ledger, driven one at a time."""
    reasons = collection_narrowing([], [_TESTS], _neutral_options(**options), _ROOT)
    assert len(reasons) == 1, reasons
    assert next(iter(options)) in reasons[0]


@pytest.mark.parametrize("argument", ["tests/test_db.py", "tests/test_db.py::test_x", "."])
def test_an_argument_that_is_not_the_whole_of_testpaths_is_reported_as_narrowing(argument):
    """A file, a node id, and the rootdir itself.

    `.` belongs here rather than with the whole-suite spellings above: it names the
    ROOTDIR, not testpaths, so it can collect test files from outside `tests/` that the
    documented total does not include.
    """
    reasons = collection_narrowing([argument], [_TESTS], _neutral_options(), _ROOT)
    assert len(reasons) == 1, reasons
    assert argument in reasons[0]


@pytest.mark.parametrize("options", [
    {"keyword": "writer"},
    {"markexpr": "not livepage"},
    {"deselect": ["tests/test_db.py::test_x"]},
    {"stepwise": True},
])
def test_deselection_is_not_narrowing_because_conftest_adds_it_back(options):
    """-k, -m, --deselect and --sw all route through `pytest_deselected`, which is counted.

    Calling any of them narrowing would make the gate skip -- for `-m`, on CI's own
    command line. `stepwise` was in the ledger until this test was written: it looks
    like a narrowing flag and is not one, because `_pytest/stepwise.py` deselects the
    already-passed prefix rather than declining to collect it.

    This test has teeth against exactly one thing, and it is the thing that matters:
    ADDING one of these names to `COLLECTION_NARROWING_OPTIONS` fails it. The override
    values themselves are inert, because `collection_narrowing` only reads ledgered
    names.
    """
    assert collection_narrowing([], [_TESTS], _neutral_options(**options), _ROOT) == []


def test_a_renamed_pytest_option_is_a_loud_failure_not_a_silent_pass():
    """The ledger is checked against the options it is handed, in both directions.

    `options.get(name, neutral)` would have been the natural way to write the loop and
    is the wrong one: a pytest release that renames a dest would make every lookup
    return "not in use" and the detector would go quietly blind. It raises instead.
    """
    with pytest.raises(UnknownPytestOptions) as caught:
        collection_narrowing([], [_TESTS], {}, _ROOT)
    assert "lf" in str(caught.value)


def test_every_ledgered_option_is_a_real_pytest_dest(request):
    """And the ledger is checked against the pytest actually installed, not a memory.

    This is what makes the test above more than a tautology: the names are asserted
    to exist on a live `config.option`, so a rename is caught by the suite rather
    than by the day someone runs `--lf` and wonders why nothing fired. Its other half
    -- that the ledgered VALUES are pytest's own defaults -- is
    `test_the_ledgered_neutrals_are_the_installed_pytests_own_defaults`.
    """
    live = vars(request.config.option)
    missing = sorted(name for name in COLLECTION_NARROWING_OPTIONS if name not in live)
    assert not missing, (
        f"{missing} are not pytest option dests any more. Re-derive them from "
        "`vars(config.option)` and update COLLECTION_NARROWING_OPTIONS.")


# --- the two conftest hooks that produce the number ------------------------------

def test_the_conftest_hooks_count_deselected_items_back_into_the_total(pytester):
    """THE IMPURE HALF, WHICH NOTHING COVERED. The claim is that a deselecting run and
    a bare run measure the SAME number -- which is the only reason PROJECT-NOTES can
    state one figure that fits both CI (`-m "not livepage"`, one deselection) and a
    local `uv run pytest`. Until this test, that claim was exercised only by the gate
    itself, which skips, and asserted by nobody.

    The nested conftest IMPORTS the three real hooks rather than restating them, so
    this cannot pass against a copy that has drifted from `tests/conftest.py`.

    A three-test suite with one test deselected by `-k` must publish a total of three.
    """
    pytester.makeconftest(
        "# The hooks under test, imported -- NOT copied -- from the real conftest.\n"
        "from tests.conftest import (  # noqa: F401\n"
        "    pytest_collection, pytest_collection_finish, pytest_deselected)\n")
    pytester.makepyfile(test_probe="""
        from tests.suite_count import COLLECTED_TESTS

        def test_deselected_by_the_k_expression():
            pass

        def test_the_total_counts_the_deselected_one_back(request):
            assert request.config.stash[COLLECTED_TESTS] == 3

        def test_a_third_so_the_total_is_not_also_the_selected_count():
            pass
    """)
    result = pytester.runpytest_inprocess("-k", "not deselected_by_the_k_expression")
    result.assert_outcomes(passed=2, deselected=1)


def test_the_deselection_counter_does_not_leak_between_runs(pytester):
    """`_deselected` is a module global -- `pytest_deselected` is handed no `Config` --
    so without a reset it is monotonic for the life of the interpreter and a second
    in-process run reads the first one's deselections into its own total.

    Latent in a normal `uv run pytest`, and NOT latent above: the test before this one
    runs a nested pytest inside the outer one, which is exactly the shape that
    double-counts. Running two nested suites of different sizes pins the reset.
    """
    pytester.makeconftest(
        "from tests.conftest import (  # noqa: F401\n"
        "    pytest_collection, pytest_collection_finish, pytest_deselected)\n")
    pytester.makepyfile(test_probe="""
        from tests.suite_count import COLLECTED_TESTS

        def test_skipped_by_k_one():
            pass

        def test_skipped_by_k_two():
            pass

        def test_the_total_is_this_runs_own(request):
            assert request.config.stash[COLLECTED_TESTS] == 3
    """)
    first = pytester.runpytest_inprocess("-k", "not skipped_by_k_one")
    first.assert_outcomes(passed=2, deselected=1)
    # The same suite again. Without the reset the second run's total would be 3 + the
    # first run's deselections, and the nested assertion would fail.
    second = pytester.runpytest_inprocess("-k", "not skipped_by_k")
    second.assert_outcomes(passed=1, deselected=2)


# --- the verdict -----------------------------------------------------------------

def test_a_matching_count_is_ok_even_on_a_partial_run():
    verdict, _ = suite_count_verdict(7, 7, ["path arguments ['tests/test_db.py']"])
    assert verdict == "ok"


@pytest.mark.parametrize("stated, collected", [(7, 12), (12, 7)])
def test_drift_fails_in_both_directions(stated, collected):
    """Every one of the nine recorded drifts was the file LAGGING a grown suite -- the
    ledger in PROJECT-NOTES records none in the other direction. The guard covers it
    anyway, because deleting a test would otherwise silently re-open the hole."""
    verdict, message = suite_count_verdict(stated, collected, [])
    assert verdict == "drift"
    assert str(stated) in message and str(collected) in message
    assert NOTES.name in message


def test_a_mismatch_on_a_partial_run_is_a_skip_that_says_why():
    verdict, message = suite_count_verdict(
        7, 12, ["pytest option lf=True (not in use is False)"])
    assert verdict == "partial"
    assert "lf" in message


def test_the_drift_message_quotes_the_one_line_edit_that_fixes_it():
    """The message is the whole user interface of this gate: whoever hits it has just
    added tests and needs to know WHICH line to change, not that a number is wrong."""
    _, message = suite_count_verdict(7, 12, [])
    assert "How to run / test" in message
