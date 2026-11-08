---
name: describe-pr
description: >
  Check a pull request's or merge request's title and description against
  its diff and fix them when they are wrong. Run it, without being asked,
  right after opening a pull request, after pushing commits to a branch that
  has one open, and before merging one. Also when the user asks to write,
  check or fix a PR title, description or body.
argument-hint: "[NUMBER]"
allowed-tools:
  - Bash(gh pr view:*)
  - Bash(gh pr diff:*)
  - Bash(gh pr edit:*)
  - Bash(glab mr view:*)
  - Bash(glab mr diff:*)
  - Bash(glab mr update:*)
  - Bash(git log:*)
  - Bash(git diff:*)
  - Read
  - Write
---

# Keep a pull request's description true

In Codex, `$ARGUMENTS` means the arguments supplied with the skill. A number
names the pull request; without one it is the current branch's.

The title and body are the commit message the merge will write, so they
follow [the format](format.md). Read it before judging or writing either.

## Read the change

```sh
gh pr view NUMBER --json number,url,title,body,baseRefName,headRefName,headRefOid,commits
gh pr diff NUMBER --name-only
```

On GitLab, `glab mr view NUMBER --output json` and `glab mr diff NUMBER`.
Read the repository's rules the format names, and the last twenty subjects
on the head branch for its title convention:

```sh
git log --oneline -20 refs/remotes/REMOTE/BASE
```

Read the diff itself, `gh pr diff NUMBER`, file by file when it is large:
the claim worth checking is in the patch, not in the file list. A local
`git diff BASE...HEAD` costs no network when both refs are fetched.

## Decide

Go through the four ways a description is wrong in the format, line by
line. Then one of two outcomes:

- Right: say so in one line, naming the pull request. Change nothing.
- Wrong: fix it, as below.

## Fix it

Change only what is wrong or missing. A line that is accurate stays as its
author wrote it. Rewrite the whole body only when it is empty, a template
left unfilled, or a commit list.

Write the body to a file with the Write tool, never as a shell argument,
then:

```sh
gh pr edit NUMBER --title 'TITLE' --body-file FILE
glab mr update NUMBER --title 'TITLE' --description "$(cat FILE)"
```

Keep every `release-note` block, issue keyword, template section and
harness footer exactly as it was. Never touch a pull request in another
repository than the one the remote names, and never change its base, its
reviewers or its labels.

Then write in your reply what changed and why, one line each, under the
pull request's URL: the old title beside the new one when it changed, and
each body line added, removed or corrected. The user does not see command
output, so the reply is the only place they learn that their description
was edited.
