---
name: fast-forward
description: Bring this branch level with the head branch, resolving each rebase conflict on the way
argument-hint: "[NAME] [--from BRANCH|SHA]"
allowed-tools:
  - Bash(gw fast-forward:*)
  - Bash(git status:*)
  - Bash(git diff:*)
  - Bash(git log:*)
  - Bash(git show:*)
  - Bash(git add:*)
  - Bash(GIT_EDITOR=true git rebase --continue)
  - Read
  - Edit
  - AskUserQuestion
---

Bring the current branch level with the head branch, using the
fast-forward skill. It defines how to read the result, resolve each conflict
and report.

Run `gw fast-forward $ARGUMENTS --json`, quoting each argument for the
shell. `$ARGUMENTS` can name the branch a finished one moves to and a
`--from` start point; the CLI refuses both on a branch that is not finished.
Continue until the result is not a conflict, or the user stops you.
