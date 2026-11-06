"""Start a branch off the head branch.

The base is `repo.head_ref`, so after a `gw sync` it is <remote>/<head> as the
server had it then, not the local copy of it.
"""

from __future__ import annotations

import os
import shlex

from . import render
from . import repo as R
from .git import git


@git("check-ref-format --branch $name", ok=(0, 1, 128))
def check_ref_format(name: str) -> None:
    """Would git accept this as a branch name?"""


@git("rev-parse --short $ref", ok=(0, 128))
def short_sha(ref: str) -> None:
    """The short sha a ref names."""


@git("switch --create $name --no-track $base", mutates=True)
def switch_create(name: str, base: str) -> None:
    """--no-track, so the head branch does not become this branch's upstream.

    A branch off refs/remotes/<remote>/main that tracked it would take it as
    its upstream, and `git push` would target the head branch.
    """


class Started:
    __slots__ = ("base", "branch", "sha")

    branch: str
    base: str  # the full ref
    sha: str

    def __init__(self, branch: str, base: str, sha: str) -> None:
        self.branch = branch
        self.base = base
        self.sha = sha


class Refusal(Exception):
    """Why no branch was made. The message is the whole answer."""


def head(
    remote: str,
    show: object = None,
    repo: str | os.PathLike[str] | None = None,
) -> R.Head:
    """The head ref, handed to `show` first, or a Refusal when it does not
    resolve."""
    found = R.head_ref(remote, repo=repo)
    if callable(show):
        show(found)
    if not found.resolves:
        raise Refusal(found.problem())
    return found


def create(
    name: str,
    show: object = None,
    repo: str | os.PathLike[str] | None = None,
) -> Started:
    """Branch `name` off the head branch and check it out."""
    if not check_ref_format(name, repo=repo):
        raise Refusal(f"{render.branch(name)} is not a valid branch name")
    if R.ref_exists(f"refs/heads/{name}", repo=repo):
        # Native git, so the message names a command that exists for somebody
        # who installed the package without the shell plugin.
        raise Refusal(
            f"{render.branch(name)} is already a branch; "
            f"git switch {shlex.quote(name)} checks it out"
        )

    base = head(R.remote(repo=repo), show, repo=repo).ref
    sha = short_sha(base, repo=repo)
    switch_create(name, base, repo=repo)
    return Started(name, base, sha.out.strip() if sha else "")
