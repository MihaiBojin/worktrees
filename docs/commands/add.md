# worktrees add

`gw add NAME` creates a worktree for branch `NAME` and prints its path, and
the `gw` shell function changes into it. `gw a` is its shorthand and `gwa`
its shortcut.

```console
gw add fix-parser
gw add fix-parser release-1.2
gw add shared --yes
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
| a local branch checked out nowhere | that branch, as it is; `created` is false |
| only on the default remote, as `origin/NAME` | a new `NAME` from `origin/NAME`, tracking it, once approved; `created` is true |
| on neither | a new branch from `BASE`, or from the head ref without one, with no upstream; `created` is true |

`BASE` starts a new branch. For a branch that already has a commit, here or
on the remote, a `BASE` that names that same commit is accepted, and any
other is refused: it would move the local branch, or hide the remote's work
behind a new branch that pushes over it.

A branch only the remote has is started from its copy, never from the head
ref. On a terminal `gw add` asks first, `NAME exists only on the remote.
Start it from origin/NAME? [y/N]`, and any answer but yes creates nothing
and exits 1. Without a terminal, or under `--json`, it refuses and names
`gw add NAME --yes`, which approves the copy without asking. `BASE` set to
the copy, `gw add NAME origin/NAME`, is the same approval. The branch
tracks `origin/NAME`, since that is the branch it pushes to.

A branch started from the head ref has no upstream: it is made with
`--no-track`, so a branch started from `origin/main` never pushes to `main`.

The head ref has to resolve in every case, as
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
- `BASE` names another commit than the branch already has, here or on the
  remote, or names no commit.
- `NAME` exists only on the remote and nobody approved starting it from
  there.
- The head ref does not resolve.

`gw add` creates the parent directories before it asks git. When git
refuses after that, the directories stay, empty, with a warning naming the
topmost one unless `-q` is given. Nothing deletes them: this command removes
nothing it did not name.

A missing `NAME` is a usage error at exit 2.

## Output

stdout carries the path alone, which is what the shell function and
`cd "$(...)"` read. stderr carries a note saying whether the branch is new
or was already here. `-q` drops the note and the warning and keeps the
path. `--json` prints `path`, `branch` and `created` with `head` and
`lastSync`.

`gw add` syncs first when a sync is due, as
[sync](../../README.md#head-and-synchronization) describes.
