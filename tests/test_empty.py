"""The `empty` verdict: a tip on the head ref's first-parent line."""

from __future__ import annotations

import json

import pytest
from test_cli import run, run_with_a_terminal_on_stdout
from test_sync import bare, sync

EMPTY_WHY = "its tip is on the head branch's first-parent history"
NEEDS = "needs --delete empty"


def rows(world, entry: str = "gwp", *args: str) -> dict[str, dict]:
    p = run(world, entry, "--json", *args)
    assert p.returncode == 0, p.stderr
    return {v["branch"]: v for v in json.loads(p.stdout)["verdicts"]}


def test_a_branch_with_no_commits_is_empty_and_needs_permission(world) -> None:
    world.worktree("fresh")
    row = rows(world)["fresh"]
    assert (row["verdict"], row["eligible"]) == ("empty", False)
    assert row["why"] == f"{EMPTY_WHY}; {NEEDS}"


@pytest.mark.parametrize("value", ["empty", "all"])
def test_permission_clears_eligible_and_leaves_the_verdict(world, value) -> None:
    world.worktree("fresh")
    row = rows(world, "gwp", "--delete", value)["fresh"]
    assert (row["verdict"], row["eligible"], row["why"]) == ("empty", True, EMPTY_WHY)


def test_an_older_main_line_commit_and_a_fast_forward_are_both_empty(world) -> None:
    old = world.git("rev-parse", "main")
    world.commit("later")
    world.git("branch", "old", old)
    world.git("switch", "--quiet", "-c", "ff")
    world.commit("ff-work")
    world.git("switch", "--quiet", "main")
    world.git("merge", "--quiet", "--ff-only", "ff")
    seen = rows(world, "gwbs")
    assert seen["old"]["verdict"] == seen["ff"]["verdict"] == "empty"


def test_a_merge_commit_leaves_the_branch_remove(world) -> None:
    world.finished_branch("merged")
    assert rows(world, "gwbs")["merged"]["verdict"] == "remove"


def test_a_local_counterpart_ahead_of_the_remote_head_adds_nothing(world) -> None:
    bare(world)
    assert sync(world).returncode == 0
    world.commit("local-only")
    world.git("branch", "on-local", "main")
    row = rows(world, "gwbs")["on-local"]
    assert row["verdict"] != "empty", row


def test_the_first_parent_line_is_read_once_per_run(world, capsys) -> None:
    from worktrees import cli
    from worktrees.git import options

    for name in ("one", "two", "three"):
        world.worktree(name)
    assert cli.gwp(["--json"]) == 0
    capsys.readouterr()
    reads = [e for e in options.log if "--first-parent" in e]
    assert len(reads) == 1, reads


def test_only_empty_filters_by_verdict(world) -> None:
    world.worktree("fresh")
    world.finished("done")
    assert set(rows(world, "gwp", "--only", "empty")) == {"fresh"}


def test_gwp_yes_removes_an_empty_worktree_only_with_permission(world) -> None:
    wt = world.worktree("fresh")
    p = run(world, "gwp", "--yes", "--json")
    assert json.loads(p.stdout)["removed"] == []
    assert wt.exists()
    p = run(world, "gwp", "--yes", "--json", "--delete", "empty")
    assert p.returncode == 0, p.stderr
    assert json.loads(p.stdout)["removed"] == ["fresh"]
    assert not wt.exists()
    assert world.git("branch", "--list", "fresh") == ""


def test_gwr_refuses_an_empty_worktree_without_permission(world) -> None:
    wt = world.worktree("fresh")
    p = run(world, "gwr", "fresh", "--yes")
    assert p.returncode == 1
    assert f"fresh is empty: {EMPTY_WHY}; {NEEDS}" in p.stderr
    assert wt.exists()
    p = run(world, "gwr", "fresh", "--yes", "--delete", "empty")
    assert p.returncode == 0, p.stderr
    assert not wt.exists()
    assert world.git("branch", "--list", "fresh") == ""


@pytest.mark.parametrize("value", ["empty", "all"])
def test_gwbd_deletes_an_empty_branch_only_with_permission(world, value) -> None:
    world.git("branch", "idle")
    p = run(world, "gwbd", "idle", "--yes")
    assert p.returncode == 3
    assert f"idle kept: {EMPTY_WHY}; {NEEDS}" in p.stderr
    assert world.git("branch", "--list", "idle")
    p = run(world, "gwbd", "idle", "--yes", "--delete", value)
    assert p.returncode == 0, p.stderr
    assert world.git("branch", "--list", "idle") == ""


def test_gwbs_never_marks_an_empty_branch_eligible(world) -> None:
    world.git("branch", "idle")
    row = rows(world, "gwbs")["idle"]
    assert (row["verdict"], row["eligible"]) == ("empty", False)


def test_empty_has_its_own_colour(world) -> None:
    world.worktree("fresh")
    code, out = run_with_a_terminal_on_stdout(world, "gwp")
    assert code == 0, out
    line = next(ln for ln in out.splitlines() if "fresh" in ln)
    assert "\x1b[36mempty" in line


def test_a_held_back_empty_row_is_dimmed_on_a_terminal(world) -> None:
    world.worktree("fresh")
    code, out = run_with_a_terminal_on_stdout(world, "gwp")
    assert code == 0, out
    line = next(ln for ln in out.splitlines() if "fresh" in ln)
    assert "\x1b[36mempty" in line, "the verdict keeps its colour"
    assert "\x1b[2m" + f"{EMPTY_WHY}; {NEEDS}" in line
