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

# commit-tree fails outright when user.email is unset, and a fixed identity
# keeps the synthetic commit reproducible. Nothing references it, so the next
# gc collects it.
_SYNTHETIC = {
    "GIT_AUTHOR_NAME": "worktrees",
    "GIT_AUTHOR_EMAIL": "worktrees@localhost",
    "GIT_AUTHOR_DATE": "@0 +0000",
    "GIT_COMMITTER_NAME": "worktrees",
    "GIT_COMMITTER_EMAIL": "worktrees@localhost",
    "GIT_COMMITTER_DATE": "@0 +0000",
}


@git(ok=(0, 1))
def is_ancestor(ref: str, head: str) -> tuple[str, ...]:
    """Non-zero means 'no', not 'broken'."""
    return "merge-base", "--is-ancestor", ref, head


@git(ok=(0, 128))
def merge_base(a: str, b: str) -> tuple[str, ...]:
    """The commit two refs last had in common."""
    return "merge-base", a, b


@git(ok=(0, 128))
def tree_of(ref: str) -> tuple[str, ...]:
    """The tree a ref points at."""
    return "rev-parse", f"{ref}^{{tree}}"


@git(env=_SYNTHETIC, ok=(0, 128))
def commit_tree(tree: str, parent: str) -> tuple[str, ...]:
    """Replay a tree as one commit on top of a parent."""
    return "commit-tree", tree, "-p", parent, "-m", "_"


@git(ok=(0, 128))
def cherry(head: str, synth: str) -> tuple[str, ...]:
    """Compare by patch content, which is what a squash preserves."""
    return "cherry", head, synth


def squash_merged(
    branch: str, head: str, repo: str | os.PathLike[str] | None = None
) -> bool:
    """Is the change `branch` makes already in `head`?

    Replay the branch's tree as a single commit on the merge base and let
    `git cherry` say whether that patch is upstream. A leading '-' means it is.
    """
    ref = f"refs/heads/{branch}"

    tree = tree_of(ref, repo=repo)
    if not tree or not tree.out.strip():
        return False

    # A branch leaving the head branch's tree exactly as it found it has
    # nothing left to contribute, whatever its history says.
    head_tree = tree_of(head, repo=repo)
    if head_tree and head_tree.out.strip() == tree.out.strip():
        return True

    base = merge_base(head, ref, repo=repo)
    if not base or not base.out.strip():
        return False

    synth = commit_tree(tree.out.strip(), base.out.strip(), repo=repo)
    if not synth or not synth.out.strip():
        return False

    verdict = cherry(head, synth.out.strip(), repo=repo)
    lines = verdict.lines if verdict else []
    return bool(lines) and lines[0].startswith("-")


def merged_reason(
    branch: str, head: str, repo: str | os.PathLike[str] | None = None
) -> str:
    """'merged', 'squash-merged', or '' when neither.

    refs/heads/ explicitly: a branch name used as a revision resolves to a tag
    first, so a tag and a branch sharing a name would answer about the tag.
    """
    if is_ancestor(f"refs/heads/{branch}", head, repo=repo):
        return "merged"
    if squash_merged(branch, head, repo=repo):
        return "squash-merged"
    return ""
