# worktrees

[![Tests](https://github.com/MihaiBojin/worktrees/actions/workflows/tests.yml/badge.svg)](https://github.com/MihaiBojin/worktrees/actions/workflows/tests.yml)
[![PyPI](https://img.shields.io/pypi/v/git-worktrees)](https://pypi.org/project/git-worktrees/)

`gw` creates Git worktrees and removes finished branches after checking for
unmerged work. It recognizes squash merges and lists ignored files before
deleting a checkout. The fish and zsh plugins let you switch worktrees
without typing paths.

## Install

Install [uv](https://docs.astral.sh/uv/getting-started/installation/), then
install the commands:

```console
uv tool install git-worktrees
```

The package requires Python 3.11 or newer and has no Python runtime
dependencies. Use Git 2.39 or newer for squash-merge detection. Partial
clones have [additional Git version requirements](docs/REFERENCE.md#sync-cache).

The install provides `gw` and its long name, `worktrees`. If uv reports that
its executable directory is missing from `PATH`, run `uv tool update-shell`
and restart your shell. Upgrade the commands with `uv tool upgrade git-worktrees`.

The commands work from any shell. Install the matching fish or zsh plugin
for completions, shortcuts and directory changes. Pin the plugin to the
release that `gw version` reports; an unpinned plugin follows `main`, which
can contain unreleased changes.

### fish

With [Fisher](https://github.com/jorgebucaran/fisher) installed, run:

```fish
fisher install MihaiBojin/worktrees@v0.13.4
```

To change release, edit the tag in `$__fish_config_dir/fish_plugins`, then
run `fisher update`. Use the same procedure to pin an untagged installation.

### zsh

With [Antidote](https://antidote.sh/) installed, add this line to its plugin
list, normally `~/.zsh_plugins.txt`, and open a new shell:

```text
MihaiBojin/worktrees branch:v0.13.4 path:zsh/plugins/worktrees
```

Completion needs `compinit`. If your shell configuration does not run it,
add this after the plugin loads:

```zsh
autoload -Uz compinit && compinit
```

To change the tag, first remove Antidote's clone:

```zsh
antidote purge MihaiBojin/worktrees
```

Then edit the tag in the plugin list and open a new shell. Editing the list
after the purge makes Antidote rebuild its generated bundle.

### Check the installation

Run `gw --help`. With matching command and shell releases, its output
includes:

```console
worktrees 0.13.4: git worktree commands that refuse to lose work
The shell plugin here is 0.13.4, the same release as these commands.
```

Upgrade the commands and the shell plugin together. For development from a
clone, see [Working on a checkout](docs/DEVELOPMENT.md#working-on-a-checkout).

## Quickstart

Run commands inside the repository you want to manage. The repository must
have a commit and a resolvable [head branch](docs/REFERENCE.md#head-and-evidence),
usually the default remote's default branch, or local `main` or `master`.

Create a worktree on a new branch named `fix-parser`. This example works in
bash and zsh with or without the shell plugin:

```sh
cd "$(command gw add fix-parser -q)"
git branch --show-current
```

The second command prints:

```text
fix-parser
```

For a main checkout at `/path/to/repo`, the new checkout is at
`/path/to/.worktrees/fix-parser/repo`. With the fish or zsh plugin loaded,
`gw add fix-parser` creates the checkout and changes directory in one call.
If the branch already exists, `gw add` uses it; a branch that exists only
on the remote needs confirmation or `--yes`.

Use `gw list` to choose another worktree. Type to filter, use the arrow keys
to move, and press enter to select. `gw list fix-parser` selects a unique
match directly. Without a terminal, `gw list` prints metadata. The shell
plugin performs the directory change.

## Commands

`gw --help` lists every command and shortcut. Each command's `--help`
describes its flags. The guides cover these tasks:

| Command | Task |
| --- | --- |
| [`gw add NAME [BASE]`](docs/commands/add.md) | Create a worktree for a new or existing branch |
| [`gw list [QUERY]`](docs/commands/list.md) | Choose a worktree or list matching checkouts |
| [`gw select-main`](docs/commands/list.md#select-main) | Go to the worktree on the head branch |
| [`gw sync`](#head-and-synchronization) | Refresh remote refs and GitHub or GitLab evidence |
| [`gw prune`](#assess-and-remove) | Assess worktrees and remove eligible ones after confirmation |
| [`gw remove [NAME\|PATH]`](#assess-and-remove) | Assess or remove one worktree |
| [`gw branch status [NAME ...]`](docs/commands/branch-status.md) | Assess branches without worktrees |
| [`gw branch prune [NAME ...]`](docs/commands/branch-prune.md) | Delete eligible branches without worktrees |
| [`gw branch create NAME`](docs/commands/branch-create.md) | Start a branch in the current checkout |
| [`gw fast-forward [NAME]`](docs/commands/fast-forward.md) | Update a branch, or continue from a finished branch on a new one |
| [`gw push`](docs/commands/push.md) | Push with checks for remote work and previously merged commits |
| [`gw ci-checks [NUMBER]`](docs/commands/ci-checks.md) | Read or wait for checks on a GitHub pull request or GitLab merge request |
| [`gw merge NUMBER...`](docs/commands/merge.md) | Squash checked GitHub pull requests, including native stacks |
| [`gw stack-link NUMBER NUMBER ...`](docs/commands/stack-link.md) | Link GitHub pull requests into a native stack |
| [`gw preflight`](docs/commands/preflight.md) | Check repository and plugin setup |
| `gw version` | Print the installed version |

A shortcut such as `gwa` is a shell function that calls `gw add`. Only `gw`
and `worktrees` are installed as executables.

## Assess and remove

Preview worktree removal as JSON:

```console
gw prune --json
```

The `verdicts` array gives each worktree's `reason`, `eligible` status and
`protections`. Use `eligible` to decide whether removal is permitted.
`gw prune --table` prints the assessment as text. Neither preview removes
worktrees or branches; assessments can save proof caches and sync first.

WARNING: Copy any ignored files that you need before removal. Removing a
worktree deletes its ignored files, including virtual environments and
build output. Git cannot restore those files.

On a terminal, assess and confirm eligible removals with:

```console
gw prune
```

The command lists each checkout, ignored path and branch that it will
delete. It prints restore commands for branches and approved stashes.
Press `y` to confirm; any other key cancels. The command reassesses before
removal and refuses targets that changed.

`gw remove NAME` applies the same checks to one worktree. Use a quoted
absolute path when names are ambiguous. Without a terminal, or with
`--json` or `--table`, both commands assess only. `--yes` approves removal
but grants no additional deletion permission. Removing the current
worktree always needs a second terminal confirmation.

The main checkout and dirty worktrees stay. Untracked files, active
operations and submodule stores also block removal. The branches `main`,
`master` and the head branch cannot be deleted. Other protections require
explicit permission:

| Flag | Permits |
| --- | --- |
| `--delete empty` | An unused branch and its checkout |
| `--delete closed` | A branch whose request closed without merging its current tip |
| `--delete stashes` | Stashes associated with the branch |
| `--delete reflog` | Commits held only by reflogs that removal deletes |
| `--delete stale-lock` | Unlocking a worktree whose recorded process has exited |
| `--delete all` | All the categories above |

Repeat `--delete` to combine categories. `--keep-branch` retains the branch
and its stashes while removing an otherwise unprotected checkout. It
accepts only `--delete reflog` and `--delete stale-lock`; a detached
checkout still needs proof that its commits are finished.

See [protections and permissions](docs/REFERENCE.md#protections-and-permissions)
for the checks, and [deletion and recovery](docs/REFERENCE.md#deletion-and-recovery)
for partial failures and restore commands. Restore branches or stashes
before Git garbage collection removes their unreachable commits.

### Shortcuts

The shell plugins provide these removal shortcuts:

| Shortcut | Runs |
| --- | --- |
| `gwp` | `gw prune` |
| `gwp!` | `gw prune --yes` |
| `gwpa` | `gw prune --delete all` |
| `gwpa!` | `gw prune --delete all --yes` |
| `gwr` | `gw remove` |
| `gwr!` | `gw remove --yes` |
| `gwrk!` | `gw remove --keep-branch --yes` |

## Head and synchronization

`gw sync` fetches every remote and asks GitHub or GitLab about local branch
tips. GitHub needs an authenticated `gh`; GitLab needs an authenticated
`glab`. With several remotes, set Git's `checkout.defaultRemote` to select
the remote used for assessments.

Assessments sync first when the default remote has never synced or its
last sync is at least 300 seconds old. `WORKTREES_SYNC_TTL` changes that
interval in whole seconds. A failed sync warns and the assessment proceeds
with available evidence. `gw list` reads local worktree records without
syncing.

`gw fast-forward` fetches the default remote when sync is due and reads
fresh request evidence when needed. It can fetch again when a merged
request names a commit missing locally. `gw push` fetches on every run.
Both refuse when a required fetch or request query fails.

Set `WORKTREES_OFFLINE=1` to prevent network access. Local assessment and
navigation still work; commands that require the network refuse. See
[head and evidence](docs/REFERENCE.md#head-and-evidence) and
[sync cache](docs/REFERENCE.md#sync-cache) for fallback rules and partial
sync outcomes.

## Output and JSON

Results go to stdout; diagnostics and prompts go to stderr. JSON results
are objects with `head` and `lastSync`. Worktree and branch assessments put
rows in `verdicts`; navigation puts rows in `worktrees`.

With [jq](https://jqlang.org/) installed, list paths or select eligible rows:

```console
gw list --json | jq -r '.worktrees[].path'
gw prune --json | jq '.verdicts[] | select(.eligible)'
```

The first command prints one path per worktree. The second prints eligible
assessment rows and removes nothing. JSON preserves paths as strings;
keep paths quoted when passing them to a command.

`--json` disables colour. `-q` suppresses summaries where supported, while
`-v` traces subprocesses on stderr. See [JSON and streams](docs/REFERENCE.md#json-and-streams)
for fields and the commands that accept each flag.

Commands append an [audit log](docs/REFERENCE.md#audit-log) under
`~/.local/state/worktrees`, or `$XDG_STATE_HOME/worktrees` when set.
`WORKTREES_AUDIT_DIR` changes the directory; `WORKTREES_AUDIT=off` disables
logging. Help and completion record nothing.

## What it refuses

The CLI refuses these Git operations regardless of command flags:

```
reset --hard
a forced checkout or switch
clean -f
push --force
worktree remove --force
branch -D
an update-ref delete that names no full sha
a forced fetch into a local ref
```

`gw --explain` lists the Git commands that the CLI can run and the guard
rules that apply to them.

## Agent plugin

The [agent plugin](plugin/README.md) provides six skills for Claude Code
and Codex. They update branches through conflicts, inspect checks, maintain
pull request descriptions, ship changes through merge, prune approved
worktrees and configure permissions.

Install the plugin from the `mihaibojin` marketplace as its README
describes, then run `gw preflight` inside each repository. The
[preflight guide](docs/commands/preflight.md) explains each check and how
to install the current client's permission rules. The skills use the
checkout assigned to the session.

## Migration

See the [migration table](docs/REFERENCE.md#migration) for renamed commands
and flags. Scripts that act on assessments must check `eligible` before
removal; a `remove` verdict alone does not grant permission.

## Contributing

[DEVELOPMENT.md](docs/DEVELOPMENT.md) lists the setup and checks for a
contribution. Report bugs through [GitHub issues](https://github.com/MihaiBojin/worktrees/issues).
The [reference](docs/REFERENCE.md) documents shared rules and links to
every command guide.

## Licence

Apache-2.0, Mihai Bojin. See [LICENSE](LICENSE).
