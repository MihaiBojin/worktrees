# worktrees fast-forward

`gw fast-forward` brings the branch you stand on level with the head ref, and
the shell plugin's `gwff` runs it. `gw ff` is its shorthand.

```console
gw fast-forward
gw fast-forward next-parser
gw fast-forward next-parser --from release-1.2
gw fast-forward --autostash
gwff --json
```

## Fresh evidence

A pull request merged on the forge a moment ago is the usual reason to run
this, so it decides on evidence read in the same run. It fetches the default
remote on every run, whatever `WORKTREES_SYNC_TTL` says, and asks the forge
for the branch's newest merged request. The finished-branch proof and the
search for a merged part both use that answer; neither reads the request
cached by `gw sync`. When nothing else marks where a merged part ends, it
also asks which merged request carried the branch's oldest commit.

A failed fetch, or a forge on GitHub or GitLab that cannot be asked, `gh` or
`glab` missing or signed out included, is a refusal. Its last line is the
same command run offline:

```console
✗ origin could not be fetched
Run: WORKTREES_OFFLINE=1 gw ff next-parser
```

`WORKTREES_OFFLINE` keeps it on the refs and local proof it has. A remote on
another host has no forge to ask, and a repository without a remote has
nothing to fetch; neither is refused.

## What it does

What it does depends on the branch, checked in this order:

1. A rebase stopped in this checkout is reported as a `conflict`, whatever
   started it.
2. Files left with conflicts and no operation in progress are reported as a
   `stash-conflict`: an autostash that `git rebase --continue` re-applied
   after this command returned.
3. A detached HEAD, or a head ref that does not resolve, is refused.
4. The head branch fast-forwards with `git merge --ff-only`. Local commits on
   it are refused and listed, because a rebase would rewrite them, with the
   two commands that move them to a branch of their own.
5. A branch that already contains the head ref is left alone: `current`.
6. A branch proved finished, by the checks
   [branch status](branch-status.md) runs or by a verified merged request,
   keeps its name and its commits. The checkout moves to a new branch from
   the head ref: `rotated`.
7. A branch whose first commits are in the head ref and which went on
   afterwards has a boundary: the newest commit below the tip with a proof,
   searched from the tip down, since the first of a squashed pair is not
   upstream on its own, or the source commit of a verified merged request.
   When neither marks one, the forge is asked which merged request carried
   the branch's oldest commit. One from another branch, into the head
   branch, whose merge commit the fetched head ref holds, marks the newest
   of the branch's commits it carried: how a stacked branch finds its
   parent's commits when the parent gained more before it merged.
   When the branch's own request merged, it keeps its name and its commits,
   and the commits after the boundary are replayed onto a new branch with
   `git rebase --onto`: `rotated`, with `carried` above zero. When another
   branch's did, as with a stacked branch whose parent merged, or offline,
   where nothing says whose it was, the branch keeps its name and any
   request open from it, and drops the merged commits in place with the same
   `git rebase --onto`: `rebased`, with `carried` above zero.
8. A branch whose tip is on the head ref's first-parent history has no commits
   of its own and fast-forwards: `fast-forwarded`.
9. A branch with commits of its own is rebased onto the head ref: `rebased`.
   The first commit that conflicts stops the rebase, and the files stay as
   git left them, markers included: `conflict`.
10. A branch whose state cannot be told, such as one with no history in common
    with the head ref, is refused.

A finished branch's name stays with its merged request on the forge, so the
next change gets a name of its own. Without `NAME`, that name is the UTC time
and the branch's name, `2026-11-08T09-05-03Z/fix-parser`, with any time a
previous rotation put in front of it replaced. `NAME` names it instead, and
`--from BRANCH` or `--from SHA` starts it there rather than at the head ref. A
name already taken here, or on the default remote, is refused. `NAME` and
`--from` on a branch that is not rotating are refused rather than ignored.
The new branch has no upstream.

A replayed commit that conflicts stops the rebase on the new branch.
`git rebase --abort` leaves the new branch at the old tip, and the source
branch never moves. A branch rebased in place has a remote copy to update:
push it with `--force-with-lease --force-if-includes`.

## Uncommitted changes

Without `--autostash`, the merge and the rebase run with `--no-autostash`, so
uncommitted changes stay in the checkout or git refuses; they never go to the
stash list every worktree shares. Moving a finished branch uses
`git switch --create`, which carries the changes it can and refuses the rest.
A partly merged branch with uncommitted changes is refused before any new
branch is made. A refusal leaves the branch and the checkout as they were.

`--autostash` passes git's own `--autostash` instead, which keeps the work off
the stash list unless it fails to re-apply. When it fails, git leaves the
conflict markers and an `autostash` entry made on the commit HEAD held before
the run. The result is `stash-conflict`, naming that entry by sha:
`git stash apply SHA` puts the work back, and no other worktree's push or drop
can renumber a sha.

## Stashes

A partly merged or rotated branch keeps its stashes, the ones whose subject
names it and whose commit is on it. `stashes` lists them with `after` true
for one made on a commit after the boundary, which is work done after the
merge. One made on
the boundary or before it may hold work the merge already took.

## Conflicts

A conflict exits 1 and leaves the rebase in progress. Resolve each file,
`git add` it and run `git rebase --continue`, which may stop at the next
commit. `git rebase --abort` puts the branch back where it was. Running the
command again reports the stopped rebase until it finishes, then `current`.

## JSON

`--json` prints an object with `head`, `lastSync` and these fields:

| field | holds |
| --- | --- |
| `result` | `current`, `fast-forwarded`, `rebased`, `rotated`, `conflict` or `stash-conflict` |
| `branch` | the branch checked out now; while stopped, the one being rebased |
| `base` | the full ref it was brought level with, or a short commit |
| `sha` | where HEAD is now |
| `previous`, `previous_sha` | the branch and commit before the run |
| `ahead` | commits of its own on top of `base` after the run |
| `behind` | commits of `base` it lacked before the run |
| `proof`, `why` | how a rotated branch, or its merged part, was proved |
| `boundary` | the branch's last commit already in the head ref |
| `carried` | commits after `boundary`, the ones replayed |
| `stopped_at`, `subject` | the commit the rebase stopped at |
| `paths` | files with conflicts, from the top level; empty once all are staged |
| `stash` | the stash entry holding changes that did not re-apply |
| `stashes` | a partly merged or rotated branch's stashes: `sha`, `subject`, `after` |

Exit 0 means the branch is level. Exit 1 is a `conflict` or a
`stash-conflict`, with the JSON above, or a refusal, with nothing on stdout
and the reason on stderr. `-q` keeps the result and every failure.
