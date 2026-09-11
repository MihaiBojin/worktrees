"""What this repository is: its worktrees, its remote, its head branch."""

from __future__ import annotations

import os
from dataclasses import dataclass
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


# @{upstream} survives the spec: braces are git's revision syntax, not a
# placeholder, which is why placeholders are spelled with $.
@git("rev-parse --abbrev-ref --symbolic-full-name $branch@{upstream}", ok=(0, 1, 128))
def upstream_of(branch: str) -> None:
    """The upstream a branch tracks. Failure means unknown, not none."""


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
@git("--no-optional-locks status --porcelain")
def status_porcelain() -> None:
    """Tracked and untracked changes, one line each."""


# --ignored=traditional collapses an ignored directory into one entry, so
# node_modules/ is one line rather than forty thousand.
@git("--no-optional-locks status --porcelain --ignored=traditional")
def status_with_ignored() -> None:
    """The same, plus the gitignored paths git otherwise never mentions."""


@git("fetch --prune $remote", mutates=True)
def fetch(remote: str) -> None:
    """Refresh every remote-tracking ref, dropping the ones that are gone."""


@git("remote set-head $remote --auto", mutates=True)
def set_head_auto(remote: str) -> None:
    """Ask the server which branch it serves by default."""


# --------------------------------------------------------------------------
# worktree records
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Worktree:
    path: str
    sha: str
    branch: str  # empty when detached or bare
    flags: frozenset[str]  # any of bare, detached, locked, prunable
    main: bool = False  # the original checkout, which git lists first

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
    """Every worktree of this repository, main checkout first.

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
    return found


def main_worktree(repo: str | os.PathLike[str] | None = None) -> str:
    """The original checkout. git lists it first."""
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
    """The remote this repository belongs to, or empty when it has none.

    `origin` is a convention, not a fact. remote.pushDefault is deliberately
    not a rung: it names where commits go, not where they come from, and the
    two differ in exactly the case that makes the question worth asking, a
    fork you push to and an upstream you branch from.
    """
    names = remotes(repo=repo).lines
    if not names:
        return ""
    if len(names) == 1:
        return names[0]

    stated = [config_get("checkout.defaultRemote", repo=repo).out.strip()]
    branch = current_branch(repo=repo).out.strip()
    if branch:
        stated.append(config_get(f"branch.{branch}.remote", repo=repo).out.strip())

    for candidate in stated:
        if candidate and candidate != "." and candidate in names:
            return candidate
    return "origin" if "origin" in names else names[0]


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


def is_dirty(path: str) -> bool:
    """Uncommitted work, which is what makes `git worktree remove` refuse."""
    return bool(status_porcelain(repo=path).out.strip())


def ignored_paths(path: str) -> list[str]:
    """The gitignored paths inside a worktree.

    `git status --porcelain` does not mention these, so a checkout holding
    .env and node_modules/ reads clean, and `git worktree remove` deletes both
    at exit 0 without --force and without a word. Nothing in git brings them
    back: no ref ever pointed at them.
    """
    return [
        line[3:]
        for line in status_with_ignored(repo=path).lines
        if line.startswith("!! ")
    ]


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
