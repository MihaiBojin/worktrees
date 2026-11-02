"""Start the next branch in a series, or catch the head branch up.

`gwbc` names a branch. This one works out the name, from the branch you
are standing on, and starts it. Two commands rather than one with an optional
argument: naming and continuing are different jobs, and a command doing both
needs an "and" in its description.
"""

from __future__ import annotations

import os
import re
from datetime import date

from . import render
from . import repo as R
from .git import git
from .new_branch import Refusal, short_sha, switch_create

# <stem>-YYYY-MM-DD_NNN. Three digits, so the sequence cannot be read as
# another field of the date the way a two-digit one beside -09-10 can.
SUFFIX = re.compile(r"-\d{4}-\d{2}-\d{2}_\d{3}$")


@git("merge --ff-only $ref", mutates=True)
def merge_ff_only(ref: str) -> None:
    """Never a rebase: rewriting local commits on the head branch is the
    class this tooling refuses everywhere else."""


@git("log --oneline --no-decorate $range")
def log_oneline(range: str) -> None:
    """The commits in a range, for saying which ones are in the way."""


class Rotated:
    __slots__ = ("ahead", "base", "branch", "previous", "previous_sha", "sha", "stem")

    branch: str
    stem: str
    base: str  # the full ref
    sha: str
    previous: str  # the branch it was on, left as it was
    previous_sha: str
    ahead: int  # commits on `previous` that `base` lacks

    def __init__(
        self,
        branch: str,
        stem: str,
        base: str,
        sha: str,
        previous: str,
        previous_sha: str,
        ahead: int,
    ) -> None:
        self.branch = branch
        self.stem = stem
        self.base = base
        self.sha = sha
        self.previous = previous
        self.previous_sha = previous_sha
        self.ahead = ahead


class CaughtUp:
    __slots__ = ("at", "branch", "count", "previous_sha", "sha")

    branch: str
    at: str  # the ref it now matches
    previous_sha: str
    sha: str
    count: int  # commits the fast-forward brought in

    def __init__(
        self, branch: str, at: str, previous_sha: str, sha: str, count: int
    ) -> None:
        self.branch = branch
        self.at = at
        self.previous_sha = previous_sha
        self.sha = sha
        self.count = count


def stem(branch: str) -> str:
    """The branch with any suffix a previous rotation added taken off.

    So four rotations in a day give four siblings rather than one name
    carrying four suffixes.
    """
    return SUFFIX.sub("", branch)


def next_name(
    base_stem: str,
    remote: str,
    today: str | None = None,
    repo: str | os.PathLike[str] | None = None,
) -> str:
    """`<stem>-YYYY-MM-DD_NNN` at the first NNN free today.

    Free means free here and on the remote: a number nothing holds locally
    but the remote already carries is not free, or two people rotate into the
    same name.
    """
    day = today or date.today().isoformat()
    for n in range(1, 1000):
        name = f"{base_stem}-{day}_{n:03d}"
        if R.ref_exists(f"refs/heads/{name}", repo=repo):
            continue
        if remote and R.ref_exists(f"refs/remotes/{remote}/{name}", repo=repo):
            continue
        return name
    raise Refusal(
        f"every number from 001 to 999 is taken for {render.branch(base_stem)} on {day}"
    )


def rotate(
    fetch: bool,
    warn: object = None,
    repo: str | os.PathLike[str] | None = None,
) -> Rotated | CaughtUp:
    """Start the next branch after this one, or catch the head branch up."""
    branch = R.current_branch(repo=repo).out.strip()
    if not branch:
        raise Refusal(
            "HEAD is detached, so there is no branch to name the next one "
            "after; 'gwbc <name>' names one"
        )

    remote = R.remote(repo=repo)
    online = fetch
    if fetch and remote and not R.fetch(remote, repo=repo):
        online = False
        if callable(warn):
            warn(
                f"{render.ref(remote)} could not be fetched; working from what is "
                "already here"
            )

    base, warning = R.head_ref(remote, online=online, repo=repo)
    if warning and callable(warn):
        warn(warning)
    if not base:
        hint = remote or "origin"
        raise Refusal(
            "cannot tell which branch this repository branches from; record it "
            f"with: git remote set-head {hint} --auto"
        )

    head_name = R.ref_name(base)
    if remote:
        head_name = head_name.removeprefix(remote + "/")

    if branch == head_name:
        return _catch_up(branch, base, repo=repo)

    local = f"refs/heads/{branch}"
    sha = _short(base, repo=repo)
    ahead = _count(base, local, repo=repo)
    if not ahead and not _count(local, base, repo=repo):
        raise Refusal(
            f"{render.branch(branch)} is already at {render.ref(R.ref_name(base))} "
            f"({sha}) with no commits "
            "of its own\nthe next branch would be the same commit under another "
            "name; commit to this one first"
        )

    chosen = next_name(stem(branch), remote, repo=repo)
    previous_sha = _short(local, repo=repo)
    switch_create(chosen, base, repo=repo)
    return Rotated(chosen, stem(branch), base, sha, branch, previous_sha, ahead)


def _short(ref: str, repo: str | os.PathLike[str] | None = None) -> str:
    sha = short_sha(ref, repo=repo)
    return sha.out.strip() if sha else ""


def _count(a: str, b: str, repo: str | os.PathLike[str] | None = None) -> int:
    """Commits reachable from `b` and not from `a`."""
    counted = R.count_between(a, b, repo=repo)
    return int(counted.out.strip() or 0) if counted else 0


def _catch_up(
    branch: str, base: str, repo: str | os.PathLike[str] | None = None
) -> CaughtUp:
    """There is no chain to continue from the head branch, so bring it level.

    --ff-only refuses when the local copy is ahead, which is the answer: those
    commits are a change of their own and belong on a branch.
    """
    local = f"refs/heads/{branch}"
    count = _count(base, local, repo=repo)
    if count:
        listed = log_oneline(f"{base}..refs/heads/{branch}", repo=repo)
        lines = "\n".join(f"  {line}" for line in listed.lines) if listed else ""
        raise Refusal(
            f"{render.branch(branch)} is {count} commit(s) ahead of "
            f"{render.ref(R.ref_name(base))}, so it "
            f"cannot be fast-forwarded:\n{lines}\n"
            "Those commits are a change of their own; "
            "'gwbc <name>' puts them on one."
        )
    behind = _count(local, base, repo=repo)
    previous_sha = _short(local, repo=repo)
    if not behind:
        raise Refusal(
            f"{render.branch(branch)} is already at "
            f"{render.ref(R.ref_name(base))} ({previous_sha})\n"
            "there is nothing to catch up"
        )
    merge_ff_only(base, repo=repo)
    return CaughtUp(
        branch, R.ref_name(base), previous_sha, _short(local, repo=repo), behind
    )
