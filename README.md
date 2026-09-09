# worktrees

`gwp` says which of this repository's worktrees are finished, why, and removes
them when you pass `--yes`.

```console
$ gwp
VERDICT  BRANCH         WHY                                              PATH
go       fix-parser     squash-merged                                    ~/git/.worktrees/fix-parser/repo
keep     add-tests      it has uncommitted changes                       ~/git/.worktrees/add-tests/repo
keep     spike-cache    merged, but holds 2 ignored path(s); pass --delete-ignored
unknown  try-something  not merged into main, and no upstream says whether its commits were pushed

1 to remove, 2 kept, 1 unclear
nothing removed; pass --yes to remove the 1 above
```

## Two holes in git

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
git brings them back: no ref ever pointed at them. `gwp` reads
`--ignored=traditional`, counts what would go, and refuses without
`--delete-ignored`. `--yes` does not answer that question.

## Three verdicts

`unknown` is not `keep` with a softer word.

| | means |
| --- | --- |
| `go` | finished, and safe to remove. The reason says how it was proved: `merged`, `squash-merged`, or a ref reaching a detached commit |
| `keep` | something says no. Uncommitted work, the head branch, a lock, the worktree you are standing in, ignored files, or a branch simply not merged |
| `unknown` | it could not tell. A branch with no upstream is the usual one: nothing says whether its commits were pushed anywhere |

There is no `--dry-run`. Without `--yes` this only reports, and a flag meaning
"do not act" on a command that does not act reads as a safety feature somebody
will one day cite as the reason a sweep was safe.

## Flags

| | |
| --- | --- |
| `--branch NAME` | consider only that branch |
| `--no-fetch` | assess from what is already here, and say so |
| `--delete-ignored` | let a worktree holding gitignored files go |
| `-y`, `--yes` | remove what it proposes |
| `--json` | verdicts as data |
| `-v`, `--verbose` | print every git command as it runs |
| `--explain` | print every git command the program can issue, and exit |

## Every git call is visible

A decorated function's body is the command it runs:

```python
@git
def worktree_records():
    """Every worktree, NUL-delimited."""
    return "worktree", "list", "--porcelain", "-z"

@git(ok=(0, 1))
def is_ancestor(ref, head):
    """Non-zero means 'no', not 'broken'."""
    return "merge-base", "--is-ancestor", ref, head

@git(mutates=True)
def remove_worktree(path):
    return "worktree", "remove", "--", path
```

The argv is a real list, so nothing is ever a shell string and a branch name
cannot inject. `gwp --explain` prints all 26 of them. A guard runs on the
resolved argv inside the wrapper, so no call site can assemble its way past it:
`reset --hard`, `clean -f`, a bare `push --force`, `worktree remove --force`
and `branch -D` are refused whatever flags are passed.

A branch is deleted only on git's own proof, with `branch -d`. The command that
puts it back is printed before anything is removed, and `--quiet` and `--yes`
do not silence it.

## Install

```console
uv tool install --from . worktrees
```

## Tests

```console
uv run --with pytest pytest
```

The fixture needs no global git config, no signing key, no forge and no
network. `GIT_CONFIG_GLOBAL` carries that: without it git still reads
`$HOME/.gitconfig`, and setting `HOME` alone still leaves
`$XDG_CONFIG_HOME/git/config`.
