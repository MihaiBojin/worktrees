# Changelog

Newest first. Each entry says what changed for somebody running these
commands, and the choices behind it. Dates are ISO 8601, versions follow
[Semantic Versioning](https://semver.org/spec/v2.0.0.html), and the shape
follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

An entry earns its place by being observable. If running a command produces
no different result, no different output and no different exit code, it is
not in here, whatever it cost to build.

A release closes with a `### Choices` section when a decision in it is worth
the reader's time: what was chosen, and what the alternative failed to do.

## 0.1.0 - 2026-09-11

The first release. Eight commands for git worktrees, four of which answer a
question and print it, and four of which land you somewhere.

`gws` says which of a repository's worktrees are finished and why, in three
verdicts. `remove` was proved finished, `keep` has a reason not to be, and
`unknown` could not tell. It removes nothing and has no flag that could.

`gwp` removes the ones `gws` marks `remove`, under the same flags, printing
what goes and the command that puts it back before asking. `-y` skips the
question, and a run whose stdin is not a terminal refuses rather than
blocking.

Two failures in git are why those two exist. A squash merge makes
`git branch -d` report "not fully merged" and point at `-D`, which deletes
anything; the probe replays the branch's tree as one commit on the merge
base and asks `git cherry` whether that patch is already upstream. And
`git worktree remove` deletes `.env` and `node_modules/` at exit 0 with no
`--force` and no word about it, so ignored paths are counted and the
worktree is kept until you pass `--delete-ignored`.

Where content cannot tell, the forge is asked. A branch merged as part of a
stack is that case: its changes reach the head branch across several
squashes, so a diff cannot separate it from a branch with work left. A
merged pull request settles it, cross-checked against the commits an
upstream has not got. `--no-forge` decides from git alone.

`gwa NAME` creates a worktree at `<PARENT>/.worktrees/<NAME>/<REPO>` and
prints where it is. `gwl [QUERY]` picks one of the existing ones, taking a
single match outright and asking when there are several. `gwm NEW` renames
this worktree's branch and moves the checkout to match. `gwr` removes one
whose branch is finished, and the branch with it.

Those four change your shell's directory, which a binary cannot do, so each
ships a shell function of the same name for fish, zsh and bash. The function
reads the path the binary printed and does the `cd`, and carries nothing
else.

`gwnb NAME` starts a branch off the head branch as the remote has it after a
fetch. `gwrot` starts the next branch in a series, named
`<stem>-YYYY-MM-DD_NNN` at the first number free today. **Both are alpha and
may go**: they start branches rather than worktrees, `origin new-branch` and
`origin rotate` already do that, and only one of the two sets survives.

Six git commands are refused wherever they appear and whatever flags are
passed: `reset --hard`, a forced `checkout` or `switch`, `clean -f`, a bare
`push --force`, `worktree remove --force`, and `branch -D`. A branch is
deleted on git's own proof of merge or not at all.

Every command takes `--json`, so an agent reads verdicts as data rather than
parsing a table. Data goes to stdout and every diagnostic to stderr, the
prompts included, so `--json` parses in every mode. `--version` reports the
version of the installed distribution.

Python 3.11 or newer, and no dependencies.
