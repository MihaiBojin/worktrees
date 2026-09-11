"""Removing the worktrees `status` marked `remove`, and nothing else."""

from __future__ import annotations

import os
import sys
from collections.abc import Callable
from pathlib import Path

from . import render
from . import repo as R
from .git import GitError, Refused, git
from .verdicts import REMOVE, Verdict


@git("worktree prune", mutates=True)
def worktree_prune() -> None:
    """Clear git's bookkeeping for worktrees somebody deleted by hand."""


@git("worktree remove -- $path", mutates=True)
def worktree_remove(path: str) -> None:
    """Drop a checkout. Refuses on its own when the worktree is dirty."""


@git("update-ref -d $ref $sha", mutates=True)
def ref_delete(ref: str, sha: str) -> None:
    """Delete a branch ref, and only while it still holds $sha.

    Not `branch -d`, which decides on history and so refuses the squash merge
    this program proved by content; not `branch -D`, which decides on nothing
    and is refused by the guard. The sha is the one the verdict was formed
    against, so a commit landing on the branch in between fails here.
    """


@git("rev-parse --verify $ref", ok=(0, 128))
def sha_of(ref: str) -> None:
    """The sha a ref names, for the line that puts it back."""


@git("cat-file -t $sha", ok=(0, 128))
def object_type(sha: str) -> None:
    """What kind of object a sha is. Every printed sha must be a commit."""


# One call for the whole repository, because refs/stash is per-repository
# rather than per-branch. The quotes survive shlex.split, so the format stays
# one argv element.
@git("stash list --format='%gd %gs'")
def stash_list() -> None:
    """Every stash entry: its selector, then the subject naming its branch."""


def removable(verdicts: list[Verdict]) -> list[Verdict]:
    """The `remove` rows, which is the whole of what prune may touch.

    The one place the set is derived. `status` prints these as `remove` and
    `prune` removes these, so the two cannot drift.
    """
    return [v for v in verdicts if v.verdict == REMOVE]


def restore_line(branch: str, repo: str | os.PathLike[str] | None = None) -> str:
    """The command that puts a branch back, or '' when no sha can be proved.

    The sha is checked to be a commit. The bug's signature is a correctly
    shaped restore line carrying the wrong sha, so the check is on the object
    and not on the sentence.
    """
    if not branch:
        return ""
    sha = sha_of(f"refs/heads/{branch}", repo=repo)
    text = sha.out.strip() if sha else ""
    if not text:
        return ""
    kind = object_type(text, repo=repo)
    if not kind or kind.out.strip() != "commit":
        return ""
    return f"git branch {branch} {text}"


def stashes_on(branch: str, entries: list[str]) -> list[str]:
    """The selectors of the stash entries made on this branch.

    `git stash` writes `WIP on <branch>: ...` and `git stash push -m` writes
    `On <branch>: ...`, so the branch is in the subject and nowhere else.
    """
    if not branch:
        return []
    found = []
    for line in entries:
        selector, _, subject = line.partition(" ")
        if subject.startswith((f"On {branch}:", f"WIP on {branch}:")):
            found.append(selector)
    return found


def plan(
    go: list[Verdict],
    say: Callable[[str], None],
    repo: str | os.PathLike[str] | None = None,
) -> None:
    """Print what would go, and what puts it back, before anything does.

    --quiet and --yes do not silence this.

    Every ignored path is named, whatever flag got the worktree this far:
    `git worktree remove` takes the whole directory, so --delete-ignored
    decides consent and never decides what is deleted.
    """
    entries = stash_list(repo=repo).lines
    for v in go:
        say(f"{render.err(v.label, render.BOLD)}  {render.err(v.path, render.DIM)}")
        restore = restore_line(v.branch, repo=repo)
        if restore:
            say(render.err(f"  restore with: {restore}", render.DIM))
        # A stash survives the branch it was made on: refs/stash pins its own
        # commits. What does not survive is the name in its subject, so say
        # which entry is about to start pointing at nothing.
        for selector in stashes_on(v.branch, entries):
            say(
                render.err(
                    f"  {selector} was made on this branch and outlives it: "
                    f"git stash branch <new> {selector}",
                    render.DIM,
                )
            )
        for path in R.ignored_paths(v.path):
            say(render.err(f"  deleting ignored, unrecoverable: {path}", render.YELLOW))


def _prompt(text: str) -> str:
    """The question on stderr, the answer from stdin.

    Not `input`, which puts its prompt on stdout: stdout carries the result
    and nothing else, so `--json` stays parseable in every mode.
    """
    sys.stderr.write(text)
    sys.stderr.flush()
    return sys.stdin.readline()


def confirm(count: int, ask: Callable[[str], str] | None = None) -> bool:
    """Ask before removing. Anything but yes is no.

    A run whose stdin is not a terminal cannot answer, so it is refused
    rather than left to block: an agent or a pipe reaches this and would
    otherwise hang forever holding the repository's worktrees.
    """
    if ask is None:
        if not sys.stdin.isatty():
            raise Refused(
                f"not a terminal, so nothing can answer for the {count} above; "
                "pass --yes to remove them"
            )
        ask = _prompt
    return ask(f"remove {count} worktree(s)? [y/N] ").strip().lower() in ("y", "yes")


def sweep(
    go: list[Verdict],
    say: Callable[[str], None],
    repo: str | os.PathLike[str] | None = None,
) -> int:
    """Remove each one, in order. Returns how many failed.

    Serial because `worktree remove` and `update-ref` take the repository's
    shared refs and its worktrees/ directory. One that git refuses is not a
    reason to abandon the rest.
    """
    main = R.main_worktree(repo)
    failed = 0
    for v in go:
        try:
            worktree_remove(v.path, repo=repo)
        except (GitError, Refused) as exc:
            say(f"{v.label} kept: {exc}")
            failed += 1
            continue

        _prune_empty_parents(Path(v.path).parent, main)

        if v.branch:
            try:
                ref_delete(f"refs/heads/{v.branch}", v.sha, repo=repo)
            except (GitError, Refused) as exc:
                say(f"branch {v.branch} kept: {exc}")

    worktree_prune(repo=repo)
    return failed


def _prune_empty_parents(start: Path, main: str) -> None:
    """Remove the directories that removing a worktree left empty.

    Up to the worktrees root and never past it. rmdir refuses a directory
    holding anything, which is the whole guard.
    """
    if not main:
        return
    root = Path(main).parent / ".worktrees"
    d = start
    while d == root or root in d.parents:
        try:
            d.rmdir()
        except OSError:
            return
        if d == root:
            return
        d = d.parent
