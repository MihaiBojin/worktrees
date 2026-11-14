"""Start a branch from the locally resolved head ref, or from this checkout."""

from __future__ import annotations

import os
import shlex

from . import render
from . import repo as R
from .git import git


@git("check-ref-format --branch $name", ok=(0, 1, 128))
def check_ref_format(name: str) -> None:
    """Would git accept this as a branch name?"""


def refuse_nested(name: str, repo: str | os.PathLike[str] | None = None) -> None:
    """Refuse `name` when a branch sits inside it or it sits inside one.

    A ref is a path, so `codex` and `codex/plan` cannot both be branches.
    Git's own refusal names refs and lock files, not the branch in the way.
    """
    for line in R.local_tips(repo=repo).lines:
        held = line.partition("\0")[0]
        if held.startswith(f"{name}/") or name.startswith(f"{held}/"):
            outer = min(name, held, key=len)
            raise Refusal(
                f"cannot create branch {render.branch(name)}: "
                f"branch {render.branch(held)} exists\n"
                f"git cannot have both a branch {render.branch(outer)} "
                f"and branches whose names start with {render.branch(outer + '/')}"
            )


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


def here(repo: str | os.PathLike[str] | None = None) -> str:
    """Where this checkout is: its branch as a full ref, or `HEAD` when detached."""
    return R.symbolic_ref("HEAD", repo=repo).value or "HEAD"


def check(name: str, repo: str | os.PathLike[str] | None = None) -> None:
    """Refuse a name git will not take or that a branch already holds."""
    if not check_ref_format(name, repo=repo):
        raise Refusal(f"{render.branch(name)} is not a valid branch name")
    if R.ref_exists(f"refs/heads/{name}", repo=repo):
        # Native git, so the message names a command that exists for somebody
        # who installed the package without the shell plugin.
        raise Refusal(
            f"{render.branch(name)} is already a branch; "
            f"git switch {shlex.quote(name)} checks it out"
        )
    refuse_nested(name, repo=repo)


def create(
    name: str,
    repo: str | os.PathLike[str] | None = None,
    base: str = "",
) -> Started:
    """Branch `name` off `base`, or the resolved head ref, and check it out."""
    check(name, repo=repo)
    if not base:
        resolved = R.head(repo)
        if not resolved.ref:
            raise Refusal(resolved.unresolved)
        base = R.symbolic_ref(resolved.ref, repo=repo).value or resolved.ref

    sha = short_sha(base, repo=repo)
    switch_create(name, base, repo=repo)
    return Started(name, base, sha.out.strip() if sha else "")
