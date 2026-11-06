"""One verdict per worktree. Nothing here mutates."""

from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

from . import branch_forge, merged, progress
from . import repo as R
from .git import GitError

# The verdicts, each the instruction it gives. `unknown` is not `keep` with a
# softer word: "no common history with the head branch" is a different fact
# from "this has commits the head branch lacks", and a sweep printing them the
# same way invites somebody to act on the wrong one.
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


@dataclass
class Proof:
    verdict: str
    why: str
    proof: str = ""  # ancestry, squash, content or forge, on a remove
    request: dict | None = None  # what the forge said, when it was asked


def prove(
    sha: str,
    branch: str,
    head: str,
    head_branch: str,
    remote: str,
    ask_forge: bool,
    say: Callable[[str], None],
) -> Proof:
    """Whether the change at `sha` is in the head branch.

    The one proof a worktree and a loose branch are both judged by. The local
    checks run first; the forge is asked only when they fail, and only its
    evidence for exactly this tip, merged into the head branch, counts.
    """
    for proof, why, question, check in (
        (
            "ancestry",
            "its tip is an ancestor of the head branch",
            f"is it merged into {head_branch}?",
            merged.is_ancestor,
        ),
        (
            "squash",
            "its tree or combined patch is already in the head branch",
            f"was it squash-merged into {head_branch}?",
            merged.squash_matches,
        ),
        (
            "content",
            "every changed path matches the head branch",
            f"does {head_branch} hold every path it changed?",
            merged.content_matches,
        ),
    ):
        say(question)
        if check(sha, head):
            return Proof(REMOVE, why, proof)
    found = Proof(UNKNOWN, "no proof that its change is in the head branch")
    if ask_forge and remote:
        say("asking the forge for its pull request")
        request = branch_forge.request_for(branch, remote)
        if request is not None:
            found.request = request.data()
            if request.state == "MERGED":
                why = request.unverified_reason(branch, sha, head_branch)
                if why:
                    found.why = why
                    return found
                if not R.object_type(request.merge_sha):
                    found.why = "the request's merge commit is not available locally"
                    return found
                if merged.is_ancestor(request.merge_sha, head):
                    found.verdict, found.proof = REMOVE, "forge"
                    found.why = (
                        f"{request.noun} #{request.number} merged this tip "
                        "into the head branch"
                    )
                    return found
                found.why = "the request's merge commit is not in the head branch"
                return found
            found.why = (
                f"{request.noun} #{request.number} is "
                f"{request.state.lower()}, not merged"
            )
    if not merged.merge_bases(sha, head).lines:
        found.why = "no common history with the head branch"
        return found
    found.verdict = KEEP
    return found


def assess(
    records: list[R.Worktree],
    only: str,
    head: str,
    head_branch: str,
    delete_ignored: bool,
    remote: str,
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
    main = R.main_worktree()
    if here is None:
        here = R.toplevel().out.strip()

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
            if R.refs_containing(wt.sha).out.strip():
                return say(REMOVE, "its commit is reached by a ref")
            return say(UNKNOWN, f"no ref reaches {wt.sha}")

        try:
            found = prove(
                wt.sha,
                wt.branch,
                head,
                head_branch,
                remote,
                ask_forge,
                lambda question: progress.say(f"{wt.label}: {question}"),
            )
        except GitError as exc:
            return say(UNKNOWN, str(exc))
        ignored = status.ignored
        if found.verdict == REMOVE and ignored and not delete_ignored:
            return say(
                KEEP,
                f"{found.why}, but holds {len(ignored)} ignored path(s); "
                "pass --delete-ignored",
                len(ignored),
            )
        return say(found.verdict, found.why)

    # Each worktree is judged on its own, and most of the time goes waiting
    # on git and on the forge, one round trip per branch git cannot settle.
    # Threads overlap the waits; map keeps git's order.
    with ThreadPoolExecutor(max_workers=8) as pool:
        return [v for v in pool.map(judge, records) if v is not None]
