"""Reject a commit message that closes a GitHub issue by accident (issue 118).

WHY THIS EXISTS, AND WHY IT IS CODE RATHER THAN A SENTENCE IN A STYLE GUIDE. GitHub
closes an issue when a merged commit body puts one of its closing keywords immediately
before a reference to it. SIX commits in this project have done that without meaning to
-- #31, #35, #40, #61, #108, #114 -- and three of them used the SAME SENTENCE:

    Filed rather than fixed: #114, #115, #116 and #117 ...

which *declares the issues unfixed* and closed #114 anyway, because `fixed:` sits next
to it. The other three survived: no keyword sits next to them. That asymmetry is the
whole mechanism -- TOKEN ADJACENCY, not "mentioning an issue" -- and it is why this
module reports one reference from that sentence rather than four.

⇒ THE SIXTH WAS FOUND BY THIS CHECK, not by a human, and it had gone uncounted for a
round: `293758c` wrote "Filed rather than fixed: #108 (make max_pair_count exact), #109
..." and closed #108 while #109-#112 survived. Every document in this repo said FIVE.
Running the finished guard over all 363 commits in the history is what turned it up --
which is the argument for the guard in one line.

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
"""
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

# THE TWO SPELLINGS OF A REFERENCE, and the second is the one this project actually
# writes: every issue in `docs/` is linked as `[#120](https://github.com/cairn-ehr/...)`,
# so a body composed from HANDOVER or ROADMAP prose carries URLs, not `#N`. The owner
# and repo are matched as "anything but a slash" rather than pinned to this repo,
# because a cross-repo close is still a close and this file is not the place to encode
# which repository it lives in.
_REFERENCE = r"(?:#|https?://github\.com/[^/\s]+/[^/\s]+/issues/)(\d+)"

# ADJACENCY IS THE ENTIRE PREDICATE. Keyword, then AT MOST an optional colon and blanks,
# then the reference. `\b` on the left stops `prefixes #12` matching through `fixes`;
# ordering the alternation longest-first stops `close` shadowing `closes`, since
# Python's `|` takes the first branch that matches rather than the longest.
#
# `[^\S\n]*` RATHER THAN `\s*` for the gap: `\s` includes the newline, so a body whose
# line ends in "... and this is fixed." followed by a line opening "#115 is next" would
# match ACROSS the line break and reject a body GitHub leaves alone. Blanks only.
_CLOSING = re.compile(
    r"\b(" + "|".join(sorted(_KEYWORDS, key=len, reverse=True)) + r")"
    r"[^\S\n]*:?[^\S\n]*"
    + _REFERENCE, re.IGNORECASE)


def _without_comments(message: str) -> str:
    """`message` with git's own comment lines removed, as git itself will remove them.

    THE FILE A `commit-msg` HOOK RECEIVES IS NOT THE COMMIT. It still holds the
    "# Please enter the commit message" block, the "# On branch <name>" line, and after
    a conflict a "# Conflicts:" list -- all of which git strips before the commit
    exists,
    so none of it can ever reach GitHub. A branch named `fix/closes-118` puts a closing
    keyword and a number on that status line through no fault of the author, and a guard
    that rejected it would be uninstalled within a week, taking the real check with it.

    KEYED ON THE FIRST CHARACTER, matching git's default `core.commentChar`. An operator
    who has changed that setting gets a guard that reads slightly more than git does,
    which is the harmless direction: it can only ever report, never miss.
    """
    return "\n".join(line for line in message.splitlines()
                     if not line.startswith("#"))


@dataclass(frozen=True)
class ClosingReference:
    """One place in a commit message where GitHub would close an issue.

    `keyword` is preserved AS WRITTEN rather than normalised, because the report shows
    the author their own text: "you wrote `fixed: #114`" is actionable where "a closing
    keyword was found" sends them hunting.

    `issue` is the digits as a string, not an int. It is only ever printed and compared,
    and `str` keeps `#007` recognisable in the report as the thing that was typed.

    `line` is the author's own sentence, carried so the report can show it back. A body
    like `ed1ab5e`'s runs to 300+ characters and the offending token is two of them; an
    author told only "a closing keyword was found" goes hunting through prose they wrote
    a minute ago.
    """
    keyword: str
    issue: str
    line: str


def closing_references(message: str) -> tuple[ClosingReference, ...]:
    """Every issue reference in `message` that GitHub would treat as a close.

    PURE, and that is what makes issue 118's real inputs testable: the two commit bodies
    that caused this are quoted verbatim in `tests/test_commit_lint.py` and run through
    here directly, so the guard is pinned on the exact text that defeated the prose rule
    rather than on a paraphrase of it.

    Returns a tuple so a caller cannot accumulate into the result by accident, and so
    the empty case -- the ordinary one, which every good commit hits -- is a falsy `()`.
    """
    return tuple(
        ClosingReference(keyword=m.group(1), issue=m.group(2), line=line) for line in
        _without_comments(message).splitlines() for m in _CLOSING.finditer(line))


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


def main(argv: Sequence[str] | None = None) -> int:
    """Read the message file git hands a `commit-msg` hook; 1 rejects the commit.

    ARGV RATHER THAN READING `sys.argv` DIRECTLY, so the tests drive this by call
    instead of by subprocess -- `cli.main`'s reason, and the same payoff: a failure
    shows a real traceback rather than a captured exit code.

    PRINTS TO STDERR. Git shows the hook's output either way, but a rejection is
    diagnostic output and a caller redirecting stdout should still see why the commit
    did not happen.
    """
    path = pathlib.Path((argv if argv is not None else sys.argv[1:])[0])
    found = closing_references(path.read_text(encoding="utf-8"))
    if not found:
        return 0
    print(_REPORT.format(
        plural=("an issue" if len(found) == 1 else f"{len(found)} issues"),
        offences="".join(f"  line: {r.line}\n  closes: #{r.issue} "
                         f"(the keyword `{r.keyword}` sits next to it)\n\n"
                         for r in found),
        suggestion=", ".join(f"issue {r.issue}" for r in found)),
        file=sys.stderr)
    return 1


if __name__ == "__main__":                                  # pragma: no cover
    # NOT COVERED BY THE SUITE, and the shipped hook is what covers it instead:
    # `tests/test_commit_lint.py` executes `.githooks/commit-msg` as a subprocess, which
    # reaches this line the way git does. A `pragma` here rather than a test that
    # re-implements the same two-line dispatch.
    sys.exit(main())
