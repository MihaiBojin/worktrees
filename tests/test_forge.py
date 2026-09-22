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
from pathlib import Path

import pytest
from conftest import env_for

from worktrees import forge, verdicts
from worktrees import repo as R

SRC = str(Path(__file__).resolve().parents[1] / "src")


def _push(world, branch: str, at: Path) -> None:
    """Give the branch an upstream that has everything it has.

    Without one, `unpushed_count` answers None, which is unknown rather than
    zero, and a merged request cannot speak for commits nobody has pushed.
    """
    bare = world.root / f"{branch}.git"
    if not bare.exists():
        subprocess.run(["git", "init", "--bare", "--quiet", str(bare)], check=True)
        world.git("remote", "add", "origin", str(bare))
    world.git("push", "--quiet", "--set-upstream", "origin", branch, at=at)


def stub_gh(tmp_path: Path, rows: list[dict] | None, code: int = 0) -> Path:
    """A `gh` on PATH that answers with `rows`, so no test needs a network."""
    bin_dir = tmp_path / "forge-bin"
    bin_dir.mkdir(exist_ok=True)
    payload = json.dumps(rows if rows is not None else [])
    exe = bin_dir / "gh"
    exe.write_text(f"#!/bin/sh\ncat <<'JSON'\n{payload}\nJSON\nexit {code}\n")
    exe.chmod(0o755)
    # glab must not answer instead; `available` prefers gh but PATH decides.
    return bin_dir


@pytest.fixture
def with_forge(tmp_path, monkeypatch):
    def use(rows: list[dict] | None, code: int = 0):
        bin_dir = stub_gh(tmp_path, rows, code)
        monkeypatch.setenv("PATH", f"{bin_dir}:{os.environ['PATH']}")
        return bin_dir

    return use


# --------------------------------------------------------------------------
# what the forge is asked, and what it answers
# --------------------------------------------------------------------------


def test_a_merged_request_finishes_a_branch_git_cannot_see(world, with_forge) -> None:
    """The stacked case. Its content is upstream, its tree is not."""
    with_forge([{"number": 12, "state": "MERGED"}])
    wt = world.worktree("stacked")
    world.commit("s.txt", "s\n", at=wt)
    _push(world, "stacked", wt)

    rows = verdicts.assess(
        R.worktrees(), "", "refs/heads/main", "main", False, ask_forge=True
    )
    row = next(v for v in rows if v.branch == "stacked")
    assert row.verdict == verdicts.REMOVE
    assert row.why == "its pull request #12 is merged"


def test_a_merged_request_cannot_speak_for_unpushed_commits(world, with_forge) -> None:
    """It says what reached it, and nothing about what never did."""
    with_forge([{"number": 12, "state": "MERGED"}])
    wt = world.worktree("stacked")
    world.commit("s.txt", "s\n", at=wt)
    _push(world, "stacked", wt)
    world.commit("after.txt", "a\n", at=wt)

    rows = verdicts.assess(
        R.worktrees(), "", "refs/heads/main", "main", False, ask_forge=True
    )
    row = next(v for v in rows if v.branch == "stacked")
    assert row.verdict == verdicts.KEEP
    assert "1 commit(s) here are not in it" in row.why


@pytest.mark.parametrize("delete_ignored", [False, True])
def test_a_merged_request_with_an_existing_upstream_checks_ignored_files(
    world, with_forge, delete_ignored
):
    with_forge([{"number": 12, "state": "MERGED"}])
    wt = world.worktree("stacked")
    world.commit(".gitignore", "cache\n", at=wt)
    _push(world, "stacked", wt)
    (wt / "cache").write_text("keep\n")
    rows = verdicts.assess(
        R.worktrees(), "", "refs/heads/main", "main", delete_ignored, ask_forge=True
    )
    row = next(v for v in rows if v.branch == "stacked")
    assert row.verdict == (verdicts.REMOVE if delete_ignored else verdicts.KEEP)


def test_no_upstream_is_unknown_rather_than_zero(world, with_forge) -> None:
    """Branches made here do not track, so this is the normal state for
    exactly the ones a merged request would otherwise reap."""
    with_forge([{"number": 12, "state": "MERGED"}])
    wt = world.worktree("stacked")
    world.commit("s.txt", "s\n", at=wt)

    rows = verdicts.assess(
        R.worktrees(), "", "refs/heads/main", "main", False, ask_forge=True
    )
    row = next(v for v in rows if v.branch == "stacked")
    assert row.verdict == verdicts.UNKNOWN
    assert "no upstream" in row.why


@pytest.fixture
def gone_upstream(world):
    wt = world.worktree("stacked")
    sha = world.commit("s.txt", "s\n", at=wt)
    _push(world, "stacked", wt)
    world.git(
        "update-ref", "-d", "refs/heads/stacked", sha, at=world.root / "stacked.git"
    )
    world.git("fetch", "--prune", "origin")
    assert world.git("config", "--get", "branch.stacked.merge") == "refs/heads/stacked"
    assert not R.upstream_of("stacked")
    assert R.unpushed_count("stacked") is None
    return wt


@pytest.mark.parametrize("state", ["MERGED", "CLOSED", "OPEN", None])
def test_a_gone_upstream_requires_a_merged_request(
    world, with_forge, gone_upstream, state
):
    with_forge([{"number": 12, "state": state}] if state else [])
    rows = verdicts.assess(
        R.worktrees(), "", "refs/heads/main", "main", False, ask_forge=True
    )
    row = next(v for v in rows if v.branch == "stacked")
    assert row.verdict == (verdicts.REMOVE if state == "MERGED" else verdicts.UNKNOWN)
    if state == "MERGED":
        assert row.why == "its pull request #12 is merged and its upstream is gone"
    elif state == "CLOSED":
        assert "upstream is gone" in row.why


@pytest.mark.parametrize("protection", ["dirty", "ignored", "locked", "current"])
def test_a_merged_request_with_a_gone_upstream_keeps_worktree_protections(
    world, with_forge, gone_upstream, protection
):
    with_forge([{"number": 12, "state": "MERGED"}])
    if protection == "dirty":
        (gone_upstream / "s.txt").write_text("unfinished\n")
    elif protection == "ignored":
        world.git("config", "core.excludesFile", str(world.root / "ignore"))
        (world.root / "ignore").write_text("cache\n")
        (gone_upstream / "cache").write_text("keep\n")
    elif protection == "locked":
        world.git("worktree", "lock", str(gone_upstream))
    rows = verdicts.assess(
        R.worktrees(),
        "",
        "refs/heads/main",
        "main",
        False,
        here=str(gone_upstream) if protection == "current" else "",
        ask_forge=True,
    )
    row = next(v for v in rows if v.branch == "stacked")
    assert row.verdict == verdicts.KEEP


def test_prune_removes_a_merged_branch_with_a_gone_upstream(
    world, with_forge, gone_upstream
):
    bin_dir = with_forge([{"number": 12, "state": "MERGED"}])
    env = env_for(world)
    env["PATH"] = f"{bin_dir}{os.pathsep}{env['PATH']}"
    proc = subprocess.run(
        [
            sys.executable,
            "-c",
            "from worktrees.cli import gwp; raise SystemExit(gwp())",
            "--no-fetch",
            "--yes",
        ],
        cwd=str(world.repo),
        capture_output=True,
        text=True,
        env=env,
    )
    assert proc.returncode == 0, proc.stderr
    assert not gone_upstream.exists()
    assert not R.ref_exists("refs/heads/stacked")
    assert world.repo.exists()


def test_an_open_request_decides_nothing(world, with_forge) -> None:
    with_forge([{"number": 12, "state": "OPEN"}])
    wt = world.worktree("live")
    world.commit("l.txt", "l\n", at=wt)

    rows = verdicts.assess(
        R.worktrees(), "", "refs/heads/main", "main", False, ask_forge=True
    )
    row = next(v for v in rows if v.branch == "live")
    assert row.verdict == verdicts.UNKNOWN
    assert "not merged into main" in row.why


@pytest.mark.parametrize(("rows", "code"), [([], 0), (None, 1), ([], 1)])
def test_silence_leaves_the_answer_to_git(world, with_forge, rows, code) -> None:
    """No request, not authenticated, no remote: all the same, and none of
    them is a `no`."""
    with_forge(rows, code)
    wt = world.worktree("quiet")
    world.commit("q.txt", "q\n", at=wt)

    out = verdicts.assess(
        R.worktrees(), "", "refs/heads/main", "main", False, ask_forge=True
    )
    row = next(v for v in out if v.branch == "quiet")
    assert row.verdict == verdicts.UNKNOWN
    assert "no upstream" in row.why


def test_the_forge_is_never_asked_about_a_branch_git_settled(world, with_forge) -> None:
    """It costs a round trip, and merged is merged."""
    bin_dir = with_forge([{"number": 99, "state": "MERGED"}])
    marker = bin_dir / "asked"
    (bin_dir / "gh").write_text(f"#!/bin/sh\ntouch {marker}\necho '[]'\n")
    (bin_dir / "gh").chmod(0o755)

    world.worktree("done")  # identical tree to main: settled without asking
    verdicts.assess(R.worktrees(), "", "refs/heads/main", "main", False, ask_forge=True)
    assert not marker.exists()


def test_no_forge_keeps_it_offline(world, with_forge) -> None:
    bin_dir = with_forge([{"number": 12, "state": "MERGED"}])
    marker = bin_dir / "asked"
    (bin_dir / "gh").write_text(f"#!/bin/sh\ntouch {marker}\necho '[]'\n")
    (bin_dir / "gh").chmod(0o755)

    wt = world.worktree("stacked")
    world.commit("s.txt", "s\n", at=wt)
    verdicts.assess(
        R.worktrees(), "", "refs/heads/main", "main", False, ask_forge=False
    )
    assert not marker.exists()


def test_request_for_reads_the_first_row(world, with_forge) -> None:
    with_forge([{"number": 7, "state": "merged"}])
    got = forge.request_for("anything")
    assert got is not None
    assert (got.number, got.state, got.noun) == (7, "MERGED", "pull request")


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


def test_a_failed_fetch_stops_the_forge_being_asked(world, tmp_path) -> None:
    """`gh` waits 30 seconds for a network that just refused git."""
    marker = tmp_path / "asked"
    bin_dir = _marker_gh(tmp_path, marker)
    world.git("remote", "add", "origin", str(world.root / "nowhere.git"))
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
    world.git("remote", "add", "origin", str(bare))
    world.git("push", "--quiet", "origin", "main")
    wt = world.worktree("unmerged")
    world.commit("u.txt", "u\n", at=wt)

    p = _gws(world, bin_dir)
    assert p.returncode == 0, p.stderr
    assert "could not be fetched" not in p.stderr
    assert marker.exists()


# --------------------------------------------------------------------------
# the question goes to the repository the command is standing in
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
    # Only this directory, so `available` finds the tool under test and not
    # the real one beside it.
    monkeypatch.setenv("PATH", str(bin_dir))
    monkeypatch.setenv(variable, "someone/else")

    assert forge.request_for("any-branch") is None
    assert seen.read_text() == "unset"


def test_the_rest_of_the_environment_reaches_the_forge(tmp_path, monkeypatch) -> None:
    """The control. Scrubbing two names must not hand gh an empty
    environment: its token and its config path live there."""
    seen = tmp_path / "seen"
    bin_dir = _reports_env(tmp_path, "gh", "GH_TOKEN", seen)
    monkeypatch.setenv("PATH", str(bin_dir))
    monkeypatch.setenv("GH_TOKEN", "kept")

    assert forge.request_for("any-branch") is None
    assert seen.read_text() == "kept"
