"""gw hook: lock the worktree an agent session runs in, and release it."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

from conftest import argv, env_for

from worktrees import session

RULES = Path(__file__).resolve().parents[1] / "plugin" / "session.md"


def hook(world, event: str, cwd: Path, name: str = "s1", stdin: str | None = None):
    """The hook as a client runs it: codex, which no test process is named, so
    the holder is the test's own process."""
    payload = json.dumps({"session_id": name, "cwd": str(cwd)})
    return subprocess.run(
        argv("gw hook", event, "--client", "codex", "--rules", str(RULES)),
        cwd=str(cwd),
        input=payload if stdin is None else stdin,
        capture_output=True,
        text=True,
        env=env_for(world),
    )


def context(proc: subprocess.CompletedProcess[str]) -> str:
    assert proc.returncode == 0, proc.stderr
    out = json.loads(proc.stdout)["hookSpecificOutput"]
    assert out["hookEventName"] == "SessionStart"
    return out["additionalContext"]


def lock_of(world, path: Path) -> str:
    out = world.git("worktree", "list", "--porcelain")
    block = out.split(f"worktree {path}\n", 1)[1].split("\n\n", 1)[0]
    lines = [ln for ln in block.splitlines() if ln.startswith("locked")]
    return lines[0].removeprefix("locked").strip() if lines else ""


def reason_for(pid: int, name: str = "other") -> str:
    return f"worktrees codex {name} (pid {pid} start {session.started(pid)})"


def test_a_session_locks_its_worktree_and_reads_the_rules(world) -> None:
    wt = world.worktree("feature")
    text = context(hook(world, "start", wt))
    assert text.startswith("# How this session uses worktrees")
    assert text.endswith("This worktree is locked for this session.")
    assert session.OURS.match(lock_of(world, wt))
    assert " s1 (pid " in lock_of(world, wt)


def test_the_same_session_starting_again_keeps_its_lock(world) -> None:
    wt = world.worktree("feature")
    hook(world, "start", wt)
    before = lock_of(world, wt)
    assert context(hook(world, "start", wt)).endswith("locked for this session.")
    assert lock_of(world, wt) == before


def test_a_live_holder_is_reported_and_kept(world) -> None:
    wt = world.worktree("feature")
    other = subprocess.Popen(["sleep", "30"])
    try:
        held = reason_for(other.pid)
        world.git("worktree", "lock", "--reason", held, str(wt))
        text = context(hook(world, "start", wt))
        assert f"Another session holds this worktree: {held}." in text
        assert lock_of(world, wt) == held
    finally:
        other.kill()
        other.wait()


def test_the_lock_of_an_ended_session_is_replaced(world) -> None:
    wt = world.worktree("feature")
    gone = subprocess.Popen(["true"])
    gone.wait()
    world.git(
        "worktree",
        "lock",
        "--reason",
        f"worktrees codex old (pid {gone.pid} start x)",
        str(wt),
    )
    assert context(hook(world, "start", wt)).endswith("locked for this session.")
    assert " s1 (pid " in lock_of(world, wt)


def test_a_lock_nobody_here_wrote_is_left_alone(world) -> None:
    wt = world.worktree("feature")
    world.git("worktree", "lock", "--reason", "on a removable disk", str(wt))
    assert "Another session holds this worktree: on a removable disk." in context(
        hook(world, "start", wt)
    )
    assert lock_of(world, wt) == "on a removable disk"


def test_claude_codes_own_lock_for_this_process_is_this_session(world) -> None:
    """Claude Code locks a worktree it creates; the hook's client is that
    process, so the lock is the session's already."""
    wt = world.worktree("feature")
    mine = f"claude session abc (pid {os.getpid()} start 1)"
    world.git("worktree", "lock", "--reason", mine, str(wt))
    assert context(hook(world, "start", wt)).endswith("by Claude Code.")
    assert lock_of(world, wt) == mine


def test_the_main_checkout_is_never_locked(world) -> None:
    text = context(hook(world, "start", world.repo))
    assert text.endswith("do the work in a linked worktree.")
    assert "locked" not in world.git("worktree", "list", "--porcelain")


def test_the_end_releases_only_its_own_session(world) -> None:
    wt = world.worktree("feature")
    hook(world, "start", wt)
    assert hook(world, "end", wt, name="someone-else").returncode == 0
    assert lock_of(world, wt)
    assert hook(world, "end", wt).returncode == 0
    assert lock_of(world, wt) == ""


def test_unreadable_input_still_starts_the_session(world, tmp_path: Path) -> None:
    text = context(hook(world, "start", tmp_path, stdin="not json"))
    assert text.endswith("This directory is not a git checkout.")


def test_prune_names_the_unlock_for_an_ended_session(world) -> None:
    wt = world.worktree("feature")
    gone = subprocess.Popen(["true"])
    gone.wait()
    world.git(
        "worktree",
        "lock",
        "--reason",
        f"worktrees codex old (pid {gone.pid} start x)",
        str(wt),
    )
    p = subprocess.run(
        argv("gw prune", "--json"),
        cwd=str(world.repo),
        capture_output=True,
        text=True,
        env=env_for(world),
    )
    assert f"git worktree unlock -- {wt} releases it" in p.stdout
