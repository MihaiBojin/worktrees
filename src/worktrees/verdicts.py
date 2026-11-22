"""Completion evidence and deletion protections for every worktree."""

from __future__ import annotations

import os
import shlex
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from . import prune
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

# Files in a worktree's git directory while an operation waits for it.
OPERATIONS = (
    ("rebase-merge", "a rebase"),
    ("rebase-apply", "a rebase or am"),
    ("MERGE_HEAD", "a merge"),
    ("CHERRY_PICK_HEAD", "a cherry-pick"),
    ("REVERT_HEAD", "a revert"),
    ("BISECT_LOG", "a bisect"),
)


def many(n: int, noun: str) -> str:
    """`1 file`, `2 files`."""
    return f"{n} {noun}{'' if n == 1 else 's'}"


def dirty(status: R.Status) -> str:
    """What `git status` holds that a commit does not, as it found it."""
    parts = []
    if status.changed:
        parts.append(f"uncommitted changes to {many(status.changed, 'file')}")
    if status.untracked:
        parts.append(many(status.untracked, "untracked path"))
    return "it has " + " and ".join(parts)


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
        # The same protections as facts, for a table to lay out.
        self.facts: list[prune.Fact] = []
        self.stashes = stashes or []
        self.eligible = False
        # Every --delete value an ineligible row needs; empty when no value
        # would make it eligible.
        self.needs: tuple[str, ...] = ()
        # The --delete values that make an ineligible row eligible beside
        # --keep-branch, when no --delete value alone does; None when
        # --keep-branch would not.
        self.keep_needs: tuple[str, ...] | None = None
        # Commits only the worktree's own HEAD reflog holds, which removing
        # the checkout deletes even when the branch stays.
        self.checkout_reflog: tuple[tuple[str, str], ...] = ()

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


@git("rev-parse --path-format=absolute --git-dir --git-path modules")
def own_dirs() -> None:
    """This worktree's own git directory, then where it keeps its submodules'."""


@git("rev-parse --path-format=absolute --git-path modules")
def modules_dir() -> None:
    """Where this worktree keeps its submodules' git directories."""


@git("rev-parse --path-format=absolute --git-dir")
def git_dir() -> None:
    """This worktree's own git directory."""


def git_dirs(path: str) -> tuple[str, str]:
    """The worktree's git directory and its submodule store, from one call.

    rev-parse ends each path with a newline and has no `-z` for these, so a
    path that holds one cannot be told apart from two. Then each is asked
    alone, which is what reading one value at a time always did.
    """
    answer = own_dirs(repo=path).out.removesuffix("\n").split("\n")
    if len(answer) == 2:
        return answer[0], answer[1]
    return git_dir(repo=path).value, modules_dir(repo=path).value


def in_progress(own: str) -> str:
    """The operation waiting in the worktree whose git directory is `own`, or ''."""
    return next((what for name, what in OPERATIONS if (Path(own) / name).exists()), "")


def submodule_protection(path: str, store: str) -> str:
    """Why `git worktree remove` would refuse this worktree, if it would.

    git refuses while a submodule is initialized, and also while the
    worktree's own modules directory `store` exists, which `deinit` leaves
    behind.

    The store decides it alone when it is there, so `git submodule status`
    runs only without one. That is also the case it can answer: once
    `.gitmodules` is deleted it exits 128 with "no submodule mapping found",
    while the store of the submodule it described is still in place.

    It costs more than every other check on a worktree put together, so it
    runs only where `.gitmodules` says a submodule can be initialized. git
    initializes one from that file and keeps its git directory in the store.
    With neither there, the one case left is a repository cloned by hand
    into a gitlink's path, and `git worktree remove`, which runs without
    `--force`, refuses that worktree itself.
    """
    if not Path(store).exists():
        if not (Path(path) / ".gitmodules").exists():
            return ""
        if not any(
            line and not line.startswith("-")
            for line in submodule_status(repo=path).lines
        ):
            return ""
    return (
        "it has an initialized submodule or a submodule store; run "
        f"git submodule deinit --all in it, then delete {store} once nothing "
        "in it is needed, since it holds the submodules' own commits, and retry"
    )


@merged.reusing
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
            hard.append((LOCKED, "it is locked"))
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
            gitdir, store = git_dirs(wt.path)
            submodule = submodule_protection(wt.path, store)
            if submodule:
                hard.append((SUBMODULE, submodule))
            operation = in_progress(gitdir)
            if operation:
                hard.append((IN_PROGRESS, f"{operation} is in progress in it"))
            if status.dirty:
                hard.append((UNCOMMITTED, dirty(status)))
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
            REMOVE
            if eligible(granted) or reason in (merged.MERGED, merged.STALE)
            else KEEP,
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
        row.facts = standing(granted)
        row.permissions = permissions
        row.keep_branch = keep_branch
        row.eligible = eligible(granted)
        row.needs = needs
        row.keep_needs = keep_needs
        row.checkout_reflog = reflogs.get(True, ())
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

    # Each check is a git process and the work is the CPU's, so a thread per
    # core: more only queue behind each other and the interpreter lock.
    workers = min(8, os.cpu_count() or 1)
    with ThreadPoolExecutor(max_workers=workers) as pool:
        return [v for v in pool.map(judge, records) if v is not None]
