# worktrees, as an agent plugin

`/worktrees:prune` asks the `worktrees` CLI which linked worktrees and local
branches are finished, shows the ones it proved in one table with the proof,
and removes the ones you pick. The decision is the CLI's, so it is the same in
every repository, and the agent reads a filtered summary of about 4 KB for 30
branches rather than the full assessment.

## Install

The plugin is listed in the `release-tools` marketplace, pinned to a release
of this repository.

Claude Code:

```shell
claude plugin marketplace add releasetools/agent-plugins
claude plugin install worktrees@release-tools
```

Codex:

```shell
codex plugin marketplace add releasetools/agent-plugins
codex plugin add worktrees@release-tools
```

## What it needs

The commands, from the same release:

```shell
uv tool install git-worktrees
```

The plugin runs `gwp`, `gwbs`, `gwr`, `gwbd`, `gw sync` and `jq`. It
installs none of them.

## What it does

1. Runs `gwp --json` and `gwbs --json` against the refs already here, and
   keeps the eligible rows with their proof, the `empty` rows and the rows
   held back by stashes, plus counts of the rest.
2. Says what the head is and how fresh the last `gw sync` is. When the
   repository was never synced, or the sync is more than ten minutes old, it
   asks whether to run `gw sync` first, and runs it only on a yes.
3. Shows the eligible rows in one table, then the `empty` and stash-held
   rows as two short lists.
4. Asks what to remove. Claude Code offers the choices as a question; Codex
   asks in plain text. `empty` and stash-held rows are removed only when you
   name them.
5. Removes each chosen worktree with `command gwr 'PATH' --yes` and the
   chosen branches with `command gwbd NAME... --yes`, adding `--delete
   empty`, `--delete stashes` or `--delete ignored-files` only for the rows
   you approved in that list. Both assess again before removing.
6. Reports what was removed, what the CLI refused and why, and the `git
   branch NAME SHA` and `git stash store` lines that restore each branch
   and stash.

## What it will not do

Remove anything before you answer, or anything the CLI did not prove. Run
`gw sync` without asking. Delete a remote branch. Run `git branch -D`, `git
worktree remove`, `git stash drop` or `rm`. Pass `--keep-branch`, or
`--delete all`. Pass `--delete ignored-files`, which deletes a worktree's
gitignored files for good, unless you ask for it.

## Versions

The plugin carries the version of the release it ships in.
`scripts/bump_version.py` writes it into `.claude-plugin/plugin.json`,
`.codex-plugin/plugin.json` and `plugin.json` along with every other copy, so
a client sees an update whenever the marketplace moves to a newer tag.
`plugin.json` is the [Agent Plugins](https://agent-plugins.org) manifest and
holds only the fields that format names.
