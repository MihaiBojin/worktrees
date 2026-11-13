# The rules ship-it needs

A plugin cannot grant its own skill the right to push or merge, in either
client, so these rules go in the user's own configuration, once. ship-it
checks for them before it starts and names any that are missing; it never
writes them.

Each rule matches a command's prefix. A rule for
`git push --force-with-lease --force-if-includes` also admits a trailing
`--force`. The skill never writes that flag, and `tests/test_plugin.py`
checks its commands. The deny rules refuse the forms a client can match.

The `git fetch` allow rule also admits `+` refspecs, `--force` and `-f`,
and destinations under local `refs/heads/`. Claude Code's deny patterns
cover unquoted `+` refspecs and standalone force flags in the listed
positions. Codex's deny rule covers `--force` and `-f` immediately after
`fetch`; its literal prefix matcher cannot cover arbitrary `+` refspecs or
force flags after a remote or another option. Those forms and local branch
destinations still match the allow prefix. The skill writes none of them,
and `tests/test_plugin.py` checks every fetch command it supplies.

`gw merge` is the GitHub merge command in both clients. Its parser requires
a full head SHA, always requests squash, and accepts no rule bypass or
arbitrary API payload. The rule admits only this command; it grants no
general `gh api` permission. Install the CLI version that provides
`gw merge` along with the updated plugin. A plugin update alone does not update the CLI.

## Claude Code

[`preflight/rules.json`](../preflight/rules.json) holds them: the allow
rules for `permissions.allow` and the deny rules for `permissions.deny`, in
`~/.claude/settings.json` or the project's `.claude/settings.json`.
`/worktrees:preflight user` or `/worktrees:preflight project` adds the
missing ones after showing them. The allow rules are what lets a merge
through auto mode, whose classifier otherwise blocks merging a pull request
no human approved; a skill's own `allowed-tools` lasts only the turn that
invoked it.

No allow rule or skill overrides a deny rule. A deny rule matches only the
position its pattern names, so test one on the Claude Code version in use
before relying on it.

## Codex

Codex runs a command these rules allow outside its sandbox, without asking.
That matters twice over: `workspace-write` has no network, and it keeps
`.git` read-only, including the git directory a linked worktree's `.git` file
points to, so a commit, a rebase and `gw`'s refs and sync cache all need it.
Put them in `~/.codex/rules/worktrees.rules`, or in a trusted project's
`.codex/rules/`, then restart Codex.

```python
prefix_rule(
    pattern=["gw", "branch", "create"],
    decision="allow",
    justification="ship-it starts a branch",
)
prefix_rule(
    pattern=["gw", ["fast-forward", "ci-checks"]],
    decision="allow",
    justification="ship-it fetches, rebases and reads CI",
)
prefix_rule(
    pattern=["git", ["add", "commit"]],
    decision="allow",
    justification="ship-it commits by path",
)
prefix_rule(
    pattern=["git", "rebase", "--continue"],
    decision="allow",
    justification="ship-it finishes a resolved rebase",
)
prefix_rule(
    pattern=["git", "push", "--set-upstream"],
    decision="allow",
    justification="ship-it pushes a new branch",
)
prefix_rule(
    pattern=["git", "push", "--force-with-lease", "--force-if-includes"],
    decision="allow",
    justification="ship-it pushes a rebased branch with a lease",
)
prefix_rule(
    pattern=["gh", "pr", ["view", "diff", "create", "edit"]],
    decision="allow",
    justification="ship-it opens and describes the pull request",
)
prefix_rule(
    pattern=["gw", "merge"],
    decision="allow",
    justification="ship-it merges one checked GitHub pull request",
)
prefix_rule(
    pattern=["gh", "run", ["view", "rerun"]],
    decision="allow",
    justification="ship-it reads and reruns CI",
)
prefix_rule(
    pattern=["git", "fetch"],
    decision="allow",
    justification="ship-it fetches the rewritten head and its base",
)
prefix_rule(
    pattern=["glab", "mr", ["view", "diff", "create", "update", "merge"]],
    decision="allow",
    justification="ship-it opens and merges the merge request",
)
prefix_rule(
    pattern=["glab", "ci", ["trace", "retry"]],
    decision="allow",
    justification="ship-it reads and reruns CI",
)
prefix_rule(
    pattern=["git", "fetch", ["--force", "-f"]],
    decision="forbidden",
    justification="Fetch without force into remote-tracking refs.",
)
prefix_rule(
    pattern=["git", "push", "--force"],
    decision="forbidden",
    justification="Use --force-with-lease --force-if-includes.",
)
```
