---
name: fixall
description: instructions to cleanup after code review
disable-model-invocation: false
allowed-tools: Bash(git *), Bash(uv *), Bash(uv run pytest *), Bash(ruff *), Bash(psql *), Bash(gh *)
---
Address all issues identified in the code review one by one. If fixing them appears manageable within this session, fix them now. If not, lodge the issue on github. Once all issues have been addressed, review the code changes thoroughly. If satisfied no issues left open, run the full test suite (`uv run pytest`, with `DRUGREF_TEST_DSN` set for the DB-gated tests) and confirm it is green. Update HANDOVER.md and ROADMAP.md ONLY if necessary to reflect these changes. Then commit and push the changes into the PR.
