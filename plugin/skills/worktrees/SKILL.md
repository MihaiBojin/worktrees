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
  head_sha,
  branches: [.verdicts[] | select(.eligible) | {branch, proof, undo}],
  kept: [.verdicts[] | select((.eligible | not) and .verdict != "unknown")] | length,
  unknown: [.verdicts[] | select(.verdict == "unknown")] | length
}'
```

Then worktrees, with the `head_sha` that printed written in place of
`HEAD_SHA`:

```sh
gwp --json | jq -c '{
  worktrees: [.verdicts[] | select(.eligible)
    | {branch, path, why, at_head: (.sha == "HEAD_SHA")}],
  kept: [.verdicts[] | select((.eligible | not) and .verdict != "unknown")] | length,
  unknown: [.verdicts[] | select(.verdict == "unknown")] | length,
  ignored: [.verdicts[] | select((.eligible | not) and .ignored > 0) | .branch]
}'
```

- `branches` are local branches with no worktree that `gwbd` would delete.
  `proof` is `forge`, `ancestry`, `squash` or `content`; `forge` means a
  merged request the last `gw sync` recorded for this tip. `undo` recreates
  the branch.
- `worktrees` are linked worktrees `gwr` would remove, together with their
  branches. `why` names the proof, such as `its tree or combined patch is
  already in the head branch`.
- `at_head` is true when the worktree's branch sits exactly on the head
  branch's tip. That is a worktree with no commits of its own, which is what
  one somebody has just started looks like.
- `ignored` names finished worktrees held back only because they hold
  gitignored files.
- `kept` and `unknown` count the rest. `kept` includes finished rows that a
  protection holds back, such as uncommitted changes, a lock or an initialized
  submodule. The main checkout is always among the kept worktrees, and `main`
  and `master` are never eligible.

`gwbs` leaves out every branch checked out in a worktree. Those appear only
in `worktrees`, so nothing is listed twice.

## 2. Show

One table, worktrees first, then branches:

```
KIND      NAME                PROOF                                                     PATH
worktree  fix-parser          its tree or combined patch is already in the head branch  ~/git/.worktrees/fix-parser/repo
branch    chore/old-renovate  squash
```

Shorten `$HOME` to `~`. Mark an `at_head` row `(no commits of its own)`.
Under the table, one line each: how many worktrees and branches were kept, how
many are unknown, and the `ignored` names if there are any. Then say that
nothing is removed without proof from the CLI, and that both commands assess
again before removing.

When both lists are empty, say so with the counts and stop.

## 3. Ask

Never remove anything without an answer.

In Claude Code, use the AskUserQuestion tool. It takes two to four options;
offer the ones that apply:

- Remove all proven: every row except those marked `at_head`.
- Only branches: the `branches` rows.
- Only worktrees: the `worktrees` rows not marked `at_head`.
- Remove nothing.

"Other" carries names: a list to remove, or "all except" a list to keep. An
`at_head` worktree is removed only when the user names it.

Where there is no AskUserQuestion tool, as in Codex, ask the same question in
plain text and wait for the reply.

## 4. Remove

Only through the CLI, and only what the user chose. Quote each argument for
the shell.

- Each chosen worktree: `command gwr 'PATH' --yes 2>&1`, with `PATH` the
  row's `path` exactly as the JSON gave it. One call per worktree. It
  removes the worktree and its branch. `command` runs the binary, not the
  shell function that would change directory.
- The chosen branches, in one call: `gwbd 'NAME' 'NAME' ... --yes 2>&1`.

The skill cannot remove the worktree it runs in. `gwp` never proposes it,
and `gwr` refuses it at exit 3 without a terminal: say so, and that the user
can remove it with `gwr` from their own shell.

Never `git branch -D`, `git worktree remove`, `git update-ref -d` or `rm`.
Never pass `--force`, whatever a refusal suggests: it removes a worktree that
is not proved finished. Never run `gwp --yes`: it removes every eligible
worktree, not the ones the user chose. Never pass `--delete all`. Never pass
`--delete ignored-files` unless the user asked for it by name: it deletes the
gitignored files in the worktree, and nothing restores them. When they do
ask for a worktree in `ignored`, it is
`command gwr 'PATH' --delete ignored-files --yes 2>&1`.

What the output means:

- `gwr` prints the branch and its path, then `restore with: git branch NAME
  SHA`, before removing, and `removed NAME` after. When the worktree is no
  longer proved finished it removes nothing, prints `NAME is not finished:
  REASON` or `NAME is finished: REASON` and exits 1. A path that matches no
  worktree, such as one already removed, prints `no worktree matches` and
  exits 1 with nothing removed.
- `gwbd` prints `each comes back with:` and one `git branch NAME SHA` per
  branch, then `deleted N branch: ...`. A branch it refuses prints
  `NAME kept: REASON` and the command exits 3; the others are still deleted.

## 5. Report

Three short parts:

- what was removed, worktrees and branches;
- what the CLI refused, with its reason;
- the undo commands, as printed, in one fenced block.

Say that an undo command recreates the branch while its commit is still in
the repository. It does not recreate a removed worktree's directory.
