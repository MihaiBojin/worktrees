# worktrees branch create

`gw branch create NAME` starts branch `NAME` in this checkout and switches
to it. It makes no worktree; [`gw add`](add.md) does. `gwbc` is its
shortcut.

```console
gw branch create fix-parser
gw branch create fix-parser --here
gw branch create fix-parser --json
```

## Where it starts

| case | `NAME` starts from |
| --- | --- |
| default | the head ref, such as `origin/main`, after a sync when one is due |
| a terminal, standing on a branch other than the head branch, answered `y` | the branch you stand on: `NAME` stacks on it |
| the same question answered anything else | the head ref |
| `--here` | the commit this checkout is on, branch or detached `HEAD`; nothing syncs |

The question reads `stack NAME on BRANCH, not HEAD_BRANCH? [y/N]`. It is
asked only on a terminal and never under `--json`, and only off the head
branch, since on it both answers start from the same place. The sync runs on a thread
while the question waits, and its spinner starts once the answer is in, so
nothing draws over the prompt.

The branch is made with `git switch --create NAME --no-track BASE`. No
upstream is set, so a branch started from `origin/main` never pushes to
`main`. Uncommitted changes come along as
`git switch` carries them, and git refuses when they conflict with `BASE`.

## Refusals

Each exits 1 with the reason on stderr:

- `NAME` is not a valid branch name.
- `NAME` is already a branch. The refusal names `git switch NAME`, which
  checks it out.
- `NAME` and an existing branch nest, such as `codex` and `codex/plan`.
- The head ref does not resolve, unless `--here` is given.

A missing `NAME` is a usage error at exit 2.

## Output

stdout carries one line, `NAME from BASE at SHA`, which `-q` keeps.
`--json` prints `branch`, `base`, the full ref it started from, and `sha`,
the short commit, with `head` and `lastSync`.
