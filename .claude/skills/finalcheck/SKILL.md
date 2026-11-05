---
name: finalcheck
description: final pre-merge pass on the current PR — review, fix everything, then drive GitHub CI to green
disable-model-invocation: false
allowed-tools: Bash(git *), Bash(uv *), Bash(uv run pytest *), Bash(ruff *), Bash(psql *), Bash(gh *)
---
Run these steps in order on the PR for the current branch (find it with `gh pr view --json number,url`;
if there is none, stop and tell me).

1. **Review.** Invoke the `pr-review-toolkit:review-pr` skill via the Skill tool and let it finish.
2. **Fix.** Invoke the `fixall` skill via the Skill tool on that review's findings. It ends by committing
   and pushing into the PR; make sure that push actually happened before moving on.
3. **Drive CI to green.** Loop:
   - Watch the checks for the pushed head commit: `gh pr checks --watch --fail-fast` (wait for checks to
     register if it reports none yet).
   - All green → done; report the PR URL and the final check list.
   - A check failed → read the cause: `gh run list --branch "$(git branch --show-current)" --limit 5`, then
     `gh run view <run-id> --log-failed`. Reproduce it locally (`uv run pytest`, `ruff check .`,
     `ruff format --check .` — whichever the failing job runs; see `.github/workflows/`), fix the root
     cause (TDD where it is a code bug), confirm it passes locally, commit with a message naming the CI
     failure, push, and loop again.
   - If the failure is not caused by the code (runner/infra outage, network flake, missing secret), rerun
     once with `gh run rerun <run-id> --failed`. If it fails the same way again, stop and report rather
     than changing code to work around it.
   - Never "fix" CI by skipping, xfail-ing or deleting tests, or by weakening lint/CI config, without my
     explicit permission. If the same failure survives three fix attempts, stop and report what you tried.
