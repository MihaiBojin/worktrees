"""Push the branch this checkout is on to the default remote.

A branch the remote lacks is pushed with `--set-upstream`. One it has is
pushed over its remote copy under a lease that names the commit the fetch
just read, and only when two checks pass. The copy may hold commits the
branch lacks by patch-id that this clone never made: another clone's work,
or GitHub's rewrite of a stacked branch. The branch may still carry commits
the head branch already merged, as a stacked branch does after a naive
rebase once its parent was squashed. `--force-if-includes` is not passed:
with an explicit lease git ignores it, and the first check asks the
question it would have asked.
"""

from __future__ import annotations

import itertools
import os
from dataclasses import asdict, dataclass

from . import fast_forward, merged, render, sync
from . import repo as R
from .errors import Refused
from .git import git


@git("push --porcelain --set-upstream $remote $refspec", ok=(0, 1, 128), mutates=True)
def push_upstream(remote: str, refspec: str) -> None:
    """Send a branch the remote lacks, and track it."""


@git(
    "push --porcelain --force-with-lease=$lease $remote $refspec",
    ok=(0, 1, 128),
    mutates=True,
)
def push_over(lease: str, remote: str, refspec: str) -> None:
    """Replace a branch's remote copy, only while it is the commit named."""


@dataclass
class Pushed:
    """What one run sent, or why it sent nothing."""

    branch: str
    remote: str
    pushed: str = ""  # the commit the remote copy is at now; empty when unchanged
    previous: str = ""  # the commit the remote copy was at; empty when absent
    refused: str = ""  # why nothing was pushed

    def data(self) -> dict:
        return asdict(self)


class Rejected(Exception):
    """git refused the push itself, after both checks passed."""

    def __init__(self, result: Pushed) -> None:
        super().__init__(result.refused)
        self.result = result


class Withheld(Exception):
    """A check refused the push; `result` says what was read."""

    def __init__(self, result: Pushed, message: str) -> None:
        super().__init__(message)
        self.result = result


def push(replace: bool = False, repo: str | os.PathLike[str] | None = None) -> Pushed:
    """Push the current branch to the default remote after fetching it.

    `replace` lifts the check on commits only the remote copy holds, for a
    person who has read them.
    """
    if sync.offline():
        raise Refused(f"gw push uses the network, and {sync.OFFLINE} is set")
    branch = R.current_branch(repo=repo).value
    if not branch:
        raise Refused("HEAD is detached, so there is no branch to push")
    remote = R.remote(repo=repo)
    if not remote:
        raise Refused("there is no default remote to push to")
    base = _head_ref(remote, repo)
    if R.ref_name(base).removeprefix(remote + "/") == branch:
        raise Refused(
            f"{render.branch(branch)} is the head branch; gw push sends only a "
            "branch of its own"
        )

    failures, _ = sync.fetch(repo)
    if failures:
        raise Refused("\n".join(failures))
    # The fetch can set the remote's HEAD where the clone had none.
    base = _head_ref(remote, repo)

    local = f"refs/heads/{branch}"
    tip = R.local_sha(local, repo=repo).value
    head = R.local_sha(base, repo=repo).value
    copy = R.local_sha(f"refs/remotes/{remote}/{branch}", repo=repo).value
    result = Pushed(branch, remote, previous=copy)
    if copy == tip:
        return result

    carried = merged.merged_part(tip, head, repo) if head else []
    if carried:
        result.refused = "merged"
        raise Withheld(
            result,
            f"{render.branch(branch)} still carries {_commits(len(carried))} "
            f"{render.ref(R.ref_name(base))} already has, so pushing it would "
            f"put a merged change back in its request:{_listed(carried, repo)}\n"
            "gw fast-forward drops them",
        )

    if not copy:
        sent = push_upstream(remote, f"{local}:{local}", repo=repo)
    else:
        made = made_here(local, head, repo)
        lost = [sha for sha in only_there(copy, tip, head, repo) if sha not in made]
        if lost and not replace:
            result.refused = "remote-only"
            raise Withheld(
                result,
                f"{render.ref(f'{remote}/{branch}')} holds {_commits(len(lost))} "
                f"{render.branch(branch)} lacks, so pushing it would delete "
                f"them:{_listed(lost, repo)}\n"
                "gw fast-forward takes a copy GitHub rewrote, or says what "
                "differs; gw push --replace pushes over them once each one is "
                "checked",
            )
        sent = push_over(f"{local}:{copy}", remote, f"{local}:{local}", repo=repo)
    if not sent:
        result.refused = fast_forward.rejected(sent)
        raise Rejected(result)
    result.pushed = tip
    return result


def _head_ref(remote: str, repo: str | os.PathLike[str] | None) -> str:
    """The full ref the head resolves to, such as `refs/remotes/origin/main`."""
    resolved = R.head(repo)
    if not resolved.ref:
        raise Refused(resolved.unresolved)
    return R.symbolic_ref(resolved.ref, repo=repo).value or resolved.ref


def only_there(
    copy: str, tip: str, head: str, repo: str | os.PathLike[str] | None = None
) -> list[str]:
    """The commits of the remote copy that neither the branch nor the head
    holds, by sha or by patch-id, oldest first.

    A commit of the copy's own merged part counts as held: the head has its
    change, though as a squash no single patch-id matches.
    """
    beyond = fast_forward.commits_beyond(copy, [tip, head], repo=repo).lines
    if not beyond:
        return []
    fork = merged.merge_base(copy, tip, repo=repo).value
    if not fork:
        return beyond
    theirs = {sha: pid for pid, sha in fast_forward.patch_order(fork, copy, repo)}
    known = {pid for pid, _ in fast_forward.patch_order(fork, tip, repo)}
    landed = merged.merge_base(copy, head, repo=repo).value if head else ""
    if landed:
        known |= {pid for pid, _ in fast_forward.patch_order(landed, head, repo)}
    done = set(merged.merged_part(copy, head, repo)) if head else set()
    return [
        sha
        for sha in beyond
        if sha in theirs and sha not in done and theirs[sha] not in known
    ]


def made_here(
    ref: str, head: str, repo: str | os.PathLike[str] | None = None
) -> set[str]:
    """The commits this clone made on the branch `ref`, by its reflog, the
    head's aside.

    Each entry a commit, an amend, a cherry-pick, a revert, `git am` or a
    rebase wrote brings the commits its tip reaches and the entry before it
    does not, outside the head. A pull, a merge, a reset or the branch's
    creation brings commits made elsewhere, and none of those count. A
    commit of its own that a rebase or an amend here rewrote is the clone's
    to replace.
    """
    entries = [line.partition("\0") for line in merged.reflog(ref, repo=repo).lines]
    made: set[str] = set()
    for (sha, _, subject), (before, _, _) in itertools.pairwise(entries):
        if subject.startswith((*merged.MADE, "rebase")):
            made |= set(
                fast_forward.commits_beyond(sha, [before, head], repo=repo).lines
            )
    return made


def _listed(commits: list[str], repo: str | os.PathLike[str] | None) -> str:
    """Each commit as a short sha and its subject, a line each."""
    lines = merged.subjects(commits, repo=repo).lines
    return "".join(
        f"\n  {sha[:7]} {subject}"
        for sha, _, subject in (line.partition("\0") for line in lines)
    )


def _commits(n: int) -> str:
    return f"{n} commit" if n == 1 else f"{n} commits"
