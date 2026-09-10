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

## Every git command is one decorated function

The body returns the argv that follows `git`, so the module reads as the list
of commands the program can run and `--explain` prints it.

```python
@git(ok=(0, 1))
def is_ancestor(ref, head):
    """Non-zero means 'no', not 'broken'."""
    return "merge-base", "--is-ancestor", ref, head
```

The argv is a real list. Nothing is ever a shell string, so a branch name
cannot inject. `--explain` derives its output by calling each body with
`<param>` placeholders, which is sound only while a body does nothing but
return a tuple built from its parameters. Keep it that way.

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
