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
The numerals in this file are fixtures for the pure helpers -- 7, 12, 40 -- chosen to
look nothing like a suite size. PROJECT-NOTES stays the single home.

**WHY THE COUNT IS TAKEN IN-PROCESS.** A `--collect-only` subprocess would measure the
same thing, and issue 146 rejected it as slow and fragile. `tests/conftest.py` records
the total during collection instead (see its `pytest_collection_finish`), which costs
nothing and cannot disagree with the run it is part of.
"""
import pytest

from tests.suite_count import (COLLECTED_TESTS, COLLECTION_NARROWING_OPTIONS, NOTES,
                               SuiteCountUnreadable, UnknownPytestOptions,
                               collection_narrowing, stated_suite_count,
                               suite_count_verdict)


def _neutral_options(**overrides):
    """Every narrowing option at its not-in-use value, with any of them overridden.

    Built FROM the ledger rather than retyped beside it, so a name added to
    `COLLECTION_NARROWING_OPTIONS` is automatically present here and the helper can
    never go stale against the thing it feeds.
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
        collection_narrowing(request.config.option.file_or_dir,
                             request.config.getini("testpaths"),
                             vars(request.config.option)))
    if verdict == "partial":
        # A single-file or --lf run collected a subset on purpose. Skipping is right
        # here and is not a hole: CI runs the whole suite with no path arguments (see
        # .github/workflows/ci.yml), so the comparison always happens where it counts
        # -- and that workflow fails on ANY skip, which pins this branch shut there.
        pytest.skip(message)
    assert verdict == "ok", message


def test_the_gate_fires_on_the_two_invocations_that_actually_run_it():
    """The negative control for the skip above -- the shape of issues 74/66/76.

    A narrowing-detector that is too eager turns this whole file into a permanent
    skip, which is exactly the vacuous green it exists to prevent, and nothing else
    would ever say so. These are the two command lines the gate has to fire on: a
    bare `uv run pytest` (no path arguments; testpaths supplies `tests`) and CI's
    `uv run pytest -q -m "not livepage"`.

    `-m` is NOT narrowing, and that is the subtle half: it DESELECTS, and conftest
    adds deselected items back, so the count is the same either way.
    """
    assert collection_narrowing([], ["tests"], _neutral_options()) == []
    assert collection_narrowing([], ["tests"], _neutral_options(markexpr="not livepage")) == []
    # `pytest tests` and `pytest tests/` name the whole of testpaths and are complete.
    assert collection_narrowing(["tests"], ["tests"], _neutral_options()) == []
    assert collection_narrowing(["tests/"], ["tests"], _neutral_options()) == []


# --- reading the one home --------------------------------------------------------

def test_the_number_is_read_off_project_notes_and_nowhere_else():
    """The file exists, is where this test says it is, and states the count once."""
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
    {"stepwise": True},
])
def test_an_option_that_shrinks_collection_is_reported_as_narrowing(options):
    """Every name in the ledger, driven one at a time."""
    reasons = collection_narrowing([], ["tests"], _neutral_options(**options))
    assert len(reasons) == 1, reasons
    assert next(iter(options)) in reasons[0]


def test_a_single_file_argument_is_reported_as_narrowing():
    reasons = collection_narrowing(["tests/test_db.py"], ["tests"], _neutral_options())
    assert len(reasons) == 1, reasons
    assert "tests/test_db.py" in reasons[0]


@pytest.mark.parametrize("options", [
    {"keyword": "writer"},
    {"markexpr": "not livepage"},
    {"deselect": ["tests/test_db.py::test_x"]},
])
def test_deselection_is_not_narrowing_because_conftest_adds_it_back(options):
    """-k, -m and --deselect all route through `pytest_deselected`, which is counted.

    Calling them narrowing would make the gate skip on CI's own command line.
    """
    assert collection_narrowing([], ["tests"], _neutral_options(**options)) == []


def test_a_renamed_pytest_option_is_a_loud_failure_not_a_silent_pass():
    """The ledger is checked against the options it is handed, in both directions.

    `options.get(name, neutral)` would have been the natural way to write the loop and
    is the wrong one: a pytest release that renames a dest would make every lookup
    return "not in use" and the detector would go quietly blind. It raises instead.
    """
    with pytest.raises(UnknownPytestOptions) as caught:
        collection_narrowing([], ["tests"], {})
    assert "lf" in str(caught.value)


def test_every_ledgered_option_is_a_real_pytest_dest(request):
    """And the ledger is checked against the pytest actually installed, not a memory.

    This is what makes the test above more than a tautology: the names are asserted
    to exist on a live `config.option`, so a rename is caught by the suite rather
    than by the day someone runs `--lf` and wonders why nothing fired.
    """
    live = vars(request.config.option)
    missing = sorted(name for name in COLLECTION_NARROWING_OPTIONS if name not in live)
    assert not missing, (
        f"{missing} are not pytest option dests any more. Re-derive them from "
        "`vars(config.option)` and update COLLECTION_NARROWING_OPTIONS.")


# --- the verdict -----------------------------------------------------------------

def test_a_matching_count_is_ok_even_on_a_partial_run():
    verdict, _ = suite_count_verdict(7, 7, ["path arguments ['tests/test_db.py']"])
    assert verdict == "ok"


@pytest.mark.parametrize("stated, collected", [(7, 12), (12, 7)])
def test_drift_fails_in_both_directions(stated, collected):
    """Four of the nine drifts were the file LAGGING a grown suite; the guard has to
    catch the other direction too, or deleting a test silently re-opens the hole."""
    verdict, message = suite_count_verdict(stated, collected, [])
    assert verdict == "drift"
    assert str(stated) in message and str(collected) in message
    assert NOTES.name in message


def test_a_mismatch_on_a_partial_run_is_a_skip_that_says_why():
    verdict, message = suite_count_verdict(7, 12, ["pytest option lf=True (neutral is False)"])
    assert verdict == "partial"
    assert "lf" in message


def test_the_drift_message_quotes_the_one_line_edit_that_fixes_it():
    """The message is the whole user interface of this gate: whoever hits it has just
    added tests and needs to know WHICH line to change, not that a number is wrong."""
    _, message = suite_count_verdict(7, 12, [])
    assert "How to run / test" in message
