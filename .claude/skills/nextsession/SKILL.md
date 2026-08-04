---
name: nextsession
description: instructions what to do when resuming or starting a session
allowed-tools: Bash(git *), Bash(uv *), Bash(uv run *), Bash(uv sync *), Bash(uv add *), Bash(uv run pytest *), Bash(ruff *), Bash(psql *), Bash(gh *)
---
read docs/HANDOVER.md and follow the instructions. Ask me if you have any questions. Remember our general coding rules:
1. preference for pure functions in reusable modules over complex code
2. test driven development paradigm (write the failing test first, then the code)
3. understandable inline documentation even for junior contributors mandatory
4. Aiming at keeping code files under 500 lines of code wherever feasible; consider refactoring where possible when files getting too large
5. Avoid technical debt - if you find an error, fix it when possible, else lodge it as issue on github
6. all tests must pass before committing, unless explicit permission is given by me. DB-gated tests need `DRUGREF_TEST_DSN` set (see docs/HANDOVER.md for the current DSN); run them with `uv run pytest`.
7. Licensing is non-negotiable: all code we produce is AGPL-3.0, and every dependency AND every bundled reference-data source must be AGPL-3.0-compatible (check the licence BEFORE adding/bundling — an incompatible licence is a blocker, not a cleanup-later item). Encumbered sources attach only as node-local, separately-licensed plug-ins, never bundled.
8. Before you start working, make sure that HANDOVER.md, PROJECT-NOTES.md and ROADMAP.md represent the
   current state of progress and are up to date. If not, update them before you start working.
9. When you are done with your work, update all three to reflect the current state. HANDOVER.md is the
   volatile one — keep it under ~120 lines, focused on what still needs to be done. PROJECT-NOTES.md and
   ROADMAP.md are edited IN PLACE and are under no line bound; do not compress them to hit a number. If you
   are not sure how to do this, ask me. Do NOT update CLAUDE.md as part of routine session wrap-up.
10. when the task is completed, commit all, push, and open a PR to the main branch. Make sure to link the PR to the relevant issue on github if applicable and include a clear description of the changes made and any relevant context for reviewers. If you are not sure how to do this, ask me.
