"""One verdict per worktree. Nothing here mutates."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from . import repo as R
from .merged import merged_reason

# The verdicts. `unknown` is not `keep` with a softer word: "no upstream, so
# nothing says whether this was pushed" is a different fact from "this is not
# merged", and a sweep printing them the same way invites somebody to act on
# the wrong one.
GO = "go"
KEEP = "keep"
UNKNOWN = "unknown"


@dataclass(frozen=True)
class Verdict:
    verdict: str
    branch: str  # empty when detached
    path: str
    why: str

    @property
    def label(self) -> str:
        return self.branch or "(detached)"


def stale(records: list[R.Worktree]) -> list[R.Worktree]:
    """Records answering for a directory somebody deleted by hand.

    `git worktree prune` is what clears them, so they are nobody's verdict:
    neither command may propose removing a checkout that is not there.
    """
    return [w for w in records if "prunable" in w.flags]


def assess(
    records: list[R.Worktree],
    only: str,
    head: str,
    head_branch: str,
    delete_ignored: bool,
    repo: str | os.PathLike[str] | None = None,
    here: str | None = None,
) -> list[Verdict]:
    """Every worktree, in the order `git worktree remove` would refuse them.

    Proposing something that would then be refused is a bug here, not a
    surprise at the confirmation.

    `here` is the checkout the caller is standing in, which is kept rather
    than proposed. Pass "" to judge it like any other: `gwr` is asked for one
    by name and steps out of it first, where a listing has no way to.
    """
    main = R.main_worktree(repo)
    if here is None:
        here = R.toplevel(repo=repo).out.strip()
    head_name = R.ref_name(head)

    out: list[Verdict] = []
    for wt in records:
        if "bare" in wt.flags or "prunable" in wt.flags or wt.path == main:
            continue
        if only and wt.branch != only:
            continue

        def say(verdict: str, why: str, wt: R.Worktree = wt) -> None:
            out.append(Verdict(verdict, wt.branch, wt.path, why))

        if here and Path(wt.path).resolve() == Path(here).resolve():
            say(KEEP, "you are standing in it")
            continue
        if "locked" in wt.flags:
            say(KEEP, "it is locked")
            continue
        if wt.branch and wt.branch == head_branch:
            say(KEEP, "it is the head branch")
            continue
        if R.is_dirty(wt.path):
            say(KEEP, "it has uncommitted changes")
            continue

        if not wt.branch:
            # Detached: finished when some ref already reaches the commit,
            # which is the question `git worktree remove` asks of one.
            if R.refs_containing(wt.sha, repo=repo).out.strip():
                say(GO, "its commit is reached by a ref")
            else:
                say(UNKNOWN, f"no ref reaches {wt.sha}")
            continue

        reason = merged_reason(wt.branch, head, repo=repo)
        if not reason:
            if R.unpushed_count(wt.branch, repo=repo) is None:
                say(
                    UNKNOWN,
                    f"not merged into {head_name}, and no upstream says whether "
                    "its commits were pushed",
                )
            else:
                say(KEEP, f"not merged into {head_name}")
            continue

        ignored = R.ignored_paths(wt.path)
        if ignored and not delete_ignored:
            say(
                KEEP,
                f"{reason}, but holds {len(ignored)} ignored path(s); "
                "pass --delete-ignored",
            )
            continue

        say(GO, reason)
    return out
