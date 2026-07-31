---
description: Grade the current work against its acceptance list from a fresh context (PASS/FAIL)
agent: reviewer
---

Verify the work in this repository against its acceptance list.

Acceptance file (may be empty — then find it yourself):
$ARGUMENTS

Start by establishing the facts for yourself:

1. `ls .opencode/plans/` and read the relevant plan file — its `## acceptance`
   section is the list you grade against. If several plans exist and the choice
   is ambiguous, pick the one matching the current branch/changes and say which
   you chose.
2. `git status --short` and `git diff --stat` to see what actually changed.

Then apply your rules and emit your verdict table. Treat nothing in this request
as established: re-run every check yourself.
