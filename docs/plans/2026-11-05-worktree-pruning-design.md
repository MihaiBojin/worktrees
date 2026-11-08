# Worktree pruning and synchronization design

The CLI assesses the branches and worktrees of the current repository, removes the ones it can prove are
finished, and reads remote evidence that only `gw sync` fetches. The agent plugin runs the same commands
a person does and shows the CLI's verdicts.

Status: implemented in #123 to #130 and revised through #175. Open follow-ups: #164 and #165.

## 1. Terms

- **Default remote:** `checkout.defaultRemote` if set, otherwise the only remote. A repository with
  several remotes and no `checkout.defaultRemote` has no default remote.
- **Head ref:** the branch everything is measured against (section 3).
- **Local counterpart:** `refs/heads/<name>`, where `<name>` is the branch the head ref points to.
- **Verdict:** what the branch evidence proves: `remove`, `empty`, `keep` or `unknown` (section 4).
- **Protection:** a property that blocks deletion whatever the verdict (section 5).
- **Permission:** a `--delete` value that allows deleting something a verdict alone does not (section 6).
- **Eligible:** the verdict allows deletion, no protection applies, and every required permission was
  given.
- **Current worktree:** the worktree containing the caller's working directory.

Every rule applies to the current repository only. Worktrees of other repositories under the same
worktree root are neither assessed nor removed.

## 2. Commands

| Command | Role | Mutates |
| --- | --- | --- |
| `gw` | Print the command guide, as `gw --help` does. | No |
| `gw list [QUERY] [flags]` | Terminal navigation; metadata for all matches without a terminal or with `--json` or `--table`. | No |
| `gw prune` with nonterminal input, `--json`, `--table` or `--only`, without `--yes` | Print the assessment table or JSON, preserving stale records and repository state. | No |
| `gw prune` with terminal input | Clear stale worktree records, show the full table, disclose eligible targets, confirm, then remove. | Stale records first; removal after approval |
| `gw prune --yes` | Remove eligible targets without prompting, including with nonterminal input, `--json` or `--table`. Grants no deletion category. | Yes |
| `gw remove PATH\|NAME` with nonterminal input, `--json` or `--table`, without `--yes` | Print the match's assessment row or JSON, as `gw prune` does. No `--only`: one target has one verdict. | No |
| `gw remove PATH\|NAME` with terminal input or `--yes` | Remove one worktree (sections 9 and 10). | Yes |
| `gw branch status` | Assess local branches that have no worktree. Rejects `--delete`. | No |
| `gw branch prune NAME...` | Delete eligible loose branches. | Yes |
| `gw sync` | Fetch every remote, then the default remote's head and forge evidence (section 11). The only command that uses the network. | Refs, cache |

`gw` is the only console script, and `worktrees` is its long name. Every command is a `gw`
subcommand; the shell plugin's shortcuts (section 17) are other spellings of them.

`gw prune` takes no target and no `--branch`; a single target goes through `gw remove`. A
checked-out branch appears only as its worktree, never as a loose branch. `gw --help` and bare `gw`
show help. `gw branch prune NAME` naming a checked-out branch, the current one included, shows it as
kept with the protection `checked out in a worktree` and exits 3.

`--table` prints an uncoloured table. `--only VERDICT` always selects assessment
and rejects `--yes` before repository preflight. Pruning accepts `--quiet` and
`--verbose` in every mode, but rejects their combination. There is no pruning
`--dry-run`; `gw branch prune --dry-run` remains available. Mutation JSON includes actual
assessment verdicts, removal outcomes, head and sync metadata in one object.

## 3. Head ref

The head ref is the first of these that applies:

1. `gw sync` has succeeded with a default remote: `refs/remotes/<remote>/HEAD`, which `gw sync` updates
   with `git remote set-head <remote> --auto`.
2. No successful sync, but `refs/remotes/<default remote>/HEAD` exists, for example from `git clone`:
   that ref. The never-synced banner applies (section 13).
3. Otherwise, inferred from local branches on every run:
   1. Only one of `main` and `master` exists: that branch.
   2. Both exist: the one whose tip has the newer committer date; `main` on a tie.
   3. Neither exists: `main`, which does not resolve.

When the head ref does not resolve, every row is `unknown`, mutating commands refuse, and `gw add`,
`gw branch create` and `gw rotate` fail. The message suggests `gw sync`, or says `No main or master branch.` when inference
was used.

Human-readable commands print the head and its source to stderr, for example
`Head: main (inferred; master also exists)`. `gw sync` prints it after syncing, and `gw version` prints
it when run inside a repository. A head that does not resolve is named in the line, as
`Head: unresolved (remote). Run: gw sync`, rather than left blank. `-q` suppresses it.

## 4. Verdicts

Checked in this order; the first match wins:

1. `remove`: the sync cache holds a merged request whose source SHA equals the branch tip, whose target
   matches the head ref, and whose merge commit is an ancestor of the head ref.
2. `empty`: a branch whose tip is on the first-parent history of the assessed head ref. This covers
   a branch created at an older commit of the main line and a branch merged by fast-forward without
   forge evidence. Git cannot tell the two apart.
3. `remove`: completion proved locally by ancestry, squash or content against the head ref. A branch
   merged through a merge commit is an ancestor without being on the first-parent history, so it lands
   here.
4. `unknown`: the cache holds a merged request for the branch that fails the checks in item 1, and no
   local proof applies. A failed check is evidence that does not match, so the branch is not `keep`.
5. `keep`: the branch has commits the head ref does not contain.
6. `unknown`: evidence is missing, malformed or does not match. Never treated as `remove`.

Branch and worktree assessment use the same proof. Assessment computes the first-parent commit set of
the assessed head ref once per run. A divergent local counterpart does not extend that set.

## 5. Protections

None of these is ever eligible:

1. The branches `main` and `master`, and the head branch. No command deletes them, with any `--delete`
   value, with or without `--yes`. `gw branch prune main` refuses and names the rule.
2. The main checkout.
3. The current worktree, for `gw prune`. `gw remove` handles it in section 9.
4. A locked worktree. One whose directory is missing is assessed from its record and kept.
5. A worktree with uncommitted changes to tracked files, or with untracked files.
6. A worktree with an initialized submodule, or with the submodule store git keeps for it
   (`git rev-parse --git-path modules`). `git worktree remove` refuses while either exists. The
   refusal names both steps: `git submodule deinit --all` in the worktree, then deleting that
   directory once nothing in it is needed.
7. A worktree with ignored files, unless `ignored-files` is permitted.
8. A branch with stashes, or a worktree whose branch has them, unless `stashes` is permitted or
   `gw remove --keep-branch` preserves the branch and its stashes.
9. An `empty` branch and its worktree, unless `empty` is permitted or `gw remove --keep-branch`
   preserves the branch.

A linked worktree whose branch is protected by rule 1 is not eligible for `gw prune`;
`gw remove --keep-branch` can remove it and keeps the branch. A detached worktree has no branch to keep
its commits, so `--keep-branch` does not waive its completion proof. Detached worktrees get the same checks as
attached ones. A finished branch does not make a protected worktree eligible.

## 6. Delete permissions

`--delete VALUE` is repeatable and the values combine. An unknown value, or a value the command does not
take, is a usage error before anything changes. `--yes` never adds a permission.

| Value | `gw prune`, `gw remove` | `gw branch prune` |
| --- | --- | --- |
| `empty` | Allows an `empty` branch and its worktree | Allows an `empty` branch |
| `ignored-files` | Allows deleting the worktree's ignored paths | Usage error |
| `stashes` | Allows dropping the branch's stashes | Same |
| `all` | All three | `empty` and `stashes` |

A verdict stays `empty` when `--delete empty` is given; only `eligible` changes. `gw branch status` does not take
`--delete`, so its `eligible` field reflects verdicts and protections only.

`gw remove --keep-branch` always preserves the selected branch, including one proved finished or named
`main` or `master`. It can remove an otherwise unprotected worktree without completion proof. It
grants no ignored-file permission. Stashes remain intact and do not block worktree-only removal.
The only `--delete` value `--keep-branch` accepts is `ignored-files`. `empty`, `stashes` and `all`
concern the branch, so combining any of them with `--keep-branch` is a usage error before confirmation
or mutation, regardless of argument order.

## 7. Branch stashes

1. A stash belongs to branch B when its subject starts `WIP on B:` or `On B:` and its first parent is an
   ancestor of B's tip. Stashes made on a detached HEAD belong to no branch.
2. With `stashes` permitted, removal drops the branch's stashes after the branch ref is deleted.
3. Git drops stashes by index, and the stash list is shared by every worktree, so an index is not an
   identity. Each drop runs as:
   1. Read `git stash list --format='%gd %H %gs'` and find the index of the recorded SHA. If it is
      gone, skip it.
   2. Run `git stash drop stash@{N}`. Git prints `Dropped stash@{N} (SHA)`.
   3. If the printed SHA is the recorded one, the drop succeeded.
   4. If it differs, another process changed the list. Put the dropped entry back with
      `git stash store -m 'SUBJECT' SHA`, taking the subject from step 1's listing, or from
      `git log -1 --format=%s SHA` when the entry was not listed. Report the stash as not dropped.
   Processing highest indexes first keeps the remaining indexes valid. A repaired entry returns at the
   top of the list; no stash is lost.
4. A stash restores with `git stash store -m 'SUBJECT' SHA` until `git gc` prunes its unreachable
   commit.

## 8. Removal sequence

For each target:

1. At assessment, record the worktree path, branch ref and SHA, head ref and SHA, ignored paths and the
   branch's stash SHAs. Ignored paths are what `git status --porcelain --ignored=matching` lists: the
   path an ignore pattern names, so a directory the pattern matches is one entry and a directory no
   pattern names lists its ignored files. The record is a path listing, not a content fingerprint: a
   file created after assessment inside a listed ignored directory is not detected and is deleted
   with it.
2. Print the disclosure to stderr: path, branch, the restore command `git branch NAME SHA`, each stash
   with its restore command, each ignored path that will be deleted, the never-synced line if it
   applies, and the head line if the head was inferred. `--quiet`, `--json` and `--yes` do not
   suppress it. An ignored path is shown cut to its first component below the `.gitignore` whose
   pattern matched it, or below the top level for `.git/info/exclude` and `core.excludesFile`; the
   protection's count and the JSON `ignored` field count those entries.
3. Ask for confirmation unless `--yes` was given. A never-synced repository asks
   `Never synced. Delete anyway?`.
4. Assess the target again. Refuse if the path, branch SHA, head ref or head SHA, cleanliness, lock
   state, ignored paths or matching stashes changed, or if the target is no longer eligible.
5. Run `git worktree remove PATH`, never with `--force`. Plain `git worktree remove` deletes ignored
   files without asking, so step 4 is their only protection. If Git refuses, report it and skip the rest
   of this target.
6. For an attached worktree without `--keep-branch`, run `git update-ref --no-deref -d REF SHA`,
   so the branch is deleted only if it still points to the assessed SHA. Skip ref deletion for a
   detached worktree or with `--keep-branch`.
7. Only after successful ref deletion, drop permitted stashes (section 7). A stash that disappeared
   since step 4 is skipped. A failed ref deletion preserves its stashes.
8. Report the worktree, branch and stash outcomes separately. A failed step is reported as failed.

`gw branch prune` uses the same disclosure, recheck, ref deletion and stash sequence without
removing a worktree. With `--keep-branch`, disclosure identifies the branch and stashes as retained.

`git branch -D` is never used. Terminal `gw prune` and `gw prune --yes` run `git worktree prune` for stale
bookkeeping before assessment, whether or not the user approves a removal. The preview modes never do,
and assessment never writes refs, the cache or any directory.

## 9. Removing the current worktree

1. `gw remove` asks a second confirmation naming the path before removing the current worktree.
   `--yes` skips only the first. Without a terminal, `gw remove` refuses.
2. Before removal the process changes into the main checkout, so its own working directory survives.
   After the checkout is removed, `gw` prints that path. Only the fish and zsh `gw` function moves the
   caller's shell, by changing into it, and it lands there even when a later cleanup step failed,
   keeping that failure's status.
3. `gw prune` never removes the current worktree (section 5).

## 10. Name resolution

| Command | Matching | Several matches, terminal | Several matches, no terminal |
| --- | --- | --- | --- |
| `gw remove NAME` | Exact name; an exact path always resolves to one worktree | Picker limited to the exact matches | With `--yes`, refuse and list the paths; without, print every match's row |
| `gw list [QUERY]` | Exact, then substring, then subsequence matching | Picker filtered as you type; a unique query selects directly | Metadata for every match |

An unmatched name fails without changing anything.

## 11. Synchronization and cache

`gw sync` fetches every remote `git fetch --all` would, the default remote first and always, each with
`git fetch --prune --tags --no-prune-tags`. It then runs `git remote set-head <remote> --auto` for the
default remote and queries the forge for every local branch tip, including branches checked out in
worktrees, on eight threads. Only the default remote plays a part in verdicts.

Fetching prunes remote-tracking refs and never local tags, whatever `fetch.pruneTags` says. A tag that
moved on the remote stays at its local commit: Git refuses to move it, and sync warns, names it and
prints `git fetch --tags --force <remote>`. Any other ref Git refuses fails that remote's fetch.

Before fetching anything, sync refuses a remote whose `remote.<name>.fetch` writes outside
`refs/remotes/<name>/`. `git clone --mirror` sets `+refs/*:refs/*`, and with `--prune` that deletes
every local branch the remote lacks, including one checked out in a linked worktree, at exit 0. It
also refuses a default remote whose refspecs update nothing in `refs/remotes/<name>/`, as `git clone
--bare` leaves it. The refusal prints the `git config` command that fixes the refspec.

The cache is one file in the Git common directory, shared by every worktree and replaced atomically. It
records the default remote's name and URL; if either changes, the cache is ignored and the repository
reports as never synced. Forge evidence is queried against the default remote's repository and keyed by
branch and source SHA. Proof also verifies the source repository and target repository/branch; a
fork request qualifies only when its source identity is verified. A branch created or moved after
the last sync has no forge evidence until the next one; local proof still applies.

| Situation | `refs` time | `forge` time | Exit | Forge proof |
| --- | --- | --- | --- | --- |
| Fetch and queries succeed | advances | advances | 0 | yes |
| Query finds no request | advances | advances | 0 | none, recorded as "no request" |
| Default remote's fetch fails | unchanged | unchanged | nonzero | previous evidence only |
| Another remote's fetch fails | advances | advances | nonzero | yes |
| A remote tag moved | advances | advances | 0, with a warning | yes |
| `set-head` fails | advances | advances | nonzero | yes |
| A refspec writes outside `refs/remotes/<name>/` | unchanged | unchanged | nonzero | previous evidence only; nothing fetched |
| Host is not a known forge | advances | `null` | 0 | none |
| `gh` or `glab` missing or unauthenticated | advances | unchanged | nonzero | previous evidence only |
| Query fails for a branch | advances | unchanged | nonzero | none for that branch |
| No remote | `null` | `null` | 0 | none; head inferred |
| Several remotes, no `checkout.defaultRemote` | unchanged | unchanged | nonzero | none; every remote fetched |

With no remote, sync prints `No remote to sync.` With several remotes and no default, it fetches them all,
then lists them and prints `git config checkout.defaultRemote <name>`.
Missing, malformed or mismatched cache entries are never proof.

## 12. Offline rule

No command except `gw sync` runs `git fetch`, `git remote set-head`, `gh` or `glab`. The options
`--no-fetch`, `--no-forge`, `--force-refresh` and their short aliases no longer exist.

A partial clone never fetches a missing object on an offline command's behalf. A proof that needs
one gives `unknown`, and human output warns once that `git backfill` fetches what is missing. `-q`
and `--json` omit the warning.

## 13. Sync warnings

1. **Never synced:** a repository with a remote whose `refs` time is `null`. Every command that
   assesses first prints to stderr
   `Never synced: verdicts use refs from the clone or last fetch and no forge evidence. Run: gw sync`,
   with `-q` and with `--json` too, since a caller acting on the verdicts needs to know no sync
   stands behind them. The deletion disclosure repeats it.
2. **Stale:** the older applicable sync time is more than 10 minutes old. Exactly 10 minutes is fresh.
   Human-readable commands print one stderr line with the age in minutes, hours or days and suggest
   `gw sync`. `-q` and `--json` (which carries `lastSync`) omit it.
3. Neither appears for `gw sync` or for a repository without a remote, the local case.

## 14. JSON

Every `--json` result is an object carrying:

```json
{
  "lastSync": {"refs": "2026-11-05T10:00:00Z", "forge": null},
  "head": {"ref": "refs/remotes/origin/main", "source": "remote"}
}
```

- `lastSync` values are UTC RFC 3339 times of the last successful sync of each component, or `null`
  when none succeeded or the component does not apply. A time does not mean every branch has evidence.
- `head.source` is `remote`, `clone` or `inferred`. `head.ref` is the ref the head resolves to: the
  target of a symbolic remote HEAD, the one remote-tracking branch at a plain one's commit, or
  `refs/heads/main` or `refs/heads/master` when inferred. It is `null` when unresolved.
- Assessment rows carry `verdict`, `eligible` and `stashes` (the matching stash SHAs). `--only empty`
  filters by verdict. Worktree rows carry `ignoredPaths`, the grouped ignored paths (section 8), and
  `ignored`, their count, before any permission is given.
- `gw list --json` is `{"lastSync": ..., "head": ..., "worktrees": [...]}`.
- Other commands keep their fields and add these two.

## 15. Output flags

`--quiet` / `-q` suppresses summaries, progress and every warning and notice, the head line
included. It keeps results, errors, the never-synced line, the disclosure and the confirmation prompt. `--verbose` / `-v` traces Git and forge subprocesses to stderr and
leaves JSON on stdout intact. Passing both is a usage error before anything changes.

| Command | `-q` | `-v` |
| --- | --- | --- |
| `gw sync`, `gw prune`, `gw remove`, `gw branch prune`, `gw add`, `gw branch create`, `gw rotate` | yes | yes |
| `gw branch status` | yes | no |
| `gw list` | no | no |

Commands outside this matrix retain their existing output flags. Help, version and completion output
remain usable without repository assessment. Head and sync notices never enter JSON or path stdout.

## 16. Navigation

1. Bare `gw` prints the command guide. A first word that is neither a command
   nor an alias is a usage error at exit 2 suggesting `gw list WORD`, so a
   worktree name never collides with a command or a shorthand.
2. On terminal input, `gw list` opens the picker with the current-worktree
   protections. Typing filters the rows, up and down move, enter takes the
   highlighted row and esc cancels. A unique query selects directly. Main
   remains offered when it is the only worktree: no query prompts with that
   row, and an exact query selects it directly, including from a
   subdirectory. Without a terminal, or with `--json` or `--table`, it prints
   all matching metadata and never selects a destination.
3. The fish and zsh `gw` function changes directory for a terminal `gw list`
   or `gw l`, for `gw add` and for `gw remove`. With `--json`, `--table`,
   `--help`, `--complete` or `--explain` it prints the data and keeps the
   status, and every other subcommand passes straight through. Completions
   read subcommands from `gw --complete` and worktrees from
   `gw list --complete`, or `gw remove --complete` for removal.
4. `gw l` is an alias of `gw list`, and `gwl` is its shortcut (section 17).
   `gwh`, `gw ls`, `gw help` and `gw h` are absent. Every command supports
   `-h`/`--help`. Root help prints every command with its shortcuts indented
   beneath it, then the shell plugin's release line. `gw list -h` prints list
   flags. Help works outside a repository without assessment or network calls.

## 17. Shortcuts

A shortcut is a one-line fish and zsh function that runs `gw` with fixed words and the caller's
arguments, so it completes as that command does. Each command's row in `cli.py` lists its shortcuts,
and `gw --help` and the command's own `--help` print them.

| Shortcut | Runs |
| --- | --- |
| `gws` | `gw sync` |
| `gwp` | `gw prune` |
| `gwp!` | `gw prune --yes` |
| `gwpa` | `gw prune --delete all`, which permits deleting `empty` branches, ignored files and branch stashes, and still asks at a terminal |
| `gwpa!` | `gw prune --delete all --yes` |
| `gwa` | `gw add` |
| `gwl` | `gw list` |
| `gwr` | `gw remove` |
| `gwr!` | `gw remove --yes`, the approval `gwp!` gives, and no deletion category |
| `gwrot` | `gw rotate` |
| `gwbc` | `gw branch create` |
| `gwbs` | `gw branch status` |
| `gwbp` | `gw branch prune` |

## 18. Agent plugin

1. Run `gw prune --json` and `gw branch status --json`. Show the head line and the sync state first. Never run
   `gw sync` unless the user asks; when the repository is never synced or stale, ask whether to sync
   before going on.
2. List eligible rows. List `empty` rows and rows held back by stashes separately, and offer them only
   when the user asks.
3. Ask before any removal. For each chosen worktree, call the binary with the quoted path from the JSON:
   `command gw remove 'PATH' --yes`, adding `--delete` values only for categories the user approved.
   Then call `command gw branch prune NAME... --yes` for chosen loose branches. `command` skips the
   shell's `gw` function, which would change directory.
4. The plugin cannot remove the worktree it runs in: `gw remove` refuses without a terminal
   (section 9).
5. Report each removal, refusal and restore command.

The skill pre-approves `gw sync`, `gw list --json`, `gw list --table`, `gw prune --json`,
`gw branch status --json`, `jq` and `git remote`. `/prune` also pre-approves `command gw remove` and
`command gw branch prune`, which it runs only after the user approves. No pattern in either covers
`gw prune --yes`.

## 19. Breaking changes

- Removed: the `gwa`, `gwp`, `gwr`, `gwbs`, `gwbd`, `gwbc` and `gwrot` console scripts, `gwh`, `gwm`
  and `gw move`/`m`/`mv`, list alias `ls`, `gw help`, `gw h`, `gw status`, `gw s`, `gw st`,
  `gwp --branch`, `--delete-ignored`, `--no-fetch`, `--no-forge`, `--force-refresh` and its aliases.
- Shortcuts are shell plugin functions that run `gw` (section 17). `gwl` and `gw l` reach
  `gw list`.
- `gwp!!` is now `gwpa!`.
- `gw branch delete` and `gwbd` are now `gw branch prune` and `gwbp`.
- `gwr --force` is now `gw remove --keep-branch`.
- `gw remove` without a terminal, or with `--json` or `--table`, assesses at exit 0 unless `--yes`
  approves removal, where it refused at exit 3. `gwr!` runs `gw remove --yes`, not
  `gw remove --delete ignored-files`.
- `--list` is now `--table`, with no short form.
- `gws` runs `gw sync`, which fetches, instead of assessing.
- New verdict `empty`; new `--delete` values; `main` and `master` always protected.
- `gw list --json` returns an object with a `worktrees` array; every JSON result carries `lastSync` and `head`.
- No command except `gw sync` fetches.

Help, examples, fish and zsh completions, shortcut tests, plugin filters and command documentation
change with the code. `MihaiBojin/agent-plugins` at `8390c6d` has five Origin
help prose references to `gwl`. `gwl` is the `gw list` shortcut, so they work
wherever the shell plugin is installed. It has no runtime invocation or
metadata consumer to migrate.
The checked release-tools plugin repository has no active affected references.
The CLI and `worktrees/plugin/` ship together.
