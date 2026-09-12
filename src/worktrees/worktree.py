"""Adding, moving and removing a checkout.

Each of these lands the caller somewhere, so each prints a destination on
stdout and nothing else. A binary cannot cd its caller; the shim reads that
line and does the move.
"""

from __future__ import annotations

import os
from pathlib import Path

from . import layout
from . import repo as R
from .git import GitError, Refused, git
from .new_branch import Refusal, check_ref_format
from .prune import _prune_empty_parents, ref_delete, worktree_remove
from .verdicts import REMOVE, Verdict, assess


@git("worktree add --no-track -b $name -- $dest $base", mutates=True)
def worktree_add(name: str, dest: str, base: str) -> None:
    """--no-track, for the reason new-branch has it: a branch off the head
    branch that tracked it would take it as its upstream."""


@git("worktree add -- $dest $name", mutates=True)
def worktree_add_existing(dest: str, name: str) -> None:
    """A branch that already exists needs no -b, and -b would refuse."""


@git("worktree move -- $old $new", mutates=True)
def worktree_move(old: str, new: str) -> None:
    """git updates its own bookkeeping, which a mv would leave behind."""


@git("branch --move -- $old $new", mutates=True)
def branch_move(old: str, new: str) -> None:
    """--move, not --move --force: a rename onto a name that exists is a
    collision to report rather than one to resolve."""


class Landed:
    """Where the caller should end up, and what happened to get there."""

    __slots__ = ("branch", "created", "path")

    path: str
    branch: str
    created: bool  # False when the branch was already here

    def __init__(self, path: str, branch: str, created: bool) -> None:
        self.path = path
        self.branch = branch
        self.created = created


def add(
    name: str,
    base: str = "",
    fetch: bool = True,
    warn: object = None,
    repo: str | os.PathLike[str] | None = None,
) -> Landed:
    """Create a worktree for `name` and say where it is."""
    if not check_ref_format(name, repo=repo):
        raise Refusal(f"{name} is not a valid branch name")

    main = R.main_worktree(repo)
    if not main:
        raise Refusal("not inside a git repository")
    dest = layout.destination(name, main)

    for wt in R.worktrees(repo):
        if wt.branch == name:
            raise Refusal(f"{name} is already checked out at {wt.path}")

    if dest.exists():
        owner = layout.owner_of(str(dest), repo=repo)
        if owner:
            raise Refusal(f"{dest} belongs to {owner}, not to this repository")
        raise Refusal(f"{dest} already exists and this repository does not own it")

    remote = R.remote(repo=repo)
    online = fetch
    if fetch and remote and not R.fetch(remote, repo=repo):
        online = False
        if callable(warn):
            warn(f"{remote} could not be fetched; branching from what is already here")

    existing = bool(R.ref_exists(f"refs/heads/{name}", repo=repo))
    made = _first_absent(dest.parent, main)
    dest.parent.mkdir(parents=True, exist_ok=True)

    try:
        if existing:
            worktree_add_existing(str(dest), name, repo=repo)
            return Landed(str(dest), name, created=False)

        start = base or _head(remote, online, warn, repo=repo)
        worktree_add(name, str(dest), start, repo=repo)
    except (GitError, Refusal, Refused):
        # The directories went in before git was asked, so a refusal leaves
        # them behind. Nothing removes them here: this command says what it
        # made, and deleting a directory the caller cannot see named is the
        # move this tooling refuses everywhere else.
        if made is not None and made.exists() and callable(warn):
            warn(f"git refused, and {made} is left behind, empty")
        raise

    return Landed(str(dest), name, created=True)


def _first_absent(dest_parent: Path, main: str) -> Path | None:
    """The topmost directory `mkdir -p` would have to create, or None.

    Bounded by the worktrees root, the only tree this command may make. None
    when every directory down to `dest_parent` is already there, which is the
    ordinary case and leaves nothing to report.
    """
    root = layout.worktrees_root(main)
    made: Path | None = None
    d = dest_parent
    while d == root or root in d.parents:
        if d.exists():
            break
        made = d
        d = d.parent
    return made


def _head(
    remote: str,
    online: bool,
    warn: object,
    repo: str | os.PathLike[str] | None = None,
) -> str:
    ref, warning = R.head_ref(remote, online=online, repo=repo)
    if warning and callable(warn):
        warn(warning)
    if not ref:
        hint = remote or "origin"
        raise Refusal(
            "cannot tell which branch this repository branches from; record it "
            f"with: git remote set-head {hint} --auto"
        )
    return ref


def move(
    new: str,
    repo: str | os.PathLike[str] | None = None,
) -> Landed:
    """Rename this worktree's branch and move the checkout to match.

    Not a convenience. Renaming the directory you are standing in leaves the
    shell with a stale $PWD and every later command failing, so the caller
    has to be told where to go.
    """
    if not check_ref_format(new, repo=repo):
        raise Refusal(f"{new} is not a valid branch name")

    here = R.toplevel(repo=repo).out.strip()
    if not here:
        raise Refusal("not inside a git repository")
    main = R.main_worktree(repo)
    if Path(here).resolve() == Path(main).resolve():
        raise Refusal(
            "this is the main checkout, not a worktree; "
            "git branch --move renames its branch"
        )

    old = R.current_branch(repo=here).out.strip()
    if not old:
        raise Refusal("HEAD is detached here, so there is no branch to rename")
    if old == new:
        raise Refusal(f"this worktree is already on {new}")
    if R.ref_exists(f"refs/heads/{new}", repo=repo):
        raise Refusal(f"{new} is already a branch")

    dest = layout.destination(new, main)
    if dest.exists():
        raise Refusal(f"{dest} already exists")

    # The branch first: `worktree move` leaves the branch alone, and a failed
    # move after a rename is recoverable where the reverse loses the name.
    branch_move(old, new, repo=repo)
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        worktree_move(here, str(dest), repo=repo)
    except (GitError, Refused):
        branch_move(new, old, repo=repo)
        raise
    _prune_empty_parents(Path(here).parent, main)
    return Landed(str(dest), new, created=False)


def remove(
    chosen: Verdict,
    say: object,
    repo: str | os.PathLike[str] | None = None,
) -> str:
    """Remove one worktree, and say where to go when it was this one.

    Returns the main checkout when the caller was standing in what went, and
    an empty string otherwise. Empty means stay put.

    What goes and what puts it back is printed by the caller, before this is
    reached, so that a run which stops at the confirmation has still said it.
    """
    here = R.toplevel(repo=repo).out.strip()
    main = R.main_worktree(repo)
    standing_in = bool(here) and Path(here).resolve() == Path(chosen.path).resolve()

    if standing_in:
        # git refuses to remove the worktree a process is sitting in only
        # sometimes; leaving first makes it always safe and gives the shim a
        # destination to move to.
        os.chdir(main)

    worktree_remove(chosen.path, repo=main)
    _prune_empty_parents(Path(chosen.path).parent, main)
    if chosen.branch:
        try:
            ref_delete(f"refs/heads/{chosen.branch}", chosen.sha, repo=main)
        except (GitError, Refused) as exc:
            if callable(say):
                say(f"  branch {chosen.branch} kept: {exc}")
    return main if standing_in else ""


def removable(
    only: str,
    head: str,
    head_branch: str,
    delete_ignored: bool,
    repo: str | os.PathLike[str] | None = None,
    ask_forge: bool = True,
) -> list[Verdict]:
    """Every worktree, judging the one you stand in like any other.

    `gws` keeps it, because a listing cannot step out of a directory on your
    behalf. `gwr` is asked for one by name and does step out, so the rule
    that protects a listing would only hide the answer here.
    """
    return assess(
        R.worktrees(repo),
        only,
        head,
        head_branch,
        delete_ignored,
        repo=repo,
        here="",
        ask_forge=ask_forge,
    )


__all__ = ["REMOVE", "Landed", "add", "move", "removable", "remove"]
