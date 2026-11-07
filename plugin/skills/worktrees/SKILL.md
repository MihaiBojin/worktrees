---
name: worktrees
description: >
  Use when the user asks to clean up, tidy or prune finished, merged or
  squash-merged git worktrees or local branches, or asks which are done.
---

# Clean up worktrees and loose branches

The `worktrees` CLI supplies completion proofs and deletion protections.
It comes from `uv tool install git-worktrees`; if its commands or `jq` are
missing, report that and stop. Work inside the requested repository.

## Assess and show sync state first

Read worktrees, then loose branches, using these pipelines:

```sh
gwp --json | jq -c '
def permission: test("^(empty branches require deletion permission|its branch has stashes|it holds [0-9]+ ignored path\\(s\\); pass --delete ignored-files)$");
def grantable: .eligible == false and (.verdict == "remove" or .verdict == "empty") and all(.protections[]; permission);
{
  head, lastSync,
  worktrees: [.verdicts[] | select(.eligible)],
  empty: [.verdicts[] | select(.verdict == "empty" and grantable)],
  stash_blocked: [.verdicts[] | select(grantable and (.stashes | length) > 0)],
  ignored: [.verdicts[] | select(grantable and .ignored > 0)],
  kept: [.verdicts[] | select(.verdict == "keep")] | length,
  unknown: [.verdicts[] | select(.verdict == "unknown")] | length
}'
```

```sh
gwbs --json | jq -c '
def permission: test("^(empty branches require deletion permission|its branch has stashes)$");
def grantable: .eligible == false and (.verdict == "remove" or .verdict == "empty") and all(.protections[]; permission);
{
  head, lastSync,
  branches: [.verdicts[] | select(.eligible)],
  empty: [.verdicts[] | select(.verdict == "empty" and grantable)],
  stash_blocked: [.verdicts[] | select(grantable and (.stashes | length) > 0)],
  kept: [.verdicts[] | select(.verdict == "keep")] | length,
  unknown: [.verdicts[] | select(.verdict == "unknown")] | length
}'
```

Before any candidate rows, show `Head: REF (SOURCE)` from `head`, followed
by the refs and forge sync times from `lastSync`. Null means no successful
sync for that component or that it does not apply. An unresolved head
(`ref: null`) prevents removal; report it.

Use `git remote` only to distinguish no remotes from never-synced remote
state. With remotes and `lastSync.refs: null`, say never synced and ask
whether to run `gw sync` before continuing. Otherwise, with remotes, use the
older non-null sync time: more than 10 minutes old is stale, exactly
10 minutes is fresh. Ask whether to sync only for never-synced or stale
state. With no remotes, explain that assessment uses local evidence and do
not offer sync on account of null times.

For never-synced or stale remote state with no existing sync answer, the
initial response contains the head line, sync state and sync question only.
Wait for the reply. Show candidates and ask about removal after that answer.
An existing sync approval or refusal already answers this stage.

Run `gw sync` only when the user asks or answers yes. Keep an existing sync
approval or refusal for this cleanup request. On approval, show the command's
actual outcome and rerun both assessments, including after a partial sync
failure. On refusal, continue with the existing evidence. A selective
removal reply does not erase that sync choice or require asking again.

## Show candidates and ask what to remove

Show eligible worktrees first, then eligible loose branches, with their
proofs and worktree paths. The `empty`, `stash_blocked` and `ignored` groups hold
only rows that the matching deletion permissions would make eligible; the
main checkout, the current checkout, protected branches and `keep` rows never
appear there. Show those groups separately, retaining all their protections; offer those categories
only when the user asks. Name ignored-file blockers separately, listing each
row's `ignoredPaths` so the user can decide before granting permission. A row can
have more than one blocker, so show every required permission.

A checked-out branch is already represented by its worktree and is absent
from `gwbs`. Never target the current checkout, even if the user names it;
identify it from the current working directory or `gw list --json`'s
`worktrees[].here` if needed. The main checkout and protected branches remain
blocked too. Do not infer eligibility from a verdict alone.

Ask which displayed targets to remove and wait for an answer. Where
AskUserQuestion is available, use it; otherwise ask in plain text. Offer all
eligible rows, only worktrees, only loose branches, named selections or
nothing as appropriate. Preserve the user's chosen targets and exclusions.
A prior explicit selection and category approval for this cleanup already
answers the question; do not ask again just because assessment was refreshed.

Deletion permission covers only the category and targets the user approved:
`empty`, `ignored-files` or `stashes`. `--yes` adds no permission. Explain
that ignored files cannot be restored and that `empty` includes unused
branches and fast-forward merges without forge proof. Keep other protections
in place. If the user requests a blocked category, get the missing explicit
permission before removing those rows. With no chosen targets, stop after
the assessment.

## Remove the approved targets

For each chosen worktree, invoke the binary with its exact JSON path,
quoted for the shell:

```sh
command gwr 'PATH' --yes
```

One call per worktree. Preserve spaces and literal quotes in the path; a
single quote inside a single-quoted value needs shell escaping. Use the
path, not a branch-name approximation. Add repeated `--delete VALUE` only
for that target's approved categories. Do not substitute `--delete all`
for a narrower approval or add `--keep-branch` on your own.

Then invoke chosen loose branches, quoting every exact branch name:

```sh
command gwbd 'NAME' 'OTHER_NAME' --yes
```

Add only their approved `--delete empty` or `--delete stashes` values. Split
branch calls when their category approvals differ. Do not run bulk `gwp`
for a selective approval. The CLI reassesses before removing and can refuse
changed targets. Use its commands rather than raw Git deletion or `rm`.

## Report actual outcomes and restoration

Read stdout, stderr and status from every removal. Report checkout removal,
branch deletion and stash outcomes separately, including refusals and
partial failures. A failed command can already have removed its checkout or
branch. Never turn a refused or retained step into a success report.

Keep exact branch restore commands printed by the CLI; loose-branch rows
also carry `undo`. When a worktree row needs a branch restore command,
construct `git branch NAME SHA` from its exact branch and full SHA with
shell quoting. Stash subjects are absent from assessment JSON: retain the
actual `git stash store -m SUBJECT SHA` commands from removal disclosure,
including their exact subject quoting. Do not invent a subject.

Present restore commands for what was actually deleted. A branch restore
recreates its ref, not its worktree directory. A dropped stash can be
restored until Git garbage collection prunes its unreachable commit.
