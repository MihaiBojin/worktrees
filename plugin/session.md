# How this session uses worktrees

The worktrees plugin hands every session these rules when it starts.

- This session works in the worktree it starts in, and owns it. Two changes
  never share a branch: a change that cannot go on this worktree's branch
  gets a worktree of its own.
- One worktree holds one branch after another. Ship a branch, then start the
  next with `gw branch create NAME`. A branch that builds on work not merged
  yet starts from it: `git switch --create NAME` while on that branch.
  `/worktrees:ship-it BRANCH ...` ships several, switching to each in turn.
- Create a worktree without asking when the next change cannot go here:
  - This worktree has to stay on its branch: its pull request waits on a
    review or a merge, another session works here, or the user wants the
    branch kept. Start a second worktree for the new change.
  - The change is in another repository. Start a worktree there rather than
    editing that repository's main checkout or another session's worktree.
- To create one in this repository in Claude Code, call `EnterWorktree` with
  a `name`. Anywhere else, run `command gw add NAME --quiet` from a checkout
  of the repository; it prints the new worktree's path. Claude Code refuses
  git aimed outside a worktree that `EnterWorktree` opened, and refuses to
  open a second, so leave it first with `ExitWorktree` and `keep`. Its shell
  returns to the start folder after a command that leaves the working
  directories, so give each command the path in full. In Codex, whose
  sandbox keeps `.git` read-only, report it and say to start a session
  there with `codex --worktree`.
- A new worktree starts from the default branch. A change that builds on
  this worktree's branch starts from it there, with
  `git switch --create NEW BRANCH`, and ships stacked on it.
- Before editing, check that no other session is working here. The
  worktree is in use when `git status --short` shows changes this session
  did not make and the user did not ask it to continue, or when
  `git worktree list --porcelain` shows it `locked` by another process.
  Then do not wait: start a second worktree, as above.
- A subagent that writes gets its own worktree (`isolation: "worktree"`).
  Commit before starting it, and have it start its branch with
  `git switch --create NAME PARENT_BRANCH`, since its worktree starts from
  the default branch.
- Leave a worktree with `keep`. `gw prune` removes finished ones and lists
  what it deletes first; `ExitWorktree` removal deletes ignored files without
  asking. Never remove or switch another session's worktree.
