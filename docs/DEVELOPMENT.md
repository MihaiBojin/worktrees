# Development

Python 3.11 through 3.14, no runtime dependencies, `uv` for everything else.

```console
uv sync --all-extras
uv run --no-sync pytest tests
uv run --no-sync pytest tests -m integration
uv run --no-sync pre-commit run --all-files
```

The first run skips the integration tests: real fish and zsh, partial
clones and the stand-in forge, about a second each. The second runs only
those. CI runs both. Each spreads its tests over one pytest-xdist worker per
core; `-n0` runs them in one process, which `-x` and `--pdb` need.

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
| `src/worktrees/prune.py` | removal snapshots, disclosures and stash cleanup |
| `src/worktrees/branches.py` | loose-branch assessment and deletion |
| `src/worktrees/sync.py` | sync, when it is due, and the shared evidence cache |
| `src/worktrees/new_branch.py` | starting a branch off the head branch |
| `src/worktrees/fast_forward.py` | the current branch, level with the head ref |
| `src/worktrees/layout.py` | where a worktree lives, derived from the main checkout |
| `src/worktrees/pick.py` | matching a query, and the arrow-key picker |
| `src/worktrees/worktree.py` | adding, moving and removing a checkout |
| `src/worktrees/cli.py` | argument parsing and output |

`pyproject.toml` installs two console scripts, `gw` and `worktrees`, both
entry points in `cli.py`. Shortcuts such as `gwp` are shell plugin functions.

`new_branch.py` and `fast_forward.py` start branches rather than worktrees.
AGENTS.md says when `fast_forward.py` starts one and how it names it.

## What an invocation costs

```console
uv run scripts/benchmark.py > b.json
uv run scripts/benchmark.py --markdown --from b.json > docs/BENCHMARK.md
```

[scripts/README.md](../scripts/README.md) says what it measures and why every
interpreter comes from uv. [docs/BENCHMARK.md](BENCHMARK.md) is the last run.

## Working on a checkout

```console
make install-local    # uv tool install --force --from . git-worktrees
make reset            # uv tool install --force git-worktrees
```

`make install-local` installs the working copy in place of anything
published, so `gw` on your `$PATH` is the code in front of you. `make reset`
undoes it: the latest release from PyPI replaces the working copy, and a
command only the working copy has, such as one renamed since that release,
leaves `~/.local/bin` with it.

The distribution is `git-worktrees` because PyPI already holds `worktrees`.
The import package is `worktrees`, and so is the long name of `gw`.

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
`clean -f`, a bare `push --force`, `worktree remove --force`, `branch -D`
and ref deletion without a full expected SHA are refused.

## Commands that land you somewhere need a shell function

A binary cannot `cd` its caller. The plugin's `gw` function captures stdout
for `gw add`, `gw remove`, `gw select-main` and a terminal `gw list`, changes
directory when it receives a destination, and prints data otherwise. It
lives under `functions/` for fish and `zsh/plugins/worktrees/functions/` for
zsh.

The [fish function](../functions/gw.fish) captures bytes with
`string collect -N` and reads the command status from `$pipestatus[1]`.
The [zsh function](../zsh/plugins/worktrees/functions/gw) uses a sentinel to
preserve trailing newlines through command substitution. Both remove only
the final destination-record newline and require an absolute existing
directory. Data is printed byte for byte and the binary's status is retained.

Metadata flags and nonterminal `gw list` pass stdout and status through.
`gw remove` can print a destination after checkout removal and still return
failure for later cleanup; the function moves to that destination and
preserves the failure.

Each shortcut, such as `gwa` or `gwp!`, is a one-line function beside it that
calls the `gw` function with fixed words, so `gwa` lands where `gw add` does.
The `shortcuts` field of each row in `cli.py` is the table `gw --help` prints,
and `tests/test_shims.py` holds both plugins' files to it.

## Sync and assessment

Only `sync.run` fetches, updates remote HEAD or queries a forge. `gw sync`
calls it, and so does `cli._dispatch` before every other command when
`sync.due` says the refs are older than `WORKTREES_SYNC_TTL`;
`WORKTREES_OFFLINE` turns both off. `sync.py` atomically replaces `git-worktrees-sync.json` in the Git common directory.
It records remote identity, successful component times and source-tip
forge evidence. Assessment uses pinned head state, one first-parent set and
read-only patch-id input. It creates no synthetic commits.

`repo.require_offline_git` checks partial-clone settings before object reads.
Accepted Git cohorts are 2.45.1 onward and patched maintenance releases
2.39.4, 2.40.2, 2.41.1, 2.42.2, 2.43.4 and 2.44.1, including later patches
within each series. Lower reported vendor versions fail conservatively;
full clones keep older Git compatibility.

## Tests

Python 3.11 through 3.14 in CI. They drive the entry points
through `subprocess`, so env, cwd, both streams and the exit code stay
per-test, and the prompt tests drive a real pty.

The fixture needs no global git config, no signing key, no forge and no
network. `GIT_CONFIG_GLOBAL` carries that: without it git still reads
`$HOME/.gitconfig`, and setting `HOME` alone still leaves
`$XDG_CONFIG_HOME/git/config`. `WORKTREES_OFFLINE=1` in `conftest.ENV` stops
a command syncing first, which would fetch a remote at `github.com` for
real. `Network.env` clears it for the fake network, and `conftest.online`
for a remote on disk.

## Publishing a version

```console
/release:cut 0.2.0
```

That does the whole of it: cut `release/v0.2.0` from `origin/main`,
`scripts/bump_version.py 0.2.0`, open the pull request, wait for its checks, squash-merge,
wait for main's own run on the merged commit, and only then tag. The tag lands
on a commit already proved green, so it never has to move.

What each step needs is in `.releasetools.yaml`: the branch, the squash, the
workflow that has to be green, the workflow the tag starts, the URL that must
not already carry the version, and the command that sets it. The keys are the
[releasetools conventions](https://github.com/releasetools/conventions/blob/main/FORMAT.md),
so the guards and the release read the same file.

The squash is not a preference. The `main` ruleset requires signed commits and
linear history: GitHub replays the author's commits unsigned on a rebase merge
and refuses with `Base branch requires signed commits`, and a merge commit is
not linear. A squash is one commit, signed by GitHub's web-flow key, and its
subject becomes `Release 0.2.0 (#NN)`.

`rt release::prechecks` runs first and refuses a version that is not `x.y.z`,
a dirty tree, a tag that already exists on the remote, a version that is not
after the newest release tag, a commit that is not on main, and a version PyPI
already carries. That last one matters most: PyPI rejects a duplicate at the
very end of a publish run, after everything else has already happened. The
distribution is `git-worktrees`; PyPI's `worktrees` belongs to somebody else.

`/release-notes:prepare` writes the `CHANGELOG.md` entry on the release branch,
so the notes are reviewed in the same pull request as the version bump. Both
commands come from the release-tools marketplace:

```console
claude plugin marketplace add releasetools/agent-plugins --scope project
claude plugin install release@release-tools --scope project
claude plugin install release-notes@release-tools --scope project
```

`--scope project` declares them in this repository rather than in your own
configuration, so the next person to release is offered the same plugins.
`rt` has to be v0.4.0 or newer, which is where `version::bump` arrived.

`.codex/config.toml` declares the same marketplace and plugins for Codex,
which reads it once you trust the folder. Codex does not fetch a marketplace
it finds there, so run this once:

```console
codex plugin marketplace upgrade release-tools
```

It clones the marketplace and installs both plugins. Until then, every
`codex plugin` command in this repository fails with `marketplace root does
not contain a supported manifest`.

`publish.yml` reads that changelog section back out for the GitHub release,
and `build` refuses a tag whose version has no section, before anything is
published. Nothing is generated from pull request titles.

By hand it is the same four commands:

```console
python3 scripts/bump_version.py 0.2.0   # uv version, then the plugins and README
# open a pull request, review it, merge it
git switch main && git pull
git tag -a v0.2.0 -m v0.2.0
git push origin v0.2.0
```

`uv version` alone is not the bump. It rewrites `pyproject.toml` and
`uv.lock`, and the shell plugins carry a copy it never sees: Fisher and
Antidote install them from the tag rather than from anything built, so each
exports `WORKTREES_PLUGIN_VERSION` written into it, and the README's install
lines name the tag. `scripts/bump_version.py` writes every one of them, and
checks each line matches before it writes any. `tests/test_version.py` fails
when one disagrees with `pyproject.toml`, so a release pull request missing
one fails its checks before anything is tagged.

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

`publish` signs each distribution with `uvx pypi-attestations@0.0.30 sign`
using the job's OIDC identity. `uv publish` uploads the resulting PEP 740
attestations alongside the distributions.

`release` needs the upload. `verify` installs the version from PyPI and runs
what it installed, allowing about 32 minutes for the index to serve it. It
then runs `uvx pypi-attestations@0.0.30 verify pypi` for each wheel and source
distribution, checking the attestations against this GitHub repository.
Either check can fail the verification job independently of the release.

`workflow_dispatch` against a tag ref re-runs a release whose publish failed.

## Snapshots on TestPyPI

`testpypi.yml` runs on every commit to `main`. It numbers the tree
`<version>.post<epoch>`, builds it and signs each distribution with
`uvx pypi-attestations@0.0.30 sign` using the job's OIDC identity. It uploads
the distributions and their PEP 740 attestations to TestPyPI, then installs
the snapshot back from there.

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
