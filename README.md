# worktrees

[![Tests](https://github.com/MihaiBojin/worktrees/actions/workflows/tests.yml/badge.svg)](https://github.com/MihaiBojin/worktrees/actions/workflows/tests.yml)
[![PyPI](https://img.shields.io/pypi/v/git-worktrees)](https://pypi.org/project/git-worktrees/)

Git worktree commands that refuse to lose work.

```console
gw sync                 # refresh remote refs and forge evidence
gw list                 # choose a worktree, or print metadata without a terminal
gw prune                # confirm and remove eligible worktrees
gw remove fix-parser    # remove one worktree and its finished branch
```

`gw sync` fetches every remote and asks the forge about each local branch.
Every other command but `gw version`, `gw list`, `gw fast-forward`,
`gw push`, `gw ci-checks`, `gw merge`, `gw stack-link`, `gw preflight`, help and completion runs it first once the last
sync is 5 minutes old, then works from local refs and that sync's evidence.
`gw fast-forward` and `gw push` fetch the default remote instead. `gw ci-checks` reads a pull request's checks from
the forge each time it runs. With `WORKTREES_OFFLINE` set, nothing touches
the network. Run commands inside the repository you want assessed; other
repositories under the same worktree root are outside their scope.

| Command | Shortcut | What it does |
| --- | --- | --- |
| `gw`, `gw --help` | | print every command, its aliases and its shortcuts |
| `gw list [QUERY] [flags]` | `gwl` | list worktrees or choose a destination in a terminal |
| `gw select-main` | `gwm` | change into the worktree on the head branch; `gw main` or `gw m` for short |
| `gw sync` | `gws` | fetch every remote and refresh forge evidence |
| `gw prune` | `gwp` | confirm and remove eligible worktrees |
| `gw remove [NAME\|PATH]` | `gwr` | remove one worktree and its finished branch |
| `gw branch status [NAME ...]` | `gwbs` | assess local branches without worktrees |
| `gw branch prune [NAME ...]` | `gwbp` | confirm and delete eligible loose branches |
| `gw add NAME [BASE]` | `gwa` | create a worktree for a branch and change into it |
| `gw branch create NAME` | `gwbc` | start a branch from the head ref, or stack it on this branch |
| `gw fast-forward [NAME]` | `gwff` | rebase this branch onto the head ref, or move a merged one, and what came after its merge, to a new branch |
| `gw push` | `gwpu` | push this branch, refusing when that would delete a commit or bring a merged one back |
| `gw merge NUMBER... [--match-head-commit SHA]` | | squash checked GitHub PRs, a native stack in one call, respecting merge queues |
| `gw stack-link NUMBER NUMBER ...` | | link open GitHub PRs into a native stack, bottom first |
| `gw ci-checks [NUMBER]` | | read a pull request's checks from GitHub or GitLab; `gw ci` for short |
| `gw preflight` | | check that the forge's CLI, GitHub's squash defaults, the agent plugin and the shell plugin are set up here |
| `gw version` | | print the installed version |

`gw` is the one command installed on `$PATH`, and `worktrees` is its long
name. A shortcut is a function from the fish or zsh plugin that runs the `gw`
command beside it and completes as that command does. `gwp!`, `gwpa`, `gwpa!`
and `gwr!` add fixed flags, as [Shortcuts](#shortcuts) lists. A subcommand takes
precedence over a worktree name: use `gw list sync` to reach a worktree named
`sync`.

## Install

```console
uv tool install git-worktrees
```

`~/.local/bin` gets `gw` and its long name, `worktrees`. The distribution is
`git-worktrees` because PyPI already holds `worktrees`. A version installs
that release and nothing later:

```console
uv tool install git-worktrees==0.1.0
```

Python 3.11 or newer, and no dependencies. The standard library answers every
question this asks, so an invocation pays for the interpreter and nothing
else.

`gw` works from any shell. Install the fish or zsh plugin too for the
shortcuts, for completions, and for `gw add`, `gw remove`, `gw list` and
`gw select-main` to change your directory.

Install the plugin at the tag of the release `uv` installed. Without a tag
both managers take `main`, which can be ahead of the last release, and the
functions then call commands they were not tested against. `gw --help` says which
release the plugin here came from, and whether it is the same one.

To upgrade, move both: `uv tool upgrade git-worktrees` for the commands, then
the plugin's tag to the same release, as each manager below describes.

### fish

```console
fisher install MihaiBojin/worktrees@v0.13.0
```

Fisher copies `functions/`, `completions/` and `conf.d/` from the repository
root, so the `gw` function and the shortcuts arrive with completions.
Nothing has to be sourced.

To change release, edit the tag on that line of
`$__fish_config_dir/fish_plugins` and run `fisher update`. The same edit moves
an install without a tag onto one; `fisher install` with a tag refuses while
the untagged one holds the files.

### zsh

Antidote takes a `path:` into the repository, and `branch:` takes a tag:

```
MihaiBojin/worktrees branch:v0.13.0 path:zsh/plugins/worktrees   # in zsh_plugins.txt
```

The plugin puts its own `functions/` and `completions/` on `fpath` and
autoloads the functions. Completion needs `compinit`, which most zsh setups already
run; without one, add it after the plugin loads:

```zsh
autoload -Uz compinit && compinit
```

Antidote checks a tag out only when it clones, and `antidote update` leaves a
tag where it is. To change release, remove the clone, then edit the tag, then
open a new shell, which clones it again at the new tag:

```zsh
antidote purge MihaiBojin/worktrees
```

The order matters. Antidote rebuilds its bundle only when `zsh_plugins.txt` is
newer than the bundle, so a tag edited before the purge leaves a new shell
with no plugin until the file changes again. The same three steps move a clone
without a tag onto one.

### From a checkout

Both managers accept a local directory in place of `MihaiBojin/worktrees`, so
the commands and the plugin can come from one clone and agree on a version.
Install the commands from the same directory:

```console
uv tool install --reinstall ~/src/worktrees
```

Remove the published plugin first. Fisher refuses to install a second plugin
that owns the same files, and Antidote would load both.

In fish:

```console
fisher remove MihaiBojin/worktrees
fisher install ~/src/worktrees
```

Fisher copies the files at install, so a change in the checkout reaches the
shell after `fisher update ~/src/worktrees`.

In zsh, replace the `MihaiBojin/worktrees` line in `zsh_plugins.txt` with the
plugin's directory:

```
~/src/worktrees/zsh/plugins/worktrees   # in zsh_plugins.txt
```

Antidote clones nothing for a local path. It sources the plugin from the
checkout, so a new shell runs whatever the checkout holds.

### What the shell code does

The `gw` function runs `command gw` and changes directory when `gw add`,
`gw remove`, `gw select-main` or a terminal `gw list` prints an absolute existing destination.
It forwards metadata unchanged without a terminal and under `--json` or
`--table`, and every other subcommand passes straight through. Each shortcut
is one line that calls the `gw` function with fixed words, so `gwa` lands
where `gw add` does.

Both plugins export `WORKTREES_PLUGIN_VERSION`, the release they came from,
since the environment is all a binary sees of the shell that started it. `gw --help`
and `gw version` compare it with their own and say whether the two match,
which is older, and what to run: `gw --help` in its text, `gw version` on stderr, so
its stdout stays the version alone for a script to read.

An excerpt of `gw --help`'s release lines with matching CLI and shell releases:

```console
worktrees 0.13.0: git worktree commands that refuse to lose work
The shell plugin here is 0.13.0, the same release as these commands.
```

A shell without the variable has no plugin loaded, or one older than the
release that started exporting it, and nothing can tell those two apart. A
bash started from fish or zsh inherits the variable without the functions.

The completions hold no candidate of their own. `gw --complete` prints every
subcommand and shorthand, `gw list --complete` prints every worktree, and both
shells narrow what those return, so the matching stays in one place and a
command added to the table needs no edit in either shell.

One row a line, `label<TAB>path`. A worktree whose path holds a newline is
left out of that listing, because both shells would read it as two rows and
offer the tail as a worktree of its own. That worktree remains reachable by
name or exact path. The `gw` function preserves path bytes, including
trailing newlines, and removes only the final destination-record newline.

Neither manager puts a binary on `$PATH`, which is why the two installs stay
separate.

## Assess and remove

`gw prune` prints the assessment and asks before removing eligible worktrees
on a terminal. Nonterminal input, `--json` and `--table` print the assessment
without writing refs, cache entries or directories. `--table` prints an
uncoloured table. `--yes` and `-y` approve removal in any of those modes and
grant no deletion category. `--only VERDICT` filters an assessment and cannot
be combined with `--yes`. `--quiet` and `--verbose` work in every pruning
mode, separately. `gw remove` selects one exact worktree name or path and
takes the same rules for it: nonterminal input, `--json` and `--table` print
its row and remove nothing, and `--yes` approves removal. It has no `--only`.
With `--yes`, several exact name matches open a picker on a terminal; without
one, `gw remove` refuses and lists the paths. A missing name fails. Use a
quoted full path to identify a target exactly.

```console
gw prune --json
gw remove '/home/you/git/.worktrees/fix-parser/repo' --yes
gw branch status --json
gw branch prune old-fix another-fix --yes
```

A checked-out branch appears only as its worktree, never as a loose branch.
`gw branch prune` with no names opens a selector;
`gw branch prune --all --dry-run` previews all loose branches.
[Branch status](docs/commands/branch-status.md) describes the proofs;
[branch prune](docs/commands/branch-prune.md) covers selection and recovery.

A row's verdict is `remove` or `keep`, and its `reason` names the fact that
decides it, the first in this table that applies:

| Reason | Verdict | When |
| --- | --- | --- |
| `main-checkout` | `keep` | it is the main checkout |
| `current` | `keep` | `gw prune` runs inside it |
| `locked` | `keep` | the worktree is locked |
| `missing` | `keep` | its directory is gone and its record locked |
| `submodule` | `keep` | it has an initialized submodule or a submodule store |
| `in-progress` | `keep` | a rebase, merge, cherry-pick, revert or bisect waits in it |
| `uncommitted` | `keep` | it has uncommitted changes or untracked files |
| `protected` | `keep` | its branch is `main`, `master` or the head branch |
| `merged` | `remove` | a matching cached merged request, local ancestry, squash or content proof, or commits made on the branch that now sit on the head ref's first-parent history |
| `closed` | `keep` | a request closed unmerged at this tip; `--delete closed` removes it |
| `open` | `keep` | a request open at this tip |
| `unused` | `keep` | the tip is on the head ref's first-parent history and its reflog records no commit made on it; `--delete empty` removes it |
| `stale` | `remove` | the same, with no reflog left to say whether it was used |
| `pushed` | `keep` | commits outside the head that a remote-tracking ref holds |
| `unpushed` | `keep` | commits outside the head that only this repository holds |
| `unknown` | `keep` | the CLI lacks valid evidence |

Deleting a branch deletes its reflog, and removing a worktree deletes the
worktree's own. A commit nothing else reaches goes with them, so a row whose
deleted reflogs hold a commit whose patch the head lacks is held back, and
the protection names `git show SHA` for each one. `eligible` also checks
protections and the deletion permissions you supplied.

The table is a box with one record for each worktree: a line of short
answers, then the reasons across the full width, wrapped to the terminal.

```
┌───────────────┬─────────┬────────┬──────────────────────┐
│ BRANCH        │ VERDICT │ REASON │ ELIGIBLE             │
├───────────────┼─────────┼────────┼──────────────────────┤
│ release-0.8.0 │ keep    │ closed │ no                   │
├───────────────┴─────────┴────────┴──────────────────────┤
│ pull request #182 was closed without merging this tip   │
│ its request is closed                                   │
│ its reflog alone holds 2 commits the head branch lacks; │
│   see each with:                                        │
│     git show 54a199e1f797                               │
│     git show ea8faa8208f5                               │
│ needs: --delete closed --delete reflog                  │
└─────────────────────────────────────────────────────────┘
```

The reasons are the evidence, then each protection that stands, a command
on a line of its own so it is never cut. `needs:` ends a record with every
`--delete` value it needs, and only where those values make it eligible.
The path is in `--json`, not in the table. Off a terminal and under
`--table` the box is drawn with `+`, `-` and `|` and nothing wraps. In
`--json`, a protection those values lift ends with them, such as
`pass --delete empty --delete stashes`. For a target it keeps, `gw remove` prints every reason and
each command that removes it: `gw remove PATH --delete VALUE`, carrying the
values you passed, as `gw prune` names them; `--keep-branch` when only that
waives the proof; and, for uncommitted changes or untracked paths,
`git worktree remove --force PATH`, which deletes the checkout with them and
keeps the branch. That line counts the ignored paths and reflog-only commits
that go too. gw never runs it.

The CLI never deletes `main`, `master` or the head branch. It never removes
the main checkout, locked or dirty worktrees, worktrees with untracked files,
or worktrees with an initialized submodule or a submodule store. For a
submodule refusal, run `git submodule deinit --all` inside the target, delete
the store the refusal names once nothing in it is needed, and retry. A locked
worktree whose directory is away keeps its record and stays until you unlock
it, as `git worktree remove` requires. `gw prune` also protects the current
worktree. Detached worktrees get the same checks.

### Permissions

`--yes` skips the first confirmation. It grants no deletion permission.
Repeat `--delete` to combine categories:

| Value | `gw prune`, `gw remove` | `gw branch prune` |
| --- | --- | --- |
| `empty` | permit an `unused` branch and its worktree | permit an `unused` branch |
| `stashes` | permit the branch's stashes | same |
| `closed` | permit a branch whose request closed unmerged at its tip | same |
| `reflog` | permit dropping commits only the deleted reflogs hold | same |
| `all` | every value above | same |

`gw branch status` rejects `--delete`. An unknown or unsupported value is a
usage error before mutation. A `--delete` value changes eligibility, not the
verdict. When only missing values hold worktrees back, `gw prune` says so
and prints the command that grants them on a line of its own, keeping the
values you passed, then the worktrees it frees. The command takes no names;
`gw remove NAME` targets one worktree.

```
→ 2 worktrees are held back only by a permission. To make them eligible, run:

      gw prune --delete closed --delete reflog

  frees: gw-design, release-0.8.0
```

Without a terminal,
the line that names `--yes` carries the `--delete` values and
`--keep-branch` you passed, so running it removes what was counted.

```console
gw prune --delete empty --delete stashes
gw remove old-checkout --keep-branch
```

`--keep-branch` removes an otherwise unprotected checkout without completion
proof and retains its branch and stashes, including a protected branch.
A detached checkout has no branch to keep its commits, so it still needs
proof. It keeps the branch's reflog but not the worktree's, so it takes
`--delete reflog`, and every other `--delete` value is a usage error with it.

### Removal and recovery

Removal deletes a worktree's ignored files: build output, caches and
virtual environments are what most of them are. They have no recovery
command, since Git never recorded their contents. Before confirmation, the
CLI discloses each thing it deletes or keeps, one line each, led by what
happens to it: `delete worktree` with its path, `delete ignored` for each
ignored path, sorted by name, `delete branch` and `delete stash` with their
restore commands, or `keep branch` under `--keep-branch`. The question
counts each kind: `delete 1 worktree, 1 ignored path and 1 branch? [y/N]`.
One key answers it, with no enter: `y` deletes, and any other key, enter
and esc among them, deletes nothing.
Each ignored path reads as the pattern that ignores it names it, from the
top level, with a trailing `/` on a directory: `__pycache__` lists
`src/pkg/__pycache__/`, never the tracked
`src/` above it. A pattern for a directory's contents, such as
`path/to/ignored/*`, lists that directory once as `path/to/ignored/`.
Below the list, `git -C PATH ls-files --others --ignored --exclude-standard`
lists every ignored file, for you to read before answering.
Quiet, JSON and `--yes` keep this disclosure on stderr.
It repeats the assessment after confirmation and refuses changed targets.
`gw prune` repeats it for every target together, before the first removal,
so a repository with a slow `git status` waits for it once and not once a
worktree; each removal then checks the head branch and the worktree's
record again, and `git worktree remove` refuses a checkout changed since.
Ignored directory entries can cover a whole directory; the recheck compares
Git's ignored path listing, not a fingerprint of every file's contents.

The CLI removes the checkout with plain `git worktree remove`, deletes its
branch only at the recorded SHA, then drops approved stashes. It reports
checkout, branch and stash outcomes separately. A failed branch deletion
keeps its stashes; other targets can still succeed. Read the actual outcome
before retrying.

```console
git branch old-fix FULL_SHA
git stash store -m 'On old-fix: saved work' STASH_SHA
```

Use the exact commands printed by the CLI, including quoted names and stash
subjects. A branch restore recreates its ref, not its worktree directory.
A dropped stash can be restored until Git garbage collection prunes its
unreachable commit. Stashes match a branch by their `WIP on NAME:` or
`On NAME:` subject and an ancestor first parent. Detached stashes belong to
no branch. Concurrent stash changes are checked by SHA; an accidentally
dropped different entry is restored at the top of the list.

`gw remove` asks a second confirmation before removing the current worktree,
even with `--yes`. It refuses without a terminal. Its process moves to the
main checkout first and prints that destination after checkout removal,
including when later branch or stash cleanup fails. The command then returns
failure; the fish and zsh functions still move your shell to the destination.
JSON carries it in `path` and `outcome.destination`.

### Shortcuts

| Shortcut | Runs |
| --- | --- |
| `gwp!` | `gw prune --yes` |
| `gwpa` | `gw prune --delete all` |
| `gwpa!` | `gw prune --delete all --yes` |
| `gwr!` | `gw remove --yes` |
| `gwrk!` | `gw remove --keep-branch --yes` |

`gwpa` permits deleting empty branches and branch stashes, and still asks
at a terminal. `gwpa!`, `gwr!` and `gwrk!` skip that question, as `gwp!`
does. `gwr!` grants no deletion category. `gwrk!` keeps the branch and its
stashes, so the branch needs no proof that it is finished.

## Head and synchronization

Assessment uses the default remote's HEAD after successful sync, or the
clone's remote HEAD before the first sync. Without either, it infers a local
head: the only `main` or `master`, the newer tip if both exist, and `main` on
a tie. With neither, the head is unresolved. Every verdict is then unknown,
mutation refuses, and branch/worktree creation and `gw fast-forward` fail.

Human output names the head and its source on stderr. A never-synced
repository with a remote uses clone or last-fetch refs and no forge evidence,
and says so on stderr even with `-q` or `--json`, so a script acting on the
verdicts hears it too. Stale sync state, older than 10 minutes, produces an
age warning; exactly 10 minutes is fresh. `-q` and `--json` omit the age
warning and the head line, and JSON carries both states in `lastSync` and
`head`. Repositories without remotes have neither sync warning. Offline, no
notice suggests `gw sync`.

The default remote is `checkout.defaultRemote`, or the only remote. Several
remotes without that setting are all fetched, then sync fails and prints the
configuration command. Other remotes do not affect verdicts.

`gw sync` fetches every remote, the default one first, with all their tags.
It updates the default remote's HEAD, then queries GitHub or GitLab for every
local branch tip, eight at a time. It prunes remote-tracking refs and never
a local tag. A tag that moved on the remote keeps its local commit, with a
warning that prints `git fetch --tags --force <remote>`.

A remote whose fetch refspec writes outside `refs/remotes/<name>/`, such as
the `+refs/*:refs/*` that `git clone --mirror` sets, makes sync refuse before
fetching: with `--prune` it would delete local branches the remote lacks.

A failed fetch of the default remote leaves both times unchanged. Forge
failure can leave refs refreshed while returning failure; an unsupported
forge leaves its time null. No remote is a successful local case with both
times null. [The reference](docs/REFERENCE.md) lists each partial outcome.

The shared cache, `git-worktrees-sync.json` in the Git common directory,
records remote name/URL, successful sync times and forge evidence keyed by
branch and source tip. Sync replaces it atomically. Changing the remote's
name or URL invalidates it. Malformed or mismatched evidence proves nothing.
A moved branch needs another sync for forge evidence; local proof still works.

Every command except `gw sync`, `gw version`, `gw list`, `gw fast-forward`,
`gw push`, `gw ci-checks`, `gw merge`, `gw stack-link`, `gw preflight`, help and completion syncs first when the default remote's refs have never
synced or last synced 5 minutes ago or more. `WORKTREES_SYNC_TTL` sets that window in whole seconds, and `0`
syncs before every command. The forge time does not count, so a forge that
keeps failing does not make every command sync. A failed sync warns, `-q` and
`--json` hide the warning, and the command goes on with the evidence it had.
Without a default remote nothing syncs first. An unreachable remote costs
every command its fetch timeout, which `WORKTREES_OFFLINE` skips.
`gw fast-forward` fetches the default remote and asks the forge on every
run, and refuses when either fails. `gw push` fetches the default remote on
every run and refuses when that fails.

`WORKTREES_OFFLINE`, set to anything but the empty string, keeps every
command off the network: nothing syncs first, and `gw sync` and `gw push`
refuse at exit 3.

Commands that read objects in a partial clone need Git 2.45.1 or newer, or a
patched maintenance release:
2.39.4, 2.40.2, 2.41.1, 2.42.2, 2.43.4 or 2.44.1 and later patches in those
series. The CLI refuses lower reported versions even if a vendor backported
the fix, because it cannot verify that patch. Ordinary full clones keep
working on older Git versions. A proof that needs an object the clone lacks
makes that branch `unknown`, with one warning that `git backfill` fetches it.

## Navigation and branches

`gw list`, `gw l` and the `gwl` shortcut offer the main checkout and this
repository's linked worktrees. A query narrows them by name, branch or path,
and one that leaves a single match changes into it without a prompt. No
query opens the picker. Without a terminal, or with `--table` or `--json`,
it prints the matches and changes nothing. `gw select-main`, `gw main`,
`gw m` and the `gwm` shortcut change into the worktree on the head branch.
[List](docs/commands/list.md) gives the matching, the picker's keys and
each exit code.

`gw add NAME [BASE]` creates a worktree for branch `NAME` under
`.worktrees/NAME/` beside the main checkout and changes into it, checking
the branch out when it exists. A branch only the remote has starts from the
remote's copy, after asking or with `--yes`. [Add](docs/commands/add.md) lists where it
goes and what it refuses. `gw branch create NAME` starts `NAME` in this
checkout from the head ref, with no upstream, or stacks it on the branch you
stand on when you answer yes; [branch create](docs/commands/branch-create.md)
has the cases.

`gw fast-forward` brings the branch you stand on level with the head ref,
after fetching and asking the forge in the same run. A branch with no
commits of its own fast-forwards, the head branch included; one with commits
is rebased, and the first conflict stops the rebase with the files left for
you to resolve. A branch proved finished keeps its name, and the checkout
moves to a new branch from the head ref or `--from`, named
`YYYY-MM-DDThh-mm-ssZ/NAME` unless you name it. A branch whose own pull
request squash-merged its first commits moves the same way, carrying only
the commits made after the merge; a stacked branch whose parent merged keeps
its name and drops the merged commits in place. When that branch's open
request still targets the merged parent, which a forge leaves there unless
the parent's branch is deleted, the run moves its base to the head branch
and pushes the branch under a lease; commits the parent gained after its
merge get a branch and a request of their own first. `--autostash` carries uncommitted changes across. A branch
already level exits 0 and changes nothing. `gw fast-forward --continue`
finishes a rebase it stopped once the conflicts are staged.
[Fast-forward](docs/commands/fast-forward.md) gives the order of the
checks, the JSON and the conflict loop.

`gw push` fetches the default remote and pushes the branch you stand on,
with `--set-upstream` when the remote lacks it and under a lease on the
commit it just read otherwise. It refuses when the remote copy holds a
commit the branch lacks by patch-id and this clone never made, which `--replace` pushes over, and
when the branch still carries commits the head branch merged, which
`gw fast-forward` drops. A plain `git push --force-with-lease
--force-if-includes` lets both through when the remote tip is in the
branch's reflog. [Push](docs/commands/push.md) has an example of each.

## Pull request checks and merges

[`gw merge`](docs/commands/merge.md) squashes one GitHub PR at its checked
head SHA, or several in the order given at the heads whose CI it reads,
building each message from the PR. A PR in a native stack merges with every
open PR below it in one call, once each passes its checks. Every merge
respects the merge queue. A rewritten head returns `head-moved` at exit 4 without
submitting. A base left on a branch whose own PR merged is refused, and
`gw fast-forward` moves it. Submitted operations
report `merged`, `enqueued`, `pending` or `failed`. The ship-it skill in
Codex and Claude uses this command; both the CLI and plugin must be updated
to provide it.

[`gw stack-link`](docs/commands/stack-link.md) links open GitHub PRs into a
native stack, bottom first: it creates the stack, or adds pull requests on
top of the one the first ones are in. The ship-it skill opens a stacked PR
with `gh pr create` and links it with this command.

`gw ci-checks`, or `gw ci` for short, reads one pull request, or GitLab merge
request, and its checks: the current branch's, or the one `NUMBER` names. It
writes nothing, not even the sync cache, and never syncs first.
`WORKTREES_OFFLINE` makes it refuse at exit 3, as `gw sync` does.

```console
gw ci-checks                     # the table, failures first
gw ci-checks 42 --json           # the snapshot as data
gw ci-checks 42 --wait --json    # read until no check is running
```

`checksState` is `passed`, `failed`, `pending`, `blocked` or `unknown`.
`unknown` means no check reported, or `stale`: the review moved while it was
read, so read again. Each check carries `name`, `workflow`, the forge's own
`state`, a `bucket` of `pass`, `fail`, `cancel`, `pending`, `skipping`,
`blocked` or `unknown`, and a `link` to its log. `pr` holds the review's
number, URL, state, draft flag, head and base, and its mergeability as the
forge reports it. `pr.trailers` holds the commits' trailers once each,
oldest commit first, or null when the complete list is unavailable.
[CI checks](docs/commands/ci-checks.md) describes the trailer limits.
`passed` describes the checks, not whether the forge will merge: a required
review still blocks it.

GitHub answers in one `gh pr view`, so the commit messages and checks
describe the same head; a re-run replaces the run it repeats. GitLab reads
the merge request's head pipeline from the project that ran it, and reads the merge
request again afterwards. A pipeline for an older head is stale, unless it is
a merged-result pipeline whose parents are the current head and the target's
current tip.

`--wait` reads every 30 seconds until no check is running and prints the last
read. An empty list counts as not started for the first two minutes. It gives
up after 30 minutes with `checksState` still `pending`.

Exit 0 means a snapshot was read, failing checks included. Exit 1 means no
snapshot: no default remote, a host that is neither GitHub nor GitLab, a
missing `gh` or `glab`, or a read that failed or came back without the
review's identity. A malformed number is a usage error, exit 2.

## Output and JSON

Results go to stdout; diagnostics and prompts go to stderr. JSON and
`gw list --table` never contain colour. A pipe, nonempty `NO_COLOR`, or `TERM=dumb`
disables colour on its stream. `NO_COLOR=` does not disable it.

`-q` suppresses summaries, progress, stale warnings, a failed sync's warnings
before the command, and the head line while
keeping results, errors, never-synced notices and deletion disclosures, which
repeat an inferred head.
It keeps `gw branch create`'s branch/base/SHA result and `gw fast-forward`'s
result table. `-v` traces subprocesses on stderr and keeps JSON stdout
parseable. Passing both is a usage error.

| Commands | `-q` | `-v` |
| --- | --- | --- |
| `gw sync`, `gw prune`, `gw remove`, `gw branch prune`, `gw add`, `gw branch create`, `gw fast-forward`, `gw push` | yes | yes |
| `gw branch status` | yes | no |
| `gw list`, `gw select-main` | no | no |
| `gw ci-checks` | no | yes |
| `gw merge` | no | yes |
| `gw stack-link` | no | yes |
| `gw preflight` | no | yes |

Other commands retain their own flags; use `--help`. Help, version and
completion work without an assessment. `--only` filters assessment verdicts,
`remove` or `keep`, and can be repeated.

Every JSON result is an object with `lastSync` and `head`:

```json
{
  "lastSync": {"refs": "2026-11-05T10:00:00Z", "forge": null},
  "head": {"ref": "refs/remotes/origin/main", "source": "remote"}
}
```

Times are UTC RFC 3339 or null. A time does not mean every branch has forge
evidence. Head source is `remote`, `clone` or `inferred`. `ref` is the branch
the head resolves to: the remote-tracking ref a remote HEAD points at, or
`refs/heads/main` or `refs/heads/master` when inferred. Unresolved `ref` is
null. Worktree assessment has `verdicts`, with `verdict`, `eligible`,
`protections`, `stashes`, exact `path`, `branch`, `sha` and proof fields.
`gw list --json` puts its rows in `worktrees`.

```console
gw prune --json | jq '.verdicts[] | select(.eligible)'
gw list --json | jq -r '.worktrees[].path'
```

## Audit log

Each command appends what it did, one JSON object per line, to
`~/.local/state/worktrees/audit-YYYY-MM.jsonl`: the command as typed, every
`git`, `gh` and `glab` process with its exit code and duration, and each
removal with the full SHA and what restores it, as fields and as commands
to paste. Lines are only ever appended, and no file is ever deleted.

```console
$ jq -c 'select(.event == "removal.outcome") | {ts, host, branch, result, restore}' \
    ~/.local/state/worktrees/audit-*.jsonl
$ jq -c 'select(.event == "exec" and .mutates) | {ts, ms, code, argv}' \
    ~/.local/state/worktrees/audit-*.jsonl
```

`WORKTREES_AUDIT=reads=off` leaves out read-only git commands,
`WORKTREES_AUDIT=off` turns the log off, and `WORKTREES_AUDIT_DIR` moves it.
[The reference](docs/REFERENCE.md#audit-log) lists every event and field.

## Migration

| Removed interface | Current interface |
| --- | --- |
| `gwa`, `gwp`, `gwr`, `gwbs`, `gwbd`, `gwbc`, `gwrot` binaries | `gw` subcommands, or the plugin's shortcuts |
| `gws` as assessment, `gw status`, `gw s`, `gw st` | `gw prune --json`; `gws` now runs `gw sync` |
| `gw rotate`, `gw rot`, `gwrot` | `gw fast-forward`, `gw ff`, `gwff` |
| a rotated branch named `NAME-YYYY-MM-DD_NNN` | `YYYY-MM-DDThh-mm-ssZ/NAME` |
| `gwp --dry-run` | `gw prune --json` or `gw prune --table` |
| `gw list --list`, `gw list -l`, `gw prune --list` | `gw list --table`, `gw prune --table` |
| `gwp --branch NAME` | `gw remove NAME` or a quoted exact path |
| `--delete-ignored`, `--delete ignored-files` | none; removal deletes ignored files and lists them first |
| `gwr --force`, `gwr -f` | `gw remove --keep-branch` |
| `--no-fetch`, `--no-forge`, `--force-refresh`, refresh `-f` | `WORKTREES_OFFLINE=1` to stay offline; `gw sync` to refresh now |
| `gw ls`, `gw NAME` | `gw list`, `gw list NAME` |
| `gwh`, `gw help`, `gw h` | `gw --help` or `gw -h` |
| a `--json` listing as an array, `.[]` | `gw list --json` object, `.worktrees[]` |

An `unused` or `closed` row requires explicit permission. `main` and `master` always remain
protected. Update scripts that assume a `remove` verdict alone permits
deletion to check `eligible` too.

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
```

## Agent plugin

[The agent plugin](plugin/README.md) gives Claude Code and Codex six skills
over these commands: bring a branch level with the head
branch through its rebase conflicts, read CI, keep a pull request's
description true to its diff, ship a change or an open pull request
through CI to its merge, prune finished worktrees and branches after
asking, and add the permission rules shipping needs to Claude Code's
settings.

The skills handle branches, pull requests and merges in the checkout the
session runs in, and never create a worktree. A person creates and
chooses worktrees through `gw add` and `gw list`, and the agent's client
through `claude --worktree` and `codex --worktree`. A Claude Code session
keeps one worktree per repository and runs `gw add` itself only where it
has none yet, or when another session is working in the one it has.

Run `gw preflight` once in each repository. It says whether `gh` or `glab`
is signed in, whether GitHub squashes with each pull request's title and
body, whether the project's Claude Code and Codex settings enable the
plugin, and whether the shell plugin is this release.
`/worktrees:preflight user` installs permission rules for the current
client. `/worktrees:preflight project` installs those rules and the plugin
setup in the repository. Both show the additions and ask before writing.
Codex uses `.rules` files and needs a restart to load them;
[preflight](docs/commands/preflight.md) lists every check and target path.

## Contributing

See [DEVELOPMENT.md](docs/DEVELOPMENT.md) for checks and release commands,
and [REFERENCE.md](docs/REFERENCE.md) for each command's page and the rules
the commands share.

## Licence

Apache-2.0, Mihai Bojin. See [LICENSE](LICENSE).
