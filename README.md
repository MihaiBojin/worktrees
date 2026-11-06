# worktrees

[![Tests](https://github.com/MihaiBojin/worktrees/actions/workflows/tests.yml/badge.svg)](https://github.com/MihaiBojin/worktrees/actions/workflows/tests.yml)
[![PyPI](https://img.shields.io/pypi/v/git-worktrees)](https://pypi.org/project/git-worktrees/)

Git worktree commands that refuse to lose work.

`gws`, `gwp`, `gwbs`, `gwbd`, `gwbc` and `gwrot` run as console
scripts. `gwa`, `gw`, `gwm`, and `gwr` change the caller's directory and need
a shell function of the same name.

| | |
| --- | --- |
| `gws` | say which worktrees are finished, and why. Removes nothing, and has no flag that could |
| `gwp` | remove the ones `gws` marks removable, having asked first |
| `gwbs [NAME ...]` | assess local branches, including those without worktrees |
| `gwbd [NAME ...]` | delete branches proved finished, with confirmation and undo commands |
| `gwa NAME [BASE]` | create a worktree for `NAME` and land you in it |
| `gw [QUERY]` | pick one of this repository's worktrees and land you in it |
| `gwm NEW` | rename this worktree's branch and move the checkout to match |
| `gwr [QUERY]` | remove a worktree whose branch is finished, and the branch |
| `gwr! [QUERY]` | `gwr --delete-ignored`, a shell function in fish and zsh |
| `gwp!` | `gwp --delete-ignored --yes`, a shell function in fish and zsh |
| `gwbc NAME` | branch `NAME` off the head branch and check it out |
| `gwrot` | start the next branch after this one, or catch the head branch up |

`gw` takes the same commands as subcommands, each with shorthands, so `gws`,
`gw status`, `gw st` and `gw s` are one command:

| | |
| --- | --- |
| `gws` | `gw status`, `st`, `s` |
| `gwp` | `gw prune`, `p` |
| `gwbs` | `gw branch status` |
| `gwbd` | `gw branch delete` |
| `gwa` | `gw add`, `a` |
| `gw` | `gw list` |
| `gwm` | `gw move`, `mv`, `m` |
| `gwr` | `gw remove`, `rm` |
| `gwbc` | `gw branch create` |
| `gwrot` | `gw rotate`, `rot` |

`gw` with no subcommand runs `gw list`, and so does `gw` followed by anything
that is not a subcommand: `gw parse` is `gw list parse`, and `gw --json` is
`gw list --json`. A subcommand wins over a worktree of the same name, which
`gw list prune` still reaches. `gw -h` prints that table with every name each
command answers to. `worktrees` is the same program under its long name.

The command reference covers [branch status](docs/commands/branch-status.md)
and [branch delete](docs/commands/branch-delete.md), including content proofs,
GitHub and GitLab merge evidence, selection, and recovery. `gwbd` with no
arguments opens a selector; `gwbd --all --dry-run` previews every branch.

Ctrl-C interrupts a running command with exit code 130 and no traceback.
At a selection or removal prompt, it cancels without choosing or removing
a worktree.

`gws` and `gwp` are what this tool is for and their behaviour is settled.

`gwbc` and `gwrot` start branches rather than worktrees: `gwbc` one you
name, `gwrot` the next in a series.

## Two holes in git

### A squash merge inverts `git branch -d`

A branch whose change is already in `main` reports `error: the branch 'x' is
not fully merged` and points you at `-D`, which deletes anything. By hand,
`squashed` and `unmerged` are indistinguishable: both report NOT merged, both
sit one commit ahead. Opposite correct actions, no signal between them.

The probe that separates them replays the branch's tree as one commit on the
merge base and asks `git cherry` whether that patch is already upstream. It
compares content rather than history, which is what a squash preserves.

A branch that probe proves is deleted with its checkout. The delete names the
sha the ref must still hold, so a commit landing between the verdict and the
removal fails it rather than going with it, and the command that puts the
branch back is printed before anything runs.

### `git worktree remove` silently deletes ignored files

With `.env` and `node_modules/` present, `git status --porcelain` prints
nothing, `git worktree remove` exits 0 with no `--force`, and both are gone.
Nothing in git brings them back: no ref ever pointed at them. `gws` reads
`--ignored=traditional`, counts what would go, and marks the worktree `keep`
until you pass `--delete-ignored`.

## Install

```console
uv tool install git-worktrees
```

One venv serves every command name, and `~/.local/bin` gets a link per name.
The distribution is `git-worktrees` because PyPI already holds `worktrees`;
every command keeps its own name. A version installs that release and nothing
later:

```console
uv tool install git-worktrees==0.1.0
```

Python 3.11 or newer, and no dependencies. The standard library answers every
question this asks, so an invocation pays for the interpreter and nothing
else.

That installs `gws`, `gwp`, `gwbs`, `gwbd`, `gwbc` and `gwrot`. The
four that land you somewhere need a shell function too, and every command has
a completion, both of which arrive through a plugin manager.

Install the plugin at the tag of the release `uv` installed. Without a tag
both managers take `main`, which can be ahead of the last release, and the
functions then call commands they were not tested against. `gw -h` says which
release the plugin here came from, and whether it is the same one.

To upgrade, move both: `uv tool upgrade git-worktrees` for the commands, then
the plugin's tag to the same release, as each manager below describes.

### fish

```console
fisher install MihaiBojin/worktrees@v0.7.0
```

Fisher copies `functions/`, `completions/` and `conf.d/` from the repository
root, so the functions for `gw`, `gwa`, `gwm`, `gwr`, `gwr!` and `gwp!` land
together with a completion for every command. Nothing has to be sourced.

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
autoloads the five. Completion needs `compinit`, which most zsh setups already
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

### What the shell code does

Each function runs `command <name>` and `cd`s to what it printed, provided
that is a directory. `--json` and `--list` print what was asked for on the
same stream a destination arrives on, so the shim asks whether what it has is
somewhere to go and stays put when it is not. `gws`, `gwp`, `gwbc` and `gwrot`
change no directory, so they ship as console scripts alone and answer the same
from a prompt and from a script.

Both plugins export `WORKTREES_PLUGIN_VERSION`, the release they came from,
since the environment is all a binary sees of the shell that started it. `gw -h`
and `gw version` compare it with their own and say whether the two match,
which is older, and what to run: `gw -h` in its text, `gw version` on stderr, so
its stdout stays the version alone for a script to read.

A shell without the variable has no plugin loaded, or one older than the
release that started exporting it, and nothing can tell those two apart. A
bash started from fish or zsh inherits the variable without the functions.

The completions hold no candidate of their own. `gw --complete` prints every
subcommand and shorthand, `gw list --complete` prints every worktree, and both
shells narrow what those return, so the matching stays in one place and a
command added to the table needs no edit in either shell.

One row a line, `label<TAB>path`. A worktree whose path holds a newline is
left out of that listing, because both shells would read it as two rows and
offer the tail as a worktree of its own. `gwa` cannot make such a path, since
it derives from a branch name and git refuses a newline in a ref, and the
worktree is still reachable by name or path.

Neither manager puts a binary on `$PATH`, which is why the two installs stay
separate.

## gws

```console
$ gws
Head: origin/main (remote)
BRANCH         PATH                                         VERDICT  WHY
main           /home/you/git/repo                           keep     it is the main checkout, and never removable
dirty-work     /home/you/git/.worktrees/dirty-work/repo     keep     it has uncommitted changes
holds-secrets  /home/you/git/.worktrees/holds-secrets/repo  keep     its tip is an ancestor of the head branch, but holds 2 ignored path(s); pass --delete-ignored
never-pushed   /home/you/git/.worktrees/never-pushed/repo   keep     no proof that its change is in the head branch
squash-merged  /home/you/git/.worktrees/squash-merged/repo  remove   its tree or combined patch is already in the head branch

1 removable, 4 kept, 0 unclear
2 worktree(s) under /home/you/git/.worktrees belong to another repository
gwp removes the 1 marked removable
```

Rows come in `git worktree list` order, the main checkout first. It is there
so the table shows what `git worktree list` shows; nothing proposes it, and
`git worktree remove` answers `fatal: '<path>' is a main working tree`
whatever flag it is given. Paths are absolute wherever this
prints one, so a row can be pasted into the next command. The table and the
count are on stdout; the `Head:` line and everything after the count are on
stderr.

Every repository in the same parent directory keeps its worktrees under that
one `.worktrees` root, so some of the directories in there are a sibling's.
`gws` counts those and says so, because nothing else does, and `--json`
carries the same count as `neighbours`. No command here reads or removes
them, and the count comes from walking the root rather than from asking git.

On a terminal the verdict carries its own colour, `remove` green, `keep` blue
and `unknown` yellow, with the branch in bold and the path dimmed. That
decision is made per stream: redirect it, pipe it, set `NO_COLOR` to anything
non-empty or run under `TERM=dumb` and the bytes are the ones above.

### Three verdicts

Each is the instruction it gives. `unknown` is not `keep` with a softer
word.

| | means |
| --- | --- |
| `remove` | finished, and safe to remove. The reason says how it was proved: by a merged request the last `gw sync` recorded, by ancestry, by squash, by content, or by a ref reaching a detached commit |
| `keep` | something says no. Uncommitted work, the head branch, a lock, the worktree you are standing in, ignored files, or a branch sharing history with the head branch and holding work it lacks |
| `unknown` | it could not tell. A branch with no common history with the head branch, forge evidence that does not match this tip, or a git command that failed while judging it, which also makes the command exit 1 |

A worktree's branch is judged by the proof `gwbs` uses for a loose branch,
and a worktree and a branch at the same commit read the same verdict and the
same reason. The worktree checks come first: the main checkout, the one you
stand in, a lock, the head branch and uncommitted changes each keep it before
the proof runs, and ignored files hold back a `remove` after it.

The forge's evidence comes first, and it comes from the last
[`gw sync`](#gw-sync). No other command runs `gh` or `glab`. A branch
merged as part of a stack needs it: its changes reach the head branch
across several squashes, so the intermediate state it holds differs from
the final one in the same regions, and a diff cannot separate a stale
branch from one with work left. A recorded request proves the branch only
when it was recorded for this exact tip, is merged, came from this
repository, targeted the head branch, and has a merge commit that is here
and that the head branch contains. A merged request that fails one of
those reads `unknown`, unless git proves the branch on its own. A branch
that moved since the sync, and a sync file that is missing, malformed or
written for another remote URL, carry no evidence, and git alone decides.

A record left behind by a directory somebody deleted by hand is nobody's
verdict. `gws` names how many there are; clearing them is `git worktree prune`,
which mutates, so `gwp` is what runs it.

## gwp

```console
$ gwp
Head: origin/main (remote)
squash-merged  /home/you/git/.worktrees/squash-merged/repo
  restore with: git branch squash-merged a2b41ad1fb271bd7d84256f0d35ee5d58c29dd08
remove 1 worktree(s)? [y/N] y
removed 1 worktree(s): squash-merged
```

It removes exactly the rows `gws` marks `remove`, under the same flags, and one
function returns that set for both. What goes and what puts it back is printed
before anything does, and neither `--quiet` nor `--yes` silences it. The sha in
the restore line is full length, because that line is meant to be pasted.

A stash made on a branch that is about to go is named too. `refs/stash` is
per-repository and pins its own commits, so the entry survives the branch and
`git stash branch <new> stash@{0}` still recovers it. What does not survive is
the branch name in its subject, so the line says which entry is about to start
pointing at nothing.

The checkout goes through plain `git worktree remove`, so every refusal git
makes still applies. The branch goes through `git update-ref -d`, naming the
sha the verdict was formed against: a branch somebody committed to in between
survives, and says so in git's own words. Neither `branch -d`, which reads
history and so refuses the squash merge the probe just proved, nor `branch -D`,
which reads nothing and is refused by the guard.

`-y` skips the question. A run whose stdin is not a terminal refuses rather
than blocking, because an agent or a pipe reaching a prompt would hang forever
holding the repository's worktrees:

```console
$ gwp < /dev/null
Head: origin/main (remote)
squash-merged  /home/you/git/.worktrees/squash-merged/repo
  restore with: git branch squash-merged a2b41ad1fb271bd7d84256f0d35ee5d58c29dd08
not a terminal, so nothing can answer for the 1 above; pass --yes to remove them
```

That exits 3 and touches nothing, the code every refusal uses. `gwp --yes --json`
is what an agent runs.

With nothing to remove it prints the table `gws` prints, rather than the name
of the command that would have printed it. The reason each worktree stayed is
the answer to the question, and naming another command puts it one round trip
away:

```console
$ gwp
Head: origin/main (remote)
BRANCH         PATH                                         VERDICT  WHY
dirty-work     /home/you/git/.worktrees/dirty-work/repo     keep     it has uncommitted changes
holds-secrets  /home/you/git/.worktrees/holds-secrets/repo  keep     its tip is an ancestor of the head branch, but holds 2 ignored path(s); pass --delete-ignored
never-pushed   /home/you/git/.worktrees/never-pushed/repo   keep     no proof that its change is in the head branch

nothing to remove; 0 removable, 3 kept, 0 unclear
```

There is no `--dry-run`. `gws` reports and `gwp` asks, so a flag meaning "do
not act" would be a no-op wearing the clothes of a safety feature, and somebody
would one day cite it as the reason a sweep was safe.

## gwa, gw, gwm, gwr

Worktrees live at `<PARENT>/.worktrees/<NAME>/<REPO>`, derived rather than
configured, so two tools cannot disagree about a location neither can be
told. A branch name with slashes becomes directories, and the repository
name goes last, so `auth/oauth` cannot collide with `auth`.

```console
$ gwa fix-parser          # creates it and lands you in it
$ gw parse                # one match takes it outright
$ gwm parser-v2           # renames the branch and moves the checkout
$ gwr                     # asks which, starting on the one you are in
```

Each prints one destination on stdout, which is what the shell function
reads, and prints data there instead under `--json` and `--list`, as `gw`
does without a terminal. The `gw` function captures only `gw list` and the
forms that run it; every other subcommand reaches the terminal as it would
without the function, so `gw prune` keeps its colour and its prompt. So `gw
add`, `gw move` and `gw remove` print their destination, and `gwa`, `gwm` and
`gwr` move you. `gwr` prints a path **only** when you were standing
in what it removed; empty output, and output that is not a directory, both
mean stay put.

`gwm` is the one that is not a convenience. Renaming the directory you are
standing in leaves the shell with a stale `$PWD` and every later command
failing:

```console
$ git status
fatal: Unable to read current working directory: No such file or directory
```

`gw` offers the main checkout too. It is where a finished branch leaves you
and the one destination that is always there, so a list without it is a list
of everywhere except the place you most often want. `gw main` finds it
whatever branch it stands on, and `--list` marks the one you are in:

```console
$ gw --list
   WORKTREE       BRANCH         PATH
   repo           main           /home/you/git/repo
   dirty-work     dirty-work     /home/you/git/.worktrees/dirty-work/repo
   holds-secrets  holds-secrets  /home/you/git/.worktrees/holds-secrets/repo
*  never-pushed   never-pushed   /home/you/git/.worktrees/never-pushed/repo
```

It never offers that one. Picking it is the one answer that cannot take you
anywhere, and asking for the one you are in says so and stays put:

```console
$ gw dirty           # standing in dirty-work
already in dirty-work
```

A query that narrows to one takes it outright. No query does not, even when
the repository holds exactly one other worktree: `gw` with no argument means
show me the options, and being moved without being asked is not that. So it
asks, however many there are:

```console
$ gw                 # standing in never-pushed
origin: github.com/you/repo
  #  WORKTREE       BRANCH         PATH
  *  never-pushed   never-pushed   /home/you/git/.worktrees/never-pushed/repo
> 1  repo           main           /home/you/git/repo
  2  dirty-work     dirty-work     /home/you/git/.worktrees/dirty-work/repo
  3  holds-secrets  holds-secrets  /home/you/git/.worktrees/holds-secrets/repo
which? [up/down and enter, 1-3, or esc to cancel]
```

The lines above the table name each remote and where it fetches from, as
host and path: `git@github.com:you/repo.git` and
`https://github.com/you/repo.git` are both `github.com/you/repo`, and a user,
token or port in the URL is not shown. `--list` and `--json` leave them out.

Rows run in the order the worktrees were made, the main checkout first and
the newest last. git keeps no date, so it is read off the file `git worktree
add` writes into `.git/worktrees/` and nothing changes afterwards.

WORKTREE is the directory under `.worktrees`, or the main checkout's own
directory name. It parts from BRANCH when somebody switches branch inside a
worktree. Up and down move the highlight, enter takes it, a digit takes that
row outright, and esc cancels.

A linked worktree you are standing in is marked `*` and carries no number.
It is where you are rather than somewhere to go, and a list without it shows
three rows where `git worktree list` shows four. The main checkout is always
numbered, even when you are inside it, and carries a `*` after its name
then: from a subdirectory, picking it takes you back to the top.

Matching is substring first and then subsequence, so `tst` finds `add-tests`,
and a substring hit always outranks a loose one. Without a terminal `gw`
selects nothing: it prints the `--list` table of every match and exits 0,
even when one worktree matches or the only match is where you stand. A query
that matches nothing fails at exit 1, and names the closest command when it
looks like a mistyped one:

```console
$ gw prnue
no worktree matches
the closest command is: gw prune
gw -h lists every command
```

`gwr` lists the main checkout dimmed and unnumbered, since git will not
remove it, and starts the highlight on the worktree you are standing in. A
name or path that is exactly one worktree's, as `gw --list` and `gw` print
them, takes that one without asking which.

`gwr` says why a branch is not finished, the same verdict `gws` prints. At a
terminal it then asks whether to remove the checkout anyway, and keeps the
branch: the worktree was in the way, the work was not. `--force` is the same
answer without the question, and `--yes` on its own refuses.

A finished branch whose worktree holds ignored files is refused too, and named
as finished, because it is:

```console
$ gwr holds-secrets
Head: origin/main (remote)
holds-secrets is finished: its tip is an ancestor of the head branch, but holds 2 ignored path(s); pass --delete-ignored
```

At a terminal, and without `--yes`, `gwr` asks instead: it names every
ignored path and removes the worktree on a `y`. `--yes` never answers that
question, so a script still needs `--delete-ignored`.

`--force` is not the answer to that one. It deletes those files just the same,
since `git worktree remove` takes the whole directory, and it keeps a branch
whose work already landed. Every ignored path is named before anything is
deleted, under `--delete-ignored` and under `--force` alike.

`gwr!` is `gwr --delete-ignored`, for when the ignored files are ones you meant
to lose. It is a shell function in fish and zsh, not a console script, and it
calls the `gwr` function so you still land where `gwr` says.

## gwbc and gwrot

Both start branches rather than worktrees.

```console
$ gwbc fix-parser
Head: origin/main (remote)
fix-parser from origin/main at 4a91c02
```

The base is the [head branch](#the-head-branch), so after a `gw sync` it is
`<remote>/<head>` as the server had it then rather than the local copy. Run
`gw sync` first for what it has now. When the head branch does not resolve,
`gwbc` and `gwrot` fail at exit 1 and make nothing. The base travels as a full
ref, because git resolves a bare name as a tag first and a repository holding a
tag called `origin/main` would branch from the tag. `--no-track`, so the head
branch does not become the new branch's upstream and `git push` does not
target it. The line at the end names the commit it got.

`gwrot` takes no argument. The name comes from the branch you are standing on,
with any suffix a previous rotation added already stripped, so four rotations
in a day give four siblings rather than one name carrying four suffixes:

```
fix-parser                   -> fix-parser-2026-09-11_001
fix-parser-2026-09-11_001    -> fix-parser-2026-09-11_002
fix-parser-2026-09-10_003    -> fix-parser-2026-09-11_001
```

Three digits, so the sequence cannot be read as another field of the date. A
number is free only when neither `refs/heads/` nor the remote holds it, or
two people rotate into the same name.

It prints the branch it left and the one it made, each with its commit:

```console
$ gwrot
Head: origin/main (remote)
was  fix-parser                 6d45e4f  kept, 1 commit not on origin/main
now  fix-parser-2026-09-11_001  910074d  new, from origin/main
```

A branch already at the head branch with no commits of its own is refused at
exit 1, because the next branch would be the same commit under another name.
Running `gwrot` twice without committing in between is how that happens.
`origin rotate` makes the branch anyway; this is the second place the two
differ, after exit codes.

On the head branch there is no chain to continue, so it catches that up
instead:

```console
$ gwrot
Head: origin/main (remote)
was  main  6d45e4f
now  main  910074d  fast-forwarded by 3 commits to origin/main
```

A head branch already level with the remote is refused at exit 1 too, since
there is nothing to catch up.

That is `merge --ff-only`, never a rebase: rewriting local commits on the
head branch is the class this tooling refuses everywhere else. Commits the
remote does not have are refused and listed, because they are a change of
their own and `gwbc` puts them on a branch.

## gw sync

```console
$ gw sync
synced origin; head is origin/main
```

`gw sync` fetches the default remote, asks it which branch it serves with
`git remote set-head <remote> --auto`, then asks the forge about every local
branch tip, eight branches at a time, including branches checked out in
worktrees. `gh` answers for a GitHub host and `glab` for a GitLab one. The
record is `git-worktrees-sync.json` in the git common directory, so every
worktree of the repository reads the same one:

```json
{
  "remote": {"name": "origin", "url": "github.com/org/repo"},
  "refs": "2026-11-06T12:00:00Z",
  "forge": "2026-11-06T12:00:02Z",
  "evidence": {
    "feature": {
      "sha": "4f1c0de9...",
      "request": {
        "number": 17, "state": "MERGED", "noun": "pull request",
        "url": "https://github.com/org/repo/pull/17",
        "repository": "github.com/org/repo",
        "source_branch": "feature", "source_sha": "4f1c0de9...",
        "target_branch": "main", "merge_sha": "9a0b7e21...",
        "merged_at": "2026-11-05T09:00:00Z", "same_repository": true
      }
    },
    "spike": {"sha": "c3d2e1f0...", "request": null}
  }
}
```

`refs` is the UTC time of the last fetch and `set-head` that succeeded.
`forge` is the time the forge last answered for every branch, and null when
the remote's host is neither GitHub nor GitLab. Each entry under `evidence`
is keyed by branch and holds the tip that was asked about, so a branch that
moves after a sync has an entry for a commit it no longer points at. A
`request` of null means the forge has no request from that branch. The URL
is written the way `gw` prints one elsewhere, without a user or token. A
record naming another remote, or another URL for this one, counts as no sync
at all.

| | exit | `refs` | `forge` and `evidence` |
| --- | --- | --- | --- |
| fetched, and the forge answered for every branch | 0 | advances | advances; replaced |
| the host is not a known forge | 0 | advances | null; empty |
| `gh` or `glab` missing, or every question failed | 1 | advances | kept from the last sync |
| the forge failed for some branches | 1 | advances | time kept; those branches have no entry |
| the fetch or `set-head` failed | 1 | untouched | untouched; git's reason is on stderr |
| no remote | 0 | not written | not written |
| several remotes, no `checkout.defaultRemote` | 1 | untouched | untouched; the message lists them |

Every branch the forge did not answer for is named on stderr with the
reason, such as the first line `gh` printed. A signed-out `gh` fails every
question, so it keeps the last sync's evidence the way a missing one does.

The remote's URL picks the forge and the repository. A host of
`github.com` or `github.*` is asked through `gh`, and `gitlab.com` or
`gitlab.*` through `glab`, each passed that host and path and nothing else.
Any other host, an ssh alias among them, records no evidence.
`GH_REPO` and `GITLAB_REPO` would override the repository passed, so both
are dropped before `gh` or `glab` runs; everything else in the environment,
the token included, is passed through.

It takes `-q`, `-v` and `--explain`, and no `--json`. It has no console
script of its own.

## gw -h

```console
$ gw -h
worktrees 0.7.0: git worktree commands that refuse to lose work

COMMAND          GW                 DOES
gws              gw status (s, st)  which worktrees are finished, and why
gwp              gw prune (p)       remove the ones status marks removable
gwa NAME [BASE]  gw add (a)         create a worktree and land you in it
gw [QUERY]       gw list            pick a worktree and land you in it
gwm NEW          gw move (m, mv)    rename this branch and move it
gwr [QUERY]      gw remove (rm)     remove one whose branch is finished
gwrot            gw rotate (rot)    the next branch in a series
—                gw sync            fetch the default remote and ask the forge
—                gw version (v)     the version this package installs
gwbc NAME        gw branch create   branch off the head branch
gwbs [NAME ...]  gw branch status   which branches are finished, and why
gwbd [NAME ...]  gw branch delete   delete local branches proved finished

gw with no subcommand runs gw list.

gwa, gw, gwm and gwr change the directory you are standing in.
A binary cannot do that, so each needs the shell function of the same name
from the plugin; the rest need nothing but $PATH.
The shell plugin here is 0.7.0, the same release as these commands.
```

`gw --help` and `worktrees -h` print the same thing, on stderr, inside a
repository or out of one. One table in `cli.py` carries the names, so
what `gw -h` prints and what `gw` accepts cannot disagree, and it raises at
import if two commands ever claim one name.

## Flags

`gw version`, or `gw v`, prints the version. No command takes a `--version`
flag. Every command but that one, which prints one thing and has nothing to
be quiet about, takes `--json`, `-q`, `-v` and `--explain`, and `gw sync`
takes all of them but `--json`. Beyond those:

| | |
| --- | --- |
| `gws` | `--branch NAME`, `--delete-ignored`, `--only VERDICT` |
| `gwp` | `--branch NAME`, `--delete-ignored`, and `-y` |
| `gwr` | `-f`, `--delete-ignored`, `-y` |
| `gwbs` | `--only VERDICT` |
| `gwbd` | `--all`, `--dry-run` and `-y` |
| `gw`, `gw list` | `-l` |
| `gwa`, `gwbc`, `gwrot`, `gwm` | none |

`--only` takes `remove`, `keep` or `unknown` and can be given more than
once. It narrows the table and `--json`; the counts under the table still
cover every row.

`gw <command> --help` prints one command's own list.

While `gws`, `gwp`, `gwr`, `gwbs` and `gwbd` judge, a spinner on
stderr names the branch and the check in progress. `gwbs` and `gwbd` judge one
branch at a time, so their line also counts how far through the list they are:
`fix-parser: was it squash-merged into main? (4 of 18)`.

### What reaches the network

Only `gw sync` runs `git fetch`, `git remote set-head`, `gh` or `glab`. Every
other command reads what is already here: the remote-tracking refs, and the
forge's answer for each branch tip, which holds until the next sync and only
for the tip it was recorded at.

Every command works against one remote, the default remote:
`checkout.defaultRemote` when it names a remote, otherwise the only remote. A
repository with several remotes and no `checkout.defaultRemote` has none, and
is treated like a repository with no remote at all: no forge evidence is
read, and the head branch comes from the local `main` or `master`.
`git config checkout.defaultRemote origin` picks one.

### The head branch

Every verdict is measured against one branch, the head branch. It is the first
of these that applies:

| source | when | the head branch |
| --- | --- | --- |
| `remote` | `gw sync` has succeeded for the default remote | `<remote>/HEAD`, which the sync set |
| `clone` | no sync, but `<remote>/HEAD` exists, as `git clone` leaves it | that ref |
| `inferred` | neither | `main` or `master`, whichever exists; with both, the one whose tip was committed later, and `main` on a tie |

A `<remote>/HEAD` naming a branch that is gone is still the answer, and it
does not resolve. Nothing falls through from it to a local branch.

`gws`, `gwp`, `gwr`, `gwbs`, `gwbd`, `gwa`, `gwbc` and `gwrot` print the
head branch and its source on stderr:

```console
Head: origin/main (remote)
Head: main (inferred; master also exists)
```

`--json` leaves the line out. `-q` leaves it out too, unless the head branch
was inferred.

When the head branch does not resolve, every row `gws` and `gwbs` print is
`unknown` and they exit 1. `gwp`, `gwr` and `gwbd` refuse at exit 1 and change
nothing, and `gwa`, `gwbc` and `gwrot` fail at exit 1. The message is
`No main or master branch.` when the head branch was inferred, and otherwise
names the ref and `gw sync`, which sets it again.

Data goes to stdout and diagnostics to stderr, the prompt included, so `--json`
is parseable in every mode. Colour is decided per stream and only for a
terminal, so a redirect, a pipe, a non-empty `NO_COLOR` or `TERM=dumb` give the
bytes a pipe would have got. `--json` and `gw --list` are never coloured, even
on a terminal. `NO_COLOR=` is not a request to
turn it off, which is the no-color.org rule.

Every command is a console script, so a script reaches it with no shell loaded
at all:

```console
$ gws --json | jq -r '.verdicts[] | select(.ignored > 0) | .branch'
holds-secrets

$ gws --json | jq -r '.verdicts[] | select(.verdict=="remove") | .branch'
squash-merged

$ fish -c 'gws --json' | jq '.verdicts | length'
4

$ bash -c 'gwbc spike --json' | jq -r .base
refs/remotes/origin/main
```

## What it refuses

These git commands are refused wherever they appear, whatever flags are passed
and whatever a caller asks for:

```
reset --hard      a forced checkout or switch      clean -f
push --force      worktree remove --force          branch -D
an update-ref delete that names no full sha
```

The check runs on the argument list as it is about to be handed to git, so no
code path can assemble its way past one. `gws --explain` prints every git
command the program can issue, marking the ones that take the repository's
shared refs and therefore run serially, and ends with that list of seven,
generated from the rules rather than written out beside them.

`--force-with-lease` is not `--force` and is allowed, and so is
`update-ref -d <ref> <sha>`, which is how a branch is deleted here: against the
sha the verdict was formed on, or not at all. The sha has to be a full object
name. An absent one, an empty one, an abbreviation and a name like `HEAD` are
all refused, because git reads the empty string as "no old value" and takes
the branch at exit 0, which is `branch -D` spelled longer. `update-ref --stdin`
is refused with them: it deletes refs without `-d` appearing at all.

## Agent plugin

`plugin/` is a plugin for Claude Code and Codex. `/worktrees:prune` runs `gws`
and `gwbs`, shows the worktrees and branches they proved finished, asks which
to remove, and removes those with `gwp` and `gwbd`. It is listed in the
`release-tools` marketplace:

```console
claude plugin marketplace add releasetools/agent-plugins
claude plugin install worktrees@release-tools
```

[plugin/README.md](plugin/README.md) covers Codex, what the command shows and
what it will not do.

## Contributing

The Python, the tests and the release procedure are in
[docs/DEVELOPMENT.md](docs/DEVELOPMENT.md). The rules the code follows are in
[AGENTS.md](AGENTS.md). What changed in each release is in
[CHANGELOG.md](CHANGELOG.md). What was decided and what must not break,
including the alternatives that were closed, is in
[docs/REFERENCE.md](docs/REFERENCE.md).

## Licence

Apache-2.0, Mihai Bojin. See [LICENSE](LICENSE).
