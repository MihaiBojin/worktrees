---
name: fast-forward
description: >
  Fetch, then fast-forward the checkout that holds the head branch (main or
  master, usually the main checkout) to the remote's tip, refusing when that
  would drop a local commit. Use at the start of a task, after a pull
  request merges, or when the user asks to update, pull or fast-forward main.
allowed-tools:
  - Bash(gw sync)
  - Bash(gw list --json)
  - Bash(jq:*)
---

# Fast-forward the head branch

`gw` comes from `uv tool install git-worktrees`; if it is missing, report
that and stop.

## Fetch

```sh
gw sync
```

It fetches every remote and records the default remote's head branch. A
failure on the default remote is the answer: report it and stop, since
there is nothing newer to move to.

## Find the checkout

```sh
gw list --json | jq -c '(.head.ref // "" | sub("^refs/remotes/[^/]+/|^refs/heads/"; "")) as $b
  | {head, branch: $b, path: [.worktrees[] | select($b != "" and .branch == $b) | .path][0]}'
```

`head.ref` is the branch the head resolves to, such as
`refs/remotes/origin/main`, and `branch` is its name. `head.ref: null` means
the head branch does not resolve; report that and stop.

`path` is the checkout that holds the head branch, usually the main
checkout. A null `path` means no checkout holds it, and there is nothing to
move: new branches start from the remote's tip, which `gw sync` just
fetched. Say so and stop.

## Move it

Only in that `path`. In a checkout on any other branch `gw rotate` starts
a new branch instead, which is not what was asked.

```sh
(cd 'PATH' && command gw rotate --json)
```

Quote `PATH` exactly as the JSON gives it. Then report one of:

- exit 0: the branch moved from `previous_sha` to `sha`.
- exit 1, "already at": it was up to date.
- exit 1, "ahead": the checkout holds commits the remote does not. List
  them from the message. They are a change of their own; nothing here
  moves or drops them.
- anything else: the message, as git gave it. A dirty file that the
  fast-forward would overwrite stops it, and the file stays as it was.
