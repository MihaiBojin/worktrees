---
name: prune
description: Remove the worktrees and branches the worktrees CLI proves finished, after asking
argument-hint: "[NAME ...]"
allowed-tools:
  - Bash(gwbs:*)
  - Bash(gwp --json:*)
  - Bash(gwr:*)
  - Bash(gwbd:*)
  - Bash(command gwr:*)
  - Bash(command gwbd:*)
  - Bash(gw sync:*)
  - Bash(git remote:*)
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

`$ARGUMENTS` may name worktrees or branches the user wants considered: offer
only those.

Run `gw sync` only when the user says to, and remove nothing until the user
has answered.
