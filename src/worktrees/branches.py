"""Assess local branches and delete only the refs proved finished."""

from __future__ import annotations

import shlex
from dataclasses import asdict, dataclass, field

from . import branch_forge, merged, prune
from . import repo as R
from .git import GitError, Refused, git


@git("for-each-ref --format='%(refname)%00%(objectname)%00%(symref)' refs/heads/")
def branch_refs() -> None:
    """Local branch names, full object IDs, and symbolic targets."""


@dataclass
class Verdict:
    branch: str
    sha: str
    verdict: str
    why: str
    proof: str = ""
    paths: list[str] = field(default_factory=list)
    request: dict | None = None
    error: bool = False

    @property
    def undo(self) -> str:
        return f"git branch {shlex.quote(self.branch)} {self.sha}"

    def data(self) -> dict:
        return {**asdict(self), "undo": self.undo}


def local_refs() -> dict[str, tuple[str, str]]:
    return {
        ref.removeprefix("refs/heads/"): (sha, symref)
        for ref, sha, symref in (line.split("\0") for line in branch_refs().lines)
    }


def assess(
    names: list[str], head_sha: str, head_branch: str, remote: str, ask_forge: bool
) -> list[Verdict]:
    refs = local_refs()
    missing = [name for name in names if name not in refs]
    if missing:
        raise ValueError("no local branch: " + ", ".join(missing))
    checked: dict[str, list[str]] = {}
    for wt in R.worktrees():
        if wt.branch:
            checked.setdefault(wt.branch, []).append(wt.path)
    rows = []
    for name in dict.fromkeys(names or refs):
        sha, symbolic = refs[name]
        row = Verdict(
            name, sha, "unknown", "no proof that its change is in the head branch"
        )
        row.paths = checked.get(name, [])
        rows.append(row)
        if name == head_branch:
            row.verdict, row.why = "keep", "it is the head branch"
        elif row.paths:
            row.verdict, row.why = "keep", "checked out in a worktree"
        elif symbolic:
            row.verdict, row.why = "keep", "it is a symbolic branch ref"
        else:
            try:
                _judge(row, head_sha, head_branch, remote, ask_forge)
            except GitError as exc:
                row.error, row.why = True, str(exc)
    return rows


def _judge(
    row: Verdict, head: str, head_branch: str, remote: str, ask_forge: bool
) -> None:
    for proof, why, check in (
        ("ancestry", "its tip is an ancestor of the head branch", merged.is_ancestor),
        (
            "squash",
            "its tree or combined patch is already in the head branch",
            merged.squash_matches,
        ),
        (
            "content",
            "every changed path matches the head branch",
            merged.content_matches,
        ),
    ):
        if check(row.sha, head):
            row.verdict, row.why, row.proof = "remove", why, proof
            return
    if ask_forge and remote:
        request = branch_forge.request_for(row.branch, remote)
        if request is not None:
            row.request = request.data()
            if request.state == "MERGED":
                why = request.unverified_reason(row.branch, row.sha, head_branch)
                if why:
                    row.why = why
                    return
                if not prune.object_type(request.merge_sha):
                    row.why = "the request's merge commit is not available locally"
                    return
                if merged.is_ancestor(request.merge_sha, head):
                    row.verdict, row.proof = "remove", "forge"
                    row.why = (
                        f"{request.noun} #{request.number} merged this tip "
                        "into the head branch"
                    )
                    return
                row.why = "the request's merge commit is not in the head branch"
                return
            row.why = (
                f"{request.noun} #{request.number} is "
                f"{request.state.lower()}, not merged"
            )
    if not merged.merge_bases(row.sha, head).lines:
        row.why = "no common history with the head branch"
        return
    row.verdict = "keep"


def delete(
    row: Verdict,
    head_ref: str,
    head_sha: str,
    head_branch: str,
    remote: str,
    ask_forge: bool,
) -> None:
    """Recheck the verdict and delete only the commit the user approved."""
    if prune.sha_of(head_ref).out.strip() != head_sha:
        raise Refused("the head branch changed after assessment; run the command again")
    fresh = assess([row.branch], head_sha, head_branch, remote, ask_forge)[0]
    if fresh.sha != row.sha:
        raise Refused(f"{row.branch} changed after assessment; run the command again")
    if fresh.verdict != "remove":
        raise Refused(f"{row.branch}: {fresh.why}")
    if prune.sha_of(head_ref).out.strip() != head_sha:
        raise Refused(
            "the head branch changed during assessment; run the command again"
        )
    # update-ref does not enforce git branch's checked-out protection.
    if any(wt.branch == row.branch for wt in R.worktrees()):
        raise Refused(f"{row.branch} is checked out in a worktree")
    prune.ref_delete(f"refs/heads/{row.branch}", row.sha)
