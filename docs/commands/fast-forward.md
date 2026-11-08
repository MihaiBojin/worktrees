# worktrees fast-forward

`gw fast-forward` brings the branch you stand on level with the head ref, and
the shell plugin's `gwff` runs it. `gw ff` is its shorthand. Like every
command but `gw sync` and `gw version`, it syncs first once the last sync is a
minute old, so the head ref is the remote's; `WORKTREES_OFFLINE` keeps it on
the refs and forge evidence it already has.

```console
gw fast-forward
gw fast-forward next-parser
gw fast-forward next-parser --from release-1.2
gwff --json
```

What it does depends on the branch, checked in this order:

1. A rebase stopped in this checkout is reported as a `conflict`, whatever
   started it.
2. A detached HEAD, or a head ref that does not resolve, is refused.
3. The head branch fast-forwards with `git merge --ff-only`. Local commits on
   it are refused and listed, because a rebase would rewrite them, with the
   two commands that move them to a branch of their own.
4. A branch that already contains the head ref is left alone: `current`.
5. A branch proved finished, by the checks
   [branch status](branch-status.md) runs, keeps its name and its commits.
   The checkout moves to a new branch from the head ref: `rotated`.
6. A branch whose tip is on the head ref's first-parent history has no commits
   of its own and fast-forwards: `fast-forwarded`.
7. A branch with commits of its own is rebased onto the head ref: `rebased`.
   The first commit that conflicts stops the rebase, and the files stay as
   git left them, markers included: `conflict`.
8. A branch whose state cannot be told, such as one with no history in common
   with the head ref, is refused.

A finished branch's name stays with its merged request on the forge, so the
next change gets a name of its own. Without `NAME`, that name is the UTC time
and the branch's name, `2026-11-08T09-05-03Z/fix-parser`, with any time a
previous rotation put in front of it replaced. `NAME` names it instead, and
`--from BRANCH` or `--from SHA` starts it there rather than at the head ref. A
name already taken here, or on the default remote, is refused. `NAME` and
`--from` on a branch that is not rotating are refused rather than ignored.
The new branch has no upstream.

The merge and the rebase both run with `--no-autostash`, so uncommitted
changes stay in the checkout or git refuses; they never go to the stash list
every worktree shares. A refusal leaves the branch and the checkout as they
were.

A conflict exits 1 and leaves the rebase in progress. Resolve each file,
`git add` it and run `git rebase --continue`, which may stop at the next
commit. `git rebase --abort` puts the branch back where it was. Running the
command again reports the stopped rebase until it finishes, then `current`.

`--json` prints an object with `head`, `lastSync` and these fields:

| field | holds |
| --- | --- |
| `result` | `current`, `fast-forwarded`, `rebased`, `rotated` or `conflict` |
| `branch` | the branch checked out now; while stopped, the one being rebased |
| `base` | the full ref it was brought level with, or a short commit |
| `sha` | where HEAD is now |
| `previous`, `previous_sha` | the branch and commit before the run |
| `ahead` | commits of its own on top of `base` after the run |
| `behind` | commits of `base` it lacked before the run |
| `proof`, `why` | how a rotated branch was proved finished |
| `stopped_at`, `subject` | the commit the rebase stopped at |
| `paths` | files with conflicts, from the top level; empty once all are staged |

Exit 0 means the branch is level. Exit 1 is a conflict, with the JSON above,
or a refusal, with nothing on stdout and the reason on stderr. `-q` keeps the
result and every failure.
