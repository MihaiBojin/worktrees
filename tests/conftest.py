"""A repository fixture that needs no global config, key, forge or network.

GIT_CONFIG_GLOBAL carries the weight: without it git still reads
$HOME/.gitconfig, and setting HOME alone still leaves $XDG_CONFIG_HOME/git/
config. On a machine with commit.gpgsign=true a fixture inheriting it fails at
the first commit and every assertion after that is meaningless.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

# Fixed dates make the first commit's sha a constant, so a test may assert on
# a literal one.
ENV = {
    "GIT_AUTHOR_NAME": "T",
    "GIT_AUTHOR_EMAIL": "t@localhost",
    "GIT_AUTHOR_DATE": "@1700000000 +0000",
    "GIT_COMMITTER_NAME": "T",
    "GIT_COMMITTER_EMAIL": "t@localhost",
    "GIT_COMMITTER_DATE": "@1700000000 +0000",
}


class World:
    """One repository, its worktrees, and a git that runs against it."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.repo = root / "repo"
        self.parent = root

    def git(self, *args: str, at: Path | None = None) -> str:
        proc = subprocess.run(
            ["git", "-C", str(at or self.repo), *args],
            capture_output=True,
            text=True,
            check=True,
        )
        return proc.stdout.strip()

    def commit(self, name: str, body: str = "x", at: Path | None = None) -> str:
        (at or self.repo).joinpath(name).write_text(body)
        self.git("add", "--", name, at=at)
        self.git("commit", "--quiet", "-m", name, at=at)
        return self.git("rev-parse", "HEAD", at=at)

    def worktree(self, branch: str, base: str = "main") -> Path:
        """The derived layout: <PARENT>/.worktrees/<branch>/<repo>."""
        path = self.parent / ".worktrees" / branch / self.repo.name
        path.parent.mkdir(parents=True, exist_ok=True)
        self.git("worktree", "add", "--quiet", "-b", branch, str(path), base)
        return path


@pytest.fixture
def world(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> World:
    for key, value in ENV.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(tmp_path / "gitconfig"))
    monkeypatch.setenv("GIT_CONFIG_SYSTEM", "/dev/null")
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    (tmp_path / "gitconfig").write_text("[init]\n\tdefaultBranch = main\n")

    w = World(tmp_path)
    w.repo.mkdir()
    subprocess.run(
        ["git", "init", "--quiet", "-b", "main", str(w.repo)],
        check=True,
        capture_output=True,
    )
    w.commit("README")
    monkeypatch.chdir(w.repo)
    return w


@pytest.fixture(autouse=True)
def fresh_log() -> None:
    """Every test asserts on the git calls its own run issued."""
    from worktrees.git import options

    options.log.clear()
    options.verbose = bool(os.environ.get("WT_VERBOSE"))
