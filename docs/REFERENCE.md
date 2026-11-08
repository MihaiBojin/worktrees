# Runtime contracts

Bare `gw` and `gw --help` print the command guide, outside a repository too.
`gw list` selects a destination on terminal input; nonterminal input prints
matching worktree metadata. `--json` and `--table` always print metadata.
`gw WORD` with a word that is not a command exits 2 and suggests
`gw list WORD`. `gw prune` assesses worktrees and asks before removal on a
terminal. Nonterminal input, `--json` and `--table` assess without mutation;
`--yes` approves removal in any mode. `--table` prints an uncoloured table.
`--only` filters assessment rows and rejects `--yes`. `gw remove` selects one
and follows the same rules for it: nonterminal input, `--json` and `--table`
print its row, in the `verdicts` shape `gw prune --json` uses, and remove
nothing; `--yes` approves removal in any mode. It takes no `--only`, since a
single target has a single verdict. A target that is not eligible still fails
under `--yes`, and removing the worktree you stand in still needs a second
answer at a terminal.
`gw branch status` assesses loose local branches and `gw branch prune`
deletes selected eligible refs. A checked-out branch appears only as a
worktree. Each shell plugin shortcut, such as `gwp!`, runs one of these with
fixed words, and `gw --help` lists them under the command they run.
[The README](../README.md) covers invocation, permissions and migration.

## Head and evidence

The head ref comes from the selected remote's HEAD after successful sync,
then the clone's remote HEAD, then local inference. Local inference chooses
the only `main` or `master`, the newer committer date when both exist, and
`main` on a tie. With neither, the inferred `main` does not resolve.
A remote HEAD that is a plain ref rather than a symbolic one takes its
branch name from the one remote-tracking branch at its commit, and does
not resolve when several sit there.
Unresolved head means unknown verdicts and no mutation. `gw add`,
`gw branch create` and `gw rotate` also fail in that state.

Completion checks run in this order:

1. Matching cached forge proof gives `remove`.
2. A tip on the assessed head's first-parent history gives `empty`.
3. Local ancestry, squash or content proof gives `remove`.
4. Commits outside the head give `keep`; absent or invalid evidence gives
   `unknown`.

The first-parent set is computed once per assessment. A divergent local
counterpart does not extend it. Local content proof compares every changed
path from merge base to branch tip with the head tree, including modes and
deletions. A stacked branch can retain an intermediate state the head later
edited; content alone cannot prove that stack finished.

Forge proof requires a merged request for the exact source tip, verified
source repository, target repository and head branch, plus a merge commit
contained in the head ref. A closed unmerged request proves nothing. Fork
requests need verified source identity. Missing or malformed evidence never
permits removal.

## Protections and permissions

`eligible` combines the verdict, deletion permissions and protections.
`main`, `master` and the assessed head branch cannot be deleted. The main
checkout, locked worktrees, tracked changes, untracked files and initialized
submodules block worktree deletion. The submodule refusal names
`git submodule deinit --all`. A submodule store that `deinit` leaves in the
worktree's git directory also blocks deletion, and the refusal names it. A
locked worktree whose directory is missing is assessed from its record and
kept. Detached worktrees receive the same checks.
`gw prune` also protects the current worktree.

Ignored paths require `--delete ignored-files`; branch stashes require
`--delete stashes`; an `empty` verdict requires `--delete empty`. Repeated
values combine. `all` expands to those three for worktrees, and to empty
branches and stashes for `gw branch prune`. `gw branch status` rejects
`--delete`; `gw branch prune` rejects `ignored-files`. Unsupported
combinations fail at usage parsing before mutation. `--yes` grants none of
these permissions.

`gw remove --keep-branch` retains the branch and stashes and needs no completion
proof for the checkout. A detached checkout has no branch to keep its
commits and still needs proof. It permits a checkout on a protected branch, while
all other worktree protections remain. Only `--delete ignored-files` can be
combined with it.

## Deletion and recovery

Assessment pins the target path, branch ref and full SHA, assessed head ref
and SHA, ignored path list, and matching stash SHAs and subjects. Disclosure
prints the path and branch, branch restore command, each stash restore
command and ignored path. An ignored path is shown cut to its first component
below the `.gitignore` whose pattern matched it, or below the top level for
`.git/info/exclude` and `core.excludesFile`: `*.log` in the top-level file
shows `build/out/a.log` and `build/out/b.log` as one `build/`. A path with a
character that would not print is quoted and escaped as `git status` does.
The protection's count and the JSON `ignored` field count those entries.
Quiet, JSON and automatic confirmation preserve that stderr disclosure. A never-synced repository asks
`Never synced. Delete anyway?` unless `--yes` skips confirmation.

After confirmation, the command assesses again and refuses changed targets,
head identity, cleanliness, lock state, ignored paths or matching stashes.
Git can collapse an ignored directory into one status entry; this comparison
does not fingerprint contents below that entry.

Removal uses `git worktree remove` without force. Only after that succeeds,
`git update-ref --no-deref -d REF FULL_SHA` deletes the attached branch at
its assessed SHA. Detached removal and `--keep-branch` skip ref deletion.
Approved stash drops follow successful ref deletion. A failed step leaves
later steps for that target undone; outcomes distinguish checkout removal,
branch deletion and retained, dropped, absent or failed stashes. Other
selected targets can still complete.

A stash belongs to a branch when its subject begins `WIP on NAME:` or
`On NAME:` and its first parent is an ancestor of the branch tip. Detached
stashes belong to none. Each drop resolves the current index by recorded SHA
and verifies the SHA Git reports dropping. A concurrent change that drops a
different entry triggers `git stash store` with that entry's exact subject
and SHA. The restored entry is at the top of the list.

Use the printed `git branch NAME SHA` and `git stash store -m SUBJECT SHA`
commands verbatim. Branch restore recreates a ref, not a checkout. Stash
restore works until Git garbage collection prunes the unreachable commit.
Ignored files have no restore command.

For current-worktree removal, `gw remove` requires a second terminal
confirmation that `--yes` cannot skip. The process changes to the main
checkout before removal. If checkout removal succeeds, stdout carries that
destination even when branch or stash cleanup later fails; exit status still
reports failure. The shell functions move to it and preserve that status. JSON
includes `path` and `outcome.destination`. The agent plugin never targets its
current checkout.

Terminal `gw prune` and `gw prune --yes` clear stale Git worktree bookkeeping
before assessment, whether or not removal is approved. `gw list` and pruning
assessment modes never write refs, cache files or directories; they return
before stale-record pruning, deletion disclosure and confirmation. The sync
that runs before them when one is due writes remote-tracking refs, the
remote's new tags and the sync cache, and nothing else. Pruning
JSON with `--yes` includes the assessment verdicts and the actual removed,
failed and per-target outcomes in one object with head and sync state.

## Sync cache

Only sync invokes `git fetch`, `git remote set-head`, `gh` or `glab`:
`gw sync`, or the sync another command runs first when one is due.
The default remote is `checkout.defaultRemote` if configured, otherwise the
only remote. Sync fetches every remote `git fetch --all` would, and the
default one always, with `--prune --tags --no-prune-tags`. Other remotes are
outside assessment. Several remotes without a default are all fetched, then
sync fails, lists the remotes and prints
`git config checkout.defaultRemote <name>`.

Sync refuses, before fetching anything, a remote whose `remote.<name>.fetch`
writes outside `refs/remotes/<name>/`, and a default remote whose refspecs
update nothing there. A moved remote tag is a warning and the local tag
stays; any other ref Git refuses fails that remote. A failed fetch of
another remote, or a failed `set-head`, still records the sync and exits 1.

After fetching, sync updates the default remote's HEAD, then queries the
forge for every local branch tip on eight threads. The cache is
`git-worktrees-sync.json` in the Git common directory, shared by worktrees
and replaced atomically. Remote name and URL identify it. A mismatch makes
the cache unusable and the repository never synced. Forge entries are keyed
by branch and source SHA; moved or new branches need sync for forge proof.

| Sync result | Refs time | Forge time | Status | Forge evidence |
| --- | --- | --- | --- | --- |
| Fetch and queries succeed | advances | advances | 0 | matching requests |
| Queries find no request | advances | advances | 0 | records no request |
| Default remote's fetch fails | unchanged | unchanged | failure | previous evidence only |
| Another remote's fetch fails | advances | advances | failure | matching requests |
| A remote tag moved | advances | advances | 0, warning | matching requests |
| Remote HEAD update fails | advances | advances | failure | matching requests |
| A refspec writes outside `refs/remotes/<name>/` | unchanged | unchanged | failure, nothing fetched | previous evidence only |
| Unknown forge host | advances | null | 0 | none |
| Missing or unauthenticated `gh`/`glab` | advances | unchanged | failure | previous evidence only |
| One branch query fails | advances | unchanged | failure | none for that branch |
| No remote | null | null | 0, inferred head | none |
| Multiple remotes without default | unchanged | unchanged | failure, every remote fetched | none |

A remote repository with no refs sync time prints a never-synced warning on
stderr, under `-q` and `--json` too, so a caller acting on the verdicts hears
that no sync stands behind them. The older non-null sync time is stale after
10 minutes, with exactly 10 minutes still fresh. Human output prints age and
suggests `gw sync`. Quiet and JSON output suppress the stale warning and the
inferred head notice; a removal's disclosure still repeats them. Repositories
without remotes, the local case, have neither warning. `gw sync` and `gw version`
print the head line, and a head that does not resolve is named in it. JSON provides the state instead. The 10 minutes
hold whatever `WORKTREES_SYNC_TTL` says, so a longer TTL lets the age
warning print before the next sync is due.

Every command except `gw sync`, `gw version`, help and completion runs sync
first when one is due: the default remote's refs time is null, or at least
`WORKTREES_SYNC_TTL` seconds old. The TTL is 60 when the variable is unset or
empty, takes whole seconds with `0` meaning every command, and anything else
is a usage error at exit 2. The forge time is not read. Without a default
remote nothing syncs first, since sync records nothing there. That sync's
failures and warnings go to stderr as warnings, hidden under `-q` and
`--json`; the command keeps its own stdout and exit status.

`WORKTREES_OFFLINE` with a non-empty value turns every sync off. No command
syncs first, `WORKTREES_SYNC_TTL` goes unread, and `gw sync` refuses at
exit 3 with nothing on stdout, `--json` included. The never-synced, age and
unresolved-head lines then end without `Run: gw sync`.

Commands that read objects in a partial clone need a Git release whose lazy-fetch guard covers patch
prefetch: 2.45.1 or newer, or maintenance series 2.39.4, 2.40.2, 2.41.1,
2.42.2, 2.43.4 or 2.44.1 and later patches in each series. Lower reported
vendor versions are refused even when a vendor may have backported the fix.
Full clones do not have this version restriction. A missing local object
never makes assessment fetch it: a proof that
needs one gives `unknown`, and human output warns once, naming
`git backfill`. Quiet and JSON omit the warning.

## JSON and streams

Every JSON result is an object with `lastSync` and `head`. `lastSync.refs`
and `lastSync.forge` are UTC RFC 3339 timestamps or null. A non-null time
does not mean every branch has evidence. `head.source` is `remote`, `clone`
or `inferred`. `head.ref` is the ref the head resolves to, never a `HEAD`:
the target of a symbolic `refs/remotes/<remote>/HEAD`, the one
remote-tracking branch at a plain one's commit, or `refs/heads/main` or
`refs/heads/master` when inferred. It is null when unresolved.

Worktree and branch assessment put rows in `verdicts`. Both retain
`verdict`, `eligible`, `proof`, `request`, `protections`, `stashes`, `branch`,
`sha`, `why` and `undo`, the shell-quoted `git branch NAME SHA` that restores
the branch; a detached worktree's `undo` is null. Worktree rows add exact
`path`, the grouped ignored paths in `ignoredPaths` and their count in
`ignored`; branch rows add `paths` and `error`. `gw prune --json` also
carries `stale`, the paths of worktree records whose directories are gone, and
`neighbours`, the count of worktrees under the shared root that belong to
another repository. `gw branch status --json` and `gw branch prune --json`
carry `head_sha`, the commit the head ref named when the branches were
assessed, or an empty string when the head does not resolve.
`gw list --json` puts navigation rows in `worktrees`, with `branch`, `path`,
`main`, `here` and `flags`. Do not apply a top-level array filter.

Removal JSON reports actual outcomes. `gw prune` has `removed`, `failed` and
`outcomes`; `gw remove` has `removed`, destination `path` and `outcome`. Each
outcome records `worktree_removed`, `branch_removed`, `retained`, `dropped`,
`skipped`, `failed` and `destination`, along with its path and branch. `gw
branch prune` reports `removed`, `failed`, `verdicts` and `outcomes`.

Stdout carries results and destinations; stderr carries diagnostics, prompts
and disclosures. JSON and list output never contain colour. Colour is decided
per stream. Nonempty `NO_COLOR` or `TERM=dumb` disables it. Quiet preserves
results, including `gw branch create`'s branch/base/SHA line and both `gw
rotate` result tables. Verbose traces subprocesses to stderr; quiet plus
verbose fails before mutation. The [README's output
matrix](../README.md#output-and-json) lists which commands accept each flag.

## Implementation boundaries

`git.py` splits literal command specs at declaration, before substituting
values into argv elements. `$name` substitutes one value; `$*name` expands
an argument list. No value receives shell parsing. The guard checks resolved
argv and refuses destructive Git operations, including unqualified ref
deletions without a full expected SHA.

Assessment uses read-only patch-id input, not synthetic commits.
`verdicts.py` declares no mutating commands. Per-worktree checks use eight
threads; mutations remain serial. `progress.py` renders on capable stderr
terminals only, and stays off under quiet or verbose output.

The command registry owns functions, names, aliases, flags and directory
behavior. Help and completions read it. Shell functions handle directory
changes and fixed-flag shortcuts; they hold no assessment or deletion logic.
The `gw` adapter navigates only for `gw list` and `gw l`; every other call passes
stdout and status through. Completion tests compare shell flags with real CLI help.
Plugin tests extract the actual skill jq filters and apply them to real CLI
JSON, including exact paths and permission-blocked rows.
