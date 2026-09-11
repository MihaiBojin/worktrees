"""gwa, gwl, gwm and gwr: the four that land you somewhere."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from worktrees import layout, pick
from worktrees import worktree as W
from worktrees.new_branch import Refusal
from worktrees.repo import Worktree

SRC = str(Path(__file__).resolve().parents[1] / "src")


def _env(world) -> dict[str, str]:
    from conftest import ENV

    return {
        "PATH": "/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin",
        "PYTHONPATH": SRC,
        "HOME": str(world.root),
        "GIT_CONFIG_GLOBAL": str(world.root / "gitconfig"),
        "GIT_CONFIG_NOSYSTEM": "1",
        **ENV,
    }


def run(world, entry: str, *args: str, at: Path | None = None):
    return subprocess.run(
        [
            sys.executable,
            "-c",
            f"from worktrees.cli import {entry}; raise SystemExit({entry}())",
            *args,
        ],
        cwd=str(at or world.repo),
        capture_output=True,
        text=True,
        env=_env(world),
    )


# --------------------------------------------------------------------------
# the derived layout
# --------------------------------------------------------------------------


def test_the_destination_is_derived_not_configured(world) -> None:
    """Two tools cannot disagree about a location neither can be told."""
    main = str(world.repo)
    assert (
        layout.destination("fix-parser", main)
        == world.parent / ".worktrees" / "fix-parser" / "repo"
    )


def test_a_branch_with_slashes_becomes_directories(world) -> None:
    """The repository name goes last, so feat/oauth cannot collide with feat."""
    main = str(world.repo)
    assert layout.destination("feat/oauth", main) == (
        world.parent / ".worktrees" / "feat" / "oauth" / "repo"
    )


# --------------------------------------------------------------------------
# gwa
# --------------------------------------------------------------------------


def test_add_prints_only_the_destination(world) -> None:
    p = run(world, "gwa", "fix-parser", "--no-fetch")
    assert p.returncode == 0, p.stderr
    dest = world.parent / ".worktrees" / "fix-parser" / "repo"
    assert p.stdout.strip() == str(dest)
    assert dest.is_dir()
    assert world.git("rev-parse", "--abbrev-ref", "HEAD", at=dest) == "fix-parser"


def test_add_uses_a_branch_that_already_exists(world) -> None:
    """-b would refuse it, so the checkout is made without one."""
    world.git("branch", "already", "main")
    landed = W.add("already", fetch=False)
    assert landed.created is False
    assert Path(landed.path).is_dir()


def test_add_refuses_a_branch_already_checked_out(world) -> None:
    world.worktree("taken")
    with pytest.raises(Refusal, match="already checked out"):
        W.add("taken", fetch=False)


def test_add_refuses_a_directory_another_repository_owns(world) -> None:
    """`git worktree list` returns empty for somebody else's checkout, which
    reads exactly like "nothing is there"."""
    other = world.root / "other"
    other.mkdir()
    subprocess.run(["git", "init", "--quiet", "-b", "main", str(other)], check=True)
    dest = layout.destination("borrowed", str(world.repo))
    dest.parent.mkdir(parents=True)
    subprocess.run(
        ["git", "-C", str(other), "worktree", "add", "--quiet", "-b", "x", str(dest)],
        check=True,
        capture_output=True,
    )
    with pytest.raises(Refusal, match="belongs to"):
        W.add("borrowed", fetch=False)


@pytest.mark.parametrize("name", ["has space", "-leading", "a..b"])
def test_add_refuses_a_name_git_rejects(world, name: str) -> None:
    with pytest.raises(Refusal, match="not a valid branch name"):
        W.add(name, fetch=False)


def test_add_with_no_name_names_what_was_typed(world) -> None:
    p = run(world, "gwa")
    assert p.returncode == 2
    assert p.stderr.strip() == "usage: gwa NAME [BASE]"


# --------------------------------------------------------------------------
# gwl
# --------------------------------------------------------------------------


def test_list_prints_the_selection_alone(world) -> None:
    world.worktree("fix-parser")
    p = run(world, "gwl", "parse")
    assert p.returncode == 0, p.stderr
    assert p.stdout.strip() == str(world.parent / ".worktrees" / "fix-parser" / "repo")


def test_list_omits_the_main_checkout(world) -> None:
    world.worktree("one")
    p = run(world, "gwl", "--json")
    payload = json.loads(p.stdout)
    assert [w["branch"] for w in payload] == ["one"]


def test_list_refuses_to_prompt_without_a_terminal(world) -> None:
    """An agent or a pipe reaching a prompt would hang."""
    world.worktree("one")
    world.worktree("two")
    p = run(world, "gwl")
    assert p.returncode == 3
    assert "not a terminal" in p.stderr
    assert "--list" in p.stderr


def test_one_match_takes_it_with_no_prompt(world) -> None:
    world.worktree("one")
    world.worktree("two")
    p = run(world, "gwl", "two")
    assert p.returncode == 0, p.stderr
    assert p.stdout.strip().endswith("/two/repo")


def test_the_picker_prefers_a_substring_to_a_subsequence() -> None:
    """Typing more of a name never moves it down the list."""
    rows = [
        Worktree("/w/add-tests", "a", "add-tests", frozenset()),
        Worktree("/w/toast", "a", "toast", frozenset()),
    ]
    assert [w.branch for w in pick.matches("tst", rows)] == ["add-tests", "toast"]
    assert [w.branch for w in pick.matches("toast", rows)] == ["toast"]


def test_a_blank_answer_cancels_the_pick() -> None:
    rows = [
        Worktree("/w/a", "a", "a", frozenset()),
        Worktree("/w/b", "a", "b", frozenset()),
    ]
    assert pick.choose(rows, lambda _: None, ask=lambda _: "\n") is None
    picked = pick.choose(rows, lambda _: None, ask=lambda _: "2")
    assert picked is not None
    assert picked.path == "/w/b"


# --------------------------------------------------------------------------
# gwm
# --------------------------------------------------------------------------


def test_move_renames_the_branch_and_the_directory(world) -> None:
    wt = world.worktree("fix-parser")
    p = run(world, "gwm", "parser-v2", at=wt)
    assert p.returncode == 0, p.stderr
    dest = world.parent / ".worktrees" / "parser-v2" / "repo"
    assert p.stdout.strip() == str(dest)
    assert dest.is_dir()
    assert not wt.exists()
    assert world.git("rev-parse", "--verify", "refs/heads/parser-v2")


def test_move_refuses_from_the_main_checkout(world) -> None:
    p = run(world, "gwm", "other")
    assert p.returncode == 1
    assert "main checkout" in p.stderr
    assert world.git("rev-parse", "--abbrev-ref", "HEAD") == "main"


def test_move_refuses_a_name_already_taken(world) -> None:
    wt = world.worktree("fix-parser")
    world.git("branch", "taken", "main")
    p = run(world, "gwm", "taken", at=wt)
    assert p.returncode == 1
    assert "already a branch" in p.stderr
    assert world.git("rev-parse", "--verify", "refs/heads/fix-parser")


def test_move_leaves_no_empty_directory_behind(world) -> None:
    wt = world.worktree("feat/one")
    run(world, "gwm", "two", at=wt)
    assert not (world.parent / ".worktrees" / "feat").exists()


# --------------------------------------------------------------------------
# gwr
# --------------------------------------------------------------------------


def test_remove_prints_nothing_when_you_were_not_standing_in_it(world) -> None:
    """Empty stdout means stay put."""
    wt = world.worktree("done")
    p = run(world, "gwr", "done", "--no-fetch", "--yes")
    assert p.returncode == 0, p.stderr
    assert p.stdout.strip() == ""
    assert not wt.exists()
    assert "done" not in world.git("branch", "--format=%(refname:short)").split()


def test_remove_prints_the_main_checkout_when_you_were(world) -> None:
    wt = world.worktree("done")
    p = run(world, "gwr", "--no-fetch", "--yes", at=wt)
    assert p.returncode == 0, p.stderr
    assert p.stdout.strip() == str(world.repo)
    assert not wt.exists()


def test_remove_refuses_an_unfinished_branch(world) -> None:
    wt = world.worktree("busy")
    world.commit("b.txt", "b\n", at=wt)
    p = run(world, "gwr", "busy", "--no-fetch", "--yes")
    assert p.returncode == 1
    assert "is not finished" in p.stderr
    assert "--force" in p.stderr
    assert wt.exists()


def test_force_removes_the_checkout_and_keeps_the_branch(world) -> None:
    """The worktree was in the way. The work was not."""
    wt = world.worktree("busy")
    world.commit("b.txt", "b\n", at=wt)
    p = run(world, "gwr", "busy", "--no-fetch", "--yes", "--force")
    assert p.returncode == 0, p.stderr
    assert not wt.exists()
    assert world.git("rev-parse", "--verify", "refs/heads/busy")


def test_remove_prints_the_restore_line_once(world) -> None:
    world.worktree("done")
    p = run(world, "gwr", "done", "--no-fetch", "--yes")
    assert p.stderr.count("restore with: git branch done ") == 1


def test_a_loose_match_ignores_the_path(world) -> None:
    """Every path holds `.worktrees`, which supplies a t, a w and an o to any
    query that wants them, so `two` matched `one` through its own directory.
    """
    rows = [
        Worktree("/home/x/.worktrees/one/repo", "a", "one", frozenset()),
        Worktree("/home/x/.worktrees/two/repo", "a", "two", frozenset()),
    ]
    assert [w.branch for w in pick.matches("two", rows)] == ["two"]
    # A literal substring of a path still finds it.
    assert [w.branch for w in pick.matches("/one/", rows)] == ["one"]
