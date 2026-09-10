# Working in this repository

A Python CLI for git worktrees. `pyproject.toml` declares one console script
per command; `src/worktrees/` holds them; `tests/` drives the entry points
through `subprocess` so env, cwd, both streams and the exit code stay per-test.

```console
uv sync --all-extras
uv run --no-sync pytest tests
uv run --no-sync pre-commit run --all-files
```

## Binaries and shell functions are not interchangeable

A shell function of a given name beats a binary of that name in bash, zsh and
fish, and in fish it beats it in `fish -c` too, where the function is autoloaded
from `functions/`. So a command that exists as both runs different code from a
prompt and from a script unless the function is a pure adapter.

The rule: **a function exists only for a command that must change the caller's
directory, it carries the same name as the binary, and its body does nothing but
call `command <name>` and `cd` to what that prints.**

| | changes directory | ships as |
| --- | --- | --- |
| `gws`, `gwp`, `gwnb` | no | a console script, and nothing else |
| a command that lands you somewhere | yes | a console script, plus a function of the same name |

A binary cannot `cd` its caller. That is the only thing shell code is here for,
and any logic that reaches a shell file is logic a script cannot call.

Two traps a shim has to handle, both measured:

- fish splits command substitution on newlines, so a path containing one
  arrives as two elements. `(command gwa $argv | string collect)` keeps it
  whole. bash and zsh strip only trailing newlines, so `"$(gwa)"` is already
  correct there.
- that pipeline's `$status` belongs to `string collect`, which returns 1
  whenever it collected nothing, which is the failure case. The command's own
  code is `$pipestatus[1]`.

```fish
function gwa --wraps gwa
    set -l dest (command gwa $argv | string collect)
    set -l code $pipestatus[1]
    test $code -eq 0; or return $code
    test -n "$dest"; or return 0
    cd -- $dest
end
```

`command` is what stops the function calling itself, in all three shells.

## Where shell code goes

Fisher copies `functions/`, `completions/` and `conf.d/` from the repository
root and nothing else. Antidote takes a `path:` into the repository and wants a
`<name>.plugin.zsh` there. So the two live in different places:

```
functions/gwa.fish                   fish, installed by Fisher
completions/gwa.fish
zsh/plugins/worktrees/worktrees.plugin.zsh    zsh, installed by Antidote
zsh/plugins/worktrees/functions/gwa
zsh/plugins/worktrees/completions/_gwa
```

Neither manager puts a binary on `$PATH`. The console scripts arrive through
`uv tool install` from the published package, which is a separate step.

Nothing installs this from a checkout on the user's disk. It is consumed as a
third-party plugin and a third-party tool, so anything that assumes a local
clone at a known path is wrong here.

A completion asks the CLI for its candidates rather than deriving them. The
ranking is tested in Python; a shell file that reimplements it is a second
answer to the same question.

## The picker is Python's, and fzf is only the widget

Building the candidate list, ranking it, deciding what each row shows, reading
the answer back: all of it in the CLI. `fzf` is spawned by the CLI with the
candidates on its stdin, and the selection is read from its stdout. No shell
file ever holds a candidate, a format string or a rank, so there is one
implementation rather than one per dialect.

This works in the shim's own configuration, where the CLI's stdout is already a
pipe. `fzf` draws on `/dev/tty` rather than on stdout, so the two do not
collide:

```
                     tty  ->  fzf's UI
cd (command gwl)  ->  CLI  ->  fzf  ->  selection  ->  CLI's stdout  ->  cd
```

The rules that come with it, both the same shape as `gwp`'s prompt:

- No terminal, no picker. Refuse at exit 2 and name the flag that answers
  without one, rather than launching something nothing can drive.
- No `fzf` on `$PATH` is not a failure. Fall back to a numbered prompt read
  from the tty, so the command works on a machine that never installed it.

`--json` and `--list` answer the same question without any of this, and an
agent uses those.

## Every git command is one spec

The spec is the command as you would type it, with `$name` where a value goes.
The module reads as the list of commands the program can run, and `--explain`
prints the specs themselves.

```python
@git("merge-base --is-ancestor $ref $head", ok=(0, 1))
def is_ancestor(ref, head): ...


@git("--no-optional-locks status --porcelain --ignored=traditional")
def ignored_paths(): ...


@git("commit-tree $tree -p $parent -m _", env=_SYNTHETIC, ok=(0, 128))
def commit_tree(tree, parent): ...
```

`shlex.split` runs once, at decoration time, on the literal spec. Only then is
each token scanned for a placeholder. That ordering is the whole safety
property: the splitting is over before any value is seen, so a branch named
`$(id)`, `a"b` or `has space` lands as exactly one argv element. git accepts
the first two as branch names, and there is a test that creates them.

`$` rather than `{}`, because git's revision syntax is full of braces:
`^{tree}`, `^{commit}` and `@{upstream}` pass through untouched. `$$` is a
literal `$`, and so is a `$` with no name after it.

Three ways to place a value: `$name` anywhere, including inside a token so
`--format=$fmt` stays one element; `$*name` splats a list at that position;
and anything the body returns is appended as a tail, for arguments with no
fixed place. Names bind from `inspect.signature`, so there is no second
mapping to keep in step, and a spec naming a parameter the function does not
have raises `NameError` at import.

The form catches its own bugs. `-C $path status --porcelain
--no-optional-locks` is wrong, because `--no-optional-locks` is a git global
and belongs before the subcommand. That is visible in a spec and invisible in
a tuple.

`guard()` runs on the resolved argv inside the wrapper, not at declaration, so
no call site can assemble its way past it. `reset --hard`, a forced `checkout`
or `switch`, `clean -f`, a bare `push --force`, `worktree remove --force` and
`branch -D` are refused absolutely. There is no flag, and `--yes` least of all.

`verdicts.py` declares no mutating command. `prune.py` declares the six that
mutate. A read-only command may not call one: a test reads the verbose log and
asserts it.

## Three verdicts, and the third is not a softer second

`go` was proved finished, `keep` has a reason not to be, and `unknown` could
not tell. "No upstream, so nothing says whether this was pushed" is a different
fact from "not merged", and printing them the same way invites somebody to act
on the wrong one. `prune.removable()` returns the `go` set and both commands
call it, so what one prints and the other removes cannot drift.

## Reproduce a failure before asserting its fix

Two holes in git are the reason this exists: a squash merge inverts
`git branch -d`, and `git worktree remove` deletes ignored files at exit 0
without a word. Each has a test that reproduces the raw git behaviour and a
test that asserts the fix. A suite that only asserts the fix passes on unfixed
code forever.

The fixture needs no global git config, no signing key, no forge and no
network. `GIT_CONFIG_GLOBAL` carries that: without it git still reads
`$HOME/.gitconfig`, and setting `HOME` alone still leaves
`$XDG_CONFIG_HOME/git/config`.
