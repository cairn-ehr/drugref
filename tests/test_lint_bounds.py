"""The two column bounds this repo enforces, pinned by DRIVING ruff rather than by
reading pyproject.toml.

Issue 79. Until this round `tests/**` carried a blanket `["E501"]` per-file-ignore, so
tests/ had no column bound at all -- 415 lines over 88 by the time it was re-measured
here, against 0 in src/. That was never drift: the ceiling across the whole suite is
**119 characters**, exactly what it was when the carve-out was written and when the issue
was filed, while the count over 88 went 324 -> 334 -> 415. A number that grows while the
ceiling stands still is the signature of a real convention nobody wrote down. This round
writes it down (120) and enforces it -- the second of the two ways issue 79 offered to
close, chosen over reflowing 415 lines because that trade (#63) has already been rejected
twice for the same reason: it buries the change's content and makes `git log -p` useless.

WHY THIS IS A TEST AND NOT A COMMENT. Two standing rules meet here.

  * "A CONFIG BLOCK UNDER THE WRONG HEADER IS SILENT IN BOTH TOOLS" (issue 66):
    `line-length = 88` sat inside `[tool.pytest.ini_options]` for a whole draft and
    nothing failed, because 88 is also ruff's own default -- lint LOOKED configured. The
    only symptom was a pytest warning. So an effective setting has to be checked against
    the TOOL, never read off the file.
  * Ruff resolves configuration HIERARCHICALLY: `tests/ruff.toml` governs the files below
    it, and none of that is visible from pyproject.toml. Someone reading only the root
    config would conclude tests/ is bounded at 88, which is false; someone reading only
    `tests/ruff.toml` would not know whether `extend` actually carried the rule selection
    down, which is the failure mode with no symptom at all.

EVERY CASE HERE IS A POSITIVE CONTROL. Each asserts a line that MUST be reported, beside
one at the same bound that must not be -- because a bound asserted only by what passes is
equally satisfied by a lint run that checks nothing whatsoever. That is this project's
"a test whose expected result is over-determined cannot fail" rule applied to the linter
itself, and it is the check the previous carve-out's comment described doing by hand with
a throwaway probe file and then did not keep.

`--stdin-filename` is what makes the probes hypothetical: ruff resolves configuration for
the path it is GIVEN, so these drive the real hierarchy without writing a file into either
tree (a probe file left behind in src/ would be linted by CI forever).
"""
import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

# `python -m ruff`, not `ruff`. Issue 66's other lesson: before ruff was pinned into the
# dev group, `uv run ruff` resolved to whatever happened to be on the developer's PATH.
# Going through the interpreter running the tests pins it to the same environment pytest
# was installed into, so this cannot silently grade a different ruff than CI runs.
_RUFF = [sys.executable, "-m", "ruff", "check", "--no-cache", "--output-format", "concise"]

_CODE = re.compile(r"^\S+:\d+:\d+: ([A-Z]+\d+)", re.MULTILINE)


def _codes(path, source):
    """Rule codes ruff reports for SOURCE, linted as though it lived at PATH.

    Returns a set, so a test says what it means ("E501 is reported") without depending on
    how many other things the probe happens to trip.
    """
    proc = subprocess.run(
        _RUFF + ["--stdin-filename", path, "-"],
        input=source, capture_output=True, text=True, cwd=REPO_ROOT,
    )
    # 0 = clean, 1 = violations found. ANYTHING else is ruff refusing to run -- an
    # unreadable config, a bad `extend` path, an unknown setting -- and it must surface
    # as a loud failure here rather than as an empty result set, which would read
    # identically to "the bound is not enforced" and pass every negative assertion below.
    assert proc.returncode in (0, 1), (
        f"ruff exited {proc.returncode} for {path}:\n{proc.stdout}\n{proc.stderr}")
    return set(_CODE.findall(proc.stdout))


def _line_of(width):
    """A syntactically valid Python line of exactly WIDTH characters."""
    prefix = "x = '"
    body = "a" * (width - len(prefix) - 1)
    line = f"{prefix}{body}'"
    assert len(line) == width, len(line)
    return line + "\n"


# --------------------------------------------------------------------------------------
# src/ -- 88, unchanged by this round and asserted here so the change is provably local
# --------------------------------------------------------------------------------------

def test_src_is_bounded_at_88():
    assert "E501" not in _codes("src/drugref/probe.py", _line_of(88))
    assert "E501" in _codes("src/drugref/probe.py", _line_of(89))


# --------------------------------------------------------------------------------------
# tests/ -- 120, the bound this round introduces
# --------------------------------------------------------------------------------------

def test_tests_are_bounded_at_120():
    assert "E501" not in _codes("tests/probe.py", _line_of(120))
    assert "E501" in _codes("tests/probe.py", _line_of(121))


def test_the_two_bounds_actually_differ():
    """The 89-character line that fails in src/ must PASS in tests/.

    Without this the suite would still be green if `tests/ruff.toml` vanished and tests/
    fell back to the root's 88 -- every other assertion here is compatible with one
    bound applied everywhere. This is the case that distinguishes the two trees, and it
    is the reason the carve-out existed at all.
    """
    over_src_bound = _line_of(89)
    assert "E501" in _codes("src/drugref/probe.py", over_src_bound)
    assert "E501" not in _codes("tests/probe.py", over_src_bound)


def test_tests_still_inherit_the_root_rule_selection():
    """`extend` carries `select = ["E", "F", "W"]` down; only the BOUND is different.

    W291 is the discriminator on purpose. Ruff's DEFAULT selection is ["E4","E7","E9","F"],
    so a probe tripping F401 or E402 would prove nothing -- those fire with no
    configuration at all, and the old carve-out's comment claimed exactly that check as
    its evidence. W is absent from the default set, so trailing whitespace being reported
    under tests/ is only explicable by the root selection having reached it.

    What this catches: a `tests/ruff.toml` that sets `line-length` and forgets `extend`.
    That file would still bound the columns -- so every assertion above would pass -- while
    silently dropping W and most of E across the entire suite.
    """
    assert "W291" in _codes("tests/probe.py", "x = 1  \n")
    assert "W291" in _codes("src/drugref/probe.py", "x = 1  \n")


def test_the_blanket_exemption_is_gone():
    """E501 is ENFORCED under tests/, not ignored -- issue 79's actual subject.

    Distinct from the bound tests above in the way that matters: a surviving
    `"tests/**" = ["E501"]` in per-file-ignores would suppress the code no matter what
    `line-length` said, so `_line_of(121)` would come back clean and the bound would be
    decoration. Stated separately so the failure names the cause.
    """
    assert "E501" in _codes("tests/probe.py", _line_of(200))
