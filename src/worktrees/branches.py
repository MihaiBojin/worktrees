"""Assess local branches and delete only the refs proved finished."""

from __future__ import annotations

import shlex
from dataclasses import asdict, dataclass, field

from . import progress, prune, verdicts
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
    wanted = list(dict.fromkeys(names or refs))
    for i, name in enumerate(wanted, 1):
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
            # One at a time, and each can cost a round trip to the forge, so
            # the count says how far through a long list this is.
            of = f" ({i} of {len(wanted)})" if len(wanted) > 1 else ""

            def say(question: str, name: str = name, of: str = of) -> None:
                progress.say(f"{name}: {question}{of}")

            try:
                found = verdicts.prove(
                    sha, name, head_sha, head_branch, remote, ask_forge, say
                )
                row.verdict, row.why = found.verdict, found.why
                row.proof, row.request = found.proof, found.request
            except GitError as exc:
                row.error, row.why = True, exc.line()
    return rows


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
