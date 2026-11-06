"""rotate: the next branch in a series, or the head branch caught up."""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import date
from pathlib import Path

import pytest
from conftest import env_for

from worktrees import rotate as R_
from worktrees.new_branch import Refusal

SRC = str(Path(__file__).resolve().parents[1] / "src")
TODAY = date.today().isoformat()


def gwrot(world, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            "-c",
            "from worktrees.cli import gwrot; raise SystemExit(gwrot())",
            *args,
        ],
        cwd=str(world.repo),
        capture_output=True,
        text=True,
        env=env_for(world),
    )


def with_work(world, branch: str = "fix-parser") -> str:
    """A branch off main carrying one commit, so rotating it changes something."""
    world.git("switch", "--quiet", "--create", branch)
    return world.commit(f"{branch}.txt", "w\n")


def with_remote(world) -> None:
    world.git("update-ref", "refs/remotes/origin/main", "main")
    world.git("symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/main")
    world.git("remote", "add", "origin", str(world.root / "upstream.git"))


# --------------------------------------------------------------------------
# the name
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("branch", "expected"),
    [
        ("fix-parser", "fix-parser"),
        ("fix-parser-2026-09-10_001", "fix-parser"),
        ("fix-parser-2026-09-09_003", "fix-parser"),
        ("feature/oauth-2026-01-02_042", "feature/oauth"),
        # Not a suffix this wrote: two digits, and a date that is not one.
        ("fix-parser-2026-09-10_01", "fix-parser-2026-09-10_01"),
        ("release-1.2.3", "release-1.2.3"),
    ],
)
def test_the_stem_drops_only_a_suffix_it_wrote(branch: str, expected: str) -> None:
    """So four rotations give four siblings, not one name with four suffixes."""
    assert R_.stem(branch) == expected


def test_four_rotations_in_a_day_number_upwards(world) -> None:
    world.git("switch", "--quiet", "--create", "fix-parser")
    seen = []
    for n in range(4):
        world.commit(f"w{n}.txt")
        result = R_.rotate()
        assert isinstance(result, R_.Rotated)
        seen.append(result.branch)
        assert result.stem == "fix-parser"

    assert seen == [f"fix-parser-{TODAY}_{n:03d}" for n in (1, 2, 3, 4)]
    # The stem never grew a second suffix.
    assert all(b.count("_") == 1 for b in seen)


def test_a_number_used_only_on_the_remote_is_skipped(world) -> None:
    """Free locally and taken upstream is not free; two people would collide."""
    with_remote(world)
    with_work(world)
    world.git("update-ref", f"refs/remotes/origin/fix-parser-{TODAY}_001", "main")

    result = R_.rotate()
    assert isinstance(result, R_.Rotated)
    assert result.branch == f"fix-parser-{TODAY}_002"


# --------------------------------------------------------------------------
# refusals
# --------------------------------------------------------------------------


def test_a_detached_head_is_refused(world) -> None:
    world.git("checkout", "--quiet", "--detach", "HEAD")
    with pytest.raises(Refusal, match="HEAD is detached"):
        R_.rotate()
    assert "gwbc" in _refusal(world)


def _refusal(world) -> str:
    try:
        R_.rotate()
    except Refusal as exc:
        return str(exc)
    return ""


def test_a_head_branch_ahead_of_the_remote_is_refused(world) -> None:
    """Those commits are a change of their own, and --ff-only says so."""
    with_remote(world)
    world.commit("local.txt", "x\n")
    before = world.git("rev-parse", "HEAD")

    with pytest.raises(Refusal) as exc:
        R_.rotate()
    assert "1 commit(s) ahead of origin/main" in str(exc.value)
    assert "gwbc" in str(exc.value)
    assert world.git("rev-parse", "HEAD") == before


def test_a_tag_cannot_become_the_base(world) -> None:
    with_remote(world)
    world.git("switch", "--quiet", "--create", "fix-parser")
    world.commit("f.txt", "f\n")
    world.git("tag", "origin/main", "refs/heads/fix-parser")

    result = R_.rotate()
    assert isinstance(result, R_.Rotated)
    assert result.base == "refs/remotes/origin/main"


# --------------------------------------------------------------------------
# the head branch
# --------------------------------------------------------------------------


def test_the_head_branch_is_fast_forwarded(world) -> None:
    """No chain to continue, so bring it level rather than branching."""
    with_remote(world)
    world.git("switch", "--quiet", "--create", "ahead", "main")
    moved = world.commit("remote.txt", "r\n")
    world.git("update-ref", "refs/remotes/origin/main", moved)
    world.git("switch", "--quiet", "main")

    before = world.git("rev-parse", "--short", "refs/heads/main")
    result = R_.rotate()
    assert isinstance(result, R_.CaughtUp)
    assert result.branch == "main"
    assert result.at == "origin/main"
    assert result.previous_sha == before
    assert result.count == 1
    assert world.git("rev-parse", "refs/heads/main") == moved


def test_a_head_branch_already_level_is_refused(world) -> None:
    """A catch-up that moves nothing says so and exits 1."""
    with_remote(world)
    with pytest.raises(Refusal, match="main is already at origin/main"):
        R_.rotate()

    p = gwrot(world)
    assert p.returncode == 1
    assert p.stdout == ""
    assert "nothing to catch up" in p.stderr


# --------------------------------------------------------------------------
# a rotation that would change nothing
# --------------------------------------------------------------------------


def test_a_branch_at_its_base_with_nothing_of_its_own_is_refused(world) -> None:
    """The next branch would be the same commit under another name."""
    world.git("switch", "--quiet", "--create", "fix-parser")
    with pytest.raises(Refusal, match="fix-parser is already at main"):
        R_.rotate()
    assert not world.git("branch", "--list", f"fix-parser-{TODAY}_*")
    assert world.git("symbolic-ref", "--short", "HEAD") == "fix-parser"


def test_rotating_again_without_committing_exits_1(world) -> None:
    with_work(world)
    first = gwrot(world)
    assert first.returncode == 0, first.stderr

    again = gwrot(world)
    assert again.returncode == 1
    assert again.stdout == ""
    assert f"fix-parser-{TODAY}_001 is already at main" in again.stderr
    assert world.git("symbolic-ref", "--short", "HEAD") == f"fix-parser-{TODAY}_001"
    assert not world.git("branch", "--list", f"fix-parser-{TODAY}_002")


def test_a_branch_left_behind_by_its_base_still_rotates(world) -> None:
    """No commits of its own, but the base moved: the new branch is newer."""
    world.git("switch", "--quiet", "--create", "fix-parser")
    world.git("switch", "--quiet", "main")
    moved = world.commit("main.txt")
    world.git("switch", "--quiet", "fix-parser")

    result = R_.rotate()
    assert isinstance(result, R_.Rotated)
    assert result.ahead == 0
    assert world.git("rev-parse", "HEAD") == moved


def test_catching_up_never_rebases(world) -> None:
    """--ff-only, because rewriting local commits on the head branch is the
    class this tooling refuses everywhere else."""
    from worktrees.git import commands

    specs = {c.name: " ".join(c.shape) for c in commands()}
    assert specs["merge_ff_only"] == "merge --ff-only $ref"
    assert not any("rebase" in s for s in specs.values())


# --------------------------------------------------------------------------
# the binary
# --------------------------------------------------------------------------


def test_the_binary_reports_where_it_was_and_where_it_is(world) -> None:
    with_work(world)
    was = world.git("rev-parse", "--short", "refs/heads/fix-parser")
    now = world.git("rev-parse", "--short", "refs/heads/main")

    p = gwrot(world)
    assert p.returncode == 0, p.stderr
    first, second = p.stdout.splitlines()
    assert first.split()[:3] == ["was", "fix-parser", was]
    assert first.endswith("kept, 1 commit not on main")
    assert second.split()[:3] == ["now", f"fix-parser-{TODAY}_001", now]
    assert second.endswith("new, from main")


def test_a_caught_up_head_branch_reports_both_commits(world) -> None:
    with_remote(world)
    before = world.git("rev-parse", "--short", "HEAD")
    world.git("switch", "--quiet", "--create", "ahead", "main")
    world.git("update-ref", "refs/remotes/origin/main", world.commit("r.txt"))
    world.git("switch", "--quiet", "main")
    after = world.git("rev-parse", "--short", "refs/remotes/origin/main")

    p = gwrot(world)
    assert p.returncode == 0, p.stderr
    first, second = p.stdout.splitlines()
    assert first.split() == ["was", "main", before]
    assert second.split()[:3] == ["now", "main", after]
    assert second.endswith("fast-forwarded by 1 commit to origin/main")


def test_json_is_data_on_stdout(world) -> None:
    with_work(world)
    was = world.git("rev-parse", "--short", "refs/heads/fix-parser")
    p = gwrot(world, "--json")
    assert p.returncode == 0, p.stderr
    payload = json.loads(p.stdout)
    assert payload["stem"] == "fix-parser"
    assert payload["branch"] == f"fix-parser-{TODAY}_001"
    assert payload["base"] == "refs/heads/main"
    assert payload["previous"] == "fix-parser"
    assert payload["previous_sha"] == was


def test_it_takes_no_argument(world) -> None:
    world.git("switch", "--quiet", "--create", "fix-parser")
    p = gwrot(world, "some-name")
    assert p.returncode == 2
    assert "unrecognized arguments" in p.stderr
