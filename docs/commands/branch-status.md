# worktrees branch status

`gw branch status` assesses local branches with no worktree, and the shell
plugin's `gwbs` runs it. It changes no branch or worktree and uses local
refs plus cached forge evidence, which a sync refreshes first when one is
due. A checked-out branch appears in `gw prune --json`, not here.

```console
gw branch status
gw branch status feature old-fix
gw branch status --only remove --json
gwbs --only keep
```

Each row's verdict is `remove` or `keep`, and its `reason` says why:
`merged` for matching cached forge evidence, local ancestry, squash or
content proof, or commits made on it that sit on the assessed head's
first-parent history; `unused` for a tip there with no commit made on it;
`stale` for one whose reflog is gone; `closed` and `open` for a cached
request at the tip; `pushed` and `unpushed` for commits outside the head;
`protected`, `checked-out` and `symbolic` for refs never deleted; and
`unknown` without usable proof. `merged` and `stale` read `remove`.
[The reference](../REFERENCE.md#head-and-evidence) defines the check order
and forge identity checks.

A verdict does not grant permission. `main`, `master`, the head branch,
branch stashes, an `unused` or `closed` reason, and commits only the
branch's reflog holds can block deletion. Rows record these
in `protections`; `eligible` is true only when deletion needs no additional
permission and no protection applies. `gw branch status` rejects `--delete`;
use approved values with [gw branch prune](branch-prune.md).

`--only VERDICT` can be repeated. `--json` prints an object with `head`,
`lastSync`, `head_sha` and `verdicts`. Each row includes `branch`, `sha`,
`verdict`, `reason`, `needs` (every `--delete` value it waits on),
`eligible`, `why`, `proof`, `paths`, `request`, `error`, `stashes`,
`protections` and the shell-quoted `undo` command. No table parsing is needed.
`request` is null when no usable cached request exists.

`-q` suppresses summaries, stale warnings and the head line. The never-synced
notice stays on stderr under `-q` and `--json`; JSON also carries it as a null
`lastSync.refs`. `-v` is unsupported.
Missing names are usage errors. Unknown evidence does not become permission
to delete.
