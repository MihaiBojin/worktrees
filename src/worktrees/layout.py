"""Where a worktree lives.

Derived rather than configured, so two independently-invoked tools cannot
disagree about a location neither can be told: `<PARENT>/.worktrees/<NAME>/
<REPO>`, where PARENT is the directory holding the main checkout and REPO is
its name.
"""

from __future__ import annotations

import os
from collections.abc import Iterable
from pathlib import Path

from . import repo as R


def worktrees_root(main: str) -> Path:
    """The directory every worktree of this repository sits under."""
    return Path(main).parent / ".worktrees"


def destination(name: str, main: str) -> Path:
    """Where the worktree for branch `name` belongs.

    A branch name may hold slashes, and they become directories, which is why
    the repository name goes last: `feat/oauth` gives
    `.worktrees/feat/oauth/<repo>` rather than colliding with `.worktrees/feat`.
    """
    return worktrees_root(main) / name / Path(main).name


def neighbours(main: str, ours: Iterable[str]) -> int:
    """How many worktrees under the shared root belong to somebody else.

    Repositories side by side share one `.worktrees` root, a directory each,
    and every command here answers for one of them. Standing in a repository
    with one worktree you can see six directories, and nothing says the other
    five are a sibling's.

    No git call: a worktree carries a `.git` file, so a directory holding one
    is somebody's and a directory holding none is a leftover. The walk stops
    descending the moment it finds one, because a branch name with slashes
    nests and the repository name always goes last.
    """
    root = worktrees_root(main)
    if not root.is_dir():
        return 0
    mine = {Path(p).resolve() for p in ours}
    found = 0
    stack = [root]
    while stack:
        for entry in _subdirectories(stack.pop()):
            if (entry / ".git").exists():
                found += entry.resolve() not in mine
            else:
                stack.append(entry)
    return found


def _subdirectories(path: Path) -> list[Path]:
    """Its directories, or none when it cannot be read."""
    try:
        return [child for child in path.iterdir() if child.is_dir()]
    except OSError:  # pragma: no cover
        return []


def owner_of(candidate: str, repo: str | os.PathLike[str] | None = None) -> str:
    """The repository owning the checkout at `candidate`, when it is not ours.

    Empty when we own it, or when there is no repository there at all.

    git is what answers this. `git worktree list` cannot: a worktree belonging
    to another repository is one this repository has never heard of, so the
    list comes back empty and reads exactly like "nothing is there", which is
    how a `gwa` that should have refused lands you in somebody else's
    checkout.
    """
    theirs = R.common_dir(repo=candidate)
    if not theirs or not theirs.out.strip():
        return ""
    ours = R.common_dir(repo=repo)
    if not ours or not ours.out.strip():
        return ""
    t = Path(theirs.out.strip()).resolve()
    if t == Path(ours.out.strip()).resolve():
        return ""
    return str(t.parent)
