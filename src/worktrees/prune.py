"""Removing the worktrees `status` marked `go`, and nothing else."""

from __future__ import annotations

import os
import sys
from collections.abc import Callable
from pathlib import Path

from . import repo as R
from .git import GitError, Refused, git
from .verdicts import GO, Verdict


@git("worktree prune", mutates=True)
def prune_records() -> None:
    """Clear git's bookkeeping for worktrees somebody deleted by hand."""


@git("worktree remove -- $path", mutates=True)
def remove_worktree(path: str) -> None:
    """Drop a checkout. Refuses on its own when the worktree is dirty."""


@git("branch -d -- $branch", mutates=True)
def delete_branch(branch: str) -> None:
    """-d, never -D: git's own proof of merge is the only proof accepted."""


@git("rev-parse --verify $ref", ok=(0, 128))
def rev_parse(ref: str) -> None:
    """The sha a ref names, for the line that puts it back."""


@git("cat-file -t $sha", ok=(0, 128))
def object_type(sha: str) -> None:
    """What kind of object a sha is. Every printed sha must be a commit."""


def removable(verdicts: list[Verdict]) -> list[Verdict]:
    """The `go` rows, which is the whole of what prune may touch.

    The one place the set is derived. `status` prints these as `go` and
    `prune` removes these, so the two cannot drift.
    """
    return [v for v in verdicts if v.verdict == GO]


def restore_line(branch: str, repo: str | os.PathLike[str] | None = None) -> str:
    """The command that puts a branch back, or '' when no sha can be proved.

    The sha is checked to be a commit. The bug's signature is a correctly
    shaped restore line carrying the wrong sha, so the check is on the object
    and not on the sentence.
    """
    if not branch:
        return ""
    sha = rev_parse(f"refs/heads/{branch}", repo=repo)
    text = sha.out.strip() if sha else ""
    if not text:
        return ""
    kind = object_type(text, repo=repo)
    if not kind or kind.out.strip() != "commit":
        return ""
    return f"git branch {branch} {text}"


def plan(
    go: list[Verdict],
    delete_ignored: bool,
    say: Callable[[str], None],
    repo: str | os.PathLike[str] | None = None,
) -> None:
    """Print what would go, and what puts it back, before anything does.

    --quiet and --yes do not silence this.
    """
    for v in go:
        say(f"{v.label}  {v.path}")
        restore = restore_line(v.branch, repo=repo)
        if restore:
            say(f"  restore with: {restore}")
        if delete_ignored:
            for path in R.ignored_paths(v.path):
                say(f"  deleting ignored, unrecoverable: {path}")


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
    delete_ignored: bool,
    say: Callable[[str], None],
    repo: str | os.PathLike[str] | None = None,
) -> int:
    """Remove each one, in order. Returns how many failed.

    Serial because `worktree remove` and `branch -d` take the repository's
    shared refs and its worktrees/ directory. One that git refuses is not a
    reason to abandon the rest.
    """
    main = R.main_worktree(repo)
    failed = 0
    for v in go:
        try:
            remove_worktree(v.path, repo=repo)
        except (GitError, Refused) as exc:
            say(f"{v.label} kept: {exc}")
            failed += 1
            continue

        _prune_empty_parents(Path(v.path).parent, main)

        if v.branch:
            try:
                # -d, so git's own proof of merge decides. A squash-merged
                # branch is refused here and kept: the checkout goes, the
                # branch stays, and the restore line is not needed.
                delete_branch(v.branch, repo=repo)
            except (GitError, Refused) as exc:
                say(f"branch {v.branch} kept: {exc}")

    prune_records(repo=repo)
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
