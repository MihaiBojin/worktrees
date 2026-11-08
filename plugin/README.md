# worktrees, as an agent plugin

`/worktrees:prune` assesses linked worktrees and loose local branches with
the worktrees CLI. It shows the head and sync state first, then asks which
targets and deletion categories you approve.

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

## Requirements

Install the CLI from the same release:

```console
uv tool install git-worktrees
```

The plugin needs `jq` too. It installs neither tool.

## Cleanup

The skill reads `gw prune --json` and `gw branch status --json`. These
commands stay offline. If remote state is never synced or older than 10 minutes, it asks
whether to run `gw sync`; it runs sync only on your answer. A previous
approval or refusal remains in force for that cleanup.

It lists eligible worktrees and loose branches, with proofs, and shows empty
and stash-blocked rows separately. Ignored-file blockers remain visible.
Those categories need explicit approval. `--yes` grants no deletion
permission, and the plugin never targets its current checkout.

After you choose, each worktree goes through `command gw remove` with its
exact quoted JSON path and only its approved `--delete` values. Selected
loose branches follow through `command gw branch prune`. Both commands reassess first and
can refuse a target that changed. The skill retains target exclusions and
category choices across a refresh.

The report distinguishes checkout removal, branch deletion and stash
outcomes, including partial failures. It keeps the CLI's exact restore
commands. Branch restore recreates a ref, not a worktree directory. Stash
restore remains possible until Git garbage collection prunes its unreachable
commit. Ignored files cannot be restored.

The plugin uses CLI proof and protections. It does not delete remote branches
or bypass a refusal with raw Git deletion or filesystem removal.

## Versions

The plugin carries the version of the release it ships in.
`scripts/bump_version.py` writes it into `.claude-plugin/plugin.json`,
`.codex-plugin/plugin.json` and `plugin.json` along with every other copy, so
a client sees an update whenever the marketplace moves to a newer tag.
`plugin.json` is the [Agent Plugins](https://agent-plugins.org) manifest and
holds only the fields that format names.
