---
name: merge
description: >
  Merge one or more pull requests or merge requests: check each title and
  description against its diff, read CI, fix failing checks on the branch,
  wait for green, merge with the repository's strategy, then report each
  problem found and how it was solved. Use when the user asks to merge,
  land or ship a pull request.
argument-hint: "[NUMBER ...]"
allowed-tools:
  - Bash(gw sync)
  - Bash(gw ci-checks:*)
  - Bash(gw list --json)
  - Bash(jq:*)
  - Bash(gh pr view:*)
  - Bash(gh pr diff:*)
  - Bash(gh run view:*)
  - Bash(gh repo view:*)
  - Bash(glab mr view:*)
  - Bash(glab ci trace:*)
  - Bash(git log:*)
  - Bash(git diff:*)
  - Bash(git status:*)
  - Read
  - Write
---

# Merge pull requests, fixing what stands in the way

In Codex, `$ARGUMENTS` means the arguments supplied with the skill. Each
number is a pull request; with none it is the current branch's. Several
numbers are merged one at a time, in the order given, and a stack bottom
first. One whose `pr.baseRef` is the branch of a pull request this run did
not merge, whatever the reason, is not merged: it would land on that branch
rather than the head branch. Report it as waiting on that one.

The user asking to merge is the approval to merge each named pull request
once it is green, to push fixes to its branch, and to correct its
description. It approves nothing else: no other pull request, no admin
bypass, no deleted branch, no disabled check.

Keep a record as you go, one row per problem: the pull request, what was
wrong, what fixed it. It is the report at the end.

## 1. Read it

```sh
gw ci-checks NUMBER --json
```

[The ci-checks skill](../ci-checks/SKILL.md) explains the snapshot. A closed or merged
`pr.state` ends this pull request's run: report it. A draft is the user's to
mark ready; report it and move on.

## 2. Check the description

Run [the describe-pr skill](../describe-pr/SKILL.md) on it. A title or body
it corrects is a problem for the record.

## 3. Get CI green

With `checksState` of:

- `pending`: `gw ci-checks NUMBER --wait --json`, in the background where the host
  can.
- `passed`: go to step 4.
- `failed`: fix it, below.
- `blocked` or a settled `unknown`: report it. A manual job or missing CI is
  for a person to decide.

### Fixing a failure

Work in the worktree that holds the pull request's branch, never in the
main checkout:

```sh
gw list --json | jq -r --arg b 'BRANCH' '.worktrees[] | select(.branch == $b) | .path'
```

When none does, `command gw add 'BRANCH' --json` checks the local branch
out in a new worktree and prints its `path`. If only the remote has the
branch, `gw sync` and then `git branch 'BRANCH' refs/remotes/REMOTE/BRANCH`
first. A pull request from a fork the user cannot push to is a blocker:
report it.

The branch must hold exactly `pr.headSha` before anything changes. If it is
behind, `git merge --ff-only` its remote-tracking ref; if it has diverged,
stop and report it rather than choosing a side.

For each failing check, read its log as the ci skill shows, and find the
cause before changing anything:

| cause | remedy |
| --- | --- |
| the code or a test is wrong | fix it, run that test and the repository's checks |
| formatting or generated files | run the repository's formatter or generator, read its diff |
| a runner outage, a timeout, a flaky test shown to pass on retry | `gh run rerun RUN --failed`, once |
| the base moved and the branch is out of date | `gh pr update-branch NUMBER` |

Never make a check pass by skipping, deleting or loosening it, and never
edit CI configuration to get past a failure unless the configuration is
what is broken. Commit by path with the repository's conventions and
trailers, never `git add -A`, then `git push` to the branch's remote. A
push the remote refuses is a blocker, not a reason to force.

After a push the old snapshot is about a commit that is no longer the head:
run step 2 again for the new commits, then `gw ci-checks NUMBER --wait --json`.
Three remedies that fail on one cause make it a blocker: report what was
tried.

## 4. Merge it

Read `pr.mergeStateStatus` in a fresh `gw ci-checks NUMBER --json`. `CLEAN`,
`HAS_HOOKS` and `UNSTABLE` merge. `BLOCKED` is a required review or rule,
`DIRTY` a conflict, `BEHIND` a base that moved (step 3's last row): report
the first two as blockers. `DRAFT` is step 1's draft. `UNKNOWN` means
GitHub has not worked it out yet, which is usual just after a push: read
again a minute later, never merge on it, and report it only when it stays.

Pick the strategy: `release.merge` in `.releasetools.yaml` when the
repository has one, otherwise the first that
`gh repo view --json squashMergeAllowed,mergeCommitAllowed,rebaseMergeAllowed`
allows of squash, merge and rebase.

For a squash or a merge commit, the message is the pull request's own
title and body, which step 2 made true:

- Subject: the title, then ` (#NUMBER)`, as GitHub would have written it.
- Body: the pull request body, without the lines that only make sense on a
  pull request page, such as a `Generated with` footer, the auto-resolution
  block, screenshots or task lists, and keeping any `release-note` block.
- Then every trailer from the branch's commits, copied from this output
  rather than retyped, once each:

```sh
git log --reverse --format='%(trailers:only,unfold)' 'BASE_SHA..HEAD_SHA' | awk 'NF && !seen[$0]++'
```

`BASE_SHA` is the merge base of `pr.headSha` and the remote's `pr.baseRef`.
Write the subject and body in your reply, under the pull request's URL,
then write the body to a file with the Write tool and merge:

```sh
gh pr merge NUMBER --squash --subject 'SUBJECT' --body-file FILE --match-head-commit HEAD_SHA
glab mr merge NUMBER --squash --squash-message "$(cat FILE)" --sha HEAD_SHA --auto-merge=false
```

`--merge` or `--rebase` replace `--squash` for those strategies; a rebase
takes no subject or body. `HEAD_SHA` is the `pr.headSha` CI passed on, so a
push since then refuses the merge instead of landing unseen work. Never add
`--admin`, `--auto` or `--delete-branch`. glab sets auto-merge by default
while a pipeline runs, which `--auto-merge=false` turns off.

Then read it back:

```sh
gh pr view NUMBER --json state,mergeCommit,mergedAt,autoMergeRequest
```

Only `MERGED` is merged. Where the base branch requires a merge queue,
`gh pr merge` exits 0 having only queued the pull request. It names the
queue on stderr: a warning that the queue sets the strategy, or that the
pull request is already queued. That warning or a non-null
`autoMergeRequest` is reported as queued. `OPEN` after an exit of 0 with
neither is reported as still open, never as merged or queued. A refusal is
reported as the forge worded it, and the merge is not tried again in
another way.

## 5. Afterwards

Once the run has merged at least one pull request, bring the checkout that
holds the head branch up to what landed, usually the main checkout:

```sh
gw list --json | jq -c '(.head.ref // "" | sub("^refs/remotes/[^/]+/|^refs/heads/"; "")) as $b
  | {branch: $b, path: [.worktrees[] | select($b != "" and .branch == $b) | .path][0]}'
```

A null `path` means no checkout holds it, and nothing needs moving. Else
follow [the fast-forward skill](../fast-forward/SKILL.md) in that
checkout, with `(cd 'PATH' && command gw fast-forward --json)`. On the head
branch it only fast-forwards; a refusal that the branch is ahead of the
remote is reported, never worked around. Offer
[the prune skill](../prune/SKILL.md) for the merged branches' worktrees;
removing them waits for the user.

## 6. Report

Lead with each pull request's outcome, merged with its commit, queued,
still open, waiting on the one below it, or blocked with the reason, and its
URL. Then the record, one short row per
problem:

| PR | problem | solution |
| --- | --- | --- |
| #41 | body named a flag the diff removed | dropped the line |
| #41 | `lint` failed: unused import in `ci.py` | removed it, pushed `3f2a9c1` |
| #42 | `tests` timed out on the runner | reran the job; passed |

No problems is a line saying so. A blocker names the branch, worktree or
commit that holds the unfinished work.
