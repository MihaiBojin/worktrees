"""Completion evidence and deletion protections for every worktree."""

from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from . import merged, progress
from . import repo as R
from .git import git

REMOVE = "remove"
EMPTY = "empty"
KEEP = "keep"
UNKNOWN = "unknown"


class Verdict:
    def __init__(
        self,
        verdict: str,
        branch: str,
        path: str,
        why: str,
        ignored: int = 0,
        sha: str = "",
        *,
        proof: str = "",
        request: dict | None = None,
        protections: list[str] | None = None,
        stashes: list[str] | None = None,
    ) -> None:
        self.verdict = verdict
        self.branch = branch
        self.path = path
        self.why = why
        self.ignored = ignored
        self.sha = sha
        self.proof = proof
        self.request = request
        self.protections = protections or []
        self.stashes = stashes or []
        self.eligible = verdict == REMOVE and not self.protections

    @property
    def label(self) -> str:
        return self.branch or "(detached)"


def stale(records: list[R.Worktree]) -> list[R.Worktree]:
    """Records for worktree directories somebody deleted by hand."""
    return [w for w in records if "prunable" in w.flags]


@git("submodule status")
def submodule_status() -> None:
    """Initialized submodules have a status prefix other than '-'."""


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
) -> list[Verdict]:
    """Assess pinned tips and keep every deletion protection separate.

    `here` blocks the caller's worktree for pruning. A targeted removal
    passes an empty string and handles departure from that directory itself.
    """
    from . import prune

    main = R.main_worktree(repo)
    if here is None:
        here = R.toplevel(repo=repo).out.strip()
    head_sha = prune.sha_of(head, repo=repo).out.strip() if head else ""
    first_parents = (
        set(merged.first_parent_history(head_sha, repo=repo).lines)
        if head_sha
        else set()
    )
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
        protections = []
        if wt.path == main:
            protections.append("it is the main checkout, and never removable")
        if here and Path(wt.path).resolve() == Path(here).resolve() and wt.path != main:
            protections.append("you are standing in it")
        if "locked" in wt.flags:
            protections.append("it is locked")
        ignored = 0
        if wt.path != main:
            progress.say(f"{wt.label}: reading its status")
            status = R.status_of(wt.path)
            if status.dirty:
                protections.append("it has uncommitted changes or untracked files")
            if any(
                line and not line.startswith("-")
                for line in submodule_status(repo=wt.path).lines
            ):
                protections.append(
                    "it has an initialized submodule; run "
                    "git submodule deinit --all in it and retry"
                )
            ignored = len(status.ignored)
            if ignored and not delete_ignored:
                protections.append(
                    f"it holds {ignored} ignored path(s); pass --delete-ignored"
                )
        stashes = prune.stashes_on(wt.branch, entries, wt.sha, repo)
        protections += prune.branch_protections(
            wt.branch, head_branch, verdict, stashes
        )
        if protections:
            why += "; " + "; ".join(protections)
        return Verdict(
            verdict,
            wt.branch,
            wt.path,
            why,
            ignored,
            wt.sha,
            proof=proof,
            request=request,
            protections=protections,
            stashes=stashes,
        )

    with ThreadPoolExecutor(max_workers=8) as pool:
        return [v for v in pool.map(judge, records) if v is not None]
