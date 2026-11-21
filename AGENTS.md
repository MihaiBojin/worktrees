# Working in this repository

A Python CLI for git worktrees. `pyproject.toml` declares one console script,
`gw`, and its long name `worktrees`; `src/worktrees/` holds the commands;
`tests/` drives the entry points through `subprocess` so env, cwd, both
streams and the exit code stay per-test.

```console
uv sync --all-extras
uv run --no-sync pre-commit install
uv run --no-sync pytest tests
uv run --no-sync pytest tests -m integration
uv run --no-sync pre-commit run --all-files
```

pytest runs one pytest-xdist worker per core. Add `-n0` to run in one process,
which `-x` and `--pdb` need.

`pre-commit run --all-files` reads `git ls-files`, so a file that is new and
unstaged is invisible to it. Stage before running the hooks, or install them
and let the commit do it.

Read [scripts/README.md](scripts/README.md) before measuring anything. It says
what is worth timing and what is not, and why every interpreter the benchmark
touches comes from uv: a run that compares packagers rather than versions
produces a number that looks like a CPython regression and is not one.

## Binaries and shell functions are not interchangeable

A shell function of a given name beats a binary of that name in bash, zsh and
fish, and in fish it beats it in `fish -c` too, where the function is autoloaded
from `functions/`. So a command that exists as both runs different code from a
prompt and from a script unless the function is a pure adapter.

`gw` is the one name that is both. Its function calls `command gw`, changes
directory when `gw add`, `gw remove`, `gw select-main` or a terminal
`gw list` or `gw l` prints a destination, and prints data otherwise. Metadata flags and
nonterminal input print data. `gw remove` can return a destination after checkout removal and
failure for later cleanup; the function changes directory and preserves the
failure status. Every other subcommand passes straight through. The words
that land are the rows with `lands` set, and `tests/test_shims.py` holds both
functions to them.

`--json` and `--table` print data on the destination stream. A function that
cannot tell them apart can try to `cd` into a JSON object or suppress its
output. `fish -c 'gw list --json'` reaches the function too, so this is the
agent path as much as the human one.

| name | ships as |
| --- | --- |
| `gw`, `worktrees` | a console script; `gw` is also the function that changes directory |
| `gwa`, `gwp`, `gwp!`, `gwr` and every other shortcut | a plugin function, and nothing else |

A binary cannot `cd` its caller. That is the only thing shell code is here for,
and any logic that reaches a shell file is logic a script cannot call.

The `gw` function preserves stdout bytes, including trailing newlines in a path. Fish
uses `string collect -N` and `$pipestatus[1]`; zsh appends a sentinel inside
command substitution so it can recover trailing newlines. Both remove only
the destination record's final newline, require an absolute existing
directory, and preserve the binary's status. Data output is printed intact.
Read [the fish function](functions/gw.fish) and
[the zsh function](zsh/plugins/worktrees/functions/gw) for the implementation.

`command` is what stops the function calling itself, in all three shells.

Because the function captures stdout, a command that lands prints its `--help`
on stderr, and argparse is asked whether that stream is a terminal before it
picks colour. A missing argument prints the error and then the help, both on
stderr, at exit 2: the function prints nothing captured from a run that
failed.

A shortcut is one call to the `gw` function with fixed words, by its plain
name so a shortcut to a command that lands still lands: `gwa` calls `gw add`,
`gwl` calls `gw list`, `gwr!` calls `gw remove --yes`, `gwrk!` calls
`gw remove --keep-branch --yes`, and
`gwpa!` calls `gw prune --delete all --yes`. No shortcut has a console script.
The `shortcuts` field of a row in `cli.py` lists that command's; `gw --help`
prints them under it, and `tests/test_shims.py` holds every function file in
both shells to that table. fish completes a shortcut through its `--wraps`,
zsh through the shortcut's line in `_gw`. A shortcut's `!` is its last
character, and interactive zsh and bash leave a `!` before a blank or the end
of the line alone, so no shortcut meets history expansion. `!!` would.

## Two commands start branches

`gw branch create` and `gw fast-forward` start branches, not worktrees.
`gw branch create NAME` starts NAME off the head ref, as `origin new-branch`
does, and keeps argparse's 2 for a usage error where `origin` has a single
non-zero exit code. On a terminal, standing on a branch other than the head
branch, it asks whether to stack NAME on that branch, and the sync it runs
first runs on a thread meanwhile: `_refresh` starts the spinner only once
the answer is in, so nothing draws over the question. `--here` starts NAME
from the commit this checkout is on and syncs nothing.

`gw fast-forward` starts a branch only when the one you stand on is proved
finished, by the same `merged.branch_proof` that `gw prune` reads, or when
its first commits are and its own request merged them: `merged.boundary`
asks each commit below the tip, from the tip down, since the first of a
squashed pair is not upstream on its own. A branch whose merged part is
another branch's, a stacked one whose parent merged, keeps its name and its
open request, and drops the merged commits in place with the same
`git rebase --onto`. When the parent gained commits before it merged, its
squash holds more than the two share, so no local proof marks the shared
part; `branch_forge.carried_by` asks the forge which merged request carried
the branch's oldest commit, and that request's commit list marks it, read
from one page of 100 by choice: a parent seldom drifts further.
GitHub rewrites that stacked branch's remote copy itself when the parent
merges, so the branch takes the copy instead, through `_rewritten`: no
commit in the branch's reflog may reach it, which is when
`--force-if-includes` refuses the push, and its patch-ids must be the
branch's own, in order. A copy the branch's own push left is in that reflog,
so a branch rebased here after its push keeps its newer commits. A run
before the rewrite arrives finds the copy still holding the merged part;
when `branch_forge.in_stack` says the open pull request is in a native
stack, `_awaited` fetches every `REWRITE_INTERVAL` seconds until it
arrives, and refuses, with the offline command, after `REWRITE_WAIT`.
Once a carry finishes, each stash made after the merged part moves to the
replayed copy of its commit, matched by patch-id:
`git merge-tree --write-tree` re-bases it without touching the checkout,
`git stash store` records the copy, and `prune.drop_stash` drops the
original by sha with the check `gw branch prune` makes.
A merged branch's name stays with its merged request on the forge, so the
finished branch keeps its name and commits and the checkout moves to
`YYYY-MM-DDThh-mm-ssZ/<stem>` in UTC, or to the NAME given, from the head
ref or `--from`. Commits after the merged part are replayed onto it with
`git rebase --onto`, so a stop is an ordinary stopped rebase. The stem drops
a time an earlier rotation put in front, and a name held here or on the
default remote is refused.

It fetches the default remote when a sync is due and asks the forge for
the branch's newest merged request in every run, never reading the cached
one, since a pull request merged a moment ago is the usual reason to run it.
A merge commit the forge names that the head ref lacks raises `_Behind`
before anything moves, and `fast_forward` fetches and decides once more.
A failed fetch or forge query is a refusal whose last line is the same
command under `WORKTREES_OFFLINE=1`.

A branch whose open request targets another branch with an open request,
a stacked one, is levelled with that branch instead of the head ref:
`fast_forward._stack` reads both requests from the forge in the same
run, and the branch below levels with the head ref in its own run.

When the request below merged instead and its branch was kept, the forge
leaves the open request on that finished branch, where a merge never
reaches the head branch. `_retarget` moves its base to the head branch,
the one forge write `gw fast-forward` makes on its own, and `_publish`
pushes the branch under a lease once it is level; a branch with an open
request that dropped a parent's merged commits is pushed the same way.
Commits the finished branch gained after its merge that the head ref
lacks, by `merged.change_in`, go first: `_rescue` copies them without a
checkout onto a dated branch from the head ref, pushes it and opens its
request, and a copy that cannot be made leaves the base where it is.
`gw merge` refuses a base that stands at a merged request's head commit,
so a run that skipped this still cannot land there.

Every other branch keeps its name. One with no commits of its own
fast-forwards and one with commits is rebased, both with `--no-autostash`
unless `--autostash` is given: `merge.autoStash` and `rebase.autoStash`
would move a dirty tree into the stash list every worktree shares without
being asked. Under `--autostash`, git's own autostash carries the changes,
and one that does not re-apply leaves an `autostash` entry that the result
names by sha as `stash-conflict`. The first conflict stops the rebase and
leaves it in progress, at exit 1 with the stopped commit and files in
`--json`, for a person or the agent plugin to finish with
`git rebase --continue`. A branch already level exits 0. The head branch
only fast-forwards.
[fast-forward](docs/commands/fast-forward.md) gives the order of the checks.

## Where shell code goes

Fisher copies `functions/`, `completions/` and `conf.d/` from the repository
root and nothing else. Antidote takes a `path:` into the repository and wants a
`<name>.plugin.zsh` there. So the two live in different places:

```
functions/gw.fish                    fish, installed by Fisher
functions/gwa.fish
completions/gw.fish
zsh/plugins/worktrees/worktrees.plugin.zsh    zsh, installed by Antidote
zsh/plugins/worktrees/functions/gw
zsh/plugins/worktrees/functions/gwa
zsh/plugins/worktrees/completions/_gw
```

Neither manager puts a binary on `$PATH`. `gw` arrives through
`uv tool install` from the published package, which is a separate step.

Both plugins export `WORKTREES_PLUGIN_VERSION`, the release they came from:
`conf.d/worktrees.fish` and `worktrees.plugin.zsh`, one assignment each.
`gw --help` and `gw version` compare it with `__version__`, in Python; the shell
files hold the value and no logic. Nothing built can stamp a file a plugin
manager copies from git, so `scripts/bump_version.py` writes it at release,
along with the tag in the README's install lines, and `tests/test_version.py`
fails when a copy disagrees with `pyproject.toml`.

Nothing installs this from a checkout on the user's disk. It is consumed as a
third-party plugin and a third-party tool, so anything that assumes a local
clone at a known path is wrong here.

A completion asks the CLI for its candidates rather than deriving them, and
`--complete` is how: `gw --complete` prints every subcommand and shorthand,
`gw list --complete` prints every worktree, both as name and description separated
by a tab, which fish reads directly and zsh splits for `_describe`. Hidden from
`--help`, because a person has `gw --help` and `--table` for the same answers.

Every TAB runs one, so `main()` answers the exact words the completion files
send before it builds a parser: `gw list`, `gw remove`, `gw branch`,
`gw branch status` and `gw branch prune`, each followed by `--complete`.
Building the argparse tree costs more than the answer, and on Python 3.14 a
parser imports `_colorize` as it is made. Any other words reach the parser,
so a typo still gets its usage error. The output is the same either way, and
`tests/test_completions.py` drives it through both shells.

No shell file holds a candidate or a rank. What `completions/gw.fish` and
`_gw` do carry is each subcommand's flags, and `tests/test_completions.py`
diffs those against `gw <command> --help` in both directions for both shells:
a renamed flag fails there rather than going quiet in a completion nobody is
watching. A shortcut has no completion file of its own.

## The agent plugin reads `--json`

`plugin/` ships with the CLI: six skills, each a directory under
`plugin/skills/` named as its command is, `/worktrees:<name>` in Claude Code
and `$worktrees:<name>` in Codex. Skills call `gw` from `$PATH`; nothing
assumes a plugin root or a checkout at a known path.

| skill | runs |
| --- | --- |
| `fast-forward` | `gw fast-forward --autostash --json`, then each rebase conflict until the branch is level |
| `ci-checks` | `gw ci-checks --json`, `--wait` to wait without polling |
| `describe-pr` | checks a review's title and body against its diff, and edits them |
| `prune` | `gw prune --json` and `gw branch status --json`, then the removals approved |
| `ship-it` | commits, `fast-forward`, a leased or plain push, `gh pr create`, `gw stack-link` for a stacked branch on GitHub, `describe-pr`, `ci-checks`, fixes on the branch, `gw merge` on GitHub or `glab mr merge` on GitLab, then `fast-forward` in the head branch's checkout, unassisted, for this checkout or each branch it is named; given numbers, those open reviews from `ci-checks` on |
| `preflight` | `jq` merges `preflight/rules.json` into the user's or the project's `.claude/settings.json`, after asking |

`describe-pr` and `ship-it` share `describe-pr/format.md`: the
review's title and body are the squash commit's subject and body, so one set
of rules writes both. `tests/test_plugin.py` holds every skill's
`allowed-tools` to reads, `gw sync` and `gw branch create`: no pattern
covers a removal, a merge, a push, a rerun or a checkout that moves. Three
exceptions are each skill's job: `describe-pr` may edit a review,
`fast-forward` runs `gw fast-forward`, which moves a base left on a merged
branch and pushes the branch it levelled, and the conflict loop its own
test lists, and `ship-it`, invoked to publish one change or the reviews it
names, commits, pushes, opens, links into a stack and edits the review,
reruns CI and merges, as its own test lists. None pre-approves a removal.

A plugin cannot grant a skill more than the turn that invoked it in Claude
Code, and nothing at all in Codex, so `ship-it/rights.md` lists the rules a
user adds to their own client configuration, and the skill checks for them
before it starts. The Claude Code ones live once, in `preflight/rules.json`,
which `preflight` merges into the settings file the user names. It runs only
when called by name, in both clients, and pre-approves no write, so the copy
over the settings file reaches the client's own prompt. Its allow patterns match prefixes, so a test holds the
skill's commands to never writing `--force`, `--admin`, `--auto` or
`--delete-branch`.

The prune skill reads `gw prune --json` and
`gw branch status --json`, preserving head/sync state and full rows for eligible, empty
and stash-blocked cases, and each worktree's ignored paths. `tests/test_plugin.py` extracts the
actual jq filters and applies them to real CLI output. Checked-out branches
are absent from the loose-branch list. Paths and restore commands must retain
exact quoting.

The prune skill's assessments sync first when one is due. The skill asks
before syncing state that is still stale or never synced after that, and
runs `gw sync` only on the user's answer; under `WORKTREES_OFFLINE` that
command refuses, and the skill goes on with the evidence there is. Removal approval covers chosen targets
and explicit deletion categories. Each worktree goes through
`command gw remove` with its exact quoted JSON path, then selected loose
branches through `command gw branch prune`. The plugin never targets its current checkout. It reports
partial checkout, branch and stash outcomes and actual restore commands.

Both clients hand each session `plugin/session.md` when it starts: a
`SessionStart` hook in `plugin/hooks/hooks.json` for Claude Code and
`plugin/hooks/codex.json`, which the Codex manifest names, wraps the file
in the `additionalContext` JSON with `jq`. The rules say how a session uses
its worktree: one per repository, one branch at a time, another only where
it has none or another session works in it, and the check it makes before
editing and whenever changes appear: changes it did not make, or
another process's lock, mean another session works there. Nothing locks a
worktree for a session.

The plugin carries the release version in three manifests, and
`scripts/bump_version.py` stamps all three. `plugin/plugin.json` is the
portable manifest, the Claude one with only the fields Agent Plugins v1 names;
the same test holds it to that.

## The picker is Python's, and there is no fzf

Building the candidate list, matching a query against it, ranking, deciding
what each row shows, reading the answer back: all of it in the CLI. No shell
file holds a candidate, a format string or a rank, and nothing spawns an
external picker.

Typing filters the list with the matching `gw list QUERY` uses, so a
repository with forty worktrees narrows to one in a few keys:

```
   WORKTREE    BRANCH      PATH
>  repo        main        /home/you/git/repo
   fix-parser  fix-parser  /home/you/git/.worktrees/fix-parser/repo
   add-tests   add-tests   /home/you/git/.worktrees/add-tests/repo
which? [type to filter, up/down and enter, esc to cancel]
```

Every printable key goes into the filter, digits included, because names
carry them: `release-0.6.2`, and every branch `gw fast-forward` names. Rows carry no
numbers for the same reason. Backspace takes a letter back, up and down move
the highlight and wrap, and enter takes it. Esc clears the filter; on an
empty filter it cancels, as ctrl-d does, and the hint names which of the two
it does now. `gw list QUERY` opens the list filtered by its query, so one esc
widens it to every worktree.

An arrow is `ESC [ A` or, in application mode, `ESC O A`. Any other `ESC [`
sequence is read to its final byte and dropped, so the `~` that ends the
delete key is never typed into the filter. A byte after an esc that starts
no sequence is a key of its own, so two escapes written together are two.

The terminal goes into cbreak mode
through the stdlib's `termios`, so ctrl-c still raises `KeyboardInterrupt`.
Both switches use `TCSANOW`: `TCSAFLUSH` discards keys typed before the list
appears, and `TCSADRAIN` blocks on an echo queue nothing reads, which is how
the pty tests hang.

A `[y/N]` question goes through the same switch and `_keys` in `pick.yes`,
and the next key answers it with no enter: `y` is yes, and every other key
is no, enter and esc among them, so the capital stays the default. Cbreak
echoes nothing, so `yes` writes `y` or `n` after the question, and it
switches before writing the question, so a key pressed the moment the
question appears is not echoed either. `gw branch prune`'s numbered
`which?` reads a line, since one answer there can take several keys.

The list is redrawn in place when stderr is a terminal that can move its
cursor: the cursor climbs over the frame drawn last and erases everything
under it, since the filter makes the list longer or shorter. The climb counts
lines, so the frame fits the screen: each row is cut to the terminal's width,
and the rows shown are a window as tall as the screen that follows the
highlight. On `TERM=dumb`,
or a stderr that is not a terminal, the list is printed once, each move
prints the row it lands on, and each change to the filter prints the rows it
leaves, so nothing writes a cursor code where it would show up as text.

A query that is exactly one worktree's name, branch or path is that worktree
alone. `gw list one` beside `one-more` is not a question, and neither is
`gw remove "$(gw list two --json | jq -r '.worktrees[0].path')"`.
`gw remove NAME` uses exact names, with an exact path resolving one worktree.
It takes `gw prune`'s rules for that one: nonterminal input, `--json` and
`--table` print the match's rows and remove nothing, so an assessment never
asks and never refuses. With `--yes`, several name matches need a picker or a
nonterminal refusal. It has no `--only`: one target has one verdict.

Dropping `fzf` is less code, not more. It removes the spawn and the fallback
branch a machine without `fzf` would otherwise need.

Matching is substring first, then subsequence, which is the one idea worth
taking from `fzf`: the query's letters appearing in order. Substring looks at
the branch and the path, which holds the worktree's name. Subsequence looks
at the branch and the name, never the whole path, where `.worktrees` hands a
`t`, a `w` and an `o` to any query. About twenty lines, `difflib` not
required.

```
'parse'  -> ['fix-parser']
'tst'    -> ['add-tests', 'try-something']
'ruff'   -> ['renovate/ruff-0.x']
```

Two rules about when the list appears:

- A query that narrows to one takes it outright, with no prompt at all. No
  query asks however many there are, because being moved without being asked
  is not what `gw list` with no argument means.
- Without a terminal, listing prints metadata for all matching worktrees.
  Removal pickers refuse at exit 3 rather than blocking.

Three more about what is in the list at all:

- The main checkout is a destination. It is the one that is always there and
  the one a finished branch leaves you in, so leaving it out leaves out the
  only answer that is always right. `main` finds it whatever branch it stands
  on, because its label carries the word, once.
- In `gw list`, a linked worktree you are standing in is shown and never
  offered. It carries a `*` in the first column and the highlight skips it,
  because picking it is the one answer that takes you nowhere. Leaving it
  out of the list entirely is worse: the list then shows fewer rows than
  `git worktree list` does, and reads as though something went missing
  rather than as where you already are. The main checkout is offered even
  from inside it, with a `*` after its name, since from a subdirectory it is
  the way back to the top.
- In `gw remove`, the main checkout is shown dimmed and never highlighted,
  because `git worktree remove` refuses it whatever flag it is given, and
  the highlight starts on the worktree you are standing in. `gw prune` keeps the main
  checkout in its list for the same reason, and never proposes it.

`--json` and `--table` answer the same question without any of this, and an
agent uses those.

## One row per command, under every name it answers to

`_COMMANDS` in `cli.py` holds the top-level commands; `_BRANCH_COMMANDS`
holds `branch status` and `branch prune`. `command_rows()` lists the
executable commands for help and completion checks. A row carries the function, the flags,
the one-line description, the argument shape, the shorthands, the shell
plugin's shortcuts and whether the command changes the caller's directory.
`gw p` and `gw prune` reach the same function because that row says so.
`gw --help` and bare `gw` print this registry, each shortcut indented under
its command, and the shell plugin's release line, and nothing else;
`gw --explain` prints every git command the program can run. Any other
word that is not a command is a usage error at exit 2 that suggests
`gw list WORD`, so a worktree name never competes with a shorthand. `gw list
[QUERY]` uses the picker on terminal input; without a terminal, or with
`--json` or `--table`, it prints matching metadata. `gw l` is `gw list`, and
`gwl` is its shortcut. `gw select-main`, `gw main` and `gw m` change into the
worktree with the head branch checked out, and `gwm` is their shortcut. `gwh`, `gw ls` and the help subcommand are absent.

A tool's version is asked through the tool's name, so `gw version` and `gw v` are the two spellings and no parser
carries a `--version` flag.

`_canonical()` raises at import when two rows answer to one name. A dict
comprehension would keep the last one and leave a command reachable under a
name that runs a different one.

A shorthand is for typing, so it is short and it is not clever: the first
letters of the word, or the two-letter name the same job has in git and in a
shell. `rm` means what it means everywhere else.

## Colour is a property of the stream, not of the program

`render.py` holds both halves of it: the codes, and a table whose columns line
up. `render.setup()` runs once per invocation and decides per stream.

Padding runs on the text and painting after it. The other order counts an
escape sequence as width, and every column under a coloured cell sits crooked
by exactly the length of the code.

An assessment prints `render.records`, not `render.table`: a box with one
record a worktree or branch, a line of short cells and then its reasons
across the box, so a reason has the terminal's width and not a last column's.
A reason that is a string is prose and wraps to `render.columns`; one that is
a `Cell` is a command or a path and is never broken, widening the box when it
must, so it pastes back. `prune.details` builds them from the row's `Fact`s:
the evidence, each protection, each of a fact's commands on its own line,
and `needs:` once, last. The JSON `protections` keep the one-line wording
with `; pass --delete ...`, which the prune skill reads. The worktree's path
is in `--json` and the removal disclosure, not in the box. `render.ruled`
picks box characters for a UTF-8 terminal and `+`, `-` and `|` otherwise;
`--table` sets `render.plain`, which turns both wrapping and box characters
off, since a script reads it.

stdout carries the path `cd $(gw add x)` reads and the JSON `jq` parses, so a
redirect, a pipe, a non-empty `NO_COLOR`, or `TERM=dumb` mean the bytes go out
as they would have without the module. `NO_COLOR=` is not a request to turn it
off: that is the no-color.org rule, and it is why `supported` tests the value
and not the key. `--json` and `gw list --table` are never painted at all, even
on a terminal: both are what a script reads. The colour is applied at the
call site that formats a table, and neither path passes a code to one.

`progress.py` draws a spinner on stderr and one line per running thread
saying what it is checking, while `gw sync`, `gw prune`, `gw remove`,
`gw branch status` and `gw branch prune` assess or mutate. A thread's line
goes when the thread ends, and every line is cut to the terminal width so
the redraw can count its rows. It runs only when stderr is a terminal that
can clear a line, and never under `-q` or `-v`, since the verbose log writes
to the lines it redraws. Anything else printed on stderr while it spins goes
through `progress.line`, which clears the spinner first.

Every message on stderr goes through `render.fail`, `render.warn` or
`render.note`, a usage error and a cancelled picker included. On a terminal
each leads with its symbol, `✗`, `!` or `→`, and its first line makes flags
and quoted commands bold, paths dim and shas yellow. A path is a word that
starts with `/`, `~/`, `./` or `../`, taken whole, so a directory named `-x`
is not a flag and a uuid inside one is not a sha. A failure prints every line
red; a warning or a note dims the lines after its first.

A branch name is wrapped in `render.branch` and a ref such as `origin/main` in
`render.ref` where the message is built, since no pattern tells a branch from
prose; the passes after leave those alone. A branch is bold magenta wherever
it prints: a table, the picker, a message, a result line. A prompt is
`render.ask`, the question bold and the answers it takes dim. The line that
puts a branch back is `render.restore`, painted the way a table paints a
branch and a sha, and it pastes the same. Off a terminal it is the words
alone, so a test or a log reads what the code wrote. `-v` prints each command once it has run, as
`render.trace` lays it out: the line that pastes back, what it printed,
indented, its exit code when that is not 0 or is the only answer, then a
blank line. Printing after the run keeps a command and its answer together
when eight threads are asking at once.

## Sync runs before a command that is due one

`gw sync` fetches every remote with `--prune --tags --no-prune-tags`, the
default first, updates the default remote's HEAD and queries the forge for
every local branch tip on eight threads. Every other command but
`gw version`, `gw list`, `gw ci-checks`, `gw merge`, `gw stack-link`,
`gw fast-forward`, help and completion runs the same `sync.run` first when
`sync.due` says so: the default remote's refs time is null or at least
`WORKTREES_SYNC_TTL` seconds old, 300 by default. The forge time is not read,
since a forge that keeps failing would sync before every command. Without a
default remote nothing syncs first, because sync records nothing there.
`gw list` reads only the worktree records, so it never syncs first and
prints no word about how fresh the evidence is; its `--json` takes `head`
and `lastSync` from the cache. `gw fast-forward` runs `sync.fetch`
instead when one is due, which fetches the default remote and records the
refs time.

A failed sync before a command is a warning, and the command goes on with
the evidence it has; quiet and JSON hide the warning. A failed fetch before
`gw fast-forward` is a refusal. Nothing it prints
reaches stdout, so `cd $(gw add x)` and `jq` never see it. A TTL that is not
whole seconds is a usage error at exit 2.

`WORKTREES_OFFLINE`, when non-empty, turns sync off everywhere: nothing syncs
first, and `gw sync` and `gw ci-checks` refuse at exit 3. Every notice that would end
`Run: gw sync` goes through `sync.hint`, which leaves that off offline,
since it names a command that refuses there.

`gw merge` squashes one GitHub PR at a supplied head SHA. It waits up to ten
minutes, polling every five seconds, for lower stack members to merge, the
base to match the stack base and the head to contain the latest parent merge.
A rewritten head returns `head-moved` at exit 4 without submitting. It reads
fresh CI and verifies membership and containment again before calling the
asynchronous merge API, then polls a pending operation for one minute. `enqueued` is queued, not merged. Both clients call
the same command; their rules admit `gw merge` without general `gh api`
writes. [Merge](docs/commands/merge.md) describes the result and refusals.

`gw ci-checks` reads one review and its checks from the forge each time it
runs and writes nothing, the sync cache included. It never syncs first: its
answer comes from the forge, not from local evidence.

Sync refuses, before fetching, any remote whose refspec writes outside its own
`refs/remotes/<name>/`, since a mirror refspec with `--prune` deletes local
branches. Git refusing a moved tag is a warning; any other refused ref fails
that remote. The fetch runs in the C locale so sync can read which it was. The
shared `git-worktrees-sync.json` cache in the Git common directory records
remote name/URL, successful sync times and source-tip forge evidence. Sync
replaces it atomically. Remote identity changes invalidate it; malformed or
mismatched evidence is never proof.

`sync._FRESH` is 10 minutes. Warnings compare the oldest non-null component
time, with exactly 10 minutes still fresh. The never-synced line goes to
stderr under `-q` and `--json` as well, because a caller acting on the
verdicts needs to hear that no sync stands behind them. Quiet and JSON
suppress every other warning, the inferred-head notice included; a removal's
disclosure still repeats it. `gw sync` and `gw version` print the head line.
JSON carries `lastSync` and `head`. A repository without remotes, the local
case, has no sync warning.

Partial-clone object reads require Git 2.45.1 onward or the maintenance
cohorts in `repo.require_offline_git`. Lower reported vendor versions refuse;
full clones keep older Git compatibility. Offline proof uses read-only
patch-id input and creates no synthetic commits.

The per-worktree checks run on a thread per core, eight at most. Each is a
git process whose work is the CPU's, so more threads than cores only queue
behind each other and the interpreter lock; `sync` keeps eight, since its
threads wait on the network. Mutations remain serial.

The recheck after confirmation is an assessment too, so `gw prune` runs one
for the whole batch, `worktree.recheck`, on the same threads, and then
removes one target at a time. Rechecking before each removal read every
checkout one after another, which a repository whose `git status` takes
half a minute pays once a target. Just before it deletes, `worktree.remove`
still checks the head and the worktree's record, branch, sha and flags, so
a ref an earlier removal moved refuses the later one, and
`git worktree remove` refuses a checkout dirtied since. What the wider gap
leaves open is an ignored path written between the batch recheck and that
target's removal. A single target, as `gw remove` has, is rechecked as it
is removed.

An assessment asks every worktree the same questions about the head, so
`merged.reuse()` keeps the answers that name objects by full SHA for the
length of one `assess`: a commit's tree, a merge base, the patch-ids of the
head's commits after a fork point. Those are facts about the objects. Nothing
outside an assessment keeps them, since `gw fast-forward` fetches and an
object missing a moment ago can appear, and a failed answer is never kept.

Two of those answers outlive the run: `_local_proof` for a tip and
`change_in` for a reflog commit, each against the head's full SHA, saved in
`git-worktrees-proofs.json` in the Git common directory and replaced
atomically when the assessment ends. They have no TTL, since neither commit
changes; the file holds one head's answers and starts again when the head
moves, which bounds it. A partial clone saves only a yes, because a fetch
can bring the object a no lacked without moving the head, and an entry that
is not the shape `merged.py` wrote is asked again. Nothing a checkout holds
is saved: `git status`, submodules, an operation in progress, reflogs and
stashes are read on every run, since they are what stops a worktree with
work in it being offered.

`git submodule status` is the costliest check, so the store decides alone
when it exists, and without one the check runs only where `.gitmodules`
exists. The store outlives a deleted `.gitmodules`, which is also when
`submodule status` itself fails with "no submodule mapping found".

`gw ci-checks` takes GitHub's review and checks from one `gh pr view` with
`statusCheckRollup` and `commits`. Commit messages and checks describe the
same `headSha`. `pr.trailers` holds each trailer once, in commit order,
oldest first, parsed with `git interpret-trailers --parse`. An incomplete
commit list gives null trailers; GitHub's 100-commit cap also gives null.

It keeps the newest run per workflow and name, and a run with
no start time counts as newest, so a queued re-run supersedes the failure it
repeats. GitLab needs several calls: the pipeline is read from the project
that ran it, its jobs and the MR's commits across every page, then the merge
request again. A change between the two reads is `stale` and clears the
trailers. A pipeline for another SHA is stale unless it is the merged-result
pipeline whose parents
are the current head and the target's tip. `ci.wait` takes its clock and
sleep as arguments, so its tests run in-process without waiting.

## The audit log is appended, never edited

`audit.py` writes one JSON line per event to `audit-YYYY-MM.jsonl` under
`$XDG_STATE_HOME/worktrees`, or `WORKTREES_AUDIT_DIR`. Each line is one
`os.write` on an `O_APPEND` descriptor, and nothing here seeks, truncates,
renames or deletes a file. A monthly name is what makes that hold: rotation
by size would need a rename.

Recording runs between `audit.begin` and `audit.end`. `_dispatch` begins
once argparse has the words, so `--help`, which leaves inside `parse_args`,
and a parsed `--explain` or `--complete` on any subcommand record nothing
and answer under a `WORKTREES_AUDIT` it does not take; no scan of the raw
words would know an abbreviated `--hel` or a value after `--`. A usage
error begins in `main`, as it leaves. Every path from `main` to `_command`
is inside the `try` whose `finally` ends the run, so no start lacks its end. Outside the pair every
function returns at once, so a test or a caller that imports the package
and runs a spec writes nothing.

A line is one `os.write` and is never resumed: after a short write
`_append` closes the fragment with a newline if one fits and raises. Each
append takes `flock`, reads the last byte and writes under it, and a file
that does not end in a newline gets one before the line, which carries
`torn`. What the file ended in at open is never kept: another process can
tear a line after that, and a look outside the lock races the write. The
lock is tried for a tenth of a second; without it the line brings its own
newline whatever the file ends in.

Two places see every process: the `@git` wrapper and `forge._run`. Each
times the run and calls `audit.ran`, filed as `reads`, `mutating` or
`forge`; `WORKTREES_AUDIT` turns each off by name and `off` turns off the
log. A spec holding `fetch` is filed as mutating though it is not declared
`mutates`. What a process printed is kept only when it changes something or
failed. `audit.clean` walks the whole event as `record` serializes it and
scrubs every string, so no caller scrubs its own fields and none can forget.

`prune.audit_proposed` runs in `prune.plan` and `prune.audit_outcome` after
`worktree.remove` and `branches.delete`, the second also when the recheck
refuses. `prune.restores` builds one action per thing taken, with its kind,
full sha, branch or subject and an unquoted `argv`; `command` and `undo` are
`shlex.join` of that, so the line a person pastes cannot disagree with the
fields a program reads. A proposal passes only what would go and an outcome
only what went, so a kept or retained branch, and its stashes, have none. They ask git for the remote
only while the log is on. `Moved` carries short shas, so `_audit_moved`
resolves both ends before it writes one.

`tests/audit_schema.py` lists every event's fields, types and values, and
`events()` in `tests/test_audit.py` holds each event a test reads to it: an
unlisted field, a missing one or another type fails. The reference is held
to the same names. `audit.clean` raises on a type it does not know, so a
new field of an odd type drops its event with a warning; nothing is ever
written as a Python repr. A failure's kind and a removal's stage are fields
of their own, `failure` and `failedStage`. A handler in `cli.py` that prints
a refusal and returns goes through `_refuse`, which hands the kind to the
log; one that only printed would end its run as `result`. A cut output
carries its count in `outDropped`, never a mark inside the text.

A write that fails turns recording off for the rest of the run and warns
once through `progress.line`. `conftest.ENV` and an autouse fixture set
`WORKTREES_AUDIT=off`; `tests/test_audit.py` turns it on at a directory of
its own. [Audit log](docs/REFERENCE.md#audit-log) lists the events.

## Every git command is one spec

The spec is the command as you would type it, with `$name` where a value goes.
The module reads as the list of commands the program can run, and `--explain`
prints the specs themselves.

```python
@git("merge-base --is-ancestor $ref $head", ok=(0, 1))
def is_ancestor(ref, head): ...


@git("--no-optional-locks status --porcelain --ignored=matching")
def ignored_paths(): ...


@git("worktree remove -- $path", mutates=True)
def worktree_remove(path): ...
```

`shlex.split` runs once, at decoration time, on the literal spec. Only then is
each token scanned for a placeholder. That ordering is the whole safety
property: the splitting is over before any value is seen, so a branch named
`$(id)`, `a"b` or `has space` lands as exactly one argv element. git accepts
the first two as branch names, and there is a test that creates them.

`$` rather than `{}`, because git's revision syntax is full of braces:
`^{tree}`, `^{commit}` and `@{upstream}` pass through untouched. `$$` is a
literal `$`, and so is a `$` with no name after it.

Three ways to place a value: `$name` anywhere, including inside a token so
`--format=$fmt` stays one element; `$*name` splats a list at that position;
and anything the body returns is appended as a tail, for arguments with no
fixed place. Names bind from `inspect.signature`, so there is no second
mapping to keep in step, and a spec naming a parameter the function does not
have raises `NameError` at import.

The form catches its own bugs. `-C $path status --porcelain
--no-optional-locks` is wrong, because `--no-optional-locks` is a git global
and belongs before the subcommand. That is visible in a spec and invisible in
a tuple.

A command's name says what the caller gets, echoing the git command where the
two can agree. `worktree_list`, `worktree_prune` and `worktree_remove` read as
the subcommands they are; `is_ancestor` beats `merge_base`, because
`merge-base --is-ancestor` answers a yes/no question and that answer is what
the caller wants.

A spec with `ok=` wide enough to swallow an error is a spec that has to be
tested on what it returns rather than on its exit code. `tree_of` takes
`ok=(0, 128)` for a ref that does not resolve, and a typo in its `^{tree}`
exits 128 too. The squash proof and the forge proof would then answer no for
every branch, and nothing would fail.

The network calls belong to sync. Their results distinguish failed
refs refresh from partial forge refresh; assessments retain usable local
proof without asking the network. The cache records each successful
component's time separately.

One `git status --porcelain --ignored=matching` answers both questions a
worktree is asked. It is a strict superset of plain `--porcelain`, so `dirty`
is "a line that does not start with `!! `" and `ignored` is the rest. Asking
both ways was 18 of an assessment's 35 calls over nine worktrees.

`guard()` runs on the resolved argv inside the wrapper, not at declaration, so
no call site can assemble its way past it. `reset --hard`, a forced `checkout`
or `switch`, `clean -f`, a bare `push --force`, `worktree remove --force`,
`branch -D` and an `update-ref` delete that names no full sha are refused
absolutely. There is no flag, and `--yes` least of all. `gw remove` prints
`git worktree remove --force PATH` beside a dirty target's refusal, for a
person who has looked to run; nothing here runs it.

Those seven are `RULES`, a tuple, each row carrying the name `--explain`
prints, the clause the refusal ends with, and the test. The footer `--explain`
prints is built from it. A rule written in one place and described in another
is a rule that goes quiet: the hand-written footer fell a rule behind the
moment `update-ref` was added, and nothing failed.

`verdicts.py` declares no mutating command. A read-only command may not call
one, the fetch and `set-head` of the sync it runs first aside, which write
remote-tracking refs and the remote's new tags: a test reads the verbose log
and asserts it.

## Verdicts and deletion are separate

`merged.branch_proof` checks matching cached forge proof, first-parent
membership, local completion proof, absent evidence, the cached request's
state, then whether a remote-tracking ref holds the tip. It returns a reason:
`merged`, `unused`, `closed`, `open`, `pushed`, `unpushed` or `unknown`.
`gw fast-forward` reads it as it is; prune's assessments split `unused` by
the branch's reflog into `merged`, `unused` and `stale`, and put the
checkout facts first, `uncommitted` among them. A row's verdict is `remove`
for `merged` and `stale` and `keep` for every other reason. First-parent
history belongs to the assessed head, not a divergent local counterpart.

Removing a worktree deletes its HEAD reflog and deleting a branch its
reflog. `merged.reflog_only` lists the commits only those reach, by
`rev-list --not --all`, and keeps each whose own patch-id the head lacks;
they hold the row back until `--delete reflog`, which `--delete all`
includes and which is the one value `--keep-branch` takes.

Content proof compares changed paths, modes and deletions against the head.
It cannot settle a stacked branch whose intermediate content the head later
edited. Cached forge proof verifies source tip, source repository, target
branch and merge ancestry. A request stacked on another branch keeps that
branch as its target after the forge lands it on the head, so a merge commit
on the head's first-parent history stands in for the target branch. Closed
unmerged requests are never proof.

`eligible` combines proof, protections and `--delete` permissions.
`prune.removable()` selects only eligible rows. `main`, `master` and the
head branch are protected. Locked or dirty worktrees, untracked files,
initialized submodules and a submodule store left by `deinit` block removal;
`git worktree remove` refuses each. A locked worktree whose directory is away
is assessed from its record. Empty branches and matching stashes require
their own permission; ignored paths go with the worktree, listed in the
disclosure first. `--yes` grants neither. A protection a value lifts ends
with every value its row needs, and only when those values make the row
eligible, so one hint followed alone frees it; the prune skill's jq reads
that wording, and `tests/test_plugin.py` runs it.
`gw prune` also protects the current worktree; `gw remove` needs a second terminal
confirmation for it. `--keep-branch` retains the branch and its stashes,
needs no completion proof for an attached checkout, and combines with no
`--delete` value.

The removal snapshot pins the branch and head SHAs, exact path, ignored
paths and stash identities. Recheck after confirmation precedes plain
worktree removal, full-SHA ref deletion and approved stash drops. Ignored
paths are what an ignore pattern names, so a matched directory is one entry;
the snapshot is a path listing, not a recursive content fingerprint, and a
file created inside a listed directory after assessment goes with it. The
disclosure and the JSON `ignored` field list each
path as git does, from the top level, sorted, so a tracked directory above
an ignored one never reads as ignored. A path whose pattern, found with
`git check-ignore -v`, ends in a bare `*` or `**` reads as its directory,
since that pattern names the directory's contents. The disclosure quotes a
path holding a character that would not print, as `git status` does, and
ends with the `git -C PATH ls-files --others --ignored --exclude-standard`
line that lists every file below those entries. Stash drops verify SHA and
repair a concurrent wrong-entry drop using the exact subject. Restoration remains
possible until Git garbage collection prunes the unreachable stash commit.
Report checkout, branch and stash outcomes separately after partial failure.

Branch assessment and deletion are documented in
[branch status](docs/commands/branch-status.md) and
[branch prune](docs/commands/branch-prune.md). Checked-out branches appear
only as worktrees; `gw branch prune NAME` naming one keeps it and exits 3. Every JSON result is an object with `head` and `lastSync`;
`gw list --json` stores navigation rows in `worktrees`.

## Reproduce a failure before asserting its fix

Two holes in git are the reason this exists: a squash merge inverts
`git branch -d`, and `git worktree remove` deletes ignored files at exit 0
without a word. Each has a test that reproduces the raw git behaviour and a
test that asserts the fix. A suite that only asserts the fix passes on unfixed
code forever.

The fixture needs no global git config, no signing key, no forge and no
network. `GIT_CONFIG_GLOBAL` carries that: without it git still reads
`$HOME/.gitconfig`, and setting `HOME` alone still leaves
`$XDG_CONFIG_HOME/git/config`. `XDG_CONFIG_HOME` points at the test's own
directory too, since `GIT_CONFIG_GLOBAL` does not reach `git/ignore`, the
default `core.excludesFile`. `WORKTREES_OFFLINE=1` in `conftest.ENV` stops
a command syncing first, which would fetch a remote at `github.com` for
real. `Network.env` clears it for the fake network, and `conftest.online`
for a remote on disk. The fake network's git answers `push` itself, as it
does `fetch`, and its `gh` and `glab` answer an edit and a create.
