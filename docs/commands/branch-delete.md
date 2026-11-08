# worktrees branch delete

`gw branch delete` deletes eligible loose local branches after confirmation
and a fresh assessment, and the shell plugin's `gwbd` runs it. It uses local
refs and cached forge evidence; only `gw sync` refreshes them. Checked-out
branches cannot be deleted here.

```console
gw branch delete old-fix another-fix
gw branch delete --all --dry-run
gw branch delete old-fix --yes --json
gw branch delete unused --delete empty
gwbd finished-with-stashes --delete stashes
```

Names select those branches; no names opens a selector on a terminal.
`--all` selects all loose branches. `--dry-run` previews without deletion.
[Branch status](branch-status.md) defines the
assessment fields and completion proofs.

## Permission and confirmation

`main`, `master` and the assessed head branch are always protected.
`empty` needs `--delete empty`; matching branch stashes need
`--delete stashes`. Values repeat and combine; `--delete all` grants both.
`ignored-files` is a usage error for this command. `--yes` skips confirmation
and grants no permission. Unknown or unsupported values fail before mutation.

Before confirmation, stderr lists branches and SHAs, their branch restore
commands, and matching stashes with exact restore commands. Quiet and JSON
keep the disclosure. Never-synced repositories ask
`Never synced. Delete anyway?` unless `--yes` skips it.

The recheck pins the branch SHA, head identity and matching stashes. Deletion
uses `git update-ref --no-deref -d REF FULL_SHA`; the full SHA prevents a
concurrent branch update from being deleted. A failed ref deletion retains
its stashes. Stash drops follow successful ref deletion and verify the
recorded SHA against Git's reported result. A concurrent drop of a different
entry restores it with its exact subject, at the top of the stash list.

## Recovery and results

Use the commands printed in the disclosure:

```console
git branch old-fix FULL_SHA
git stash store -m 'On old-fix: saved work' STASH_SHA
```

Branch restore recreates the ref. Stash restore works until Git garbage
collection prunes its unreachable commit. Quote names and subjects exactly
as printed.

Partial success is possible. Read each branch and stash outcome: a refused
branch can remain while another is deleted. JSON includes `removed`,
`failed`, `verdicts`, `outcomes`, `head` and `lastSync`; each verdict keeps
its `undo` command. Exit 0 means the requested operation completed or was
cancelled, exit 1 reports a cleanup failure, exit 2 is a usage error, and
exit 3 is a refusal. Ctrl-C exits 130.

`-q` keeps results, errors and disclosures while suppressing summaries and
stale warnings. `-v` traces subprocesses on stderr without changing JSON
stdout. Passing both is a usage error.
