"""What this repository is: its worktrees, its remote, its head branch."""

from __future__ import annotations

import os
from pathlib import Path

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


@git("for-each-ref --count=1 --format=%(refname) $prefix")
def first_ref_under(prefix: str) -> None:
    """One ref under a prefix, or nothing."""


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


def fetched_at(repo: str | os.PathLike[str] | None = None) -> float | None:
    """When any worktree of this repository last fetched, or None.

    FETCH_HEAD is written per worktree, so a fetch from a neighbour counts
    only when every worktree's copy is read.
    """
    common = Path(common_dir(repo=repo).out.strip())
    heads = [common / "FETCH_HEAD", *common.glob("worktrees/*/FETCH_HEAD")]
    return max((p.stat().st_mtime for p in heads if p.exists()), default=None)


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


# The other call that reaches the network. 128 is how it says it could not
# reach the server, and 1 is a server with no branch to name. head_ref and
# gw sync read the result rather than assuming it worked.
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


def head_ref(
    remote_name: str,
    online: bool,
    repo: str | os.PathLike[str] | None = None,
) -> tuple[str, str]:
    """The full ref this repository branches from, and a warning or ''.

    Returns a full ref, never `origin/main`: every merge question below uses
    it as a revision, and a bare name resolves to a tag first.
    """
    if remote_name:
        # 1. The repository's own answer, recorded at clone time from what the
        #    server advertises, so it tells master from main without guessing.
        #    Ignored when it dangles, which is what a server-side rename
        #    leaves behind.
        symref = symbolic_ref(f"refs/remotes/{remote_name}/HEAD", repo=repo).out.strip()
        if symref and ref_exists(symref, repo=repo):
            return symref, ""

        # set-head can only point at a remote-tracking ref, so don't spend a
        # round trip when the repository has none.
        tracking = first_ref_under(f"refs/remotes/{remote_name}", repo=repo).out.strip()
        if online and tracking and set_head_auto(remote_name, repo=repo):
            symref = symbolic_ref(
                f"refs/remotes/{remote_name}/HEAD", repo=repo
            ).out.strip()
            if symref and ref_exists(symref, repo=repo):
                return symref, ""

    # 2. Conventional names, decisive only when exactly one exists.
    #    Remote-tracking first: a repository can have a remote and no
    #    refs/remotes at all, because a remote added by hand was never fetched.
    found: list[str] = []
    if remote_name:
        found = [
            f"{remote_name}/{c}"
            for c in ("main", "master", "trunk")
            if ref_exists(f"refs/remotes/{remote_name}/{c}", repo=repo)
        ]
    if not found:
        found = [
            c
            for c in ("main", "master", "trunk")
            if ref_exists(f"refs/heads/{c}", repo=repo)
        ]
    if not found:
        return "", ""

    # 3. Guess, and be honest that it is one.
    warning = ""
    if len(found) > 1:
        warning = (
            f"{remote_name}/HEAD is unset and " if remote_name else ""
        ) + f"{', '.join(found)} all exist, guessing {found[0]}"
        if remote_name:
            warning += f"; settle it with: git remote set-head {remote_name} --auto"
    return full_ref(found[0], repo=repo), warning


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
