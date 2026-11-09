---
name: finalcheck
description: final pre-merge pass on an open PR (found via gh, run in its local worktree) — review, fix everything, then drive GitHub CI to green
disable-model-invocation: false
allowed-tools: Bash(git *), Bash(uv *), Bash(uv run pytest *), Bash(ruff *), Bash(psql *), Bash(gh *)
---
**Pick the PR and where it lives locally.**

1. If I named a PR, use it. Otherwise list open PRs: `gh pr list --state open --json number,url,headRefName,title`.
   None → stop and tell me. More than one and I didn't name one → ask which.
2. Find the local checkout of its `headRefName`: `git worktree list` (run from the main checkout — PR
   branches usually sit in sibling worktrees such as `../drugref-<topic>`, not in `main`). Use the worktree
   whose branch matches. If none, and the current checkout is clean, `git switch <headRefName>` (fetch
   first if it's not local). If the current checkout is dirty, stop and tell me rather than switching.
3. Do **all** of the following work in that directory: prefix every shell command with `cd <worktree> &&`
   (the shell may not keep `cd` across calls for paths outside the main repo), edit files by their path
   inside the worktree, and pass the PR number to `gh` (`gh pr checks <n>`), since the main checkout's
   branch is not the PR's. Tell the review/fix skills the worktree path and PR number.

Then run these steps in order on that PR.

1. **Review.** Invoke the `pr-review-toolkit:review-pr` skill via the Skill tool and let it finish.
2. **Fix.** Invoke the `fixall` skill via the Skill tool on that review's findings. It ends by committing
   and pushing into the PR; make sure that push actually happened before moving on.
3. **Drive CI to green.** Loop:
   - Watch the checks for the pushed head commit: `gh pr checks <n> --watch --fail-fast` (wait for checks to
     register if it reports none yet).
   - All green → done; report the PR URL and the final check list.
   - A check failed → read the cause: `gh run list --branch <headRefName> --limit 5`, then
     `gh run view <run-id> --log-failed`. Reproduce it locally (`uv run pytest`, `ruff check .`,
     `ruff format --check .` — whichever the failing job runs; see `.github/workflows/`), fix the root
     cause (TDD where it is a code bug), confirm it passes locally, commit with a message naming the CI
     failure, push, and loop again.
   - If the failure is not caused by the code (runner/infra outage, network flake, missing secret), rerun
     once with `gh run rerun <run-id> --failed`. If it fails the same way again, stop and report rather
     than changing code to work around it.
   - Never "fix" CI by skipping, xfail-ing or deleting tests, or by weakening lint/CI config, without my
     explicit permission. If the same failure survives three fix attempts, stop and report what you tried.
