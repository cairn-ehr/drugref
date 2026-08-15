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

# THE LINES THAT ACTUALLY HAPPENED, COPIED BYTE FOR BYTE out of the commits that closed
# an issue nobody meant to close -- `git log -1 --format=%B <sha>` will reproduce each.
# A paraphrase of a token-adjacency bug is a different input, so an earlier version of
# this file that reworded them was not testing what it said it was.
#
# THE PHYSICAL LINE, not the sentence, because that is the unit `closing_references`
# scans and the unit the report quotes back. `ed1ab5e`'s sentence wraps across four
# lines of a body running to nearly three thousand characters; only the first carries
# the offending token.
_92BAAEA = "Filed rather than fixed: #61 gains the index question this round leaves"
_ED1AB5E = "Filed rather than fixed: #114 effective_grades_for has no consumer in src/,"
# ⇒ THE SAME BODY'S FOUR-LINE BLOCK, and it is a stronger input than the one line. It
# names FIVE issues and closed exactly ONE, and `#115` opens the line immediately after
# one ending in a comma -- so a pattern whose gap class included `\n` would pair the
# `fixed:` on line 1 with a reference on line 2 and reject a body GitHub left alone.
_ED1AB5E_BLOCK = (
    "Filed rather than fixed: #114 effective_grades_for has no consumer in src/,\n"
    "#115 ClassGrainCounts.total reads as a denominator for a pair count, #116\n"
    "NULLS FIRST inside DISTINCT ON publishes severity_rank = NULL to a\n"
    "thresholding client, #117 db/035 says nine class rules where #94 says seven.")
# THE OCCURRENCE NOBODY HAD COUNTED, found by this guard rather than by a reviewer.
_293758C = ("Filed rather than fixed: #108 (make max_pair_count exact), #109 "
            "(mirror-oriented rule")
# AND THE ONE THAT RE-CLOSED #114 WHILE DOCUMENTING THE RULE, a round later: quoting the
# offending sentence in a commit body re-arms it. Four commits in this history did that,
# which is why the six ISSUES were closed across ten COMMITS.
_5353BBB = 'done: ed1ab5e\'s own "Filed rather than fixed: #114" sentence closed it,'


def test_reports_the_sentence_that_closed_issue_61() -> None:
    """`92baaea`'s body: the fourth occurrence, and the first with this wording."""
    found = commit_lint.closing_references(_92BAAEA)
    assert [(r.keyword, r.issue) for r in found] == [("fixed", "61")]


def test_reports_the_sentence_that_closed_issue_114_only_for_the_adjacent_number() -> None:
    """`ed1ab5e` named FIVE issues and closed ONE. The guard must say the same thing.

    THE ASSERTION IS THE WHOLE FILE IN ONE LINE. If this returned all five, the guard
    would be reporting "mentions an issue" -- which every commit in this repo does -- and
    would be turned off within a round. It must report exactly the one GitHub closed.

    AND IT PINS THE LINE BOUNDARY, which the single-line version could not: `#115` opens
    the line after one ending `... in src/,`. A gap class including `\\n` would pair that
    comma's `fixed:` with `#115` and reject a body GitHub left alone.
    """
    found = commit_lint.closing_references(_ED1AB5E_BLOCK)
    assert [(r.keyword, r.issue) for r in found] == [("fixed", "114")], (
        "#115, #116, #117 and #94 sit in the same sentence with no keyword adjacent, "
        "and GitHub left every one of them open")


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


def test_ignores_gits_own_trailing_boilerplate() -> None:
    """The message file git hands the hook still carries its own editor block.

    Git deletes that block when it builds the commit, so nothing in it can reach GitHub.
    Scanning it would reject on git's own text -- and the branch name is not
    hypothetical: this very PR was developed on `fix/issues-118-120-122-guards`, and a
    branch called `fix/closes-118` lands a keyword and a number on the status line
    through no fault of the author. A guard that fires on text git is about to delete is
    a guard that gets uninstalled, taking the real check with it.
    """
    body = ("fix(db/039): the guard that runs\n"
            "\n"
            "# Please enter the commit message for your changes.\n"
            "# On branch fix/closes #118\n")
    assert commit_lint.closing_references(body) == ()


def test_a_comment_line_git_KEEPS_is_scanned() -> None:
    """⇒ THE FALSE NEGATIVE THIS GUARD SHIPPED WITH, and the one that mattered most.

    The first version dropped EVERY line beginning with `#`, justified by "git strips
    them before the commit exists". That is true of an EDITOR commit and FALSE of
    `git commit -m` and `-F`: git's cleanup mode is then `whitespace`, not `strip`, and
    `#` lines are stored verbatim. Measured with real git before this test was written:

        git commit -m $'feat: thing\\n\\n## Done\\n# fixes #999 heading\\n'

    stored the `#` line and GitHub would close 999, while the guard reported nothing --
    a silent close produced BY the guard's own blind spot, which is the exact failure
    direction this module says it refuses.

    NOT A CONTRIVED SHAPE. This project pastes commit bodies out of HANDOVER and ROADMAP
    markdown, whose headings begin with `#`, and the existing history already carries
    sixteen body lines that start with one. The distinguishing fact is that git's block
    is contiguous, trailing, and announces itself; a `-m` body has no such marker, so
    the whole message is the author's and all of it is scanned.
    """
    body = "feat: thing\n\n## Done\n# fixes #999 heading\n"
    found = commit_lint.closing_references(body)
    assert [(r.keyword, r.issue) for r in found] == [("fixes", "999")], (
        "git keeps `#` lines for -m/-F commits, so the guard must read them")


def test_the_authors_text_before_gits_block_is_still_scanned() -> None:
    """Truncating at git's block must not truncate the AUTHOR's text above it.

    The failure this pins is an over-eager fix for the test above: a rule that dropped
    the whole file on seeing any boilerplate, or truncated at the first `#` of any kind,
    would swing back to reporting nothing. The author's own line must survive.
    """
    body = ("fix(review/125): the guard\n"
            "\n"
            "Filed rather than fixed: #114 which needs a design call.\n"
            "\n"
            "# Please enter the commit message for your changes.\n"
            "# On branch main\n")
    assert [(r.keyword, r.issue)
            for r in commit_lint.closing_references(body)] == [("fixed", "114")]


def test_the_scissors_line_also_ends_the_authors_text() -> None:
    """`git commit -v` appends a scissors line and the whole diff below it.

    That diff is code, and code says `fixes #123` in comments and changelogs all the
    time. It is also the one marker that is not English, so it keeps working under a
    translated locale where the advice block does not.
    """
    body = ("fix: a thing\n"
            "\n"
            "# ------------------------ >8 ------------------------\n"
            "# Do not modify or remove the line above.\n"
            "+# fixes #777 in a diff hunk\n")
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


@pytest.mark.parametrize("keyword", ["close", "closes", "closed",
                                     "fix", "fixes", "fixed",
                                     "resolve", "resolves", "resolved"])
def test_every_one_of_githubs_nine_keywords_is_reported(keyword: str) -> None:
    """ALL NINE, because only two of them were ever exercised.

    The suite drove `fixed` and `Closes` and nothing else, so a typo in `_KEYWORDS`
    (`resolv` for `resolve`) shipped green -- and GitHub would go on closing issues
    through the spelling the guard had stopped knowing about. The list is the thing a
    reader checks first and the thing nothing else pins.
    """
    found = commit_lint.closing_references(f"This round {keyword} #451 at last.")
    assert [(r.keyword, r.issue) for r in found] == [(keyword, "451")]


def test_the_keyword_must_be_a_whole_word() -> None:
    """`\\b` on the left, and the failure without it is a false POSITIVE.

    `prefixes #12` contains `fixes`, and a guard that rejected it would be quoting a
    keyword the author never wrote back at them -- which earns one `--no-verify`, then
    a second, then an uninstall. The acceptance half of this file exists because a
    guard tuned only by what it rejects rejects everything.
    """
    assert commit_lint.closing_references("The prefixes #12 and #13 are unrelated.") == ()


def test_two_references_to_one_issue_are_counted_once() -> None:
    """The report must say "an issue", not "2 issues", when one issue is named twice.

    A body can reach a keyword twice for the same number -- this project's own review
    rounds quote an offending sentence and then discuss it. Counting matches rather
    than issues told the author "would CLOSE 2 issues" and suggested the fix as
    "issue 114, issue 114", which is the kind of wrongness that costs a guard its
    credibility. BOTH OFFENDING LINES ARE STILL LISTED: two places to fix are two lines
    to show.
    """
    found = commit_lint.closing_references(
        "fixes #114 in passing\nand this closed #114 again\n")
    assert len(found) == 2, "both occurrences are still located for the author"

    report = commit_lint.report(found)
    assert "CLOSE an issue" in report, f"one issue was named twice, not two issues: {report}"
    assert "issue 114, issue 114" not in report
    assert report.count("line: ") == 2, "each place the author must fix is shown"


def test_the_report_counts_distinct_issues_when_there_really_are_two() -> None:
    """The plural half, which no test reached: `len(found) > 1` never ran."""
    report = commit_lint.report(
        commit_lint.closing_references("fixes #114 and resolves #115 too"))
    assert "CLOSE 2 issues" in report
    assert "issue 114, issue 115" in report


def test_an_undecodable_message_still_reports_rather_than_tracebacking(tmp_path) -> None:
    """A message file that is not UTF-8 must not abort the commit with a stack trace.

    Reachable through `i18n.commitEncoding`. `read_text` used to raise
    `UnicodeDecodeError`, which exits non-zero -- so the commit was rejected, correctly,
    for a reason the author could not see and with no mention of what the hook wanted.
    The documented escape from a rejection is `--no-verify`, so an encoding fault taught
    the author to switch the guard off. A replacement character cannot fabricate a `#`
    or a digit, so the scan stays sound on the bytes that did decode.
    """
    path = tmp_path / "COMMIT_EDITMSG"
    path.write_bytes("fix: a caf\xe9 in latin-1\n\nfixes #451\n".encode("latin-1"))
    assert commit_lint.main([str(path)]) == 1


def test_the_shipped_hook_accepts_a_clean_message(tmp_path) -> None:
    """Exit 0 THROUGH THE SHELL, which nothing checked.

    `main` returning 0 was pinned; the wrapper propagating it was not. A hook that
    rejects every commit is uninstalled just as fast as one that rejects none, and
    `uv run` has its own ways to fail -- a stale lock, an unsynced project -- which is
    why the script triages the exit status rather than forwarding it blindly.
    """
    hook = pathlib.Path(__file__).resolve().parent.parent / ".githooks" / "commit-msg"
    path = tmp_path / "COMMIT_EDITMSG"
    path.write_text("fix(db/039): the guard that runs\n\nFiled rather than fixed: "
                    "issue 114, which needs a design call.\n")

    run = subprocess.run([str(hook), str(path)], capture_output=True, text=True,
                         cwd=hook.parent.parent)

    assert run.returncode == 0, f"the hook rejected a clean message: {run.stderr}"


def test_the_hooks_python3_fallback_runs_when_uv_is_absent(tmp_path) -> None:
    """⇒ THE BRANCH WHOSE WHOLE JUSTIFICATION IS "still gets the guard", never run.

    `command -v uv` succeeds in every dev and CI environment, so the fallback at the
    foot of the hook was dead code as far as the suite was concerned -- and it was
    BROKEN: `commit_lint` used a PEP 604 annotation evaluated at import time, so under
    the Python the OS ships (3.9.6 on current macOS) the module raised `TypeError`
    before reading a byte. Exit 1 then aborted EVERY commit, clean ones included, with a
    traceback instead of a report: the precise inverse of what the fallback promises.

    THE PATH IS SCRUBBED rather than mocked, because the thing under test is what
    happens when `uv` cannot be found -- which is the ordinary case for GUI git clients
    and many CI images, not an exotic one.
    """
    hook = pathlib.Path(__file__).resolve().parent.parent / ".githooks" / "commit-msg"
    path = tmp_path / "COMMIT_EDITMSG"
    path.write_text(f"fix(review/119): five silent gaps\n\n{_ED1AB5E}\n")

    run = subprocess.run(
        [str(hook), str(path)], capture_output=True, text=True, cwd=hook.parent.parent,
        env={"PATH": "/usr/bin:/bin", "HOME": str(tmp_path)})

    assert run.returncode == 1, (
        f"the fallback interpreter did not reject: {run.stdout}{run.stderr}")
    assert "114" in run.stdout + run.stderr, "it must print the report, not a traceback"
    assert "Traceback" not in run.stderr, run.stderr


def test_the_hooks_python3_fallback_accepts_a_clean_message(tmp_path) -> None:
    """The fallback's acceptance half: exit 0, so it cannot block every commit.

    This is the assertion that would have caught the `TypeError`. The rejection test
    above passes even on a module that cannot be imported, because a crash also exits
    non-zero -- "rejects the bad message" and "rejects everything" are the same
    observation until something asserts the good message survives.
    """
    hook = pathlib.Path(__file__).resolve().parent.parent / ".githooks" / "commit-msg"
    path = tmp_path / "COMMIT_EDITMSG"
    path.write_text("fix(db/039): the guard that runs\n\nFiled rather than fixed: "
                    "issue 114, which needs a design call.\n")

    run = subprocess.run(
        [str(hook), str(path)], capture_output=True, text=True, cwd=hook.parent.parent,
        env={"PATH": "/usr/bin:/bin", "HOME": str(tmp_path)})

    assert run.returncode == 0, (
        f"the fallback rejected a clean message: {run.stdout}{run.stderr}")


def test_the_sentence_that_re_closed_114_while_documenting_the_rule() -> None:
    """QUOTING THE OFFENDING SENTENCE RE-ARMS IT, which is how six issues took ten
    commits. `5353bbb` explained the bug in its own body and closed #114 a second time
    doing so -- and this guard rejected its own shipping commit for the same reason.
    """
    found = commit_lint.closing_references(_5353BBB)
    assert [(r.keyword, r.issue) for r in found] == [("fixed", "114")]


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
