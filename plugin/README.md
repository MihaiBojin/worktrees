# worktrees, as an agent plugin

`/worktrees:prune` assesses linked worktrees and loose local branches with
the worktrees CLI. It shows the head and sync state first, then asks which
targets and deletion categories you approve.

`/worktrees:fast-forward` brings the branch you stand on level with the head
branch through `gw fast-forward --json`, and resolves each conflict the
rebase stops at.

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

The skill reads `gw prune --json` and `gw branch status --json`. Both sync
first once the last sync is a minute old, unless `WORKTREES_OFFLINE` is set.
If remote state is still never synced or older than 10 minutes, it asks
whether to run `gw sync`; it runs sync only on your answer. A previous
approval or refusal remains in force for that cleanup. The skill and
`/prune` run `gw sync`, `gw list --json`, `gw list --table`,
`gw prune --json`, `gw branch status --json`, `jq` and `git remote` without
a permission prompt.

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

## Fast-forward

The fast-forward skill and `/fast-forward` run `gw fast-forward --json`,
which is what the shell plugin's `gwff` runs; an agent's shell has no plugin
functions, so they call the binary. Arguments to `/fast-forward` pass
through, so `/fast-forward next-parser --from release-1.2` names the branch a
finished one moves to and where it starts.

The CLI decides what happens: fast-forward, rebase, or a new branch when
this one is already merged. When the rebase stops, the skill reads each
conflicted file and what each side changed, edits the file to carry both,
`git add`s it, runs `GIT_EDITOR=true git rebase --continue` and runs the
command again, until the branch is level. It asks you when the two sides
cannot both be kept, and before touching a rebase that was already stopped
when it started, such as one paused in `git rebase -i`. It stages only the
conflicted files. It reports each commit the rebase stopped at and how each
file was resolved.

Those commands, `git status`, `git diff`, `git log` and `git show` run
without a permission prompt. `git rebase --abort` runs only when you ask, and
the skill never runs `git rebase --skip`, which drops a commit. A refusal
from the CLI, such as uncommitted changes, is reported and not worked
around.

## Versions

The plugin carries the version of the release it ships in.
`scripts/bump_version.py` writes it into `.claude-plugin/plugin.json`,
`.codex-plugin/plugin.json` and `plugin.json` along with every other copy, so
a client sees an update whenever the marketplace moves to a newer tag.
`plugin.json` is the [Agent Plugins](https://agent-plugins.org) manifest and
holds only the fields that format names.
