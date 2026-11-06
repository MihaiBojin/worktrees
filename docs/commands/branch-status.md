# worktrees branch status

`gwbs` assesses local branches with no worktree. It changes nothing and uses
local refs plus cached forge evidence. Run `gw sync` explicitly to refresh
that evidence. A checked-out branch appears in `gwp --dry-run`, not here.

```console
gwbs
gwbs feature old-fix
gwbs --only empty --json
worktrees branch status --json
```

`remove` means matching cached forge evidence or local ancestry, squash or
content proof. `empty` means the tip is on the assessed head's first-parent
history; this includes unused branches and fast-forward merges without
forge proof. `keep` has commits outside the head, while `unknown` lacks
usable proof. [The reference](../REFERENCE.md#head-and-evidence) defines the
check order and forge identity checks.

A verdict does not grant permission. `main`, `master`, the head branch,
branch stashes and an empty verdict can block deletion. Rows record these
in `protections`; `eligible` is true only when deletion needs no additional
permission and no protection applies. `gwbs` rejects `--delete`; use approved
values with [gwbd](branch-delete.md).

`--only VERDICT` can be repeated. `--json` prints an object with `head`,
`lastSync`, `head_sha` and `verdicts`. Each row includes `branch`, `sha`,
`verdict`, `eligible`, `why`, `proof`, `paths`, `request`, `error`, `stashes`,
`protections` and the shell-quoted `undo` command. No table parsing is needed.
`request` is null when no usable cached request exists.

`-q` suppresses summaries and stale warnings. Inferred-head and never-synced
notices remain; JSON carries those states instead. `-v` is unsupported.
Missing names are usage errors. Unknown evidence does not become permission
to delete.
