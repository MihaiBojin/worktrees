"""What this repository is: its worktrees, its remote, its head branch."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from .git import Refused, git

# --------------------------------------------------------------------------
# the git calls
# --------------------------------------------------------------------------


@git(
    "config --null --get-regexp "
    "'^(extensions[.]partialclone|remote[.].*[.](promisor|partialclonefilter))$'",
    ok=(0, 1),
)
def partial_clone_config() -> None:
    """Promisor settings, including older filter-only configurations."""


@git("--version")
def git_version() -> None:
    """The installed Git release, without reading repository objects."""


def require_offline_git() -> None:
    """Refuse implicit object retrieval on unpatched partial clones."""
    values = {}
    for record in partial_clone_config().out.split("\0"):
        if record:
            key, separator, value = record.partition("\n")
            values[key] = value if separator else None
    partial = any(
        not key.endswith(".promisor")
        or value is None
        or value.lower() not in ("", "false", "no", "off", "0")
        for key, value in values.items()
    )
    if not partial:
        return
    import re

    version = git_version().out.strip()
    match = re.search(r"\b(\d+)\.(\d+)\.(\d+)\b", version)
    if match:
        major, minor, patch = map(int, match.groups())
        # Patch prefetch honors the lazy-fetch guard on these maintenance releases.
        floors = {39: 4, 40: 2, 41: 1, 42: 2, 43: 4, 44: 1}
        if (major, minor, patch) >= (2, 45, 1) or (
            major == 2 and minor in floors and patch >= floors[minor]
        ):
            return
    raise Refused(
        "partial clone requires Git 2.45.1 or a patched maintenance release "
        f"for offline object reads; upgrade Git ({version})"
    )


@git("worktree list --porcelain -z")
def worktree_list() -> None:
    """Every worktree, NUL-delimited."""


@git("for-each-ref --format='%(refname:strip=2)%00%(objectname)' refs/heads/")
def local_tips() -> None:
    """Every local branch and its tip, including checked-out branches."""


@git("remote")
def remotes() -> None:
    """The names of every remote."""


@git("remote -v")
def remote_verbose() -> None:
    """Each remote's fetch and push URL, with url.insteadOf applied."""


def remote_urls(repo: str | os.PathLike[str] | None = None) -> list[tuple[str, str]]:
    """Every remote and where it fetches from, as `short_url` writes it."""
    seen: list[tuple[str, str]] = []
    for line in remote_verbose(repo=repo).lines:
        name, _, rest = line.partition("\t")
        url, _, kind = rest.rpartition(" ")
        if kind == "(fetch)" and url:
            seen.append((name, short_url(url)))
    return seen


def short_url(url: str) -> str:
    """host/owner/repo, whichever way the URL spells it.

    `git@github.com:org/repo.git`, `ssh://git@github.com:22/org/repo` and
    `https://user:token@github.com/org/repo.git` are all
    `github.com/org/repo`. The user, any password or token and the port are
    dropped, which is also what keeps a credential out of the terminal. A
    local path is shown as it is, less a trailing `.git`.
    """
    from urllib.parse import urlsplit

    text = url.strip().rstrip("/").removesuffix(".git")
    if "://" in text:
        parts = urlsplit(text)
        if parts.scheme == "file":
            return parts.path
        return f"{parts.hostname or ''}{parts.path}"
    host, colon, path = text.partition(":")
    # scp-like `[user@]host:path`: a colon before any slash. A Windows drive
    # (`C:\\...`) is one letter, and a local path has its slash first.
    if colon and "/" not in host and len(host) > 1:
        return f"{host.rpartition('@')[2].lower()}/{path.lstrip('/')}"
    return text


@git("config --get $key", ok=(0, 1))
def config_get(key: str) -> None:
    """One config value. Exit 1 means unset, not broken."""


@git("symbolic-ref --quiet $ref", ok=(0, 1, 128))
def symbolic_ref(ref: str) -> None:
    """What a symbolic ref points at."""


@git("symbolic-ref --quiet --short HEAD", ok=(0, 1, 128))
def current_branch() -> None:
    """The checked-out branch, short. Exit 1 on a detached HEAD."""


@git("show-ref --verify --quiet $ref", ok=(0, 1))
def ref_exists(ref: str) -> None:
    """Does this exact ref exist?"""


@git("for-each-ref --count=1 --contains $sha")
def refs_containing(sha: str) -> None:
    """One ref reaching this commit, or nothing."""


# @{upstream} survives the spec: braces are git's revision syntax, not a
# placeholder, which is why placeholders are spelled with $.
@git("rev-parse --abbrev-ref --symbolic-full-name $branch@{upstream}", ok=(0, 1, 128))
def upstream_of(branch: str) -> None:
    """The upstream a branch tracks. Failure means unknown, not none."""


@git("for-each-ref --format=%(upstream:track) refs/heads/$branch")
def upstream_tracking(branch: str) -> None:
    """Tracking status, including [gone] for a missing configured upstream."""


@git("rev-list --count $a..$b", ok=(0, 128))
def count_between(a: str, b: str) -> None:
    """How many commits b has that a does not."""


@git("rev-parse --show-toplevel", ok=(0, 128))
def toplevel() -> None:
    """The root of the worktree we stand in."""


@git("rev-parse --path-format=absolute --git-common-dir", ok=(0, 128))
def common_dir() -> None:
    """The repository directory every worktree of it shares."""


# --no-optional-locks is a git global, so it goes before the subcommand, which
# a spec shows and a tuple hides. Without it a listing writes another
# worktree's index and contends with a `git add` there.
#
# --ignored=traditional collapses an ignored directory into one entry, so
# node_modules/ is one line rather than forty thousand. It is also a strict
# superset of plain --porcelain, which is why there is one spec here and not
# two: asking twice was 18 of this program's 35 calls over nine worktrees.
@git("--no-optional-locks status --porcelain --ignored=traditional")
def status_lines() -> None:
    """Every change in a worktree, the gitignored paths included."""


# 128 is what git exits when it cannot reach the remote, and that is an
# answer rather than a failure: the caller carries on with the refs already
# here and says so. Asserted on what comes back, not on the code, because an
# ok= this wide would otherwise swallow a typo in one of the flags.
@git("fetch --prune $remote", ok=(0, 128), mutates=True)
def fetch(remote: str) -> None:
    """Refresh every remote-tracking ref, dropping the ones that are gone."""


# Sync reads both network calls' results before it records a successful time.
@git("remote set-head $remote --auto", ok=(0, 128), mutates=True)
def set_head_auto(remote: str) -> None:
    """Ask the server which branch it serves by default."""


# --------------------------------------------------------------------------
# worktree records
# --------------------------------------------------------------------------


class Worktree:
    __slots__ = ("branch", "flags", "main", "path", "sha")

    path: str
    sha: str
    branch: str  # empty when detached or bare
    flags: frozenset[str]  # any of bare, detached, locked, prunable
    main: bool  # the original checkout, which git lists first

    def __init__(
        self,
        path: str,
        sha: str,
        branch: str,
        flags: frozenset[str],
        main: bool = False,
    ) -> None:
        self.path = path
        self.sha = sha
        self.branch = branch
        self.flags = flags
        self.main = main

    @property
    def label(self) -> str:
        """What a person calls it, and whether it is the one you can always
        go back to. Two branch names say nothing about which that is.

        The word appears once. A main checkout standing on a branch called
        main is `main`, not `main (main)`, and either way `main` finds it.
        """
        name = self.branch or "(detached)"
        return name if not self.main or name == "main" else f"{name} (main)"


def worktrees(repo: str | os.PathLike[str] | None = None) -> list[Worktree]:
    """Every worktree of this repository: the main checkout, then the rest
    oldest to newest, as `created_at` dates them. git's own order is the
    admin directories' names, which says nothing about when.

    `git worktree list --porcelain -z` NUL-terminates each attribute and ends
    a record with an empty one. Without -z git separates attributes by
    newline, so a directory name holding one parses into two worktrees,
    neither of which exists.
    """
    out = worktree_list(repo=repo).out
    found: list[Worktree] = []
    seen: set[str] = set()
    path = sha = branch = ""
    flags: set[str] = set()

    def emit() -> None:
        nonlocal path, sha, branch, flags
        if path:
            # The same directory under two spellings is still one worktree.
            key = str(Path(path).resolve())
            if key not in seen:
                seen.add(key)
                # git lists the main checkout first, and that ordering is the
                # only thing that says which one it is.
                main = not found
                found.append(Worktree(path, sha, branch, frozenset(flags), main))
        path = sha = branch = ""
        flags = set()

    for attr in out.split("\0"):
        if attr.startswith("worktree "):
            emit()
            path = attr[len("worktree ") :]
        elif attr.startswith("HEAD "):
            sha = attr[len("HEAD ") :]
        elif attr.startswith("branch "):
            branch = attr[len("branch ") :].removeprefix("refs/heads/")
        elif attr in ("bare", "detached"):
            flags.add(attr)
        elif attr.startswith("locked"):
            flags.add("locked")
        elif attr.startswith("prunable"):
            flags.add("prunable")
    emit()
    return sorted(found, key=created_at)


def created_at(wt: Worktree) -> float:
    """When `git worktree add` made it, read off its admin directory.

    git records no date, but `add` writes `commondir` into
    `.git/worktrees/<id>/` once and nothing rewrites it: `move` and `repair`
    touch `gitdir`, a checkout touches `HEAD` and `index`. The worktree's own
    `.git` file says which admin directory is its. The main checkout, and a
    worktree whose directory or admin record is gone, have no date and sort
    as 0 and infinity respectively.
    """
    if wt.main:
        return 0.0
    try:
        pointer = (Path(wt.path) / ".git").read_text().strip()
        admin = Path(pointer.removeprefix("gitdir: "))
        if not admin.is_absolute():
            admin = Path(wt.path) / admin
        return (admin / "commondir").stat().st_mtime
    except OSError:
        return float("inf")


def main_worktree(repo: str | os.PathLike[str] | None = None) -> str:
    """The original checkout, which `worktrees` puts first."""
    records = worktrees(repo)
    return records[0].path if records else ""


# --------------------------------------------------------------------------
# refs
# --------------------------------------------------------------------------


def full_ref(name: str, repo: str | os.PathLike[str] | None = None) -> str:
    """The full ref naming branch `name`.

    A bare name used as a revision is resolved as a tag before a branch, so a
    tag called `origin/main` is what `origin/main` means to merge-base, cherry
    and ^{tree}. Point that tag at a commit containing an unmerged branch and
    the branch reads as merged: deleted, at exit 0, with nothing to say it
    happened.

    Remote-tracking first, because that is what a head branch normally is. A
    name with no remote-tracking ref is spelled under refs/heads/ whether or
    not it exists: a name resolving to nothing beats one resolving to a tag.
    """
    if not name:
        return ""
    if ref_exists(f"refs/remotes/{name}", repo=repo):
        return f"refs/remotes/{name}"
    return f"refs/heads/{name}"


def ref_name(ref: str) -> str:
    """The ref as a person says it."""
    return ref.removeprefix("refs/remotes/").removeprefix("refs/heads/")


def remote(repo: str | os.PathLike[str] | None = None) -> str:
    """The configured default remote, or the sole remote when unset."""
    names = remotes(repo=repo).lines
    stated = config_get("checkout.defaultRemote", repo=repo).out.strip()
    if stated:
        return stated if stated in names else ""
    return names[0] if len(names) == 1 else ""


@git("rev-parse --verify $ref^{commit}", ok=(0, 128))
def local_sha(ref: str) -> None:
    """The commit a local ref identifies, or no answer."""


@git("log -1 --format=%ct $ref", ok=(0, 128))
def commit_date(ref: str) -> None:
    """The tip's committer date, in seconds since the epoch."""


@dataclass
class Head:
    ref: str | None
    source: str
    branch: str
    other: bool = False

    def data(self) -> dict:
        return {"ref": self.ref, "source": self.source}

    @property
    def unresolved(self) -> str:
        return (
            "No main or master branch."
            if self.source == "inferred"
            else "The head ref does not resolve. Run: gw sync"
        )


def head(repo: str | os.PathLike[str] | None = None) -> Head:
    """Resolve the remote HEAD locally, or infer main/master by committer date."""
    from . import sync

    remote_name = remote(repo)
    cache = sync.read(repo)
    remote_head = f"refs/remotes/{remote_name}/HEAD"
    target = symbolic_ref(remote_head, repo=repo).out.strip() if remote_name else ""
    if target or (remote_name and cache["lastSync"]["refs"]):
        source = "remote" if cache["lastSync"]["refs"] else "clone"
        valid = target.startswith(f"refs/remotes/{remote_name}/") and bool(
            ref_exists(target, repo=repo)
        )
        branch = target.removeprefix(f"refs/remotes/{remote_name}/")
        return Head(remote_head if valid else None, source, branch)
    found = [
        name
        for name in ("main", "master")
        if ref_exists(f"refs/heads/{name}", repo=repo)
    ]
    if not found:
        return Head(None, "inferred", "main")
    chosen = max(
        found,
        key=lambda name: int(
            commit_date(f"refs/heads/{name}", repo=repo).out.strip() or 0
        ),
    )
    return Head(f"refs/heads/{chosen}", "inferred", chosen, len(found) == 2)


# --------------------------------------------------------------------------
# per-worktree questions
# --------------------------------------------------------------------------


class Status:
    """What one `git status` says about a worktree.

    Both answers from one call. `dirty` is what makes `git worktree remove`
    refuse; `ignored` is what it deletes at exit 0 without --force and without
    a word, which nothing in git brings back because no ref ever pointed at
    them.
    """

    __slots__ = ("dirty", "ignored")

    dirty: bool
    ignored: list[str]

    def __init__(self, dirty: bool, ignored: list[str]) -> None:
        self.dirty = dirty
        self.ignored = ignored


def status_of(path: str) -> Status:
    """Read a worktree once and answer both questions about it."""
    lines = status_lines(repo=path).lines
    return Status(
        dirty=any(not line.startswith("!! ") for line in lines),
        ignored=[line[3:] for line in lines if line.startswith("!! ")],
    )


def ignored_paths(path: str) -> list[str]:
    """The gitignored paths alone, for a caller that has no verdict in hand."""
    return status_of(path).ignored


def unpushed_count(
    branch: str, repo: str | os.PathLike[str] | None = None
) -> int | None:
    """Commits the upstream has not got, or None when there is no upstream.

    None is not zero. Branches made here do not track, so "no upstream" is the
    normal state, and reporting zero would quietly disarm the guard for
    exactly those.
    """
    up = upstream_of(branch, repo=repo)
    if not up or not up.out.strip():
        return None
    counted = count_between(up.out.strip(), f"refs/heads/{branch}", repo=repo)
    if not counted:
        return None
    return int(counted.out.strip() or 0)
