"""Completion evidence and deletion protections for every worktree."""

from __future__ import annotations

import os
import shlex
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .prune import HeadState, Snapshot

from . import merged, progress
from . import repo as R
from .git import git

REMOVE = "remove"
EMPTY = "empty"
KEEP = "keep"
UNKNOWN = "unknown"

# What `--delete all` grants a worktree.
PERMISSIONS = frozenset(("empty", "ignored-files", "stashes"))


def restore_command(branch: str, sha: str) -> str:
    """`git branch NAME SHA`, the line that puts a deleted branch back.

    The name is quoted, because this line exists to be pasted and git permits
    `$`, a backtick and `;` in a branch name. `shlex.quote` writes POSIX
    single quotes, which bash, zsh and fish all read the same way.
    """
    return f"git branch {shlex.quote(branch)} {sha}"


class Verdict:
    def __init__(
        self,
        verdict: str,
        branch: str,
        path: str,
        why: str,
        ignored_paths: tuple[str, ...] = (),
        sha: str = "",
        *,
        proof: str = "",
        request: dict | None = None,
        protections: list[str] | None = None,
        stashes: list[str] | None = None,
    ) -> None:
        self.snapshot: Snapshot | None = None
        self.permissions: frozenset[str] = frozenset()
        self.keep_branch = False
        self.verdict = verdict
        self.branch = branch
        self.path = path
        # What the completion proof found, before any protection.
        self.evidence = why
        # Grouped below the ignore file that names each one, as people read them.
        self.ignored_paths = ignored_paths
        self.sha = sha
        self.proof = proof
        self.request = request
        self.protections = protections or []
        self.stashes = stashes or []
        self.eligible = verdict == REMOVE and not self.protections
        # The --delete values that alone hold an ineligible row back; empty
        # when no value would make it eligible.
        self.needs: tuple[str, ...] = ()
        # Whether --keep-branch would make an ineligible row eligible.
        self.keep_branch_frees = False

    @property
    def label(self) -> str:
        return self.branch or "(detached)"

    @property
    def why(self) -> str:
        """The evidence, then every protection that stands."""
        return "; ".join([self.evidence, *self.protections])

    @property
    def held_back(self) -> list[str]:
        """Every reason this row is not eligible, and none when it is.

        The evidence is a reason only when it proves nothing finished, and
        not under --keep-branch, which waives proof for a checkout on a branch.
        """
        if self.eligible:
            return []
        waived = self.keep_branch and bool(self.branch)
        unproved = self.verdict in (KEEP, UNKNOWN) and not waived
        return ([self.evidence] if unproved else []) + self.protections

    @property
    def ignored(self) -> int:
        return len(self.ignored_paths)

    @property
    def undo(self) -> str | None:
        """None on a detached worktree, which has no branch to put back."""
        return restore_command(self.branch, self.sha) if self.branch else None


def stale(records: list[R.Worktree]) -> list[R.Worktree]:
    """Records for worktree directories somebody deleted by hand."""
    return [w for w in records if "prunable" in w.flags]


@git("submodule status")
def submodule_status() -> None:
    """Initialized submodules have a status prefix other than '-'."""


@git("rev-parse --path-format=absolute --git-path modules")
def modules_dir() -> None:
    """Where this worktree keeps its submodules' git directories."""


def submodule_protection(path: str) -> str:
    """Why `git worktree remove` would refuse this worktree, if it would.

    git refuses while a submodule is initialized, and also while the
    worktree's own modules directory exists, which `deinit` leaves behind.
    """
    initialized = any(
        line and not line.startswith("-") for line in submodule_status(repo=path).lines
    )
    store = modules_dir(repo=path).value
    if not initialized and not Path(store).exists():
        return ""
    return (
        "it has an initialized submodule or a submodule store; run "
        f"git submodule deinit --all in it, then delete {store} once nothing "
        "in it is needed, since it holds the submodules' own commits, and retry"
    )


def assess(
    records: list[R.Worktree],
    only: str,
    head: str,
    head_branch: str,
    delete_ignored: bool,
    repo: str | os.PathLike[str] | None = None,
    here: str | None = None,
    ask_forge: bool = False,
    include_main: bool = False,
    permissions: frozenset[str] = frozenset(),
    keep_branch: bool = False,
    state: HeadState | None = None,
) -> list[Verdict]:
    """Assess pinned tips and keep every deletion protection separate.

    `here` blocks the caller's worktree for pruning. A targeted removal
    passes an empty string and handles departure from that directory itself.
    """
    from . import prune

    main = R.main_worktree(repo)
    if here is None:
        here = R.toplevel(repo=repo).value
    state = state or prune.head_state(head, head_branch, repo)
    head_sha = state.sha
    first_parents = set(state.first_parents)
    remote = R.remote(repo)
    entries = prune.stash_list(repo=repo).lines

    def judge(wt: R.Worktree) -> Verdict | None:
        if "bare" in wt.flags or "prunable" in wt.flags:
            return None
        if only and wt.branch != only:
            return None
        if wt.path == main and not include_main:
            return None
        progress.say(f"{wt.label}: checking completion evidence")
        verdict, why, proof, request = merged.branch_proof(
            wt.branch,
            wt.sha,
            head_sha,
            head_branch,
            remote,
            ask_forge,
            first_parents,
            repo,
        )
        # The protections no --delete value lifts.
        protections = []
        if wt.path == main:
            protections.append("it is the main checkout, and never removable")
        if here and Path(wt.path).resolve() == Path(here).resolve() and wt.path != main:
            protections.append("you are standing in it")
        if "locked" in wt.flags:
            protections.append("it is locked")
        ignored_paths: tuple[str, ...] = ()
        ignored_groups: tuple[str, ...] = ()
        held = 0
        if wt.path != main and not Path(wt.path).is_dir():
            # A locked worktree keeps its record while its directory is away,
            # on a removable disk say. git refuses to remove it until it is
            # unlocked, so there is nothing here to read and nothing to delete.
            protections.append("its directory is missing")
        elif wt.path != main:
            progress.say(f"{wt.label}: reading its status")
            status = R.status_of(wt.path)
            if status.dirty:
                protections.append("it has uncommitted changes or untracked files")
            submodule = submodule_protection(wt.path)
            if submodule:
                protections.append(submodule)
            ignored_paths = tuple(status.ignored)
            ignored_groups = tuple(R.ignored_groups(wt.path, status.ignored))
            held = len(ignored_groups)
        stashes = prune.stashes_on(wt.branch, entries, wt.sha, repo)

        def standing(granted: frozenset[str], keep: bool = keep_branch) -> list[str]:
            """The protections left once these --delete values are granted."""
            left = list(protections)
            if held and "ignored-files" not in granted:
                left.append(
                    f"it holds {held} ignored path(s); pass --delete ignored-files"
                )
            return left + prune.branch_protections(
                wt.branch, head_branch, verdict, stashes, granted, keep
            )

        def eligible(granted: frozenset[str], keep: bool = keep_branch) -> bool:
            # --keep-branch waives completion proof because the branch keeps
            # the commits. A detached worktree has no branch to keep them.
            return not standing(granted, keep) and (
                (keep and bool(wt.branch))
                or verdict == REMOVE
                or (verdict == EMPTY and "empty" in granted)
            )

        granted = permissions | {"ignored-files"} if delete_ignored else permissions
        row = Verdict(
            verdict,
            wt.branch,
            wt.path,
            why,
            ignored_groups,
            wt.sha,
            proof=proof,
            request=request,
            protections=standing(granted),
            stashes=stashes,
        )
        row.permissions = permissions
        row.keep_branch = keep_branch
        row.eligible = eligible(granted)
        row.keep_branch_frees = not row.eligible and eligible(granted, keep=True)
        # A value is needed when the row stays held back without it, every
        # other value granted.
        if not row.eligible and eligible(PERMISSIONS):
            row.needs = tuple(
                value
                for value in sorted(PERMISSIONS)
                if not eligible(PERMISSIONS - {value})
            )
        row.snapshot = prune.Snapshot(
            state,
            wt.branch,
            wt.sha,
            wt.path,
            wt.flags,
            ignored_paths,
            prune.stash_subjects(stashes, entries),
            ignored_groups,
        )
        return row

    with ThreadPoolExecutor(max_workers=8) as pool:
        return [v for v in pool.map(judge, records) if v is not None]
