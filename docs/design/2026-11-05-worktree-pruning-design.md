# Worktree pruning and synchronization design

The CLI assesses the branches and worktrees of the current repository, removes the ones it can prove are
finished, and reads remote evidence that only `gw sync` fetches. The agent plugin runs the same commands
a person does and shows the CLI's verdicts.

Status: accepted 2026-11-06.

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
- **Terminal:** standard input is a terminal. A command that would ask a question asks only then.

Every rule applies to the current repository only. Worktrees of other repositories under the same
worktree root are neither assessed nor removed.

## 2. Commands

| Command | Role | Mutates |
| --- | --- | --- |
| `gw [ARGS]` | `gw list [ARGS]` whenever the first argument is not a subcommand (section 16). | No |
| `gw list [QUERY]` | Enter a worktree, or print the ones that match (section 16). | No |
| `gwp` | Print the assessment table. At a terminal, disclose the eligible worktrees, confirm, remove them. | After approval |
| `gwr PATH\|NAME` | Remove one worktree (sections 9 and 10). | Yes |
| `gwbs` | Assess local branches that have no worktree. Rejects `--delete`. | No |
| `gwbd NAME...` | Delete eligible loose branches. | Yes |
| `gw sync`, `gws` | Fetch the default remote and forge evidence. The only command that uses the network. | Refs, cache |

`gwp` asks only at a terminal, and only when neither `--json` nor `--list` was given. Otherwise it
prints the assessment, removes nothing and exits 0. `--yes` removes every eligible worktree without
asking, in every mode, and grants no permission (section 6). `--list` prints the table uncoloured.
`--only VERDICT` narrows the table and the JSON rows; the disclosure still names every target. There
is no `gwp --dry-run`.

`gwp` takes no target and no `--branch`; a single target goes through `gwr`. A checked-out branch
appears only as its worktree, never as a loose branch.

Every command takes `-h` and `--help`. `gw -h` lists every command under each name it answers to,
says that `gw` with no subcommand runs `gw list`, and reports the shell plugin's release. There is
no `gw help`, `gw h` or `gwh`.

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

When the head ref does not resolve, every row is `unknown`, mutating commands refuse, and `gwa`, `gwbc`
and `gwrot` fail. The message suggests `gw sync`, or says `No main or master branch.` when inference
was used.

The commands that read the head ref are `gwp`, `gwr`, `gwbs`, `gwbd`, `gwa`, `gwbc` and `gwrot`.
Without `--json`, they print the head and its source to stderr, for example
`Head: main (inferred; master also exists)`. `-q` does not suppress this line when the head is inferred.

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
4. `keep`: the branch has commits the head ref does not contain.
5. `unknown`: evidence is missing, malformed or does not match. Never treated as `remove`.

Branch and worktree assessment use the same proof. Assessment computes the first-parent commit set of
the assessed head ref once per run. A divergent local counterpart does not extend that set.

## 5. Protections

None of these is ever eligible:

1. The branches `main` and `master`, and the head branch. No command deletes them, with any `--delete`
   value, with or without `--yes`. `gwbd main` refuses and names the rule.
2. The main checkout.
3. The current worktree, for `gwp`. `gwr` handles it in section 9.
4. A locked worktree.
5. A worktree with uncommitted changes to tracked files, or with untracked files.
6. A worktree with an initialized submodule. The refusal says to run `git submodule deinit --all` in it
   and retry.
7. A worktree with ignored files, unless `ignored-files` is permitted.
8. A branch with stashes, or a worktree whose branch has them, unless `stashes` is permitted or
   `gwr --keep-branch` preserves the branch and its stashes.
9. An `empty` branch and its worktree, unless `empty` is permitted or `gwr --keep-branch` preserves
   the branch.

A linked worktree whose branch is protected by rule 1 is not eligible for `gwp`;
`gwr --keep-branch` can remove it and keeps the branch. Detached worktrees get the same checks as
attached ones. A finished branch does not make a protected worktree eligible.

The main checkout and the head branch are not judged; their verdict is `keep`. Every other row keeps
its verdict, and a protection or a missing permission (section 6) only clears `eligible`. In the
assessment table the VERDICT column is the verdict. A row whose verdict allows deletion but which is
not eligible names the protection, or the permission it needs, in its WHY column after the proof.
The summary under the table counts eligible rows.

## 6. Delete permissions

`--delete VALUE` is repeatable and the values combine. An unknown value, or a value the command does not
take, is a usage error before anything changes. `--yes` never adds a permission.

| Value | `gwp`, `gwr` | `gwbd` |
| --- | --- | --- |
| `empty` | Allows an `empty` branch and its worktree | Allows an `empty` branch |
| `ignored-files` | Allows deleting the worktree's ignored paths | Usage error |
| `stashes` | Allows dropping the branch's stashes | Same |
| `all` | All three | `empty` and `stashes` |

A verdict stays `empty` when `--delete empty` is given; only `eligible` changes. `gwbs` does not take
`--delete`, so its `eligible` field reflects verdicts and protections only.

`gwr --keep-branch` always preserves the selected branch, including one proved finished or named
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
   branch's stash SHAs.
2. Print the disclosure to stderr: path, branch, the restore command `git branch NAME SHA`, each stash
   with its restore command, each ignored path that will be deleted, the never-synced line if it
   applies, and the head line if the head was inferred. `--quiet`, `--json` and `--yes` do not
   suppress it.
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

`gwbd` uses the same disclosure, recheck, ref deletion and stash sequence without removing a
worktree. With `--keep-branch`, disclosure identifies the branch and stashes as retained.

Steps 2 to 8 run only when removal can follow: before the prompt, or under `--yes`. `git branch -D`
is never used. An approved `gwp` may run `git worktree prune` for stale bookkeeping. A `gwp` that
removes nothing writes no ref, no cache and no directory.

## 9. Removing the current worktree

1. `gwr` asks a second confirmation naming the path before removing the current worktree. `--yes` skips
   only the first. Without a terminal, `gwr` refuses.
2. Before removal the `gwr` process changes into the main checkout, so its own working directory
   survives. After successful removal the binary prints that path. Only the fish and zsh `gwr`
   functions move the caller's shell, by changing into it.
3. `gwp` never removes the current worktree (section 5).

## 10. Name resolution

| Command | Matching | Several matches, terminal | Several matches, no terminal |
| --- | --- | --- | --- |
| `gwr NAME` | Exact name; an exact path always resolves to one worktree | Picker limited to the exact matches | Refuse and list the paths |
| `gw NAME`, `gw list NAME` | Exact, then substring, then subsequence | Picker over the matches; one match is taken | Print every match's metadata |

An unmatched name fails without changing anything.

## 11. Synchronization and cache

`gw sync` fetches the default remote, runs `git remote set-head <remote> --auto`, and queries the forge
for every local branch tip, including branches checked out in worktrees. Other remotes are not fetched
and play no part in verdicts.

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
| Fetch fails | unchanged | unchanged | nonzero | previous evidence only |
| Host is not a known forge | advances | `null` | 0 | none |
| `gh` or `glab` missing or unauthenticated | advances | unchanged | nonzero | previous evidence only |
| Query fails for a branch | advances | unchanged | nonzero | none for that branch |
| No remote | `null` | `null` | 0 | none; head inferred |
| Several remotes, no `checkout.defaultRemote` | unchanged | unchanged | nonzero | none |

The last row's message lists the remotes and prints `git config checkout.defaultRemote <name>`.
When every branch's query fails, `gw sync` cannot tell that from a CLI that is not signed in, and
takes the `gh` or `glab` row: previous evidence only.
Missing, malformed or mismatched cache entries are never proof.

## 12. Offline rule

No command except `gw sync` runs `git fetch`, `git remote set-head`, `gh` or `glab`. The options
`--no-fetch`, `--no-forge`, `--force-refresh` and their short aliases no longer exist.

## 13. Sync warnings

1. **Never synced:** a repository with a remote whose `refs` time is `null`. Every command that reads
   the head ref (section 3) first prints to stderr
   `Never synced: verdicts use refs from the clone or last fetch and no forge evidence. Run: gw sync`.
   `-q` does not suppress it; the deletion disclosure repeats it.
2. **Stale:** the older applicable sync time is more than 10 minutes old. Exactly 10 minutes is fresh.
   The same commands print one stderr line with the age in minutes, hours or days and suggest
   `gw sync`.
3. Neither appears for `gw sync`, for `gw list`, for `--json` (which carries `lastSync` instead), or
   for a repository without a remote. `-q` suppresses the stale warning.

## 14. JSON

Every `--json` result is an object carrying:

```json
{
  "lastSync": {"refs": "2026-11-05T10:00:00Z", "forge": null},
  "head": {"ref": "refs/remotes/origin/HEAD", "source": "remote"}
}
```

- `lastSync` values are UTC RFC 3339 times of the last successful sync of each component, or `null`
  when none succeeded or the component does not apply. A time does not mean every branch has evidence.
- `head.source` is `remote`, `clone` or `inferred`. `head.ref` is `null` when unresolved.
- Assessment rows carry `verdict`, `eligible` and `stashes` (the matching stash SHAs). `--only empty`
  filters by verdict.
- `gw list --json` is `{"lastSync": ..., "head": ..., "worktrees": [...]}`.
- Other commands keep their fields and add these two.

## 15. Output flags

`--quiet` / `-q` suppresses summaries, progress and the stale warning. It keeps results, errors, the inferred-head
line, the never-synced line and the disclosure. `--verbose` / `-v` traces Git and forge subprocesses to stderr and
leaves JSON on stdout intact. Passing both is a usage error before anything changes.

| Command | `-q` | `-v` |
| --- | --- | --- |
| `gw sync`, `gwp`, `gwr`, `gwbd`, `gwa`, `gwbc`, `gwrot` | yes | yes |
| `gwbs` | yes | no |
| `gw`, `gw list`, `gw NAME` | no | no |

Commands outside this matrix retain their existing output flags. Help, version and completion output
remain usable without repository assessment. Head and sync notices never enter JSON or path stdout.

## 16. Navigation

1. `gw` with no arguments runs `gw list`. When the first argument is not a subcommand or alias,
   `gw ARGS` runs `gw list ARGS`, so the query and every flag go to `gw list`. A subcommand or alias
   takes precedence over a worktree name; `gw list NAME` reaches a worktree whose name collides with
   one. There is no `gwl`, and no `l` or `ls` alias.
2. `gw list QUERY` matches worktrees exactly, then by substring, then by subsequence. At a terminal
   it shows the picker, a query that matches one worktree selects it without asking, and the
   selected path goes to stdout. Without a terminal, or with `--json` or `--list`, it prints the
   metadata of every match and selects nothing: JSON under `--json`, the `--list` table otherwise.
3. A query that matches nothing fails without changing anything. When it is close to a subcommand,
   the message names that subcommand.
4. The fish and zsh `gw` functions pass every subcommand except `list` straight to the binary, so its
   output and exit code reach the caller unchanged. For `gw list` and every form that runs it, they
   capture stdout, change directory when it is a directory, and print it otherwise. Capturing
   anything else would hold `gwp`'s table back until after its prompt and strip its colour.
   `gw add`, `gw move` and `gw remove` print their destination; `gwa`, `gwm` and `gwr` move you.
5. The functions read the subcommand list from `gw --complete`. Worktree completions come from
   `gw list --complete`.
6. `gw` prints its help on stderr, as every command that changes directory does.

## 17. Shortcuts

Defined in fish and zsh:

- `gwp!` runs `gwp --yes "$@"`.
- `gwp!!` runs `gwp --delete all --yes "$@"`, which deletes `empty` branches, ignored files and branch
  stashes. Its help says so.
- `gwr!` runs `gwr --delete ignored-files "$@"`.

## 18. Agent plugin

1. Run `gwp --json` and `gwbs --json`. Show the head line and the sync state first. Never run
   `gw sync` unless the user asks; when the repository is never synced or stale, ask whether to sync
   before going on.
2. List eligible rows. List `empty` rows and rows held back by stashes separately, and offer them only
   when the user asks.
3. Ask before any removal. For each chosen worktree, call the binary with the quoted path from the JSON:
   `command gwr 'PATH' --yes`, adding `--delete` values only for categories the user approved. Then call
   `command gwbd NAME... --yes` for chosen loose branches.
4. The plugin cannot remove the worktree it runs in: `gwr` refuses without a terminal (section 9).
5. Report each removal, refusal and restore command.

## 19. Breaking changes

- Removed: `gwl`, `gwh`, `gw help`, `gw h`, `gw l`, `gw ls`, `gw status`, `gw s`, `gw st`,
  `gwp --branch`, `--delete-ignored`, `--no-fetch`, `--no-forge`, `--force-refresh` and its aliases.
- `gws` runs `gw sync`. The table it printed is `gwp`'s.
- `gw` with no arguments lists worktrees. `gw -h` prints the help it used to print.
- `gwp` without a terminal prints the assessment and exits 0, where it refused at exit 3.
- `gw list` without a terminal prints every match, where it refused or printed a path.
- `gwr --force` is now `gwr --keep-branch`.
- New verdict `empty`; new `--delete` values; `main` and `master` always protected.
- `gw list --json` returns an object instead of an array; every JSON result carries `lastSync` and
  `head`.
- No command except `gw sync` fetches.

Help, examples, fish and zsh completions, shortcut tests, plugin filters and command documentation
change with the code. The CLI and `worktrees/plugin/` ship together. The `origin` skill in
`MihaiBojin/agent-plugins` names `gwl` in its prose and changes there, separately.
