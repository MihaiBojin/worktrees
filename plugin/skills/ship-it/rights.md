# The rules ship-it needs

A plugin cannot grant its own skill the right to push or merge, in either
client, so these rules go in the user's own configuration, once. ship-it
checks for them before it starts and names any that are missing; it never
writes them.

Each rule matches a command's prefix. A rule for
`git push --force-with-lease --force-if-includes` also admits a trailing
`--force`, and one for `gh pr merge` also admits `--admin`. The skill never
writes either, `tests/test_plugin.py` holds its text to that, and the deny
rules below refuse the forms a client can match.

## Claude Code

Add these to `permissions.allow` in `~/.claude/settings.json`, or in the
project's `.claude/settings.json`. They are what lets a merge through auto
mode, whose classifier otherwise blocks merging a pull request no human
approved; a skill's own `allowed-tools` lasts only the turn that invoked it.

```json
[
  "Bash(gw branch create *)",
  "Bash(gw fast-forward *)",
  "Bash(gw ci-checks *)",
  "Bash(git add -- *)",
  "Bash(git commit *)",
  "Bash(git push --set-upstream *)",
  "Bash(git push --force-with-lease --force-if-includes *)",
  "Bash(gh pr create *)",
  "Bash(gh pr edit *)",
  "Bash(gh pr merge *)",
  "Bash(gh run rerun *)",
  "Bash(glab mr create *)",
  "Bash(glab mr update *)",
  "Bash(glab mr merge *)",
  "Bash(glab ci retry *)"
]
```

And these to `permissions.deny`, which no allow rule or skill overrides:

```json
[
  "Bash(git push --force *)",
  "Bash(git push * --force)",
  "Bash(gh pr merge * --admin)",
  "Bash(gh pr merge * --admin *)"
]
```

A deny rule matches only the position its pattern names, so test one on the
Claude Code version in use before relying on it.

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
