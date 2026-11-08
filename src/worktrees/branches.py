"""Assess local branches and delete only the refs proved finished."""

from __future__ import annotations

from dataclasses import dataclass, field, fields

from . import merged, progress, prune
from . import repo as R
from .git import GitError, Refused, git
from .verdicts import restore_command


@git("for-each-ref --format='%(refname)%00%(objectname)%00%(symref)' refs/heads/")
def branch_refs() -> None:
    """Local branch names, full object IDs, and symbolic targets."""


@dataclass
class Verdict:
    branch: str
    sha: str
    verdict: str
    why: str
    proof: str = ""
    paths: list[str] = field(default_factory=list)
    request: dict | None = None
    error: bool = False
    eligible: bool = False
    stashes: list[str] = field(default_factory=list)
    protections: list[str] = field(default_factory=list)
    snapshot: prune.Snapshot | None = None
    permissions: frozenset[str] = frozenset()

    @property
    def undo(self) -> str:
        return restore_command(self.branch, self.sha)

    def data(self) -> dict:
        return {
            **{
                f.name: getattr(self, f.name)
                for f in fields(self)
                if f.name not in ("snapshot", "permissions")
            },
            "undo": self.undo,
        }


def local_refs() -> dict[str, tuple[str, str]]:
    return {
        ref.removeprefix("refs/heads/"): (sha, symref)
        for ref, sha, symref in (line.split("\0") for line in branch_refs().lines)
    }


def assess(
    names: list[str],
    head_sha: str,
    head_branch: str,
    remote: str,
    ask_forge: bool,
    include_checked: bool = False,
    first_parents: set[str] | None = None,
    permissions: frozenset[str] = frozenset(),
    state: prune.HeadState | None = None,
) -> list[Verdict]:
    refs = local_refs()
    missing = [name for name in names if name not in refs]
    if missing:
        raise ValueError("no local branch: " + ", ".join(missing))
    checked: dict[str, list[str]] = {}
    for wt in R.worktrees():
        if wt.branch:
            checked.setdefault(wt.branch, []).append(wt.path)
    if first_parents is None:
        first_parents = (
            set(merged.first_parent_history(head_sha).lines) if head_sha else set()
        )
    if state is None:
        state = prune.head_state(
            R.head().ref or "", head_branch, sha=head_sha, first_parents=first_parents
        )
    entries = prune.stash_list().lines
    rows = []
    wanted = list(dict.fromkeys(names or refs))
    for i, name in enumerate(wanted, 1):
        sha, symbolic = refs[name]
        row = Verdict(
            name, sha, "unknown", "no proof that its change is in the head branch"
        )
        row.paths = checked.get(name, [])
        rows.append(row)
        if row.paths:
            if include_checked:
                row.why = "checked out in a worktree"
                row.protections.append(row.why)
            else:
                rows.pop()
                continue
        try:
            progress.say(f"{name}: checking completion evidence ({i} of {len(wanted)})")
            row.verdict, row.why, row.proof, row.request = merged.branch_proof(
                name, sha, head_sha, head_branch, remote, ask_forge, first_parents
            )
            row.stashes = prune.stashes_on(name, entries, sha)
            if symbolic:
                row.protections.append("it is a symbolic branch ref")
            row.protections += prune.branch_protections(
                name, head_branch, row.verdict, row.stashes, permissions
            )
            row.permissions = permissions
            row.snapshot = prune.Snapshot(
                state, name, sha, stashes=prune.stash_subjects(row.stashes, entries)
            )
            row.eligible = (
                row.verdict == "remove"
                or (row.verdict == "empty" and "empty" in permissions)
            ) and not row.protections
            if row.protections:
                row.why += "; " + "; ".join(row.protections)
        except GitError as exc:
            row.error, row.why = True, str(exc)

    return rows


def delete(
    row: Verdict,
    head_ref: str,
    head_sha: str,
    head_branch: str,
    remote: str,
    ask_forge: bool,
    first_parents: set[str] | None = None,
) -> prune.Outcome:
    """Recheck the approved ref and stashes, then return every cleanup outcome."""
    snapshot = row.snapshot
    if snapshot is None:
        raise Refused("no assessed deletion snapshot")
    prune.check_head(snapshot.head)
    fresh = assess(
        [row.branch],
        head_sha,
        head_branch,
        remote,
        ask_forge,
        include_checked=True,
        first_parents=set(snapshot.head.first_parents),
        permissions=row.permissions,
        state=snapshot.head,
    )[0]
    if fresh.snapshot != snapshot or not fresh.eligible:
        raise Refused(f"{row.branch} changed after assessment; {fresh.why}")
    prune.check_head(snapshot.head)
    outcome = prune.Outcome(branch=row.branch, retained=list(row.stashes))
    try:
        prune.delete_ref(snapshot, outcome)
    except (GitError, Refused) as exc:
        outcome.failed.append(str(exc))
    return outcome
