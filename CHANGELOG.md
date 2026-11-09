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

## 0.8.0 - 2026-11-09

### Changed

`gw` and `worktrees` are the only commands `uv tool install` puts on
`$PATH`. Every shortcut is a fish and zsh plugin function that runs a `gw`
command and completes as that command does: `gws`, `gwp`, `gwp!`, `gwpa`,
`gwpa!`, `gwa`, `gwl`, `gwm`, `gwr`, `gwr!`, `gwff`, `gwbc`, `gwbs` and
`gwbp`. A script or an agent that ran one of them as a binary, such as
`gwp --json`, needs the `gw` form instead: `gw prune --json`. Upgrade the
shell plugin to the same release as the CLI, because the previous plugin's
shortcuts call binaries this release no longer installs.

Every command except `gw sync`, `gw version`, `gw ci-checks`,
`gw fast-forward`, help and completion runs `gw sync` first when the last
sync is 60 seconds old or more, or has never run: it fetches every remote
and asks GitHub or GitLab about each local branch. `WORKTREES_SYNC_TTL`
sets that window in whole seconds, and `0` syncs before every command. A
failed sync prints a warning, hidden under `-q` and `--json`, and the
command carries on with the evidence it had.

`--no-fetch`, `--no-forge`, `--force-refresh` and its short form `-f` are
removed from every command. Run `gw sync` when you need fresh evidence.

`WORKTREES_OFFLINE`, set to any non-empty value, keeps every command off
the network. `gw sync` and `gw ci-checks` then refuse at exit 3,
`gw fast-forward` decides from local refs alone, and the never-synced, age
and unresolved-head notices stop suggesting `gw sync`. A script or agent
that relied on assessment never touching the network should set it.

`gws` runs `gw sync` through the shell plugin. In 0.7.0, `gws` assessed
worktrees without changing anything; it now fetches.

`gw prune` prints every worktree's verdict and asks for approval on a
terminal. Without `--yes`, `--json`, `--table` and a run without a
terminal assess without changing anything, at exit 0, where 0.7.0 refused
at exit 3 without a terminal. `--only VERDICT`, which was on `gws`, filters
`gw prune` now, assesses without removing, and cannot be combined with
`--yes`. `gw prune` takes no target and `--branch` is removed: use
`gw remove` for one worktree.

Assessment rows separate the verdict from eligibility. A branch whose tip
is on the head branch's first-parent history is `empty`, a fourth verdict
beside `remove`, `keep` and `unknown`, and protected worktrees and branches
remain ineligible even when their changes are upstream. The table gains an
ELIGIBLE column, and the counts under it read as
`2 remove, 1 empty, 3 keep, 0 unknown; 2 eligible`. `gw branch status`
lists loose branches only, the ones no worktree has checked out.

`gw prune`, `gw remove` and `gw branch prune` require permission to delete
empty branches or branch stashes. Use `--delete empty` and
`--delete stashes` as needed. For worktrees, `--delete ignored-files`
replaces `--delete-ignored`. `--delete all` grants every applicable
category; `--yes` adds none.

`gwp!` runs `gw prune --yes`, and `gwr!` runs `gw remove --yes`. In 0.7.0
`gwp!` was `gwp --force-refresh --delete-ignored --yes` and `gwr!` was
`gwr --delete-ignored`. Neither permits deleting ignored files any more:
add `--delete ignored-files`, or use `gwpa!`, which runs
`gw prune --delete all --yes`.

`gw remove` follows the same rules as `gw prune`. Without `--yes`, a run
without a terminal, or with `--json` or `--table`, prints the matching
worktree's assessment row and removes nothing, at exit 0, where it used to
refuse at exit 3. `--json` prints the same `verdicts` rows as
`gw prune --json`. `--yes` approves removal in every mode, and `-q` prints
the verdicts only. `gw remove` matches exact names or paths. Removing the
worktree you are standing in asks a second time at a terminal, even with
`--yes`.

Use `gw remove --keep-branch` for checkout-only removal that retains its
branch and stashes, including finished branches. The `--force` spelling is
removed.

Removal prints recovery commands and rechecks the approved state before
changing it.

`gw branch delete` is now `gw branch prune`, and its shortcut `gwbd` is now
`gwbp`. Scripts calling `gw branch delete` must use the new name.

`gw fast-forward`, its shorthand `gw ff` and the shell shortcut `gwff`
replace `gw rotate`, `gw rot` and `gwrot`. Where `gwrot` started a new
branch from the head ref whatever the branch held, `gw fast-forward` keeps
the branch you are on: it fast-forwards one with no commits of its own,
and rebases one with commits onto the head ref, stopping at the first
conflict with the rebase left for you to finish. Only a branch proved
finished moves to a new branch, named `YYYY-MM-DDThh-mm-ssZ/NAME` rather
than `NAME-YYYY-MM-DD_NNN`, or the `NAME` you pass, started from the head
ref or `--from`. A branch already level exits 0, where `gwrot` exited 1.
The JSON result gains `result` and loses `stem` and `at`.

`gw fast-forward` fetches the default remote and asks the forge on every
run, so a pull request merged a moment ago is seen, and refuses at exit 1
when either fails, ending with the same command under
`WORKTREES_OFFLINE=1`.

Without `--autostash`, neither the fast-forward nor the rebase autostashes
uncommitted changes, whatever `merge.autoStash` or `rebase.autoStash` say,
so git refuses when they are in the way; this needs Git 2.27 or later.
`--autostash` carries them across, and when they do not re-apply cleanly,
the result names the stash entry that holds them by sha.

`gw list --list` is now `gw list --table`, and `gw list -l` is removed:
`--table` has no short form.

`gw list` without a terminal prints the matching worktrees as `--table`
does, at exit 0. In 0.7.0 `gwl QUERY` printed the path of the one worktree
it matched and refused at exit 3 when it matched several, so a script that
ran `cd "$(gwl QUERY)"` needs `gw list QUERY --json` and its
`.worktrees[0].path` instead.

Every `--json` result is an object carrying `lastSync`, when the refs and
the forge evidence were last synced, and `head`. `head.ref` names the ref
the head resolves to, such as `refs/remotes/origin/main`. For a remote HEAD
that is not symbolic it names the one remote-tracking branch at that
commit, the branch the verdicts were measured against. An inferred head
reads `refs/heads/main` or `refs/heads/master`, and an unresolved one is
`null`. In 0.7.0 `gws --json` printed `head` as a string. `gw list --json`
returns an object with its rows under `worktrees`, where it printed a bare
array.

Typing in the `gw list` and `gw remove` pickers filters the rows, matching
the way `gw list QUERY` does. Backspace removes a character, Escape clears
the filter, and Escape on an empty filter cancels. Digits are part of the
filter, since worktree names carry them, so a digit no longer picks a row:
type to narrow the list, then press Enter.

Running `gw` with no command prints help, as `gw --help` does: the
commands, each with its shortcuts indented beneath it, and the shell
plugin's release, and nothing else. Every command accepts `-h` and
`--help`, and each command's `--help` opens with a sentence or two saying
what it does and ends with its shortcuts.

A word that is not a command exits 2 and suggests `gw list WORD`, where
0.7.0 named the closest command.

`gw add` and `gw branch create` without their argument print which one is
missing and then the command's full help, at exit 2.

`-q` silences warnings in every command except the never-synced one, and
`-q` with `-v` is refused at exit 2 before any repository work.
`gw branch status` accepts `-q` and rejects `-v`, and `gw list` accepts
neither. Quiet mode keeps the result line of `gw branch create` and
`gw fast-forward`.

Messages that suggest a command name its `gw` form, such as
`gw prune --yes removes the 2 marked removable`.

In a partial clone, assessment needs Git 2.45.1 or later, or a patched
maintenance release (2.39.4, 2.40.2, 2.41.1, 2.42.2, 2.43.4 or 2.44.1 and
later in each series), and refuses an older Git at exit 3 before reading
any object. Missing objects give `unknown` verdicts with a warning.

The agent plugin installs as `worktrees@mihaibojin`, from the marketplace
that `claude plugin marketplace add MihaiBojin/agent-plugins` or
`codex plugin marketplace add MihaiBojin/agent-plugins` adds.
`worktrees@release-tools` is no longer listed.

The agent plugin's cleanup skill is now named `prune` and still runs as
`/worktrees:prune`. It shows head and sync state before candidates and
asks whether to sync before continuing with stale or never-synced
evidence. Selected worktrees use their exact JSON paths and only the
deletion categories the user approved. It runs `gw sync`,
`gw list --json`, `gw list --table`, `gw prune --json`,
`gw branch status`, `jq` and `git remote` without asking for permission.

### Removed

`gwh`, `gw help` and `gw h` are removed; `gw` or `gw --help` prints the
command list. `gw ls` is removed, and `gw l` remains.

`gw status`, `gw s` and `gw st` are removed. `gw prune --json`,
`gw prune --table`, or `gw prune` without a terminal, assesses without
removing anything.

`gw move` and `gw mv` are removed; `gwm` and `gw m` run `gw select-main`.

### Added

`gw sync` fetches every remote and all their tags, records which local
branch tips the forge has merged, and queries the forge eight branches at
a time with a line per branch in progress. A tag that moved on the remote
keeps its local commit, with a warning that names it. `gw sync` refuses a
remote whose fetch refspec writes outside `refs/remotes/<name>/`, such as
the one `git clone --mirror` sets, because fetching with it deletes local
branches. A failed `git remote set-head` keeps the rest of the sync.

`gw sync` and `gw version` print the head branch the verdicts are measured
against, such as `Head: main (remote)`.

A repository with a remote that `gw sync` has never fetched prints
`Never synced: ... Run: gw sync` on stderr even with `-q` or `--json`, so
scripts and agents see that the verdicts rest on clone or last-fetch refs.
The stale-sync warning and the head line are hidden by `-q` and `--json`,
and a repository without a remote prints neither warning.

`gw add`, `gw remove`, `gw select-main` and, at a terminal, `gw list`
change your directory through the plugin's `gw` shell function, so
`gw a NAME` lands you in the new worktree.

`gwpa` runs `gw prune --delete all` and still asks at a terminal, and
`gwpa!` adds `--yes`.

`gw select-main`, or `gw main` and `gw m` for short, changes into the
worktree that has the head branch checked out: `main`, `master` or
whichever branch the remote's HEAD names. `gwm` runs it through the shell
plugin. It works from inside that worktree too, which brings you back to
its top. Without a terminal it prints the path, so
`cd "$(gw select-main)"` works in a script, and `--json` prints the
worktree's row under `worktrees`. When no worktree has the branch it exits
1 and suggests `gw add BRANCH`.

`gw ci-checks`, or `gw ci` for short, reads a pull request's checks, or a
GitLab merge request's, from the forge: the current branch's, or the one
`gw ci-checks NUMBER` names. It prints the checks with failures first, or
with `--json` the review, every check with a link to its log, and one
`checksState` of `passed`, `failed`, `pending`, `blocked` or `unknown`.
`--wait` reads every 30 seconds until no check is running, for up to 30
minutes. It needs `gh` or `glab` signed in. It reads the forge each time
it runs and never syncs first.

`gw fast-forward` handles a branch whose first commits were squash-merged
and which went on afterwards: it finds where the merged part ends and
carries only the later commits. When the branch's own pull request merged,
they move to a new branch and the old one stays as it was; a stacked
branch whose parent merged keeps its name and its open pull request.

When `gw fast-forward` replays the commits a branch made after its merged
part, each stash made on one of those commits moves to the replayed copy,
on the branch that holds the commits afterwards, so `git stash list` still
ties it to that branch. A stash that cannot move, because its commit was
not replayed or its changes conflict there, stays where it is, and the
result says why.

`gw list QUERY` with several matches opens the picker filtered by the
query, and one Escape widens it to every worktree. A query whose letters
appear in order in a worktree's directory name now finds it, as one
already did for a branch.

`gw prune --json` worktree rows carry `ignoredPaths`: the ignored paths
the worktree holds, each cut to its first component below the `.gitignore`
that ignores it, so `build/out/a.log` and `build/out/b.log` read as
`build/`. The removal disclosure lists the same entries, and `ignored` and
the "it holds N ignored path(s)" protection count them rather than every
path Git lists. A file name holding a newline or an escape character is
quoted and escaped in the disclosure.

Worktree rows in `gw prune --json` carry `undo`, the `git branch NAME SHA`
that restores the branch. It is null on a detached worktree.

The agent plugin adds six skills: `new-branch`, `ci-checks`,
`describe-pr`, `merge`, `fast-forward` and `ship-it`. `new-branch` starts
a branch from the freshly fetched head, `ci-checks` reads checks through
`gw ci-checks`, and `describe-pr` corrects a pull request's title and
description against its diff. `merge` fixes failing checks, merges once
CI is green, brings the head branch's checkout level with what landed,
and reports what it fixed. `fast-forward` runs `gw fast-forward --json`
and resolves each rebase conflict until the branch is level.

`ship-it` runs as `/worktrees:ship-it` in Claude Code and
`$worktrees:ship-it` in Codex. It takes the change in the checkout from
uncommitted work to a merged pull request without asking along the way:
it commits by path, brings the branch level, pushes, opens the pull
request, fixes failing CI and merges, then reports what it fixed.
`--hold-merge` stops before the merge and prints the command that makes
it. Neither client lets a plugin grant itself the right to push or merge,
so the plugin's `skills/ship-it/rights.md` lists the rules to add to
Claude Code's settings or Codex's rules, and the skill names any that are
missing.

### Fixed

`gwa -h`, `gwl -h`, `gwm -h` and `gwr -h` are coloured through the fish
and zsh functions, as the other commands' help already was. Their help
now goes to stderr, so `gwa -h | less` needs `2>&1`.

On a terminal narrower than the picker's rows, moving through the list
stacked a fresh copy of it under the last. Rows are now cut to the
terminal's width, and a list taller than the screen scrolls with the
highlight.

Shell functions preserve path newlines and error output. After removing
the current checkout, `gwr` enters the main checkout even if later cleanup
fails, and keeps the failure status. `--json` output leaves the caller's
directory unchanged, even when a relative directory has the same text.

Assessment keeps unrelated histories ineligible.

Worktrees with a leftover submodule store and locked worktrees whose
directory is missing are kept rather than removed.

Worktree removal preserves current-checkout safeguards for paths ending in
whitespace. Branch protection and stash ownership retain exact names,
including Unicode separators.

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
