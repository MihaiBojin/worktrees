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

## 0.2.0 - 2026-09-11

A squash-merged branch goes with its checkout, every removal names the
ignored files it deletes, `gwl` finds every worktree and `gwp` says why it
removed nothing. Every command is also reachable by a short name, output takes
colour, and both shells complete every command.

A branch the probe proved squash-merged is deleted with its worktree. It used
to survive: the verdict came from comparing content, and the delete then went
through `git branch -d`, which reads history and refuses exactly that case, so
`gwp` removed the checkout and left the branch behind with git's refusal
printed under it. The delete is now `git update-ref -d <ref> <sha>` against the
sha the verdict was formed on, so a commit landing between the verdict and the
removal fails it rather than going with it. `git branch -D` stays refused, and
so does `update-ref -d` with no sha, which is the same thing spelled longer.

Every removal names the ignored files it is about to delete, not just the ones
under `--delete-ignored`. `gwr --force` used to print a worktree, remove it,
and say nothing about the `.env` inside it; `git worktree remove` takes the
whole directory whatever flag got it there, so `--delete-ignored` decides
consent and never decides what is deleted.

`gwr` no longer calls a finished branch unfinished. A worktree holding ignored
files was refused with `is not finished: squash-merged, but holds 6 ignored
path(s)`, contradicting itself in one line, and the flag it offered was
`--force`, which keeps a branch whose work already landed and deletes those
files anyway. It now says `is finished` and names `--delete-ignored` alone.

`gwl` offers the main checkout, and never the worktree you are standing in.
In a repository with one linked worktree, standing in it, `gwl` printed
nothing and exited 0: the main checkout was filtered out of the candidates,
leaving one, which the picker took outright and handed back the path you were
already at. `gwl main` now finds the main checkout whatever branch it stands
on, two worktrees and no query go to the other one, and asking for the one
you are in says `already in <branch>` and stays put. `--list` shows every
worktree and marks that one.

`gwp` with nothing removable prints the verdict table. It used to print
`nothing to remove; gws says why`, so the reason each worktree stayed cost a
second command to read.

`gwh` is the help, so nothing needs `gw --help` typed out. It joins `gwa`,
`gwl`, `gwm`, `gwr`, `gws`, `gwp`, `gwnb`, `gwrot` and `gw` on `$PATH`, and
`gw` still takes the same set as subcommands. `gw` has its own entry point
now, so a usage error from it names `gw` rather than `worktrees`.

Output is coloured, and `NO_COLOR` turns it off. The check reads the value
rather than the key, which is the no-color.org rule: `NO_COLOR=` unsets the
request instead of making it. A redirect or a pipe turns colour off as well,
because `cd $(gwa x)` and `jq` read that output and neither wants escapes.

Completion covers every command in fish and in zsh, ten files per shell. The
candidates come from the CLI and never from a shell file: `gw --complete`
prints every subcommand and shorthand, `gwl --complete` and `gwr --complete`
print worktrees, each as a name and a description separated by a tab, which
fish reads directly and zsh splits for `_describe`. A command added to the
table needs no edit in either shell.

`gwl` and `gwr` do not offer the same set. `gwr` cannot remove the main
checkout, so offering it would complete to "no worktree matches"; for `gwl`
it is the one destination always there.

`gwa` says when it leaves something behind. It creates the directories before
git is asked, so a refusal used to strand an empty one silently.

The worktree picker prints destinations in full rather than abbreviating them
to `~/git/...`. The abbreviation was not what got printed and not what you
could paste.

### Choices

A branch is deleted by `update-ref -d <ref> <sha>` rather than by `branch -D`.
Both force the delete; only one names what it expects to find, which turns a
concurrent commit into a failure instead of a loss. The guard reads that last
argument rather than counting the arguments: git takes `""` as "no old value"
and deletes the branch at exit 0, so a rule checking the shape would have
passed the one spelling that matters. `branch -d` was the
original choice and it inverts on the case this tool exists for: it reads
history, a squash merge leaves none, and deferring to it meant the content
probe bought the checkout and never the branch.

Completion flags live in the shell files, because that is the half that rots
without failing: rename a flag and nothing breaks, the candidate just stops
being offered. `test_completions.py` diffs both shells against `--help` in
both directions rather than trusting either copy, and drives every candidate
`gwr` offers back through `gwr` rather than comparing against a second copy
of the filter.

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
ships a shell function of the same name for fish and for zsh. The function
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
