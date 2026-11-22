# Reference

Where each command is described, then the rules the commands share: the
head ref, protections, deletion, the audit log, the sync cache and the
output streams. [The README](../README.md) covers installation and a tour.

## Commands

| command | shortcuts | described in |
| --- | --- | --- |
| `gw`, `gw --help` | | [below](#commands) |
| `gw sync` | `gws` | [README](../README.md#head-and-synchronization), [sync cache](#sync-cache) |
| `gw prune`, `gw p` | `gwp`, `gwp!`, `gwpa`, `gwpa!` | [README](../README.md#assess-and-remove), [protections](#protections-and-permissions), [deletion](#deletion-and-recovery) |
| `gw add`, `gw a` | `gwa` | [add](commands/add.md) |
| `gw list`, `gw l` | `gwl` | [list](commands/list.md) |
| `gw select-main`, `gw main`, `gw m` | `gwm` | [select-main](commands/list.md#select-main) |
| `gw remove`, `gw rm` | `gwr`, `gwr!` | [README](../README.md#assess-and-remove), [protections](#protections-and-permissions), [deletion](#deletion-and-recovery) |
| `gw fast-forward`, `gw ff` | `gwff` | [fast-forward](commands/fast-forward.md) |
| `gw push` | `gwpu` | [push](commands/push.md) |
| `gw ci-checks`, `gw ci` | | [ci-checks](commands/ci-checks.md) |
| `gw merge` | | [merge](commands/merge.md) |
| `gw stack-link` | | [stack-link](commands/stack-link.md) |
| `gw preflight` | | [preflight](commands/preflight.md) |
| `gw version`, `gw v` | | [below](#commands) |
| `gw branch create` | `gwbc` | [branch create](commands/branch-create.md) |
| `gw branch status` | `gwbs` | [branch status](commands/branch-status.md) |
| `gw branch prune` | `gwbp` | [branch prune](commands/branch-prune.md) |

Bare `gw` and `gw --help` print every command with its shorthands and the
shell plugin's shortcuts under it, then the plugin's release, outside a
repository too. `gw WORD`, with a word that is not a command, exits 2 and
suggests `gw list WORD`, so a worktree name never competes with a
shorthand. Each shortcut runs one command with fixed words.

`gw version` prints the installed release on stdout, alone, for a script to
read. On stderr it says whether the shell plugin here is the same release,
and inside a repository it prints the head line.

`gw prune` assesses worktrees and asks before removal on a terminal.
Nonterminal input, `--json` and `--table` assess without mutation;
`--yes` approves removal in any mode. `--table` prints an uncoloured table.
`--only` filters assessment rows and rejects `--yes`. `gw remove` selects one
and follows the same rules for it: nonterminal input, `--json` and `--table`
print its row, in the `verdicts` shape `gw prune --json` uses, and remove
nothing; `--yes` approves removal in any mode. It takes no `--only`, since a
single target has a single verdict. A target that is not eligible still fails
under `--yes`, and removing the worktree you stand in still needs a second
answer at a terminal.

## Head and evidence

The head ref comes from the selected remote's HEAD after successful sync,
then the clone's remote HEAD, then local inference. Local inference chooses
the only `main` or `master`, the newer committer date when both exist, and
`main` on a tie. With neither, the inferred `main` does not resolve.
A remote HEAD that is a plain ref rather than a symbolic one takes its
branch name from the one remote-tracking branch at its commit, and does
not resolve when several sit there.
Unresolved head means the `unknown` reason and no mutation. `gw add`,
`gw branch create` and `gw fast-forward` also fail in that state.

A row's verdict is `remove` or `keep`, and its `reason` is the first fact
that applies. Checkout facts come first: `main-checkout`, `current`,
`locked`, `missing`, `submodule`, `in-progress` (a rebase, merge,
cherry-pick, revert or bisect waiting in the worktree's git directory) and
`uncommitted`, then `protected` for `main`, `master` or the head branch.
Each of these reads `keep` unless `--keep-branch` makes a protected branch's
checkout eligible for removal. The branch's proof stays in `proof`. Without
a checkout or protection fact, the completion checks run in this order:

1. Matching cached forge proof gives `merged`.
2. A tip on the assessed head's first-parent history reads its branch's
   reflog: a commit made on the branch gives `merged`; another entry, or
   `core.logAllRefUpdates=false`, gives `unused`; no entry gives `stale`.
3. Local ancestry, squash or content proof gives `merged`.
4. Absent or invalid evidence gives `unknown`.
5. A cached request at the tip gives `closed` or `open`.
6. Commits a remote-tracking ref holds give `pushed`, others `unpushed`.

`merged` and `stale` read `remove`. Any row made eligible by `--delete` or
`--keep-branch` also reads `remove`; its reason stays unchanged. Every other
row reads `keep`. Tables, JSON, counts and `--only` use this verdict.

Removal deletes the branch's reflog, and the worktree's own HEAD reflog.
Assessment lists the commits those reach and no ref, worktree HEAD or kept
reflog does, from `git rev-list COMMITS --not --all`, and keeps each whose
own patch-id the head lacks since its merge base. Under `--keep-branch` only
the worktree's reflog goes. Those commits hold the row back with a
protection naming `git show SHA` for each, and `--delete reflog` lifts it.

The first-parent set is computed once per assessment. A divergent local
counterpart does not extend it. Local content proof compares every changed
path from merge base to branch tip with the head tree, including modes and
deletions. A stacked branch can retain an intermediate state the head later
edited; content alone cannot prove that stack finished.

Forge proof requires a merged request for the exact source tip, verified
source repository, target repository and head branch, plus a merge commit
contained in the head ref. A request whose recorded target is another
branch, as a stacked request's stays after it lands, counts when its merge
commit is on the head ref's first-parent history. A closed unmerged request
proves nothing. Fork
requests need verified source identity. Missing or malformed evidence never
permits removal.

## Protections and permissions

`eligible` combines completion evidence, deletion permissions and protections.
`main`, `master` and the assessed head branch cannot be deleted. The main
checkout, locked worktrees, tracked changes, untracked files and initialized
submodules block worktree deletion. The submodule refusal names
`git submodule deinit --all`. A submodule store that `deinit` leaves in the
worktree's git directory also blocks deletion, and the refusal names it. A
locked worktree whose directory is missing is assessed from its record and
kept. Detached worktrees receive the same checks.
`gw prune` also protects the current worktree.

Ignored paths are deleted with the worktree and listed before confirmation.
Branch stashes require `--delete stashes`, an `unused` row `--delete empty`,
a `closed` row `--delete closed`, and reflog-only commits `--delete reflog`.
Repeated values combine, and `all` expands to every one of them.
`gw branch status` rejects `--delete`. Unsupported combinations fail at
usage parsing before mutation. `--yes` grants neither permission. A
protection these values lift ends with `pass --delete VALUE`, repeated for
every value its row needs, only when those values make the row eligible. A
preview's `--yes` line carries the `--delete` values and `--keep-branch`
passed.

`gw remove --keep-branch` retains the branch and stashes and needs no completion
proof for the checkout. A detached checkout has no branch to keep its
commits and still needs proof. It permits a checkout on a protected branch, while
all other worktree protections remain. Only `--delete reflog` can be
combined with it. For a target it keeps, `gw remove` names in full the
`--delete` command that makes it eligible, carrying the values passed;
otherwise the `--keep-branch` command, when that does. A target held by
uncommitted changes or untracked paths, and by no lock, submodule or
operation in progress, also gets `git worktree remove --force PATH`, with a
count of the ignored paths and reflog-only commits it deletes and the branch
it keeps.

## Deletion and recovery

Assessment pins the target path, branch ref and full SHA, assessed head ref
and SHA, ignored path list, and matching stash SHAs and subjects. Disclosure
prints one line for each thing deleted or kept, led by what happens to it:
`delete worktree PATH`, `delete ignored PATH, unrecoverable` for each ignored
path, sorted by name, `delete reflog SHA SUBJECT` with its recovery command,
`delete branch NAME at SHA` and `delete stash SHA` with their restore
commands, or `keep branch NAME at SHA` under `--keep-branch`. An ignored path is shown as the
pattern that matched it names it, from the top level, with a trailing `/` on
a directory, whichever ignore file holds the pattern: `*.log` shows
`build/out/a.log` and `build/out/b.log`, and `__pycache__` shows
`src/pkg/__pycache__/`. A pattern whose last part is a bare `*` or `**`
names a directory's contents, so `path/to/ignored/*`, or `*` in
`cache/.gitignore`, shows that directory once; at the top level each match
shows as itself. A path with a character that would not print is quoted and
escaped as `git status` does. After the list, the disclosure prints
`git -C PATH ls-files --others --ignored --exclude-standard`, with the path
quoted to paste, which lists every ignored file the entries cover.
The JSON `ignored` field counts those entries.
Quiet, JSON and automatic confirmation preserve that stderr disclosure. The
question counts each kind deleted, `delete 1 worktree and 1 branch?`, and a
never-synced repository asks `Never synced. Delete 1 worktree and 1 branch
anyway?`, unless `--yes` skips confirmation.

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
assessment modes never write refs or directories; they return
before stale-record pruning, deletion disclosure and confirmation. An
assessment saves the proofs it found in `git-worktrees-proofs.json` in the
Git common directory, so the next one does not ask git for them again. Each
is a fact about a branch's commit and the head's, both named by full SHA, so
it has no age: the file holds one head commit's proofs and starts again when
the head moves. A partial clone saves only a yes, since a fetch can bring the
object a no was missing. What a checkout holds, `git status` and the rest, is
read on every run and never saved. The sync
that runs before them when one is due writes remote-tracking refs, the
remote's new tags and the sync cache, and nothing else. Pruning
JSON with `--yes` includes the assessment verdicts and the actual removed,
failed and per-target outcomes in one object with head and sync state.

## Audit log

Every command appends what it did to
`audit-YYYY-MM.jsonl`, by UTC month, in `$XDG_STATE_HOME/worktrees`, which
is `~/.local/state/worktrees` when that is unset. `WORKTREES_AUDIT_DIR`
names another directory. One file holds every repository; each event names
the directory it ran in. Help, `--explain` and completion record nothing, on
a subcommand as at the top: `gw prune --help`, `gw prune --explain` and
`gw list --complete --json` write no line, and each still answers when
`WORKTREES_AUDIT` holds a value it does not take. One of those words after
`--` is a value, and that command is recorded. A usage error is recorded,
at exit 2. Every run that writes an `invocation.start` writes its
`invocation.end`.

A line is one JSON object, written by one `write` to a descriptor opened
with `O_APPEND`. Nothing seeks, truncates, renames or deletes a file, so
commands running side by side interleave whole lines and an earlier line is
never changed. The directory is created `0700` and each file `0600`. No file
is ever removed: retention is yours, by month.

A `write` that comes back short, on a full disk or at a file size limit,
leaves a fragment that cannot be taken back. The rest of that line is never
written; the fragment is closed with a newline when one fits, the command
warns as for any failed write, and it records nothing more. So that no later
line is lost to a fragment left open, every append takes an advisory lock on
the file, looks at its last byte and, when that is not a newline, begins
with one and marks its line `torn: true`. The look and the write are under
one lock, so this holds for a command that had the file open before another
tore a line, as for one that starts after. The lock is held for one write
and waited on for a tenth of a second at most. Where it cannot be had, on a
filesystem without `flock` or past that wait, the line begins with a newline
whatever the file ends in, which leaves an empty line. So a reader skips a
line that is empty or is not JSON: a fragment is the one event lost, and
every line after it is whole.

### Events

The field names, types and values below are the contract for `v` 1, and
`tests/audit_schema.py` holds every event the tests read to them: no field
that is not listed, none missing, each of its type. A field is always
present unless its row says it can be absent. A string that does not apply
is empty, not absent, and null is used only where a row says so. Counts and
times are whole numbers, zero or more.

Every event carries:

| field | type | meaning |
| --- | --- | --- |
| `v` | number | 1 |
| `ts` | string | UTC, `YYYY-MM-DDThh:mm:ss.mmmZ` |
| `run` | string | 16 hex digits, shared by one invocation's events |
| `seq` | number | counted from 1 within the run |
| `event` | string | one of the eight below |
| `user`, `host` | string | who ran it and where |
| `pid` | number | the `gw` process |
| `version` | string | the `gw` release |
| `cwd` | string | where the process stood, empty if that directory is gone |
| `torn` | `true`, or absent | this line closes a fragment left by a lost event |

`invocation.start`, when a command begins:

| field | type | meaning |
| --- | --- | --- |
| `argv` | list of strings | the command as typed, one element per word |
| `records` | list of strings | which of `reads`, `mutating` and `forge` are on |
| `terminal` | boolean | stdin is a terminal |

`exec`, when a `git`, `gh` or `glab` process ended:

| field | type | meaning |
| --- | --- | --- |
| `tool` | string | `git`, `gh` or `glab` |
| `argv` | list of strings | one element per argument, never joined or split |
| `code` | number or null | exit code; null when it never started or timed out |
| `ok` | boolean | the code is one its caller takes as an answer |
| `ms` | number | how long it ran |
| `mutates` | boolean | it changes something |
| `out`, `err` | string, or absent | the first 4000 characters it printed; present only when it changes something or failed, and printed something |
| `outDropped`, `errDropped` | number, or absent | characters left off after those; present exactly when the text is, and 0 when it is whole |

An `exec` whose `ok` is true exited with a code its caller takes as an
answer, which for some reads includes 1 or 128. A `git fetch` counts as
changing something, since it moves and prunes remote-tracking refs, and so
does a forge call whose `--method` is `PUT`, `POST`, `PATCH` or `DELETE`.

`invocation.end`, when the command ends:

| field | type | meaning |
| --- | --- | --- |
| `code` | number or null | exit code; null after an exception nothing caught |
| `ms` | number | from entry to exit |
| `failure` | string or null | null at exit 0, else how it ended, below |
| `error` | string, or absent | the message of a refusal or failure, first 4000 characters |
| `errorDropped` | number, or absent | characters left off; present exactly when `error` is |

`failure` is `usage` for a usage error, `refused` for a refusal, `git` for
a git command that failed, `interrupted` for ctrl-c, `crash` for an
exception nothing caught, and `result` when the command ran to its end and
its answer is the exit code, as after a stopped rebase or a partial removal. A
refusal a command prints itself is `refused` like any other: a name already
checked out, no worktree matching, a target kept, a fetch that failed. A
missing argument is `usage`.

`removal.proposed`, when a removal is disclosed and before any answer, and
`removal.outcome`, when an approved removal was tried, share:

| field | type | meaning |
| --- | --- | --- |
| `branch` | string | empty for a detached worktree |
| `sha` | string | the full commit the branch or worktree was on |
| `path` | string | the worktree, empty for a loose branch |
| `reason`, `proof`, `evidence` | string | the verdict's reason, its proof and what the proof found |
| `request` | object or null | the forge request the assessment used, as its JSON gives it |
| `permissions` | list of strings | the `--delete` values granted |
| `repository` | object | `commonDir`, `remote` and `url`, each a string |
| `head` | object or null | `branch` and `sha` of the head assessed against; null when refused before assessment |
| `notices` | list of strings | the sync and head notices in force |
| `keepBranch` | boolean | `--keep-branch` was given |
| `restore` | list of objects | the recovery actions, below |
| `undo` | list of strings | each action's `command`, in order |
| `recovery` | string | what can and cannot come back |

`removal.proposed` adds `deletes`, an object: `worktree` and `branch`,
strings, empty for what stays, and `stashes`, `reflog` and `ignored`, lists
of full SHAs, full SHAs and paths. `removal.outcome` adds:

| field | type | meaning |
| --- | --- | --- |
| `result` | string | `completed`, `partial` when a step failed after another took effect, `refused` when nothing was removed |
| `worktreeRemoved`, `branchRemoved` | boolean | each step on its own |
| `stashesDropped`, `stashesRetained`, `stashesAbsent` | list of strings | full SHAs |
| `ignoredDeleted` | list of strings | ignored paths that went with the worktree |
| `failed` | list of strings | each failure's message |
| `failedStage` | string or null | null when nothing failed, else `recheck`, `worktree`, `branch` or `stashes`: the step that stopped it |

A `removal.proposed` with no `removal.outcome` in the same run was declined
or refused before it started.

`fast-forward.outcome`, when `gw fast-forward` decided, carries the fields
of [its `--json` result](commands/fast-forward.md), under the names and
types that has: `result`, `branch`, `base`, `sha`, `previous`,
`previous_sha`, `proof`, `why`, `boundary`, `adopted`, `stopped_at`,
`subject`, `stash`, `pushed` and `unpushed`, strings; `ahead`, `behind` and
`carried`, numbers; `paths`, a list of strings; `stashes`, a list of
objects; and `retargeted` and `rescued`, objects. It adds
`previous_full_sha` and `full_sha`, strings that are empty when the short
one no longer resolves, and `restore` and `undo`.

`push.outcome`, when `gw push` returned after its fetch, carries the
fields of [its `--json` result](commands/push.md): `branch`, `remote`,
`pushed`, `previous` and `refused`, strings. It adds `replace`, true when
`--replace` was given.

`merge.outcome`, one for each merge call `gw merge` made or refused, carries
`number`, a number, `head`, the SHA it was asked to merge at, and the fields
of [its `--json` result](commands/merge.md): `status` and `message`,
strings, `sha` and `uuid`, strings or null, `pr`, an object, and
`warnings`, a list of strings. `merged` lists each pull request the call
merged, as `number` and `sha`, its merge commit or null. `remaining` lists
each one still open, as `number`, `headSha`, `baseRef`, `mergeStateStatus`
and `checksState`. `branches` lists each local branch of the stack above
the merge that gw looked at, as `number`, `branch`, `from`, `to`, `path`
and `result`: `moved`, `checked-out`, `kept` or `not-rewritten`.

### Recovery actions

`restore` holds one object for each thing the event took, or would take,
so a program reads recovery as fields and parses no shell line. Each has:

| field | type | meaning |
| --- | --- | --- |
| `kind` | string | `branch`, `stash`, `reflog-commit` or `previous-tip` |
| `sha` | string | the full commit to restore |
| `argv` | list of strings | the command, one element per word, nothing quoted |
| `command` | string | the same words shell-quoted, for pasting |

and the fields its `kind` needs, each a string and absent for another kind:

| `kind` | restores | fields |
| --- | --- | --- |
| `branch` | a deleted branch | `branch` |
| `stash` | a dropped stash | `subject` |
| `reflog-commit` | a commit only a deleted reflog reached | `subject`, and `branch`, the `rescue-SHORT` name it is given |
| `previous-tip` | where a branch stood before `gw fast-forward` moved it | `of`, that branch, and `branch`, the `rescue-SHORT` name |

`undo` is every `command`, in the same order. A proposal lists only what
would go: a branch kept by `--keep-branch` keeps its stashes, so neither
appears. An outcome lists only what went, so a retained branch has no
action. The commands work until Git garbage collection prunes the commit:
recording a SHA protects no object. Ignored and untracked files have no
action, and `ignoredDeleted` lists the first. A stash subject is the one
git kept, in which each run of spaces is one.

### Settings

`WORKTREES_AUDIT` chooses what is recorded. Unset, empty or `all` records
everything, and `off` records nothing. Otherwise it is a comma-separated
list of `reads=on|off`, `mutating=on|off` and `forge=on|off`, which file
each `exec`: a read-only git command, a git command that changes something,
and a `gh` or `glab` call. A category not named stays on, so `reads=off`
leaves the invocation, every change and every outcome. Any other value is a
usage error at exit 2.

The user and password inside a URL are removed from every string in an
event as it is serialized, at any depth: an argument, an output, a message,
a stash or commit subject, a `restore` action. `git@host:path` is kept as it
is. A restore command for a stash whose subject held a credential therefore
stores it under the subject without one. A command's arguments are
otherwise recorded as typed, and so is a path.

A log that cannot be written is one warning on stderr, under `--quiet` and
`--json` too, and the command goes on unrecorded. Nothing reaches stdout.

The agent plugin's skills run `gh pr create` and `glab mr merge`
themselves, so those are not in the log; every `gw` command a skill runs
is, `gw push` among them.

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

Every command except `gw sync`, `gw version`, `gw list`, `gw fast-forward`,
`gw ci-checks`, `gw merge`, `gw stack-link`, `gw preflight`, help and completion runs sync first when one is due: the default remote's
refs time is null, or at least `WORKTREES_SYNC_TTL` seconds old. The TTL is 300 when the variable is unset or
empty, takes whole seconds with `0` meaning every command, and anything else
is a usage error at exit 2. The forge time is not read. Without a default
remote nothing syncs first, since sync records nothing there. That sync's
failures and warnings go to stderr as warnings, hidden under `-q` and
`--json`; the command keeps its own stdout and exit status. `gw fast-forward`
fetches the default remote instead when one is due, asks the forge for the
branch's newest merged request in every run, and fetches again when that
answer names a merge the head ref lacks. A failed fetch or forge query is a
refusal whose last line is the same command under `WORKTREES_OFFLINE=1`.

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
`verdict`, `reason`, `needs` (every `--delete` value the row waits on, empty
when none frees it), `eligible`, `proof`, `request`, `protections`,
`stashes`, `branch`,
`sha`, `why` and `undo`, the shell-quoted `git branch NAME SHA` that restores
the branch; a detached worktree's `undo` is null. Worktree rows add exact
`path`, the ignored paths the disclosure lists in `ignoredPaths` and their count in
`ignored`; branch rows add `paths` and `error`. `gw prune --json` also
carries `stale`, the paths of worktree records whose directories are gone, and
`neighbours`, the count of worktrees under the shared root that belong to
another repository. `gw branch status --json` and `gw branch prune --json`
carry `head_sha`, the commit the head ref named when the branches were
assessed, or an empty string when the head does not resolve.
`gw list --json` and `gw select-main --json` put navigation rows in
`worktrees`, with `branch`, `path`, `main`, `here` and `flags`. Do not apply
a top-level array filter.

Removal JSON reports actual outcomes. `gw prune` has `removed`, `failed` and
`outcomes`; `gw remove` has `removed`, destination `path` and `outcome`. Each
outcome records `worktree_removed`, `branch_removed`, `retained`, `dropped`,
`skipped`, `failed` and `destination`, along with its path and branch. `gw
branch prune` reports `removed`, `failed`, `verdicts` and `outcomes`.

`gw ci-checks --json` reports a fresh forge snapshot. `pr` has
`number`, `url`, `state`, `draft`, `headSha`, `headRef`, `baseRef`,
`repository`, `source`, `mergeable`, `mergeStateStatus` and `trailers`.
`trailers` is an ordered list of unique commit trailers, or null when the
complete list is unavailable. [CI checks](commands/ci-checks.md) describes
its limits. `checksState` is `passed`, `failed`, `pending`, `blocked` or
`unknown`, and `stale: true`
forces `unknown`. Each of `checks` has `name`, `workflow`, `state`, `bucket`
and `link`. `pipeline` is GitLab's head pipeline with its `association`:
`head`, `merge-result` or `stale`. It is null on GitHub.

Stdout carries results and destinations; stderr carries diagnostics, prompts
and disclosures. A `[y/N]` question takes one key and no enter: `y` is yes,
and any other key, enter and esc among them, is no. JSON and list output never contain colour. Colour is decided
per stream. Nonempty `NO_COLOR` or `TERM=dumb` disables it. Quiet preserves
results, including `gw branch create`'s branch/base/SHA line and `gw
fast-forward`'s result table. Verbose traces subprocesses to stderr; quiet plus
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
The `gw` adapter navigates only for `gw add`, `gw remove`, `gw select-main` and
a terminal `gw list`; every other call passes stdout and status through.
Completion tests compare shell flags with real CLI help.
Plugin tests extract the actual skill jq filters and apply them to real CLI
JSON, including exact paths and permission-blocked rows.
