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

The plugin runs `gws`, `gwbs`, `gwp`, `gwbd` and `jq`. It installs none of
them.

## What it does

1. Runs `gws --json` and `gwbs --json` against the refs already here, and
   keeps only the rows marked `remove`, with their proof, plus counts of the
   rest.
2. Shows them in one table. A worktree whose branch sits on the head branch's
   tip, with no commits of its own, is marked: that is also what a worktree
   somebody has just started looks like.
3. Asks what to remove. Claude Code offers the choices as a question; Codex
   asks in plain text.
4. Removes each chosen worktree with `gwp --branch NAME --yes` and the chosen
   branches with `gwbd NAME... --yes`. Both assess again before removing.
5. Reports what was removed, what the CLI refused and why, and the
   `git branch NAME SHA` line that restores each branch.

## What it will not do

Remove anything before you answer, or anything the CLI did not prove. Delete a
remote branch. Run `git branch -D`, `git worktree remove` or `rm`. Pass
`--delete-ignored`, which deletes a worktree's gitignored files for good,
unless you ask for it.

## Versions

The plugin carries the version of the release it ships in.
`scripts/bump_version.py` writes it into `.claude-plugin/plugin.json`,
`.codex-plugin/plugin.json` and `plugin.json` along with every other copy, so
a client sees an update whenever the marketplace moves to a newer tag.
`plugin.json` is the [Agent Plugins](https://agent-plugins.org) manifest and
holds only the fields that format names.
