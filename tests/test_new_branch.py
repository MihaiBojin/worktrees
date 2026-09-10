"""gwnb: fetch, then branch off the head branch and check it out."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from worktrees import new_branch
from worktrees.git import Refused, guard

SRC = str(Path(__file__).resolve().parents[1] / "src")


def gwnb(world, *args: str) -> subprocess.CompletedProcess[str]:
    from conftest import ENV

    return subprocess.run(
        [
            sys.executable,
            "-c",
            "from worktrees.cli import gwnb; raise SystemExit(gwnb())",
            *args,
        ],
        cwd=str(world.repo),
        capture_output=True,
        text=True,
        env={
            "PATH": "/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin",
            "PYTHONPATH": SRC,
            "HOME": str(world.root),
            "GIT_CONFIG_GLOBAL": str(world.root / "gitconfig"),
            "GIT_CONFIG_NOSYSTEM": "1",
            **ENV,
        },
    )


def with_remote(world) -> None:
    """A remote whose HEAD is recorded, the way a clone records it."""
    world.git("update-ref", "refs/remotes/origin/main", "main")
    world.git("symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/main")
    world.git("remote", "add", "origin", str(world.root / "upstream.git"))


def test_it_branches_off_the_head_branch(world) -> None:
    started = new_branch.create("feature", fetch=False)
    assert started.branch == "feature"
    assert started.base == "refs/heads/main"
    assert world.git("symbolic-ref", "--short", "HEAD") == "feature"


def test_the_base_is_the_remote_copy_when_there_is_one(world) -> None:
    """A branch made here is already on top of what the server has."""
    with_remote(world)
    world.commit("local-only", "x\n")  # main moves; origin/main does not
    started = new_branch.create("feature", fetch=False)
    assert started.base == "refs/remotes/origin/main"
    assert world.git("rev-parse", "HEAD") == world.git(
        "rev-parse", "refs/remotes/origin/main"
    )


def test_the_new_branch_does_not_track_the_head_branch(world) -> None:
    """A branch that tracked it would take it as its upstream, and git push
    would target the head branch."""
    with_remote(world)
    new_branch.create("feature", fetch=False)
    proc = subprocess.run(
        ["git", "-C", str(world.repo), "config", "--get", "branch.feature.merge"],
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 1, f"feature tracks {proc.stdout.strip()}"


def test_an_existing_branch_is_refused(world) -> None:
    world.git("branch", "taken", "main")
    with pytest.raises(new_branch.Refusal) as exc:
        new_branch.create("taken", fetch=False)
    assert "already a branch" in str(exc.value)
    assert "git switch taken checks it out" in str(exc.value)


@pytest.mark.parametrize("name", ["has space", "-leading", "a..b", "x.lock", ""])
def test_an_invalid_name_is_refused(world, name: str) -> None:
    with pytest.raises(new_branch.Refusal) as exc:
        new_branch.create(name, fetch=False)
    assert "not a valid branch name" in str(exc.value)
    assert world.git("symbolic-ref", "--short", "HEAD") == "main"


def test_a_tag_cannot_be_the_base(world) -> None:
    """The base travels as a full ref: a bare name resolves to a tag first."""
    with_remote(world)
    world.commit("later", "later\n")
    world.git("tag", "origin/main", "HEAD")  # the tag is not where origin/main is
    started = new_branch.create("feature", fetch=False)
    assert started.base == "refs/remotes/origin/main"
    assert world.git("rev-parse", "HEAD") != world.git(
        "rev-parse", "refs/tags/origin/main"
    )


def test_a_name_is_required(world) -> None:
    p = gwnb(world)
    assert p.returncode == 2
    assert "usage: gwnb NAME" in p.stderr


def test_the_binary_reports_what_it_made(world) -> None:
    p = gwnb(world, "feature", "--no-fetch")
    assert p.returncode == 0, p.stderr
    sha = world.git("rev-parse", "--short", "refs/heads/main")
    assert p.stdout.strip() == f"feature from main at {sha}"
    assert "refs may be stale" in p.stderr


def test_json_is_data_on_stdout(world) -> None:
    p = gwnb(world, "feature", "--no-fetch", "--json")
    assert p.returncode == 0, p.stderr
    assert json.loads(p.stdout)["base"] == "refs/heads/main"


def test_a_refusal_leaves_the_checkout_alone(world) -> None:
    world.git("branch", "taken", "main")
    p = gwnb(world, "taken", "--no-fetch")
    assert p.returncode == 1
    assert "already a branch" in p.stderr
    assert p.stdout == ""
    assert world.git("symbolic-ref", "--short", "HEAD") == "main"


def test_a_forced_checkout_is_refused(world) -> None:
    """The same class as reset --hard: it discards the working tree."""
    for argv in (
        ("checkout", "-f", "main"),
        ("checkout", "--force", "main"),
        ("switch", "--discard-changes", "main"),
    ):
        with pytest.raises(Refused):
            guard(argv)
    guard(("checkout", "--no-track", "-b", "x", "refs/heads/main"))
