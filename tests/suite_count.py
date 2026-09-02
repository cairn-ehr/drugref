# tests/suite_count.py
"""PURE helpers behind issue 146's gate, plus the stash key the count travels in.

Split out of `tests/test_suite_count.py` for the reason `dsn_verdict` was split out of
the fixture that uses it: the decision has to be drivable without a pytest run that is
already narrowed, already green, or already stale. Everything here is a function of its
arguments -- no `config`, no environment, no filesystem except the one `read_text()` the
caller does.

Three jobs, in the order the gate uses them:

1. `stated_suite_count(text)` -- read THE ONE HOME out of PROJECT-NOTES, refusing a file
   that states it zero times or twice rather than measuring nothing and passing.
2. `collection_narrowing(...)` -- decide whether THIS invocation collected the whole
   suite, so a developer's single-file run does not fail on a comparison it cannot make.
3. `suite_count_verdict(...)` -- combine the two into "ok" / "drift" / "partial", with
   the message that tells whoever hit it which line to edit.
"""
import pathlib
import re

import pytest

#: The file that carries the number. There is no second copy anywhere, by design --
#: issue 146's sixth occurrence was created by a round that wrote the count into three
#: further places while filing the issue about the first one drifting.
NOTES = pathlib.Path(__file__).resolve().parent.parent / "docs" / "PROJECT-NOTES.md"

#: The section of NOTES the line lives in, quoted in the drift message so the fix is a
#: one-line edit rather than a hunt through 5,700 lines.
SECTION = 'How to run / test'

#: The phrase that marks the line. It is the comment's own self-description, so the
#: marker and the claim cannot drift apart: a line that stops calling itself the one
#: home stops being read as the one home, loudly (see SuiteCountUnreadable).
MARKER = "THE ONE HOME FOR THIS NUMBER"

#: `^# <count> tests (THE ONE HOME ...`. Anchored at the start of a line and requiring
#: the numeral to sit immediately before the word "tests", because the paragraph it
#: heads is full of other numerals -- every past drift is recorded there as a pair.
_STATED = re.compile(r"^#\s*(\d+)\s+tests\s+\(" + re.escape(MARKER), re.MULTILINE)

#: Where `tests/conftest.py` leaves this run's collected total for the gate to read.
#: A stash key rather than a module global so it is scoped to the `Config` that
#: produced it and cannot survive into another run in the same process.
COLLECTED_TESTS = pytest.StashKey[int]()

#: pytest options that make a run collect FEWER tests than a bare `uv run pytest`,
#: mapped to the value that means "not in use". These are DESTS on `config.option`,
#: pinned against the installed pytest by
#: `test_suite_count.py::test_every_ledgered_option_is_a_real_pytest_dest`.
#:
#: `-k` (keyword), `-m` (markexpr) and `--deselect` are DELIBERATELY ABSENT. They
#: deselect rather than narrow collection, and conftest adds deselected items back, so
#: the total is unchanged -- which matters because CI's own command line is
#: `pytest -q -m "not livepage"`, and calling that narrowing would make this gate skip
#: in the one place it must never skip.
#:
#: `--ff`/`--nf` are absent too: they REORDER a complete collection.
COLLECTION_NARROWING_OPTIONS = {
    "ignore": None,
    "ignore_glob": None,
    "lf": False,
    "stepwise": False,
}


class SuiteCountUnreadable(Exception):
    """PROJECT-NOTES does not state the count exactly once."""


class UnknownPytestOptions(Exception):
    """A ledgered option dest is not on the `config.option` handed in."""


def stated_suite_count(text):
    """PURE: the number PROJECT-NOTES states, or a loud refusal.

    Refusing on anything but exactly one match is the whole reason this is a function
    and not a `re.search(...).group(1)`. `test_spl_tools_smoke.py` shipped a scan that
    could match nothing and pass, and did; a count read off a reworded file would be a
    silent zero-match here in exactly the same way.
    """
    found = _STATED.findall(text)
    if len(found) != 1:
        raise SuiteCountUnreadable(
            f"expected exactly ONE line matching {_STATED.pattern!r} in {NOTES.name} "
            f"§ \"{SECTION}\", found {len(found)}: {found}. That line is the single "
            f"home for the suite count (issue 146). If it was reworded, restore the "
            f"marker {MARKER!r}; if it was duplicated, delete the copy -- a number "
            f"with two homes has two chances to be the stale one.")
    return int(found[0])


def collection_narrowing(file_or_dir, testpaths, options):
    """PURE: the reasons this invocation collected less than the whole suite.

    An empty list means the run is comparable with the documented total. Anything else
    is a reason, phrased for a skip message.

    `options` is `vars(config.option)`. Every ledgered name must be PRESENT in it: the
    obvious `options.get(name, neutral)` would turn a renamed pytest dest into a
    detector that reports "not in use" forever, which is the gate-that-never-fires
    shape this repo has now lost four rounds to.

    The asymmetry that makes an incomplete ledger survivable: a narrowing this does not
    know about produces FEWER collected tests than the file states, and the verdict
    below turns that into a LOUD failure. Under-detection is noisy; over-detection is
    the silent one, which is why the negative control in the test file pins the two
    command lines that must come back empty.
    """
    unknown = sorted(set(COLLECTION_NARROWING_OPTIONS) - set(options))
    if unknown:
        raise UnknownPytestOptions(
            f"{unknown} are not present on the pytest options handed in. They are "
            f"dests on `config.option`; re-derive them from `vars(config.option)` "
            f"rather than defaulting them, or collection narrowing goes undetected.")
    reasons = []
    roots = {str(path).rstrip("/") for path in testpaths}
    extra = [arg for arg in file_or_dir if str(arg).rstrip("/") not in roots]
    if extra:
        reasons.append(
            f"path arguments {extra} rather than the whole of testpaths "
            f"{sorted(roots)}")
    for name, neutral in sorted(COLLECTION_NARROWING_OPTIONS.items()):
        if options[name] != neutral:
            reasons.append(
                f"pytest option {name}={options[name]!r} (not in use is {neutral!r})")
    return reasons


def suite_count_verdict(stated, collected, narrowing):
    """PURE: ("ok" | "drift" | "partial", message) for the gate to act on.

    Order matters. A count that MATCHES is "ok" whatever the invocation was -- there is
    nothing to complain about and no reason to skip. Only a MISMATCH has to ask whether
    this run could have produced one honestly.
    """
    if collected == stated:
        return "ok", (
            f"{NOTES.name} states {stated} tests and this run collected {collected}")
    if narrowing:
        return "partial", (
            f"this run collected {collected} of the {stated} tests {NOTES.name} "
            f"states, because it narrowed collection: {'; '.join(narrowing)}. Run the "
            f"whole suite (`uv run pytest`) to compare the two -- see issue 146.")
    return "drift", (
        f"{NOTES.name} § \"{SECTION}\" states {stated} tests; this run collected "
        f"{collected} ({collected - stated:+d}). That comment is the ONE home for the "
        f"number (issue 146) and it has now drifted nine times, every time because the "
        f"round that changed the suite updated its own section, a commit message, or "
        f"nothing at all. Change it THERE, to {collected}, and read the figure off the "
        f"run that verified green after your LAST edit.")
