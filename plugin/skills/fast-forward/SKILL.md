---
name: fast-forward
description: >
  Use when the user asks to bring a branch up to date with main or the head
  branch, rebase it onto the head branch, fast-forward it, move on from a
  merged or partly merged branch, or finish a rebase that gw fast-forward
  stopped at.
argument-hint: "[NAME] [--from BRANCH|SHA]"
allowed-tools:
  - Bash(gw fast-forward:*)
  - Bash(git status:*)
  - Bash(git diff:*)
  - Bash(git log:*)
  - Bash(git show:*)
  - Bash(git add:*)
  - Bash(GIT_EDITOR=true git rebase --continue)
  - Read
  - Edit
  - AskUserQuestion
---

# Bring a branch level with the head ref

The `gw` CLI decides what happens to the branch. This skill resolves the
conflicts its rebase stops at. `gw` comes from `uv tool install
git-worktrees`; if it is missing, report that and stop. Work in the checkout
the user is in.

`gwff` is a shell plugin function that runs `gw fast-forward`. An agent's
shell has no plugin functions, so call the binary.

## Run it

```sh
gw fast-forward --autostash --json
```

`--autostash` carries uncommitted changes across through git's own
autostash; pass it every time, without asking.

In Codex, `$ARGUMENTS` means the arguments supplied with the skill. They
pass through, quoted for the shell: `gw fast-forward 'NAME' --from 'REF'
--autostash --json` names the branch a finished one moves to and where it
starts. The CLI refuses both on a branch that is not finished.

Read stdout, stderr and the exit status.

- Exit 0: the branch is level. Report the result.
- Exit 1 with `"result": "conflict"` on stdout: resolve it, below.
- Exit 1 with `"result": "stash-conflict"`: the branch moved, and the
  uncommitted changes it carried did not re-apply. The files in `paths` hold
  conflict markers between those changes and the new code, and `stash` names
  the entry that still holds the changes as they were. Report both, with
  `git stash apply STASH` as the way back. The changes are the user's own
  work: resolve the markers only when the user asks, and never drop the
  entry.
- Any other non-zero exit leaves stdout empty. That is a refusal, and the
  branch is unchanged. Report stderr as the CLI wrote it and stop. Do not
  work around it with `git rebase`, `git merge`, `git switch` or `git stash`.
  A refusal whose last line is `Run: WORKTREES_OFFLINE=1 ...` means the
  fetch or the forge query failed. Report it; never set `WORKTREES_OFFLINE`
  yourself, since the run it skips is the one that sees a merge made a
  moment ago.

A `conflict` from the first run of a request is a rebase that was stopped
before you ran anything, perhaps one the user paused in `git rebase -i`.
Show its `stopped_at`, `subject` and `paths`, and ask before resolving or
continuing it. Continue on your own only a rebase this request started.

The command fetches and asks the forge on every run, so the head ref is the
remote's. Under `WORKTREES_OFFLINE`, when `lastSync.refs` is null or more than
10 minutes old after the run, say the head ref can be behind the remote. Do
not run `gw sync` yourself.

The result's fields:

- `result`: `current`, `fast-forwarded`, `rebased`, `rotated`, `conflict` or `stash-conflict`
- `branch`, `sha`: the branch checked out now and its commit
- `base`: what it was brought level with
- `previous`, `previous_sha`: the branch and commit before the run
- `ahead`, `behind`: its own commits after, the head ref's it lacked before
- `proof`, `why`: how a rotated branch, or its merged part, was proved
- `boundary`, `carried`: a rotated branch's last merged commit, and the commits after it replayed onto the new one
- `stopped_at`, `subject`: the commit the rebase stopped at
- `paths`: files with conflicts, relative to the top level
- `stash`: the stash entry holding changes that did not re-apply
- `stashes`: a partly merged or rotated branch's stashes, each with `sha`, `subject`, `after`, true for one made after the merged part, `moved`, the copy such a stash moved to, and `kept`, why one did not move

## Resolve a conflict

The rebase is replaying `stopped_at` onto the head ref's work. In each file
in `paths`, the side above `=======` is the head ref plus the commits already
replayed; the side below is `stopped_at`. Under `merge.conflictStyle` diff3
or zdiff3, the lines after `|||||||` are what both sides started from.

For each path:

1. Read the file, every conflicted hunk of it.
2. Find what each side meant. `git show STOPPED_AT -- PATH` shows the
   branch's change; `git log -p PREVIOUS_SHA..BASE -- PATH` shows what the
   head ref changed there since the branch left it.
3. Edit the file so it carries both changes, and remove every marker. When
   the two sides want incompatible things and the code and commit messages
   do not say which wins, ask the user rather than pick one.
4. `git add PATH`, naming only paths from `paths`. Never `git add -A`,
   `git add .` or `git add -u`: untracked files can sit in the checkout
   during a rebase, and those would sweep them into a rewritten commit.

When every path is staged, continue with the editor pinned, so git does not
wait on a commit message nobody can type:

```sh
GIT_EDITOR=true git rebase --continue
```

Then run `gw fast-forward --json` again. A new `conflict` is the next commit
stopping: repeat. `paths: []` means everything is staged and the rebase
still needs continuing. `current` means it finished.

If `git rebase --continue` fails without leaving conflicts, report its
stderr and stop with the rebase in progress. `git rebase --abort` puts the
branch back at `previous_sha`; run it only when the user asks. Never run
`git rebase --skip`, which drops the branch's commit, and never take one
side of a file wholesale, as `git checkout --ours` or `--theirs` does,
without telling the user that is what you did.

## Report what happened

Name the branch and its commit before and after. For `rotated`, say the
finished branch is kept as it was, how it was proved finished, and the new
branch's name; the new branch has no upstream until it is pushed. With
`carried` above zero, say that the commits up to `boundary` were merged and
the `carried` ones after it moved to the new branch. For `rebased` with
`carried` above zero, say that the commits up to `boundary` were already in
the head branch, merged through another branch's request, and were dropped.
List `stashes` made after the merged part. Each `moved` one is on the branch
holding the commits now, under the sha in `moved`. Give each `kept` reason,
and its `sha` for `git stash apply`: after a rebase in place, a kept stash
matches no branch. For a rebase that
stopped, list each commit it stopped at with its subject, and for each file,
what each side changed and how the two were combined.

## Push

A rebased branch the remote already has, `refs/remotes/REMOTE/BRANCH`
existing, goes with a lease; a branch the remote lacks goes plainly:

```sh
git push --force-with-lease --force-if-includes REMOTE BRANCH
git push --set-upstream REMOTE BRANCH
```

After a `rebased` result on a branch the remote has, push it with the lease,
so its open request shows the rebased commits. After a rotation, give the
plain push and run it when the user asked for the branch to be pushed. The
client asks before either runs unless the user's own rules allow it.
A push the lease rejects means the remote has commits this clone never had:
report it, and never retry with `--force`.
