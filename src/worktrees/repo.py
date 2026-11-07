"""What this repository is: its worktrees, its remote, its head branch."""

from __future__ import annotations

import os
from pathlib import Path
from typing import NamedTuple

from .git import git

# --------------------------------------------------------------------------
# the git calls
# --------------------------------------------------------------------------


@git("worktree list --porcelain -z")
def worktree_list() -> None:
    """Every worktree, NUL-delimited."""


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


@git("cat-file -t $sha", ok=(0, 128))
def object_type(sha: str) -> None:
    """What kind of object a sha is, or nothing when it is not here."""


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


# 128 is what git exits when it cannot reach the remote. gw sync, the only
# caller, reads the result and stops with git's reason; asserted on what
# comes back, not on the code, because an ok= this wide would otherwise
# swallow a typo in one of the flags.
@git("fetch --prune $remote", ok=(0, 128), mutates=True)
def fetch(remote: str) -> None:
    """Refresh every remote-tracking ref, dropping the ones that are gone."""


# The other call that reaches the network, also gw sync's alone. 128 is how
# it says it could not reach the server, and 1 is a server with no branch to
# name.
@git("remote set-head $remote --auto", ok=(0, 1, 128), mutates=True)
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


def ref_name(ref: str) -> str:
    """The ref as a person says it."""
    return ref.removeprefix("refs/remotes/").removeprefix("refs/heads/")


def remote(repo: str | os.PathLike[str] | None = None) -> str:
    """The default remote: `checkout.defaultRemote` when it names a remote,
    otherwise the only remote, otherwise empty.

    Empty covers a repository with no remote and one with several and no
    setting, and every caller treats both alike: nothing is fetched, the
    forge is not asked, and the head branch comes from local branch names.
    """
    names = remotes(repo=repo).lines
    if not names:
        return ""
    if len(names) == 1:
        return names[0]
    stated = config_get("checkout.defaultRemote", repo=repo).out.strip()
    return stated if stated in names else ""


class Head(NamedTuple):
    """The ref everything is measured against, and where it came from.

    `ref` is a full ref, never `origin/main`: every merge question uses it as
    a revision, and a bare name resolves to a tag first. `source` is
    `remote` (the last `gw sync`), `clone` (a `<remote>/HEAD` nothing has
    synced) or `inferred` (from the local `main` and `master`). `also` names
    the conventional branch that lost, when both exist. `remote` is the
    default remote it was looked up for, and `synced` the sync record of it,
    None when there is none to trust.
    """

    ref: str
    source: str
    resolves: bool
    also: str = ""
    remote: str = ""
    synced: dict | None = None

    @property
    def name(self) -> str:
        return ref_name(self.ref)

    @property
    def branch(self) -> str:
        """The head branch's name, `main` for both `refs/heads/main` and
        `refs/remotes/origin/main`."""
        return self.name.removeprefix(f"{self.remote}/") if self.remote else self.name

    def problem(self) -> str:
        if self.source == "inferred":
            return "No main or master branch."
        return f"{self.name} does not resolve; gw sync updates it"


# A pattern to for-each-ref, so `refs/heads/main` also matches
# `refs/heads/main/x`. Asked only of a ref show-ref has found exactly, which
# git then guarantees has nothing beneath it.
@git("for-each-ref --format=%(committerdate:unix) $ref")
def committed_at(ref: str) -> None:
    """When the commit an existing full ref names was committed."""


def head_ref(
    remote_name: str,
    repo: str | os.PathLike[str] | None = None,
) -> Head:
    """The first of: the last sync's `<remote>/HEAD`, a `<remote>/HEAD`
    some clone left, or whichever of `main` and `master` is here.

    A `<remote>/HEAD` pointing at nothing is still the answer, and does not
    resolve: falling through to a local branch would measure against
    something nobody chose.
    """
    from . import sync

    if remote_name:
        symref = symbolic_ref(f"refs/remotes/{remote_name}/HEAD", repo=repo)
        target = symref.out.strip() or f"refs/remotes/{remote_name}/HEAD"
        resolves = bool(ref_exists(target, repo=repo))
        record = sync.read(repo)
        if record:
            return Head(target, "remote", resolves, "", remote_name, record)
        if symref.out.strip():
            return Head(target, "clone", resolves, "", remote_name)

    found = [
        name
        for name in ("main", "master")
        if ref_exists(f"refs/heads/{name}", repo=repo)
    ]
    if not found:
        return Head("refs/heads/main", "inferred", False, "", remote_name)
    if len(found) == 1:
        return Head(f"refs/heads/{found[0]}", "inferred", True, "", remote_name)
    main, master = (
        int(committed_at(f"refs/heads/{name}", repo=repo).out) for name in found
    )
    if master > main:
        return Head("refs/heads/master", "inferred", True, "main", remote_name)
    return Head("refs/heads/main", "inferred", True, "master", remote_name)


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


@git("rev-parse --git-path modules")
def modules_dir() -> None:
    """Where this worktree keeps the git directories of its submodules."""


@git("ls-files --stage -z")
def index_entries() -> None:
    """Every index entry as mode, sha, stage and path, NUL-delimited."""


def has_submodule(path: str) -> bool:
    """Whether `git worktree remove` would refuse this worktree for a
    submodule, asked the way git asks it and without the network.

    git refuses while the worktree's own modules directory exists, which
    `git submodule deinit` leaves behind, or while a submodule's path holds
    a checkout.
    """
    modules = Path(path, modules_dir(repo=path).out.strip())
    if modules.is_dir():
        return True
    for entry in index_entries(repo=path).out.split("\0"):
        meta, _, name = entry.partition("\t")
        if meta.startswith("160000 ") and Path(path, name, ".git").exists():
            return True
    return False
