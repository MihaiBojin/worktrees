# worktrees

Four commands, and none of them needs a shell wrapper: nothing here cds its
caller.

| | |
| --- | --- |
| `gw` | the CLI itself, taking a subcommand. `worktrees` is the same program under its long name |
| `gws` | say which worktrees are finished, and why. Removes nothing, and has no flag that could |
| `gwp` | remove the ones `gws` marks removable, having asked first |
| `gwnb NAME` | fetch, then branch `NAME` off the head branch and check it out |
| `gwrot` | start the next branch after this one, or catch the head branch up |

## Install

```console
uv tool install git+https://github.com/MihaiBojin/worktrees
```

One venv of 124 KB serves every command name, at about 1 KB each, all of them
into `~/.local/bin`. A tag installs that release and nothing later:

```console
uv tool install git+https://github.com/MihaiBojin/worktrees@v0.1.0
```

Python 3.11 or newer, and no dependencies. The standard library answers every
question this asks, so an invocation pays for the interpreter and nothing
else.

Shell functions and completions arrive separately, through a plugin manager,
the same way any other plugin does. Nothing here ships shell code yet; when
it does, Fisher reads `functions/`, `completions/` and `conf.d/` from the
repository root and Antidote takes a `path:` into it:

```console
fisher install MihaiBojin/worktrees
```

```
MihaiBojin/worktrees path:zsh/plugins/worktrees   # in zsh_plugins.txt
```

Neither manager puts a binary on `$PATH`, which is why the two installs stay
separate.

## gws

```console
$ gws
VERDICT  BRANCH         WHY
go       squash-merged  squash-merged
keep     dirty-work     it has uncommitted changes
keep     holds-secrets  merged, but holds 2 ignored path(s); pass --delete-ignored
unknown  never-pushed   not merged into main, and no upstream says whether its commits were pushed

1 removable, 2 kept, 1 unclear
```

### Three verdicts

`unknown` is not `keep` with a softer word.

| | means |
| --- | --- |
| `go` | finished, and safe to remove. The reason says how it was proved: `merged`, `squash-merged`, or a ref reaching a detached commit |
| `keep` | something says no. Uncommitted work, the head branch, a lock, the worktree you are standing in, ignored files, or a branch simply not merged |
| `unknown` | it could not tell. A branch with no upstream is the usual one: nothing says whether its commits were pushed anywhere |

A record left behind by a directory somebody deleted by hand is nobody's
verdict. `gws` names how many there are; clearing them is `git worktree prune`,
which mutates, so `gwp` is what runs it.

### Two holes in git

**A squash merge inverts `git branch -d`.** A branch whose change is already in
`main` reports `error: the branch 'x' is not fully merged` and points you at
`-D`, which deletes anything. By hand, `squashed` and `unmerged` are
indistinguishable: both report NOT merged, both sit one commit ahead. Opposite
correct actions, no signal between them.

The probe that separates them replays the branch's tree as one commit on the
merge base and asks `git cherry` whether that patch is already upstream. It
compares content rather than history, which is what a squash preserves.

**`git worktree remove` silently deletes ignored files.** With `.env` and
`node_modules/` present, `git status --porcelain` prints nothing,
`git worktree remove` exits 0 with no `--force`, and both are gone. Nothing in
git brings them back: no ref ever pointed at them. `gws` reads
`--ignored=traditional`, counts what would go, and marks the worktree `keep`
until you pass `--delete-ignored`.

## gwp

```console
$ gwp
squash-merged  ~/git/.worktrees/squash-merged/repo
  restore with: git branch squash-merged d79e417
remove 1 worktree(s)? [y/N]
```

It removes exactly the rows `gws` marks `go`, under the same flags, and one
function returns that set for both. What goes and what puts it back is printed
before anything does, and neither `--quiet` nor `--yes` silences it.

`-y` skips the question. A run whose stdin is not a terminal refuses rather
than blocking, because an agent or a pipe reaching a prompt would hang forever
holding the repository's worktrees:

```console
$ gwp < /dev/null
not a terminal, so nothing can answer for the 1 above; pass --yes to remove them
```

The checkout goes through plain `git worktree remove`, so every refusal git
makes still applies. The branch goes through `git branch -d`, never `-D`, so a
squash-merged branch keeps its ref after its worktree is gone.

There is no `--dry-run`. `gws` reports and `gwp` asks, so a flag meaning "do
not act" would be a no-op wearing the clothes of a safety feature, and somebody
would one day cite it as the reason a sweep was safe.

## gwnb

```console
$ gwnb fix-parser
fix-parser from origin/main at 4a91c02
```

The base is `<remote>/<head>` as it stands after the fetch, not the local copy
of it, so the branch starts on top of what the server has and nothing has to be
rebased afterwards. It travels as a full ref, because git resolves a bare name
as a tag first and a repository holding a tag called `origin/main` would branch
from the tag.

`--no-track`, so the head branch does not become the new branch's upstream. A
branch that tracked it would take it as its upstream and `git push` would
target the head branch.

A failed fetch is not fatal. An offline machine still gets a branch, off
whatever it last saw, and the line at the end names the commit it got.

## gwrot

```console
$ gwrot
fix-parser-2026-09-11_001 from origin/main at 4a91c02
```

It takes no argument. The name comes from the branch you are standing on,
with any suffix a previous rotation added already stripped, so four
rotations in a day give four siblings rather than one name carrying four
suffixes:

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

## Flags

`--branch NAME`, `--no-fetch`, `--delete-ignored`, `--json`, `-q`, `-v` and
`--explain` are shared by `gws` and `gwp`. `-y` is `gwp` alone. `gwnb` and
`gwrot` take `--no-fetch`, `--json`, `-q`, `-v` and `--explain`.

Data goes to stdout and diagnostics to stderr, including the prompt, so
`--json` is parseable in every mode.

## From a prompt, and from a script

Every command here is a console script, so a script reaches it with no shell
loaded at all. `gws` and `gwnb` answer identically either way:

```console
$ gws --json | jq -r '.verdicts[] | select(.verdict=="go") | .branch'
fix-parser

$ fish -c 'gws --json' | jq '.verdicts | length'
4

$ bash -c 'gwnb spike --json' | jq -r .base
refs/remotes/origin/main
```

`gwp` is the one that reads the terminal, and it does so deliberately. With a
tty it asks; without one it refuses rather than blocking, because a script or
an agent reaching a prompt would hang holding the repository's worktrees:

```console
$ gwp                       # at a prompt
squash-merged  ~/git/.worktrees/squash-merged/repo
  restore with: git branch squash-merged d79e417
remove 1 worktree(s)? [y/N] y
removed 1 worktree(s)

$ gwp < /dev/null           # in a script, a pipe, a CI job
not a terminal, so nothing can answer for the 1 above; pass --yes to remove them

$ gwp --yes --json          # what an agent runs
{"removed": ["squash-merged"], "failed": 0}
```

Data is on stdout and every diagnostic on stderr, the prompt included, so
`--json` parses in all three cases.

## What it refuses

Six git commands are refused wherever they appear, whatever flags are passed
and whatever a caller asks for:

```
reset --hard      a forced checkout or switch      clean -f
push --force      worktree remove --force          branch -D
```

The check runs on the argument list as it is about to be handed to git, so no
code path can assemble its way past one. `gws --explain` prints every git
command the program can issue, all 28 of them, marking the six that change
anything.

`--force-with-lease` is not `--force` and is allowed. `branch -d` is not
`branch -D` and is how a branch is deleted here: on git's own proof of merge,
or not at all.

## Contributing

The Python, the tests and the release procedure are in
[docs/DEVELOPMENT.md](docs/DEVELOPMENT.md). The rules the code follows are in
[AGENTS.md](AGENTS.md).
