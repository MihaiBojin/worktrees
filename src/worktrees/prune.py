"""Remove eligible worktrees after assessment and confirmation."""

from __future__ import annotations

import os
import re
import shlex
import sys
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, NamedTuple

if TYPE_CHECKING:
    from . import branches

from . import render
from . import repo as R
from .git import Refused, git
from .verdicts import REMOVE as REMOVE
from .verdicts import Verdict, restore_command


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
    # Every path git lists, which the recheck compares, and the same paths as
    # the ignore patterns name them, which the disclosure shows.
    ignored: tuple[str, ...] = ()
    stashes: tuple[tuple[str, str], ...] = ()
    ignored_groups: tuple[str, ...] = ()
    # Commits only the deleted reflogs reach, with their subjects.
    reflog: tuple[tuple[str, str], ...] = ()


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


def drop_stash(sha: str, repo: str | os.PathLike[str] | None = None) -> bool:
    """Drop the stash entry `sha`; False when the list no longer holds it.

    git drops by position, and another worktree can push or drop between the
    listing and the drop, so the sha git reports dropping is checked. An
    entry dropped in its place is stored back under its subject, and the
    drop is refused.
    """
    entries = [line.split(" ", 2) for line in stash_list(repo=repo).lines]
    found = next((item for item in entries if item[1] == sha), None)
    if found is None:
        return False
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
    return True


def drop_stashes(snapshot: Snapshot, outcome: Outcome, repo=None) -> None:
    wanted = {sha for sha, _ in snapshot.stashes}
    listing = [line.split(" ", 2) for line in stash_list(repo=repo).lines]
    ordered = [sha for _, sha, _ in reversed(listing) if sha in wanted]
    vanished = wanted - set(ordered)
    outcome.skipped.extend(sorted(vanished))
    outcome.retained = [sha for sha in outcome.retained if sha not in vanished]
    for sha in ordered:
        try:
            dropped = drop_stash(sha, repo)
        except Refused:
            current = {line.split(" ", 2)[1] for line in stash_list(repo=repo).lines}
            absent = set(outcome.retained) - current
            outcome.skipped.extend(sorted(absent))
            outcome.retained = [item for item in outcome.retained if item in current]
            raise
        (outcome.dropped if dropped else outcome.skipped).append(sha)
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
    """
    sha = restore_sha(branch, repo=repo)
    return restore_command(branch, sha) if sha else ""


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


class Fact(NamedTuple):
    """Something that holds a row back: its reason code, the --delete value
    that lifts it or '' when none does, and how it reads."""

    code: str
    value: str
    text: str


PROTECTED_WHY = "it is a protected branch"


def protected(branch: str, head_branch: str) -> bool:
    return bool(branch) and branch in ("main", "master", head_branch)


def branch_protections(
    branch: str,
    head_branch: str,
    evidence: str,
    stashes: list[str],
    permissions: frozenset[str] = frozenset(),
    keep_branch: bool = False,
) -> list[Fact]:
    """Branch rules that apply to loose refs and checked-out branches."""
    from . import merged

    if keep_branch and branch:
        return []
    if protected(branch, head_branch):
        return [Fact("protected", "", PROTECTED_WHY)]
    reasons: list[Fact] = []
    if stashes and "stashes" not in permissions:
        reasons.append(Fact("stashes", "stashes", "its branch has stashes"))
    if evidence == merged.UNUSED and "empty" not in permissions:
        reasons.append(Fact(merged.UNUSED, "empty", "the branch is empty"))
    if evidence == merged.CLOSED and "closed" not in permissions:
        reasons.append(Fact(merged.CLOSED, "closed", "its request is closed"))
    return reasons


def reflog_fact(lost: Sequence[tuple[str, str]]) -> Fact:
    """Commits only a deleted reflog reaches, each with the line that shows it."""
    shown = " and ".join(f"git show {sha[:12]}" for sha, _ in lost)
    return Fact(
        "reflog",
        "reflog",
        f"its reflog alone holds {len(lost)} commit(s) the head branch lacks; "
        f"see each with {shown}",
    )


def needed(
    eligible: Callable[[frozenset[str]], bool], values: frozenset[str]
) -> tuple[str, ...] | None:
    """Every --delete value a row needs to be eligible, or None when no set
    of them makes it eligible."""
    if not eligible(values):
        return None
    return tuple(v for v in sorted(values) if not eligible(values - {v}))


def delete_flags(values: Iterable[str], keep_branch: bool = False) -> str:
    """The flags that grant these values, as typed."""
    words = ["--keep-branch"] if keep_branch else []
    return " ".join(words + [f"--delete {value}" for value in sorted(values)])


def hinted(protections: list[Fact], needs: Sequence[str]) -> list[str]:
    """Each protection as it reads, one that a needed value lifts ending with
    every value the row needs, so following that hint alone frees it."""
    hint = f"; pass {delete_flags(needs)}"
    return [f.text + hint if f.value in needs else f.text for f in protections]


def plan(
    go: Sequence[Verdict | branches.Verdict],
    say: Callable[[str], None],
    repo: str | os.PathLike[str] | None = None,
    *,
    keep_branch: bool = False,
) -> None:
    """Disclose the immutable assessed state before confirmation: one line
    for each thing that goes or stays, led by what happens to it."""
    for v in go:
        snapshot = v.snapshot
        if snapshot is None:
            raise Refused("no assessed deletion snapshot")
        for notice in snapshot.head.notices:
            say(render.note(notice))
        say(render.branch(v.branch or "(detached)"))
        if snapshot.path:
            say(_line("delete worktree", render.err(snapshot.path, render.DIM)))
        for path in map(render.printable, snapshot.ignored_groups):
            say(
                render.err(
                    _line("delete ignored", f"{path}, unrecoverable"), render.YELLOW
                )
            )
        if snapshot.ignored_groups:
            # The lines above name an ignored directory once; this lists every
            # file in it, for a person to read before answering.
            listing = shlex.join(
                [
                    "git",
                    "-C",
                    snapshot.path,
                    "ls-files",
                    "--others",
                    "--ignored",
                    "--exclude-standard",
                ]
            )
            say(_line("", f"list every ignored file: {render.command(listing)}"))
        for sha, subject in snapshot.reflog:
            restore = f"git branch rescue-{sha[:7]} {sha}"
            say(_line("delete reflog", f"{sha[:7]} {subject}, recover with: {restore}"))
        if not v.branch:
            continue
        short = render.err(snapshot.sha[:7], render.YELLOW)
        at = f"{render.branch(v.branch)} at {short}"
        if keep_branch:
            kept = ", and its stashes" if snapshot.stashes else ""
            say(_line("keep branch", f"{at}{kept}"))
            continue
        say(
            _line(
                "delete branch",
                f"{at}, restore with: {render.restore(v.branch, snapshot.sha)}",
            )
        )
        for sha, subject in snapshot.stashes:
            restore = f"git stash store -m {shlex.quote(subject)} {sha}"
            say(_line("delete stash", f"{sha[:7]}, restore with: {restore}"))


def _line(label: str, value: str) -> str:
    """A disclosure line, its values in one column under the longest label."""
    return f"  {label:<15}  {value}"


def deletions(
    go: Sequence[Verdict | branches.Verdict], *, keep_branch: bool = False
) -> str:
    """What the disclosure deletes, counted by kind, for the question."""
    snapshots = [v.snapshot for v in go if v.snapshot]
    dropping = [s for s in snapshots if s.branch and not keep_branch]
    counts = (
        (sum(bool(s.path) for s in snapshots), "worktree", "worktrees"),
        (
            sum(len(s.ignored_groups) for s in snapshots),
            "ignored path",
            "ignored paths",
        ),
        (sum(len(s.reflog) for s in snapshots), "reflog commit", "reflog commits"),
        (len(dropping), "branch", "branches"),
        (sum(len(s.stashes) for s in dropping), "stash", "stashes"),
    )
    parts = [f"{n} {one if n == 1 else many}" for n, one, many in counts if n]
    if len(parts) == 1:
        return parts[0]
    return f"{', '.join(parts[:-1])} and {parts[-1]}"


def _prompt(text: str) -> str:
    """The question on stderr, the answer from stdin.

    Not `input`, which puts its prompt on stdout: stdout carries the result
    and nothing else, so `--json` stays parseable in every mode.
    """
    sys.stderr.write(text)
    sys.stderr.flush()
    return sys.stdin.readline()


def confirm(
    what: str, ask: Callable[[str], str] | None = None, *, never: bool = False
) -> bool:
    """Ask before deleting `what`, as `deletions` counts it. Anything but yes
    is no.

    A run whose stdin is not a terminal cannot answer, so it is refused
    rather than left to block: an agent or a pipe reaches this and would
    otherwise hang forever holding the repository's worktrees.
    """
    if ask is None:
        if not sys.stdin.isatty():
            raise Refused(
                f"not a terminal, so nothing can answer for the {what} above; "
                "pass --yes to delete them"
            )
        ask = _prompt
    question = render.ask(
        f"Never synced. Delete {what} anyway?" if never else f"delete {what}?",
        "[y/N]",
    )
    return ask(question).strip().lower() in ("y", "yes")


def never_synced(go: Sequence[Verdict | branches.Verdict]) -> bool:
    from . import sync

    return any(
        v.snapshot and any(n.startswith(sync.NEVER) for n in v.snapshot.head.notices)
        for v in go
    )


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
