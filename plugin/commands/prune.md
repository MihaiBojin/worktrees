---
name: prune
description: Remove the worktrees and branches the worktrees CLI proves finished, after asking
argument-hint: "[--no-fetch]"
allowed-tools:
  - Bash(gws:*)
  - Bash(gwbs:*)
  - Bash(gwp:*)
  - Bash(gwbd:*)
  - Bash(jq:*)
  - Bash(git status:*)
  - Bash(git worktree list:*)
  - Bash(git rev-parse:*)
  - Bash(git log:*)
  - Read
  - AskUserQuestion
---

Prune this repository with the worktrees skill. It carries the commands, the
jq filters, the question to ask and how to report. Nothing below repeats it.

`$ARGUMENTS` may say `--no-fetch`; pass it to every CLI command when it does.
Anything else in it names worktrees or branches the user wants considered:
offer only those.

Remove nothing until the user has answered.
