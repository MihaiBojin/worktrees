---
name: preflight
description: >
  Use when the user calls preflight by name to check or configure Worktrees
  permissions and plugin setup in Claude Code or Codex, for one project or
  the user account.
disable-model-invocation: true
argument-hint: "[user | project]"
allowed-tools:
  - Bash(gw preflight --json)
  - Bash(git rev-parse --show-toplevel)
  - Bash(jq:*)
  - Bash(mktemp)
  - Bash(diff -u:*)
  - Read
  - AskUserQuestion
---

# Set up gw and this plugin

`gw preflight --json` checks a repository: the forge's CLI, GitHub's squash
defaults, the project's Claude Code and Codex plugin setup, and the shell
plugin's release. This skill reports what it finds and writes what a file
can hold, after asking.

[rules.json](rules.json) holds Claude Code's permission rules;
[worktrees.rules](worktrees.rules) holds Codex's. They contain the allow
rules [ship-it](../ship-it/rights.md) publishes with and the deny rules
each client can match. [project.json](project.json) and
[config.toml](config.toml) hold the project's plugin setup for Claude Code
and Codex. A plugin can write none of them itself.

An allow rule lets every session the file covers run that
command without asking, not just ship-it. Write only the lines the user
approves in this run, and only after they approve them.

Use the current client's permission files. After step 2, Codex uses step 7;
Claude Code uses steps 3 to 6. Both continue with step 8.

Below, `RULES`, `PROJECT` and `CONFIG` are the absolute paths of
`rules.json`, `project.json` and `config.toml` beside this file, and `TOP`
is what `git rev-parse --show-toplevel` prints. Write each out in every
command, since nothing carries from one command to the next.

## 1. Check

```sh
gw preflight --json
```

Report every check in `checks`: its `name`, `status` and `detail`, and its
`fix` when there is one. The status is `ok`, `missing`, `wrong` or
`skipped`. Exit 1 reports setup gaps: continue with those checks. When gw
refuses at exit 3, report what it said and stop. The `codex` check covers
plugin configuration; permission rules are checked separately in step 7,
even when every CLI check is `ok`.

Two fixes are the user's to run, never this skill's:

- `forge-cli`: print the `gh auth login` or `glab auth login` command in
  `fix` for the user to run with `!`. A shell without the network, such as
  Codex's sandbox, fails this check too, so ask the user to run
  `gw preflight` in their own shell first.
- `squash-defaults`: print the `gh api -X PATCH` command in `fix` for the
  user to run with `!`. It changes the repository's settings on GitHub.
  With it, GitHub writes each pull request's title and body as its squash
  commit when `gw merge` merges a stack's lower pull requests in one call.

`shell-plugin` reads the variable the fish or zsh plugin exports. This
session's shell inherits it from the shell that started the client, or has
none, so say that `gw preflight` in the user's own shell answers for that
shell.

## 2. Which file

The argument is the scope. With none, ask which one, using the current
client's rows from this table:

| client | scope | permission file | reaches |
| --- | --- | --- | --- |
| Claude Code | `user` | `~/.claude/settings.json` | every project on this machine |
| Claude Code | `project` | `TOP/.claude/settings.json` | each trusted clone once committed |
| Codex | `user` | `${CODEX_HOME:-$HOME/.codex}/rules/worktrees.rules` | every project using that Codex home |
| Codex | `project` | `TOP/.codex/rules/worktrees.rules` | each trusted clone once committed |

Any other argument: say that it takes `user` or `project`, and stop.

`TARGET` below is the chosen file's absolute path.

## 3. Which rules are missing

Where `TARGET` does not exist, every rule is missing. Otherwise:

```sh
jq --slurpfile want RULES '$want[0].permissions as $w | {allow: ($w.allow - (.permissions.allow // [])), deny: ($w.deny - (.permissions.deny // []))}' TARGET
```

When jq cannot read the file, stop: name the file and what jq said, and
change nothing. When both lists are empty, say the file already carries
every rule, and go to step 8.

## 4. Ask about the rules

Write the file and every missing line in your reply, allow and deny apart,
then ask: add them, or skip them. Say that the allow lines let any session
that file covers commit, push, open, edit and merge pull requests, and
rerun CI, without asking.

## 5. Write the rules

Merge into a temporary file, show the change, then copy it over the target:

```sh
mktemp
jq --slurpfile want RULES '$want[0].permissions as $w | .permissions.allow = (.permissions.allow // []) + ($w.allow - (.permissions.allow // [])) | .permissions.deny = (.permissions.deny // []) + ($w.deny - (.permissions.deny // []))' TARGET > TEMP
diff -u TARGET TEMP
cp TEMP TARGET
```

`TEMP` is the path `mktemp` printed. For a `TARGET` that does not exist,
run the same program after `{} |` with `jq -n`, and `mkdir -p` the
directory before the copy.

Every other key keeps its value and its place, the rules already there keep
theirs, and each missing rule is appended once. jq rewrites the layout, with
two-space indentation. `cp` onto an existing file keeps that file's
permissions, which matters for a `~/.claude/settings.json` that holds
secrets in `env`.

Nothing here pre-approves the copy, so the client asks before it. Where it
refuses, print the `cp` command for the user to run with `!`, report the
rules as uninstalled, and continue with step 8.

## 6. Check the rules

Run step 3 again: both lists are empty. Continue with step 8.

## 7. Codex permission rules

`MERGER` is the absolute path of [merge_codex_rules.py](merge_codex_rules.py)
beside this file. `TARGET` is the Codex permission file selected in step 2.
Prepare a candidate without changing `TARGET`:

```sh
mktemp
python3 MERGER TARGET > TEMP
codex execpolicy check --rules TEMP -- gw push --json
diff -u TARGET TEMP
```

`TEMP` is the path `mktemp` printed. Quote every path. If `TARGET` does not
exist, compare `/dev/null` with `TEMP`. A diff exits 1 when files differ;
that is the candidate to review. Stop this permission write if the merger
or Codex validation fails; show the error and leave `TARGET` unchanged.
If `codex` is unavailable, report that validation needs the Codex CLI and
leave the candidate for the user to validate and install.

The merger reads the bundled rules and appends missing literal patterns
and decisions. It preserves existing text, including comments and other
rules, and ignores formatting and justification differences when matching.
It never executes the target as Python. Codex validates the candidate's
rule syntax. An existing `prompt` or `forbidden` rule remains in effect:
the strictest matching decision wins. Do not remove such a rule to make an
allow rule effective.

If the diff is empty, report that the file already carries the rules and
continue with step 8. Otherwise show the target's absolute path and every
added rule, with `allow` and `forbidden` apart, then ask to install or skip
this candidate. Explain that allow rules permit matching commands outside
the sandbox without a prompt, subject to other policy restrictions. They
cover commits, pushes, pull request creation and merging, and CI reruns
for every session within the selected scope.

After approval, create the target's parent directory if needed and copy
the reviewed candidate:

```sh
cp TEMP TARGET
```

The copy preserves an existing file's permissions. Nothing here
pre-approves the copy. If the client refuses it, report the refusal and
give the exact command for the user to run. Never report an uncompleted
copy as installed.

After a successful copy, run the merger and comparison again: the diff
must be empty. Validate `TARGET` with the same `codex execpolicy check`
command and report its decision. Tell the user to restart Codex to load
the rules. Rules in a project apply only after the user trusts it.

## 8. The project's plugin setup

With the `user` scope, skip this step and say that
`/worktrees:preflight project` writes these files. With `project`, it
writes them only for a `claude-code` or `codex` check that is not `ok`.
Ask first: name each file and show the lines from the check's `fix`, then
ask whether to write them.

For `claude-code`, `SETTINGS` is `TOP/.claude/settings.json`. Merge
[project.json](project.json) into it the way step 5 merges the rules:

```sh
mktemp
jq --slurpfile project PROJECT '$project[0] as $p | . * $p | .extraKnownMarketplaces.mihaibojin.source = $p.extraKnownMarketplaces.mihaibojin.source' SETTINGS > TEMP
diff -u SETTINGS TEMP
cp TEMP SETTINGS
```

`*` merges objects key by key, so every other key keeps its value and the
`worktrees@mihaibojin` flag takes the value in `project.json`. The
`mihaibojin` marketplace's `source` is replaced whole, so no field of
another source survives beside it. For a `SETTINGS` that does not exist, run the
program after `{} |` with `jq -n`, and `mkdir -p` the directory before the
copy. When jq cannot read the file, name it and what jq said, and change
nothing.

For `codex`, `CODEX` is `TOP/.codex/config.toml`. When it does not exist,
copy [config.toml](config.toml) there:

```sh
mkdir -p TOP/.codex
cp CONFIG CODEX
```

When it exists, print the lines for the user to add: nothing here edits
TOML in place without risking a table defined twice. Then say that Codex
reads the file only in a trusted project, and that
`codex plugin marketplace upgrade mihaibojin` clones the marketplace and
installs the plugin.

## 9. Report

Run `gw preflight --json` again and report each check that changed. Name
each file this run wrote and the lines it gained. A file under `TOP` is not
committed: say that it reaches other machines once it is merged through a
pull request. Never commit it here. Say that Claude Code loads a plugin a
project enables only once each person has installed it, with
`claude plugin install worktrees@mihaibojin --scope project`. Repeat the commands from step 1 that
are still the user's to run.
