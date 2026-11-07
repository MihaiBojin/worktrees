---
name: worktrees
description: >
  Remove the linked git worktrees and local branches that the worktrees CLI
  proves finished, after showing them and asking which to remove. Use when the
  user asks to clean up, tidy or prune finished, merged or squash-merged
  branches or worktrees, or asks which of them are done. Triggers on: clean up
  branches, prune worktrees, delete merged branches, squash-merged branches
  left behind, which branches are finished, gwp, gwr, gwbs, gwbd.
---

# worktrees: remove what is proved finished

The `worktrees` CLI decides. `gwp --json` and `gwbs --json` say which
worktrees and branches are finished, with the proof; `gwr` and `gwbd` remove
them, assessing each again first and refusing anything that is no longer
proved. This skill runs those four, shows the answer compactly and asks
before removing anything. It makes no judgement of its own about whether a
branch is merged.

The commands come from `uv tool install git-worktrees`. If `gwp` is not
found, say so and stop.

## 1. Assess

Run these two from inside the repository, in this order, each exactly as
written.

Branches first:

```sh
gwbs --json | jq -c '{
  branches: [.verdicts[] | select(.eligible) | {branch, proof, undo}],
  empty: [.verdicts[] | select(.why | endswith("needs --delete empty"))
    | {branch, undo}],
  stashed: [.verdicts[] | select(.why | endswith("need --delete stashes"))
    | {branch, undo, verdict, stashes}],
  kept: [.verdicts[] | select((.eligible | not) and .verdict != "unknown"
    and (.why | endswith("needs --delete empty") | not)
    and (.why | endswith("need --delete stashes") | not))] | length,
  unknown: [.verdicts[] | select(.verdict == "unknown")] | length
}'
```

Then worktrees:

```sh
gwp --json | jq -c '{
  head,
  lastSync,
  sync: (if .lastSync.refs == null then "never"
    elif now - ([.lastSync.refs, .lastSync.forge] | map(select(. != null)
      | fromdate) | min) > 600 then "stale"
    else "fresh" end),
  worktrees: [.verdicts[] | select(.eligible) | {branch, path, why}],
  empty: [.verdicts[] | select(.why | endswith("needs --delete empty"))
    | {branch, path, why}],
  stashed: [.verdicts[] | select(.why | endswith("need --delete stashes"))
    | {branch, path, verdict, why, stashes}],
  kept: [.verdicts[] | select((.eligible | not) and .verdict != "unknown"
    and (.why | endswith("needs --delete empty") | not)
    and (.why | endswith("need --delete stashes") | not))] | length,
  unknown: [.verdicts[] | select(.verdict == "unknown")] | length,
  ignored: [.verdicts[] | select((.eligible | not) and .ignored > 0) | .branch]
}'
```

- `head` is the ref everything was measured against, as `ref` and `source`:
  `remote` after a `gw sync`, `clone` from the clone, `inferred` from `main`
  and `master`. A `ref` of null means it does not resolve.
- `sync` is `never` when no `gw sync` has recorded the refs, `stale` when
  the older of `lastSync`'s times is more than ten minutes old, and `fresh`
  otherwise.
- `branches` are local branches with no worktree that `gwbd` would delete.
  `proof` is `forge`, `ancestry`, `squash` or `content`; `forge` means a
  merged request the last `gw sync` recorded for this tip. `undo` recreates
  the branch.
- `worktrees` are linked worktrees `gwr` would remove, together with their
  branches. `why` names the proof, such as `its tree or combined patch is
  already in the head branch`.
- `empty`, in both, lists branches and worktrees whose tip is on the head
  branch's main line and that only `--delete empty` holds back: no commits
  of their own, which is what one somebody has just started looks like, and
  what a fast-forward merge leaves.
- `stashed`, in both, lists finished branches and worktrees held back by
  stashes on the branch, with the stashes' shas. `--delete stashes` lets
  the CLI remove them and drop those stashes. A row whose `verdict` is
  `empty` also needs `--delete empty`.
- `ignored` names finished worktrees held back only because they hold
  gitignored files.
- `kept` and `unknown` count the rest. `kept` includes finished and `empty`
  rows that a protection holds back, such as uncommitted changes, a lock, or
  an initialized submodule or its store. The main checkout is always among
  the kept worktrees, and `main` and `master` are never eligible.

`gwbs` leaves out every branch checked out in a worktree. Those appear only
in `worktrees`, so nothing is listed twice.

## 2. Head and sync first

Before any table, say what the head is, as `Head: REF (SOURCE)`. When its
`ref` is null, say the head branch does not resolve, that nothing can be
removed until it does, and stop.

Then the sync state. Never run `gw sync` unless the user asks for it: it is
the one command that reaches the network.

- `fresh`: say when the last sync was, and go on.
- `stale`: say how old the last sync is, and ask whether to run `gw sync`
  before going on.
- `never`: run `git remote`. When it prints nothing there is no remote to
  sync from: say so, and go on. Otherwise say the verdicts use the refs from
  the clone or the last fetch and no forge evidence, and ask whether to run
  `gw sync` before going on.

When the user says yes, run `gw sync 2>&1`, report what it printed, and run
both commands in section 1 again. When they say no, go on with what is here.

## 3. Show

One table of what is eligible, worktrees first, then branches:

```
KIND      NAME                PROOF                                                     PATH
worktree  fix-parser          its tree or combined patch is already in the head branch  ~/git/.worktrees/fix-parser/repo
branch    chore/old-renovate  squash
```

Shorten `$HOME` to `~`. Under the table, one line each: how many worktrees
and branches were kept, how many are unknown, and the `ignored` names if
there are any. Then say that nothing is removed without proof from the CLI,
and that both commands assess again before removing.

Then, apart from the table, two short lists when they have rows: the `empty`
ones, headed `no commits of their own`, and the `stashed` ones, headed
`held back by stashes`, with how many stashes each has. Say that each is
removed only on request, and what that removes: an `empty` branch, or a
branch together with its stashes.

When every list is empty, say so with the counts and stop.

## 4. Ask

Never remove anything without an answer.

In Claude Code, use the AskUserQuestion tool. It takes two to four options;
offer the ones that apply:

- Remove all proven: every `worktrees` and `branches` row.
- Only branches: the `branches` rows.
- Only worktrees: the `worktrees` rows.
- Remove nothing.

"Other" carries names: a list to remove, or "all except" a list to keep.
`empty` and `stashed` rows are never among the options. One is removed only
when the user names it, or asks for that list by name, and the removal
carries the permission for that list alone.

Where there is no AskUserQuestion tool, as in Codex, ask the same question in
plain text and wait for the reply.

## 5. Remove

Only through the CLI, and only what the user chose. Quote each argument for
the shell. `command` runs the binary, not the shell function that would
change directory.

- Each chosen worktree: `command gwr 'PATH' --yes 2>&1`, with `PATH` the
  row's `path` exactly as the JSON gave it. One call per worktree. It
  removes the worktree and its branch. Add `--delete empty` for a row from
  `empty`, `--delete stashes` for a row from `stashed`, and `--delete
  ignored-files` for a row from `ignored`, each only when the user approved
  that row. A `stashed` row whose `verdict` is `empty` takes both `--delete
  empty --delete stashes`, and only when the user approved both lists for
  it; otherwise leave it alone.
- The chosen branches, in one call: `command gwbd 'NAME' 'NAME' ... --yes
  2>&1`. Approved `empty` branches and approved `stashed` branches each go
  in a call of their own, with `--delete empty` or `--delete stashes`, and
  an `empty` branch in `stashed` with both, under the same rule.

The skill cannot remove the worktree it runs in. `gwp` never proposes it,
and `gwr` refuses it at exit 3 without a terminal: say so, and that the user
can remove it with `gwr` from their own shell.

Never `git branch -D`, `git worktree remove`, `git update-ref -d`, `git stash
drop` or `rm`. Never pass `--keep-branch`, whatever a refusal suggests: it
removes a worktree that is not proved finished. Never run `gwp --yes`: it
removes every eligible worktree, not the ones the user chose. Never pass
`--delete all`. Never pass `--delete ignored-files` unless the user asked
for it by name: it deletes the gitignored files in the worktree, and nothing
restores them.

What the output means:

- `gwr` prints the branch and its path, then `restore with: git branch NAME
  SHA`, and for each stash it will drop `dropping stash SUBJECT, restore
  with: git stash store -m 'SUBJECT' SHA`, before removing. After, it prints
  `removed worktree PATH`, `deleted branch NAME` and `dropped stash
  SUBJECT`. When the worktree is no longer proved finished it removes
  nothing, prints `NAME is not finished: REASON`, `NAME is empty: REASON` or
  `NAME is finished: REASON` and exits 1. When it changed after the
  assessment it prints `NAME kept: REASON; nothing removed` and exits 1.
  `branch NAME not deleted: REASON` means the worktree went and the branch
  stayed, with its stashes, at exit 1. `stash SUBJECT not dropped` means
  the stash list changed under it; the stash is still there. A path that
  matches no worktree, such as one already removed, prints `no worktree
  matches` and exits 1 with nothing removed.
- `gwbd` prints its plan and the `git stash store` line for each stash it
  will drop, then `each comes back with:` and one `git branch NAME SHA` per
  branch, then `deleted N branch: ...`. A branch it refuses prints `NAME
  kept: REASON` and the command exits 3; the others are still deleted.

## 6. Report

Three short parts:

- what was removed: worktrees, branches and stashes, each on its own;
- what the CLI refused, with its reason;
- the restore commands, as printed, `git branch` and `git stash store`
  alike, in one fenced block.

Say that a restore command works while its commit is still in the
repository, until `git gc` prunes it. It does not recreate a removed
worktree's directory.
