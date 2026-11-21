# worktrees add

`gw add NAME` creates a worktree for branch `NAME` and prints its path, and
the `gw` shell function changes into it. `gw a` is its shorthand and `gwa`
its shortcut.

```console
gw add fix-parser
gw add fix-parser release-1.2
cd "$(command gw add fix-parser -q)"
gw add fix-parser --json
```

## Where it goes

The path is derived, never configured:
`PARENT/.worktrees/NAME/REPO`, where `PARENT` holds the main checkout and
`REPO` is the main checkout's directory name. A slash in `NAME` is a
directory, so `feat/oauth` lands in `.worktrees/feat/oauth/repo` and cannot
collide with `feat`. Repositories that sit side by side share one
`.worktrees` directory, one subdirectory each.

## Which commit

| `NAME` is | the worktree gets |
| --- | --- |
| a local branch checked out nowhere | that branch, as it is; `BASE` is ignored and `created` is false |
| no local branch | a new branch from `BASE`, or from the head ref without one; `created` is true |

The new branch has no upstream: it is made with `--no-track`, so a branch
started from `origin/main` never pushes to `main`. A branch that exists only
on the remote counts as no local branch. `gw add NAME` starts a new `NAME`
from the head ref, not from `origin/NAME`; `gw add NAME origin/NAME` starts
it from the remote's commit, still with no upstream.

The head ref has to resolve even when `NAME` exists, as
[the head rules](../REFERENCE.md#head-and-evidence) describe.

## Refusals

Each exits 1 with the reason on stderr and nothing on stdout:

- `NAME` is not a valid branch name.
- The current directory is not in a git repository.
- `NAME` and an existing branch nest, such as `codex` and `codex/plan`: git
  cannot hold both.
- `NAME` is already checked out in a worktree. The refusal names its path;
  `gw list NAME` goes there.
- The destination directory already exists. When another repository owns
  it, the refusal names that repository.
- The head ref does not resolve.

`gw add` creates the parent directories before it asks git. When git
refuses after that, the directories stay, empty, with a warning naming the
topmost one. Nothing deletes them: this command removes nothing it did not
name.

A missing `NAME` is a usage error at exit 2.

## Output

stdout carries the path alone, which is what the shell function and
`cd "$(...)"` read. stderr carries a note saying whether the branch is new
or was already here. `-q` drops the note and keeps the path. `--json`
prints `path`, `branch` and `created` with `head` and `lastSync`.

`gw add` syncs first when a sync is due, as
[sync](../../README.md#head-and-synchronization) describes.
