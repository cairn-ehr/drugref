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

**THERE IS NO EXEMPTION LIST ANY MORE (issue 177).** Seven modules were already
over the cap when this gate was written, so it shipped with an `OVER_CAP_TODAY`
ledger naming each with its measured size -- checked in both directions, and a
ratchet: a ledgered module had to stay over (or leave the list) and could not grow
past its recorded size. Each was then split on a real seam, the last being
`curation.py` (555 -> 413, its class-grain counts to `class_grain_read.py`), and an
empty ledger is just an allow-list waiting for its first entry, so it was deleted
with its test. Every module is now under the cap, with no exceptions. A module that
grows over it needs a split with a reason, not a new list.
"""
import pathlib

#: THE ONE HOME FOR RULE 4'S NUMBER.
CAP = 500

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


def test_every_module_is_under_the_size_cap():
    """Rule 4, swept over the whole package rather than over three remembered files.

    The message names the seam-finding job, not just the number: a module over the
    cap needs a split with a REASON, and this project's precedent for that is
    `spl_release.py` out of `spl_dailymed.py` and `cli_status.py` out of `cli.py`
    -- verbatim move first, whole suite green, then any behaviour change.
    """
    over = {name: lines for name, lines in _modules() if lines > CAP}
    assert not over, (
        f"over CLAUDE.md rule 4's ~{CAP}-line cap: {over}. Split it on a real "
        "seam (verbatim move first, suite green, then behaviour); there is no "
        "exemption list to add it to, and issue 177 deleted the last one.")


def test_the_modules_this_round_moved_code_between_are_under_the_cap():
    """The specific pins issues 172 and 177 asked for, kept explicit as well as swept.

    `spl_evidence.py` is the file issue 172 was filed about; `registry_read.py`
    is where its read path went; the rest were added as later splits (issue 177
    among them) landed. Naming them is not redundant with the sweep: it
    is what makes a future edit that pushes either one over fail with the ISSUE
    NUMBER in the message rather than with a bare line count.
    """
    sizes = dict(_modules())
    for name in ("spl_evidence.py", "registry_read.py", "analyze.py",
                 "server_messages.py", "db.py", "cli.py", "cli_signing.py",
                 "cli_signing_release.py", "questions.py",
                 "gap_sources_registry.py", "gap_sources_interactions.py",
                 "ingest/fda_cyp.py", "ingest/fda_cyp_cell.py",
                 "ingest/fda_cyp_run.py", "ingest/fda_cyp_disposition.py",
                 "release_verification.py", "release_signature.py",
                 "signing.py", "signing_contexts.py",
                 "curation.py", "class_grain_read.py"):
        # Named, so a rename or a split fails with THIS sentence rather than with
        # a bare KeyError from the lookup below -- the guard its sibling above
        # already carries.
        assert name in sizes, (
            f"{name} no longer exists under src/drugref; this pin names the "
            "modules issues 172 and 177 moved code between, so update the list with the "
            "split rather than deleting the pin")
        assert sizes[name] <= CAP, (
            f"{name} is {sizes[name]} lines, over the ~{CAP} cap -- the shape issues "
            "172 and 177 split: a module holding two vocabularies (spl_evidence.py "
            "reached 518 with a READ path, Registry/load_registry, inside the SOLE "
            "WRITER of the SPL projection). Find the seam, do not add a paragraph.")
