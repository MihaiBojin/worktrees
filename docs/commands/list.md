# worktrees list and select-main

`gw list [QUERY]` picks a worktree to change into, or prints the worktrees
that match. `gw select-main` changes into the worktree that has the head
branch checked out. Both print a path, and the `gw` shell function changes
into it.

| command | shorthand | shortcut |
| --- | --- | --- |
| `gw list [QUERY]` | `gw l` | `gwl` |
| `gw select-main` | `gw main`, `gw m` | `gwm` |

```console
gw list
gw list parser
gw list --table
gw list --json | jq -r '.worktrees[].path'
cd "$(command gw select-main)"
```

## What is listed

The main checkout and every linked worktree of this repository, the main
checkout first and the rest oldest to newest. Bare entries are left out.
Worktrees of other repositories under the same `.worktrees` directory are
not this repository's and never appear.

The main checkout is always a destination, even from inside it, since from a
subdirectory it is the way back to the top. Its row reads `main` when it
stands on `main`, and `BRANCH (main)` otherwise, so `main` finds it whatever
branch it holds. A linked worktree you stand in is shown with `*` and never
offered: picking it would take you nowhere.

## Matching

A query that is exactly one worktree's name, branch or path matches that
worktree alone, so `gw list one` beside `one-more` is not a question.
Otherwise matching ignores case and runs in two passes:

1. Substring, on the branch and then the path, which holds the worktree's
   name.
2. The query's letters in order, on the branch and the name, never the whole
   path: every path holds `.worktrees`, which hands a `t`, a `w` and an `o`
   to any query.

Every substring match ranks above every in-order match, so typing more of a
name never moves it down.

```
'parse'  -> fix-parser
'tst'    -> add-tests, try-something
'ruff'   -> renovate/ruff-0.x
```

## On a terminal

With stdin a terminal and neither `--json` nor `--table`:

- A query that leaves one destination takes it, with no prompt.
- No query opens the picker, however few worktrees there are.
- A query that leaves several opens the picker filtered by it. One esc
  clears the filter and shows every worktree.
- A query whose only match is the worktree you stand in prints
  `already in NAME` and exits 0.

The picker prints each remote's name and URL above the rows, so you can see
which repository it is. Typing filters the rows with the matching above,
digits included, since branch names carry them. Backspace takes a character
back, up and down move the highlight and wrap, and enter takes the row. Esc
clears the filter, and on an empty filter it cancels, as ctrl-d does. A
cancel prints `nothing picked` and exits 0; ctrl-c exits 130.

## Without a terminal

Nonterminal stdin, or `--table`, prints a table of every match, `*` marking
where you stand, and changes nothing. `--json` puts the matches in
`worktrees`, each with `branch`, `path`, `main`, `here` and `flags`.

| no worktree matches | exit | stdout |
| --- | --- | --- |
| `--json` | 0 | an empty `worktrees` list |
| table or picker | 1 | nothing; `no worktree matches` on stderr |

`gw list` reads only the worktree records, which no fetch changes, so it
never syncs first and prints no word about how fresh the evidence is.

## select-main

`gw select-main` finds the worktrees whose branch is the head branch, as
[the head rules](../REFERENCE.md#head-and-evidence) name it, and prints the
path with or without a terminal, so `cd "$(command gw select-main)"` works
in a script. It is taken from inside that worktree too.

| case | exit | result |
| --- | --- | --- |
| one worktree has the head branch | 0 | its path |
| two have it, which only a forced checkout makes, on a terminal | 0 | the picker asks which |
| two have it, without a terminal | 3 | a refusal that says to use `--json` |
| none has it | 1 | stderr suggests `gw add BRANCH` |
| none has it, under `--json` | 0 | an empty `worktrees` list |
| the head ref does not resolve | 3 | the reason on stderr |

Unlike `gw list`, it syncs first when a sync is due.
