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

Only `gw sync` uses the network. Every other command uses local refs and the
last sync's evidence. Run commands inside the repository you want assessed;
other repositories under the same worktree root are outside their scope.

| Command | Shortcut | What it does |
| --- | --- | --- |
| `gw`, `gw --help` | | print every command, its aliases and its shortcuts |
| `gw list [QUERY] [flags]` | `gwl` | list worktrees or choose a destination in a terminal |
| `gw sync` | `gws` | fetch the default remote and refresh forge evidence |
| `gw prune` | `gwp` | confirm and remove eligible worktrees |
| `gw remove [NAME\|PATH]` | `gwr` | remove one worktree and its finished branch |
| `gw branch status [NAME ...]` | `gwbs` | assess local branches without worktrees |
| `gw branch delete [NAME ...]` | `gwbd` | confirm and delete eligible loose branches |
| `gw add NAME [BASE]` | `gwa` | create or enter a worktree |
| `gw branch create NAME` | `gwbc` | start a branch from the assessed head ref |
| `gw rotate` | `gwrot` | start the next branch in a series, or fast-forward the head branch |
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
shortcuts, for completions, and for `gw add`, `gw remove` and `gw list` to
change your directory.

Install the plugin at the tag of the release `uv` installed. Without a tag
both managers take `main`, which can be ahead of the last release, and the
functions then call commands they were not tested against. `gw --help` says which
release the plugin here came from, and whether it is the same one.

To upgrade, move both: `uv tool upgrade git-worktrees` for the commands, then
the plugin's tag to the same release, as each manager below describes.

### fish

```console
fisher install MihaiBojin/worktrees@v0.7.0
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
MihaiBojin/worktrees branch:v0.7.0 path:zsh/plugins/worktrees   # in zsh_plugins.txt
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
`gw remove` or a terminal `gw list` prints an absolute existing destination.
It forwards metadata unchanged without a terminal and under `--json` or
`--list`, and every other subcommand passes straight through. Each shortcut
is one line that calls the `gw` function with fixed words, so `gwa` lands
where `gw add` does.

Both plugins export `WORKTREES_PLUGIN_VERSION`, the release they came from,
since the environment is all a binary sees of the shell that started it. `gw --help`
and `gw version` compare it with their own and say whether the two match,
which is older, and what to run: `gw --help` in its text, `gw version` on stderr, so
its stdout stays the version alone for a script to read.

An excerpt of `gw --help`'s release lines with matching CLI and shell releases:

```console
worktrees 0.7.0: git worktree commands that refuse to lose work
The shell plugin here is 0.7.0, the same release as these commands.
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
on a terminal. Nonterminal input, `--json` and `--list` print the assessment
without writing refs, cache entries or directories. `--list` prints an
uncoloured table. `--yes` and `-y` approve removal in any of those modes and
grant no deletion category. `--only VERDICT` filters an assessment and cannot
be combined with `--yes`. `--quiet` and `--verbose` work in every pruning
mode, separately. `gw remove` selects one exact worktree name or path. Several
exact name matches open a picker on a terminal; without one, `gw remove`
refuses and lists the paths. A missing name fails. Use a quoted full path to
identify a target exactly.

```console
gw prune --json
gw remove '/home/you/git/.worktrees/fix-parser/repo' --yes
gw branch status --json
gw branch delete old-fix another-fix --yes
```

A checked-out branch appears only as its worktree, never as a loose branch.
`gw branch delete` with no names opens a selector;
`gw branch delete --all --dry-run` previews all loose branches.
[Branch status](docs/commands/branch-status.md) describes the proofs;
[branch delete](docs/commands/branch-delete.md) covers selection and recovery.

| Verdict | Evidence |
| --- | --- |
| `remove` | a matching cached merged request, or local ancestry, squash or content proof |
| `empty` | the tip is on the head ref's first-parent history |
| `keep` | commits remain outside the head ref |
| `unknown` | the CLI lacks valid evidence |

`empty` includes unused branches and branches merged by fast-forward without
forge proof. Git cannot distinguish them. A verdict describes completion;
`eligible` also checks protections and the deletion permissions you supplied.

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

| Value | `gw prune`, `gw remove` | `gw branch delete` |
| --- | --- | --- |
| `empty` | permit an empty branch and its worktree | permit an empty branch |
| `ignored-files` | permit the worktree's ignored paths | usage error |
| `stashes` | permit the branch's stashes | same |
| `all` | all categories above | empty branches and stashes |

`gw branch status` rejects `--delete`. An unknown or unsupported value is a
usage error before mutation. `--delete empty` changes eligibility, not the
verdict. Ignored files have no recovery command: Git never recorded their
contents.

```console
gw prune --delete empty --delete ignored-files
gw remove old-checkout --keep-branch
gw remove old-checkout --keep-branch --delete ignored-files
```

`--keep-branch` removes an otherwise unprotected checkout without completion
proof and retains its branch and stashes, including a protected branch.
A detached checkout has no branch to keep its commits, so it still needs
proof.
It grants no ignored-file permission. Only `--delete ignored-files` combines
with it; `empty`, `stashes` and `all` are usage errors with `--keep-branch`.

### Removal and recovery

Before confirmation, the CLI discloses the path and branch, the branch
restore command, stashes with their restore commands, and ignored paths it
will delete. Each ignored path is cut to its first component below the
`.gitignore` that ignores it, so `build/out/a.log` and `build/out/b.log` read
as `build/`. Quiet, JSON and `--yes` keep this disclosure on stderr.
It repeats the assessment after confirmation and refuses changed targets.
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
| `gwr!` | `gw remove --delete ignored-files` |

`gwpa` permits deleting empty branches, ignored files and branch stashes, and
still asks at a terminal. `gwpa!` skips that question.

## Head and synchronization

Assessment uses the default remote's HEAD after successful sync, or the
clone's remote HEAD before the first sync. Without either, it infers a local
head: the only `main` or `master`, the newer tip if both exist, and `main` on
a tie. With neither, the head is unresolved. Every verdict is then unknown,
mutation refuses, and branch/worktree creation and rotation fail.

Human output names the head and its source on stderr. A never-synced
repository with a remote uses clone or last-fetch refs and no forge evidence,
and says so on stderr even with `-q` or `--json`, so a script acting on the
verdicts hears it too. Stale sync state, older than 10 minutes, produces an
age warning; exactly 10 minutes is fresh. `-q` and `--json` omit the age
warning and the head line, and JSON carries both states in `lastSync` and
`head`. Repositories without remotes have neither sync warning.

The default remote is `checkout.defaultRemote`, or the only remote. Several
remotes without that setting make sync refuse and print the configuration
command. Other remotes do not affect verdicts.

`gw sync` fetches that remote, updates its remote HEAD, then queries GitHub
or GitLab for every local branch tip. A fetch failure leaves both times
unchanged. Forge failure can leave refs refreshed while returning failure;
an unsupported forge leaves its time null. No remote is a successful local
case with both times null. [The reference](docs/REFERENCE.md) lists each
partial outcome.

The shared cache, `git-worktrees-sync.json` in the Git common directory,
records remote name/URL, successful sync times and forge evidence keyed by
branch and source tip. Sync replaces it atomically. Changing the remote's
name or URL invalidates it. Malformed or mismatched evidence proves nothing.
A moved branch needs another sync for forge evidence; local proof still works.
There is no automatic refresh or expiration-based network access.

Offline commands in partial clones need Git 2.45.1 or newer, or a patched
maintenance release:
2.39.4, 2.40.2, 2.41.1, 2.42.2, 2.43.4 or 2.44.1 and later patches in those
series. The CLI refuses lower reported versions even if a vendor backported
the fix, because it cannot verify that patch. Ordinary full clones keep
working on older Git versions. A proof that needs an object the clone lacks
makes that branch `unknown`, with one warning that `git backfill` fetches it.

## Navigation and branches

`gw list`, `gw l` and the `gwl` shortcut offer the main checkout and this
repository's linked worktrees. A query matches a worktree's name, branch or
path: substring matching comes first, then ordered-letter matching on the
name and branch. An exact name, branch or path wins over a partial match. In
a terminal, a query with one match selects it without a prompt; several open
the picker filtered by the query, and no query asks.

In the picker, typing filters the rows, digits included, and Backspace
removes a character. Arrow keys wrap and Enter selects. Escape clears the
filter, and a second Escape, or one on an empty filter, cancels, as Ctrl-D
does. Ctrl-C exits 130.

Without a terminal, the command prints a table of every matching worktree.
`--list` prints that table in a terminal too; `--json` prints metadata in the
`worktrees` array. Neither format selects a destination or changes directory.

A current linked worktree is shown with `*` and cannot be selected. The main
checkout remains a destination from inside it, so you can return from a
subdirectory. A worktree is selected through `gw list NAME`, so a worktree
named `sync` or `p` never collides with a command. The fish and zsh `gw`
function preserves stdout and status for metadata and changes directory for
terminal selections.

`gw add NAME [BASE]` creates or enters a worktree. `gw branch create NAME`
creates a branch from the assessed head ref without setting that head as
upstream. `gw rotate` creates the next `NAME-YYYY-MM-DD_NNN` sibling from the
head, stripping a previous rotation suffix. Names must be absent from local
and cached remote refs. On the head branch, rotation fast-forwards it instead.
An unchanged result or divergent head refuses at exit 1.

## Output and JSON

Results go to stdout; diagnostics and prompts go to stderr. JSON and
`gw list --list` never contain colour. A pipe, nonempty `NO_COLOR`, or `TERM=dumb`
disables colour on its stream. `NO_COLOR=` does not disable it.

`-q` suppresses summaries, progress, stale warnings and the head line while
keeping results, errors, never-synced notices and deletion disclosures, which
repeat an inferred head.
It keeps `gw branch create`'s branch/base/SHA result and both `gw rotate`
outcome tables. `-v` traces subprocesses on stderr and keeps JSON stdout
parseable. Passing both is a usage error.

| Commands | `-q` | `-v` |
| --- | --- | --- |
| `gw sync`, `gw prune`, `gw remove`, `gw branch delete`, `gw add`, `gw branch create`, `gw rotate` | yes | yes |
| `gw branch status` | yes | no |
| `gw list` | no | no |

Other commands retain their own flags; use `--help`. Help, version and
completion work without an assessment. `--only` filters assessment verdicts
and can be repeated, including `--only empty`.

Every JSON result is an object with `lastSync` and `head`:

```json
{
  "lastSync": {"refs": "2026-11-05T10:00:00Z", "forge": null},
  "head": {"ref": "refs/remotes/origin/HEAD", "source": "remote"}
}
```

Times are UTC RFC 3339 or null. A time does not mean every branch has forge
evidence. Head source is `remote`, `clone` or `inferred`; unresolved `ref` is
null. Worktree assessment has `verdicts`, with `verdict`, `eligible`,
`protections`, `stashes`, exact `path`, `branch`, `sha` and proof fields.
`gw list --json` puts its rows in `worktrees`.

```console
gw prune --json | jq '.verdicts[] | select(.eligible)'
gw list --json | jq -r '.worktrees[].path'
```

## Migration

| Removed interface | Current interface |
| --- | --- |
| `gwa`, `gwp`, `gwr`, `gwbs`, `gwbd`, `gwbc`, `gwrot` binaries | `gw` subcommands, or the plugin's shortcuts |
| `gws` as assessment, `gw status`, `gw s`, `gw st` | `gw prune --json`; `gws` now runs `gw sync` |
| `gwp --dry-run` | `gw prune --json` or `gw prune --list` |
| `gwp --branch NAME` | `gw remove NAME` or a quoted exact path |
| `--delete-ignored` | `--delete ignored-files` |
| `gwr --force`, `gwr -f` | `gw remove --keep-branch` |
| `--no-fetch`, `--no-forge`, `--force-refresh`, refresh `-f` | offline by default; run `gw sync` explicitly |
| `gw ls`, `gw NAME` | `gw list`, `gw list NAME` |
| `gwh`, `gw help`, `gw h` | `gw --help` or `gw -h` |
| a `--json` listing as an array, `.[]` | `gw list --json` object, `.worktrees[]` |

`empty` requires explicit permission. `main` and `master` always remain
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

[The agent plugin](plugin/README.md) runs the same assessment and removal
commands. It shows head and sync state first, asks about stale or missing
sync, then asks which targets and deletion categories you approve. It never
removes its current checkout.

## Contributing

See [DEVELOPMENT.md](docs/DEVELOPMENT.md) for checks and release commands,
and [REFERENCE.md](docs/REFERENCE.md) for runtime contracts.

## Licence

Apache-2.0, Mihai Bojin. See [LICENSE](LICENSE).
