# tests/test_module_size_cap.py
"""CLAUDE.md rule 4, as a gate rather than as a habit (issue 172).

⇒ WHY THIS FILE REPLACES TWO OTHERS. `500` was written down in
`test_cli.py::test_cli_py_is_under_the_size_cap` and again in
`test_cli_signing.py::test_cli_signing_py_is_under_the_size_cap`, and each pinned
the one file its author had just written. Between them they covered three of the
package's **76** modules, so rule 4 was enforced on the files that happened to
have been near the line once and on nothing else -- which is how
`spl_evidence.py` reached **518** with a green suite. A cap is a vocabulary like
any other, and this repo has repeatedly lost rounds to one rule kept in two
places; both of those tests are DELETED and folded into
`test_the_modules_this_round_moved_code_between_are_under_the_cap` below, which
pins all three of the modules they pinned. This is now the ONE home for the
number.

**THE MEASUREMENT IS OF THE FILE ON DISK, not of `module.__file__`** as the two
deleted tests did. Reading the path off an imported module makes a size gate
depend on the module importing, so a file with a syntax error or a missing
dependency would take the cap test down with everything else instead of being
measured. Nothing here imports drugref at all.

⇒ AND IT SWEEPS, so a module written next round is covered without anybody
remembering to add it. That is the difference between this and what it replaces:
the old tests could only ever fail for a file someone had already worried about.

**THE EXEMPTION LIST IS A LEDGER, NOT AN ESCAPE HATCH**, and it is checked in
both directions. Seven modules were already over the cap when this gate was
written -- none of them touched by the round that wrote it -- and refusing to
merge until all seven were split would have been a different project. They are
named below with their measured size, and the second test asserts that each one is
STILL over: a module that gets split and then quietly stays on the list would turn
the ledger into a permanent exemption, which is the thing an allow-list always
decays into. Removing a name is part of splitting the file.

⇒ **AND IT IS A RATCHET, WHICH IS WHAT MAKES "BOTH DIRECTIONS" TRUE.** The first
version of this file recorded each module's size and then never read the number:
both tests used only the keys, so the seven LARGEST modules in the package -- the
seven most likely to keep growing -- were exempt from rule 4 in the growing
direction, permanently and by construction. A ledger whose values are decoration is
an allow-list wearing a ledger's clothes. The recorded size is now the ceiling: a
ledgered module may shrink and leave, and may not grow.
"""
import pathlib

import pytest

#: THE ONE HOME FOR RULE 4'S NUMBER.
CAP = 500

#: Modules that were ALREADY over `CAP` when this gate landed (2026-09-02), with
#: the size measured that day -- WHICH IS ALSO EACH ONE'S CEILING, see the ratchet
#: paragraph above. Nothing here was written or grown by the round that
#: added this file; the list exists so the gate could be switched on at all.
#: Filed as issue 177 -- and see issue 172, which is the one of these that WAS
#: split (`spl_evidence.py`, 518 -> 428 by the move and 430 today, `Registry`/`load_registry` moved to
#: `registry_read.py`) and is therefore not on the list.
OVER_CAP_TODAY = {
    "questions.py": 797,
    "ingest/fda_cyp.py": 707,
    "ingest/fda_cyp_run.py": 622,
    "signing.py": 605,
    "curation.py": 555,
    "release_verification.py": 540,
    "ingest/spl_match.py": 524,
}

_PACKAGE = pathlib.Path(__file__).resolve().parent.parent / "src" / "drugref"


def _modules():
    """Every .py under src/drugref, as (relative path, line count).

    `migrations/` is excluded because it holds no Python at all -- pyproject's
    force-include copies db/*.sql there in a wheel build, and an editable install
    gets that copy too.
    """
    return sorted(
        (str(path.relative_to(_PACKAGE)), len(path.read_text().splitlines()))
        for path in _PACKAGE.rglob("*.py")
        if "migrations" not in path.parts)


def test_every_module_is_under_the_size_cap_unless_it_is_on_the_ledger():
    """Rule 4, swept over the whole package rather than over three remembered files.

    The message names the seam-finding job, not just the number: a module over the
    cap needs a split with a REASON, and this project's precedent for that is
    `spl_release.py` out of `spl_dailymed.py` and `cli_status.py` out of `cli.py`
    -- verbatim move first, whole suite green, then any behaviour change.
    """
    over = {name: lines for name, lines in _modules()
            if lines > CAP and name not in OVER_CAP_TODAY}
    assert not over, (
        f"over CLAUDE.md rule 4's ~{CAP}-line cap and not on the ledger: {over}. "
        "Split it (verbatim move first, suite green, then behaviour) rather than "
        "adding it to OVER_CAP_TODAY -- that list is a record of what predates "
        "this gate, not a place to put new work.")


@pytest.mark.parametrize("name", sorted(OVER_CAP_TODAY))
def test_a_ledgered_module_that_is_no_longer_over_the_cap_must_leave_the_ledger(name):
    """The direction an allow-list normally rots in, closed.

    A name left behind after its file was split is a module silently exempt from
    rule 4 forever, and nothing else in the suite would ever mention it again.
    """
    sizes = dict(_modules())
    assert name in sizes, f"{name} no longer exists; drop it from OVER_CAP_TODAY"
    assert sizes[name] > CAP, (
        f"{name} is {sizes[name]} lines, back under the ~{CAP} cap -- remove it "
        "from OVER_CAP_TODAY so the sweep starts guarding it")
    assert sizes[name] <= OVER_CAP_TODAY[name], (
        f"{name} is {sizes[name]} lines, up from the {OVER_CAP_TODAY[name]} "
        f"recorded when this ledger was written. A ledgered module may SHRINK and "
        f"leave the ledger; it may not grow. Split it (verbatim move first, suite "
        f"green, then behaviour) rather than raising the recorded number -- raising "
        f"it is how an allow-list is made out of a ledger.")


def test_the_modules_this_round_moved_code_between_are_under_the_cap():
    """The specific pins issue 172 asked for, kept explicit as well as swept.

    `spl_evidence.py` is the file the issue was filed about; `registry_read.py`
    is where its read path went. Naming them is not redundant with the sweep: it
    is what makes a future edit that pushes either one over fail with the ISSUE
    NUMBER in the message rather than with a bare line count.
    """
    sizes = dict(_modules())
    for name in ("spl_evidence.py", "registry_read.py", "analyze.py",
                 "server_messages.py", "db.py", "cli.py", "cli_signing.py",
                 "cli_signing_release.py"):
        # Named, so a rename or a split fails with THIS sentence rather than with
        # a bare KeyError from the lookup below -- the guard its sibling above
        # already carries.
        assert name in sizes, (
            f"{name} no longer exists under src/drugref; this pin names the "
            "modules issue 172 moved code between, so update the list with the "
            "split rather than deleting the pin")
        assert sizes[name] <= CAP, (
            f"{name} is {sizes[name]} lines, over the ~{CAP} cap. This is issue "
            "172's shape again: spl_evidence.py reached 518 because the module "
            "held a READ path (Registry/load_registry) inside the SOLE WRITER of "
            "the SPL projection. Find the seam, do not add a paragraph.")
