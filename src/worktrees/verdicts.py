"""Completion evidence and deletion protections for every worktree."""

from __future__ import annotations

import os
import shlex
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .prune import HeadState, Snapshot

from . import merged, progress
from . import repo as R
from .git import git

REMOVE = "remove"
KEEP = "keep"

# The checkout facts that decide a row before its branch does, in the order
# the first one found is its reason.
MAIN = "main-checkout"
CURRENT = "current"
LOCKED = "locked"
MISSING = "missing"
SUBMODULE = "submodule"
IN_PROGRESS = "in-progress"
UNCOMMITTED = "uncommitted"
PROTECTED = "protected"

# What the evidence leaves open; no --delete value settles it.
UNFINISHED = frozenset((merged.OPEN, merged.PUSHED, merged.UNPUSHED, merged.UNKNOWN))
# What lets a row go once its permission, if it has one, is given.
DONE = frozenset((merged.MERGED, merged.STALE, merged.UNUSED, merged.CLOSED))

# Every --delete value, and what `--delete all` grants.
PERMISSIONS = frozenset(("empty", "stashes", "closed", "reflog"))

DIRTY = "it has uncommitted changes or untracked files"

# Files in a worktree's git directory while an operation waits for it.
OPERATIONS = (
    ("rebase-merge", "a rebase"),
    ("rebase-apply", "a rebase or am"),
    ("MERGE_HEAD", "a merge"),
    ("CHERRY_PICK_HEAD", "a cherry-pick"),
    ("REVERT_HEAD", "a revert"),
    ("BISECT_LOG", "a bisect"),
)


def restore_command(branch: str, sha: str) -> str:
    """`git branch NAME SHA`, the line that puts a deleted branch back.

    The name is quoted, because this line exists to be pasted and git permits
    `$`, a backtick and `;` in a branch name. `shlex.quote` writes POSIX
    single quotes, which bash, zsh and fish all read the same way.
    """
    return f"git branch {shlex.quote(branch)} {sha}"


class Verdict:
    def __init__(
        self,
        verdict: str,
        branch: str,
        path: str,
        why: str,
        ignored_paths: tuple[str, ...] = (),
        sha: str = "",
        *,
        proof: str = "",
        request: dict | None = None,
        protections: list[str] | None = None,
        stashes: list[str] | None = None,
        reason: str = "",
    ) -> None:
        self.snapshot: Snapshot | None = None
        # The fact that decides the row: a checkout fact, else its evidence.
        self.reason = reason
        self.permissions: frozenset[str] = frozenset()
        self.keep_branch = False
        self.verdict = verdict
        self.branch = branch
        self.path = path
        # What the completion proof found, before any protection.
        self.evidence = why
        # As the ignore patterns name them, which is how people read them.
        self.ignored_paths = ignored_paths
        self.sha = sha
        self.proof = proof
        self.request = request
        self.protections = protections or []
        self.stashes = stashes or []
        self.eligible = False
        # Every --delete value an ineligible row needs; empty when no value
        # would make it eligible.
        self.needs: tuple[str, ...] = ()
        # The --delete values that make an ineligible row eligible beside
        # --keep-branch, when no --delete value alone does; None when
        # --keep-branch would not.
        self.keep_needs: tuple[str, ...] | None = None

    @property
    def label(self) -> str:
        return self.branch or "(detached)"

    @property
    def reasons(self) -> list[str]:
        """The evidence, then every protection that stands.

        A dirty worktree's evidence is its protection, said once.
        """
        return list(dict.fromkeys([self.evidence, *self.protections]))

    @property
    def why(self) -> str:
        return "; ".join(self.reasons)

    @property
    def held_back(self) -> list[str]:
        """Every reason this row is not eligible, and none when it is.

        The evidence is a reason only when it proves nothing finished, and
        not under --keep-branch, which waives proof for a checkout on a branch.
        """
        if self.eligible:
            return []
        waived = self.keep_branch and bool(self.branch)
        unproved = self.reason in UNFINISHED and not waived
        return list(
            dict.fromkeys(([self.evidence] if unproved else []) + self.protections)
        )

    @property
    def ignored(self) -> int:
        return len(self.ignored_paths)

    @property
    def undo(self) -> str | None:
        """None on a detached worktree, which has no branch to put back."""
        return restore_command(self.branch, self.sha) if self.branch else None


def stale(records: list[R.Worktree]) -> list[R.Worktree]:
    """Records for worktree directories somebody deleted by hand."""
    return [w for w in records if "prunable" in w.flags]


@git("submodule status")
def submodule_status() -> None:
    """Initialized submodules have a status prefix other than '-'."""


@git("rev-parse --path-format=absolute --git-path modules")
def modules_dir() -> None:
    """Where this worktree keeps its submodules' git directories."""


@git("rev-parse --path-format=absolute --git-dir")
def git_dir() -> None:
    """This worktree's own git directory."""


def in_progress(path: str) -> str:
    """The operation waiting in this worktree, or ''."""
    own = Path(git_dir(repo=path).value)
    return next((what for name, what in OPERATIONS if (own / name).exists()), "")


def submodule_protection(path: str) -> str:
    """Why `git worktree remove` would refuse this worktree, if it would.

    git refuses while a submodule is initialized, and also while the
    worktree's own modules directory exists, which `deinit` leaves behind.
    """
    initialized = any(
        line and not line.startswith("-") for line in submodule_status(repo=path).lines
    )
    store = modules_dir(repo=path).value
    if not initialized and not Path(store).exists():
        return ""
    return (
        "it has an initialized submodule or a submodule store; run "
        f"git submodule deinit --all in it, then delete {store} once nothing "
        "in it is needed, since it holds the submodules' own commits, and retry"
    )


def assess(
    records: list[R.Worktree],
    only: str,
    head: str,
    head_branch: str,
    repo: str | os.PathLike[str] | None = None,
    here: str | None = None,
    ask_forge: bool = False,
    include_main: bool = False,
    permissions: frozenset[str] = frozenset(),
    keep_branch: bool = False,
    state: HeadState | None = None,
) -> list[Verdict]:
    """Assess pinned tips and keep every deletion protection separate.

    `here` blocks the caller's worktree for pruning. A targeted removal
    passes an empty string and handles departure from that directory itself.
    """
    from . import prune

    main = R.main_worktree(repo)
    if here is None:
        here = R.toplevel(repo=repo).value
    state = state or prune.head_state(head, head_branch, repo)
    head_sha = state.sha
    first_parents = set(state.first_parents)
    remote = R.remote(repo)
    entries = prune.stash_list(repo=repo).lines

    def judge(wt: R.Worktree) -> Verdict | None:
        if "bare" in wt.flags or "prunable" in wt.flags:
            return None
        if only and wt.branch != only:
            return None
        if wt.path == main and not include_main:
            return None
        progress.say(f"{wt.label}: checking completion evidence")
        evidence, why, proof, request = merged.branch_proof(
            wt.branch,
            wt.sha,
            head_sha,
            head_branch,
            remote,
            ask_forge,
            first_parents,
            repo,
        )
        if evidence == merged.UNUSED:
            evidence, why = merged.first_parent_reason(wt.branch, repo)
        # The checkout facts no --delete value lifts, in reason order.
        hard: list[tuple[str, str]] = []
        if wt.path == main:
            hard.append((MAIN, "it is the main checkout, and never removable"))
        if here and Path(wt.path).resolve() == Path(here).resolve() and wt.path != main:
            hard.append((CURRENT, "you are standing in it"))
        if "locked" in wt.flags:
            from . import session

            hard.append(
                (
                    LOCKED,
                    "it is locked by an agent session that has ended; "
                    f"git worktree unlock -- {shlex.quote(wt.path)} releases it"
                    if session.ended(wt.lock)
                    else "it is locked",
                )
            )
        ignored_paths: tuple[str, ...] = ()
        ignored_groups: tuple[str, ...] = ()
        reflogs: dict[bool, tuple[tuple[str, str], ...]] = {}
        if wt.path != main and not Path(wt.path).is_dir():
            # A locked worktree keeps its record while its directory is away,
            # on a removable disk say. git refuses to remove it until it is
            # unlocked, so there is nothing here to read and nothing to delete.
            hard.append((MISSING, "its directory is missing"))
        elif wt.path != main:
            progress.say(f"{wt.label}: reading its status")
            status = R.status_of(wt.path)
            submodule = submodule_protection(wt.path)
            if submodule:
                hard.append((SUBMODULE, submodule))
            operation = in_progress(wt.path)
            if operation:
                hard.append((IN_PROGRESS, f"{operation} is in progress in it"))
            if status.dirty:
                hard.append((UNCOMMITTED, DIRTY))
            ignored_paths = tuple(status.ignored)
            ignored_groups = tuple(R.ignored_groups(wt.path, status.ignored))
            own = merged.reflog_shas("HEAD", wt.path)
            branch = (
                merged.reflog_shas(f"refs/heads/{wt.branch}", repo) if wt.branch else []
            )
            upstream: dict[str, set[str]] = {}
            # Removal deletes the worktree's HEAD reflog, and the branch's
            # unless --keep-branch keeps the branch.
            reflogs = {
                False: merged.reflog_only(own + branch, [], head_sha, repo, upstream),
                True: merged.reflog_only(own, branch, head_sha, repo, upstream),
            }
        stashes = prune.stashes_on(wt.branch, entries, wt.sha, repo)
        deciding = hard + (
            [(PROTECTED, prune.PROTECTED_WHY)]
            if prune.protected(wt.branch, head_branch)
            else []
        )
        reason, decided = deciding[0] if deciding else (evidence, why)

        def standing(
            granted: frozenset[str], keep: bool = keep_branch
        ) -> list[prune.Fact]:
            """The protections left once these --delete values are granted."""
            facts = [prune.Fact(code, "", text) for code, text in hard]
            # With a checkout fact deciding the row, what its commits prove
            # is beside the point, and saying "empty" of a dirty one misleads.
            facts += [
                fact
                for fact in prune.branch_protections(
                    wt.branch, head_branch, evidence, stashes, granted, keep
                )
                if not hard or fact.code not in (merged.UNUSED, merged.CLOSED)
            ]
            lost = reflogs.get(keep and bool(wt.branch), ())
            if lost and "reflog" not in granted:
                facts.append(prune.reflog_fact(lost))
            return facts

        def eligible(granted: frozenset[str], keep: bool = keep_branch) -> bool:
            # --keep-branch waives completion proof because the branch keeps
            # the commits. A detached worktree has no branch to keep them.
            return not standing(granted, keep) and (
                (keep and bool(wt.branch)) or evidence in DONE
            )

        granted = permissions
        needs: tuple[str, ...] = ()
        keep_needs = None
        if not eligible(granted):
            needs = prune.needed(eligible, PERMISSIONS) or ()
            if not needs and not keep_branch:
                keep_needs = prune.needed(
                    lambda values: eligible(values, keep=True), PERMISSIONS
                )
        row = Verdict(
            REMOVE if reason in (merged.MERGED, merged.STALE) else KEEP,
            wt.branch,
            wt.path,
            decided,
            ignored_groups,
            wt.sha,
            proof=proof,
            request=request,
            protections=prune.hinted(standing(granted), needs),
            stashes=stashes,
            reason=reason,
        )
        row.permissions = permissions
        row.keep_branch = keep_branch
        row.eligible = eligible(granted)
        row.needs = needs
        row.keep_needs = keep_needs
        row.snapshot = prune.Snapshot(
            state,
            wt.branch,
            wt.sha,
            wt.path,
            wt.flags,
            ignored_paths,
            prune.stash_subjects(stashes, entries),
            ignored_groups,
            reflogs.get(keep_branch and bool(wt.branch), ()),
        )
        return row

    with ThreadPoolExecutor(max_workers=8) as pool:
        return [v for v in pool.map(judge, records) if v is not None]
