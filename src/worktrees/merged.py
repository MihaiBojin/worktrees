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

from .git import git


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
    ref: str, head: str, repo: str | os.PathLike[str] | None = None
) -> bool:
    """Compare a pinned commit's combined patch with the head branch."""

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
    wanted = ids[0].split()[0]
    upstream = upstream_patches(base.out.strip(), head, repo=repo).out
    return any(
        line.split()[0] == wanted for line in patch_ids(input=upstream, repo=repo).lines
    )


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


def branch_proof(
    branch: str,
    sha: str,
    head: str,
    head_branch: str,
    remote: str,
    ask_forge: bool,
    first_parents: set[str],
    repo: str | os.PathLike[str] | None = None,
) -> tuple[str, str, str, dict | None]:
    """The same pinned tip and head yield the same completion evidence."""
    from . import branch_forge

    if not head:
        return "unknown", "the head ref does not resolve", "", None
    request = (
        branch_forge.request_for(branch, remote, sha, repo)
        if branch and remote and ask_forge
        else None
    )
    data = request.data() if request is not None else None
    invalid = ""
    if request is not None and request.state == "MERGED":
        invalid = request.unverified_reason(branch, sha, head_branch)
        if not invalid:
            commit = tree_of(request.merge_sha, repo=repo)
            if not commit:
                invalid = "the request's merge commit is not available locally"
            elif is_ancestor(request.merge_sha, head, repo=repo):
                return (
                    "remove",
                    f"{request.noun} #{request.number} merged this tip "
                    "into the head branch",
                    "forge",
                    data,
                )
            else:
                invalid = "the request's merge commit is not in the head branch"
    if sha in first_parents:
        return (
            "empty",
            "its tip is on the head ref's first-parent history",
            "first-parent",
            data,
        )
    for proof, why, check in (
        ("ancestry", "its tip is an ancestor of the head branch", is_ancestor),
        (
            "squash",
            "its tree or combined patch is already in the head branch",
            squash_matches,
        ),
        ("content", "every changed path matches the head branch", content_matches),
    ):
        if check(sha, head, repo=repo):
            return "remove", why, proof, data
    if invalid:
        return "unknown", invalid, "", data
    if not merge_bases(sha, head, repo=repo).lines:
        return "unknown", "no common history with the head branch", "", data
    return "keep", "its commits are not contained in the head branch", "", data
