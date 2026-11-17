# How this session uses worktrees

The worktrees plugin hands every session these rules when it starts.

- This session keeps one worktree per repository and works on one branch at
  a time in it. In the repository it starts in, that is the worktree it
  starts in, unless that is the main checkout, which stays on the head
  branch. In any repository where it has none, it creates one before its
  first change there and keeps it for every later branch.
- Branches follow one another in that worktree. Commit and push a branch,
  then start the next with `gw branch create NAME`, even while its pull
  request waits on a review or a merge: switching a branch is much faster
  than creating a worktree, and `git switch --no-guess BRANCH` goes back to
  fix it. A branch that builds on work not merged yet starts from it:
  `git switch --create NAME` while on that branch.
  `/worktrees:ship-it BRANCH ...` ships several, switching to each in turn.
- When a change the user decided on is implemented and its tests and checks
  pass, ship it without being asked: `/worktrees:ship-it` commits it on its
  own branch, pushes it, opens the pull request, fixes CI and merges it once
  it is ready.
- Check that no other session is working here before editing, and again
  whenever changes appear that this session did not make. The worktree is
  in use when `git status --short` shows changes this session did not make
  and the user did not ask it to continue, or when
  `git worktree list --porcelain` shows it `locked` by another process.
  Then do not wait, and do not commit, stash or discard those changes:
  create another worktree, run `git switch --create NEW BRANCH` there from
  this worktree's branch, and decide when shipping whether NEW stacks on it.
- To create a worktree in the repository this session started in, in
  Claude Code, call `EnterWorktree` with a `name`. Anywhere else, run
  `command gw add NAME --quiet` from a checkout of the repository; it
  prints the new worktree's path. Claude Code refuses git aimed outside a
  worktree that `EnterWorktree` opened, and refuses to open a second, so
  leave it first with `ExitWorktree` and `keep`. Its shell returns to the
  start folder after a command that leaves the working directories, so give
  each command the path in full. In Codex, whose sandbox keeps `.git`
  read-only, report it and say to start a session there with
  `codex --worktree`.
- A subagent that writes gets its own worktree (`isolation: "worktree"`).
  Commit before starting it, and have it start its branch with
  `git switch --create NAME PARENT_BRANCH`, since its worktree starts from
  the default branch.
- Leave a worktree with `keep`. `gw prune` removes finished ones and lists
  what it deletes first; `ExitWorktree` removal deletes ignored files without
  asking. Never remove or switch another session's worktree.
