"""One verdict per worktree, and the sweep that acts on them."""

from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from . import repo as R
from .git import GitError, Refused, git
from .merged import merged_reason

# The verdicts. `unknown` is not `keep` with a softer word: "no upstream, so
# nothing says whether this was pushed" is a different fact from "this is not
# merged", and a sweep printing them the same way invites somebody to act on
# the wrong one.
GO = "go"
KEEP = "keep"
UNKNOWN = "unknown"


@dataclass(frozen=True)
class Verdict:
    verdict: str
    branch: str  # empty when detached
    path: str
    why: str

    @property
    def label(self) -> str:
        return self.branch or "(detached)"


@git(mutates=True)
def prune_records() -> tuple[str, ...]:
    """Clear git's bookkeeping for worktrees somebody deleted by hand."""
    return "worktree", "prune"


@git(mutates=True)
def remove_worktree(path: str) -> tuple[str, ...]:
    """Drop a checkout. Refuses on its own when the worktree is dirty."""
    return "worktree", "remove", "--", path


@git(mutates=True)
def delete_branch(branch: str) -> tuple[str, ...]:
    """-d, never -D: git's own proof of merge is the only proof accepted."""
    return "branch", "-d", "--", branch


@git(ok=(0, 128))
def rev_parse(ref: str) -> tuple[str, ...]:
    """The sha a ref names, for the line that puts it back."""
    return "rev-parse", "--verify", ref


@git(ok=(0, 128))
def object_type(sha: str) -> tuple[str, ...]:
    """What kind of object a sha is. Every printed sha must be a commit."""
    return "cat-file", "-t", sha


def assess(
    only: str,
    head: str,
    head_branch: str,
    delete_ignored: bool,
    repo: str | os.PathLike[str] | None = None,
) -> list[Verdict]:
    """Every worktree, in the order `git worktree remove` would refuse them.

    Proposing something that would then be refused is a bug here, not a
    surprise at the confirmation.
    """
    main = R.main_worktree(repo)
    here = R.toplevel(repo=repo).out.strip()
    head_name = R.ref_name(head)

    out: list[Verdict] = []
    for wt in R.worktrees(repo):
        if "bare" in wt.flags or wt.path == main:
            continue
        if only and wt.branch != only:
            continue

        def say(verdict: str, why: str, wt: R.Worktree = wt) -> None:
            out.append(Verdict(verdict, wt.branch, wt.path, why))

        if here and Path(wt.path).resolve() == Path(here).resolve():
            say(KEEP, "you are standing in it")
            continue
        if "locked" in wt.flags:
            say(KEEP, "it is locked")
            continue
        if wt.branch and wt.branch == head_branch:
            say(KEEP, "it is the head branch")
            continue
        if R.is_dirty(wt.path):
            say(KEEP, "it has uncommitted changes")
            continue

        if not wt.branch:
            # Detached: finished when some ref already reaches the commit,
            # which is the question `git worktree remove` asks of one.
            if R.refs_containing(wt.sha, repo=repo).out.strip():
                say(GO, "its commit is reached by a ref")
            else:
                say(UNKNOWN, f"no ref reaches {wt.sha}")
            continue

        reason = merged_reason(wt.branch, head, repo=repo)
        if not reason:
            if R.unpushed_count(wt.branch, repo=repo) is None:
                say(
                    UNKNOWN,
                    f"not merged into {head_name}, and no upstream says whether "
                    "its commits were pushed",
                )
            else:
                say(KEEP, f"not merged into {head_name}")
            continue

        ignored = R.ignored_paths(wt.path)
        if ignored and not delete_ignored:
            say(
                KEEP,
                f"{reason}, but holds {len(ignored)} ignored path(s); "
                "pass --delete-ignored",
            )
            continue

        say(GO, reason)
    return out


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


def sweep(
    verdicts: list[Verdict],
    delete_ignored: bool,
    say: Callable[[str], None],
    repo: str | os.PathLike[str] | None = None,
) -> int:
    """Remove every `go`, one at a time. Returns how many failed.

    Serial because `worktree remove` and `branch -d` take the repository's
    shared refs and its worktrees/ directory. One that git refuses is not a
    reason to abandon the rest.
    """
    main = R.main_worktree(repo)
    failed = 0
    for v in verdicts:
        if v.verdict != GO:
            continue

        # What goes, and what puts it back, before anything is deleted.
        # --quiet and --yes do not silence this.
        say(f"{v.label}  {v.path}")
        restore = restore_line(v.branch, repo=repo)
        if restore:
            say(f"  restore with: {restore}")
        if delete_ignored:
            for path in R.ignored_paths(v.path):
                say(f"  deleting ignored, unrecoverable: {path}")

        try:
            remove_worktree(v.path, repo=repo)
        except (GitError, Refused) as exc:
            say(f"  kept: {exc}")
            failed += 1
            continue

        _prune_empty_parents(Path(v.path).parent, main)

        if v.branch:
            try:
                # -d, so git's own proof of merge decides. A squash-merged
                # branch is refused here and kept: the checkout goes, the
                # branch stays, and the restore line above is not needed.
                delete_branch(v.branch, repo=repo)
            except (GitError, Refused) as exc:
                say(f"  branch {v.branch} kept: {exc}")

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
