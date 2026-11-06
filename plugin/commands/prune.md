---
name: prune
description: Assess worktrees and loose branches, then remove the targets and categories the user approves
argument-hint: "[NAMES]"
allowed-tools:
  - Bash(gw:*)
  - Bash(gwp:*)
  - Bash(gwbs:*)
  - Bash(gwl:*)
  - Bash(command gwr:*)
  - Bash(command gwbd:*)
  - Bash(jq:*)
  - Bash(git remote:*)
  - Bash(git rev-parse:*)
  - Read
  - AskUserQuestion
---

Prune this repository with the worktrees skill. It defines the assessment
pipelines, sync question, target/category approval and outcome reporting.

`$ARGUMENTS` names worktrees or branches to consider. Keep that scope and any
existing choices about sync, targets or deletion categories. Remove only
what the user approves, using the command binaries specified in the skill.
