"""One verdict per worktree. Nothing here mutates."""

from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from . import forge, progress
from . import repo as R
from .merged import merged_reason

# The verdicts, each the instruction it gives. `unknown` is not `keep` with a
# softer word: "no upstream, so nothing says whether this was pushed" is a
# different fact from "this is not merged", and a sweep printing them the same
# way invites somebody to act on the wrong one.
REMOVE = "remove"
KEEP = "keep"
UNKNOWN = "unknown"


class Verdict:
    __slots__ = ("branch", "ignored", "path", "sha", "verdict", "why")

    verdict: str
    branch: str  # empty when detached
    path: str
    why: str
    # How many ignored paths held back a branch that is otherwise done, and 0
    # everywhere else. Whether the work landed and whether the directory is
    # safe to delete are two questions, and only the second one answers to
    # --delete-ignored, so a caller that prints a refusal needs them apart.
    ignored: int
    # The commit the verdict was formed against. Deleting the branch names it
    # as the value the ref must still hold, so a commit made between the
    # judgement and the removal fails the delete rather than going with it.
    sha: str

    def __init__(
        self,
        verdict: str,
        branch: str,
        path: str,
        why: str,
        ignored: int = 0,
        sha: str = "",
    ) -> None:
        self.verdict = verdict
        self.branch = branch
        self.path = path
        self.why = why
        self.ignored = ignored
        self.sha = sha

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
    ask_forge: bool = False,
    include_main: bool = False,
) -> list[Verdict]:
    """Every worktree, in the order `git worktree remove` would refuse them.

    Proposing something that would then be refused is a bug here, not a
    surprise at the confirmation.

    `here` is the checkout the caller is standing in, which is kept rather
    than proposed. Pass "" to judge it like any other: `gwr` is asked for one
    by name and steps out of it first, where a listing has no way to.

    `include_main` puts the main checkout in the list, kept and never
    proposed. A listing leaving it out shows one row where `git worktree
    list` shows two, and reads as though something went missing. `gwr` takes
    the list as its candidates, so it asks for the list without it.
    """
    main = R.main_worktree(repo)
    if here is None:
        here = R.toplevel(repo=repo).out.strip()
    head_name = R.ref_name(head)

    def judge(wt: R.Worktree) -> Verdict | None:
        def say(verdict: str, why: str, ignored: int = 0) -> Verdict:
            return Verdict(verdict, wt.branch, wt.path, why, ignored, wt.sha)

        if "bare" in wt.flags or "prunable" in wt.flags:
            return None
        if only and wt.branch != only:
            return None
        if wt.path == main:
            # Said before the standing-in rule below, because which one is
            # the main checkout is the more useful of the two facts and the
            # only one that is true from anywhere.
            if include_main:
                return say(KEEP, "it is the main checkout, and never removable")
            return None

        if here and Path(wt.path).resolve() == Path(here).resolve():
            return say(KEEP, "you are standing in it")
        if "locked" in wt.flags:
            return say(KEEP, "it is locked")
        if wt.branch and wt.branch == head_branch:
            return say(KEEP, "it is the head branch")
        # One read, both answers. Asking again further down was half of
        # every assessment's git calls.
        progress.say(f"{wt.label}: reading its status")
        status = R.status_of(wt.path)
        if status.dirty:
            return say(KEEP, "it has uncommitted changes")

        if not wt.branch:
            # Detached: finished when some ref already reaches the commit,
            # which is the question `git worktree remove` asks of one.
            if R.refs_containing(wt.sha, repo=repo).out.strip():
                return say(REMOVE, "its commit is reached by a ref")
            return say(UNKNOWN, f"no ref reaches {wt.sha}")

        progress.say(f"{wt.label}: is it merged into {head_name}?")
        reason = merged_reason(wt.branch, head, repo=repo)
        if not reason and ask_forge:
            progress.say(f"{wt.label}: asking the forge for its pull request")
            reason, verdict = _forge_reason(wt.branch, wt.sha, repo=repo)
            if verdict:
                return say(verdict, reason)
        if not reason:
            progress.say(f"{wt.label}: were its commits pushed?")
            if R.unpushed_count(wt.branch, repo=repo) is None:
                return say(
                    UNKNOWN,
                    f"not merged into {head_name}, and no upstream says whether "
                    "its commits were pushed",
                )
            return say(KEEP, f"not merged into {head_name}")

        ignored = status.ignored
        if ignored and not delete_ignored:
            return say(
                KEEP,
                f"{reason}, but holds {len(ignored)} ignored path(s); "
                "pass --delete-ignored",
                len(ignored),
            )

        return say(REMOVE, reason)

    # Each worktree is judged on its own, and most of the time goes waiting
    # on git and on the forge, one round trip per branch git cannot settle.
    # Threads overlap the waits; map keeps git's order.
    with ThreadPoolExecutor(max_workers=8) as pool:
        return [v for v in pool.map(judge, records) if v is not None]


def _forge_reason(
    branch: str, sha: str = "", repo: str | os.PathLike[str] | None = None
) -> tuple[str, str]:
    """What the forge says, cross-checked against what it cannot see.

    Returns (reason, verdict), or ("", "") when the forge said nothing and
    the content probes keep the answer.

    A merged request with a missing configured upstream permits pruning.
    An existing upstream is checked for unpushed commits. A branch with no
    tracking configuration stays unknown.
    """
    request = forge.request_for(branch, repo=repo, sha=sha)
    if request is None or request.state not in ("MERGED", "CLOSED"):
        return "", ""

    lower = request.state.lower()
    unpushed = R.unpushed_count(branch, repo=repo)
    if unpushed is None:
        if R.upstream_tracking(branch, repo=repo).out.strip() == "[gone]":
            if request.state == "MERGED":
                return (
                    f"its {request.noun} #{request.number} is merged "
                    "and its upstream is gone",
                    "",
                )
            return (
                f"its {request.noun} is {lower}, but its upstream is gone; "
                "cannot check for unpushed commits",
                UNKNOWN,
            )
        return (
            f"its {request.noun} is {lower}, but the branch has no upstream "
            "to have been pushed to",
            UNKNOWN,
        )
    if unpushed:
        return (
            f"its {request.noun} is {lower}, but {unpushed} commit(s) here "
            "are not in it",
            KEEP,
        )
    return f"its {request.noun} #{request.number} is {lower}", ""
