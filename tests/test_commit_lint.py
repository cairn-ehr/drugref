"""The commit-msg guard (issue 118): a closing keyword adjacent to an issue reference.

WHY A MECHANICAL GUARD AND NOT A THIRD RESTATEMENT OF THE RULE. SIX commits have now
closed an issue nobody meant to close -- #31, #35, #40, #61, #108, #114 -- and THREE of
them did it with the SAME SENTENCE, `Filed rather than fixed: #N`, a sentence that
*declares the issue unfixed*. The rule was written out in full, with the mechanism and
the explicit warning that "a colon in between does not save you", after occurrence four;
occurrences five and six were the next two rounds to write that line. Prose has been
tried and has lost.

⇒ AND THE SIXTH WAS FOUND BY RUNNING THE FINISHED GUARD OVER ALL 363 COMMITS, not by a
human review. `293758c` closed #108 and every document in this repo -- issue 118 included
-- said five. A guard that finds an uncounted instance of the thing it was built for, on
its first run, is the argument for building it.

THE POSITIVE CONTROLS ARE THE POINT, not decoration. `ed1ab5e` named four issues in one
sentence and closed exactly one of them -- #114, the one a keyword sat next to -- while
#115, #116 and #117 in the same sentence survived. That asymmetry is what pins the
mechanism to TOKEN ADJACENCY rather than to "mentioning an issue in a commit", and a
guard that rejected all four would be a different, useless tool: every round of this
project references issues it is not closing. So each rejection case below is paired with
an acceptance case that must NOT be reported, and a guard that flagged everything would
fail this file rather than pass it.
"""
import pathlib
import subprocess

import pytest

from drugref import commit_lint

# THE TWO BODIES THAT ACTUALLY HAPPENED, quoted rather than paraphrased, because a
# paraphrase of a token-adjacency bug is a different input. Both are real commit bodies:
# `92baaea` closed #61 and `ed1ab5e` closed #114, and both sentences say the opposite.
_92BAAEA = "Filed rather than fixed: #61 -- the class-grain measurement needs content."
_ED1AB5E = ("Filed rather than fixed: #114, #115, #116 and #117, each needing a design "
            "call this round should not make in passing.")
# THE OCCURRENCE NOBODY HAD COUNTED, found by this guard rather than by a reviewer.
_293758C = ("Filed rather than fixed: #108 (make max_pair_count exact), #109 "
            "(mirror-oriented rule pairs), #110 (ship the precedence as a view).")


def test_reports_the_sentence_that_closed_issue_61() -> None:
    """`92baaea`'s body: the fourth occurrence, and the first with this wording."""
    found = commit_lint.closing_references(_92BAAEA)
    assert [(r.keyword, r.issue) for r in found] == [("fixed", "61")]


def test_reports_the_sentence_that_closed_issue_114_only_for_the_adjacent_number() -> None:
    """`ed1ab5e` named FOUR issues and closed ONE. The guard must say the same thing.

    THE ASSERTION IS THE WHOLE FILE IN ONE LINE. If this returned all four, the guard
    would be reporting "mentions an issue" -- which every commit in this repo does -- and
    would be turned off within a round. It must report exactly the one GitHub closed.
    """
    found = commit_lint.closing_references(_ED1AB5E)
    assert [(r.keyword, r.issue) for r in found] == [("fixed", "114")]


def test_reports_the_uncounted_sixth_occurrence() -> None:
    """`293758c`: the same sentence again, and #108 really is CLOSED on GitHub today.

    IT WAS NEVER IN THE LEDGER. Issue 118, HANDOVER, PROJECT-NOTES and ROADMAP all said
    five occurrences and listed #31, #35, #40, #61, #114. This body was found by running
    the finished guard across the whole history, which is the strongest evidence the
    guard is worth having: the failure mode is silent BY CONSTRUCTION, so counting it by
    hand undercounts it, and the count was the evidence the prose rule was failing.

    #108 was in fact fixed later by db/037, so no work was lost -- unlike #114, where the
    issue sat closed with nothing done. That is luck, not a mitigation.
    """
    found = commit_lint.closing_references(_293758C)
    assert [(r.keyword, r.issue) for r in found] == [("fixed", "108")], (
        "#109 and #110 sit in the same sentence with no keyword adjacent, and GitHub "
        "left them open -- the guard must draw the same line GitHub drew")


def test_reports_the_full_url_form() -> None:
    """GitHub closes on a full issue URL too, and this project writes those constantly.

    EVERY DOCUMENT IN `docs/` LINKS ISSUES THIS WAY -- `[#120](https://github.com/...)`
    is the house style -- so a body pasted out of HANDOVER or ROADMAP carries the URL
    form, not the `#N` form. A guard that only knew `#N` would be blind to the shape
    this repo's own prose produces most often.
    """
    found = commit_lint.closing_references(
        "Closes https://github.com/cairn-ehr/drugref/issues/123 at last.")
    assert [(r.keyword, r.issue) for r in found] == [("Closes", "123")]


def test_ignores_git_comment_lines() -> None:
    """The message file git hands the hook still carries its own `#` comment lines.

    Git strips them when it builds the commit, so they never reach GitHub and can never
    close anything. Scanning them would reject on git's own boilerplate -- and the
    boilerplate is not hypothetical: `git commit` after a conflict writes
    "# Conflicts:" blocks, and any branch named for an issue lands in the status
    comment. A guard that fires on text git is about to delete is a guard that gets
    uninstalled.
    """
    body = ("fix(db/039): the guard that runs\n"
            "\n"
            "# Please enter the commit message for your changes.\n"
            "# On branch fix/closes #118\n")
    assert commit_lint.closing_references(body) == ()


def test_main_rejects_and_names_the_offending_line(tmp_path, capsys) -> None:
    """The hook's exit code is what stops the commit; the report is what fixes it.

    ASSERTS THE LINE, NOT JUST THE NUMBER. An author who is told "a closing keyword was
    found" goes hunting through a body they just wrote; one who is shown their own
    sentence back fixes it in seconds. `ed1ab5e`'s body is 300+ characters and the
    offending token is two of them.
    """
    path = tmp_path / "COMMIT_EDITMSG"
    path.write_text(f"fix(review/119): five silent gaps\n\n{_ED1AB5E}\n")

    assert commit_lint.main([str(path)]) == 1

    report = capsys.readouterr().err
    assert "114" in report
    assert _ED1AB5E in report, "the author must be shown their own sentence"
    assert "issue 114" in report, "the report must state the documented workaround"
    assert "--no-verify" in report, "the deliberate close must have a stated way out"


def test_main_accepts_a_clean_message(tmp_path) -> None:
    """Exit 0 on the ordinary case, which is every commit this project should write."""
    path = tmp_path / "COMMIT_EDITMSG"
    path.write_text("fix(db/039): the guard that runs\n\nFiled rather than fixed: "
                    "issue 114, which needs a design call.\n")
    assert commit_lint.main([str(path)]) == 0


def test_the_shipped_hook_script_rejects_the_sentence_that_closed_issue_114(
        tmp_path) -> None:
    """Drive `.githooks/commit-msg` itself, as git will: argv[1] is the message file.

    WHY THE SHELL WRAPPER IS EXERCISED AND NOT ONLY THE PYTHON. The guard is three
    pieces -- a pure function, a `__main__`, and a shell script git executes -- and the
    first two being green says nothing about whether the third finds the interpreter,
    passes its argument through, or propagates the exit code. A hook that exits 0 on
    every message is indistinguishable from no hook at all, and that is precisely the
    failure this project has already met once as "a gate that exists and never fires"
    (issues 74, 66, 76). This test is the thing that would notice.
    """
    hook = pathlib.Path(__file__).resolve().parent.parent / ".githooks" / "commit-msg"
    path = tmp_path / "COMMIT_EDITMSG"
    path.write_text(f"fix(review/119): five silent gaps\n\n{_ED1AB5E}\n")

    run = subprocess.run([str(hook), str(path)], capture_output=True, text=True,
                         cwd=hook.parent.parent)

    assert run.returncode == 1, f"the hook did not reject: {run.stdout}{run.stderr}"
    assert "114" in run.stdout + run.stderr


@pytest.mark.parametrize("body", [
    # THE STANDING RULE'S OWN PRESCRIPTION -- "write the number WITHOUT a `#`" -- which
    # must therefore be accepted, or the guard rejects the documented workaround.
    "Filed rather than fixed: issue 114, which needs a design call.",
    # A BARE REFERENCE WITH NO KEYWORD NEAR IT: #115 survived `ed1ab5e` for exactly this
    # reason, and every round of this project references issues it is not closing.
    "Follows #115 and #116; see the ledger in PROJECT-NOTES.",
    # THE KEYWORD ALONE, no reference: prose about fixing things is not a link.
    "This round fixes the detector that was keyed on the cause it imagined.",
    # A NUMBER THAT IS NOT AN ISSUE REFERENCE. `#113`'s PR number appears in nearly every
    # body this project writes, but only next to a keyword does it close anything.
    "fix(db/038): PR #113's four filed issues, and the one that closed itself",
])
def test_accepts_bodies_that_close_nothing(body: str) -> None:
    """The acceptance half. A guard tuned only by what it rejects rejects everything."""
    assert commit_lint.closing_references(body) == ()
