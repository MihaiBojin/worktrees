"""Removing the eligible worktrees, and nothing else."""

from __future__ import annotations

import os
import shlex
import sys
from collections.abc import Callable
from pathlib import Path

from . import render, sync
from . import repo as R
from .git import GitError, Refused, git
from .verdicts import Verdict


@git("worktree prune", mutates=True)
def worktree_prune() -> None:
    """Clear git's bookkeeping for worktrees somebody deleted by hand."""


@git("worktree remove -- $path", mutates=True)
def worktree_remove(path: str) -> None:
    """Drop a checkout. Refuses on its own when the worktree is dirty."""


@git("update-ref --no-deref -d $ref $sha", mutates=True)
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


# One call for the whole repository, because refs/stash is per-repository
# rather than per-branch. The quotes survive shlex.split, so the format stays
# one argv element.
@git("stash list --format='%gd %gs'")
def stash_list() -> None:
    """Every stash entry: its selector, then the subject naming its branch."""


def removable(verdicts: list[Verdict]) -> list[Verdict]:
    """The eligible rows, which is the whole of what gwp may touch.

    The one place the set is derived. The counts line counts these and gwp
    removes these, so the two cannot drift.
    """
    return [v for v in verdicts if v.eligible]


def restore_line(branch: str, repo: str | os.PathLike[str] | None = None) -> str:
    """The command that puts a branch back, or '' when no sha can be proved.

    The sha is checked to be a commit. The bug's signature is a correctly
    shaped restore line carrying the wrong sha, so the check is on the object
    and not on the sentence.

    The name is quoted, because this line exists to be pasted and git permits
    `$`, a backtick and `;` in a branch name. `shlex.quote` writes POSIX
    single quotes, which bash, zsh and fish all read the same way.
    """
    sha = restore_sha(branch, repo=repo)
    return f"git branch {shlex.quote(branch)} {sha}" if sha else ""


def restore_sha(branch: str, repo: str | os.PathLike[str] | None = None) -> str:
    """The commit `restore_line` names, or '' when none can be proved."""
    if not branch:
        return ""
    sha = sha_of(f"refs/heads/{branch}", repo=repo)
    text = sha.out.strip() if sha else ""
    if not text:
        return ""
    kind = R.object_type(text, repo=repo)
    if not kind or kind.out.strip() != "commit":
        return ""
    return text


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
) -> bool:
    """Print what would go, and what puts it back, before anything does.
    Returns whether the repository was never synced, which the plan says
    last and the question asks about.

    --quiet and --yes do not silence this.

    Every ignored path is named, whatever flag got the worktree this far:
    `git worktree remove` takes the whole directory, so --delete ignored-files
    decides consent and never decides what is deleted.
    """
    entries = stash_list(repo=repo).lines
    for v in go:
        say(f"{render.branch(v.label)}  {render.err(v.path, render.DIM)}")
        sha = restore_sha(v.branch, repo=repo)
        if sha:
            restore = render.restore(v.branch, sha)
            say(f"  {render.err('restore with:', render.DIM)} {restore}")
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
    never = sync.never()
    if never:
        say(render.warn(sync.NEVER))
    return never


def _prompt(text: str) -> str:
    """The question on stderr, the answer from stdin.

    Not `input`, which puts its prompt on stdout: stdout carries the result
    and nothing else, so `--json` stays parseable in every mode.
    """
    sys.stderr.write(text)
    sys.stderr.flush()
    return sys.stdin.readline()


def confirm(
    count: int, ask: Callable[[str], str] | None = None, never: bool = False
) -> bool:
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
    question = render.ask(
        sync.ASK if never else f"remove {count} worktree(s)?",
        "[y/N]",
    )
    return ask(question).strip().lower() in ("y", "yes")


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
            say(render.fail(f"{render.branch(v.label)} kept: {exc}"))
            failed += 1
            continue

        _prune_empty_parents(Path(v.path).parent, main)

        if v.branch:
            try:
                ref_delete(f"refs/heads/{v.branch}", v.sha, repo=repo)
            except (GitError, Refused) as exc:
                say(render.fail(f"branch {render.branch(v.branch)} kept: {exc}"))

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
