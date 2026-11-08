# Changelog

Newest first. Each entry says what changed for somebody running these
commands, and the choices behind it. Dates are ISO 8601, released versions
follow [Semantic Versioning](https://semver.org/spec/v2.0.0.html), and the
shape follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

Every version below was tagged and published. The snapshots `main` sends to
TestPyPI are neither, and they are numbered `<version>.post<epoch>`, which
PEP 440 orders after the release it follows and SemVer does not describe at
all. `docs/DEVELOPMENT.md` says how and why.

An entry earns its place by being observable. If running a command produces
no different result, no different output and no different exit code, it is
not in here, whatever it cost to build.

A release closes with a `### Choices` section when a decision in it is worth
the reader's time: what was chosen, and what the alternative failed to do.

## 0.8.0 - 2026-11-08

### Changed

`gw` and `worktrees` are the only commands `uv tool install` puts on
`$PATH`. `gwa`, `gwl`, `gwp`, `gwr`, `gwbs`, `gwbc` and `gwrot` are fish
and zsh plugin functions that run `gw add`, `gw list`, `gw prune`,
`gw remove`, `gw branch status`, `gw branch create` and `gw rotate`, and
complete as those do. `gwbp` replaces `gwbd` and runs `gw branch prune`.
A script or an agent that ran one of them as a binary, such as
`gwp --json`, needs the `gw` form instead: `gw prune --json`. Upgrade the
shell plugin to this release with the CLI, because the previous plugin's
shortcuts call binaries this release no longer installs.

`gw sync` fetches every remote and all their tags, updates the default
remote's HEAD, and asks the forge about every local branch, eight at a
time, with a line per branch in progress. `gws` runs it through the shell
plugin. In 0.7.0 `gws` assessed worktrees without changing anything;
`gw prune --json` does that now.

Every command except `gw sync`, `gw version`, help and completion now
runs `gw sync` first when the last sync is 60 seconds old or more, or
has never run: it fetches every remote and asks GitHub or GitLab about
each local branch. `WORKTREES_SYNC_TTL` sets that window in whole
seconds, and `0` syncs before every command. A failed sync prints a
warning, hidden under `-q` and `--json`, and the command carries on with
the evidence it had.

`WORKTREES_OFFLINE`, set to any non-empty value, keeps every command off
the network. `gw sync` then refuses at exit 3, and the never-synced, age
and unresolved-head notices stop suggesting it. A script or agent that
relied on assessment never touching the network should set it.

`gw prune` prints the full assessment, protected worktrees included, and
asks before removing anything on a terminal. With `--json`, `--table` or
nonterminal input and no `--yes`, it only assesses. It takes no target:
remove one worktree with `gw remove`.

Deleting needs permission per category. `--delete empty` allows an empty
branch, `--delete stashes` dropping a branch's stashes, `--delete
ignored-files` deleting a worktree's gitignored files in place of
`--delete-ignored`, and `--delete all` every category. `--yes` grants
none of them. `gwp!` runs `gw prune --yes`. `gwpa` runs
`gw prune --delete all` and still asks at a terminal, and `gwpa!` adds
`--yes`.

Assessment separates what the evidence proves from what may be deleted.
Each row carries a `verdict`, which can now be `empty` for a branch whose
tip is on the main line, and an `eligible` field. Protected worktrees and
branches stay ineligible even when their changes are upstream, and `main`,
`master` and the head branch are never deleted. `gw branch status` lists
only branches without a worktree.

`gw remove` matches exact names and paths. `gw remove --keep-branch`
replaces `--force`: it removes the checkout and keeps the branch and its
stashes. Removing the checkout you are standing in asks a second time,
even with `--yes`.

`gw branch prune` replaces `gw branch delete`. Scripts calling the old
name must use the new one.

`--table` replaces `--list` on `gw list` and `gw prune`. It has no short
form, so `gw list -l` is a usage error.

Running `gw` with no command prints every command with its shortcuts
indented beneath it, and the shell plugin's release. A first word that is
not a command is a usage error that suggests `gw list WORD`. Every command
takes `-h` and `--help`, and a command's help ends with its shortcuts. `gw add`
and `gw remove` print their help on stderr, so `gw add -h | less` needs
`2>&1`. Without their argument, `gw add` and `gw branch create` say which
one is missing and print their help, at exit 2.

`-q` and `-v` are set per command: `gw branch status` takes `-q` only,
`gw list` takes neither, and passing both is a usage error before any
repository work. `-q` hides every warning but one: a repository with a
remote that `gw sync` has never fetched prints
`Never synced: ... Run: gw sync` on stderr, with `-q` and with `--json`
too.

Every JSON result is an object carrying `lastSync` and `head`.
`head.ref` names the branch the verdicts are measured against, such as
`refs/remotes/origin/main`, or `refs/heads/main` for an inferred head, and
is `null` when the head does not resolve. `gw list --json` puts its rows
under `worktrees`.

Partial clones need Git 2.45.1 or a supported maintenance release. The
CLI refuses older ones before reading any object, and a verdict that
needs a missing object is `unknown`, with a warning, rather than fetched.

### Removed

`--no-fetch`, `--no-forge` and `--force-refresh`: `WORKTREES_OFFLINE`
keeps every command off the network, and `gw sync` refreshes the evidence
at once. `gw prune --branch` and `gw prune --dry-run`: use `gw remove` for
one worktree, and `gw prune --json` or `gw prune --table` to assess.
`gw branch prune --dry-run` remains.

`gwm`, `gw move`, `gw m` and `gw mv`: use `git branch --move` and
`git worktree move`. `gwh`, `gw help`, `gw h`, `gw ls`, `gw status`,
`gw s` and `gw st`.

### Added

Typing in the `gw list` and `gw remove` pickers filters the rows, matching
the way `gw list QUERY` does. Backspace removes a character, Escape clears
the filter, and Escape on an empty filter cancels. Digits are part of the
filter, since worktree names carry them, so a digit no longer picks a row:
type to narrow the list, then press Enter. `gw list QUERY` with several
matches opens the picker filtered by the query, and one Escape widens it
to every worktree. A query whose letters appear in order in a worktree's
directory name now finds it. `gw l` is `gw list`.

`gw add` and `gw remove` change your directory through the `gw` shell
function, as a terminal `gw list` does, so `gw a NAME` lands you in the
new worktree.

`gw sync` refuses a remote whose fetch refspec writes outside
`refs/remotes/<name>/`, such as the one `git clone --mirror` sets, because
fetching with it deletes local branches. A tag that moved on the remote
keeps its local commit, with a warning that names it.

`gw prune --json` worktree rows carry `ignoredPaths`: the ignored paths
the worktree holds, each cut to its first component below the
`.gitignore` that ignores it, so `build/out/a.log` and `build/out/b.log`
read as `build/`. The removal disclosure lists the same entries, and the
"it holds N ignored path(s)" protection counts them.

Worktree rows in `gw prune --json` carry `undo`, the `git branch NAME SHA`
that restores the branch. It is null on a detached worktree.

Removal prints the commands that restore each branch and stash, and checks
the approved state again before changing anything. `gw sync` and
`gw version` name the head branch that verdicts are measured against.

`gw ci` reads a pull request's checks, or a GitLab merge request's, from
the forge: the current branch's, or the one `gw ci NUMBER` names. It prints
the checks with failures first, or with `--json` the review, every check
with a link to its log, and one `checksState` of `passed`, `failed`,
`pending`, `blocked` or `unknown`. `--wait` reads every 30 seconds until no
check is running, for up to 30 minutes. It needs `gh` or `glab` signed in.

The agent plugin adds five skills. `new-branch` starts a branch from the
freshly fetched head, `fast-forward` brings the checkout holding the head
branch up to the remote, `ci` reads checks through `gw ci`, `describe-pr`
corrects a pull request's title and description against its diff, and
`merge` fixes failing checks, merges once CI is green and reports what it
fixed. The cleanup skill is now named `prune` and still runs as
`/worktrees:prune`.

The prune skill shows the head and sync state first, asks before syncing
stale or never-synced evidence, and removes only the worktrees and
deletion categories you approve, by their exact paths. It runs
`gw sync`, `gw list --json`, `gw list --table`, `gw prune --json`,
`gw branch status`, `jq` and `git remote` without asking for permission.

### Fixed

A worktree holding a leftover submodule store, a locked worktree whose
directory is missing, and a detached worktree under
`gw remove --keep-branch` are kept rather than removed.

After removing the checkout you stand in, the `gw` shell function lands
you in the main checkout even when a later cleanup step fails, and keeps
that failure's status. It keeps newlines in paths, and `--json` output
never changes the caller's directory.

On a terminal narrower than the picker's rows, moving through the list
stacked a fresh copy of it under the last. Rows are now cut to the
terminal's width, and a list taller than the screen scrolls with the
highlight.

A failed `git remote set-head` no longer discards a successful sync.
After repairing a concurrent stash drop, cleanup reports stashes that
something else removed as already absent. A file name holding a newline
or an escape character is quoted in the removal disclosure. Assessment
handles non-UTF-8 patches, keeps unrelated histories ineligible, and keeps
its protections for worktree paths ending in whitespace and for branch
names with Unicode separators.

## 0.7.0 - 2026-11-05

### Added

An agent plugin for Claude Code and Codex, in `plugin/`. `/worktrees:prune`
runs `gws` and `gwbs`, shows the worktrees and branches they proved
finished with the proof for each, asks which to remove, and removes those
with `gwp` and `gwbd`, then lists what was removed, what was refused and
the commands that restore each branch. Install it from the `release-tools`
marketplace as `worktrees@release-tools`.

## 0.6.2 - 2026-11-04

### Added

`gwp!` runs `gwp --force-refresh --delete-ignored --yes` in fish and zsh:
it fetches fresh, counts worktrees holding gitignored files as removable,
and removes everything `gws` marks removable without asking. Arguments
after it pass through, so `gwp! --branch NAME` works. Update the fish or
zsh plugin to install this shortcut.

## 0.6.1 - 2026-11-02

### Added

`gwh` says which release the shell plugin in this shell came from, and whether
it matches the commands. When the plugin is older, it names the tag to pin it
to, and when newer, it gives the `uv tool upgrade git-worktrees` to run.
`gw version` says the same on stderr, and its stdout is still the version
alone. Both plugins export `WORKTREES_PLUGIN_VERSION` for this. A shell whose
plugin predates this release reports none.

### Changed

The README installs the plugins at the release's tag, `fisher install
MihaiBojin/worktrees@v0.6.1` for fish and `branch:v0.6.1` for Antidote, and
says how to move each to another release. Moving an existing install onto a
tag takes an edit to `fish_plugins` and `fisher update`, or `antidote purge
MihaiBojin/worktrees` before editing the tag.

## 0.6.0 - 2026-11-02

### Changed

`gwnb` is now `gwbc`, and `gw new-branch` is now `gw branch create`. Update
the fish or zsh plugin so the new name completes. `gwbc` and `gwrot` are no
longer marked alpha.

`gwrot` prints the branch it left and the one it is on now, each with its
commit, and exits 1 when a rotation or catch-up would change nothing.

On a terminal, every command colours its output the same way: errors red
behind ✗, warnings behind !, notes behind →, branches bold magenta, refs
blue, commits yellow, paths dim, and each verdict in one colour wherever it
appears. Piped or redirected output, and any run with `NO_COLOR` set, stays
plain.

`gwl`, `gws`, `gwp` and `gwr` list the main checkout first, then the other
worktrees from oldest to newest.

`-v` prints each git and `gh` command after it runs, with what it printed and
its exit code indented beneath it.

`gwp` names the worktrees it removed.

### Added

`gws` and `gwbs` take `--only VERDICT`, repeatable, to show only the rows
marked `remove`, `keep` or `unknown`, in the table and in `--json`. The counts
under the table still cover every row. Both also take `-f` for
`--force-refresh`.

`gwrot -f` is short for `gwrot --force-refresh`.

`gwbd` shows each branch's verdict in its selector and in the list it prints
before asking, and a `--dry-run` ends by saying how many branches would go.

`gwbs` and `gwbd` show a spinner on stderr while they judge, with a line
naming the branch, the check in progress and how far through the list they
are, such as `fix-parser: was it squash-merged into main? (4 of 18)`. `gwbd`
keeps it running while it rechecks each branch before deleting it. It stays
off when stderr is not a terminal, on `TERM=dumb`, and under `-q` or `-v`.

`gwl` names each remote above the picker as host and path, such as
`origin: github.com/org/repo`, for SSH and HTTPS remotes alike and without
any user name or token the URL carries.

### Fixed

`gwbd` says why it kept each branch it was asked to delete, rather than
exiting 3 with nothing printed.

## 0.5.1 - 2026-09-29

### Added

The wheel and source distribution on PyPI carry PEP 740 attestations signed
by the GitHub workflow that built them. PyPI shows the provenance on the
release's page, and `uvx pypi-attestations verify pypi --repository
https://github.com/MihaiBojin/worktrees pypi:<file>` checks a file against
this repository before you install it.

## 0.5.0 - 2026-09-29

### Added

Local branches can be assessed with `worktrees branch status` (`gwbs`)
and deleted with `worktrees branch delete` (`gwbd`). Deletion accepts
exact names, offers a selector when no names are supplied, and supports
`--all` and `--dry-run`. A branch qualifies through Git history, matching
changed paths, or a merged GitHub PR or GitLab MR tied to its exact tip.
The command protects checked-out branches, rechecks refs before deletion,
and prints the command to restore each branch.

`gwr!` runs `gwr --delete-ignored` in fish and zsh, permitting removal of
ignored files while retaining the command's checks and directory change.
Update the fish or zsh plugin to install this shortcut.

### Changed

`gwl` and `gwr` pick with the arrow keys from a table of worktree, branch and
path: enter takes the highlighted row, a digit takes that row, and esc
cancels. `gwl` offers the main checkout even from inside it. `gwr` shows
the main checkout dimmed, starts on the worktree you stand in, and at a
terminal asks for confirmation when a branch is unfinished (keeping the
branch) or holds ignored files. An exact branch or path selects that
worktree alone.

`gwl --list` prints the same columns and is never coloured. `gws` and `gwp`
print BRANCH, PATH, VERDICT, WHY. The `gwa`, `gwl`, `gwm` and `gwr` shell
functions print `--list` and `--json` output.

`gws`, `gwp` and `gwr` show a spinner naming the check in progress and judge
worktrees in parallel. Every command that fetches reuses a fetch under 10
minutes old, and forge answers that cannot change yet are kept; the README
lists each case. `--force-refresh` fetches and asks again.

## 0.4.3 - 2026-09-22

### Fixed

`gwp` can prune a branch whose pull request is merged and whose configured
upstream is gone. Branches with no upstream configured remain unknown.
Forge-confirmed branches with ignored files require `--delete-ignored`.

## 0.4.2 - 2026-09-22

CLI behavior is unchanged from 0.4.1.

## 0.4.1 - 2026-09-21

CLI behavior is unchanged from 0.4.0.

## 0.4.0 - 2026-09-21

### Security

Forge queries ignore `GH_REPO` and `GITLAB_REPO`, so a merged request in
another repository cannot authorize removal here.

Printed branch restoration commands and `gwnb` checkout hints quote branch
names so shell metacharacters are treated as part of the name.

### Added

`gws` reports how many worktrees under the shared `.worktrees` directory
belong to other repositories, with the count in `gws --json` as `neighbours`.

### Fixed

After a failed fetch, worktree assessments use local refs and skip forge
queries.

`gwl` and `gwr` shell completions omit paths containing newlines, which
cannot fit their one-row-per-line format; those worktrees remain reachable
by name or path.

Pressing Ctrl-C at a worktree selection or removal prompt exits with code
130 and no traceback. No worktree is selected or removed.

## 0.3.1 - 2026-09-20

`gws` and `gwp` list the main checkout, and `gwl`'s picker shows the
worktree you are standing in. Both listings used to leave those rows out, so
a repository with the main checkout and one worktree showed one row where
`git worktree list` shows two, which reads as though something went missing
rather than as where you already are.

The main checkout is `keep`, with `it is the main checkout, and never
removable`. Nothing proposes it, and `git worktree remove` answers
`fatal: '<path>' is a main working tree` whatever flag it is given, so `gwr`
still does not offer it.

The one you are standing in carries a `*` rather than a number:

```
  *  cleanup                           /home/you/git/.worktrees/cleanup/repo
  1  docs-ship-with-the-change (main)  /home/you/git/repo
which? [1-1, or blank to cancel]
```

Two things move with those rows. The counts include the main checkout, so a
repository with one linked worktree reads `1 removable, 1 kept, 0 unclear`
where it read `1 removable, 0 kept, 0 unclear`, and `gws --json` and
`gwp --json` carry the extra verdict. A caller filtering on
`verdict == "remove"` is unaffected; one reading `verdicts[0]` gets the main
checkout now.

`no worktrees besides the main checkout` is on stderr rather than stdout, and
says `; gwa NAME makes one`. The table is always on stdout now, so stdout is
never that sentence instead.

## 0.3.0 - 2026-09-20

`gwl` with no query asks which worktree rather than moving you to one.
Whenever exactly one was left it took it outright, so a repository holding
the main checkout and one worktree moved you without a word, and running it
twice walked you back and forth. A query that narrows to one has already said
which and still takes it outright. Without a terminal it now refuses at exit
3 and names `--list` and `--json`, where it used to print a path, so
`cd $(gwl)` in a script stops working. `gwl <query>` is unchanged.

`gwl --list` no longer tries to `cd` into its own listing, and `--json` no
longer tries to `cd` into the JSON. The shell function reads what the binary
printed and moves you there, and those two modes print on the same stream a
destination arrives on, so every one of them ended in a failed `cd`. `gwa
--json` was the worst: the worktree was created and then the move failed, so
the run looked broken and was not. It reached `gwa`, `gwl`, `gwm` and `gwr`
in both fish and zsh, and `fish -c 'gwl --json'` too, because a fish function
wins there. **That fix lives in the shell functions, so `uv tool upgrade`
alone does not deliver it; update the fish or zsh plugin as well.**

An invocation costs about 18 ms less on Python 3.11 and about 12 ms less on
3.14. `gw version` was 48.4 ms and is 30.5 ms on 3.11.13, and was 55.6 ms and
is 43.1 ms on 3.14.7, measured from installed wheels on one machine. Four
changes account for it: the command you typed imports only what it needs
rather than every command, records are plain classes rather than dataclasses,
the parser is built for the subcommand you asked for rather than all eleven,
and `json` is imported only where it is printed. `difflib`, which named the
closest command when you mistyped one, is no longer imported to start.

### Choices

`gwl` asks whenever no query was given, even when the repository holds one
other worktree. Taking it outright reads as a toggle: the command moves you,
and moves you back, and never says where. Prompting for a single option looks
redundant until it is the difference between being shown your options and
being sent somewhere. A query is the way to skip it, and it always was.

`gwl` without a terminal exits 3, joining `gwp`, which 0.2.3 moved there for
the same reason. A script can then tell a refusal from a typo: 2 is
argparse's code for a usage error and 3 is nobody else's.

## 0.2.3 - 2026-09-11

An unreachable remote is an answer rather than a failure. Every command that
touches the network used to exit 1 with git's own message, so the warnings
written for the offline case never reached anybody. A failed fetch now says
the refs are the ones already here, and the assessment carries on.

Refusing to prompt exits 3 everywhere. `gwp` used to exit 2 when stdin was
not a terminal, which is argparse's code for a usage error, so a script could
not tell a refusal from a typo.

Removing a worktree names the stash entries made on its branch. A stash
outlives the branch while its subject goes on naming it, which is how it
becomes unfindable.

`gwr` asks the forge once, about the branch it is removing, instead of about
every worktree: three round trips became one in a three-worktree repository.
`--no-forge` turns it off there, as it already did for `gws` and `gwp`.

`--explain` lists all seven refusals the guard enforces. The footer was
written by hand and had been a rule behind since `update-ref` was added.

An invocation costs about 15 ms less, and an assessment makes half as many
`git status` calls.

### Choices

The version is written into the wheel at build time rather than read back
through `importlib.metadata`, which cost 10 ms of a 67 ms invocation for a
string two commands print. Deriving it from the tag with `hatch-vcs` was the
alternative, and it inverts which of the tag and `pyproject.toml` has to
agree with the other.

## 0.2.1 - 2026-09-11

`--version` is gone from every command. `gw version`, or `gw v`, prints the
version this package installs; `gws --version` and the rest now fail as an
unrecognised argument, so a script reading the version back has one line to
change.

A name `gw` does not carry names the closest one that exists, instead of
printing a usage line and all twenty-two commands under an argparse error:

    $ gw verison
    gw: there is no command 'verison'
    the closest is: gw version
    gw help lists every command

### Choices

`version` installs no binary of its own, and `gwh` prints a `—` in its
column to say so. A tenth console script, for a question asked once, would
be a name nobody types. Keeping `--version` beside it was the alternative:
two spellings of one answer, on every command, each to be completed,
documented and tested.

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
worktree and marks that one. A repository with no linked worktrees says so
and names `gwa`, where it used to answer as though nothing were there.

`gws --json` carries `ignored` and `sha` on every verdict. The count of
ignored paths reads as a number rather than as a phrase inside `why`, and the
sha is the commit the verdict was formed against, which is the value a caller
would otherwise have to go and read for itself.

`gwp` with nothing removable prints the verdict table. It used to print
`nothing to remove; gws says why`, so the reason each worktree stayed cost a
second command to read.

`gwh` is the help, so nothing needs `gw --help` typed out. It joins `gwa`,
`gwl`, `gwm`, `gwr`, `gws`, `gwp`, `gwnb`, `gwrot` and `gw` on `$PATH`, and
`gw` still takes the same set as subcommands. `gw` with no subcommand prints
that table too, where it used to print argparse's usage. `gw` has its own
entry point now, so a usage error from it names `gw` rather than `worktrees`.

Every subcommand answers to a shorthand as well as its name: `s` and `st` for
`status`, `p` for `prune`, `a` for `add`, `l` and `ls` for `list`, `m` and
`mv` for `move`, `rm` for `remove`, `nb` and `new` for `new-branch`, `rot` for
`rotate`, `h` for `help`. One table in the CLI carries every name, so what
`gwh` prints, what the completions offer and what `gw` accepts cannot
disagree, and it refuses to import if two commands ever claim one name.

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
