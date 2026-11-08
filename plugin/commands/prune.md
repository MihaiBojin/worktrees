---
name: prune
description: Assess worktrees and loose branches, then remove the targets and categories the user approves
argument-hint: "[NAMES]"
allowed-tools:
  - Bash(gw sync)
  - Bash(gw list --json)
  - Bash(gw list --table)
  - Bash(gw prune --json)
  - Bash(gw branch status:*)
  - Bash(command gw remove:*)
  - Bash(command gw branch delete:*)
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
what the user approves, using the commands specified in the skill.
