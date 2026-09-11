# Development

Python 3.11 through 3.14, no runtime dependencies, `uv` for everything else.

```console
uv sync --all-extras
uv run --no-sync pytest tests
uv run --no-sync pre-commit run --all-files
```

`uv sync --all-extras --locked` is what CI runs, so a stale `uv.lock` fails
there rather than resolving something the lock never described.

The rules this code follows, and the reasoning behind them, are in
[AGENTS.md](../AGENTS.md). This file is what to run.

## Layout

| | |
| --- | --- |
| `src/worktrees/git.py` | the decorator, the guard, the registry |
| `src/worktrees/repo.py` | what the repository is: worktrees, remote, head branch |
| `src/worktrees/merged.py` | whether a branch's change is already upstream |
| `src/worktrees/verdicts.py` | one verdict per worktree, and nothing that mutates |
| `src/worktrees/prune.py` | the six commands that mutate, and the sweep |
| `src/worktrees/new_branch.py` | alpha: starting a branch off the head branch |
| `src/worktrees/rotate.py` | alpha: the next branch in a series |
| `src/worktrees/cli.py` | argument parsing and output |

`pyproject.toml` maps each console script to an entry point in `cli.py`.

`new_branch.py` and `rotate.py` are alpha. They start branches rather than
worktrees, `origin new-branch` and `origin rotate` already do the same job,
and only one of the two sets survives. Keep them matching `origin` rather
than improving on it: a difference between them is a decision somebody has
to make later, and there is no plan that makes both correct.

## Working on a checkout

```console
uv tool install --from . git-worktrees
```

That installs the working copy in place of anything published, so `gws` on
your `$PATH` is the code in front of you. `uv tool uninstall git-worktrees`
puts it back.

The distribution is `git-worktrees` because PyPI already holds `worktrees`.
The import package is `worktrees` and every console script keeps its own
name.

## Every git call is a spec

Each git command the program can run is one decorated function, written the
way you would type the command, with `$name` where a value goes:

```python
@git("worktree list --porcelain -z")
def worktree_records(): ...


@git("merge-base --is-ancestor $ref $head", ok=(0, 1))
def is_ancestor(ref, head): ...


@git("worktree remove -- $path", mutates=True)
def remove_worktree(path): ...
```

`shlex.split` runs once at decoration time on the literal spec, before any
value exists. So a branch named `$(id)` becomes one argv element and stays a
name: git accepts that as a refname, and the suite creates it. `--explain`
prints all 28 specs, marking the six that mutate.
A guard runs on the resolved argv inside the wrapper, so no call site can
assemble its way past it: `reset --hard`, a forced `checkout` or `switch`,
`clean -f`, a bare `push --force`, `worktree remove --force` and `branch -D`
are refused whatever flags are passed.

## Commands that land you somewhere need a shell function

A binary cannot `cd` its caller. A command that does gets a shell function of
the same name, whose whole body is `command <name>` and a `cd`, so a script
calling the binary still gets the path and only loses the move. `gws`, `gwp`
and `gwnb` need none; `gwa`, `gwl`, `gwr` and `gwm` will, and
[issue #3](https://github.com/MihaiBojin/worktrees/issues/3) carries the rest
of it.

```fish
function gwa --wraps gwa
    set -l dest (command gwa $argv | string collect)
    set -l code $pipestatus[1]
    test $code -eq 0; or return $code
    test -n "$dest"; or return 0
    cd -- $dest
end
```

`string collect` because fish splits command substitution on newlines and a
path may hold one. `$pipestatus[1]` because that pipeline's `$status` belongs
to `string collect`, which returns 1 whenever it collected nothing, which is
the failure case. bash and zsh need neither:

```bash
gwa() {
  local dest
  dest="$(command gwa "$@")" || return
  [ -n "$dest" ] || return 0
  cd -- "$dest"
}
```

## Tests

99 of them, on Python 3.11 through 3.14 in CI. They drive the entry points
through `subprocess`, so env, cwd, both streams and the exit code stay
per-test, and the prompt tests drive a real pty.

The fixture needs no global git config, no signing key, no forge and no
network. `GIT_CONFIG_GLOBAL` carries that: without it git still reads
`$HOME/.gitconfig`, and setting `HOME` alone still leaves
`$XDG_CONFIG_HOME/git/config`.

## Publishing a version

```console
/release 0.2.0
```

The skill at `.agents/skills/release/` does the whole of it: cut
`release/v0.2.0` from `origin/main`, `uv version 0.2.0`, open the pull
request, wait for its checks, merge with `--rebase`, wait for main's own run
on the rebased commit, and only then tag. The tag lands on a commit already
proved green, so it never has to move.

`scripts/check-releasable.bash` runs first and refuses a version that is not
`x.y.z`, a dirty tree, a version that is not after the one on `origin/main`,
a tag that already exists on the remote, and a version PyPI already carries.
That last one matters most: PyPI rejects a duplicate at the very end of a
publish run, after everything else has already happened.

`/release-notes:draft` writes the `CHANGELOG.md` entry on the release branch,
so the notes are reviewed in the same pull request as the version bump. It
comes from the ReleaseTools marketplace:

```console
claude plugin marketplace add releasetools/agent-plugins
claude plugin install release-notes@ReleaseTools
```

`publish.yml` reads that section back out for the GitHub release, and `build`
refuses a tag whose version has no section, before anything is published.
Nothing is generated from pull request titles.

By hand it is the same four commands:

```console
uv version 0.2.0          # rewrites pyproject.toml and re-locks
# open a pull request, review it, merge it
git switch main && git pull
git tag -a v0.2.0 -m v0.2.0
git push origin v0.2.0
```

The version lives in `pyproject.toml`, in the repository, on every commit,
and in git history. The tag agrees with it or nothing is published. A tag
moved onto a commit carrying a different version is refused, which is the
integrity check a tag-derived version could not have:

```console
$ scripts/check-tag-version.bash
Tag/version mismatch, refusing to publish.
  tag(s) at HEAD:         9.9.9
  pyproject.toml version: 0.1.0
```

Pushing the tag starts `publish.yml`:

```
build ──> publish-test ──> publish ──> release
```

`build` runs three guards before it does anything, cheapest first. The tag has
to name the version the commit carries. The commit has to be one `main` took,
so nothing is released from a tree no review ever saw. And that commit's test
run has to have passed already, so a doomed release stops before it reaches
TestPyPI. Then it runs the suite again and hands one artifact to every job
below, so what reaches PyPI is byte-for-byte what TestPyPI accepted.

TestPyPI gates the real upload on purpose: a PyPI upload cannot be undone or
replaced, so a failed rehearsal stops the run while there is still nothing to
pin against. The merge happens first and the irreversible step is last, so
everything recoverable is already done by the time anything is published.

`workflow_dispatch` against a tag ref re-runs a release whose publish failed.

No API token is stored anywhere. Both uploads use PyPI Trusted Publishing over
OIDC, which needs one registration on each index before the first release:

| field | value |
| --- | --- |
| owner | `MihaiBojin` |
| repository | `worktrees` |
| workflow | `publish.yml` |
| environment | `testpypi` on TestPyPI, `pypi` on PyPI |

Both GitHub environments have to exist under Settings, Environments. Adding a
required reviewer to `pypi` puts a manual gate in front of the real index.

**Renaming `publish.yml` breaks publishing.** PyPI matches a request against
the repository, that filename and the environment, so a rename means
re-registering the publisher first.
