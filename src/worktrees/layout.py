"""Where a worktree lives.

Derived rather than configured, so two independently-invoked tools cannot
disagree about a location neither can be told: `<PARENT>/.worktrees/<NAME>/
<REPO>`, where PARENT is the directory holding the main checkout and REPO is
its name.
"""

from __future__ import annotations

import os
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
