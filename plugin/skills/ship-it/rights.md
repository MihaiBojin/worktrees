# The rules ship-it needs

A plugin cannot grant its own skill the right to push or merge, in either
client, so these rules go in the user's own configuration, once. ship-it
checks for them before it starts and names any that are missing; it never
writes them.

Each rule matches a command's prefix, so a rule for `gw push` also admits a
trailing `--replace`. The skill never writes that flag, or `--force`, and
`tests/test_plugin.py` checks its commands. Every push goes through
`gw push`, which refuses one that would delete a commit only the remote
holds or bring back commits the head branch merged, and every rebase
through `gw fast-forward`. Both clients deny direct `git rebase`,
`gh pr merge` and `gh stack merge` commands. Claude Code also denies the
forced push forms listed in [rules.json](../preflight/rules.json) and
`gh api` calls whose command text names a merge. Codex denies every direct
`git push` and `gh api` command.

`gw merge` is the GitHub merge command in both clients. It merges the pull
requests it names, a stack bottom first, each only once its head and CI are
checked; with one number its parser requires the full head SHA. It always
requests squash, and accepts no rule bypass or arbitrary API payload. The rule admits only this command; it grants no
general `gh api` permission. `gw stack-link` is the command that links a
stacked pull request; it takes pull request numbers and nothing else.
Install the CLI version that provides `gw merge` and `gw stack-link` along
with the updated plugin. A plugin update alone does not update the CLI.

## Claude Code

[`preflight/rules.json`](../preflight/rules.json) holds them: the allow
rules for `permissions.allow` and the deny rules for `permissions.deny`, in
`~/.claude/settings.json` or the project's `.claude/settings.json`.
`/worktrees:preflight user` or `/worktrees:preflight project` adds the
missing ones after showing them. The allow rules are what lets a merge
through auto mode, whose classifier otherwise blocks merging a pull request
no human approved; a skill's own `allowed-tools` lasts only the turn that
invoked it.

No allow rule or skill overrides a deny rule, and a deny rule matches past
a leading assignment, so `Bash(git rebase *)` refuses
`GIT_EDITOR=true git rebase --continue` too: `gw fast-forward --continue`
runs it instead. A deny rule matches only the position its pattern names,
so test one on the Claude Code version in use before relying on it.

## Codex

Codex allow rules permit matching commands outside its sandbox without
asking, subject to other policy restrictions. A sandbox that blocks network
access or keeps `.git` read-only needs these rules for fetches and Git
writes, including writes to a linked worktree's shared Git directory.

[`preflight/worktrees.rules`](../preflight/worktrees.rules) holds the Codex
rules. In Codex, `/worktrees:preflight user` adds them under
`${CODEX_HOME:-$HOME/.codex}/rules/worktrees.rules`;
`/worktrees:preflight project` adds them to `.codex/rules/worktrees.rules`.
The skill prepares a merged file, validates it with `codex execpolicy check`,
and shows the additions before asking to copy it. Existing rules stay in
place, and the strictest matching decision wins. Restart Codex after
installation. Project rules load only in trusted projects.

A Codex rule matches whole words from the start of the command. Denying
`git push` covers every trailing argument, including
`--force-with-lease=REF:SHA`, flags after the remote and `+` refspecs. This
also denies ordinary pushes and tag pushes. Denying `gh api` covers every
endpoint and GraphQL payload, including reads. The checked `gw` commands
remain allowed; their child processes are not separate tool requests.

Codex applies the strictest matching decision, so these denies also win
over a broader allow rule. Verify the installed rules without running a
push or merge:

```sh
codex execpolicy check --rules ~/.codex/rules/worktrees.rules -- git push origin branch --force-with-lease=refs/heads/branch:SHA
codex execpolicy check --rules ~/.codex/rules/worktrees.rules -- gh api repos/OWNER/REPO/pulls/NUMBER/merge -X PUT
```

Both results must report `"decision":"forbidden"`. Include every active
rule file with another `--rules` argument when testing combined policy.

[Codex rules](https://developers.openai.com/codex/rules) control command
requests outside the sandbox; they do not inspect arbitrary programs or
scripts. A prefix such as `git -C PATH push` does not match `git push`.
Aliases, other API clients and scripts can also fall outside these rules.
An unmatched command follows the client's remaining policy; a prompt is
not guaranteed. The `gw push` allow rule also admits `--replace`, which
the skills prohibit but these prefix rules cannot exclude at every
argument position. Keep the sandbox and approval policy in place; do not
treat these rules as complete enforcement of every operation an agent can
perform.
