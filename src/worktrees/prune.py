"""Removing the eligible worktrees, and nothing else."""

from __future__ import annotations

import os
import shlex
import sys
from collections.abc import Callable
from pathlib import Path
from typing import NamedTuple

from . import render, sync
from . import repo as R
from .git import GitError, Refused, git
from .verdicts import HEAD_BRANCH, MAIN_OR_MASTER, PROTECTED, Verdict, stashes_of


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


def branch_delete(
    branch: str,
    sha: str,
    head_branch: str,
    repo: str | os.PathLike[str] | None = None,
) -> None:
    """`ref_delete` for a branch, refusing main, master and the head branch.

    Every caller has judged the branch already and found it eligible. This
    check holds even when that judgement was wrong.
    """
    if branch in PROTECTED:
        raise Refused(MAIN_OR_MASTER)
    if branch == head_branch:
        raise Refused(HEAD_BRANCH)
    ref_delete(f"refs/heads/{branch}", sha, repo=repo)


@git("rev-parse --verify $ref", ok=(0, 128))
def sha_of(ref: str) -> None:
    """The sha a ref names, for the line that puts it back."""


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


# The quotes survive shlex.split, so the format stays one argv element.
@git("stash list --format='%gd %H %gs'")
def stash_list() -> None:
    """Every stash: its selector, its sha and its subject."""


@git("stash drop $selector", mutates=True)
def stash_drop(selector: str) -> None:
    """Drop one entry by index. git prints `Dropped stash@{N} (SHA)`."""


@git("stash store -m $subject $sha", mutates=True)
def stash_store(subject: str, sha: str) -> None:
    """Put a dropped entry back at the top of the list."""


@git("log -1 --format=%s $sha")
def subject_of(sha: str) -> None:
    """A commit's subject line."""


def _listed() -> dict[str, tuple[str, str]]:
    """Each stash's sha, mapped to its selector and its subject."""
    found = {}
    for line in stash_list().lines:
        selector, sha, subject = line.split(" ", 2)
        found[sha] = (selector, subject)
    return found


def drop_stashes(stashes: list[R.Stash], say: Callable[[str], None]) -> bool:
    """Drop each of these, by sha, and put back anything dropped by mistake.
    Returns whether none was left behind.

    The stash list is shared by every worktree, so an index read a moment
    ago may name another entry now. Each drop reads the list again, and the
    sha git prints says which entry went. Highest index first, so the
    indexes still to come stay where they were.
    """
    first = _listed()
    order = sorted(
        stashes,
        key=lambda s: int(first[s.sha][0][7:-1]) if s.sha in first else -1,
        reverse=True,
    )
    clean = True
    for stash in order:
        now = _listed()
        if stash.sha not in now:
            say(render.note(f"stash {stash.subject} is already gone"))
            continue
        selector = now[stash.sha][0]
        out = stash_drop(selector).out.strip()
        dropped = out.rpartition("(")[2].rstrip(")")
        if dropped == stash.sha:
            say(render.note(f"dropped stash {stash.subject}"))
            continue
        # Somebody changed the list in between: put that entry back.
        subject = now[dropped][1] if dropped in now else subject_of(dropped).out.strip()
        stash_store(subject, dropped)
        say(
            render.fail(
                f"stash {stash.subject} not dropped: the stash list changed, "
                f"and {dropped} was put back"
            )
        )
        clean = False
    return clean


def stash_restore(stash: R.Stash) -> str:
    """The command that puts a dropped stash back, while `git gc` has not
    pruned its commit."""
    return f"git stash store -m {shlex.quote(stash.subject)} {stash.sha}"


def plan(
    go: list[Verdict],
    say: Callable[[str], None],
    repo: str | os.PathLike[str] | None = None,
    keep_branch: bool = False,
) -> bool:
    """Print what would go, and what puts it back, before anything does.
    Returns whether the repository was never synced, which the plan says
    last and the question asks about.

    --quiet and --yes do not silence this.

    Every ignored path is named, whatever flag got the worktree this far:
    `git worktree remove` takes the whole directory, so --delete ignored-files
    decides consent and never decides what is deleted.

    Under `keep_branch` the branch and its stashes stay, and the plan says
    that instead of how to put them back.
    """
    for v in go:
        say(f"{render.branch(v.label)}  {render.err(v.path, render.DIM)}")
        if keep_branch and v.branch:
            stashes = f" and its {len(v.stashes)} stash(es)" if v.stashes else ""
            say(f"  keeping branch {render.branch(v.branch)}{stashes}")
        elif not keep_branch:
            sha = restore_sha(v.branch, repo=repo)
            if sha:
                restore = render.restore(v.branch, sha)
                say(f"  {render.err('restore with:', render.DIM)} {restore}")
            # An eligible row holds stashes only under --delete stashes.
            for stash in v.stashes:
                lead = f"dropping stash {stash.subject}, restore with:"
                say(f"  {render.err(lead, render.DIM)} {stash_restore(stash)}")
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


class Snapshot(NamedTuple):
    """What a target was when it was assessed. Nothing is removed unless it
    still is."""

    path: str
    branch: str
    sha: str
    head_ref: str
    head_sha: str
    locked: bool
    dirty: bool
    ignored: tuple[str, ...]
    stashes: tuple[str, ...]


# What a second look compares, in order, and what it says when one differs.
CHANGES = (
    ("branch", "it is on another branch now"),
    ("sha", "it has moved to another commit"),
    ("head_ref", "the head ref is another one now"),
    ("head_sha", "the head branch has moved"),
    ("dirty", "its uncommitted changes changed"),
    ("locked", "its lock changed"),
    ("ignored", "its ignored files changed"),
    ("stashes", "its branch's stashes changed"),
)


def look(path: str, repo: str | os.PathLike[str] | None = None) -> Snapshot | None:
    """The worktree at `path` as it is now, or None once git does not list
    it. A locked worktree's directory may be away, and reads as clean."""
    found = next((w for w in R.worktrees(repo) if w.path == path), None)
    if found is None:
        return None
    head = R.head_ref(R.remote(repo), repo=repo)
    sha = sha_of(head.ref, repo=repo) if head.resolves else None
    status = R.status_of(path) if Path(path).is_dir() else R.Status(False, [])
    return Snapshot(
        path,
        found.branch,
        found.sha,
        head.ref,
        sha.out.strip() if sha else "",
        "locked" in found.flags,
        status.dirty,
        tuple(status.ignored),
        tuple(s.sha for s in stashes_of(found.branch, found.sha, R.stashes())),
    )


def record(
    v: Verdict, head_ref: str, repo: str | os.PathLike[str] | None = None
) -> Snapshot | None:
    """The snapshot a removal is checked against, tied to the branch, the
    commit, the head ref and the stashes the verdict was formed from."""
    now = look(v.path, repo=repo)
    return now and now._replace(
        branch=v.branch,
        sha=v.sha,
        head_ref=head_ref,
        stashes=tuple(s.sha for s in v.stashes),
    )


def changed(
    v: Verdict, was: Snapshot | None, now: Snapshot | None, fresh: Verdict | None
) -> str:
    """Why `v` may not be removed after all, or '' when nothing changed.

    `fresh` is the target assessed again, which has to reach the verdict and
    the eligibility the first assessment did.
    """
    if was is None or now is None:
        return "git no longer lists it"
    for field, why in CHANGES:
        if getattr(was, field) != getattr(now, field):
            return why
    if fresh is None or (fresh.verdict, fresh.eligible) != (v.verdict, v.eligible):
        return f"it is not eligible now: {fresh.why if fresh else 'it is gone'}"
    return ""


def remove(
    v: Verdict,
    was: Snapshot | None,
    again: Callable[[str], Verdict | None],
    say: Callable[[str], None],
    head_branch: str,
    keep_branch: bool = False,
    repo: str | os.PathLike[str] | None = None,
) -> tuple[bool, bool]:
    """Look again, remove the worktree, then delete its branch at the sha
    the verdict was formed against. Returns whether the worktree went, and
    whether every step did.

    `again` assesses one path afresh. Each outcome is said on its own line:
    a worktree that went and a branch that stayed are two results.
    """
    why = changed(v, was, look(v.path, repo=repo), again(v.path))
    if why:
        say(render.fail(f"{render.branch(v.label)} kept: {why}; nothing removed"))
        return False, False
    try:
        worktree_remove(v.path, repo=repo)
    except (GitError, Refused) as exc:
        say(render.fail(f"worktree {v.path} kept: {exc}"))
        return False, False
    say(render.note(f"removed worktree {v.path}"))
    _prune_empty_parents(Path(v.path).parent, R.main_worktree(repo))

    if not v.branch:
        return True, True
    if keep_branch:
        say(render.note(f"kept branch {render.branch(v.branch)}"))
        return True, True
    try:
        branch_delete(v.branch, v.sha, head_branch, repo=repo)
    except (GitError, Refused) as exc:
        say(render.fail(f"branch {render.branch(v.branch)} not deleted: {exc}"))
        return True, False
    say(render.note(f"deleted branch {render.branch(v.branch)}"))
    # Only now: a branch that stayed keeps its stashes. An eligible row
    # holds stashes only when --delete stashes was given.
    return True, drop_stashes(v.stashes, say)


def sweep(
    go: list[Verdict],
    recorded: dict[str, Snapshot | None],
    again: Callable[[str], Verdict | None],
    head_branch: str,
    say: Callable[[str], None],
    repo: str | os.PathLike[str] | None = None,
) -> tuple[list[Verdict], int]:
    """Remove each one, in order. Returns the ones whose worktree went, and
    how many failed a step.

    Serial because `worktree remove` and `update-ref` take the repository's
    shared refs and its worktrees/ directory. One that git refuses is not a
    reason to abandon the rest.
    """
    removed, failed = [], 0
    for v in go:
        went, ok = remove(v, recorded.get(v.path), again, say, head_branch, repo=repo)
        if went:
            removed.append(v)
        failed += not ok
    worktree_prune(repo=repo)
    return removed, failed


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
