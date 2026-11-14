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
also asks which merged request carried the branch's oldest commit. When the
branch's remote copy still holds its merged part, it asks GitHub whether the
branch's open pull request is in a native stack.

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
   the branch's oldest commit. A commit the forge does not hold, as when
   nobody has pushed the branch, was carried by none. One from another
   branch whose merge commit the fetched head ref holds, and which targeted
   the head branch or left that commit on its first-parent history, marks
   the newest of the branch's commits it carried: how a
   stacked branch finds its parent's commits when the parent gained more
   before it merged. The forge
   lists at most 100 of a request's commits here, a choice made because a
   parent seldom drifts that far from the branch stacked on it. A request
   with more can mark the boundary too early or not at all, and the rebase
   then replays commits the head ref already has: one that conflicts stops
   it, and `git rebase --abort` puts the branch back.
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

A branch in step 5, 7 or 9 whose remote copy was rewritten elsewhere takes
that copy rather than being rebased. GitHub rewrites a stacked pull request's
branch when the one below it merges: the same commits, committed again on the
merge. The copy, `refs/remotes/REMOTE/BRANCH`, counts when no commit the
branch's reflog has held reaches it, which is when a push with
`--force-if-includes` refuses, and when its commits the head ref lacks are
the branch's own after any merged part, in order and with the same
patch-ids. The branch moves to the copy with `git rebase --onto`, replaying
only the commits made after it, and `adopted` names the copy: `rebased`. A
branch already level with the head ref takes it too, so a second run mends a
branch an earlier one rebased in place. A copy that lacks the head ref's
newest commits leaves the branch there, with `base` naming the copy, and the
next run rebases it onto the head ref. A copy holding a commit
the branch lacks is not taken.

Run straight after the parent merged, before GitHub's rewrite arrives, a
stacked branch's remote copy still holds the merged part. When its open
pull request is in a native stack, the run fetches every 2 seconds, for a
minute at most, until the rewrite arrives, then takes it. A rewrite that
never arrives is a refusal that leaves the branch as it was, and its last
line is the same command run offline, which rebases the branch here instead.
A branch in no native stack is rebased without waiting.

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
push it with `--force-with-lease --force-if-includes`. After it takes its
remote copy, that push sends only the commits made after the copy, or
nothing.

## Why not `gh stack rebase`

GitHub's `gh stack` extension also rebases stacked branches. It does not
replace this command:

- It only knows stacks it set up in this worktree. It keeps them in a file
  inside the worktree's own git directory, so a stack made with
  `gh stack link`, on another machine or in another worktree is unknown
  here. This command reads the stack from git and the forge.
- It refuses to run while another worktree has one of the stack's branches
  checked out.
- When GitHub rewrites a branch after its parent merges, it keeps the local
  branch and pushes over the rewrite. This command takes the rewrite, and
  waits for it if it has not arrived.
- `gh stack rebase --abort` runs `reset --hard` on every branch. In a test
  with `rebase.autoStash` on, that lost an uncommitted change. This command
  never runs `reset --hard`.

It also works only with GitHub, and moves no stashes.

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

A partly merged or rotated branch's stashes, and those of a branch that took
its remote copy, are the ones whose subject names it and whose commit is on
it. `stashes` lists them, with `after` true for one made on a commit after
the boundary, which is work done after the merge; for a branch with no
merged part, the boundary is where it left the head ref.
One made on the boundary or before it may hold work the merge already took,
and stays where it is.

Once the carry finishes, each stash made after the boundary moves to the
replayed copy of the commit it was made on, the one with the same patch-id,
taken from the lines the commit changes and none around them.
It goes to the branch that holds the commits now: the new one after a
rotation, the same one after a rebase in place or after taking the remote copy,
whose new commits would otherwise leave it matching no branch. `git merge-tree --write-tree` puts
its index and its changes onto that commit without touching the checkout,
and the copy keeps its untracked files as they were. `git stash store` puts
the copy on the stash list, and the original is dropped by sha with the
check `gw branch prune` makes. `moved` is the copy's sha.

A stash made after the boundary stays, with `kept` saying why, when no
replayed commit has the patch-id of the one it was made on, as with a merge
commit, which the rebase drops; when its changes conflict on the replayed
commit; when the rebase stopped; or on a git before 2.40, which lacks
`merge-tree --merge-base`. `git stash apply SHA` still applies it by hand.

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
| `base` | the full ref it was brought level with, or a short commit: the head ref, or `adopted` when that copy lacks the head ref's newest commits |
| `sha` | where HEAD is now |
| `previous`, `previous_sha` | the branch and commit before the run |
| `ahead` | commits of its own on top of `base` after the run |
| `behind` | commits of `base` it lacked before the run |
| `proof`, `why` | how a rotated branch, or its merged part, was proved |
| `boundary` | the branch's last commit already in the head ref |
| `carried` | commits after `boundary`: the ones replayed, or the copies `adopted` holds |
| `adopted` | the remote copy, rewritten elsewhere, the branch moved to; empty otherwise |
| `stopped_at`, `subject` | the commit the rebase stopped at |
| `paths` | files with conflicts, from the top level; empty once all are staged |
| `stash` | the stash entry holding changes that did not re-apply |
| `stashes` | a partly merged, rotated or `adopted` branch's stashes: `sha`, `subject`, `after`, `moved`, `kept` |

Exit 0 means the branch is level with `base`. Exit 1 is a `conflict` or a
`stash-conflict`, with the JSON above, or a refusal, with nothing on stdout
and the reason on stderr. `-q` keeps the result and every failure.
