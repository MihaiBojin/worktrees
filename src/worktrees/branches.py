"""Assess local branches and delete only the refs proved finished."""

from __future__ import annotations

from dataclasses import dataclass, field, fields

from . import merged, progress, prune
from . import repo as R
from .git import GitError, Refused, git
from .verdicts import DONE, KEEP, PERMISSIONS, REMOVE, restore_command


@git("for-each-ref --format='%(refname)%00%(objectname)%00%(symref)' refs/heads/")
def branch_refs() -> None:
    """Local branch names, full object IDs, and symbolic targets."""


@dataclass
class Verdict:
    branch: str
    sha: str
    verdict: str
    # What the completion proof found, or the error, before any protection.
    evidence: str
    proof: str = ""
    paths: list[str] = field(default_factory=list)
    request: dict | None = None
    error: bool = False
    eligible: bool = False
    stashes: list[str] = field(default_factory=list)
    protections: list[str] = field(default_factory=list)
    snapshot: prune.Snapshot | None = None
    permissions: frozenset[str] = frozenset()
    # The fact that decides the row: a ref fact, else its evidence.
    reason: str = merged.UNKNOWN
    # Every --delete value an ineligible row needs; empty when none frees it.
    needs: tuple[str, ...] = ()
    # What the branch's evidence found, before any ref fact decides the row.
    found: tuple[str, str] = (merged.UNKNOWN, "")
    # Commits only the branch's reflog reaches, with their subjects.
    lost: tuple[tuple[str, str], ...] = ()

    @property
    def undo(self) -> str:
        return restore_command(self.branch, self.sha)

    @property
    def reasons(self) -> list[str]:
        """The evidence, then every protection that stands; an error alone."""
        return [self.evidence] if self.error else [self.evidence, *self.protections]

    @property
    def why(self) -> str:
        return "; ".join(self.reasons)

    def data(self) -> dict:
        return {
            **{
                f.name: getattr(self, f.name)
                for f in fields(self)
                if f.name
                not in ("evidence", "snapshot", "permissions", "found", "lost")
            },
            "why": self.why,
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
        row = Verdict(name, sha, KEEP, "no proof that its change is in the head branch")
        row.paths = checked.get(name, [])
        if row.paths and not include_checked:
            continue
        rows.append(row)
        hard = [("checked-out", "checked out in a worktree")] if row.paths else []
        if symbolic:
            hard.append(("symbolic", "it is a symbolic branch ref"))
        try:
            progress.say(f"{name}: checking completion evidence ({i} of {len(wanted)})")
            _judge(
                row,
                hard,
                entries,
                head_sha,
                head_branch,
                remote,
                ask_forge,
                first_parents,
            )
            row.permissions = permissions
            _settle(row, hard, head_branch, permissions)
            row.snapshot = prune.Snapshot(
                state,
                name,
                sha,
                stashes=prune.stash_subjects(row.stashes, entries),
                reflog=row.lost,
            )
        except GitError as exc:
            row.error, row.evidence = True, str(exc)

    return rows


def _judge(
    row: Verdict,
    hard: list[tuple[str, str]],
    entries: list[str],
    head_sha: str,
    head_branch: str,
    remote: str,
    ask_forge: bool,
    first_parents: set[str],
) -> None:
    """Read the branch's evidence, stashes and reflog-only commits into `row`."""
    evidence, why, row.proof, row.request = merged.branch_proof(
        row.branch, row.sha, head_sha, head_branch, remote, ask_forge, first_parents
    )
    if evidence == merged.UNUSED:
        evidence, why = merged.first_parent_reason(row.branch)
    row.found = (evidence, why)
    row.stashes = prune.stashes_on(row.branch, entries, row.sha)
    row.lost = merged.reflog_only(
        merged.reflog_shas(f"refs/heads/{row.branch}"), [], head_sha
    )
    deciding = hard + (
        [("protected", prune.PROTECTED_WHY)]
        if prune.protected(row.branch, head_branch)
        else []
    )
    row.reason, row.evidence = deciding[0] if deciding else (evidence, why)
    row.verdict = REMOVE if row.reason in (merged.MERGED, merged.STALE) else KEEP


def _standing(
    row: Verdict, hard: list[tuple[str, str]], head_branch: str, granted: frozenset[str]
) -> list[prune.Fact]:
    """The protections left once these --delete values are granted."""
    facts = [prune.Fact(code, "", text) for code, text in hard]
    facts += prune.branch_protections(
        row.branch, head_branch, row.found[0], row.stashes, granted
    )
    if row.lost and "reflog" not in granted:
        facts.append(prune.reflog_fact(row.lost))
    return facts


def _settle(
    row: Verdict, hard: list[tuple[str, str]], head_branch: str, granted: frozenset[str]
) -> None:
    """Eligibility, the values that would give it, and the protections left."""

    def eligible(values: frozenset[str]) -> bool:
        return row.found[0] in DONE and not _standing(row, hard, head_branch, values)

    row.eligible = eligible(granted)
    row.needs = () if row.eligible else prune.needed(eligible, PERMISSIONS) or ()
    row.protections = prune.hinted(
        _standing(row, hard, head_branch, granted), row.needs
    )


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
