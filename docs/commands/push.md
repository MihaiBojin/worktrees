# worktrees push

`gw push` sends the branch you stand on to the default remote, and the
shell plugin's `gwpu` runs it.

```console
gw push
gw push --json
gw push --replace
```

It fetches the default remote first, whatever `WORKTREES_SYNC_TTL` says,
since both checks below judge the remote copy as it is now. A failed fetch
is a refusal. A branch the remote lacks is pushed with
`git push --set-upstream REMOTE BRANCH`. One the remote has is pushed with
`--force-with-lease=refs/heads/BRANCH:SHA`, where `SHA` is the commit the
fetch read, so a push that lands after the remote moved again is refused by
git. A branch the remote already holds at the same commit pushes nothing.

## Two checks

The remote copy may hold commits the branch lacks. Each commit the copy has
and neither the branch nor the head ref holds is compared by patch-id with
the branch's commits and the head ref's. One that matches neither, is not
part of a change the head ref already merged, and was not made in this
clone is work the push would delete: another clone's commit, or GitHub's
rewrite of a stacked branch holding something the branch never had. The
run refuses and lists them:

```console
✗ origin/fix-parser holds 1 commit fix-parser lacks, so pushing it would delete them:
    3f2a9c1 Handle empty input
  gw fast-forward takes a copy GitHub rewrote, or says what differs; gw push --replace pushes over them once each one is checked
```

Merge commits, including GitHub's "Update branch", and empty commits have
no patch-id in this comparison. They still count as remote work unless
the branch or the head already holds them, the merged-part proof covers
them, or the branch's reflog proves this clone made them. A merge can hold
conflict resolutions that none of its parents contains.

`git push --force-with-lease --force-if-includes` lets that push through
when the remote tip is in the branch's reflog: a branch that pulled the
commit and then dropped it in a rebase deletes it from the remote.

A commit was made in this clone when an entry in the branch's reflog that
a commit, an amend, a cherry-pick, a revert, `git am` or a rebase wrote
brought it, and the entry before did not. An amend, or a conflict resolved
during a rebase, changes a commit's patch-id; when the commit was made here,
the rewrite is this clone's own and the push goes ahead. A commit that came
by a pull or a merge was made elsewhere.

`--replace` lifts this check, for a person who has read each commit listed:
a commit another clone made, pulled here and then edited, reads as one the
branch lacks.

The branch may still carry commits the head branch already merged, by the
proofs [branch status](branch-status.md) uses: ancestry, a squash's tree or
combined patch, or matching content. A stacked branch rebased by hand after
its parent squash-merged keeps the parent's commits, and pushing it puts
them back in its pull request, or undoes GitHub's rewrite of the branch.
The run refuses and names `gw fast-forward`, which drops them. `--replace`
does not lift this check.

A rebase onto the head branch that drops nothing passes both:

```console
$ git rebase origin/main
$ git commit -m 'Cover the empty case'
$ gw push
pushed fix-parser to origin at 8c1d2ab
```

The commits on the remote copy match the rebased ones by patch-id, and none
of them is merged.

## Refusals

The head branch, a detached HEAD, a repository with no default remote and a
head ref that does not resolve are refused before the fetch.
`WORKTREES_OFFLINE` refuses it too, as it does `gw sync`.

## JSON

`--json` prints an object with `head`, `lastSync` and these fields:

| field | holds |
| --- | --- |
| `branch` | the branch pushed or refused |
| `remote` | the default remote |
| `pushed` | the commit the remote copy is at now; empty when nothing was pushed |
| `previous` | the commit the remote copy was at before; empty when the remote lacked the branch |
| `refused` | `remote-only` or `merged` for the two checks, git's reason for a push it rejected, empty otherwise |

Exit 0 means the remote holds the branch's tip. Exit 3 is a refusal: one of
the two checks, with the JSON above and the commits on stderr, or one made
before the fetch, with nothing on stdout. Exit 1 is a push git rejected,
with the JSON above. `-q` keeps the JSON and every failure and drops the
summary line and the progress.
