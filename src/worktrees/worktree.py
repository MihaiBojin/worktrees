"""Adding and removing a checkout.

Each of these lands the caller somewhere, so each prints a destination on
stdout and nothing else. A binary cannot cd its caller; the shim reads that
line and does the move.
"""

from __future__ import annotations

import os
from pathlib import Path

from . import layout, prune, render
from . import repo as R
from .git import GitError, Refused, git
from .new_branch import Refusal, check_ref_format, refuse_nested
from .prune import _prune_empty_parents
from .verdicts import REMOVE, Verdict, assess


@git("worktree add --no-track -b $name -- $dest $base", mutates=True)
def worktree_add(name: str, dest: str, base: str) -> None:
    """--no-track, for the reason new-branch has it: a branch off the head
    branch that tracked it would take it as its upstream."""


@git("worktree add -- $dest $name", mutates=True)
def worktree_add_existing(dest: str, name: str) -> None:
    """A branch that already exists needs no -b, and -b would refuse."""


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
    warn: object = None,
    repo: str | os.PathLike[str] | None = None,
) -> Landed:
    """Create a worktree for `name` and say where it is."""
    if not check_ref_format(name, repo=repo):
        raise Refusal(f"{render.branch(name)} is not a valid branch name")

    main = R.main_worktree(repo)
    if not main:
        raise Refusal("not inside a git repository")
    refuse_nested(name, repo=repo)
    dest = layout.destination(name, main)

    for wt in R.worktrees(repo):
        if wt.branch == name:
            raise Refusal(f"{render.branch(name)} is already checked out at {wt.path}")

    if dest.exists():
        owner = layout.owner_of(str(dest), repo=repo)
        if owner:
            raise Refusal(f"{dest} belongs to {owner}, not to this repository")
        raise Refusal(f"{dest} already exists and this repository does not own it")

    resolved = R.head(repo)
    if not resolved.ref:
        raise Refusal(resolved.unresolved)
    existing = bool(R.ref_exists(f"refs/heads/{name}", repo=repo))
    made = _first_absent(dest.parent, main)
    dest.parent.mkdir(parents=True, exist_ok=True)

    try:
        if existing:
            worktree_add_existing(str(dest), name, repo=repo)
            return Landed(str(dest), name, created=False)

        start = base or resolved.ref
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


def remove(chosen: Verdict, say, repo=None) -> prune.Outcome:
    """Recheck the approved worktree before removing its checkout and ref."""
    outcome = prune.Outcome(
        path=chosen.path, branch=chosen.branch, retained=list(chosen.stashes)
    )
    main = R.main_worktree(repo)
    try:
        snapshot = chosen.snapshot
        if snapshot is None:
            raise Refused("no assessed deletion snapshot")
        prune.check_head(snapshot.head, repo)
        records = [w for w in R.worktrees(repo) if w.path == snapshot.path]
        if len(records) != 1 or (
            records[0].branch,
            records[0].sha,
            records[0].flags,
        ) != (snapshot.branch, snapshot.sha, snapshot.flags):
            raise Refused(f"{chosen.path}: worktree changed after assessment")
        fresh = assess(
            records,
            "",
            snapshot.head.ref,
            snapshot.head.branch,
            False,
            repo=repo,
            here="",
            ask_forge=True,
            include_main=True,
            permissions=chosen.permissions,
            keep_branch=chosen.keep_branch,
            state=snapshot.head,
        )[0]
        if fresh.snapshot != snapshot or not fresh.eligible:
            raise Refused(f"{chosen.path}: state changed after assessment; {fresh.why}")
        prune.check_head(snapshot.head, repo)
        here = R.toplevel(repo=repo).value
        standing_in = bool(here) and Path(here).resolve() == Path(chosen.path).resolve()
        if standing_in:
            os.chdir(main)
        prune.worktree_remove(chosen.path, repo=main)
        outcome.worktree_removed = True
        outcome.destination = main if standing_in else ""
        _prune_empty_parents(Path(chosen.path).parent, main)
        if chosen.branch and not chosen.keep_branch:
            prune.delete_ref(snapshot, outcome, main)
    except (GitError, Refused) as exc:
        outcome.failed.append(str(exc))
    prune.report(outcome, say)
    return outcome


def removable(
    only: str,
    head: str,
    head_branch: str,
    delete_ignored: bool,
    repo: str | os.PathLike[str] | None = None,
    ask_forge: bool = True,
    permissions: frozenset[str] = frozenset(),
    keep_branch: bool = False,
) -> list[Verdict]:
    """Assess targets for gw remove, including the current worktree.

    The caller requires a second terminal confirmation before removing its
    current checkout and changes to the main checkout before removal.
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
        permissions=permissions,
        keep_branch=keep_branch,
    )


__all__ = ["REMOVE", "Landed", "add", "removable", "remove"]
