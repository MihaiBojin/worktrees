"""One verdict per worktree. Nothing here mutates."""

from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

from . import branch_forge, merged, progress, sync
from . import repo as R
from .git import GitError

# The verdicts, each the instruction it gives. `unknown` is not `keep` with a
# softer word: "no common history with the head branch" is a different fact
# from "this has commits the head branch lacks", and a sweep printing them the
# same way invites somebody to act on the wrong one.
REMOVE = "remove"
KEEP = "keep"
UNKNOWN = "unknown"

MAIN_CHECKOUT = "it is the main checkout, and never removable"
HEAD_BRANCH = "it is the head branch"
MAIN_OR_MASTER = "it is main or master"


class Verdict:
    __slots__ = ("branch", "error", "ignored", "path", "sha", "verdict", "why")

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
    # git failed while judging it. The verdict is `unknown`, and the command
    # exits 1 the way `gwbs` does, so a script can tell this from unclear.
    error: bool

    def __init__(
        self,
        verdict: str,
        branch: str,
        path: str,
        why: str,
        ignored: int = 0,
        sha: str = "",
        error: bool = False,
    ) -> None:
        self.verdict = verdict
        self.branch = branch
        self.path = path
        self.why = why
        self.ignored = ignored
        self.sha = sha
        self.error = error

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
    proof: str = ""  # forge, ancestry, squash or content, on a remove
    request: dict | None = None  # what the last gw sync recorded for this tip


def recorded(evidence: dict, branch: str, sha: str) -> branch_forge.Request | None:
    """The request the last `gw sync` found for `branch` at exactly `sha`.

    None when it found none, when the branch has moved since, and for an
    entry in any shape `Request.data()` does not write.
    """
    entry = evidence.get(branch)
    if not isinstance(entry, dict) or entry.get("sha") != sha:
        return None
    return branch_forge.Request.from_data(entry.get("request"))


def prove(
    sha: str,
    branch: str,
    head: str,
    head_branch: str,
    evidence: dict,
    repository: str,
    say: Callable[[str], None],
) -> Proof:
    """Whether the change at `sha` is in the head branch.

    The one proof a worktree and a loose branch are both judged by.
    `evidence` is what the last `gw sync` recorded per branch, and a merged
    request it holds for exactly this tip, made in `repository` and merged
    into the head branch, is checked first. Nothing here asks the forge.
    """
    found = Proof(UNKNOWN, "no proof that its change is in the head branch")
    # A merged request that fails a check settles `unknown`, but only once
    # git has had its say: content can still prove what the forge did not.
    settled = False
    request = recorded(evidence, branch, sha)
    if request is not None:
        say("did the last gw sync find it merged?")
        found.request = request.data()
        if request.state != "MERGED":
            found.why = (
                f"{request.noun} #{request.number} is "
                f"{request.state.lower()}, not merged"
            )
        else:
            settled = True
            no = request.unverified_reason(branch, sha, head_branch, repository)
            if not no and R.object_type(request.merge_sha).out.strip() != "commit":
                no = "the request's merge commit is not available locally"
            if not no and merged.is_ancestor(request.merge_sha, head):
                found.verdict, found.proof = REMOVE, "forge"
                found.why = (
                    f"{request.noun} #{request.number} merged this tip "
                    "into the head branch"
                )
                return found
            found.why = no or "the request's merge commit is not in the head branch"
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
            return Proof(REMOVE, why, proof, found.request)
    if settled:
        return found
    if not merged.merge_bases(sha, head).lines:
        found.why = "no common history with the head branch"
        return found
    found.verdict = KEEP
    return found


def unresolved(records: list[R.Worktree], head_branch: str, why: str) -> list[Verdict]:
    """With no head ref, the main checkout and a worktree on the head branch,
    `main` or `master` are kept, since neither answer measures anything, and
    every other worktree is `unknown`."""
    main = R.main_worktree()
    rows = []
    for wt in records:
        if "bare" in wt.flags or "prunable" in wt.flags:
            continue
        if wt.path == main:
            rows.append(Verdict(KEEP, wt.branch, wt.path, MAIN_CHECKOUT, sha=wt.sha))
        elif wt.branch and wt.branch == head_branch:
            rows.append(Verdict(KEEP, wt.branch, wt.path, HEAD_BRANCH, sha=wt.sha))
        elif wt.branch in ("main", "master"):
            rows.append(Verdict(KEEP, wt.branch, wt.path, MAIN_OR_MASTER, sha=wt.sha))
        else:
            rows.append(
                Verdict(UNKNOWN, wt.branch, wt.path, why, sha=wt.sha, error=True)
            )
    return rows


def assess(
    records: list[R.Worktree],
    head: str,
    head_branch: str,
    delete_ignored: bool,
    here: str | None = None,
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
    evidence = (sync.read() or {}).get("evidence", {})
    repository = branch_forge.repository_for(R.remote())
    if here is None:
        here = R.toplevel().out.strip()

    def judge(wt: R.Worktree) -> Verdict | None:
        def say(verdict: str, why: str, ignored: int = 0) -> Verdict:
            return Verdict(verdict, wt.branch, wt.path, why, ignored, wt.sha)

        if "bare" in wt.flags or "prunable" in wt.flags:
            return None
        if wt.path == main:
            # Said before the standing-in rule below, because which one is
            # the main checkout is the more useful of the two facts and the
            # only one that is true from anywhere.
            if include_main:
                return say(KEEP, MAIN_CHECKOUT)
            return None

        if here and Path(wt.path).resolve() == Path(here).resolve():
            return say(KEEP, "you are standing in it")
        if "locked" in wt.flags:
            return say(KEEP, "it is locked")
        if wt.branch and wt.branch == head_branch:
            return say(KEEP, HEAD_BRANCH)
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

        found = prove(
            wt.sha,
            wt.branch,
            head,
            head_branch,
            evidence,
            repository,
            lambda question: progress.say(f"{wt.label}: {question}"),
        )
        ignored = status.ignored
        if found.verdict == REMOVE and ignored and not delete_ignored:
            return say(
                KEEP,
                f"{found.why}, but holds {len(ignored)} ignored path(s); "
                "pass --delete-ignored",
                len(ignored),
            )
        return say(found.verdict, found.why)

    def judged(wt: R.Worktree) -> Verdict | None:
        try:
            return judge(wt)
        except GitError as exc:
            return Verdict(
                UNKNOWN, wt.branch, wt.path, exc.line(), sha=wt.sha, error=True
            )

    # Each worktree is judged on its own, and most of the time goes waiting
    # on git. Threads overlap the waits; map keeps git's order.
    with ThreadPoolExecutor(max_workers=8) as pool:
        return [v for v in pool.map(judged, records) if v is not None]
