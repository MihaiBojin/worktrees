# worktrees, as an agent plugin

Six skills for Claude Code and Codex over the worktrees CLI. Each is
`/worktrees:NAME` in Claude Code and `$worktrees:NAME` in Codex, and an agent
reaches for one on its own when a task matches its description.

| skill | does |
| --- | --- |
| `new-branch` | fetches, then starts a branch off the remote's head |
| `fast-forward` | brings this branch level with the head branch, through each rebase conflict |
| `ci-checks` | reads a pull request's checks as JSON, and waits for them |
| `describe-pr` | checks a pull request's title and body against its diff, and fixes them |
| `merge` | fixes failing checks, waits for green, merges, then reports what it fixed |
| `prune` | removes the worktrees and branches the CLI proves finished, after asking |

## Install

The plugin is listed in the `mihaibojin` marketplace, pinned to a release
of this repository.

Claude Code:

```shell
claude plugin marketplace add MihaiBojin/agent-plugins
claude plugin install worktrees@mihaibojin
```

Codex:

```shell
codex plugin marketplace add MihaiBojin/agent-plugins
codex plugin add worktrees@mihaibojin
```

## Requirements

Install the CLI from the same release:

```console
uv tool install git-worktrees
```

The skills need `jq`, and `gh` or `glab` signed in for the forge the remote
is on. The plugin installs none of them. Skills call `gw` from `$PATH`.

## What runs without asking

Each skill pre-approves the reads it makes, `gw sync` and `gw branch create`.
Two go further, because the extra step is the skill's job: `describe-pr`
edits a pull request's title and body, and `fast-forward` runs
`gw fast-forward` and its conflict loop. Nothing pre-approves a merge, a
push, a CI rerun or a removal: those reach the client's own permission
prompt.

## CI

`gw ci-checks --json` prints one snapshot: the review's identity, every check with
its link, and one `checksState`. `gw ci-checks --wait` returns once no check is
running, so an agent waiting on CI reads one result rather than one per
poll. For a failing check the skill reads only the failing part of that
job's log.

## Pull request descriptions

The title and body become the squash commit, so [one format](skills/describe-pr/format.md)
serves both: an imperative title in the history's convention, then one line
per change a reader can see in the diff. Every line has to be supported by
the diff, the commits or a linked issue. The skill changes only lines that
are wrong or missing, keeps `release-note` blocks, issue keywords and
harness footers as they were, and says in its reply what it changed.

## Merge

Asking to merge approves pushing fixes to each named pull request's branch,
correcting its description, and merging it once green. The skill works in
the worktree holding the branch, never the main checkout. It merges with
`.releasetools.yaml`'s `release.merge` or the first strategy the repository
allows of squash, merge and rebase; a squash keeps GitHub's ` (#NN)` subject
and the branch's commit trailers, and `--match-head-commit` refuses a head
that moved after CI passed. It never uses `--admin`, `--auto` or
`--delete-branch`, and reports a queued merge as queued. The report ends
with one row per problem: the pull request, what was wrong, what fixed it.

## Cleanup

The prune skill reads `gw prune --json` and `gw branch status --json`. Both
sync first once the last sync is a minute old, unless `WORKTREES_OFFLINE` is
set. If remote state is still never synced or older than 10 minutes, it asks
whether to run `gw sync`; it runs sync only on your answer. A previous
approval or refusal remains in force for that cleanup.

It lists eligible worktrees and loose branches, with proofs, and shows empty
and stash-blocked rows separately. Ignored-file blockers remain visible.
Those categories need explicit approval. `--yes` grants no deletion
permission, and the plugin never targets its current checkout.

After you choose, each worktree goes through `command gw remove` with its
exact quoted JSON path and only its approved `--delete` values. Selected
loose branches follow through `command gw branch prune`. Both commands
reassess first and can refuse a target that changed. The skill retains
target exclusions and category choices across a refresh.

The report distinguishes checkout removal, branch deletion and stash
outcomes, including partial failures. It keeps the CLI's exact restore
commands. Branch restore recreates a ref, not a worktree directory. Stash
restore remains possible until Git garbage collection prunes its unreachable
commit. Ignored files cannot be restored.

The plugin uses CLI proof and protections. It does not delete remote branches
or bypass a refusal with raw Git deletion or filesystem removal.

## Fast-forward

The fast-forward skill runs `gw fast-forward --json`, which is what the
shell plugin's `gwff` runs; an agent's shell has no plugin functions, so it
calls the binary. Arguments to `/worktrees:fast-forward` pass through, so
`/worktrees:fast-forward next-parser --from release-1.2` names the branch a
finished one moves to and where it starts. The merge skill runs it in the
checkout holding the head branch once its pull requests land.

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
