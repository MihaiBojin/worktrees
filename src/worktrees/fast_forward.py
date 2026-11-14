"""Bring the branch you stand on level with the head ref.

A branch with commits of its own is rebased onto the head ref, and one
without is fast-forwarded to it. A branch whose change is already in the head
ref, proved the way `gw prune` proves one, keeps its name and its commits, and
the checkout moves to a new branch started from the head ref. A merged
branch's name stays with its merged request on the forge, so the next change
gets a name of its own. A branch whose first commits are in the head ref and
which went on afterwards moves the same way when its own request merged them,
carrying only the commits after the merged part; when another branch's did,
as with a stacked branch whose parent merged, it keeps its name and drops the
merged commits in place. The head branch itself only ever fast-forwards.
"""

from __future__ import annotations

import os
import re
import shlex
import time
from dataclasses import asdict, dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path

from . import branch_forge, forge, merged, prune, render, sync
from . import repo as R
from .errors import GitError
from .errors import Refused as Unsafe
from .git import git
from .new_branch import (
    Refusal,
    check_ref_format,
    refuse_nested,
    short_sha,
    switch_create,
)

CURRENT = "current"
FAST_FORWARDED = "fast-forwarded"
REBASED = "rebased"
ROTATED = "rotated"
CONFLICT = "conflict"
# The move happened, and the uncommitted work it carried did not re-apply.
STASH_CONFLICT = "stash-conflict"

# What a rotation puts in front of the branch's name: the UTC time it ran,
# with `-` where the clock has `:`, which no ref may hold.
PREFIX = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}-\d{2}-\d{2}Z/")

_NAMING = "NAME and --from only name the branch a finished one moves to"

# The subject git gives the stash entry its autostash leaves behind.
_AUTOSTASH = "autostash"

# GitHub rewrites a stacked pull request's branch five or six seconds after
# the one below it merges. A run straight after the merge fetches until the
# rewrite arrives, for this long at most, every this many seconds.
REWRITE_WAIT = 60
REWRITE_INTERVAL = 2


class Unfetched(Refusal):
    """The fetch or the forge query this run decides on did not answer."""


# `autostash` is `--autostash` or `--no-autostash`, whatever merge.autoStash
# and rebase.autoStash say, so uncommitted changes move only when asked to,
# and otherwise stay in the checkout or git refuses.
@git("merge --ff-only $autostash $ref", mutates=True)
def merge_ff_only(autostash: str, ref: str) -> None:
    """Moves a branch with no commits of its own, the head branch included."""


@git("rebase $autostash $onto", ok=(0, 1), mutates=True)
def rebase(autostash: str, onto: str) -> None:
    """Replay the branch's own commits onto `onto`; 1 is a refusal or a stop."""


@git("rebase $autostash --onto $onto $upstream $branch", ok=(0, 1), mutates=True)
def rebase_onto(autostash: str, onto: str, upstream: str, branch: str) -> None:
    """Check out `branch` and replay `upstream..branch` onto `onto`."""


@git("branch --no-track $name $start", mutates=True)
def branch_at(name: str, start: str) -> None:
    """A new branch at `start`, leaving the checkout where it is."""


@git("update-ref -d $ref $sha", mutates=True)
def delete_ref(ref: str, sha: str) -> None:
    """Take back a branch this run made, only while it is still at `sha`."""


@git("--no-optional-locks status --porcelain --untracked-files=no")
def tracked_changes() -> None:
    """Uncommitted changes to tracked files, the ones a rebase refuses."""


@git("log --oneline --no-decorate $range")
def log_oneline(range: str) -> None:
    """The commits in a range, for saying which ones are in the way."""


@git("rev-parse --path-format=absolute --git-path rebase-merge --git-path rebase-apply")
def rebase_dirs() -> None:
    """Where a stopped rebase keeps its state, under either backend."""


@git(
    "rev-parse --path-format=absolute --git-path MERGE_HEAD "
    "--git-path CHERRY_PICK_HEAD --git-path REVERT_HEAD"
)
def operation_heads() -> None:
    """The files a stopped merge, cherry-pick or revert leaves behind."""


@git("rev-parse --verify --quiet ORIG_HEAD^{commit}", ok=(0, 1))
def orig_head() -> None:
    """Where the last rebase, merge or reset found HEAD."""


@git("show --no-patch --format=%h%n%s REBASE_HEAD", ok=(0, 128))
def rebase_head() -> None:
    """The commit a stopped rebase could not replay, then its subject."""


@git("diff --name-only --no-relative --diff-filter=U -z")
def unmerged_paths() -> None:
    """The files left with conflicts, from the top level."""


@git("rev-parse $commit^@")
def parents_of(commit: str) -> None:
    """A commit's parents, first to last."""


# Writes the tree and touches no checkout. --merge-base needs git 2.40.
@git("merge-tree --write-tree --merge-base=$base $onto $commit", ok=(0, 1))
def merge_tree(base: str, onto: str, commit: str) -> None:
    """The tree `commit`'s changes since `base` make on `onto`; 1 is a conflict."""


# No context lines, so a replayed commit keeps its patch-id when the head ref
# edited the lines around its change.
@git(
    "log --no-merges --format=%H -p --no-ext-diff --no-textconv --no-renames "
    "--full-index --binary --no-color --no-relative --ignore-submodules=none "
    "--submodule=short --unified=0 --diff-algorithm=myers --no-indent-heuristic "
    "$base..$tip --",
    env={"GIT_DIFF_OPTS": ""},
)
def bare_patches(base: str, tip: str) -> None:
    """Each commit's patch in `base..tip`, its changed lines alone."""


@git("log --walk-reflogs --format=%H $ref", ok=(0, 128))
def reflog_commits(ref: str) -> None:
    """Every commit the ref's reflog has held, newest first."""


# Each line is a commit, or ^commit for one to stop at; the answer is the
# first commit the plain ones reach and the others do not.
@git("rev-list --stdin --max-count=1")
def first_unreached() -> None:
    """A commit the revisions on standard input leave, or nothing."""


@git("commit-tree --no-gpg-sign -m $message $*parents $tree", mutates=True)
def commit_tree(message: str, parents: list[str], tree: str) -> None:
    """A commit of `tree`, with `-p SHA` in `parents` for each parent."""


@dataclass
class Moved:
    """Where the checkout stands after one run, and how it got there."""

    # current, fast-forwarded, rebased, rotated, conflict or stash-conflict
    result: str
    branch: str  # checked out now; while stopped, the one being rebased
    base: str  # the full ref, or the short commit, it was brought level with
    sha: str  # where HEAD is now
    previous: str = ""  # the branch before the run; another one after a rotation
    previous_sha: str = ""
    ahead: int = 0  # commits of its own on top of `base` after the run
    behind: int = 0  # commits of `base` it lacked before the run
    proof: str = ""  # how a rotated branch, or its merged part, was proved
    why: str = ""
    boundary: str = ""  # the branch's last commit already in `base`
    carried: int = 0  # its commits after `boundary`: replayed, or `adopted`'s copies
    adopted: str = ""  # the remote copy of its commits, made elsewhere, it moved to
    stopped_at: str = ""  # the commit a stopped rebase could not replay
    subject: str = ""
    paths: list[str] = field(default_factory=list)  # files left with conflicts
    stash: str = ""  # the entry holding uncommitted work that did not re-apply
    # A partly merged branch's stashes, each made after `boundary` or not,
    # the copy one made after it moved to, and why one made after it stayed.
    stashes: list[dict] = field(default_factory=list)

    def data(self) -> dict:
        return asdict(self)


def stem(branch: str) -> str:
    """The branch without the prefix a previous rotation gave it.

    So rotating twice gives two siblings rather than one name carrying two
    timestamps.
    """
    return PREFIX.sub("", branch)


def rotation_name(branch: str, now: datetime | None = None) -> str:
    """`YYYY-MM-DDThh-mm-ssZ/<stem>`, at the time given or now."""
    moment = now or datetime.now(UTC)
    return f"{moment:%Y-%m-%dT%H-%M-%SZ}/{stem(branch)}"


def fast_forward(
    name: str = "",
    start: str = "",
    repo: str | os.PathLike[str] | None = None,
    now: datetime | None = None,
    autostash: bool = False,
    live: bool = False,
) -> Moved:
    """Bring the current branch level with the head ref, or rotate a finished
    one onto `name`, started from `start` or the head ref.

    `live` asks the forge, in this run, whether the branch merged; the
    cached evidence is never read. `autostash` carries uncommitted work
    across through git's own autostash.
    """
    stopped = stopped_rebase(repo)
    if stopped:
        return stopped
    left = unapplied(repo)
    if left:
        return left

    branch = R.current_branch(repo=repo).value
    if not branch:
        raise Refusal(
            "HEAD is detached, so there is no branch to bring up to date; "
            "'gw branch create <name>' names one"
        )

    remote = R.remote(repo=repo)
    resolved = R.head(repo)
    if not resolved.ref:
        raise Refusal(resolved.unresolved)
    base = R.symbolic_ref(resolved.ref, repo=repo).value or resolved.ref
    shown = render.ref(R.ref_name(base))
    stash = "--autostash" if autostash else "--no-autostash"

    head_name = R.ref_name(base)
    if remote:
        head_name = head_name.removeprefix(remote + "/")

    if branch == head_name:
        _unnamed(name, start, f"{render.branch(branch)} is the head branch")
        return _catch_up(branch, base, stash, repo=repo)

    local = f"refs/heads/{branch}"
    tip = R.local_sha(local, repo=repo).value
    head_sha = R.local_sha(base, repo=repo).value
    if merged.is_ancestor(base, local, repo=repo):
        _unnamed(name, start, f"{render.branch(branch)} already has {shown}")
        taken = _rewritten(branch, remote, head_sha, head_sha, tip, repo)
        if taken:
            return _replay(branch, base, tip, None, taken, stash, repo)
        sha = _short(local, repo=repo)
        ahead = _count(base, local, repo)
        return Moved(CURRENT, branch, base, sha, branch, sha, ahead=ahead)

    request = merged_request(branch, remote, repo) if live else None
    first_parents = set(merged.first_parent_history(head_sha, repo=repo).lines)
    verdict, why, proof, _ = merged.branch_proof(
        branch,
        tip,
        head_sha,
        resolved.branch,
        remote,
        False,
        first_parents,
        repo,
        live=request if request and request.source_sha == tip else None,
    )
    found = None
    if verdict == "remove":
        found = (tip, proof, why)
    elif verdict == "keep":
        found = (
            _forge_boundary(
                request, branch, tip, head_sha, resolved.branch, first_parents, repo
            )
            or merged.boundary(tip, head_sha, repo)
            or (
                _parent_boundary(
                    branch, tip, head_sha, resolved.branch, first_parents, remote, repo
                )
                if live
                else None
            )
        )
    # A finished branch moves to a new one, and so does a partly merged one
    # whose own request merged, since the name stays with that request. One
    # whose merged part is another branch's, a stacked branch whose parent
    # merged, keeps its name and its open request, and drops the merged
    # commits in place.
    if found and (verdict == "remove" or request is not None):
        point, proof, why = found
        return _rotate(
            branch, base, name, start, proof, why, remote, repo, now, point, stash
        )
    if verdict == "unknown":
        raise Refusal(
            f"cannot tell whether {render.branch(branch)} is finished, so it is "
            f"left as it is: {why}"
        )
    if found:
        _unnamed(
            name,
            start,
            f"{render.branch(branch)} keeps its name, since no merged request "
            f"of its own marks {_short(found[0], repo=repo)}",
        )
    _unnamed(name, start, f"{render.branch(branch)} is not finished: {why}")

    if verdict == "empty":
        previous_sha = _short(local, repo=repo)
        behind = _count(local, base, repo)
        before = _stash_shas(repo)
        merge_ff_only(stash, base, repo=repo)
        moved = Moved(
            FAST_FORWARDED,
            branch,
            base,
            _short(local, repo=repo),
            branch,
            previous_sha,
            behind=behind,
        )
        return _reapplied(moved, before, tip, repo)

    taken = _rewritten(
        branch, remote, head_sha, found[0] if found else head_sha, tip, repo
    )
    if found and live and not taken:
        taken = _awaited(branch, remote, head_sha, found[0], tip, repo)
    return _replay(branch, base, tip, found, taken, stash, repo)


def _replay(
    branch: str,
    base: str,
    tip: str,
    found: tuple[str, str, str] | None,
    taken: tuple[str, str] | None,
    stash: str,
    repo: str | os.PathLike[str] | None,
) -> Moved:
    """Rebase the branch's own commits onto the head ref, the ones after the
    boundary `found` marks when it marks one.

    When `taken` names a remote copy of those commits made elsewhere, the
    branch moves to that copy instead, and only the commits after the one
    the copy's tip matches are replayed, onto it. A copy made before the
    head ref's newest commits is what the branch is then level with.
    """
    local = f"refs/heads/{branch}"
    previous_sha = _short(local, repo=repo)
    before = _stash_shas(repo)
    if taken:
        replayed = rebase_onto(stash, taken[0], taken[1], branch, repo=repo)
    elif found:
        replayed = rebase_onto(stash, base, found[0], branch, repo=repo)
    else:
        replayed = rebase(stash, base, repo=repo)
    if not replayed:
        stopped = stopped_rebase(repo)
        if stopped:
            if found:
                stopped.stashes = _left(_branch_stashes(branch, tip, found[0], repo))
            return stopped
        raise Refusal(replayed.err.strip() or f"git rebase exited {replayed.code}")
    level = base
    if taken and not merged.is_ancestor(base, local, repo=repo):
        level = taken[0]
    moved = Moved(
        REBASED,
        branch,
        level,
        _short(local, repo=repo),
        branch,
        previous_sha,
        ahead=_count(level, local, repo),
        behind=_count(tip, level, repo),
        adopted=taken[0] if taken else "",
    )
    if not found:
        moved = _reapplied(moved, before, tip, repo)
        if taken:
            fork = merged.merge_base(base, tip, repo=repo).out.strip()
            moved.stashes = _move_stashes(
                _branch_stashes(branch, tip, fork, repo),
                branch,
                branch,
                fork,
                tip,
                base,
                repo,
            )
        return moved
    point, moved.proof, moved.why = found
    moved.boundary = _short(point, repo=repo)
    moved.carried = _count(point, tip, repo)
    moved = _reapplied(moved, before, tip, repo)
    moved.stashes = _move_stashes(
        _branch_stashes(branch, tip, point, repo),
        branch,
        branch,
        point,
        tip,
        base,
        repo,
    )
    return moved


def merged_request(
    branch: str, remote: str, repo: str | os.PathLike[str] | None = None
) -> branch_forge.Request | None:
    """The branch's newest merged request, read from the forge now.

    None where the default remote is on no forge this reads, or the forge has
    no merged request from the branch. A forge that cannot be asked is a
    refusal, since the answer this run wants is the one a cache would miss.
    """
    selected = branch_forge.provider(remote, repo) if remote else None
    if selected is None:
        return None
    tool, repository = selected
    if not branch_forge.available(tool) or not forge.authenticated(
        tool, repository.split("/", 1)[0], repo
    ):
        raise Unfetched(
            f"{tool} is unavailable or signed out, so the forge cannot say "
            f"whether {render.branch(branch)} merged"
        )
    answered, request = branch_forge.fetch_request(branch, remote, repo, merged=True)
    if not answered:
        raise Unfetched(
            f"the forge could not be asked whether {render.branch(branch)} merged"
        )
    if request is None or not branch_forge.identified(request, remote, repo):
        return None
    return request


def _forge_boundary(
    request: branch_forge.Request | None,
    branch: str,
    tip: str,
    head: str,
    head_branch: str,
    first_parents: set[str],
    repo: str | os.PathLike[str] | None,
) -> tuple[str, str, str] | None:
    """The commit below the tip a verified merged request names, its proof, why.

    The same checks as a request for the tip, with the source commit one of
    the branch's own rather than its tip.
    """
    if request is None or request.state != "MERGED":
        return None
    if request.source_sha not in merged.own_commits(head, tip, repo=repo).lines[1:]:
        return None
    if request.unverified_reason(
        branch, request.source_sha, head_branch, first_parents
    ):
        return None
    if not merged.tree_of(request.merge_sha, repo=repo) or not merged.is_ancestor(
        request.merge_sha, head, repo=repo
    ):
        return None
    return (
        request.source_sha,
        "forge",
        f"{request.noun} #{request.number} merged its first commits "
        + request.merged_into(head_branch),
    )


def _parent_boundary(
    branch: str,
    tip: str,
    head: str,
    head_branch: str,
    first_parents: set[str],
    remote: str,
    repo: str | os.PathLike[str] | None,
) -> tuple[str, str, str] | None:
    """The newest of the branch's commits another branch's merged request
    carried, its proof and why.

    For a branch stacked on one whose request squash-merged after the parent
    gained commits of its own: the squash holds more than the commits the two
    share, so neither ancestry nor content can mark where the parent's part
    ends. Its request can, from the branch's oldest commit. Only a request
    whose merge reached the head branch, and whose merge commit the fetched
    head ref holds, counts.
    """
    own = merged.own_commits(head, tip, repo=repo).lines
    if not own:
        return None
    answered, carried = branch_forge.carried_by(own[-1], remote, repo)
    if not answered:
        raise Unfetched(
            "the forge could not be asked which request carried the first "
            f"commit of {render.branch(branch)}"
        )
    for request in carried:
        if request.source_branch == branch or not branch_forge.landed(
            request.target_branch, request.merge_sha, head_branch, first_parents
        ):
            continue
        if not merged.tree_of(request.merge_sha, repo=repo) or not merged.is_ancestor(
            request.merge_sha, head, repo=repo
        ):
            continue
        point = next((commit for commit in own if commit in request.commits), "")
        if point:
            return (
                point,
                "forge",
                f"{request.noun} #{request.number} from "
                f"{render.branch(request.source_branch)} merged its commits up to "
                f"{_short(point, repo=repo)}",
            )
    return None


def stopped_rebase(repo: str | os.PathLike[str] | None = None) -> Moved | None:
    """The rebase this checkout is stopped in, if any, read from git's state.

    The files are the ones git's own prompt reads. Each holds one value and
    its newline, which is all that comes off: a branch name may end in a
    character `str.strip` would also take.
    """
    found = [Path(p) for p in rebase_dirs(repo=repo).lines if Path(p).is_dir()]
    if not found:
        return None
    state = found[0]

    def read(name: str) -> str:
        try:
            raw = (state / name).read_bytes()
        except OSError:
            return ""
        return raw.decode("utf-8", "surrogateescape").removesuffix("\n")

    head_name = read("head-name")
    # `detached HEAD` when the rebase started from no branch.
    branch = (
        head_name[len("refs/heads/") :] if head_name.startswith("refs/heads/") else ""
    )
    stopped = rebase_head(repo=repo).lines
    return Moved(
        CONFLICT,
        branch,
        _short(read("onto"), repo=repo),
        _short("HEAD", repo=repo),
        branch,
        _short(read("orig-head"), repo=repo),
        stopped_at=stopped[0] if stopped else "",
        subject=stopped[1] if len(stopped) > 1 else "",
        paths=_unmerged(repo),
    )


def unapplied(repo: str | os.PathLike[str] | None = None) -> Moved | None:
    """Conflicted files with no operation in progress: work an autostash left.

    A rebase finished by `git rebase --continue` re-applies its autostash
    there, after this command has returned. A merge, cherry-pick or revert
    still in progress is git's to report.
    """
    paths = _unmerged(repo)
    if not paths or any(Path(p).exists() for p in operation_heads(repo=repo).lines):
        return None
    branch = R.current_branch(repo=repo).value
    held = orig_head(repo=repo).out.strip()
    entry = next(
        (
            sha
            for sha, subject in _stash_entries(repo)
            if subject == _AUTOSTASH and held and _parent(sha, repo) == held
        ),
        "",
    )
    return Moved(
        STASH_CONFLICT,
        branch,
        "",
        _short("HEAD", repo=repo),
        branch,
        _short(held, repo=repo),
        paths=paths,
        stash=entry,
    )


def start_point(value: str, repo: str | os.PathLike[str] | None = None) -> str:
    """The full ref or short commit `--from` names.

    A branch before a commit, and its full ref rather than the bare name, which
    git would read as a tag first. Only hex is tried as a commit, so nothing
    that starts with `-` reaches git as an option.
    """
    ref = R.full_ref(value, repo=repo)
    if ref and R.ref_exists(ref, repo=repo):
        return ref
    if re.fullmatch(r"[0-9a-f]{4,64}", value):
        commit = R.local_sha(value, repo=repo)
        if commit:
            return _short(commit.value, repo=repo)
    raise Refusal(f"{render.ref(value)} names no branch or commit here")


def _unnamed(name: str, start: str, reason: str) -> None:
    """Refuse a NAME or --from given to a branch that is not rotating."""
    if name or start:
        raise Refusal(f"{reason}; {_NAMING}")


def _rotate(
    branch: str,
    base: str,
    name: str,
    start: str,
    proof: str,
    why: str,
    remote: str,
    repo: str | os.PathLike[str] | None,
    now: datetime | None,
    boundary: str,
    stash: str,
) -> Moved:
    """Leave a finished branch as it is and start the next one.

    `boundary` is the branch's last commit in the head ref: its tip when the
    whole branch is, an earlier commit when the branch went on after its
    merge. The commits after it are replayed onto the new branch.

    Free means free here and on the remote: a name only the remote holds is
    somebody's branch, and pushing this one would land on it.
    """
    chosen = name or rotation_name(branch, now)
    if not check_ref_format(chosen, repo=repo):
        raise Refusal(f"{render.branch(chosen)} is not a valid branch name")
    if R.ref_exists(f"refs/heads/{chosen}", repo=repo):
        raise Refusal(f"{render.branch(chosen)} is already a branch")
    refuse_nested(chosen, repo=repo)
    if remote and R.ref_exists(f"refs/remotes/{remote}/{chosen}", repo=repo):
        raise Refusal(
            f"{render.ref(f'{remote}/{chosen}')} already exists; another NAME "
            "keeps the two apart"
        )
    origin = start_point(start, repo) if start else base
    local = f"refs/heads/{branch}"
    tip = R.local_sha(local, repo=repo).value
    previous_sha = _short(local, repo=repo)
    carried = _count(boundary, tip, repo)
    dirty = bool(tracked_changes(repo=repo).out.strip())
    moved = Moved(
        ROTATED,
        chosen,
        origin,
        "",
        branch,
        previous_sha,
        proof=proof,
        why=why,
        boundary=_short(boundary, repo=repo),
        carried=carried,
    )
    if not carried and (stash == "--no-autostash" or not dirty):
        # git switch carries the uncommitted work it can and refuses the rest.
        switch_create(chosen, origin, repo=repo)
        moved.sha = _short("HEAD", repo=repo)
        moved.stashes = _branch_stashes(branch, tip, boundary, repo)
        return moved
    if dirty and stash == "--no-autostash":
        raise Refusal(
            f"the working tree has uncommitted changes, and the "
            f"{_commits(carried)} of {render.branch(branch)} after {moved.boundary} "
            f"would be replayed onto {render.branch(chosen)}; --autostash carries "
            "the changes across"
        )
    before = _stash_shas(repo)
    branch_at(chosen, tip, repo=repo)
    replayed = rebase_onto(stash, origin, boundary, chosen, repo=repo)
    if not replayed:
        stopped = stopped_rebase(repo)
        if stopped:
            stopped.stashes = _left(_branch_stashes(branch, tip, boundary, repo))
            return stopped
        if R.current_branch(repo=repo).value != chosen:
            delete_ref(f"refs/heads/{chosen}", tip, repo=repo)
        raise Refusal(replayed.err.strip() or f"git rebase exited {replayed.code}")
    moved.sha = _short("HEAD", repo=repo)
    moved.ahead = _count(origin, f"refs/heads/{chosen}", repo)
    moved = _reapplied(moved, before, tip, repo)
    moved.stashes = _move_stashes(
        _branch_stashes(branch, tip, boundary, repo),
        branch,
        chosen,
        boundary,
        tip,
        origin,
        repo,
    )
    return moved


def _catch_up(
    branch: str, base: str, stash: str, repo: str | os.PathLike[str] | None = None
) -> Moved:
    """There is nothing to rebase the head branch onto, so bring it level.

    --ff-only refuses when the local copy is ahead, which is the answer: those
    commits are a change of their own and belong on a branch. The refusal
    gives the two commands that put them on one, in the order that keeps
    them: `gw branch create` would start from the head ref and leave them
    behind. The full ref, because a tag named `origin/main` is what a bare
    `origin/main` means to git.
    """
    local = f"refs/heads/{branch}"
    count = _count(base, local, repo)
    if count:
        listed = log_oneline(f"{base}..{local}", repo=repo)
        lines = "\n".join(f"  {line}" for line in listed.lines) if listed else ""
        raise Refusal(
            f"{render.branch(branch)} is {count} commit(s) ahead of "
            f"{render.ref(R.ref_name(base))}, so it "
            f"cannot be fast-forwarded:\n{lines}\n"
            "Those commits are a change of their own. The first command puts "
            f"them on a branch NAME, and the second moves {branch} back to "
            f"{R.ref_name(base)}:\n"
            "  git switch --create NAME\n"
            f"  git branch --force {shlex.quote(branch)} {shlex.quote(base)}"
        )
    behind = _count(local, base, repo)
    previous_sha = _short(local, repo=repo)
    if not behind:
        return Moved(CURRENT, branch, base, previous_sha, branch, previous_sha)
    tip = R.local_sha(local, repo=repo).value
    before = _stash_shas(repo)
    merge_ff_only(stash, base, repo=repo)
    moved = Moved(
        FAST_FORWARDED,
        branch,
        base,
        _short(local, repo=repo),
        branch,
        previous_sha,
        behind=behind,
    )
    return _reapplied(moved, before, tip, repo)


def _reapplied(
    moved: Moved,
    before: set[str],
    held: str,
    repo: str | os.PathLike[str] | None,
) -> Moved:
    """The move, or a stash-conflict when its autostash did not re-apply.

    git keeps the work as a new `autostash` entry on the stash list every
    worktree shares, made on the commit HEAD held before the run, and exits
    0. That entry is named by its sha, which no other worktree's push or
    drop can renumber.
    """
    entry = next(
        (
            sha
            for sha, subject in _stash_entries(repo)
            if sha not in before
            and subject == _AUTOSTASH
            and _parent(sha, repo) == held
        ),
        "",
    )
    if not entry:
        return moved
    return replace(moved, result=STASH_CONFLICT, stash=entry, paths=_unmerged(repo))


def _branch_stashes(
    branch: str, tip: str, boundary: str, repo: str | os.PathLike[str] | None
) -> list[dict]:
    """The branch's stashes, each made after the boundary or not.

    One made on a commit after the boundary is work done after the merge. One
    made on the boundary or before it may hold work the merge already took.
    """
    entries = prune.stash_list(repo=repo).lines
    shas = prune.stashes_on(branch, entries, tip, repo)
    subjects = dict(prune.stash_subjects(shas, entries))
    found = []
    for sha in shas:
        parent = _parent(sha, repo)
        after = parent != boundary and bool(
            merged.is_ancestor(boundary, parent, repo=repo)
        )
        found.append(
            {
                "sha": sha,
                "subject": subjects.get(sha, ""),
                "after": after,
                "moved": "",
                "kept": "",
            }
        )
    return found


def _left(stashes: list[dict]) -> list[dict]:
    """The stashes of a carry that stopped, which moves none of them."""
    for entry in stashes:
        if entry["after"]:
            entry["kept"] = "the rebase stopped before any stash moved"
    return stashes


def _move_stashes(
    stashes: list[dict],
    branch: str,
    to: str,
    boundary: str,
    tip: str,
    onto: str,
    repo: str | os.PathLike[str] | None,
) -> list[dict]:
    """Move each stash made after the boundary onto the replayed copy of the
    commit it was made on, under `to`, the branch now holding the commits.

    The replayed copy is the one with the same patch-id; a commit the rebase
    dropped, a merge among them, has none, and its stashes stay where they
    are. Oldest first, so the moved ones keep their order.
    """
    if not any(entry["after"] for entry in stashes):
        return stashes
    made_on = {sha: pid for pid, sha in _by_patch_id(boundary, tip, repo).items()}
    replayed = _by_patch_id(onto, f"refs/heads/{to}", repo)
    for entry in reversed(stashes):
        if not entry["after"]:
            continue
        target = replayed.get(made_on.get(_parent(entry["sha"], repo), ""), "")
        if not target:
            entry["kept"] = "no replayed commit matches the one it was made on"
            continue
        try:
            entry["moved"], entry["kept"] = _restash(
                entry["sha"], entry["subject"], target, branch, to, repo
            )
        except GitError as exc:
            # The first line is the command, painted for a terminal; the
            # second is the first thing git said.
            entry["kept"] = f"git could not copy it: {str(exc).splitlines()[1]}"
    return stashes


def _restash(
    sha: str,
    subject: str,
    target: str,
    branch: str,
    to: str,
    repo: str | os.PathLike[str] | None,
) -> tuple[str, str]:
    """Copy stash `sha` onto `target`, then drop it once the copy is listed.

    The copy's index and work tree are the original's changes merged onto
    `target`, and its untracked files are the original's. The copy's sha,
    and why the original is still on the list.
    """
    parents = parents_of(sha, repo=repo).lines
    if len(parents) < 2:
        return "", "git stash did not make it: it has no index commit"
    made_on, index, *untracked = parents
    trees = []
    for commit in (index, sha):
        merge = merge_tree(made_on, target, commit, repo=repo)
        if not merge:
            return "", f"it does not apply cleanly to {_short(target, repo=repo)}"
        trees.append(merge.lines[0])
    index_subject = _renamed(prune.stash_subject(index, repo=repo).value, branch, to)
    copied_index = commit_tree(
        index_subject, ["-p", target], trees[0], repo=repo
    ).out.strip()
    flags = ["-p", target, "-p", copied_index]
    for commit in untracked:
        flags += ["-p", commit]
    copy = commit_tree(
        _renamed(subject, branch, to), flags, trees[1], repo=repo
    ).out.strip()
    prune.stash_store(_renamed(subject, branch, to), copy, repo=repo)
    if copy not in _stash_shas(repo):
        return "", "the copy did not reach the stash list"
    try:
        prune.drop_stash(sha, repo)
    except Unsafe as exc:
        return copy, str(exc)
    return copy, ""


def _renamed(subject: str, branch: str, to: str) -> str:
    """A stash subject naming `to` where it named `branch`."""
    for lead in ("On ", "WIP on ", "index on "):
        if subject.startswith(f"{lead}{branch}:"):
            return f"{lead}{to}:{subject[len(lead) + len(branch) + 1 :]}"
    return subject


def _rewritten(
    branch: str,
    remote: str,
    head: str,
    start: str,
    tip: str,
    repo: str | os.PathLike[str] | None,
) -> tuple[str, str] | None:
    """The default remote's copy of the branch, when it was rewritten
    elsewhere with the branch's own commits, and the commit its tip copies.

    GitHub rewrites a stacked pull request's branch when the one below it
    merges. The copy counts when none of the commits the branch's reflog
    has held reaches it, which is when a push with --force-if-includes
    refuses, and when its commits the head lacks are, in order and by
    patch-id, the branch's first commits after `start`.
    """
    if not remote:
        return None
    copy = f"refs/remotes/{remote}/{branch}"
    copy_tip = R.local_sha(copy, repo=repo).value
    if not copy_tip:
        return None
    held = {tip, *reflog_commits(f"refs/heads/{branch}", repo=repo).lines}
    revisions = "".join(f"{line}\n" for line in [copy_tip, *(f"^{s}" for s in held)])
    if not first_unreached(input=revisions, repo=repo).out.strip():
        return None
    theirs = [pid for pid, _ in _patch_order(head, copy_tip, repo)]
    ours = _patch_order(start, tip, repo)
    if not theirs or [pid for pid, _ in ours[: len(theirs)]] != theirs:
        return None
    return copy, ours[len(theirs) - 1][1]


def _awaited(
    branch: str,
    remote: str,
    head: str,
    point: str,
    tip: str,
    repo: str | os.PathLike[str] | None,
) -> tuple[str, str] | None:
    """The remote copy of a stacked branch, as `_rewritten` takes it, once
    GitHub has rewritten it.

    Only for a branch whose remote copy still holds the merged commits, up to
    `point`, and whose open pull request is in a native stack: replaying the
    commits here would race GitHub's rewrite, and the push after it would be
    refused or would undo it. None when there is nothing to wait for.
    """
    copy = f"refs/remotes/{remote}/{branch}"
    if not remote or not R.local_sha(copy, repo=repo).value:
        return None
    if not merged.is_ancestor(point, copy, repo=repo):
        return None
    answered, number = branch_forge.in_stack(branch, remote, repo)
    if not answered:
        raise Unfetched(
            f"the forge could not be asked whether {render.branch(branch)}'s "
            "pull request is in a stack"
        )
    if not number:
        return None
    deadline = time.monotonic() + REWRITE_WAIT
    while True:
        failures, _ = sync.fetch(repo)
        if failures:
            raise Unfetched("\n".join(failures))
        if not merged.is_ancestor(point, copy, repo=repo):
            return _rewritten(branch, remote, head, point, tip, repo)
        if time.monotonic() + REWRITE_INTERVAL > deadline:
            raise Unfetched(
                f"GitHub has not yet rewritten {render.branch(branch)} for its "
                f"stacked pull request #{number}, so it is left as it is"
            )
        time.sleep(REWRITE_INTERVAL)


def _patch_order(
    base: str, tip: str, repo: str | os.PathLike[str] | None
) -> list[tuple[str, str]]:
    """Each commit in `base..tip`, oldest first, as its patch-id and sha."""
    patches = bare_patches(base, tip, repo=repo).out
    rows = [line.split() for line in merged.patch_ids(input=patches, repo=repo).lines]
    return [(pid, sha) for pid, sha in reversed(rows)]


def _by_patch_id(
    base: str, tip: str, repo: str | os.PathLike[str] | None
) -> dict[str, str]:
    """Each commit in `base..tip` by its patch-id, or '' where two share one."""
    found: dict[str, str] = {}
    for pid, sha in _patch_order(base, tip, repo):
        found[pid] = "" if pid in found else sha
    return found


def _stash_entries(
    repo: str | os.PathLike[str] | None,
) -> list[tuple[str, str]]:
    """Every stash entry's sha and subject, newest first."""
    found = []
    for line in prune.stash_list(repo=repo).lines:
        parts = line.split(" ", 2)
        if len(parts) == 3:
            found.append((parts[1], parts[2]))
    return found


def _stash_shas(repo: str | os.PathLike[str] | None) -> set[str]:
    return {sha for sha, _ in _stash_entries(repo)}


def _parent(sha: str, repo: str | os.PathLike[str] | None) -> str:
    """The commit a stash entry was made on."""
    return prune.sha_of(f"{sha}^1", repo=repo).out.strip()


def _unmerged(repo: str | os.PathLike[str] | None) -> list[str]:
    return [p for p in unmerged_paths(repo=repo).out.split("\0") if p]


def _commits(n: int) -> str:
    return f"{n} commit" if n == 1 else f"{n} commits"


def _short(ref: str, repo: str | os.PathLike[str] | None = None) -> str:
    if not ref:
        return ""
    sha = short_sha(ref, repo=repo)
    return sha.out.strip() if sha else ""


def _count(a: str, b: str, repo: str | os.PathLike[str] | None = None) -> int:
    """Commits reachable from `b` and not from `a`."""
    counted = R.count_between(a, b, repo=repo)
    return int(counted.out.strip() or 0) if counted else 0
