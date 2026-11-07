"""A branch's stashes hold it back, and the disclosure says how to restore each."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from conftest import env_for
from test_cli import run

PROOF = "its tip is an ancestor of the head branch"


def stash(world, at: Path, message: str = "") -> str:
    """Stash a change to README in `at`, with `-m message` when given."""
    at.joinpath("README").write_text(f"{message or 'wip'}\n")
    world.git("stash", "push", "--quiet", *(["-m", message] if message else []), at=at)
    return world.git("rev-parse", "stash@{0}")


def rows(world, entry: str = "gwp") -> dict[str, dict]:
    p = run(world, entry, "--json")
    assert p.returncode == 0, p.stderr
    return {v["branch"] or "(detached)": v for v in json.loads(p.stdout)["verdicts"]}


def test_a_stash_on_the_branch_clears_eligible(world) -> None:
    wt = world.finished("wip")
    named = stash(world, wt, "half-finished")
    plain = stash(world, wt)
    row = rows(world)["wip"]
    assert (row["verdict"], row["eligible"]) == ("remove", False)
    assert row["why"] == f"{PROOF}; 2 stash(es) belong to its branch"
    assert row["stashes"] == [plain, named]
    assert world.git("stash", "list", "--format=%gs").splitlines() == [
        "WIP on wip: " + world.git("log", "-1", "--format=%h %s", "wip"),
        "On wip: half-finished",
    ]


def test_a_stash_named_for_another_branch_does_not_belong(world) -> None:
    world.finished("wip")
    stash(world, world.repo, "on main")
    row = rows(world)["wip"]
    assert (row["eligible"], row["stashes"]) == (True, [])


def test_a_stash_from_a_commit_the_branch_left_does_not_belong(world) -> None:
    wt = world.finished("wip")
    world.commit("later", at=wt)
    stash(world, wt, "on the later commit")
    world.git("reset", "--quiet", "--hard", "HEAD~1", at=wt)
    row = rows(world)["wip"]
    assert (row["eligible"], row["stashes"]) == (True, [])


def test_a_stash_made_on_a_detached_head_belongs_to_no_branch(world) -> None:
    wt = world.parent / ".worktrees" / "loose" / world.repo.name
    world.git("worktree", "add", "--quiet", "--detach", str(wt), "main")
    stash(world, wt, "detached")
    row = rows(world)["(detached)"]
    assert (row["eligible"], row["stashes"]) == (True, [])


def test_gwp_yes_keeps_a_branch_with_stashes(world) -> None:
    wt = world.finished("wip")
    stash(world, wt, "half")
    p = run(world, "gwp", "--yes", "--json")
    assert p.returncode == 0, p.stderr
    assert json.loads(p.stdout)["removed"] == []
    assert wt.exists()


def test_a_loose_branch_with_stashes_is_kept_by_gwbd(world) -> None:
    wt = world.finished("wip")
    sha = stash(world, wt, "half")
    world.git("worktree", "remove", str(wt))
    row = rows(world, "gwbs")["wip"]
    assert (row["verdict"], row["eligible"]) == ("remove", False)
    assert row["stashes"] == [sha]
    p = run(world, "gwbd", "wip", "--yes")
    assert p.returncode == 3
    assert "wip kept: " in p.stderr and "1 stash(es) belong to its branch" in p.stderr
    assert world.git("branch", "--list", "wip")


def test_gwr_force_keeps_the_branch_and_its_stashes_and_says_how_to_restore(
    world,
) -> None:
    wt = world.finished("wip")
    sha = stash(world, wt, "half finished")
    p = run(world, "gwr", "wip", "--yes", "--force")
    assert p.returncode == 0, p.stderr
    restore = f"git stash store -m 'On wip: half finished' {sha}"
    assert restore in p.stderr
    assert not wt.exists()
    assert world.git("branch", "--list", "wip")
    assert world.git("rev-parse", "stash@{0}") == sha

    # The line does what it says once the entry is gone.
    world.git("stash", "drop", "--quiet")
    subprocess.run(restore, shell=True, check=True, cwd=world.repo, env=env_for(world))
    assert (
        world.git("stash", "list", "--format=%H %gs") == f"{sha} On wip: half finished"
    )


def test_gwbd_refuses_a_branch_that_gained_a_stash_after_assessment(world) -> None:
    import pytest

    from worktrees import branches
    from worktrees.git import Refused

    world.finished_branch("done")
    head = world.git("rev-parse", "main")
    [row] = branches.assess(["done"], head, "main")
    assert row.eligible
    world.git("switch", "--quiet", "done")
    stash(world, world.repo, "late")
    world.git("switch", "--quiet", "main")
    with pytest.raises(Refused, match="stashes changed"):
        branches.delete(row, "refs/heads/main", head, "main")
    assert world.git("branch", "--list", "done")
