# Development

Python 3.11 through 3.14, no runtime dependencies, `uv` for everything else.

```console
uv sync --all-extras
uv run --no-sync pytest tests
uv run --no-sync pre-commit run --all-files
```

`uv sync --all-extras --locked` is what CI runs, so a stale `uv.lock` fails
there rather than resolving something the lock never described.
`pre-commit install` writes the git hook, so the same checks run at `git
commit` rather than only at CI. Do it once per clone:

```console
uv run --no-sync pre-commit install
```

Without it, `pre-commit run --all-files` is the only thing that runs them,
and it reads `git ls-files`: a file that is new and not yet staged is
invisible to it, so CI is the first thing to format a file you just wrote.
`git add` before running the hooks, or let the installed hook do it for you.


The rules this code follows, and the reasoning behind them, are in
[AGENTS.md](../AGENTS.md). What was decided, what must not break and which
failure classes have a test are in [REFERENCE.md](REFERENCE.md). This file is
what to run.

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
| `src/worktrees/layout.py` | where a worktree lives, derived from the main checkout |
| `src/worktrees/pick.py` | matching a query, and the numbered prompt |
| `src/worktrees/worktree.py` | adding, moving and removing a checkout |
| `src/worktrees/cli.py` | argument parsing and output |

`pyproject.toml` maps each console script to an entry point in `cli.py`.

`new_branch.py` and `rotate.py` are alpha. They start branches rather than
worktrees, `origin new-branch` and `origin rotate` already do the same job,
and only one of the two sets survives. Keep them matching `origin` rather
than improving on it: a difference between them is a decision somebody has
to make later, and there is no plan that makes both correct.

## What an invocation costs

```console
uv run scripts/benchmark.py > b.json
uv run scripts/benchmark.py --markdown --from b.json > docs/BENCHMARK.md
```

[scripts/README.md](../scripts/README.md) says what it measures and why every
interpreter comes from uv. [docs/BENCHMARK.md](BENCHMARK.md) is the last run.

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
prints every spec, marking the ones that mutate.

A guard runs on the resolved argv inside the wrapper, so no call site can
assemble its way past it: `reset --hard`, a forced `checkout` or `switch`,
`clean -f`, a bare `push --force`, `worktree remove --force` and `branch -D`
are refused whatever flags are passed.

## Commands that land you somewhere need a shell function

A binary cannot `cd` its caller. A command that does gets a shell function of
the same name, whose whole body is `command <name>` and a `cd`, so a script
calling the binary still gets the path and only loses the move. `gwa`, `gwl`,
`gwm` and `gwr` have one each, for fish under `functions/` and for zsh under
`zsh/plugins/worktrees/functions/`. `gws`, `gwp`, `gwnb` and `gwrot` need
none.

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
the failure case. zsh needs neither, because `$(...)` strips only trailing
newlines:

```bash
gwa() {
  local dest
  dest="$(command gwa "$@")" || return
  [ -n "$dest" ] || return 0
  cd -- "$dest"
}
```

## Tests

Python 3.11 through 3.14 in CI. They drive the entry points
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

`rt release::prechecks` runs first and refuses a version that is not `x.y.z`,
a dirty tree, a tag that already exists on the remote, a version that is not
after the newest release tag, a commit that is not on main, and a version PyPI
already carries. That last one matters most: PyPI rejects a duplicate at the
very end of a publish run, after everything else has already happened.

`/release-notes:prepare` writes the `CHANGELOG.md` entry on the release branch,
so the notes are reviewed in the same pull request as the version bump. It
comes from the release-tools marketplace:

```console
claude plugin marketplace add releasetools/agent-plugins --scope project
claude plugin install release-notes@release-tools --scope project
```

`--scope project` declares it in this repository rather than in your own
configuration, so the next person to release is offered the same plugin.

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
$ rt git::assert_tag_version "$(uv version --short)"
ERROR: tag/version mismatch, refusing to continue.
ERROR:   tag(s) at HEAD: v9.9.9
ERROR:   version given:  0.1.0
```

Pushing the tag starts `publish.yml`:

```
build ──> publish ──> release
                   └─> verify
```

`build` runs three guards before it does anything, cheapest first. The tag has
to name the version the commit carries. The commit has to be one `main` took,
so nothing is released from a tree no review ever saw. And that commit's test
run has to have passed already, so a doomed release stops before anything is
uploaded. Then it runs the suite again, builds one artifact for every job
below, installs that wheel and runs it: the wheel is not the tree, and one
built without `_version.py` falls back to the metadata lookup and says
nothing.

Nothing rehearses on TestPyPI here. `testpypi.yml` published this commit
there when it reached `main`, as `<version>.post<epoch>`, and installed it
back, so the tree the tag names has already proved it uploads to an index and
installs from one. The merge happens first and the irreversible step is last,
so everything recoverable is already done by the time anything is published.

`release` needs the upload and nothing else. `verify` installs the version
from PyPI and runs what it installed, on its own, because an index serves what
it has accepted after a delay it does not bound: 0.2.1 took longer than the
script waited and a correct release went red. A red `verify` says PyPI is
slow, not that the version is missing. It gives up after about 32 minutes.

`workflow_dispatch` against a tag ref re-runs a release whose publish failed.

## Snapshots on TestPyPI

`testpypi.yml` runs on every commit to `main`. It numbers the tree
`<version>.post<epoch>`, builds it, uploads it to TestPyPI and installs it
back from there.

`post` rather than `dev` because that is what the build is: made after
`<version>` shipped. PEP 440 sorts `0.2.3.post1789247568` above `0.2.3` and
below `0.2.4`, so TestPyPI's newest snapshot is what it serves, and a plain
`pip install` finds it without `--pre`. Epoch seconds rather than the run
number, because a re-run repeats that number and the duplicate upload is
refused.

A snapshot version is not SemVer, deliberately. SemVer allows three numeric
components and has no `post` segment, so `0.2.3.post1789247568` is outside its
grammar. The spellings that satisfy both describe something else: a
pre-release of the next version, `0.2.4-dev.<epoch>`, names a release nobody
has decided and hides the snapshot from a plain `pip install`, and
`0.2.3-post<epoch>` is a pre-release to SemVer, ordered *below* `0.2.3`, which
is backwards. That last one also survives nowhere: `uv version` rewrites it to
`0.2.3.post<epoch>` on the way into `pyproject.toml`, and hatchling normalises
it again on the way into the wheel, so the wheel, the index and `gw version`
all show the PEP 440 form whatever was typed. Snapshots are never tagged,
never released and never in the changelog, and PEP 440 is what the index
enforces.

The suite is not run again there. `tests.yml` runs it on the same commit, and
a snapshot a test would have caught costs one number on a test index.

No API token is stored anywhere. Every upload uses Trusted Publishing over
OIDC, which matches a request against the repository, the workflow filename
and the environment, so each row below is its own registration:

| index | owner | repository | workflow | environment |
| --- | --- | --- | --- | --- |
| PyPI | `MihaiBojin` | `worktrees` | `publish.yml` | `pypi` |
| TestPyPI | `MihaiBojin` | `worktrees` | `testpypi.yml` | `testpypi` |

Both GitHub environments have to exist under Settings, Environments. Adding a
required reviewer to `pypi` puts a manual gate in front of the real index.

**Renaming either file breaks its uploads**, and a rename means re-registering
that publisher first.
