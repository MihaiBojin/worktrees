"""Is a branch's change already in the head branch?

`git branch --merged` answers for the merge git can see. Most branches now end
in a squash, which rewrites their commits into one, so git sees a branch whose
commits appear nowhere in the head branch: the same shape as a branch nobody
ever merged. Reaping on that reading loses work; refusing on it means never
reaping anything.

By hand the two are indistinguishable. `squashed` and `unmerged` both report
NOT merged and both sit one commit ahead. Opposite correct actions, no signal
between them.
"""

from __future__ import annotations

import os
import threading
from typing import TYPE_CHECKING

from .errors import MissingObjects
from .git import git

if TYPE_CHECKING:  # annotations only
    from . import branch_forge

# Set when a proof needed an object this partial clone lacks, so the command
# can say once why those branches are unknown.
lacking = threading.Event()
LACKING = "this partial clone lacks objects the proof needs"

# What a branch's commits say about it: the reason a row carries when nothing
# about its checkout decides it first.
MERGED = "merged"
UNUSED = "unused"
STALE = "stale"
CLOSED = "closed"
OPEN = "open"
PUSHED = "pushed"
UNPUSHED = "unpushed"
UNKNOWN = "unknown"

# Reflog subjects that record a commit made on the branch, as opposed to one
# that only moved it: a fast-forward, a reset or a rebase's finish.
MADE = ("commit", "cherry-pick", "revert", "am:")


@git("merge-base --is-ancestor $ref $head", ok=(0, 1))
def is_ancestor(ref: str, head: str) -> None:
    """Non-zero means 'no', not 'broken'."""


@git("merge-base $a $b", ok=(0, 1, 128))
def merge_base(a: str, b: str) -> None:
    """The commit two refs last had in common."""


@git("rev-parse $ref^{tree}", ok=(0, 128))
def tree_of(ref: str) -> None:
    """The tree a ref points at."""


@git(
    "diff --no-ext-diff --no-textconv --no-renames --full-index --binary "
    "--no-color --no-relative --ignore-submodules=none --submodule=short "
    "--unified=3 --inter-hunk-context=0 --diff-algorithm=myers "
    "--no-indent-heuristic $base $tip --",
    env={"GIT_DIFF_OPTS": ""},
)
def combined_patch(base: str, tip: str) -> None:
    """The branch's combined patch from its merge base."""


@git(
    "log --no-merges --format=%H -p --no-ext-diff --no-textconv --no-renames "
    "--full-index --binary --no-color --no-relative --ignore-submodules=none "
    "--submodule=short --unified=3 --inter-hunk-context=0 --diff-algorithm=myers "
    "--no-indent-heuristic $base..$head --",
    env={"GIT_DIFF_OPTS": ""},
)
def upstream_patches(base: str, head: str) -> None:
    """Individual upstream patches after the common ancestor."""


@git("patch-id --stable")
def patch_ids() -> None:
    """Patch identities read from standard input."""


def squash_matches(
    ref: str,
    head: str,
    repo: str | os.PathLike[str] | None = None,
    upstream: dict[str, set[str]] | None = None,
) -> bool:
    """Compare a pinned commit's combined patch with the head branch.

    `upstream` keeps the head's patch-ids by merge base, for a caller asking
    about several commits of one branch.
    """

    base = merge_base(head, ref, repo=repo)
    if not base or not base.out.strip():
        return False

    tree = tree_of(ref, repo=repo)
    if not tree or not tree.out.strip():
        return False

    # A branch leaving the head branch's tree exactly as it found it has
    # nothing left to contribute, whatever its history says.
    head_tree = tree_of(head, repo=repo)
    if head_tree and head_tree.out.strip() == tree.out.strip():
        return True

    patch = combined_patch(base.out.strip(), ref, repo=repo).out
    ids = patch_ids(input=patch, repo=repo).lines
    if not ids:
        return False
    return ids[0].split()[0] in _upstream_ids(base.out.strip(), head, repo, upstream)


def _upstream_ids(
    fork: str,
    head: str,
    repo: str | os.PathLike[str] | None,
    upstream: dict[str, set[str]] | None,
) -> set[str]:
    """The patch-ids of the head's commits after `fork`, kept in `upstream`."""
    known = {} if upstream is None else upstream
    if fork not in known:
        patches = upstream_patches(fork, head, repo=repo).out
        known[fork] = {
            line.split()[0] for line in patch_ids(input=patches, repo=repo).lines
        }
    return known[fork]


@git("merge-base --all $a $b", ok=(0, 1))
def merge_bases(a: str, b: str) -> None:
    """Every best common ancestor; none for unrelated histories."""


@git(
    "diff-tree --no-commit-id --no-ext-diff --no-renames "
    "--ignore-submodules=none -r --name-only -z $a $b --"
)
def changed_paths(a: str, b: str) -> None:
    """Paths whose objects or modes differ between two commits."""


def content_matches(
    tip: str, head: str, repo: str | os.PathLike[str] | None = None
) -> bool:
    """Every path the branch changes has its final state on the head branch."""
    bases = merge_bases(tip, head, repo=repo).lines
    if len(bases) != 1:
        return False
    touched = set(changed_paths(bases[0], tip, repo=repo).out.split("\0")) - {""}
    different = set(changed_paths(tip, head, repo=repo).out.split("\0")) - {""}
    return touched.isdisjoint(different)


@git("rev-list --first-parent $head")
def first_parent_history(head: str) -> None:
    """Every commit on the assessed head's first-parent history."""


@git("rev-list --topo-order $head..$tip")
def own_commits(head: str, tip: str) -> None:
    """The branch's commits the head lacks, newest first."""


@git("for-each-ref --count=1 --format=%(refname) --contains $sha refs/remotes/")
def remote_holding(sha: str) -> None:
    """A remote-tracking ref that holds the commit, if one does."""


@git("reflog show --format=%H%x00%gs $ref --", ok=(0, 128))
def reflog(ref: str) -> None:
    """A ref's reflog, newest first: each commit, then what moved the ref there."""


@git("config --get core.logAllRefUpdates", ok=(0, 1))
def logs_ref_updates() -> None:
    """`false` when git writes no reflog for a branch."""


@git("rev-list $*commits --not --all $*kept")
def unreachable(commits: list[str], kept: list[str]) -> None:
    """The commits these reach that no ref, worktree HEAD or `kept` one does."""


@git("rev-parse --verify --quiet $commit^1", ok=(0, 1))
def first_parent(commit: str) -> None:
    """The commit's first parent; nothing for a root commit."""


@git("show --no-patch --format=%H%x00%s $*commits")
def subjects(commits: list[str]) -> None:
    """Each commit and its subject."""


def reflog_shas(ref: str, repo: str | os.PathLike[str] | None = None) -> list[str]:
    """Every commit a ref's reflog names, once each."""
    entries = reflog(ref, repo=repo)
    return list(dict.fromkeys(line.split("\0")[0] for line in entries.lines))


def first_parent_reason(
    branch: str, repo: str | os.PathLike[str] | None = None
) -> tuple[str, str]:
    """Whether a branch whose tip is on the head's first-parent history was used.

    Its reflog tells a fresh branch from one whose commits went in by
    fast-forward. With no reflog left, every commit the branch reaches is
    still in the head, so nothing is lost by removing it.
    """
    entries = reflog(f"refs/heads/{branch}", repo=repo).lines if branch else []
    if any(line.split("\0", 1)[-1].startswith(MADE) for line in entries):
        return MERGED, "commits made on it are on the head ref's first-parent history"
    if entries or not branch or logs_ref_updates(repo=repo).value == "false":
        return UNUSED, "its tip is on the head ref's first-parent history"
    return STALE, (
        "its tip is on the head ref's first-parent history, and no reflog is "
        "left to say it was used"
    )


def reflog_only(
    lose: list[str],
    keep: list[str],
    head: str,
    repo: str | os.PathLike[str] | None = None,
    upstream: dict[str, set[str]] | None = None,
) -> tuple[tuple[str, str], ...]:
    """The commits only the reflogs being deleted reach, whose change the head
    lacks, each with its subject.

    `lose` names the commits of the reflogs a removal deletes, `keep` those of
    the reflogs it leaves. A commit counts as in the head when its own patch
    is there since it forked; one this partial clone cannot read counts as
    missing.
    """
    commits = unreachable(lose, keep, repo=repo).lines if lose else []
    try:
        left = [c for c in commits if not change_in(c, head, repo, upstream)]
    except MissingObjects:
        lacking.set()
        left = commits
    if not left:
        return ()
    return tuple(
        (sha, subject)
        for sha, _, subject in (
            line.partition("\0") for line in subjects(left, repo=repo).lines
        )
    )


def change_in(
    commit: str,
    head: str,
    repo: str | os.PathLike[str] | None = None,
    upstream: dict[str, set[str]] | None = None,
) -> bool:
    """The commit's own change is in the head, by patch-id since it forked."""
    base = merge_base(head, commit, repo=repo).value
    parent = first_parent(commit, repo=repo).value
    if not base or not parent:
        return False
    ids = patch_ids(input=combined_patch(parent, commit, repo=repo).out, repo=repo)
    if not ids.lines:
        # It changes nothing, so nothing of it can be lost.
        return True
    return ids.lines[0].split()[0] in _upstream_ids(base, head, repo, upstream)


def branch_proof(
    branch: str,
    sha: str,
    head: str,
    head_branch: str,
    remote: str,
    ask_forge: bool,
    first_parents: set[str],
    repo: str | os.PathLike[str] | None = None,
    live: branch_forge.Request | None = None,
) -> tuple[str, str, str, dict | None]:
    """The same pinned tip and head yield the same completion evidence.

    `live` is a request read from the forge in this run, for a caller that
    must not trust the cache; `ask_forge` reads the cache otherwise.
    """
    from . import branch_forge

    if not head:
        return UNKNOWN, "the head ref does not resolve", "", None
    request = live or (
        branch_forge.request_for(branch, remote, sha, repo)
        if branch and remote and ask_forge
        else None
    )
    data = request.data() if request is not None else None
    invalid = ""
    if request is not None and request.state == "MERGED":
        invalid = request.unverified_reason(branch, sha, head_branch, first_parents)
        if not invalid:
            commit = tree_of(request.merge_sha, repo=repo)
            if not commit:
                invalid = "the request's merge commit is not available locally"
            elif is_ancestor(request.merge_sha, head, repo=repo):
                return (
                    MERGED,
                    f"{request.noun} #{request.number} merged this tip "
                    + request.merged_into(head_branch),
                    "forge",
                    data,
                )
            else:
                invalid = "the request's merge commit is not in the head branch"
    if sha in first_parents:
        return (
            UNUSED,
            "its tip is on the head ref's first-parent history",
            "first-parent",
            data,
        )
    try:
        found = _local_proof(sha, head, repo)
    except MissingObjects:
        lacking.set()
        return UNKNOWN, LACKING, "", data
    if found:
        return MERGED, found[1], found[0], data
    if invalid:
        return UNKNOWN, invalid, "", data
    if not merge_bases(sha, head, repo=repo).lines:
        return UNKNOWN, "no common history with the head branch", "", data
    if request is not None and request.state == "CLOSED":
        noun = f"{request.noun} #{request.number}"
        return CLOSED, f"{noun} was closed without merging this tip", "", data
    if request is not None and request.state == "OPEN":
        return OPEN, f"{request.noun} #{request.number} is open for this tip", "", data
    if remote_holding(sha, repo=repo).value:
        return PUSHED, "its commits are on a remote, not in the head branch", "", data
    return UNPUSHED, "its commits are only here, not in the head branch", "", data


def boundary(
    tip: str, head: str, repo: str | os.PathLike[str] | None = None
) -> tuple[str, str, str] | None:
    """The newest commit below the tip whose change the head already has.

    For a branch whose first commits were squash-merged and which went on
    afterwards. Asked from the tip down, because the answer is not monotone:
    the first commit of a squashed pair is not upstream on its own, while the
    pair is. Returns the commit, its proof and why, or None when no commit
    below the tip is proved, or when this partial clone lacks what a proof
    needs, which leaves the branch to an ordinary rebase.
    """
    upstream: dict[str, set[str]] = {}
    for commit in own_commits(head, tip, repo=repo).lines[1:]:
        try:
            found = _local_proof(commit, head, repo, upstream)
        except MissingObjects:
            lacking.set()
            return None
        if found:
            return commit, found[0], found[1]
    return None


def _local_proof(
    sha: str,
    head: str,
    repo: str | os.PathLike[str] | None,
    upstream: dict[str, set[str]] | None = None,
) -> tuple[str, str] | None:
    """The first local proof that the tip's change is in the head, if any."""
    if is_ancestor(sha, head, repo=repo):
        return "ancestry", "its tip is an ancestor of the head branch"
    if squash_matches(sha, head, repo=repo, upstream=upstream):
        return "squash", "its tree or combined patch is already in the head branch"
    if content_matches(sha, head, repo=repo):
        return "content", "every changed path matches the head branch"
    return None
