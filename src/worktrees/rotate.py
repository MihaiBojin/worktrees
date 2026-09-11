"""Start the next branch in a series, or catch the head branch up.

`new-branch` names a branch. This one works out the name, from the branch you
are standing on, and starts it. Two commands rather than one with an optional
argument: naming and continuing are different jobs, and a command doing both
needs an "and" in its description.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from datetime import date

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


@dataclass(frozen=True)
class Rotated:
    branch: str
    stem: str
    base: str  # the full ref
    sha: str


@dataclass(frozen=True)
class CaughtUp:
    branch: str
    at: str  # the ref it now matches


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
    raise Refusal(f"every number from 001 to 999 is taken for {base_stem} on {day}")


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
            "after; 'worktrees new-branch <name>' names one"
        )

    remote = R.remote(repo=repo)
    online = fetch
    if fetch and remote and not R.fetch(remote, repo=repo):
        online = False
        if callable(warn):
            warn(f"{remote} could not be fetched; working from what is already here")

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

    chosen = next_name(stem(branch), remote, repo=repo)
    sha = short_sha(base, repo=repo)
    switch_create(chosen, base, repo=repo)
    return Rotated(chosen, stem(branch), base, sha.out.strip() if sha else "")


def _catch_up(
    branch: str, base: str, repo: str | os.PathLike[str] | None = None
) -> CaughtUp:
    """There is no chain to continue from the head branch, so bring it level.

    --ff-only refuses when the local copy is ahead, which is the answer: those
    commits are a change of their own and belong on a branch.
    """
    ahead = R.count_between(base, f"refs/heads/{branch}", repo=repo)
    count = int(ahead.out.strip() or 0) if ahead else 0
    if count:
        listed = log_oneline(f"{base}..refs/heads/{branch}", repo=repo)
        lines = "\n".join(f"  {line}" for line in listed.lines) if listed else ""
        raise Refusal(
            f"{branch} is {count} commit(s) ahead of {R.ref_name(base)}, so it "
            f"cannot be fast-forwarded:\n{lines}\n"
            "Those commits are a change of their own; "
            "'worktrees new-branch <name>' puts them on one."
        )
    merge_ff_only(base, repo=repo)
    return CaughtUp(branch, R.ref_name(base))
