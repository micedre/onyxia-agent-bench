# Completion contract — your work is FAILED until proven otherwise

You do not decide that a task is done. Evidence does. Until you can point at a
command **you actually ran in this session** whose **output you pasted**, every
acceptance item is FAILED. "Should work", "the logic is correct", "the tests
would pass" are all FAILED.

This contract binds you as soon as you have changed a file or produced an
artifact. A question, an explanation or a read-only investigation is exempt —
answer those directly.

## 1. Find the acceptance list

Every deliverable has one. Look, in this order:

1. `.opencode/plans/<slug>.md` — written by the `plan` agent. **Read it before
   you touch any code**; it also holds the evidence log below.
2. Otherwise, the `## 📋 specification` block in this conversation.
3. Otherwise, write one yourself — 3 to 7 checkable items — and show it to the
   user before you start. A task with no acceptance list is a task you cannot
   finish: ask.

## 2. Evidence rules

- One acceptance item = one command = one pasted output. Not a paraphrase of the
  output: the output.
- **Never** state that tests pass, that a lint is clean, that a render
  succeeded, or that a file exists without having run the command in this
  session. If you did not run it, write `NOT VERIFIED` and say why.
- **Never** invent, round or reconstruct a command output, a row count, a
  metric, a file listing or an error message. If you need it, run the command
  again. This extends AGENTS.md's "Data integrity" rule from report figures to
  *everything you assert*.
- A stub, a `pass`, a `TODO`, a hardcoded return value, a commented-out
  assertion or a skipped test does **not** satisfy an item. Say the item is
  FAILED and why.
- Exit status is evidence; the absence of a traceback is not. Prefer
  `cmd && echo EXIT_OK || echo EXIT_FAIL`.
- Bound the output so the part that matters survives truncation:
  `uv run pytest -q 2>&1 | tail -n 30`, `uv run ruff check . 2>&1 | tail -n 40`,
  `uv run quarto render report.qmd 2>&1 | tail -n 25`,
  `kubectl logs <pod> --tail=100`.
  When output *is* truncated, OpenCode saves the full text and tells you the
  path — `grep` that file. Do not re-run hoping for less output.

## 3. Keep the evidence log on disk

Your context window will not survive a long task, and a compaction does not
preserve details. `.opencode/plans/<slug>.md` is the handover, not your memory.

After each acceptance item flips to satisfied, append:

    ## evidence
    - [x] <acceptance item>
          cmd:  uv run pytest -q tests/test_ingest.py
          out:  12 passed in 3.4s
    - [ ] <acceptance item>  — BLOCKED: <one line>

and keep this current:

    ## state
    step:  <what you are doing now>
    files: <files touched so far>
    next:  <the single next action>

**On any resume, and immediately after a compaction, re-read this file before
doing anything else.**

## 4. Before you claim done

1. Re-read the acceptance list.
2. Re-run every check cleanly, in one go. Paste the output.
3. Delegate to `@reviewer`, passing **only**: the path to the acceptance file,
   and `git diff --stat`. Do not summarise your own work to it. Do not tell it
   what you believe passes — it is grading you, and it re-derives the list from
   disk on purpose.
4. If the reviewer returns **FAIL**, the task is not done. Fix the specific
   failed items and go back to 3. After **two** failed rounds, stop and report
   to the user: what fails, the evidence, and what you tried.
5. Only after **PASS** may you tell the user the work is complete, and your
   final message must end with:

       ## Completion report
       | # | acceptance item | verdict | evidence (command → key output line) |
       |---|-----------------|---------|--------------------------------------|
       reviewer: PASS

   If the user explicitly waived review, write `reviewer: waived by user`.
   Never omit that line.

## 5. When you are stuck

If the same command has failed about 3 times, STOP — do not try a 4th
variation. Report what you tried, the exact error and your best hypothesis, and
ask. OpenCode's own repeated-call guard will start asking for confirmation
around the same point; treat that prompt as a signal that you are looping, not
as an obstacle to work around.
