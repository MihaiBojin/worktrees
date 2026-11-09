---
name: ship-it
description: >
  Take the change in this checkout from uncommitted work to a merged pull
  request or merge request, unassisted: commit, bring the branch level with
  the head branch, push, open the review, fix failing CI, merge, and report
  what was fixed. Use when the user asks to ship the change. With
  --hold-merge, stop before the merge and print the command that makes it.
argument-hint: "[--hold-merge]"
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

# Ship the change, unassisted

In Codex, `$ARGUMENTS` means the arguments supplied with the skill.
`--hold-merge` is the only one: do everything below except the merge.

The user invoking this skill approves every step below for this change: the
branch, the commits, the pushes, the review and its description, the CI
fixes and the merge. It approves nothing else: no other branch or review, no
removal, no admin bypass, no deleted branch, no disabled check. Do not ask
for routine steps. Where a skill this one follows would ask the user, take
the safe default below or stop and report.

`gw` comes from `uv tool install git-worktrees`; if it is missing, report
that and stop. Work in the checkout the user is in.

Keep a record as you go, one row per problem CI raised: the check, the
cause, the fix and the commit that carries it. It becomes the
auto-resolution block.

## 0. The rights it needs

A plugin cannot grant its own skill the right to push or merge. Read
[the rules ship-it needs](rights.md) and the client's configuration:

- Claude Code: `permissions.allow` in `~/.claude/settings.json`, the
  project's `.claude/settings.json` and `.claude/settings.local.json`.
- Codex: the `.rules` files in `~/.codex/rules/` and, once the project is
  trusted, its `.codex/rules/`.

When any rule is missing, name the missing lines and the file they go in
once, in your reply, then go on. The client will ask before each command
those rules would have covered. Never edit those files yourself.

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
`head.ref`, such as `origin` in `refs/remotes/origin/main`. A push the lease rejects means the remote has commits this clone
never had: stop and report it, and never retry with `--force`.

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
gw ci-checks NUMBER --wait --json
```

[The ci-checks skill](../ci-checks/SKILL.md) explains the snapshot. Fix each
failure as [the merge skill](../merge/SKILL.md) does, in its table of causes
and remedies, then commit by path, push as in step 4, run describe-pr again
and wait again. A base that moved is steps 3 and 4 again, not
`gh pr update-branch`, which would merge the head branch into this one. Every problem and its fix go in the record. Three remedies
that fail on one cause make it a blocker: stop and report what was tried.
`blocked`, or an `unknown` that stays, is a blocker too.

## 8. The auto-resolution block

When the record has rows, add the block [the format](../describe-pr/format.md)
describes just above the review body's `release-note` block, or at its end
when there is none, with `gh pr edit NUMBER --body-file FILE` or
`glab mr update NUMBER --description "$(cat FILE)"`. It stays on the review
page: the squash message leaves it out.

## 9. Merge

Follow the merge skill's step 4: read `mergeStateStatus` again, pick the
strategy, build the subject and body, and merge with the head commit CI
passed on. `BLOCKED` and `DIRTY` are blockers.

With `--hold-merge`, do not merge. Make a file that outlives this session
with `mktemp -t ship-it.XXXXXX`, write the body into it with the Write tool,
and print the subject, the body and the one command that merges with them:

```sh
gh pr merge NUMBER --squash --subject 'SUBJECT' --body-file FILE --match-head-commit HEAD_SHA
glab mr merge NUMBER --squash --squash-message "$(cat FILE)" --sha HEAD_SHA --auto-merge=false
```

Print the command with every value written out, the file's path included,
and the strategy's flag in place of `--squash` when the merge skill picks
another. The head-commit check refuses the merge if anything is pushed after
CI passed. In Claude Code, `!` before the command runs it in the session.
The user may run it, change it, or merge another way.

## 10. Afterwards and the outcome

After a merge, follow the merge skill's step 5 to bring the head branch's
checkout level.

Lead with the outcome: merged with its commit, queued, still open, held
with the merge command, or blocked with the reason, and the review's URL. Then the
auto-resolution block, or a line saying CI needed no fixes. Then anything
left out of the commits, the missing rights from step 0, and a release note
that was not written.
