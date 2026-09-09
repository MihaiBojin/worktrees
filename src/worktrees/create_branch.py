"""Start a branch off a head branch that was fetched a moment ago.

The base is <remote>/<head> as it stands after the fetch, not the local copy
of it, so the branch is already on top of what the server has and nothing has
to be rebased afterwards.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from . import repo as R
from .git import git


@git(ok=(0, 1, 128))
def check_ref_format(name: str) -> tuple[str, ...]:
    """Would git accept this as a branch name?"""
    return "check-ref-format", "--branch", name


@git(ok=(0, 128))
def abbrev(ref: str) -> tuple[str, ...]:
    """The short sha a ref names."""
    return "rev-parse", "--short", ref


@git(mutates=True)
def checkout_new_branch(name: str, base: str) -> tuple[str, ...]:
    """--no-track, so the head branch does not become this branch's upstream.

    A branch off refs/remotes/<remote>/main that tracked it would take it as
    its upstream, and `git push` would target the head branch.
    """
    return "checkout", "--no-track", "-b", name, base


@dataclass(frozen=True)
class Started:
    branch: str
    base: str  # the full ref
    sha: str


class Refusal(Exception):
    """Why no branch was made. The message is the whole answer."""


def create(
    name: str,
    fetch: bool,
    warn: object = None,
    repo: str | os.PathLike[str] | None = None,
) -> Started:
    """Fetch, then branch `name` off the head branch and check it out."""
    if not check_ref_format(name, repo=repo):
        raise Refusal(f"{name} is not a valid branch name")
    if R.ref_exists(f"refs/heads/{name}", repo=repo):
        raise Refusal(f"{name} is already a branch; git switch {name} checks it out")

    remote = R.remote(repo=repo)
    if fetch and remote and not R.fetch(remote, repo=repo) and callable(warn):
        # An offline machine still gets a branch, off whatever it last saw,
        # and the line at the end names the commit it got.
        warn(f"{remote} could not be fetched; branching from what is already here")

    base, warning = R.head_ref(remote, online=fetch, repo=repo)
    if warning and callable(warn):
        warn(warning)
    if not base:
        hint = remote or "origin"
        raise Refusal(
            "cannot tell which branch this repository branches from; record it "
            f"with: git remote set-head {hint} --auto"
        )

    sha = abbrev(base, repo=repo)
    checkout_new_branch(name, base, repo=repo)
    return Started(name, base, sha.out.strip() if sha else "")
