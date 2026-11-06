"""The head ref: the last sync's `<remote>/HEAD`, a clone's, or `main` or
`master`, and the line every command that reads it prints."""

from __future__ import annotations

import json
import os
import subprocess

import pytest
from test_cli import run
from test_sync import bare, sync

from worktrees import repo as R
from worktrees.sync import NEVER

INFERRED = "No main or master branch."
SYNC = "origin/main does not resolve; gw sync updates it"


def tip_at(world, branch: str, when: int) -> None:
    """Point `branch` at a new commit on main, committed at `when`."""
    sha = subprocess.run(
        ["git", "-C", str(world.repo), "commit-tree", "main^{tree}", "-p", "main"],
        input=branch,
        capture_output=True,
        text=True,
        check=True,
        env={**os.environ, "GIT_COMMITTER_DATE": f"@{when} +0000"},
    ).stdout.strip()
    world.git("update-ref", f"refs/heads/{branch}", sha)


def head_line(p: subprocess.CompletedProcess[str]) -> str:
    lines = [line for line in p.stderr.splitlines() if line.startswith("Head: ")]
    assert len(lines) == 1, p.stderr
    return lines[0]


def gws_rows(p: subprocess.CompletedProcess[str]) -> list[dict]:
    return json.loads(p.stdout)["verdicts"]


# --------------------------------------------------------------------------
# the ladder
# --------------------------------------------------------------------------


def test_a_sync_makes_the_remote_head_the_head(world) -> None:
    bare(world)
    assert sync(world).returncode == 0
    world.commit("local-only")  # main moves; origin/main does not
    p = run(world, "gwbc", "spike")
    assert p.returncode == 0, p.stderr
    assert head_line(p) == "Head: origin/main (remote)"
    assert world.git("rev-parse", "spike") == world.git("rev-parse", "origin/main")


def test_a_clone_head_is_the_head_before_any_sync(world) -> None:
    clone = world.root / "clone"
    world.git("clone", "--quiet", str(world.repo), str(clone))
    p = run(world, "gwbs", at=clone)
    assert p.returncode == 0, p.stderr
    assert head_line(p) == "Head: origin/main (clone)"
    assert not (clone / ".git" / "git-worktrees-sync.json").exists()


def test_the_sync_read_is_the_one_of_the_repository_asked(world, monkeypatch) -> None:
    bare(world)
    assert sync(world).returncode == 0
    monkeypatch.chdir(world.root)
    assert R.head_ref("origin", repo=world.repo).source == "remote"


def test_only_main_is_the_head(world) -> None:
    p = run(world, "gwbc", "spike")
    assert p.returncode == 0, p.stderr
    assert head_line(p) == "Head: main (inferred)"


def test_only_master_is_the_head(world) -> None:
    world.git("branch", "--move", "main", "master")
    p = run(world, "gwbc", "spike")
    assert p.returncode == 0, p.stderr
    assert head_line(p) == "Head: master (inferred)"
    assert "spike from master at" in p.stdout


@pytest.mark.parametrize(("newer", "older"), [("main", "master"), ("master", "main")])
def test_of_main_and_master_the_newer_tip_is_the_head(world, newer, older) -> None:
    tip_at(world, older, 1700000100)
    tip_at(world, newer, 1700000200)
    p = run(world, "gwbs", "--json")
    assert json.loads(p.stdout)["head"] == f"refs/heads/{newer}"
    p = run(world, "gwbs")
    assert head_line(p) == f"Head: {newer} (inferred; {older} also exists)"


def test_a_branch_under_main_is_not_main(world) -> None:
    world.git("branch", "--move", "main", "master")
    world.git("branch", "main/x", "master")
    world.git("branch", "main/y", "master")
    p = run(world, "gwbc", "spike")
    assert p.returncode == 0, p.stderr
    assert head_line(p) == "Head: master (inferred)"


def test_a_committer_date_tie_goes_to_main(world) -> None:
    tip_at(world, "master", 1700000100)
    tip_at(world, "main", 1700000100)
    assert world.git("rev-parse", "main") != world.git("rev-parse", "master")
    p = run(world, "gwbs")
    assert head_line(p) == "Head: main (inferred; master also exists)"


def test_a_remote_tracking_main_without_a_head_is_not_the_head(world) -> None:
    world.git("remote", "add", "origin", str(world.root / "nowhere.git"))
    world.git("update-ref", "refs/remotes/origin/main", "main")
    world.git("branch", "--move", "main", "trunk")
    p = run(world, "gwbc", "spike")
    assert p.returncode == 1
    assert INFERRED in p.stderr


# --------------------------------------------------------------------------
# a head that does not resolve
# --------------------------------------------------------------------------


def no_main_or_master(world) -> None:
    world.git("branch", "--move", "main", "trunk")


def synced_then_gone(world) -> None:
    """A sync, and then the branch its HEAD names deleted under it."""
    bare(world)
    assert sync(world).returncode == 0
    world.git("update-ref", "-d", "refs/remotes/origin/main")


def cloned_then_gone(world) -> None:
    world.git("remote", "add", "origin", str(world.root / "nowhere.git"))
    world.git("symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/main")


UNRESOLVED = [
    pytest.param(no_main_or_master, INFERRED, id="inferred"),
    pytest.param(synced_then_gone, SYNC, id="synced"),
    pytest.param(cloned_then_gone, SYNC, id="cloned"),
]


def refusal(setup, message: str) -> str:
    """What a refusing command prints: the never-synced line first, for a
    remote nothing has synced, then the refusal alone."""
    return f"{NEVER}\n{message}\n" if setup is cloned_then_gone else f"{message}\n"


@pytest.mark.parametrize(("setup", "message"), UNRESOLVED)
def test_every_row_a_head_would_judge_is_unknown(world, setup, message) -> None:
    """The main checkout, the head branch and a checked-out branch keep
    their rows, since none of those answers measures anything."""
    feature = world.worktree("feature")
    world.git("branch", "loose", "main")
    setup(world)
    p = run(world, "gws", "--json")
    assert p.returncode == 1, p.stderr
    assert p.stderr == f"{message}\n"
    worktrees = {v["path"]: (v["verdict"], v["why"]) for v in gws_rows(p)}
    assert worktrees == {
        str(world.repo): ("keep", "it is the main checkout, and never removable"),
        str(feature): ("unknown", message),
    }

    p = run(world, "gwbs", "--json")
    assert p.returncode == 1, p.stderr
    assert p.stderr == f"{message}\n"
    rows = {v["branch"]: v for v in json.loads(p.stdout)["verdicts"]}
    assert (rows["loose"]["verdict"], rows["loose"]["why"]) == ("unknown", message)
    assert rows["feature"]["verdict"] == "keep"
    assert rows["feature"]["paths"] == [str(feature)]
    here = world.git("branch", "--show-current")
    assert rows[here]["verdict"] == "keep"
    assert rows[here]["paths"] == [str(world.repo)]


def test_an_unresolved_head_fails_with_only_the_main_checkout(world) -> None:
    no_main_or_master(world)
    p = run(world, "gws")
    assert p.returncode == 1
    assert p.stderr.startswith(f"{INFERRED}\n")
    assert "Head:" not in p.stderr


def test_main_and_master_are_kept_beside_a_dangling_head(world) -> None:
    world.git("branch", "master", "main")
    world.git("remote", "add", "origin", str(world.root / "nowhere.git"))
    world.git("symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/develop")
    p = run(world, "gwbs", "--json")
    assert p.returncode == 1
    rows = {
        v["branch"]: (v["verdict"], v["why"]) for v in json.loads(p.stdout)["verdicts"]
    }
    assert rows["master"] == ("keep", "it is main or master")


@pytest.mark.parametrize(("setup", "message"), UNRESOLVED)
@pytest.mark.parametrize(
    "argv",
    [("gwp", "--yes"), ("gwr", "feature", "--yes"), ("gwbd", "--all", "--yes")],
    ids=lambda argv: argv[0],
)
def test_a_command_that_changes_something_refuses(world, setup, message, argv) -> None:
    world.worktree("feature")
    setup(world)
    p = run(world, *argv)
    assert p.returncode == 1, p.stderr
    # The refusal alone: no head line, and no row judged before it.
    assert p.stderr == refusal(setup, message)
    assert world.git("rev-parse", "--verify", "refs/heads/feature")
    assert (world.parent / ".worktrees" / "feature" / "repo").is_dir()


def test_prune_refuses_before_clearing_a_stale_record(world) -> None:
    path = world.worktree("gone")
    subprocess.run(["rm", "-rf", str(path)], check=True)
    no_main_or_master(world)
    p = run(world, "gwp", "--yes")
    assert p.returncode == 1
    assert str(path) in world.git("worktree", "list")


@pytest.mark.parametrize(("setup", "message"), UNRESOLVED)
@pytest.mark.parametrize(
    "argv", [("gwa", "spike"), ("gwbc", "spike"), ("gwrot",)], ids=lambda a: a[0]
)
def test_a_command_that_starts_a_branch_fails(world, setup, message, argv) -> None:
    setup(world)
    p = run(world, *argv)
    assert p.returncode == 1, p.stderr
    assert p.stderr == refusal(setup, message)
    assert world.git("branch", "--list", "spike*") == ""
    assert not (world.parent / ".worktrees").exists()


# --------------------------------------------------------------------------
# the head line
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "argv",
    [
        ("gws",),
        ("gwp", "--yes"),
        ("gwr", "feature", "--yes"),
        ("gwbs",),
        ("gwbd", "--all", "--yes"),
        ("gwa", "spike"),
        ("gwbc", "spike"),
        ("gwrot",),
    ],
    ids=lambda argv: argv[0],
)
@pytest.mark.parametrize("quiet", [(), ("-q",)], ids=["", "quiet"])
def test_every_command_reading_the_head_prints_it(world, argv, quiet) -> None:
    """Under -q too, when the head was inferred: that one is a guess."""
    world.worktree("feature")
    assert head_line(run(world, *argv, *quiet)) == "Head: main (inferred)"


@pytest.mark.parametrize(
    "argv", [("gws",), ("gwbs",), ("gwbc", "spike")], ids=lambda a: a[0]
)
def test_quiet_drops_a_head_that_was_not_inferred(world, argv) -> None:
    world.git("remote", "add", "origin", str(world.root / "nowhere.git"))
    world.git("update-ref", "refs/remotes/origin/main", "main")
    world.git("symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/main")
    assert head_line(run(world, *argv)) == "Head: origin/main (clone)"
    assert "Head:" not in run(world, *argv, "-q").stderr


@pytest.mark.parametrize(
    "argv",
    [
        ("gws",),
        ("gwp", "--yes"),
        ("gwr", "feature", "--yes"),
        ("gwbs",),
        ("gwbd", "--all", "--yes"),
        ("gwa", "spike"),
        ("gwbc", "spike"),
        ("gwrot",),
    ],
    ids=lambda a: a[0],
)
def test_json_has_no_head_line(world, argv) -> None:
    """gwrot on a main with nothing to catch up exits 1, and says so without
    the line."""
    world.worktree("feature")
    p = run(world, *argv, "--json")
    assert "Head:" not in p.stderr
