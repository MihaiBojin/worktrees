"""Claim the worktree an agent session runs in, and release it when it ends.

The claim is git's own worktree lock, so `gw prune`, `gw remove` and
`git worktree remove` all respect it. Its reason names the client, the
session and the client's process with its start time, so a later session can
tell a live holder from one that crashed: a process ID alone is reused.
"""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

from . import repo as R
from .git import git

# What this module writes, and what Claude Code writes for a worktree it
# created itself: `claude <kind> <id> (pid N start T)`.
OURS = re.compile(r"^worktrees (claude|codex) (\S+) \(pid (\d+) start (.+)\)$")
CLAUDE = re.compile(r"^claude \S+ \S+ \(pid (\d+)(?: start [^)]+)?\)$")
CLIENTS = ("claude", "codex")


@git("worktree lock --reason $reason -- $path", mutates=True, ok=(0, 128))
def worktree_lock(reason: str, path: str) -> None:
    """Lock a linked worktree, saying who holds it."""


@git("worktree unlock -- $path", mutates=True, ok=(0, 128))
def worktree_unlock(path: str) -> None:
    """Release a lock."""


def _ps(field: str, pid: int) -> str:
    # One locale and zone, so two sessions print one start time alike.
    proc = subprocess.run(
        ["ps", "-o", f"{field}=", "-p", str(pid)],
        capture_output=True,
        text=True,
        check=False,
        env={**os.environ, "LC_ALL": "C", "TZ": "UTC"},
    )
    return proc.stdout.strip() if proc.returncode == 0 else ""


def client_pid(client: str) -> int:
    """The client process this hook runs under.

    A hook may run inside a shell the client started, so walk up until a
    process named for the client appears; without one, the parent.
    """
    pid = os.getppid()
    for _ in range(8):
        name = Path(_ps("comm", pid)).name
        if name == client:
            return pid
        parent = _ps("ppid", pid)
        if not parent.isdigit() or int(parent) <= 1:
            break
        pid = int(parent)
    return os.getppid()


def started(pid: int) -> str:
    """When the process started, as `ps` prints it, or nothing when it is gone."""
    return " ".join(_ps("lstart", pid).split())


def alive(pid: int, start: str = "") -> bool:
    """The process is running, and is the one that took the lock."""
    now = started(pid)
    return bool(now) and (not start or now == start)


def _here(cwd: str) -> R.Worktree | None:
    top = R.toplevel(repo=cwd).value
    if not top:
        return None
    for wt in R.worktrees(repo=cwd):
        if Path(wt.path).resolve() == Path(top).resolve():
            return wt
    return None


def claim(client: str, session: str, cwd: str) -> str:
    """Lock the worktree at `cwd` for this session; say how it stands."""
    wt = _here(cwd)
    if wt is None:
        return "This directory is not a git checkout."
    if wt.main:
        return (
            "This is the main checkout. It stays on the head branch: do the "
            "work in a linked worktree."
        )
    pid = client_pid(client)
    reason = f"worktrees {client} {session} (pid {pid} start {started(pid)})"
    if "locked" in wt.flags:
        ours = OURS.match(wt.lock)
        theirs = CLAUDE.match(wt.lock)
        holder = int(ours[3]) if ours else int(theirs[1]) if theirs else 0
        if holder == pid:
            if theirs:
                return "This worktree is locked for this session by Claude Code."
            if ours and ours[2] == session:
                return "This worktree is locked for this session."
            # The same client after /clear: a new session, the same process.
        elif not holder or alive(holder, ours[4] if ours else ""):
            # A lock nobody here wrote, or a holder still running.
            return (
                f"Another session holds this worktree: {wt.lock or 'locked'}. "
                "Follow the rule for a held worktree."
            )
        worktree_unlock(wt.path, repo=cwd)
    worktree_lock(reason, wt.path, repo=cwd)
    return "This worktree is locked for this session."


def release(session: str, cwd: str) -> None:
    """Unlock the worktree at `cwd` if this session holds it."""
    wt = _here(cwd)
    if wt is None or wt.main or "locked" not in wt.flags:
        return
    ours = OURS.match(wt.lock)
    if ours and ours[2] == session:
        worktree_unlock(wt.path, repo=cwd)


def ended(lock: str) -> bool:
    """A lock this module took for a session whose client has exited."""
    ours = OURS.match(lock)
    return ours is not None and not alive(int(ours[3]), ours[4])
