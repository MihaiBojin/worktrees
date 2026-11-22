"""Adding and removing a checkout.

Each of these lands the caller somewhere, so each prints a destination on
stdout and nothing else. A binary cannot cd its caller; the shim reads that
line and does the move.
"""

from __future__ import annotations

import os
import shlex
from collections.abc import Callable
from pathlib import Path

from . import layout, prune, render
from . import repo as R
from .git import GitError, Refused, git
from .new_branch import Refusal, check_ref_format, refuse_nested
from .prune import _prune_empty_parents
from .verdicts import REMOVE, Verdict, assess


@git("worktree add --no-track -b $name -- $dest $base", mutates=True)
def worktree_add(name: str, dest: str, base: str) -> None:
    """--no-track, for the reason `gw branch create` has it: a branch off the head
    branch that tracked it would take it as its upstream."""


@git("worktree add -- $dest $name", mutates=True)
def worktree_add_existing(dest: str, name: str) -> None:
    """A branch that already exists needs no -b, and -b would refuse."""


@git("worktree add --track -b $name -- $dest $copy", mutates=True)
def worktree_add_tracking(name: str, dest: str, copy: str) -> None:
    """A branch only the remote has starts from its copy and pushes back to it,
    so it tracks the copy: the upstream is the same branch, not the head."""


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
    approve: Callable[[str], bool] | None = None,
) -> Landed:
    """Create a worktree for `name` and say where it is.

    A branch only the default remote has is started from its copy, after
    `approve` says yes to the copy's name; without `approve` that is a
    refusal. A new branch of the same name from the head ref would hide the
    remote's work behind a branch that pushes over it.
    """
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
    copy = "" if existing else _remote_copy(name, repo)
    if existing and base:
        _same_commit_or_refuse(name, f"refs/heads/{name}", base, repo)
    elif copy and base:
        _same_commit_or_refuse(name, copy, base, repo)
    elif copy:
        short = R.ref_name(copy)
        if approve is None:
            raise Refusal(
                f"{render.branch(name)} exists only on the remote, as "
                f"{render.ref(short)}; "
                f"gw add {shlex.quote(name)} --yes starts it from there"
            )
        if not approve(short):
            raise Refusal(
                f"{render.branch(name)} not created: a new one from the head ref "
                f"would hide {render.ref(short)}"
            )
    made = _first_absent(dest.parent, main)
    dest.parent.mkdir(parents=True, exist_ok=True)

    try:
        if existing:
            worktree_add_existing(str(dest), name, repo=repo)
            return Landed(str(dest), name, created=False)
        if copy:
            worktree_add_tracking(name, str(dest), copy, repo=repo)
            return Landed(str(dest), name, created=True)

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


def _remote_copy(name: str, repo: str | os.PathLike[str] | None) -> str:
    """The default remote's `refs/remotes/REMOTE/NAME`, or nothing."""
    remote = R.remote(repo)
    if not remote:
        return ""
    copy = f"refs/remotes/{remote}/{name}"
    return copy if R.ref_exists(copy, repo=repo) else ""


def _same_commit_or_refuse(
    name: str, ref: str, base: str, repo: str | os.PathLike[str] | None
) -> None:
    """BASE starts a new branch. For a branch that already has a commit, here
    or on the remote, a BASE naming that commit says the same thing, and any
    other would move or hide the branch, which this command does not do."""
    at = R.local_sha(ref, repo=repo).value
    wanted = R.local_sha(base, repo=repo).value
    if not wanted:
        raise Refusal(f"{base} names no commit")
    if wanted != at:
        where = "a branch" if ref.startswith("refs/heads/") else "on the remote"
        short = R.ref_name(ref)
        raise Refusal(
            f"{render.branch(name)} is already {where} at {at[:12]}, not at "
            f"{render.ref(base)}; BASE starts a new branch only. "
            f"gw add {shlex.quote(name)} checks out {render.ref(short)} as it is"
        )


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


def recheck(go: list[Verdict], repo=None) -> dict[str, Verdict]:
    """Assess every approved worktree again, together, by path.

    One assessment for the batch reads each checkout on its own thread, where
    a recheck before each removal reads them one after another: a repository
    whose `git status` takes half a minute pays that once, not once a
    target. `remove` still checks the head and the worktree's record just
    before it deletes, and `git worktree remove` refuses a checkout dirtied
    since. Empty for a single target, for targets approved under different
    terms, or when the assessment fails; `remove` then rechecks its own.
    """
    snapshots = [v.snapshot for v in go]
    if len(go) < 2 or any(s is None for s in snapshots):
        return {}
    first = go[0]
    head = snapshots[0].head  # type: ignore[union-attr]
    if any(
        (v.permissions, v.keep_branch, s.head)  # type: ignore[union-attr]
        != (first.permissions, first.keep_branch, head)
        for v, s in zip(go, snapshots, strict=True)
    ):
        return {}
    wanted = {v.path for v in go}
    try:
        prune.check_head(head, repo)
        fresh = assess(
            [w for w in R.worktrees(repo) if w.path in wanted],
            "",
            head.ref,
            head.branch,
            repo=repo,
            here="",
            ask_forge=True,
            include_main=True,
            permissions=first.permissions,
            keep_branch=first.keep_branch,
            state=head,
        )
    except (GitError, Refused):
        return {}
    return {v.path: v for v in fresh}


def remove(
    chosen: Verdict, say, repo=None, rechecked: Verdict | None = None
) -> prune.Outcome:
    """Recheck the approved worktree before removing its checkout and ref.

    `rechecked` is this worktree's row from `recheck`, read with the rest of
    its batch; without one the worktree is assessed here.
    """
    outcome = prune.Outcome(
        path=chosen.path, branch=chosen.branch, retained=list(chosen.stashes)
    )
    main = R.main_worktree(repo)
    stage = "recheck"
    try:
        snapshot = chosen.snapshot
        if snapshot is None:
            raise Refused("no assessed deletion snapshot")
        prune.check_head(snapshot.head, repo)

        def unchanged() -> list[R.Worktree]:
            records = [w for w in R.worktrees(repo) if w.path == snapshot.path]
            if len(records) != 1 or (
                records[0].branch,
                records[0].sha,
                records[0].flags,
            ) != (snapshot.branch, snapshot.sha, snapshot.flags):
                raise Refused(f"{chosen.path}: worktree changed after assessment")
            return records

        fresh = (
            rechecked
            or assess(
                unchanged(),
                "",
                snapshot.head.ref,
                snapshot.head.branch,
                repo=repo,
                here="",
                ask_forge=True,
                include_main=True,
                permissions=chosen.permissions,
                keep_branch=chosen.keep_branch,
                state=snapshot.head,
            )[0]
        )
        if fresh.snapshot != snapshot or not fresh.eligible:
            raise Refused(f"{chosen.path}: state changed after assessment; {fresh.why}")
        prune.check_head(snapshot.head, repo)
        # Again after the assessment, which can take seconds: a commit made
        # meanwhile on a detached HEAD is reachable from nothing else, and
        # `git worktree remove` does not look at HEAD.
        unchanged()
        here = R.toplevel(repo=repo).value
        standing_in = bool(here) and Path(here).resolve() == Path(chosen.path).resolve()
        if standing_in:
            os.chdir(main)
        stage = "worktree"
        prune.worktree_remove(chosen.path, repo=main)
        outcome.worktree_removed = True
        outcome.destination = main if standing_in else ""
        _prune_empty_parents(Path(chosen.path).parent, main)
        if chosen.branch and not chosen.keep_branch:
            stage = "branch"
            prune.delete_ref(snapshot, outcome, main)
    except (GitError, Refused) as exc:
        outcome.failed.append(str(exc))
    prune.audit_outcome(chosen, outcome, main, stage=stage)
    prune.report(outcome, say)
    return outcome


def removable(
    only: str,
    head: str,
    head_branch: str,
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
        repo=repo,
        here="",
        ask_forge=ask_forge,
        permissions=permissions,
        keep_branch=keep_branch,
    )


__all__ = ["REMOVE", "Landed", "add", "removable", "remove"]
