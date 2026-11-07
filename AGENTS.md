# Working in this repository

A Python CLI for git worktrees. `pyproject.toml` declares one console script
per command; `src/worktrees/` holds them; `tests/` drives the entry points
through `subprocess` so env, cwd, both streams and the exit code stay per-test.

```console
uv sync --all-extras
uv run --no-sync pre-commit install
uv run --no-sync pytest tests
uv run --no-sync pre-commit run --all-files
```

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

A navigation function carries the binary's name and calls `command <name>`.
It changes directory when stdout is a destination and prints data otherwise.
`gw list` changes directory for terminal selections.
Metadata flags and nonterminal input print data; its adapter gets subcommands
from `--complete`.
`gwr` can return a destination after checkout removal and failure for later
cleanup; its adapter changes directory and preserves the failure status.

`--json` and `--list` print data on the destination stream. A shim that cannot
tell them apart can try to `cd` into a JSON object or suppress its output.
`fish -c 'gw list --json'` reaches the function too, so this is the agent path as much as the human one.

| | changes directory | ships as |
| --- | --- | --- |
| `gwp`, `gwbs`, `gwbd`, `gwbc`, `gwrot` | no | a console script, and nothing else |
| a command that lands you somewhere | yes | a console script, plus a function of the same name |

A binary cannot `cd` its caller. That is the only thing shell code is here for,
and any logic that reaches a shell file is logic a script cannot call.

Adapters preserve stdout bytes, including trailing newlines in a path. Fish
uses `string collect -N` and `$pipestatus[1]`; zsh appends a sentinel inside
command substitution so it can recover trailing newlines. Both remove only
the destination record's final newline, require an absolute existing
directory, and preserve the binary's status. Data output is printed intact.
Read [the fish adapter](functions/gwa.fish) and
[the zsh adapter](zsh/plugins/worktrees/functions/gwa) for the implementation.

`command` is what stops the function calling itself, in all three shells.

Because the function captures stdout, a command that lands prints its `--help`
on stderr, and argparse is asked whether that stream is a terminal before it
picks colour. A missing argument prints the error and then the help, both on
stderr, at exit 2: the function prints nothing captured from a run that
failed.

Shortcuts are fixed-flag calls through the plain command name: `gwr!` calls
`gwr --delete ignored-files`, `gwp!` calls `gwp --yes`, and `gwp!!` calls
`gwp --delete all --yes`. No shortcut has a console script.
`SHORTCUTS` in `tests/test_shims.py` holds each to one line. Interactive zsh
with `BANG_HIST` requires quoted `'gwp!!'` to avoid history expansion.

## Two commands start branches

`gwbc` and `gwrot` start branches, not worktrees, and do the job
`origin new-branch` and `origin rotate` do. The name format, the
stem-stripping and the remote-aware collision check are the same in both, so
a branch either one names is one the other would have named.

Two divergences are deliberate. `origin` has a single non-zero exit code and
draws no line between a usage error and a runtime one; this CLI keeps
argparse's 2 for a usage error. And `gwrot` refuses, at exit 1, a rotation or
catch-up that would change nothing, where `origin rotate` makes a branch at
the same commit under a new name.

## Where shell code goes

Fisher copies `functions/`, `completions/` and `conf.d/` from the repository
root and nothing else. Antidote takes a `path:` into the repository and wants a
`<name>.plugin.zsh` there. So the two live in different places:

```
functions/gwa.fish                   fish, installed by Fisher
completions/gwa.fish
zsh/plugins/worktrees/worktrees.plugin.zsh    zsh, installed by Antidote
zsh/plugins/worktrees/functions/gwa
zsh/plugins/worktrees/completions/_gwa
```

Neither manager puts a binary on `$PATH`. The console scripts arrive through
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
`--help`, because a person has `gw --help` and `--list` for the same answers.

No shell file holds a candidate or a rank. What each file does carry is its own
command's flags, and `tests/test_completions.py` diffs those against `--help`
in both directions for both shells: a renamed flag fails there rather than
going quiet in a completion nobody is watching.

## The agent plugin reads `--json`

`plugin/` ships with the CLI. Its skill reads `gwp --json` and
`gwbs --json`, preserving head/sync state and full rows for eligible, empty,
stash-blocked and ignored-file cases. `tests/test_plugin.py` extracts the
actual jq filters and applies them to real CLI output. Checked-out branches
are absent from the loose-branch list. Paths and restore commands must retain
exact quoting.

The skill asks before syncing stale or never-synced remote state, and runs
`gw sync` only on the user's answer. Removal approval covers chosen targets
and explicit deletion categories. Each worktree goes through `command gwr`
with its exact quoted JSON path, then selected loose branches through
`command gwbd`. The plugin never targets its current checkout. It reports
partial checkout, branch and stash outcomes and actual restore commands.

The plugin carries the release version in three manifests, and
`scripts/bump_version.py` stamps all three. `plugin/plugin.json` is the
portable manifest, the Claude one with only the fields Agent Plugins v1 names;
the same test holds it to that.

## The picker is Python's, and there is no fzf

Building the candidate list, matching a query against it, ranking, deciding
what each row shows, reading the answer back: all of it in the CLI. No shell
file holds a candidate, a format string or a rank, and nothing spawns an
external picker.

The set is small. Across the repositories this was written for, the largest
number of linked worktrees in one is four. A fuzzy finder is the wrong
instrument at that size, and a list moved through with the arrow keys reads
faster:

```
  #  WORKTREE    BRANCH      PATH
> 1  repo        main        /home/you/git/repo
  2  fix-parser  fix-parser  /home/you/git/.worktrees/fix-parser/repo
  3  add-tests   add-tests   /home/you/git/.worktrees/add-tests/repo
which? [up/down and enter, 1-3, or esc to cancel]
```

Up and down move the highlight and wrap, enter takes it, a digit takes that
row outright, and esc or ctrl-d cancels. The terminal goes into cbreak mode
through the stdlib's `termios`, so ctrl-c still raises `KeyboardInterrupt`.
Both switches use `TCSANOW`: `TCSAFLUSH` discards keys typed before the list
appears, and `TCSADRAIN` blocks on an echo queue nothing reads, which is how
the pty tests hang.

The list is redrawn in place when stderr is a terminal that can move its
cursor. On `TERM=dumb`, or a stderr that is not a terminal, it is printed
once and each move prints the row it lands on, so nothing writes a cursor
code where it would show up as text.

A query that is exactly one worktree's branch or path is that worktree
alone. `gw list one` beside `one-more` is not a question, and neither is
`gwr "$(gw list two --json | jq -r '.worktrees[0].path')"`.
`gwr NAME` uses exact names, with an exact path resolving one worktree; several name matches need a picker or a nonterminal refusal.

Dropping `fzf` is less code, not more. It removes the spawn and the fallback
branch a machine without `fzf` would otherwise need.

Matching is substring first, then subsequence, which is the one idea worth
taking from `fzf`: the query's letters appearing in order, ranked by how
tightly they cluster. About twenty lines, `difflib` not required.

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
  offered. It carries a `*` rather than a number and the highlight skips it,
  because picking it is the one answer that takes you nowhere. Leaving it
  out of the list entirely is worse: the list then shows fewer rows than
  `git worktree list` does, and reads as though something went missing
  rather than as where you already are. The main checkout is offered even
  from inside it, with a `*` after its name, since from a subdirectory it is
  the way back to the top.
- In `gwr`, the main checkout is shown dimmed and unnumbered, because `git
  worktree remove` refuses it whatever flag it is given, and the highlight
  starts on the worktree you are standing in. `gwp` keeps the main
  checkout in its list for the same reason, and never proposes it.

`--json` and `--list` answer the same question without any of this, and an
agent uses those.

## One row per command, under every name it answers to

`_COMMANDS` in `cli.py` holds the top-level commands; `_BRANCH_COMMANDS`
holds `branch status` and `branch delete`. `command_rows()` lists the
executable commands for help and completion checks. A row carries the function, the flags,
the one-line description, the console script, the argument shape, the
shorthands and whether the command changes the caller's directory. `gwp` and
`gw prune` reach the same function because that row says so.
`gw --help` and bare `gw` print this registry and the full command guide;
`gw --explain` prints every git command the program can run. Any other
word that is not a command is a usage error at exit 2 that suggests
`gw list WORD`, so a worktree name never competes with a shorthand. `gw list
[QUERY]` uses the picker on terminal input; without a terminal, or with
`--json` or `--list`, it prints matching metadata. `gwl`, `gwh`, list
aliases and the help subcommand are absent.

`list`, `version` and `sync` install no console script of their own.
`gw --help` prints
`—` in its first column for those rows. A tool's version is asked through the
tool's name, so `gw version` and `gw v` are the two spellings and no parser
carries a `--version` flag.

`_canonical()` raises at import when two rows answer to one name. A dict
comprehension would keep the last one and leave a command reachable under a
name that runs a different one.

A shorthand is for typing, so it is short and it is not clever: the first
letters of the word, or the two-letter name the same job has in git and in a
shell. `rm` and `mv` mean what they mean everywhere else.

## Colour is a property of the stream, not of the program

`render.py` holds both halves of it: the codes, and a table whose columns line
up. `render.setup()` runs once per invocation and decides per stream.

Padding runs on the text and painting after it. The other order counts an
escape sequence as width, and every column under a coloured cell sits crooked
by exactly the length of the code.

stdout carries the path `cd $(gwa x)` reads and the JSON `jq` parses, so a
redirect, a pipe, a non-empty `NO_COLOR`, or `TERM=dumb` mean the bytes go out
as they would have without the module. `NO_COLOR=` is not a request to turn it
off: that is the no-color.org rule, and it is why `supported` tests the value
and not the key. `--json` and `gw list --list` are never painted at all, even
on a terminal: both are what a script reads. The colour is applied at the
call site that formats a table, and neither path passes a code to one.

`progress.py` draws a spinner on stderr and one line saying what is being
checked, while `gw sync`, `gwp`, `gwr`, `gwbs` and `gwbd` assess or mutate. It runs only when
stderr is a terminal that can clear a line, and never under `-q` or `-v`,
since the verbose log writes to the line it redraws. Anything else printed on
stderr while it spins goes through `progress.line`, which clears the spinner
first.

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

## Only explicit sync uses the network

`gw sync` fetches the default remote, updates its remote HEAD and queries the
forge for every local branch tip. Every other command stays offline. The
shared `git-worktrees-sync.json` cache in the Git common directory records
remote name/URL, successful sync times and source-tip forge evidence. Sync
replaces it atomically. Remote identity changes invalidate it; malformed or
mismatched evidence is never proof.

`sync._FRESH` is 10 minutes. Warnings compare the oldest non-null component
time, with exactly 10 minutes still fresh. Quiet suppresses every warning,
the never-synced and inferred-head notices included; a removal's disclosure
still repeats them. `gw sync` and `gw version` print the head line. JSON
carries `lastSync` and `head` instead. A repository without remotes has no sync warning.

Partial-clone object reads require Git 2.45.1 onward or the maintenance
cohorts in `repo.require_offline_git`. Lower reported vendor versions refuse;
full clones keep older Git compatibility. Offline proof uses read-only
patch-id input and creates no synthetic commits.

The per-worktree checks run on eight threads. Mutations remain serial.

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
tested on what it returns rather than on its exit code. `upstream_of` takes
`ok=(0, 1, 128)`, so a typo in one of its flags exits 128, reads as "no
upstream", and turns every branch into `unknown` with nothing failing.

The network calls belong to explicit sync. Their results distinguish failed
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
absolutely. There is no flag, and `--yes` least of all.

Those seven are `RULES`, a tuple, each row carrying the name `--explain`
prints, the clause the refusal ends with, and the test. The footer `--explain`
prints is built from it. A rule written in one place and described in another
is a rule that goes quiet: the hand-written footer fell a rule behind the
moment `update-ref` was added, and nothing failed.

`verdicts.py` declares no mutating command. A read-only command may not call
one: a test reads the verbose log and asserts it.

## Verdicts and deletion are separate

`merged.branch_proof` checks matching cached forge proof, first-parent
membership, local completion proof, then remaining commits or absent evidence.
The verdicts are `remove`, `empty`, `keep` and `unknown`. A branch merged by
fast-forward without forge proof is `empty`, because Git cannot distinguish
it from an unused branch. First-parent history belongs to the assessed head,
not a divergent local counterpart.

Content proof compares changed paths, modes and deletions against the head.
It cannot settle a stacked branch whose intermediate content the head later
edited. Cached forge proof verifies source tip, source repository, target
branch and merge ancestry. Closed unmerged requests are never proof.

`eligible` combines proof, protections and `--delete` permissions.
`prune.removable()` selects only eligible rows. `main`, `master` and the
head branch are protected. Locked or dirty worktrees, untracked files and
initialized submodules block removal. Ignored paths, empty branches and
matching stashes require their own permission. `--yes` grants none.
`gwp` also protects the current worktree; `gwr` needs a second terminal
confirmation for it. `--keep-branch` retains the branch and its stashes,
needs no completion proof for the checkout, and combines only with
`--delete ignored-files`.

The removal snapshot pins the branch and head SHAs, exact path, ignored
paths and stash identities. Recheck after confirmation precedes plain
worktree removal, full-SHA ref deletion and approved stash drops. Ignored
paths are what an ignore pattern names, so a matched directory is one entry;
the snapshot is a path listing, not a recursive content fingerprint, and a
file created inside a listed directory after assessment goes with it. Stash drops verify SHA and repair a
concurrent wrong-entry drop using the exact subject. Restoration remains
possible until Git garbage collection prunes the unreachable stash commit.
Report checkout, branch and stash outcomes separately after partial failure.

Branch assessment and deletion are documented in
[branch status](docs/commands/branch-status.md) and
[branch delete](docs/commands/branch-delete.md). Checked-out branches appear
only as worktrees; `gwbd NAME` naming one keeps it and exits 3. Every JSON result is an object with `head` and `lastSync`;
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
`$XDG_CONFIG_HOME/git/config`.
