"""Reject a commit message that closes a GitHub issue by accident (issue 118).

WHY THIS EXISTS, AND WHY IT IS CODE RATHER THAN A SENTENCE IN A STYLE GUIDE. GitHub
closes an issue when a merged commit body puts one of its closing keywords immediately
before a reference to it. SIX ISSUES in this project have been closed that way without
anyone meaning to -- #31, #35, #40, #61, #108, #114 -- across TEN COMMITS, because four
later commits re-closed an already-known issue by QUOTING the rule while documenting it.
Three of the ten used the same opening, `Filed rather than fixed: #N`, a sentence that
*declares the issue unfixed*; `ed1ab5e`'s runs:

    Filed rather than fixed: #114 effective_grades_for has no consumer in src/,

and closed #114 anyway, because `fixed:` sits next to it. #115, #116 and #117 in that
same sentence survived: no keyword sits next to them. That asymmetry is the whole
mechanism -- TOKEN ADJACENCY, not "mentioning an issue" -- and it is why this module
reports one reference from that sentence rather than four.

⇒ TWO OF THE SIX WERE FOUND BY THIS CHECK, not by a human. `293758c` wrote "Filed rather
than fixed: #108 (make max_pair_count exact), #109 ..." and closed #108 while #109-#112
survived; every document in this repo said FIVE. Running the finished guard over the
whole history is what turned it up -- which is the argument for the guard in one line.
The count is deliberately not written here as a number of commits scanned: that number
is stale at the next merge, and this repo has already lost four rounds to one fact kept
in two places.

THE RULE WAS ALREADY WRITTEN DOWN IN PROSE, in PROJECT-NOTES § "Standing rules", with
the mechanism explained and the explicit warning that "a colon in between does not save
you". It was written after occurrence four, and occurrences five and six were the next
two rounds to write that line. A seventh restatement is not a different intervention, so
this round ships a check that runs instead.

NO ATTEMPT IS MADE TO BE GITHUB. This deliberately matches slightly MORE than GitHub's
linker does (any of the nine keywords, any case, an optional colon, any run of blanks),
because the two failure directions are not equal: a false positive costs one
`--no-verify` and a moment's thought, while a false negative closes an open issue with a
sentence saying it is not closed, and nothing announces it. When in doubt, report.

WHAT IT CANNOT COVER, stated so nobody reads a green commit as full protection: GitHub
also parses PULL REQUEST descriptions, which no commit hook can see, and a message that
is already pushed cannot be un-linked by anything here. Issue 118 records that a CI
check on the PR body is the other half.

RUNS ON ANY PYTHON 3, DELIBERATELY, and the `from __future__` import below is what buys
it. The shipped hook falls back to a bare `python3` when `uv` is absent from the hook's
PATH -- which is the ordinary case for GUI git clients and many CI images -- and that
interpreter is whatever the OS ships (3.9.6 on current macOS). Without the import, the
PEP 604 annotation in `main` is evaluated at import time and raises `TypeError`, so the
fallback aborts EVERY commit with a traceback: the exact inverse of the guarantee the
hook's own comment makes. This module imports only the standard library for the same
reason.
"""
from __future__ import annotations

import pathlib
import re
import sys
from collections.abc import Sequence
from dataclasses import dataclass

# GITHUB'S NINE CLOSING KEYWORDS, all three verbs in all three inflections. Written out
# rather than built from stems (`close` + `s`/`d` would produce `closed` but also invite
# `fixs`), because the list is closed, short, and is the thing a reader checks first.
_KEYWORDS = ("close", "closes", "closed",
             "fix", "fixes", "fixed",
             "resolve", "resolves", "resolved")

# THE TWO SPELLINGS OF A REFERENCE. Bare `#N` is the commoner form in this repo's prose
# (roughly three to one across `docs/`), but the markdown link form
# `[#120](https://github.com/cairn-ehr/drugref/issues/120)` is used throughout ROADMAP
# and PROJECT-NOTES, so a body pasted out of either carries URLs -- and GitHub closes on
# those too. BOTH ARE MATCHED because missing either is a false negative, which is the
# direction this module refuses. The owner and repo are matched as "anything but a
# slash" rather than pinned to this repo, because a cross-repo close is still a close
# and this file is not the place to encode which repository it lives in.
_REFERENCE = r"(?:#|https?://github\.com/[^/\s]+/[^/\s]+/issues/)(?P<issue>\d+)"

# ADJACENCY IS THE ENTIRE PREDICATE. Keyword, then AT MOST an optional colon and blanks,
# then the reference. `\b` on the left stops `prefixes #12` matching through `fixes`;
# ordering the alternation longest-first stops `close` shadowing `closes`, since
# Python's `|` takes the first branch that matches rather than the longest.
#
# `[^\S\n]*` RATHER THAN `\s*` for the gap: `\s` includes the newline, so "... is
# fixed." followed by a line opening "#115 is next" would match ACROSS the break and
# reject a body GitHub leaves alone. Belt and braces, since `closing_references` also
# scans line by line -- but the class is the one that is correct on its own terms, and a
# later caller handing this pattern a whole message would otherwise inherit the bug.
_CLOSING = re.compile(
    r"\b(?P<keyword>" + "|".join(sorted(_KEYWORDS, key=len, reverse=True)) + r")"
    r"[^\S\n]*:?[^\S\n]*"
    + _REFERENCE, re.IGNORECASE)

# WHERE GIT'S OWN TRAILING BLOCK BEGINS. Git appends this block when it opens an editor,
# and deletes it again before the commit exists, so nothing in it can reach GitHub.
# Matching the block's OPENING LINE and discarding everything after it is the whole
# rule; see `_authors_text` for why it is not "drop every line starting with #".
_GIT_BLOCK = re.compile(
    r"^\s*#\s*(?:-+\s*>8\s*-+"                    # the scissors line `commit -v` writes
    r"|Please enter (?:the|a) commit message"     # the standard advice block
    r"|On branch "                                # ... and the status that follows it
    r"|Not currently on any branch)", re.IGNORECASE)


def _authors_text(message: str) -> str:
    """`message` up to where git's own boilerplate starts -- what the AUTHOR wrote.

    ⇒ THIS IS NOT "DROP EVERY LINE STARTING WITH `#`", AND THE DIFFERENCE IS A LIVE
    FALSE NEGATIVE. An earlier version did exactly that, justified by "git strips them
    before the commit exists, so none of it can ever reach GitHub". That is true of an
    EDITOR commit and FALSE of `git commit -m` and `-F`, where git's cleanup mode is
    `whitespace` rather than `strip` and `#` lines are kept verbatim. Measured:

        git commit -m $'feat: thing\\n\\n## Done\\n# fixes #999 heading\\n'

    stores the `#` line, GitHub closes 999, and the old guard reported nothing -- the
    silent close this module exists to prevent, produced BY the guard's own blind spot.
    That shape is this project's house style, not a contrivance: bodies are routinely
    pasted out of HANDOVER and ROADMAP markdown, whose headings begin with `#`, and the
    existing history already carries sixteen body lines that start with one.

    SO THE DEFAULT IS NOW TO SCAN, and only git's own block is removed. The block is
    always contiguous and always last, so its first line is all that must be found. A
    message with no such line -- every `-m` commit -- is scanned whole.

    WHAT STILL DEFEATS IT, and in which direction: the markers are English, so under a
    translated locale nothing matches and the boilerplate is scanned too. That costs a
    false positive on a branch named `fix/closes-118` -- one `--no-verify` -- rather
    than a silent close, which is the trade this module makes everywhere else. The
    scissors line is locale-independent and covers `commit -v` regardless.

    ALSO WHY `core.commentChar` NO LONGER MATTERS. An operator who sets it to `;` makes
    `#` lines ordinary text that git keeps; this rule scans them, which is correct,
    where the old first-character test discarded exactly the lines git was about to
    keep.
    """
    lines = message.splitlines()
    for index, line in enumerate(lines):
        if _GIT_BLOCK.match(line):
            return "\n".join(lines[:index])
    return message


@dataclass(frozen=True)
class ClosingReference:
    """One place in a commit message where GitHub would close an issue.

    `keyword` is preserved AS WRITTEN rather than normalised, because the report shows
    the author their own text: "you wrote `fixed: #114`" is actionable where "a closing
    keyword was found" sends them hunting.

    `issue` is the digits as a string, not an int. It is only ever printed and compared,
    and `str` keeps `#007` recognisable in the report as the thing that was typed.

    `line` is the PHYSICAL LINE the match sits on -- not the sentence, which may wrap
    across several -- carried so the report can show it back. `ed1ab5e`'s body runs to
    nearly three thousand characters and the offending token is two of them; an author
    told only "a closing keyword was found" goes hunting through prose they wrote a
    minute ago. The line is the largest unit that can be quoted back without guessing
    where a sentence began.
    """
    keyword: str
    issue: str
    line: str


def closing_references(message: str) -> tuple[ClosingReference, ...]:
    """Every issue reference in `message` that GitHub would treat as a close.

    PURE, and that is what makes issue 118's real inputs testable: the three commit
    bodies that caused this are quoted in `tests/test_commit_lint.py` and run through
    here directly, so the guard is pinned on text that really defeated the prose rule.

    Returns a tuple so a caller cannot accumulate into the result by accident, and so
    the empty case -- the ordinary one, which every good commit hits -- is a falsy `()`.
    """
    found = []
    for line in _authors_text(message).splitlines():
        for match in _CLOSING.finditer(line):
            found.append(ClosingReference(keyword=match.group("keyword"),
                                          issue=match.group("issue"), line=line))
    return tuple(found)


# THE REPORT. One string, built once, because an author reads it at the exact moment
# their commit was refused and their attention is on the commit, not on this file.
_REPORT = """\
drugref: this commit message would CLOSE {plural} on GitHub.

{offences}
GitHub closes an issue whenever one of its keywords -- close/closes/closed,
fix/fixes/fixed, resolve/resolves/resolved -- sits immediately before a reference to it.
A colon in between does not save you, and NEITHER DOES THE SENTENCE MEANING THE
OPPOSITE: `Filed rather than fixed: #114` closed 114, twice, in this repository.

  * If you did NOT mean to close it, write the number WITHOUT the `#`: {suggestion}.
  * If you DID mean to close it, commit again with `--no-verify`.
"""


def report(found: Sequence[ClosingReference]) -> str:
    """The author's rejection notice. PURE, so the wording is testable without a file.

    SPLIT OUT OF `main` FOR THE REASON `migration_guard.guard_message` states: a message
    reachable only through I/O is a message nobody asserts, and this one is read at the
    exact moment a commit was refused.

    THE COUNT IS OF DISTINCT ISSUES, not of matches. A body that writes `fixes #114`
    and later `closed #114` produces two references to ONE issue, and "would CLOSE 2
    issues" is simply false -- as is a suggestion line reading "issue 114, issue 114".
    Keyed on `int` so `#007` and `#7` collapse, displayed as the author spelled it,
    which is the same split `ClosingReference.issue` is a `str` for. Every OFFENCE is
    still listed: two places to fix are two lines to show.
    """
    distinct = {}
    for reference in found:
        distinct.setdefault(int(reference.issue), reference)
    return _REPORT.format(
        plural=("an issue" if len(distinct) == 1 else f"{len(distinct)} issues"),
        offences="".join(f"  line: {r.line}\n  closes: #{r.issue} "
                         f"(the keyword `{r.keyword}` sits next to it)\n\n"
                         for r in found),
        suggestion=", ".join(f"issue {r.issue}" for r in distinct.values()))


def main(argv: Sequence[str] | None = None) -> int:
    """Read the message file git hands a `commit-msg` hook; 1 rejects the commit.

    ARGV RATHER THAN READING `sys.argv` DIRECTLY, so the tests drive this by call
    instead of by subprocess -- `cli.main`'s reason, and the same payoff: a failure
    shows a real traceback rather than a captured exit code.

    PRINTS TO STDERR. Git shows the hook's output either way, but a rejection is
    diagnostic output and a caller redirecting stdout should still see why the commit
    did not happen.

    `errors="replace"` RATHER THAN A DECODE FAILURE. A message file that is not UTF-8 --
    reachable through `i18n.commitEncoding` -- used to raise `UnicodeDecodeError`, which
    exits non-zero and so rejects the commit with a traceback that never mentions what
    the hook wanted. The documented escape from a rejection is `--no-verify`, so an
    encoding fault trained the author to switch the guard off. A replacement character
    cannot fabricate a `#` or a digit, so the scan stays sound on the bytes that did
    decode, and the author sees either a real report or nothing.
    """
    path = pathlib.Path((argv if argv is not None else sys.argv[1:])[0])
    found = closing_references(path.read_text(encoding="utf-8", errors="replace"))
    if not found:
        return 0
    print(report(found), file=sys.stderr)
    return 1


if __name__ == "__main__":                                  # pragma: no cover
    # NOT COVERED BY THE SUITE, and the shipped hook is what covers it instead:
    # `tests/test_commit_lint.py` executes `.githooks/commit-msg` as a subprocess, which
    # reaches this line the way git does. A `pragma` here rather than a test that
    # re-implements the same two-line dispatch.
    sys.exit(main())
