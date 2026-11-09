---
name: ship-it
description: >
  Take a change to a merged pull request or merge request, unassisted. With
  no numbers, the change in this checkout from uncommitted work: commit,
  bring the branch level with the head branch, push and open the review.
  With numbers, those open reviews, a stack bottom first. Then fix failing
  CI, merge, and report what was fixed. Use when the user asks to ship,
  merge or land a change or a pull request. With --hold-merge, stop before
  the merge and print the command that makes it.
argument-hint: "[NUMBER ...] [--hold-merge]"
allowed-tools:
  - Bash(gw sync)
  - Bash(gw list --json)
  - Bash(gw branch create:*)
  - Bash(gw fast-forward:*)
  - Bash(gw ci-checks:*)
  - Bash(jq:*)
  - Bash(mktemp:*)
  - Bash(cat:*)
  - Bash(git status:*)
  - Bash(git diff:*)
  - Bash(git log:*)
  - Bash(git show:*)
  - Bash(git rev-parse:*)
  - Bash(git add --:*)
  - Bash(git commit:*)
  - Bash(GIT_EDITOR=true git rebase --continue)
  - Bash(git push --set-upstream:*)
  - Bash(git push --force-with-lease --force-if-includes:*)
  - Bash(gh pr view:*)
  - Bash(gh pr diff:*)
  - Bash(gh pr create:*)
  - Bash(gh pr edit:*)
  - Bash(gh pr merge:*)
  - Bash(gh run view:*)
  - Bash(gh run rerun:*)
  - Bash(gh repo view:*)
  - Bash(glab mr view:*)
  - Bash(glab mr diff:*)
  - Bash(glab mr create:*)
  - Bash(glab mr update:*)
  - Bash(glab mr merge:*)
  - Bash(glab ci trace:*)
  - Bash(glab ci retry:*)
  - Read
  - Edit
  - Write
---

# Ship a change, unassisted

In Codex, `$ARGUMENTS` means the arguments supplied with the skill. Each
number is an open review to ship; with none, the change is the one in this
checkout. `--hold-merge` does everything below except the merge.

The user invoking this skill approves every step below for what it ships.
For this checkout's change, that is the branch, the commits, the pushes,
the review and its description, the CI fixes and the merge. For each named
review, it is the fixes pushed to its branch, its description and its
merge. It approves nothing else: no other branch or review, no removal, no
admin bypass, no deleted branch, no disabled check. Do not ask for routine
steps. Where a skill this one follows would ask the user, take the safe
default below or stop and report.

`gw` comes from `uv tool install git-worktrees`; if it is missing, report
that and stop.

Keep a record as you go, one row per problem: the review, what was wrong,
what fixed it and the commit that carries it. It is the report at the end,
and the rows CI raised become the auto-resolution block.

## 0. The rights it needs

A plugin cannot grant its own skill the right to push or merge. Read
[the rules ship-it needs](rights.md) and the client's configuration:

- Claude Code: the rules in [rules.json](../preflight/rules.json), against
  `permissions.allow` and `permissions.deny` in `~/.claude/settings.json`,
  the project's `.claude/settings.json` and `.claude/settings.local.json`.
- Codex: the `.rules` files in `~/.codex/rules/` and, once the project is
  trusted, its `.codex/rules/`.

When any rule is missing, name the missing lines and the file they go in
once, in your reply, then go on. In Claude Code, add that
`/worktrees:preflight user` or `/worktrees:preflight project` writes them.
The client will ask before each command those rules would have covered.
Never edit those files yourself.

## Which change

With no numbers, work in the checkout the user is in, from step 1.

With numbers, each review goes through steps 7 to 10 on its own, one at a
time in the order given, and a stack bottom first. One whose `pr.baseRef`
is the branch of a review this run did not merge, whatever the reason, is
not merged: it would land on that branch rather than the head branch.
Report it as waiting on that one.

## 1. A branch of its own

```sh
gw list --json | jq -r '.head.ref // "" | sub("^refs/remotes/[^/]+/|^refs/heads/"; "")'
git rev-parse --abbrev-ref HEAD
```

On the head branch, bring it level first, then start a branch named from
the change, short and lowercase with hyphens:

```sh
gw fast-forward --autostash --json
gw branch create 'NAME' --json
```

The uncommitted work comes along, since both start at the same commit. A
detached HEAD, printed as `HEAD`, is a blocker: report it.

## 2. Commit

Read `git status --short` and `git diff`. Commit the work that belongs to
this change by path, in commits the repository's history would recognise,
with its conventions and the trailers your harness asks for:

```sh
git add -- 'PATH' ...
git commit -m 'SUBJECT' -m 'BODY'
```

Never `git add -A`, `git add .` or `git add -u`, never `--no-verify`, and
never `--amend` a commit that is already pushed. A file you cannot assign to
the change, a secret and build output stay out of every commit; list them
in the outcome. A clean checkout with nothing to commit goes on to step 3.

## 3. Level with the head branch

```sh
gw fast-forward --autostash --json
```

Follow [the fast-forward skill](../fast-forward/SKILL.md) for each result,
with these defaults in place of its questions:

- A `conflict` on the first run is a rebase stopped before this skill ran:
  stop and report it, since it may be the user's.
- A `conflict` whose two sides want incompatible things, with nothing in
  the code or the commit messages to say which wins: stop with the rebase in
  progress, name both sides, and report it.
- A `stash-conflict`: stop and report `paths` and `stash`.
- A refusal: stop and report stderr as it is. Never set `WORKTREES_OFFLINE`.

A `rotated` result with `carried` 0 and nothing left uncommitted means the
change is already merged: report the merged review and stop. Otherwise the
branch to ship is the one checked out now.

## 4. Push

```sh
git rev-parse --verify --quiet 'refs/remotes/REMOTE/BRANCH'
git push --force-with-lease --force-if-includes 'REMOTE' 'BRANCH'
git push --set-upstream 'REMOTE' 'BRANCH'
```

The first when the remote has the branch, the second when it has not.
`REMOTE` is the default remote: the remote in `gw list --json`'s
`head.ref`, such as `origin` in `refs/remotes/origin/main`. A push the
lease rejects means the remote has commits this clone never had: stop and
report it, and never retry with `--force`.

## 5. Open the review

```sh
gh pr view 'BRANCH' --json number,state,url
glab mr view 'BRANCH' --output json
```

A review that is open is the one to ship. A merged one with nothing new is
step 3's merged change. Otherwise open one against the head branch, with a
title and body that follow [the format](../describe-pr/format.md), the body
written to a file with the Write tool:

```sh
gh pr create --base 'HEAD_BRANCH' --head 'BRANCH' --title 'TITLE' --body-file FILE
glab mr create --target-branch 'HEAD_BRANCH' --source-branch 'BRANCH' --title 'TITLE' --description "$(cat FILE)" --yes
```

Then run [the describe-pr skill](../describe-pr/SKILL.md) on it.

## 6. Release note

When the agent has the release-notes plugin's `release-notes:write` skill,
run it for this branch and review, then commit and push the changelog entry
it writes, as in steps 2 and 4. When it has not, say in one line that no
release note was written because the release-notes plugin is not
installed, and go on. The note is optional, and organising it is the
user's.

## 7. CI

```sh
gw ci-checks NUMBER --json
```

[The ci-checks skill](../ci-checks/SKILL.md) explains the snapshot. A
closed or merged `pr.state` ends a named review's run: report it. A draft is
the user's to mark ready: report it and go on to the next. A named review
runs [the describe-pr skill](../describe-pr/SKILL.md) here, as step 5 did
for this checkout's; a title or body it corrects is a row for the record.
Then wait for CI:

```sh
gw ci-checks NUMBER --wait --json
```

`passed` goes to step 8. `blocked`, or an `unknown` that stays, is a
blocker: a manual job or missing CI is for a person to decide. `failed` is
fixed, below.

### Fixing a failure

This checkout's change is fixed here. A named review is fixed in the
worktree that holds its branch, never in the main checkout:

```sh
gw list --json | jq -r --arg b 'BRANCH' '.worktrees[] | select(.branch == $b) | .path'
```

When none does, `command gw add 'BRANCH' --json` checks the local branch
out in a new worktree and prints its `path`. If only the remote has the
branch, `gw sync` and then `git branch 'BRANCH' refs/remotes/REMOTE/BRANCH`
first. A review from a fork the user cannot push to is a blocker: report
it. The branch must hold exactly `pr.headSha` before anything changes. If
it is behind, `git merge --ff-only` its remote-tracking ref; if it has
diverged, stop and report it rather than choosing a side.

For each failing check, read its log as the ci-checks skill shows, and find
the cause before changing anything:

| cause | remedy |
| --- | --- |
| the code or a test is wrong | fix it, run that test and the repository's checks |
| formatting or generated files | run the repository's formatter or generator, read its diff |
| a runner outage, a timeout, a flaky test shown to pass on retry | `gh run rerun RUN --failed` or `glab ci retry JOB`, once |
| the base moved and the branch is out of date | steps 3 and 4 again, in the branch's worktree |

Never make a check pass by skipping, deleting or loosening it, and never
edit CI configuration to get past a failure unless the configuration is
what is broken. `gh pr update-branch` is no remedy: it merges the head
branch into this one. Commit by path as in step 2, push as in step 4, and
put every problem and its fix in the record.

After a push the old snapshot is about a commit that is no longer the head:
run describe-pr again for the new commits, then wait again. Three remedies
that fail on one cause make it a blocker: stop and report what was tried.

## 8. The auto-resolution block

When the record has rows CI raised, add the block
[the format](../describe-pr/format.md) describes just above the review
body's `release-note` block, or at its end when there is none, with
`gh pr edit NUMBER --body-file FILE` or
`glab mr update NUMBER --description "$(cat FILE)"`. It stays on the review
page: the squash message leaves it out.

## 9. Merge

Read `pr.mergeStateStatus` in a fresh `gw ci-checks NUMBER --json`. `CLEAN`,
`HAS_HOOKS` and `UNSTABLE` merge. `BLOCKED`, a required review or rule, and
`DIRTY`, a conflict, are blockers. `BEHIND` is a base that moved: step 7's
last row. `DRAFT` is step 7's draft. `UNKNOWN` means GitHub has not worked
it out yet, which is usual just after a push: read again a minute later,
never merge on it, and report it only when it stays.

Pick the strategy: `release.merge` in `.releasetools.yaml` when the
repository has one, otherwise the first that
`gh repo view --json squashMergeAllowed,mergeCommitAllowed,rebaseMergeAllowed`
allows of squash, merge and rebase.

For a squash or a merge commit, the message is the review's own title and
body, which describe-pr made true:

- Subject: the title, then ` (#NUMBER)`, as GitHub would have written it.
- Body: the review body, without the lines that only make sense on a review
  page, such as a `Generated with` footer, the auto-resolution block,
  screenshots or task lists, and keeping any `release-note` block.
- Then every trailer from the branch's commits, copied from this output
  rather than retyped, once each:

```sh
git log --reverse --format='%(trailers:only,unfold)' 'BASE_SHA..HEAD_SHA' | awk 'NF && !seen[$0]++'
```

`BASE_SHA` is the merge base of `pr.headSha` and the remote's `pr.baseRef`.
Write the subject and body in your reply, under the review's URL, then
write the body to a file with the Write tool and merge:

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

With `--hold-merge`, do not merge. Make a file that outlives this session
with `mktemp -t ship-it.XXXXXX`, write the body into it with the Write tool,
and print the subject, the body and the one command that merges with them.
Print the command with every value written out, the file's path included,
and the strategy's flag in place of `--squash`. The head-commit check
refuses the merge if anything is pushed after CI passed. In Claude Code,
`!` before the command runs it in the session. The user may run it, change
it, or merge another way.

## 10. Afterwards and the outcome

Once the run has merged at least one review, bring the checkout that holds
the head branch up to what landed, usually the main checkout:

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

Lead with each review's outcome, merged with its commit, queued, still
open, waiting on the one below it, held with the merge command, or blocked
with the reason, and its URL. Then the record, one short row per problem:

| review | problem | solution |
| --- | --- | --- |
| #41 | body named a flag the diff removed | dropped the line |
| #41 | `lint` failed: unused import in `ci.py` | removed it, pushed `3f2a9c1` |
| #42 | `tests` timed out on the runner | reran the job; passed |

No problems is a line saying so. A blocker names the branch, worktree or
commit that holds the unfinished work. Then anything left out of the
commits, the missing rights from step 0, and a release note that was not
written.
