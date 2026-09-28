# worktrees branch delete

`worktrees branch delete [NAME ...]`, `gw branch delete [NAME ...]`, and
`gwbd [NAME ...]` delete local branches proved finished by
[branch status](branch-status.md). They print each branch's full SHA and undo
command before asking for confirmation.

```console
gwbd old-fix finished-feature
gwbd
gwbd --all --dry-run
gwbd old-fix --no-fetch --no-forge --yes --json
```

| Selection | Behaviour |
| --- | --- |
| `NAME ...` | Assess exact local names and offer those proved removable. |
| No names | Show a numbered selector. Enter space-separated numbers, or blank to cancel. |
| `--all` | Assess every local branch and offer the removable set for confirmation. |
| `--dry-run` | Report the assessment and undo commands without deleting. With no names, assess all branches without a selector. |

`--all` and names cannot be combined. An unknown name stops the entire request
before any deletion. The selector shows protected branches without numbers;
only removable branches can be selected. Selection is followed by a separate
confirmation. Blank input, a negative confirmation, or Ctrl-C at either
prompt deletes nothing.

## Confirmation and protection

`--yes` skips deletion confirmation. It does not choose branches or override
a verdict. Without a terminal, supply names or `--all`, and `--yes` to delete.
`--dry-run` needs neither a terminal nor confirmation.

The command checks the head SHA and assesses the selected branch again after
confirmation. A changed branch tip, changed head ref, or newly checked-out
branch prevents deletion. The final operation is
`git update-ref --no-deref -d refs/heads/NAME SHA`: git checks the expected
full SHA and deletes the named ref without following a symbolic ref.

Deletion affects local branch refs. It does not delete remote branches,
worktrees, working files, or stashes. Fetching can refresh or prune
remote-tracking refs; pass `--no-fetch` to use the existing refs.

## Undo

Every proposed deletion prints a command such as:

```console
git branch old-fix 0123456789abcdef0123456789abcdef01234567
```

The command uses the actual assessed SHA and quotes the branch name for the
shell. It recreates the local branch while that commit remains available.
The printed SHA does not protect an unreachable commit from garbage
collection, and the command does not recreate branch tracking configuration.

There is no persistent event log yet. Deletion-event history, host and remote
context, and stored recovery commands are tracked in
[issue #93](https://github.com/MihaiBojin/worktrees/issues/93).

## Output and exit codes

All [branch status flags](branch-status.md#flags-and-output) apply. Add
`--all`, `--dry-run`, or `-y` / `--yes` as needed.

JSON includes the assessment plus `removed`, containing the names actually
deleted, and `failed`, containing each failed branch name and reason. A dry
run also sets `dry_run` to true. Undo commands remain on stderr even with
`--quiet` or `--yes`, and each JSON verdict contains its `undo` command.

| Exit | Meaning |
| --- | --- |
| `0` | Completed, cancelled, or no eligible branches in a scan. |
| `1` | Assessment or deletion encountered an operational failure. Partial results remain in the report. |
| `2` | Invalid arguments or an unknown branch name. |
| `3` | A named branch was refused, a selector answer was invalid, or a required prompt has no terminal. |
| `130` | Interrupted with Ctrl-C. |
