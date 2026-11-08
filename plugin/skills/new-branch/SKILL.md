---
name: new-branch
description: >
  Start a branch for new work from the freshly fetched head branch with
  `gw branch create`. Use when starting a task, a feature or a fix that
  needs its own branch, or when the user asks for a new branch.
argument-hint: "[NAME]"
allowed-tools:
  - Bash(gw sync)
  - Bash(gw branch create:*)
---

# Start a branch

In Codex, `$ARGUMENTS` means the arguments supplied with the skill. `gw`
comes from `uv tool install git-worktrees`; if it is missing, report that
and stop.

## Name it

Use the name the user gave. Otherwise name the work, in kebab-case, two to
four words: `fix-parser-crash`, `add-ci-command`. Follow a convention the
repository's instructions or its existing branches show, such as a
`feat/` prefix.

## Fetch, then branch

```sh
gw sync
gw branch create 'NAME' --json
```

`gw sync` first, every time, so the branch starts on what the remote has
now. If it fails on the default remote, say so and ask whether to branch
from the last fetched head instead. Under `WORKTREES_OFFLINE` it refuses at
exit 3: branch from the last fetched head and say that it was not fetched.

`gw branch create` branches off the remote's head with `--no-track`, so a
plain `git push` cannot land on the head branch, and checks it out here.
Uncommitted changes come along. It refuses a name that is already a branch,
naming `git switch NAME`, and a name git will not take. Report the refusal
and pick another name only if the user left the choice to you.

Report the branch, the ref it came from and the commit, from `branch`,
`base` and `sha`.

## A branch in its own worktree

When the user wants the work in a worktree of its own, rather than this
checkout, `command gw add 'NAME' --json` creates the branch the same way in
a new directory and prints its `path`. Work there from then on.
