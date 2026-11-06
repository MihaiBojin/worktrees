"""The forge as evidence, for the case content cannot answer.

A branch merged as part of a stack reaches the head branch across several
squashes, and the intermediate state it holds differs from the final one in
the same regions. To a diff that is indistinguishable from real work left
over, so something outside git has to say. `gw sync` asks it; assessment
reads what that sync recorded and asks nothing.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from conftest import env_for
from test_branch_forge import merged, synced
from test_branches import cli
from test_sync import forge_remote

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
    world.git("symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/main")
    return wt


def gwp(world, *args: str) -> dict:
    p = cli(world, "prune", "--json", *args)
    assert p.returncode == 0, p.stderr
    return {v["branch"]: v for v in json.loads(p.stdout)["verdicts"]}


def test_a_synced_merged_request_finishes_a_worktree_git_cannot_see(world) -> None:
    forge_worktree(world)
    assert gwp(world)["feature"]["verdict"] == "keep"

    synced(world, merged(world))
    row = gwp(world)["feature"]
    assert row["verdict"] == "remove", row
    assert row["why"] == "pull request #17 merged this tip into the head branch"


def test_a_merged_request_cannot_speak_for_a_later_commit(world) -> None:
    """It names the tip it merged, and nothing made after it."""
    wt = forge_worktree(world)
    synced(world, merged(world))
    world.commit("after", "a\n", at=wt)
    assert gwp(world)["feature"]["verdict"] == "keep"


def test_an_open_request_decides_nothing(world) -> None:
    forge_worktree(world)
    synced(world, merged(world, state="OPEN"))
    row = gwp(world)["feature"]
    assert row["verdict"] == "keep"
    assert row["why"] == "pull request #17 is open, not merged"


@pytest.mark.parametrize("protection", ["dirty", "ignored", "locked"])
def test_a_merged_request_keeps_worktree_protections(world, protection) -> None:
    wt = forge_worktree(world)
    synced(world, merged(world))
    if protection == "dirty":
        (wt / "feature").write_text("unfinished\n")
    elif protection == "ignored":
        world.git("config", "core.excludesFile", str(world.root / "ignore"))
        (world.root / "ignore").write_text("cache\n")
        (wt / "cache").write_text("keep\n")
    else:
        world.git("worktree", "lock", str(wt))
    assert gwp(world)["feature"]["verdict"] == "keep"


def test_prune_removes_a_worktree_the_sync_proved(world) -> None:
    wt = forge_worktree(world)
    synced(world, merged(world))
    p = cli(world, "--yes", entry="gwp")
    assert p.returncode == 0, p.stderr
    assert not wt.exists()
    assert not R.ref_exists("refs/heads/feature")
    assert world.repo.exists()


def test_gwr_removes_a_worktree_the_sync_proved(world) -> None:
    wt = forge_worktree(world)
    synced(world, merged(world))
    p = cli(world, "feature", "--yes", entry="gwr")
    assert p.returncode == 0, p.stderr
    assert not wt.exists()
    assert not R.ref_exists("refs/heads/feature")


# --------------------------------------------------------------------------
# only gw sync runs gh or glab
# --------------------------------------------------------------------------


def _recording(world) -> tuple[dict[str, str], Path]:
    """An env whose `gh` and `glab` write each call down and answer nothing.

    Each line ends with the redirect variables as the CLI saw them, and the
    env sets both, so a line ending `unset unset` shows they were dropped.
    """
    log = world.root / "forge-calls"
    bin_dir = world.root / "recording-bin"
    bin_dir.mkdir()
    for tool in ("gh", "glab"):
        exe = bin_dir / tool
        exe.write_text(
            "#!/bin/sh\n"
            f'echo "{tool} $* ${{GH_REPO-unset}} ${{GITLAB_REPO-unset}}" >> {log}\n'
            'echo "[]"\n'
        )
        exe.chmod(0o755)
    env = env_for(world)
    env["PATH"] = f"{bin_dir}{os.pathsep}{env['PATH']}"
    env["GH_REPO"] = env["GITLAB_REPO"] = "someone/else"
    return env, log


def _unsettled(world, provider: str) -> Path:
    """A forge host, and a worktree and a loose branch git cannot settle."""
    forge_remote(world, provider)
    wt = world.worktree("feature")
    world.commit("feature", "branch\n", at=wt)
    world.commit("feature", "later edits\n")
    world.git("branch", "loose", "feature")
    return wt


@pytest.mark.parametrize("provider", ["github", "gitlab"])
@pytest.mark.parametrize(
    ("entry", "args"),
    [
        ("gwp", ("--yes",)),
        ("gwr", ("feature", "--yes")),
        ("gwbs", ()),
        ("gwbd", ("loose", "--dry-run")),
        ("gwa", ("fresh",)),
        ("gwbc", ("fresh",)),
        ("gwrot", ()),
        ("gw", ("list", "--list")),
    ],
)
def test_only_sync_asks_the_forge(world, provider, entry, args) -> None:
    wt = _unsettled(world, provider)
    env, log = _recording(world)
    p = cli(world, *args, entry=entry, env=env)
    assert p.returncode in (0, 1, 3), p.stderr
    assert not log.exists(), log.read_text()
    assert wt.exists()


@pytest.mark.parametrize("provider", ["github", "gitlab"])
def test_sync_asks_the_forge(world, provider) -> None:
    """The control for the test above: the same repository, the same fake.
    Every question names the remote's repository, and no redirect reaches
    the CLI."""
    _unsettled(world, provider)
    env, log = _recording(world)
    p = cli(world, "sync", env=env)
    assert p.returncode == 0, p.stderr
    tool, repo = (
        ("gh", "github.com/owner/repo")
        if provider == "github"
        else ("glab", "https://gitlab.com/owner/repo")
    )
    calls = log.read_text().splitlines()
    assert len(calls) == 3, calls  # feature, loose and main
    for call in calls:
        assert call.startswith(f"{tool} "), call
        assert f" --repo {repo} " in call, call
        assert call.endswith(" unset unset"), call


@pytest.mark.parametrize("entry", ["gws", "gwp", "gwr", "gwbs", "gwbd"])
def test_no_command_takes_no_forge(world, entry) -> None:
    p = cli(world, "--no-forge", entry=entry)
    assert p.returncode == 2
    assert "unrecognized arguments: --no-forge" in p.stderr


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
