# Reference

What is decided, what must not break, what is left.

[README.md](../README.md) says what the commands do, [AGENTS.md](../AGENTS.md)
says what rules the code follows, and [DEVELOPMENT.md](DEVELOPMENT.md) says
what to run. This file holds what none of them do: the decisions and the
alternatives they closed, the invariants that hold whatever the code is
written in, the failure classes each has a test for, and the measurements
behind the choices.

It descends from [origin#4](https://github.com/MihaiBojin/origin/issues/4), written on 2026-09-07 against a bash
CLI and two shell plugins that no longer exist, by way of
[origin#6](https://github.com/MihaiBojin/origin/issues/6). Section 10 maps that first issue's sections onto this
one.

## 1. What exists today

One implementation. The idea used to be implemented three times, and the other
two were deleted rather than ported:

| | what happened |
| --- | --- |
| zsh and fish `git-worktree` in `shell-plugins` | dropped at `08b3de9`, 2026-09-11 |
| the bash `origin` CLI in `agent-plugins` | dropped its worktree commands at `89e2907`, 2026-09-09 |

`origin` is still built, and still does `new-branch`, `rotate`, `sync`,
`merge` and `ci`. It has no worktree command, and its skills say so.

What is here:

| | where |
| --- | --- |
| the CLI | `src/worktrees/` |
| the suite | `tests/` |
| the fish half | `functions/`, `completions/`, `conf.d/` |
| the zsh half | `zsh/plugins/worktrees/` |

Eleven console scripts: `gws`, `gwp`, `gwa`, `gwl`, `gwm`, `gwr`, `gwnb`,
`gwrot`, `gwh`, `gw` and `worktrees`. Four of them ship a shell function of
the same name, one per shell, because a binary cannot `cd` its caller.

The distribution is `git-worktrees` on PyPI, installed with
`uv tool install`. The shell half arrives separately, through Fisher for fish
and Antidote for zsh, because neither plugin manager puts a binary on `$PATH`.
Python 3.11 or newer, and no runtime dependencies.

## 2. The contract

One CLI holds the behaviour. Everything else is a thin caller.

| consumer | what it gets | what it must never need |
| --- | --- | --- |
| a person at a terminal | nine `gw`-prefixed names and `gw` itself, a numbered picker, colour, `cd` | to know a Python package is under it |
| a shell | one path on stdout, `--complete` for candidates | to hold a candidate, a rank or a format string |
| an agent | `--json` on every command but `gwh`, and refusals on stderr | to parse a table meant for a person |

Four rules the CLI does not break:

- Data on stdout, diagnostics on stderr, prompts included. That is what makes
  `cd "$(gwa foo)"` work and `--json` parseable while the same run explains
  itself to a person.
- Worktrees live at `<PARENT>/.worktrees/<NAME>/<REPO>`, derived rather than
  configured, so two independently-invoked tools cannot disagree about a
  location neither can be told. A branch name with slashes nests, and the
  repository name goes last, so `auth/oauth` cannot collide with `auth`.
- Every git command the program can run is one decorated spec, written the way
  you would type it, and all 35 of them register in one list. A guard reads the
  resolved argv inside the wrapper, so no call site can assemble its way past a
  refusal. `gws --explain` prints the list, marking the 11 that mutate.
- There is no configuration. No file, no environment variable beyond
  `NO_COLOR` and `TERM`, and no `git config` key of this program's own. The
  two git keys it reads, `checkout.defaultRemote` and `branch.<name>.remote`,
  are git's and answer a question git already asks.

## 3. Decisions

### 3.1 Settled

Scope and safety.

| what | why | where |
| --- | --- | --- |
| Every command acts on the repository `$PWD` is in, and a checkout belonging to another is refused by name | a tool that can act on a repository it is not in can do so by mistake | `layout.owner_of`. `git worktree list` cannot answer this: another repository's worktree is one this one never heard of, so the list comes back empty and reads as "nothing is there" |
| Nothing removes a worktree holding uncommitted work, and no flag overrides it | the checkout is the only place that work exists | `verdicts.assess` keeps it, and `--force` does not reach git: the removal is always plain `git worktree remove`, which refuses a dirty worktree itself |
| Every removal names the ignored paths it is about to delete, whatever flag got the worktree that far | `git worktree remove` takes the whole directory, so consent and contents are two questions | `prune.plan`. `--delete-ignored` decides consent alone |
| `gwr` on the worktree you are standing in steps out, removes it, and prints the main checkout for the shim to `cd` to | refusing would make the common case need a `cd` first, and the `cd` out is the shim's job | `worktree.remove` |
| The main checkout is never removable, and no escape-hatch flag is printed | `git worktree remove` answers `fatal: '<path>' is a main working tree` and no flag gets past it, so there is nothing to offer | `verdicts.assess` skips it before judging |
| A branch is deleted on this program's own proof, or not at all | a squash merge leaves no history, so deferring to `git branch -d` means the content probe buys the checkout and never the branch | `prune.ref_delete`, and §3.2 for what it replaced |
| A stale record for a directory somebody deleted by hand is nobody's verdict | clearing one mutates, so the command that asks may not do it | `verdicts.stale` names them, `gwp` runs `git worktree prune` |

Flags and commands.

| what | why | where |
| --- | --- | --- |
| `gws` reports and `gwp` acts, under one function that returns the set | what one prints and the other removes cannot drift | `prune.removable` |
| There is no `--dry-run`, and passing it is refused before the parser sees it | a flag meaning "do not act" on a command that already asks is a no-op wearing the clothes of a safety feature, and somebody will cite it as the reason a sweep was safe | `cli._dispatch` |
| `gwp` with nothing removable prints the verdict table | the reason each worktree stayed is the answer to the question, and naming another command puts it one round trip away | `cli.run_prune` |
| One row per command carries its function, flags, description, shorthands and whether it lands you somewhere | `gws`, `gw status`, `gw st` and `gw s` reach one function because the row says so, and `gwh` prints the row rather than a second list | `cli._COMMANDS`, and `_canonical()` raises at import when two rows claim one name |
| The head-branch ladder tries `refs/remotes/<remote>/<b>` before `refs/heads/<b>` and returns a full ref | returning a bare name is what lets a tag named `origin/main` answer for the head branch | `repo.full_ref`, `repo.head_ref` |
| `gwa` and `gwnb` fetch the whole remote with `--prune` rather than the base branch alone | base-only fetching forks a second divergent branch where the remote already had one, and the network cost is the round trip rather than the ref count | `repo.fetch` |
| Branches this tool starts do not track the head branch | a branch off `refs/remotes/<remote>/main` that tracked it would take it as its upstream, and `git push` would target the head branch | `--no-track` on `worktree_add` and `switch_create` |

Output and machine surface.

| what | why | where |
| --- | --- | --- |
| Three verdicts, and `unknown` is not `keep` with a softer word | "no upstream, so nothing says whether this was pushed" is a different fact from "not merged", and printing them alike invites somebody to act on the wrong one | `verdicts.py` |
| Colour is decided per stream, once, and only for a terminal | stdout carries the path `cd $(gwa x)` reads and the JSON `jq` parses | `render.setup`. `NO_COLOR` is tested for a non-empty value, which is the no-color.org rule |
| Padding runs on the text and painting after it | the other order counts an escape sequence as width, and every column under a coloured cell sits crooked by the length of the code | `render.table` |
| Help asked for goes to stdout at exit 0; a usage printed because the invocation was wrong goes to stderr at exit 2 | `--help \| less` on an empty screen is the failure this avoids | argparse, plus the hand-written `usage:` lines |
| Completions hold no candidate of their own | `gw --complete`, `gwl --complete` and `gwr --complete` print name and description, tab separated, which fish reads directly and zsh splits for `_describe` | a command added to the table needs no edit in either shell |
| The picker is Python's, and there is no fzf | the largest number of linked worktrees in one repository here is four, and at that size a numbered prompt reads faster and costs no spawn, no tty rules and no absent-fzf fallback | `pick.py`, about twenty lines of substring-then-subsequence matching |
| No terminal, no prompt | an agent or a pipe reaching a prompt would hang forever holding the repository's worktrees | `pick.choose` and `prune.confirm` both refuse and name the flag that answers without one |

Language, distribution, packaging.

| what | why | where |
| --- | --- | --- |
| Python, standard library only | an invocation costs an interpreter and nothing else, and the standard library answers every question this asks | `pyproject.toml` declares no dependencies. §7 for what an invocation actually costs |
| The version is static in `pyproject.toml` and read back through installed metadata | a tag agrees with the version the commit carries or nothing is published | `worktrees.__version__`, and `rt git::assert_tag_version` in `publish.yml` |
| The distribution is `git-worktrees`; the import package and every command keep their own names | PyPI already holds `worktrees` | |
| TestPyPI gates the real upload | a PyPI upload cannot be undone or replaced, so a failed rehearsal stops the run while there is still nothing to pin against | `publish.yml`, and the merge happens before the irreversible step |

### 3.2 Settled differently from the record that preceded this

Each was settled in [origin#4](https://github.com/MihaiBojin/origin/issues/4) and is settled the other way here.
The third column is the reason, in the present tense, so nobody re-derives
the first.

| origin#4 said | here | why |
| --- | --- | --- |
| A branch is deleted only on git's own proof, and `branch -D` is refused absolutely | a branch is deleted by `git update-ref -d <ref> <sha>` against the sha the verdict was formed on | `git branch -d` reads history, a squash merge leaves none, and it inverts on exactly the case this tool exists for. `branch -D` stays refused, and so does `update-ref -d` with an absent, empty or abbreviated old value: git reads the empty string as "no old value" and takes the branch at exit 0 |
| Configuration is `git config git-worktree-plugin.*` and nothing else | there is no configuration | one implementation cannot disagree with itself, so a key that only reconciles two of them has nothing left to do. `git-worktree-plugin.forge`, `.remote` and `.headBranch` are gone with the shells that read them |
| The CLI prints candidates and the shim runs fzf | the CLI runs the picker and the shim only reads a path | dropping fzf removes the spawn, the tty rules around it and the fallback branch a machine without it needs |
| Go, once the command surface stops moving | Python | the surface did not stop moving, and the case for Go was made against a bash CLI with a shell shim around it. Python costs 67 ms an invocation, paid once per command a person types, and buys a standard library that answers every question this asks with no dependency and no build |
| Completions are generated from the CLI's own flag declarations | completion files are written by hand and hold their own command's flags; candidates come from `--complete` | `tests/test_completions.py` diffs the flags against `--help` in both directions for both shells, so a renamed flag fails there rather than going quiet |
| Every command takes `--json` on one envelope carrying `schema`, `command`, `ok`, `refusals`, `losing` and `result` | every command but `gwh` takes `--json`, and each prints the shape its own answer has | no envelope, no `schema` field, and no version on the payload |
| `--force` retires into `--remove-unfinished-checkout` and `--remove-detached-checkout`, and `--keep-branch` opts out of a branch delete | `gwr --force` removes the checkout and keeps the branch | the worktree was in the way, the work was not. There is one flag and it names one outcome |
| The spinner lives in the binary and animates when stdout is a terminal | there is no spinner | |
| The binary is called `origin`, and the plugin is `origin@origin-plugin` | the CLI is `worktrees`, reachable as `gw`, and there is no plugin | the two repositories split instead of merging |
| Homebrew is recorded and unbuilt; `install.sh` covers the case that exists | `uv tool install git-worktrees` covers it, and nothing installs from a checkout | the installer and its uninstall asymmetry are gone with it |

### 3.3 Withdrawn, or believed and false

| | what is true |
| --- | --- |
| `origin shell-init` | never built. A shim sources its own file |
| Shared directories, and the dotfile for them | dropped, along with the distinction the dotfile existed to draw. `--delete-ignored` gates the whole set at the removal path instead. The trap that killed the idea is still live: on git 2.55.0, `.claude/` in a `.gitignore` does not match a symlink and `.claude` does, so a symlinked shared directory under the conventional spelling shows as `?? .claude`, the worktree reads dirty, and it becomes unremovable by its own tooling |
| "A worktree that is not clean is never removed without an explicit flag" | uncommitted work is refused unconditionally. No flag exists, and `gwr --force` does not reach one: the removal is plain `git worktree remove`, which refuses it itself |
| "`gwl --list` is 58 ms against `origin gwl`'s 1.94 s" | not like-for-like then, and not a live comparison now. §7 has today's figures |
| "The bats suite is the specification for the port" | there is no port and no bats suite. `tests/` is the specification, and it was written against this code |
| `origin worktree path <branch>` | not built. `gwl --list` and `gwl --json` answer it |
| `origin prune` is read-only until `--yes` | `gwp` runs `git worktree prune` and `git fetch --prune` before it assesses. Both mutate. `--yes` gates the checkout and the branch delete, and nothing else |
| "Every mutation stays serial" as a property of the program | true within one process and asserted nowhere across two. Nothing takes a cross-process lock |

## 4. What must not break

Two gates, from reproduced failures. Both are invariants rather than
decisions: no reading of any decision above makes either one wrong.

**Gate 1: a ref that names a branch resolves to that branch.** Assert on the
literal sha, not on the word `restore`: the bug's signature is a correctly
shaped restore line carrying the wrong sha, so a test that checks the sentence
passes while the bug is live. Every printed sha has to satisfy
`git cat-file -t` = `commit`.

`prune.restore_line` does that check. Every merge question names
`refs/heads/<branch>`, and `repo.full_ref` returns a full ref for the head
branch.

**Gate 2: the head branch survives whatever the verdict says.**
`verdicts.assess` drops it before judging, and the head ref it compares
against is a full ref, so a tag or a local branch called `origin/main` cannot
answer for it.

Beyond the gates:

- Every mutating git call goes through one guard, on the resolved argv inside
  the wrapper. `reset --hard`, a forced `checkout` or `switch`, `clean -f`, a
  bare `push --force`, `worktree remove --force`, `branch -D` and an
  `update-ref` delete that does not name a full sha are refused absolutely. No
  flag reaches them, and `--yes` least of all.
- Anything about to be deleted is printed first, with the command that puts it
  back, whatever `--quiet` and `--yes` say. That line is the only way back now
  that the branch goes with its checkout. Ignored paths are printed as
  unrecoverable, because no ref ever pointed at them and nothing restores them.
- `verdicts.py` declares no mutating git command.
  `test_no_unexpected_git_command_ran` reads the call log and asserts every
  subcommand issued is one the registry declares, and
  `test_status_names_a_stale_record_rather_than_clearing_it` asserts the
  observable half, that `gws` leaves a stale record where it found it. The
  stronger property is not asserted anywhere: that a read-only path issues no
  command the registry marks `mutates=True`. The registry knows, so it is one
  assertion.

Four behaviours the field survey found nowhere else, of which three are here:

| | state |
| --- | --- |
| a merged request as evidence, cross-checked against unpushed commits, with "no upstream" treated as unknown rather than zero | `forge.py` and `verdicts._forge_reason` |
| a worktree belonging to another repository, which makes `git worktree list` return empty and so reads as "nothing is there" | `layout.owner_of`, reached from `gwa` |
| the derived layout | `layout.destination` |
| stash entries parked on a branch counting as work | **not needed.** The entry survives the removal and `git stash branch` recovers it, so there is nothing to keep. #39 names it instead |

## 5. Failure classes that stay tested

Every row below was run. `held` means the behaviour was reproduced against
this CLI on 2026-09-11 and it did the right thing; the last column says
whether a test in `tests/` would catch a regression.

`gitrevisions(7)` resolves a short name as `refs/<name>` before
`refs/tags/<name>` before `refs/heads/<name>`, so A4 and A6 fire with no tag
involved.

| | state in the repository | today | test |
| --- | --- | --- | --- |
| A1 | tag `feature`, unmerged branch `feature` | held, `unknown` | `test_a_tag_cannot_answer_for_a_branch` |
| A2 | the same, annotated | held: the restore line carries the branch's commit, not the tag object | `test_every_printed_sha_is_a_commit` |
| A3 | tag `main`, no remote, head ref is the bare name | held: `full_ref("main")` is `refs/heads/main` | `test_the_head_ref_is_never_a_bare_name` |
| A4 | branch `x` and branch `heads/x` | held, both `unknown` | none |
| A5 | tag `foo` and branch `tags/foo` | held, `unknown` | none |
| A6 | branch named a full 40-hex sha | held: judged `squash-merged`, and the restore line carries the branch's own sha | none |
| A7 | tag `feature` outside the head branch, branch `feature` merged | held: reaped, reason `merged` | none. `test_a_tag_cannot_answer_for_a_branch` runs A1's direction only |
| A8 | branch `weird>pwned`, a name a shell redirects on | held: the restore line and the `gwnb` hint quote the name, and bash, zsh and fish read the quotes alike | `test_the_restore_line_is_safe_to_paste`, `test_a_restore_line_means_the_same_in_every_shell` |

A7 is the class running the other way. A suite that only asserts "never delete
unmerged work" passes while it holds.

| | state in the repository | today | test |
| --- | --- | --- | --- |
| B1 | head branch `main` in a linked worktree | held: not proposed | `test_the_head_branch_is_never_proposed` |
| B2 | plus a tag named `origin/main` | held | `test_a_tag_shadowing_the_head_branch_does_not_shadow_it` |
| B3 | plus a branch named `origin/main` | held: `refs/remotes/origin/main` wins the ladder | none |
| B4 | `git config git-worktree-plugin.headBranch origin/main` | gone. There is no such key | |
| B5 | `headBranch` naming a ref that does not exist | gone with B4 | |
| B6 | `origin/HEAD` unset, no remote-tracking refs, remote unreachable | held since #46: the fetch and the `set-head` behind it answer 128 rather than raising, and the command says so and judges from the refs already here | `test_a_failed_fetch_still_answers_status` |
| B7 | detached-HEAD worktree | held: named, and judged on whether a ref reaches its commit | `test_a_detached_worktree_is_named_not_mistaken` |

B2 and B3 are why the head-branch ladder returns a full ref.
`git symbolic-ref --quiet --short` shortens to the shortest *unambiguous*
form and returns `remotes/origin/main`, which an `origin/` strip then misses.
`git tag origin/main main` puts the tag where both candidate answers agree, so
it passes on unfixed code forever; `git tag origin/main feature` is the
spelling that makes the resolution observable. Assert that the shadow shadows
before asserting the fix.

Classes that changed shape when the implementation did:

| class | today |
| --- | --- |
| SIGPIPE 141 from a shell function piped into an `awk` that exits early | gone. There are no shell pipelines. Nine worktrees into `head -1` exits 0 with no traceback |
| A path containing a newline | alive in the shims alone. fish splits command substitution on newlines, so `string collect` at one site per function, and that pipeline's `$status` belongs to `string collect`, which returns 1 whenever it collected nothing. `$pipestatus[1]` is the command's own. zsh needs neither |
| Ignored files | alive and tested twice: the raw `git worktree remove` behaviour, then the fix |
| A trailing slash in a `.gitignore` pattern not matching a symlink | gone with shared directories |
| An uninstall that deletes any symlink of the right name | gone. `uv tool` owns the links |

Two holes in git are the reason this exists, and each has a test that
reproduces the raw git behaviour beside the test that asserts the fix. A suite
that only asserts the fix passes on unfixed code forever.

## 6. How it is tested

One module per concern, and a `conftest.py` holding the repository fixture
and the environment a driven test runs under. Most of them drive a console
script out of process, because exec is the only way env, cwd, both streams
and the exit code stay per-test; the ones that ask about a prompt drive a
real pty, because a test that fakes the terminal tests the fake.

The fixture needs no global git config, no signing key, no forge and no
network. `GIT_CONFIG_GLOBAL` carries the weight: without it git still reads
`$HOME/.gitconfig`, and setting `HOME` alone still leaves
`$XDG_CONFIG_HOME/git/config`. On a machine with `commit.gpgsign=true` a
fixture that inherits it fails at the first commit and every assertion after
that is meaningless. `GIT_CONFIG_SYSTEM=/dev/null` and `GIT_CONFIG_NOSYSTEM=1`
close the other two.

Rules that each have a failure behind them:

- Drive the console script through `subprocess` wherever the answer is an exit
  code, a stream or a directory. Exec is the only way env, cwd, both streams
  and the exit code stay per-test.
- Drive a real pty where the answer depends on a terminal. `pick.choose` and
  `prune.confirm` both branch on `isatty`, and a test that fakes it tests the
  fake.
- A stub `gh` on `PATH` is what makes the forge assertable without a network.
  It is a shell script that prints the rows the test wants.
- Every refusal assertion needs a canary the run must have issued. An empty
  call log is indistinguishable from a CLI that never reached git, and
  `test_no_unexpected_git_command_ran` asserts `len(options.log) > 5` before
  it asserts anything else.
- Fixed `GIT_AUTHOR_DATE` and `GIT_COMMITTER_DATE` make the first commit's sha
  a constant, so a test may assert on a literal sha.
- `worktrees.git.options.log` records every argv the process issued, which is
  what makes "no `branch -D` was issued" assertable. It only reaches
  in-process tests; a subprocess test asserts on output and on the filesystem.

CI runs `ubuntu-latest` on Python 3.11, 3.12, 3.13 and 3.14, with `ruff` and
`mypy` through `pre-commit` on 3.14. `uv sync --locked` fails the run on a
stale `uv.lock` rather than resolving something the lock never described.
Neither fish nor zsh is on that image, so `tests.yml` installs both and the
assertions that drive a real shell turn their `pytest.skip` into a
`pytest.fail` when `CI` is set. A skip is invisible in `pytest -q`, so a green
run used to mean nothing about either shim.

## 7. What an invocation costs

[`BENCHMARK.md`](BENCHMARK.md) holds the figures and
`scripts/benchmark.py` regenerates them, so this section names what is worth
timing rather than carrying a second copy of the numbers.

The only timing worth keeping is the cost of starting. Everything else this
program does is a `git` subprocess or a forge round trip, and both are facts
about a repository and a network rather than about the code: a monorepo and a
five-file repository do not agree, and neither do two runs on the same laptop.

Every column is a uv-managed interpreter. Without that a run compares
packagers rather than versions: Homebrew's 3.14 `dlopen`s 76 extension modules
where uv's builds link all but two into the executable, and on macOS each
`dlopen` pays a code-signature check, which reads as an 8 ms CPython
regression and is not one.

Where it stood on 2026-09-12, against `45ba3f2` on an Apple silicon Mac, 50
runs each:

| | 3.11.13 | 3.12.14 | 3.13.15 | 3.14.7 |
| --- | --- | --- | --- | --- |
| the interpreter alone | 22.8 ms | 24.3 ms | 25.1 ms | 25.7 ms |
| `gw version` | 30.5 ms | 33.4 ms | 32.7 ms | 43.1 ms |
| `/bin/echo`, for scale | 3.2 ms | 3.3 ms | 3.0 ms | 3.1 ms |

3.14 is the outlier and argparse is why. Constructing the first
`ArgumentParser` pulls 33 modules there against 10 on 3.13, because 3.14
colours its help: `_colorize` brings `annotationlib`, `ast`, `dis`,
`inspect`, `dataclasses`, `tokenize` and `compression.zstd` with it. That is
10.6 ms on the first construction and 0.14 ms on every one after. It also
undoes part of what keeping `dataclasses` and `inspect` off the import path
bought: on 3.14 both are loaded by the time `gw version` has printed, and on
3.13 neither is.

### How many git calls, which is a count rather than a clock

Counts travel between repositories where milliseconds do not. Over nine
linked worktrees, `gws --no-fetch --no-forge` issues 26:

| | count |
| --- | --- |
| `merge-base --is-ancestor`, once per worktree | 9 |
| `--no-optional-locks status --porcelain --ignored=traditional`, once per worktree | 9 |
| `show-ref --verify`, the head-branch ladder | 4 |
| `worktree list --porcelain -z` | 2 |
| `rev-parse --show-toplevel` and `remote` | 2 |

Eighteen of those were `status`, twice per worktree, until `#40`. They are
serial: one process each, nothing overlapped.

### What may run at once

Concurrency here is about locks rather than about reads. `git status` does
write the index, and every linked worktree has its own at
`$GIT_DIR/worktrees/<id>/index`, so parallelising it is safe.

| call | lock | may overlap |
| --- | --- | --- |
| `rev-list`, `for-each-ref`, `config --get`, `symbolic-ref`, `show-ref`, `rev-parse`, `log`, `worktree list` | none | freely |
| `git -C <path> status --porcelain` | that worktree's own `index.lock` | with each other |
| the same with `--no-optional-locks`, which is what this runs | none; the index mtime does not move | and cannot contend with a `git add` in that worktree |
| `fetch`, `worktree add \| remove \| move`, `update-ref`, `branch --move`, `switch` | shared refs, `$GIT_DIR/index`, `worktrees/` | no |

`gws --explain` marks the last row's commands with `!`, and there are 11 of
them. Nothing overlaps today; the table says what could.

## 8. What is left

Known and unfixed:

| | |
| --- | --- |
| a merged request stops counting once the merge deletes the remote branch | `fetch --prune` drops the remote-tracking ref the merge removed, `unpushed_count` answers `None`, and a branch that is definitely merged reads `unknown` for good. #47 |
| `status` cannot judge a branch with no worktree | #29 |
| `gwnb` and `gwrot` are alpha and may go | they start branches rather than worktrees, `origin new-branch` and `origin rotate` already do that, and only one of the two sets survives |

Nine rows left this table in #46, merged 2026-09-11 as `daa8284`:
the unreachable remote (#35), `gwr`'s forge scope and `--no-forge`
(#36), one exit code for a missing terminal (#37), the
version in the wheel (#38), the stash named before its branch goes
(#39), one `git status` per worktree (#40), fish and zsh
in CI (#41), the guard's rules as data (#42) and `shutil`
imported where it is used (#43). §1, §6 and §7 are measured against
`a37af6e` and have not been taken again since.

Recorded and not planned:

- Onboard an existing worktree. One made by hand is invisible to anything that
  assumes the derived layout. Either it is adopted, or the tools keep a list of
  the ones they did not make and say so rather than silently ignoring them.
- A daemon that prepares worktrees before they are wanted, which matters most
  on the large repositories where `gwa` is slowest.
- A cross-process lock. Two `gwp` runs in one repository are serial only by
  luck.
- Windows. The CLI shells out to `git` and reads `isatty`, and nobody has run
  it there.

## 9. Surfaces nobody has designed yet

Both agent clients have worktree machinery of their own, and neither is wired
to any of this.

Claude Code has `WorktreeCreate` and `WorktreeRemove` hook events, added in
2.1.50 and fixed for plugin-declared hooks in 2.1.69. Registered in a plugin's
`hooks/hooks.json` or in settings, `WorktreeCreate` replaces the default
`git worktree add` whenever `claude --worktree <name>` runs or `EnterWorktree`
is called mid-session. 2.1.84 added a `type: "http"` variant that returns the
path through `hookSpecificOutput.worktreePath`. Recorded and not verified here:
the payload arrives as JSON on stdin with `worktree_name` and an optional
`base_commit`, and a command hook must print only the resulting directory path
to stdout.

That hook is where `gwa` belongs. It would give an agent session the same
layout, the same refusals and the same restore lines as a terminal, instead of
a default `.claude/worktrees/<name>` nothing else knows about. Whatever is
built there has to constrain the name and the returned path to the current
repository and the derived root: the name reaches `git worktree add` and a
branch name is not a safe path component by default.

Claude Code also has `.worktreeinclude`, which copies files into a new
worktree, for things like `.env`. It overlaps with what the dropped shared
directories were reaching for, and copying a secret needs an opt-in, a
retention rule and a deletion rule before it is a good idea.

Codex manages worktrees and gives nothing to override where they go. On
codex-cli 0.154.0, `--worktree` runs the session in a new managed git worktree,
there is no `worktree` subcommand, and `~/.codex/worktrees` does not exist. The
`codex worktree list|path|remove|prune` design [origin#4](https://github.com/MihaiBojin/origin/issues/4) recorded
never shipped. Until there is a hook, a Codex session's worktree is somewhere this
tool does not know about.

## 10. Concordance to #4

[origin#4](https://github.com/MihaiBojin/origin/issues/4) is closed and nothing cites it any more. Its sections map
onto this file as follows.

| origin#4 | here |
| --- | --- |
| §1, three implementations and their counts | §1. Two of the three are deleted |
| §2, the contract | §2. The configuration rule reversed |
| §3.1, settled | §3.1 for what carried, §3.2 for what did not |
| §3.2, withdrawn or false | §3.3 |
| §4, the two gates and what must not break | §4, both gates held |
| §5, the A and B failure classes | §5, with B4 and B5 retired and B6 open |
| §6, how it is tested | §6. The Go fixture rules became pytest rules |
| §7, measurements | §7. All of the figures are new |
| §8, seven milestones in `docs/plan/` | retired at 90e5108. §8 is today's list |
| §9, agent client surfaces | §9, re-checked |
| §10, the concordance to its own item numbers | not carried. [origin#1](https://github.com/MihaiBojin/origin/issues/1) and [origin#2](https://github.com/MihaiBojin/origin/issues/2) are closed |
