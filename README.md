# worktrees

[![Tests](https://github.com/MihaiBojin/worktrees/actions/workflows/tests.yml/badge.svg)](https://github.com/MihaiBojin/worktrees/actions/workflows/tests.yml)
[![PyPI](https://img.shields.io/pypi/v/git-worktrees)](https://pypi.org/project/git-worktrees/)

Git worktree commands that refuse to lose work.

`gws`, `gwp`, `gwnb`, `gwrot` and `gwh` answer a question and print it. `gwa`,
`gwl`, `gwm` and `gwr` land you somewhere, and each of those needs a shell
function, because a binary cannot change its caller's directory.

| | |
| --- | --- |
| `gws` | say which worktrees are finished, and why. Removes nothing, and has no flag that could |
| `gwp` | remove the ones `gws` marks removable, having asked first |
| `gwa NAME [BASE]` | create a worktree for `NAME` and land you in it |
| `gwl [QUERY]` | pick one of this repository's worktrees and land you in it |
| `gwm NEW` | rename this worktree's branch and move the checkout to match |
| `gwr [QUERY]` | remove a worktree whose branch is finished, and the branch |
| `gwnb NAME` | alpha. Fetch, then branch `NAME` off the head branch and check it out |
| `gwrot` | alpha. Start the next branch after this one, or catch the head branch up |
| `gwh` | print that table, with every name each command answers to |

`gw` takes the same commands as subcommands, each with shorthands, so `gwl`,
`gw list`, `gw ls` and `gw l` are one command:

| | |
| --- | --- |
| `gws` | `gw status`, `st`, `s` |
| `gwp` | `gw prune`, `p` |
| `gwa` | `gw add`, `a` |
| `gwl` | `gw list`, `ls`, `l` |
| `gwm` | `gw move`, `mv`, `m` |
| `gwr` | `gw remove`, `rm` |
| `gwnb` | `gw new-branch`, `nb`, `new` |
| `gwrot` | `gw rotate`, `rot` |
| `gwh` | `gw help`, `h`, and `gw` with no subcommand |

`worktrees` is the same program under its long name.

`gws` and `gwp` are what this tool is for and their behaviour is settled.

**`gwnb` and `gwrot` are alpha and may go.** They start branches rather than
worktrees, which is a different job from the one this repository exists to do,
and `origin new-branch` and `origin rotate` already do it. Only one of the two
sets survives. Until that is decided the names, the flags and the output may
change, and nothing should be built on top of them.

## Two holes in git

### A squash merge inverts `git branch -d`

A branch whose change is already in `main` reports `error: the branch 'x' is
not fully merged` and points you at `-D`, which deletes anything. By hand,
`squashed` and `unmerged` are indistinguishable: both report NOT merged, both
sit one commit ahead. Opposite correct actions, no signal between them.

The probe that separates them replays the branch's tree as one commit on the
merge base and asks `git cherry` whether that patch is already upstream. It
compares content rather than history, which is what a squash preserves.

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

`gwa`, `gwl`, `gwm` and `gwr` need a shell function as well, and it arrives
through a plugin manager. Fisher takes the four from `functions/`, plus the
`conf.d/` file that initialises nothing:

```console
fisher install MihaiBojin/worktrees
```

Antidote takes a `path:` into it:

```
MihaiBojin/worktrees path:zsh/plugins/worktrees   # in zsh_plugins.txt
```

Each function runs `command <name>`, reads the one path the binary printed and
`cd`s there, and carries nothing else. `gws`, `gwp`, `gwnb` and `gwrot` change
no directory, so they ship as console scripts alone and answer the same from a
prompt and from a script.

Neither manager puts a binary on `$PATH`, which is why the two installs stay
separate.

## gws

```console
$ gws
VERDICT  BRANCH         WHY                                                                                PATH
keep     dirty-work     it has uncommitted changes                                                         /home/you/git/.worktrees/dirty-work/repo
keep     holds-secrets  merged, but holds 2 ignored path(s); pass --delete-ignored                         /home/you/git/.worktrees/holds-secrets/repo
unknown  never-pushed   not merged into origin/main, and no upstream says whether its commits were pushed  /home/you/git/.worktrees/never-pushed/repo
remove   squash-merged  squash-merged                                                                      /home/you/git/.worktrees/squash-merged/repo

1 removable, 2 kept, 1 unclear
unclear, and gwp does not touch these: never-pushed
gwp removes the 1 marked removable
```

Rows come in `git worktree list` order. Paths are absolute wherever this
prints one, so a row can be pasted into the next command. The table and the
count are on stdout; the two lines after them are on stderr.

On a terminal the verdict carries its own colour, `remove` green, `keep` blue
and `unknown` yellow, with the branch in bold and the path dimmed. That
decision is made per stream: redirect it, pipe it, set `NO_COLOR` to anything
non-empty or run under `TERM=dumb` and the bytes are the ones above.

### Three verdicts

Each is the instruction it gives. `unknown` is not `keep` with a softer
word.

| | means |
| --- | --- |
| `remove` | finished, and safe to remove. The reason says how it was proved: `merged`, `squash-merged`, a merged request on the forge, or a ref reaching a detached commit |
| `keep` | something says no. Uncommitted work, the head branch, a lock, the worktree you are standing in, ignored files, or a branch simply not merged |
| `unknown` | it could not tell. A branch with no upstream is the usual one: nothing says whether its commits were pushed anywhere |

Where git cannot tell, the forge is asked. A branch merged as part of a
stack is that case: its changes reach the head branch across several
squashes, so the intermediate state it holds differs from the final one in
the same regions, and a diff cannot separate a stale branch from one with
work left. A merged pull request settles it, cross-checked against the
commits an upstream has not got, because a request speaks for what reached
it and nothing about what never did. `--no-forge` decides from git alone.

A record left behind by a directory somebody deleted by hand is nobody's
verdict. `gws` names how many there are; clearing them is `git worktree prune`,
which mutates, so `gwp` is what runs it.

## gwp

```console
$ gwp
squash-merged  /home/you/git/.worktrees/squash-merged/repo
  restore with: git branch squash-merged a2b41ad1fb271bd7d84256f0d35ee5d58c29dd08
remove 1 worktree(s)? [y/N] y
branch squash-merged kept: git branch -d -- squash-merged exited 1: error: the branch 'squash-merged' is not fully merged
hint: If you are sure you want to delete it, run 'git branch -D squash-merged'
hint: Disable this message with "git config set advice.forceDeleteBranch false"
removed 1 worktree(s)
```

It removes exactly the rows `gws` marks `remove`, under the same flags, and one
function returns that set for both. What goes and what puts it back is printed
before anything does, and neither `--quiet` nor `--yes` silences it. The sha in
the restore line is full length, because that line is meant to be pasted.

The checkout goes through plain `git worktree remove`, so every refusal git
makes still applies. The branch goes through `git branch -d`, never `-D`, which
is why the branch above outlives its worktree and says so in git's own words.

`-y` skips the question. A run whose stdin is not a terminal refuses rather
than blocking, because an agent or a pipe reaching a prompt would hang forever
holding the repository's worktrees:

```console
$ gwp < /dev/null
squash-merged  /home/you/git/.worktrees/squash-merged/repo
  restore with: git branch squash-merged a2b41ad1fb271bd7d84256f0d35ee5d58c29dd08
not a terminal, so nothing can answer for the 1 above; pass --yes to remove them
```

That exits 2 and touches nothing. `gwp --yes --json` is what an agent runs.

With nothing to remove it prints the table `gws` prints, rather than the name
of the command that would have printed it. The reason each worktree stayed is
the answer to the question, and naming another command puts it one round trip
away:

```console
$ gwp
VERDICT  BRANCH         WHY                                                                         PATH
keep     dirty-work     it has uncommitted changes                                                  /home/you/git/.worktrees/dirty-work/repo
keep     holds-secrets  squash-merged, but holds 2 ignored path(s); pass --delete-ignored           /home/you/git/.worktrees/holds-secrets/repo
unknown  never-pushed   not merged into main, and no upstream says whether its commits were pushed  /home/you/git/.worktrees/never-pushed/repo

nothing to remove; 0 removable, 2 kept, 1 unclear
```

There is no `--dry-run`. `gws` reports and `gwp` asks, so a flag meaning "do
not act" would be a no-op wearing the clothes of a safety feature, and somebody
would one day cite it as the reason a sweep was safe.

## gwa, gwl, gwm, gwr

Worktrees live at `<PARENT>/.worktrees/<NAME>/<REPO>`, derived rather than
configured, so two tools cannot disagree about a location neither can be
told. A branch name with slashes becomes directories, and the repository
name goes last, so `auth/oauth` cannot collide with `auth`.

```console
$ gwa fix-parser          # creates it and lands you in it
$ gwl parse               # one match takes it outright
$ gwm parser-v2           # renames the branch and moves the checkout
$ gwr                     # removes the one you are standing in
```

Each prints one destination on stdout and nothing else, which is what the
shell function reads. `gwr` prints a path **only** when you were standing in
what it removed; empty output means stay put.

`gwm` is the one that is not a convenience. Renaming the directory you are
standing in leaves the shell with a stale `$PWD` and every later command
failing:

```console
$ git status
fatal: Unable to read current working directory: No such file or directory
```

`gwl` offers the main checkout too. It is where a finished branch leaves you
and the one destination that is always there, so a list without it is a list
of everywhere except the place you most often want. `gwl main` finds it
whatever branch it stands on, and `--list` marks the one you are in:

```console
$ gwl --list
  main           /home/you/git/repo
  dirty-work     /home/you/git/.worktrees/dirty-work/repo
  holds-secrets  /home/you/git/.worktrees/holds-secrets/repo
* never-pushed   /home/you/git/.worktrees/never-pushed/repo
```

It never offers that one. Picking it is the one answer that cannot take you
anywhere, and a picker whose single candidate is where you already are prints
nothing at all, which reads as a broken command. So two worktrees and no query
is not a question, and asking for the one you are in says so and stays put:

```console
$ gwl dirty          # standing in dirty-work
already in dirty-work
```

With more than one worth offering it asks, numbered:

```console
$ gwl                # standing in the main checkout
  1  dirty-work     /home/you/git/.worktrees/dirty-work/repo
  2  holds-secrets  /home/you/git/.worktrees/holds-secrets/repo
  3  never-pushed   /home/you/git/.worktrees/never-pushed/repo
which? [1-3, or blank to cancel]
```

Matching is substring first and then subsequence, so `tst` finds `add-tests`,
and a substring hit always outranks a loose one. Without a terminal `gwl`
refuses at exit 3 and names `--list` and `--json`.

`gwr` refuses a branch that is not finished and says why, the same verdict
`gws` prints. `--force` removes the checkout and keeps the branch: the
worktree was in the way, the work was not.

## gwnb and gwrot (alpha)

Both are alpha and both start branches rather than worktrees. See the note
under the command table.

```console
$ gwnb fix-parser
fix-parser from origin/main at 4a91c02
```

The base is `<remote>/<head>` as it stands after the fetch, not the local copy
of it, so the branch starts on top of what the server has and nothing has to be
rebased afterwards. It travels as a full ref, because git resolves a bare name
as a tag first and a repository holding a tag called `origin/main` would branch
from the tag. `--no-track`, so the head branch does not become the new branch's
upstream and `git push` does not target it. A failed fetch is not fatal: an
offline machine still gets a branch, off whatever it last saw, and the line at
the end names the commit it got.

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

On the head branch there is no chain to continue, so it catches that up
instead:

```console
$ gwrot
main is at origin/main
```

That is `merge --ff-only`, never a rebase: rewriting local commits on the
head branch is the class this tooling refuses everywhere else. Commits the
remote does not have are refused and listed, because they are a change of
their own and `gwnb` puts them on a branch.

## gwh

```console
$ gwh
worktrees 0.2.0: git worktree commands that refuse to lose work

COMMAND          GW                       DOES
gws              gw status (s, st)        which worktrees are finished, and why
gwp              gw prune (p)             remove the ones status marks removable
gwa NAME [BASE]  gw add (a)               create a worktree and land you in it
gwl [QUERY]      gw list (l, ls)          pick a worktree and land you in it
gwm NEW          gw move (m, mv)          rename this branch and move it
gwr [QUERY]      gw remove (rm)           remove one whose branch is finished
gwnb NAME        gw new-branch (nb, new)  branch off the head branch (alpha)
gwrot            gw rotate (rot)          the next branch in a series (alpha)
gwh              gw help (h)              every command and alias, this list
```

`gw help`, `gw h` and `gw` with no subcommand print the same thing. One table
in `cli.py` carries the names, so what `gwh` prints and what `gw` accepts
cannot disagree, and it raises at import if two commands ever claim one name.

## Flags

Every command takes `--version`. All but `gwh`, which prints a table and has
nothing to be quiet about, also take `--json`, `-q`, `-v` and `--explain`.
Beyond those:

| | |
| --- | --- |
| `gws` | `--branch NAME`, `--no-fetch`, `--delete-ignored`, `--no-forge` |
| `gwp` | those four, and `-y` |
| `gwr` | `-f`, `--delete-ignored`, `--no-fetch`, `-y` |
| `gwa`, `gwnb`, `gwrot` | `--no-fetch` |
| `gwl` | `-l` |
| `gwm`, `gwh` | none |

`gw <command> --help` prints one command's own list.

Data goes to stdout and diagnostics to stderr, the prompt included, so `--json`
is parseable in every mode. Colour is decided per stream and only for a
terminal, so a redirect, a pipe, a non-empty `NO_COLOR` or `TERM=dumb` give the
bytes a pipe would have got, `--json` included. `NO_COLOR=` is not a request to
turn it off, which is the no-color.org rule.

Every command is a console script, so a script reaches it with no shell loaded
at all:

```console
$ gws --json | jq -r '.verdicts[] | select(.verdict=="remove") | .branch'
squash-merged

$ fish -c 'gws --json' | jq '.verdicts | length'
4

$ bash -c 'gwnb spike --json' | jq -r .base
refs/remotes/origin/main
```

## What it refuses

These git commands are refused wherever they appear, whatever flags are passed
and whatever a caller asks for:

```
reset --hard      a forced checkout or switch      clean -f
push --force      worktree remove --force          branch -D
```

The check runs on the argument list as it is about to be handed to git, so no
code path can assemble its way past one. `gws --explain` prints every git
command the program can issue, marking the ones that take the repository's
shared refs and therefore run serially.

`--force-with-lease` is not `--force` and is allowed. `branch -d` is not
`branch -D` and is how a branch is deleted here: on git's own proof of merge,
or not at all.

## Contributing

The Python, the tests and the release procedure are in
[docs/DEVELOPMENT.md](docs/DEVELOPMENT.md). The rules the code follows are in
[AGENTS.md](AGENTS.md). What changed in each release is in
[CHANGELOG.md](CHANGELOG.md).

## Licence

Apache-2.0, Mihai Bojin. See [LICENSE](LICENSE).
