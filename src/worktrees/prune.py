"""Remove eligible worktrees after assessment and confirmation."""

from __future__ import annotations

import os
import re
import shlex
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from . import branches

from . import render
from . import repo as R
from .git import Refused, git
from .verdicts import REMOVE as REMOVE
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


@git("cat-file -t $sha", ok=(0, 128))
def object_type(sha: str) -> None:
    """What kind of object a sha is. Every printed sha must be a commit."""


# One call for the whole repository, because refs/stash is per-repository
# rather than per-branch. The quotes survive shlex.split, so the format stays
# one argv element.
@git("stash list --format='%gd %H %gs'")
def stash_list() -> None:
    """Every stash entry: its selector, then the subject naming its branch."""


@dataclass(frozen=True)
class HeadState:
    ref: str
    target: str
    sha: str
    branch: str
    first_parents: frozenset[str]
    notices: tuple[str, ...]


def head_state(
    ref: str,
    branch: str,
    repo: str | os.PathLike[str] | None = None,
    *,
    sha: str | None = None,
    first_parents: set[str] | None = None,
) -> HeadState:
    from . import merged, sync

    tip = sha if sha is not None else sha_of(ref, repo=repo).out.strip() if ref else ""
    parents = (
        first_parents
        if first_parents is not None
        else (merged.first_parent_history(tip, repo=repo).lines if tip else [])
    )
    target = R.symbolic_ref(ref, repo=repo).value if ref else ""
    return HeadState(
        ref,
        target or ref,
        tip,
        branch,
        frozenset(parents),
        tuple(sync.notices(True, repo)),
    )


def check_head(state: HeadState, repo=None) -> None:
    current = R.head(repo)
    ref = current.ref or ""
    target = R.symbolic_ref(ref, repo=repo).value if ref else ""
    sha = sha_of(ref, repo=repo).out.strip() if ref else ""
    if (ref, target or ref, sha, current.branch) != (
        state.ref,
        state.target,
        state.sha,
        state.branch,
    ):
        raise Refused("the head branch changed after assessment; run the command again")


@dataclass(frozen=True)
class Snapshot:
    head: HeadState
    branch: str
    sha: str
    path: str = ""
    flags: frozenset[str] = frozenset()
    ignored: tuple[str, ...] = ()
    stashes: tuple[tuple[str, str], ...] = ()


def stash_subjects(shas: list[str], entries: list[str]) -> tuple[tuple[str, str], ...]:
    return tuple(
        sorted(
            (sha, subject)
            for _, sha, subject in (line.split(" ", 2) for line in entries)
            if sha in shas
        )
    )


@dataclass
class Outcome:
    path: str = ""
    branch: str = ""
    worktree_removed: bool = False
    branch_removed: bool = False
    retained: list[str] = field(default_factory=list)
    dropped: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)
    destination: str = ""


@git("stash drop $selector", mutates=True, env={"LC_ALL": "C"})
def stash_drop(selector: str) -> None:
    """Drop a current index and report the SHA Git actually removed."""


@git("stash store -m $subject $sha", mutates=True)
def stash_store(subject: str, sha: str) -> None:
    """Restore a stash with its recorded subject."""


@git("log -1 --format=%s $sha")
def stash_subject(sha: str) -> None:
    """Read the subject of an entry inserted after the last listing."""


def drop_stashes(snapshot: Snapshot, outcome: Outcome, repo=None) -> None:
    wanted = {sha for sha, _ in snapshot.stashes}
    listing = [line.split(" ", 2) for line in stash_list(repo=repo).lines]
    ordered = [sha for _, sha, _ in reversed(listing) if sha in wanted]
    vanished = wanted - set(ordered)
    outcome.skipped.extend(sorted(vanished))
    outcome.retained = [sha for sha in outcome.retained if sha not in vanished]
    for sha in ordered:
        entries = [line.split(" ", 2) for line in stash_list(repo=repo).lines]
        found = next((item for item in entries if item[1] == sha), None)
        if found is None:
            outcome.skipped.append(sha)
            outcome.retained.remove(sha)
            continue
        result = stash_drop(found[0], repo=repo)
        match = re.search(r"Dropped stash@\{\d+\} \(([0-9a-f]+)\)", result.out)
        if not match:
            raise Refused(
                f"stash {sha} not dropped: Git did not identify the dropped entry"
            )
        actual = match.group(1)
        if actual != sha:
            subject = next((item[2] for item in entries if item[1] == actual), None)
            if subject is None:
                subject = stash_subject(actual, repo=repo).value
            stash_store(subject, actual, repo=repo)
            raise Refused(
                f"stash {sha} not dropped: concurrent stash change; restored {actual}"
            )
        outcome.dropped.append(sha)
        outcome.retained.remove(sha)


def delete_ref(snapshot: Snapshot, outcome: Outcome, repo=None) -> None:
    if any(wt.branch == snapshot.branch for wt in R.worktrees(repo)):
        raise Refused(
            f"branch {snapshot.branch} kept: it is checked out in another worktree"
        )
    if R.symbolic_ref(f"refs/heads/{snapshot.branch}", repo=repo).value:
        raise Refused(f"branch {snapshot.branch} kept: it is a symbolic branch ref")
    ref_delete(f"refs/heads/{snapshot.branch}", snapshot.sha, repo=repo)
    outcome.branch_removed = True
    drop_stashes(snapshot, outcome, repo)


def report(outcome: Outcome, say: Callable[[str], None]) -> None:
    if outcome.path:
        state = "removed" if outcome.worktree_removed else "kept"
        say(render.note(f"worktree {outcome.path}: {state}"))
    if outcome.branch:
        state = "deleted" if outcome.branch_removed else "retained"
        say(render.note(f"branch {render.branch(outcome.branch)}: {state}"))
    for sha in outcome.retained:
        say(render.note(f"stash {sha}: retained"))
    for sha in outcome.dropped:
        say(render.note(f"stash {sha}: dropped"))
    for sha in outcome.skipped:
        say(render.note(f"stash {sha}: already absent"))
    for failure in outcome.failed:
        say(render.fail(failure))


def removable(verdicts: list[Verdict]) -> list[Verdict]:
    """Only rows whose proof and protections permit deletion."""
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
    kind = object_type(text, repo=repo)
    if not kind or kind.out.strip() != "commit":
        return ""
    return text


def stashes_on(
    branch: str,
    entries: list[str],
    tip: str = "",
    repo: str | os.PathLike[str] | None = None,
) -> list[str]:
    """Stash SHAs whose branch subject and first parent match this tip."""
    from . import merged

    if not branch:
        return []
    tip = tip or sha_of(f"refs/heads/{branch}", repo=repo).out.strip()
    found = []
    for line in entries:
        parts = line.split(" ", 2)
        if len(parts) != 3:
            continue
        _, sha, subject = parts
        if subject.startswith((f"On {branch}:", f"WIP on {branch}:")):
            parent = sha_of(f"{sha}^1", repo=repo)
            if parent and merged.is_ancestor(parent.out.strip(), tip, repo=repo):
                found.append(sha)
    return found


def branch_protections(
    branch: str,
    head_branch: str,
    verdict: str,
    stashes: list[str],
    permissions: frozenset[str] = frozenset(),
    keep_branch: bool = False,
) -> list[str]:
    """Branch rules that apply to loose refs and checked-out branches."""
    reasons: list[str] = []
    if keep_branch:
        return reasons
    if branch and branch in ("main", "master", head_branch):
        reasons.append("it is a protected branch")
    if stashes and "stashes" not in permissions:
        reasons.append("its branch has stashes")
    if verdict == "empty" and "empty" not in permissions:
        reasons.append("empty branches require deletion permission")
    return reasons


def plan(
    go: Sequence[Verdict | branches.Verdict],
    say: Callable[[str], None],
    repo: str | os.PathLike[str] | None = None,
    *,
    keep_branch: bool = False,
) -> None:
    """Disclose the immutable assessed state before confirmation."""
    for v in go:
        snapshot = v.snapshot
        if snapshot is None:
            raise Refused("no assessed deletion snapshot")
        for notice in snapshot.head.notices:
            say(render.note(notice))
        say(
            f"{render.branch(v.branch or '(detached)')}  "
            f"{render.err(snapshot.path, render.DIM)}"
        )
        if v.branch:
            say(f"  restore with: {render.restore(v.branch, snapshot.sha)}")
            if keep_branch:
                say("  branch and its stashes retained")
        for sha, subject in snapshot.stashes:
            say(f"  stash {sha}: git stash store -m {shlex.quote(subject)} {sha}")
        for path in snapshot.ignored:
            say(render.err(f"  deleting ignored, unrecoverable: {path}", render.YELLOW))


def _prompt(text: str) -> str:
    """The question on stderr, the answer from stdin.

    Not `input`, which puts its prompt on stdout: stdout carries the result
    and nothing else, so `--json` stays parseable in every mode.
    """
    sys.stderr.write(text)
    sys.stderr.flush()
    return sys.stdin.readline()


def confirm(
    count: int, ask: Callable[[str], str] | None = None, *, never: bool = False
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
        "Never synced. Delete anyway?" if never else f"remove {count} worktree(s)?",
        "[y/N]",
    )
    return ask(question).strip().lower() in ("y", "yes")


def never_synced(go: Sequence[Verdict | branches.Verdict]) -> bool:
    from . import sync

    return any(v.snapshot and sync.NEVER in v.snapshot.head.notices for v in go)


def sweep(go: list[Verdict], say: Callable[[str], None], repo=None) -> list[Outcome]:
    """Remove independently approved targets and retain each step's outcome."""
    from . import worktree

    outcomes = [worktree.remove(v, say, repo=repo) for v in go]
    worktree_prune(repo=repo)
    return outcomes


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
