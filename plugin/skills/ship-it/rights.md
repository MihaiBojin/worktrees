# The rules ship-it needs

A plugin cannot grant its own skill the right to push or merge, in either
client, so these rules go in the user's own configuration, once. ship-it
checks for them before it starts and names any that are missing; it never
writes them.

Each rule matches a command's prefix. A rule for
`git push --force-with-lease --force-if-includes` also admits a trailing
`--force`, and one for `gh pr merge` also admits `--admin`. The skill never
writes either, `tests/test_plugin.py` holds its text to that, and the deny
rules refuse the forms a client can match.

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
    pattern=["gh", "pr", ["view", "diff", "create", "edit", "merge"]],
    decision="allow",
    justification="ship-it opens and merges the pull request",
)
prefix_rule(
    pattern=["gh", "run", ["view", "rerun"]],
    decision="allow",
    justification="ship-it reads and reruns CI",
)
prefix_rule(
    pattern=["gh", "repo", "view"],
    decision="allow",
    justification="ship-it reads the merge strategies",
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
    pattern=["git", "push", "--force"],
    decision="forbidden",
    justification="Use --force-with-lease --force-if-includes.",
)
```
