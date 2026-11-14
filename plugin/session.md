# How this session uses worktrees

The worktrees plugin hands every session these rules when it starts.

- This session works in the worktree it starts in, and owns it. The client
  creates worktrees: `claude --worktree NAME` or the `EnterWorktree` tool
  with a `name` in Claude Code, `codex --worktree` in Codex. A person may
  create one with `gw add` before the session starts. Never run
  `git worktree add` or `gw add` yourself.
- One worktree holds one branch after another. Ship a branch, then start the
  next with `gw branch create NAME`. A branch that builds on work not merged
  yet starts from it: `git switch --create NAME` while on that branch.
  `/worktrees:ship-it BRANCH ...` ships several, switching to each in turn.
- Before editing, check that no other session is working here. The
  worktree is in use when `git status --short` shows changes this session
  did not make and the user did not ask it to continue, or when
  `git worktree list --porcelain` shows it `locked` by another process.
  Then do not wait. In Claude Code, call `EnterWorktree` with a `name`, run
  `git switch --create NEW BRANCH` there from this worktree's branch, and
  decide when shipping whether NEW stacks on it. In Codex, report it and
  say to start a new session with `codex --worktree`.
- A subagent that writes gets its own worktree (`isolation: "worktree"`).
  Commit before starting it, and have it start its branch with
  `git switch --create NAME PARENT_BRANCH`, since its worktree starts from
  the default branch.
- Leave a worktree with `keep`. `gw prune` removes finished ones and lists
  what it deletes first; `ExitWorktree` removal deletes ignored files without
  asking.
