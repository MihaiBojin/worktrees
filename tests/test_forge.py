"""The forge as evidence, for the case content cannot answer.

A branch merged as part of a stack reaches the head branch across several
squashes, and the intermediate state it holds differs from the final one in
the same regions. To a diff that is indistinguishable from real work left
over, so something outside git has to say.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest
from conftest import env_for
from test_branch_forge import evidence, stub
from test_branches import cli

from worktrees import forge
from worktrees import repo as R

SRC = str(Path(__file__).resolve().parents[1] / "src")


def forge_worktree(world) -> Path:
    """A worktree whose change content cannot settle.

    The head branch edited the same path further, the shape a branch merged
    as part of a stack leaves behind, so only the forge can say.
    """
    wt = world.worktree("feature")
    world.commit("feature", "branch\n", at=wt)
    world.commit("feature", "later edits\n")
    world.git("remote", "add", "origin", "git@github.com:owner/repo.git")
    world.git("update-ref", "refs/remotes/origin/main", "main")
    return wt


def gws(world, env, *args: str) -> dict:
    p = cli(world, "status", "--no-fetch", "--json", *args, env=env)
    assert p.returncode == 0, p.stderr
    return {v["branch"]: v for v in json.loads(p.stdout)["verdicts"]}


def test_a_merged_request_finishes_a_worktree_git_cannot_see(world) -> None:
    forge_worktree(world)
    assert gws(world, env_for(world), "--no-forge")["feature"]["verdict"] == "keep"

    env = stub(world, "github", [evidence(world, "github")])
    row = gws(world, env)["feature"]
    assert row["verdict"] == "remove", row
    assert row["why"] == "pull request #17 merged this tip into the head branch"


def test_a_merged_request_cannot_speak_for_a_later_commit(world) -> None:
    """It names the tip it merged, and nothing made after it."""
    wt = forge_worktree(world)
    row = evidence(world, "github")
    world.commit("after", "a\n", at=wt)
    row = gws(world, stub(world, "github", [row]))["feature"]
    assert row["verdict"] == "unknown"
    assert row["why"] == "the request does not identify this branch tip"


def test_an_open_request_decides_nothing(world) -> None:
    forge_worktree(world)
    env = stub(world, "github", [evidence(world, "github", state="OPEN")])
    row = gws(world, env)["feature"]
    assert row["verdict"] == "keep"
    assert row["why"] == "pull request #17 is open, not merged"


@pytest.mark.parametrize(("answer", "code"), [("[]", 0), ("", 1), ("[]", 1)])
def test_a_silent_forge_leaves_the_answer_to_git(world, answer, code) -> None:
    """No request, not signed in, failing: none of them is a `no`."""
    forge_worktree(world)
    marker = world.root / "asked"
    bin_dir = world.root / "silent-bin"
    bin_dir.mkdir()
    exe = bin_dir / "gh"
    exe.write_text(f"#!/bin/sh\n: > {marker}\nprintf '%s' '{answer}'\nexit {code}\n")
    exe.chmod(0o755)
    env = env_for(world)
    env["PATH"] = f"{bin_dir}{os.pathsep}{env['PATH']}"
    row = gws(world, env)["feature"]
    assert marker.exists(), "the forge was not asked"
    assert (row["verdict"], row["why"]) == (
        "keep",
        "no proof that its change is in the head branch",
    )


@pytest.mark.parametrize("protection", ["dirty", "ignored", "locked"])
def test_a_merged_request_keeps_worktree_protections(world, protection) -> None:
    wt = forge_worktree(world)
    env = stub(world, "github", [evidence(world, "github")])
    if protection == "dirty":
        (wt / "feature").write_text("unfinished\n")
    elif protection == "ignored":
        world.git("config", "core.excludesFile", str(world.root / "ignore"))
        (world.root / "ignore").write_text("cache\n")
        (wt / "cache").write_text("keep\n")
    else:
        world.git("worktree", "lock", str(wt))
    assert gws(world, env)["feature"]["verdict"] == "keep"


def test_prune_removes_a_worktree_the_forge_proved(world) -> None:
    wt = forge_worktree(world)
    env = stub(world, "github", [evidence(world, "github")])
    p = cli(world, "--no-fetch", "--yes", entry="gwp", env=env)
    assert p.returncode == 0, p.stderr
    assert not wt.exists()
    assert not R.ref_exists("refs/heads/feature")
    assert world.repo.exists()


def test_gwr_asks_the_forge_about_the_one_it_was_given(world) -> None:
    """`gwr` scans without the forge, then asks about the worktree picked."""
    wt = forge_worktree(world)
    env = stub(world, "github", [evidence(world, "github")])
    p = cli(world, "feature", "--no-fetch", "--yes", entry="gwr", env=env)
    assert p.returncode == 0, p.stderr
    assert not wt.exists()
    assert not R.ref_exists("refs/heads/feature")


def _marker(world, marker: Path) -> dict[str, str]:
    """An env whose `gh` records having been asked and answers nothing."""
    bin_dir = world.root / "marker-bin"
    bin_dir.mkdir(exist_ok=True)
    exe = bin_dir / "gh"
    exe.write_text(f"#!/bin/sh\n: > {marker}\nprintf '[]\\n'\n")
    exe.chmod(0o755)
    env = env_for(world)
    env["PATH"] = f"{bin_dir}{os.pathsep}{env['PATH']}"
    return env


def test_the_forge_is_never_asked_about_a_branch_git_settled(world) -> None:
    """It costs a round trip, and merged is merged."""
    marker = world.root / "asked"
    world.git("remote", "add", "origin", "git@github.com:owner/repo.git")
    world.git("update-ref", "refs/remotes/origin/main", "main")
    world.worktree("done")  # identical tree to main: settled without asking
    assert gws(world, _marker(world, marker))["done"]["verdict"] == "remove"
    assert not marker.exists()


def test_no_forge_keeps_it_offline(world) -> None:
    marker = world.root / "asked"
    forge_worktree(world)
    env = _marker(world, marker)
    gws(world, env, "--no-forge")
    assert not marker.exists()
    gws(world, env)
    assert marker.exists(), "the control: without --no-forge it is asked"


def test_the_cli_offers_no_forge(world) -> None:
    from conftest import env_for

    proc = subprocess.run(
        [
            sys.executable,
            "-c",
            "from worktrees.cli import gws; raise SystemExit(gws())",
            "--help",
        ],
        cwd=str(world.repo),
        capture_output=True,
        text=True,
        env=env_for(world),
    )
    assert "--no-forge" in proc.stdout


# --------------------------------------------------------------------------
# the forge is over the network the fetch just used
# --------------------------------------------------------------------------


def _marker_gh(tmp_path: Path, marker: Path) -> Path:
    """A `gh` that records having been asked and answers nothing."""
    bin_dir = tmp_path / "marker-bin"
    bin_dir.mkdir(exist_ok=True)
    exe = bin_dir / "gh"
    exe.write_text(f"#!/bin/sh\n: > {marker}\nprintf '[]\\n'\n")
    exe.chmod(0o755)
    return bin_dir


def _gws(world, bin_dir: Path):
    env = env_for(world)
    env["PATH"] = f"{bin_dir}{os.pathsep}{env['PATH']}"
    return subprocess.run(
        [
            sys.executable,
            "-c",
            "from worktrees.cli import gws; raise SystemExit(gws())",
        ],
        cwd=str(world.repo),
        capture_output=True,
        text=True,
        env=env,
    )


def _github_at(world, path: Path) -> None:
    """A remote whose URL names GitHub, which is the only kind the forge is
    asked about, and whose fetch goes to `path`."""
    url = "git@github.com:owner/repo.git"
    world.git("config", f"url.{path}.insteadOf", url)
    world.git("remote", "add", "origin", url)


def test_a_failed_fetch_stops_the_forge_being_asked(world, tmp_path) -> None:
    """`gh` waits 30 seconds for a network that just refused git."""
    marker = tmp_path / "asked"
    bin_dir = _marker_gh(tmp_path, marker)
    _github_at(world, world.root / "nowhere.git")
    world.git("update-ref", "refs/remotes/origin/main", "main")
    wt = world.worktree("unmerged")
    world.commit("u.txt", "u\n", at=wt)

    p = _gws(world, bin_dir)
    assert p.returncode == 0, p.stderr
    assert "could not be fetched" in p.stderr
    assert "not asking the forge" in p.stderr
    assert not marker.exists()


def test_a_reachable_remote_leaves_the_forge_asked(world, tmp_path) -> None:
    """The control. Without it the test above passes on a run that never
    reached the forge for some other reason."""
    marker = tmp_path / "asked"
    bin_dir = _marker_gh(tmp_path, marker)
    bare = world.root / "origin.git"
    subprocess.run(["git", "init", "--bare", "--quiet", str(bare)], check=True)
    _github_at(world, bare)
    world.git("push", "--quiet", "origin", "main")
    wt = world.worktree("unmerged")
    world.commit("u.txt", "u\n", at=wt)

    p = _gws(world, bin_dir)
    assert p.returncode == 0, p.stderr
    assert "could not be fetched" not in p.stderr
    assert marker.exists()


# --------------------------------------------------------------------------
# the question goes to the repository the remote names
# --------------------------------------------------------------------------


def _reports_env(tmp_path: Path, tool: str, variable: str, seen: Path) -> Path:
    """A forge CLI that writes down whether the redirect reached it."""
    bin_dir = tmp_path / f"{tool}-bin"
    bin_dir.mkdir(exist_ok=True)
    exe = bin_dir / tool
    exe.write_text(
        f'#!/bin/sh\nprintf "%s" "${{{variable}-unset}}" > {seen}\nprintf "[]\\n"\n'
    )
    exe.chmod(0o755)
    return bin_dir


@pytest.mark.parametrize(
    ("tool", "variable"), [("gh", "GH_REPO"), ("glab", "GITLAB_REPO")]
)
def test_a_stray_variable_cannot_redirect_the_question(
    tmp_path, monkeypatch, tool: str, variable: str
) -> None:
    """Either variable beats the directory the CLI runs in, and a merged
    request found in the wrong repository reads here as proof."""
    seen = tmp_path / "seen"
    bin_dir = _reports_env(tmp_path, tool, variable, seen)
    # Only this directory, so the tool under test answers and not the real
    # one beside it.
    monkeypatch.setenv("PATH", str(bin_dir))
    monkeypatch.setenv(variable, "someone/else")

    assert forge.query([tool, "pr", "list"]) == []
    assert seen.read_text() == "unset"


def test_the_rest_of_the_environment_reaches_the_forge(tmp_path, monkeypatch) -> None:
    """The control. Scrubbing two names must not hand gh an empty
    environment: its token and its config path live there."""
    seen = tmp_path / "seen"
    bin_dir = _reports_env(tmp_path, "gh", "GH_TOKEN", seen)
    monkeypatch.setenv("PATH", str(bin_dir))
    monkeypatch.setenv("GH_TOKEN", "kept")

    assert forge.query(["gh", "pr", "list"]) == []
    assert seen.read_text() == "kept"


# --------------------------------------------------------------------------
# a fetch under 10 minutes old is used as it stands
# --------------------------------------------------------------------------


def _with_origin(world) -> None:
    bare = world.root / "origin.git"
    subprocess.run(["git", "init", "--bare", "--quiet", str(bare)], check=True)
    world.git("remote", "add", "origin", str(bare))
    world.git("push", "--quiet", "origin", "main")


def _gws_args(world, *args: str):
    return subprocess.run(
        [
            sys.executable,
            "-c",
            "from worktrees.cli import gws; raise SystemExit(gws())",
            *args,
        ],
        cwd=str(world.repo),
        capture_output=True,
        text=True,
        env=env_for(world),
    )


def test_a_recent_fetch_is_not_repeated(world) -> None:
    _with_origin(world)
    first = _gws_args(world, "--no-forge")
    assert first.returncode == 0, first.stderr
    assert "fetched" not in first.stderr

    second = _gws_args(world, "--no-forge", "-v")
    assert second.returncode == 0, second.stderr
    assert "fetched 0 min ago; using those refs" in second.stderr
    assert "+ git fetch" not in second.stderr


def test_a_reused_fetch_says_so_once(world) -> None:
    """Not also the --no-fetch warning, for a flag nobody passed."""
    _with_origin(world)
    _gws_args(world, "--no-forge")
    p = _gws_args(world, "--no-forge")
    assert p.returncode == 0, p.stderr
    assert "fetched 0 min ago; using those refs" in p.stderr
    assert "--no-fetch" not in p.stderr


def test_force_refresh_fetches_whatever_the_age(world) -> None:
    _with_origin(world)
    _gws_args(world, "--no-forge")
    p = _gws_args(world, "--no-forge", "--force-refresh", "-v")
    assert p.returncode == 0, p.stderr
    assert "+ git fetch --prune origin" in p.stderr


def test_a_fetch_over_10_minutes_old_is_repeated(world) -> None:
    _with_origin(world)
    _gws_args(world, "--no-forge")
    head = Path(world.git("rev-parse", "--git-path", "FETCH_HEAD").strip())
    head = head if head.is_absolute() else world.repo / head
    old = time.time() - 11 * 60
    os.utime(head, (old, old))
    p = _gws_args(world, "--no-forge", "-v")
    assert "+ git fetch --prune origin" in p.stderr


def test_gwa_reuses_a_fetch_another_command_made(world) -> None:
    _with_origin(world)
    _gws_args(world, "--no-forge")
    p = subprocess.run(
        [
            sys.executable,
            "-c",
            "from worktrees.cli import gwa; raise SystemExit(gwa())",
            "fresh",
            "-v",
        ],
        cwd=str(world.repo),
        capture_output=True,
        text=True,
        env=env_for(world),
    )
    assert p.returncode == 0, p.stderr
    assert "fetched 0 min ago; using those refs" in p.stderr
    assert "+ git fetch" not in p.stderr
