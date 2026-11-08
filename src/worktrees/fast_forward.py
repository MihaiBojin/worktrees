"""Bring the branch you stand on level with the head ref.

A branch with commits of its own is rebased onto the head ref, and one
without is fast-forwarded to it. A branch whose change is already in the head
ref, proved the way `gw prune` proves one, keeps its name and its commits, and
the checkout moves to a new branch started from the head ref. A merged
branch's name stays with its merged request on the forge, so the next change
gets a name of its own. The head branch itself only ever fast-forwards.
"""

from __future__ import annotations

import os
import re
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from . import merged, render
from . import repo as R
from .git import git
from .new_branch import Refusal, check_ref_format, short_sha, switch_create

CURRENT = "current"
FAST_FORWARDED = "fast-forwarded"
REBASED = "rebased"
ROTATED = "rotated"
CONFLICT = "conflict"

# What a rotation puts in front of the branch's name: the UTC time it ran,
# with `-` where the clock has `:`, which no ref may hold.
PREFIX = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}-\d{2}-\d{2}Z/")

_NAMING = "NAME and --from only name the branch a finished one moves to"


# Both take --no-autostash whatever merge.autoStash and rebase.autoStash say,
# so uncommitted changes stay in the checkout or git refuses, rather than
# going to the stash list every worktree shares.
@git("merge --ff-only --no-autostash $ref", mutates=True)
def merge_ff_only(ref: str) -> None:
    """Moves a branch with no commits of its own, the head branch included."""


@git("rebase --no-autostash $onto", ok=(0, 1), mutates=True)
def rebase(onto: str) -> None:
    """Replay the branch's own commits onto `onto`; 1 is a refusal or a stop."""


@git("log --oneline --no-decorate $range")
def log_oneline(range: str) -> None:
    """The commits in a range, for saying which ones are in the way."""


@git("rev-parse --path-format=absolute --git-path rebase-merge --git-path rebase-apply")
def rebase_dirs() -> None:
    """Where a stopped rebase keeps its state, under either backend."""


@git("show --no-patch --format=%h%n%s REBASE_HEAD", ok=(0, 128))
def rebase_head() -> None:
    """The commit a stopped rebase could not replay, then its subject."""


@git("diff --name-only --no-relative --diff-filter=U -z")
def unmerged_paths() -> None:
    """The files a stopped rebase left with conflicts, from the top level."""


@dataclass
class Moved:
    """Where the checkout stands after one run, and how it got there."""

    result: str  # current, fast-forwarded, rebased, rotated or conflict
    branch: str  # checked out now; while stopped, the one being rebased
    base: str  # the full ref, or the short commit, it was brought level with
    sha: str  # where HEAD is now
    previous: str = ""  # the branch before the run; another one after a rotation
    previous_sha: str = ""
    ahead: int = 0  # commits of its own on top of `base` after the run
    behind: int = 0  # commits of `base` it lacked before the run
    proof: str = ""  # how a rotated branch was proved finished
    why: str = ""
    stopped_at: str = ""  # the commit a stopped rebase could not replay
    subject: str = ""
    paths: list[str] = field(default_factory=list)  # files left with conflicts

    def data(self) -> dict:
        return asdict(self)


def stem(branch: str) -> str:
    """The branch without the prefix a previous rotation gave it.

    So rotating twice gives two siblings rather than one name carrying two
    timestamps.
    """
    return PREFIX.sub("", branch)


def rotation_name(branch: str, now: datetime | None = None) -> str:
    """`YYYY-MM-DDThh-mm-ssZ/<stem>`, at the time given or now."""
    moment = now or datetime.now(UTC)
    return f"{moment:%Y-%m-%dT%H-%M-%SZ}/{stem(branch)}"


def fast_forward(
    name: str = "",
    start: str = "",
    repo: str | os.PathLike[str] | None = None,
    now: datetime | None = None,
) -> Moved:
    """Bring the current branch level with the head ref, or rotate a finished
    one onto `name`, started from `start` or the head ref."""
    stopped = stopped_rebase(repo)
    if stopped:
        return stopped

    branch = R.current_branch(repo=repo).value
    if not branch:
        raise Refusal(
            "HEAD is detached, so there is no branch to bring up to date; "
            "'gw branch create <name>' names one"
        )

    remote = R.remote(repo=repo)
    resolved = R.head(repo)
    if not resolved.ref:
        raise Refusal(resolved.unresolved)
    base = R.symbolic_ref(resolved.ref, repo=repo).value or resolved.ref
    shown = render.ref(R.ref_name(base))

    head_name = R.ref_name(base)
    if remote:
        head_name = head_name.removeprefix(remote + "/")

    if branch == head_name:
        _unnamed(name, start, f"{render.branch(branch)} is the head branch")
        return _catch_up(branch, base, repo=repo)

    local = f"refs/heads/{branch}"
    if merged.is_ancestor(base, local, repo=repo):
        _unnamed(name, start, f"{render.branch(branch)} already has {shown}")
        sha = _short(local, repo=repo)
        ahead = _count(base, local, repo)
        return Moved(CURRENT, branch, base, sha, branch, sha, ahead=ahead)

    tip = R.local_sha(local, repo=repo).value
    head_sha = R.local_sha(base, repo=repo).value
    first_parents = set(merged.first_parent_history(head_sha, repo=repo).lines)
    verdict, why, proof, _ = merged.branch_proof(
        branch, tip, head_sha, resolved.branch, remote, True, first_parents, repo
    )
    if verdict == "remove":
        return _rotate(branch, base, name, start, proof, why, remote, repo, now)
    if verdict == "unknown":
        raise Refusal(
            f"cannot tell whether {render.branch(branch)} is finished, so it is "
            f"left as it is: {why}"
        )
    _unnamed(name, start, f"{render.branch(branch)} is not finished: {why}")

    previous_sha = _short(local, repo=repo)
    behind = _count(local, base, repo)
    if verdict == "empty":
        merge_ff_only(base, repo=repo)
        return Moved(
            FAST_FORWARDED,
            branch,
            base,
            _short(local, repo=repo),
            branch,
            previous_sha,
            behind=behind,
        )

    replayed = rebase(base, repo=repo)
    if not replayed:
        stopped = stopped_rebase(repo)
        if stopped:
            return stopped
        raise Refusal(replayed.err.strip() or f"git rebase exited {replayed.code}")
    return Moved(
        REBASED,
        branch,
        base,
        _short(local, repo=repo),
        branch,
        previous_sha,
        ahead=_count(base, local, repo),
        behind=behind,
    )


def stopped_rebase(repo: str | os.PathLike[str] | None = None) -> Moved | None:
    """The rebase this checkout is stopped in, if any, read from git's state.

    The files are the ones git's own prompt reads. Each holds one value and
    its newline, which is all that comes off: a branch name may end in a
    character `str.strip` would also take.
    """
    found = [Path(p) for p in rebase_dirs(repo=repo).lines if Path(p).is_dir()]
    if not found:
        return None
    state = found[0]

    def read(name: str) -> str:
        try:
            raw = (state / name).read_bytes()
        except OSError:
            return ""
        return raw.decode("utf-8", "surrogateescape").removesuffix("\n")

    head_name = read("head-name")
    # `detached HEAD` when the rebase started from no branch.
    branch = (
        head_name[len("refs/heads/") :] if head_name.startswith("refs/heads/") else ""
    )
    stopped = rebase_head(repo=repo).lines
    return Moved(
        CONFLICT,
        branch,
        _short(read("onto"), repo=repo),
        _short("HEAD", repo=repo),
        branch,
        _short(read("orig-head"), repo=repo),
        stopped_at=stopped[0] if stopped else "",
        subject=stopped[1] if len(stopped) > 1 else "",
        paths=[p for p in unmerged_paths(repo=repo).out.split("\0") if p],
    )


def start_point(value: str, repo: str | os.PathLike[str] | None = None) -> str:
    """The full ref or short commit `--from` names.

    A branch before a commit, and its full ref rather than the bare name, which
    git would read as a tag first. Only hex is tried as a commit, so nothing
    that starts with `-` reaches git as an option.
    """
    ref = R.full_ref(value, repo=repo)
    if ref and R.ref_exists(ref, repo=repo):
        return ref
    if re.fullmatch(r"[0-9a-f]{4,64}", value):
        commit = R.local_sha(value, repo=repo)
        if commit:
            return _short(commit.value, repo=repo)
    raise Refusal(f"{render.ref(value)} names no branch or commit here")


def _unnamed(name: str, start: str, reason: str) -> None:
    """Refuse a NAME or --from given to a branch that is not rotating."""
    if name or start:
        raise Refusal(f"{reason}; {_NAMING}")


def _rotate(
    branch: str,
    base: str,
    name: str,
    start: str,
    proof: str,
    why: str,
    remote: str,
    repo: str | os.PathLike[str] | None,
    now: datetime | None,
) -> Moved:
    """Leave a finished branch as it is and start the next one.

    Free means free here and on the remote: a name only the remote holds is
    somebody's branch, and pushing this one would land on it.
    """
    chosen = name or rotation_name(branch, now)
    if not check_ref_format(chosen, repo=repo):
        raise Refusal(f"{render.branch(chosen)} is not a valid branch name")
    if R.ref_exists(f"refs/heads/{chosen}", repo=repo):
        raise Refusal(f"{render.branch(chosen)} is already a branch")
    if remote and R.ref_exists(f"refs/remotes/{remote}/{chosen}", repo=repo):
        raise Refusal(
            f"{render.ref(f'{remote}/{chosen}')} already exists; another NAME "
            "keeps the two apart"
        )
    origin = start_point(start, repo) if start else base
    previous_sha = _short(f"refs/heads/{branch}", repo=repo)
    switch_create(chosen, origin, repo=repo)
    return Moved(
        ROTATED,
        chosen,
        origin,
        _short("HEAD", repo=repo),
        branch,
        previous_sha,
        proof=proof,
        why=why,
    )


def _catch_up(
    branch: str, base: str, repo: str | os.PathLike[str] | None = None
) -> Moved:
    """There is nothing to rebase the head branch onto, so bring it level.

    --ff-only refuses when the local copy is ahead, which is the answer: those
    commits are a change of their own and belong on a branch.
    """
    local = f"refs/heads/{branch}"
    count = _count(base, local, repo)
    if count:
        listed = log_oneline(f"{base}..{local}", repo=repo)
        lines = "\n".join(f"  {line}" for line in listed.lines) if listed else ""
        raise Refusal(
            f"{render.branch(branch)} is {count} commit(s) ahead of "
            f"{render.ref(R.ref_name(base))}, so it "
            f"cannot be fast-forwarded:\n{lines}\n"
            "Those commits are a change of their own; "
            "'gw branch create <name>' puts them on one."
        )
    behind = _count(local, base, repo)
    previous_sha = _short(local, repo=repo)
    if not behind:
        return Moved(CURRENT, branch, base, previous_sha, branch, previous_sha)
    merge_ff_only(base, repo=repo)
    return Moved(
        FAST_FORWARDED,
        branch,
        base,
        _short(local, repo=repo),
        branch,
        previous_sha,
        behind=behind,
    )


def _short(ref: str, repo: str | os.PathLike[str] | None = None) -> str:
    if not ref:
        return ""
    sha = short_sha(ref, repo=repo)
    return sha.out.strip() if sha else ""


def _count(a: str, b: str, repo: str | os.PathLike[str] | None = None) -> int:
    """Commits reachable from `b` and not from `a`."""
    counted = R.count_between(a, b, repo=repo)
    return int(counted.out.strip() or 0) if counted else 0
