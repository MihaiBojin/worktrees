"""What the forge says about a branch, when git cannot tell.

A branch merged as part of a stack is the case content cannot answer. Its
changes reach the head branch across several squashes, and the intermediate
state it holds differs from the final one in the same regions, so a stale
branch whose work is upstream and a branch with real work left look exactly
alike to a diff.

The forge knows. It is asked last, because it costs a round trip and because
it can be wrong about work pushed after the merge, which is why the answer is
cross-checked against the commits an upstream has not got.
"""

from __future__ import annotations

import os
import subprocess

from .git import options


class Request:
    """A pull or merge request for a branch."""

    __slots__ = ("noun", "number", "state")

    number: int
    state: str  # MERGED, CLOSED, OPEN
    noun: str  # what that forge calls it

    def __init__(self, number: int, state: str, noun: str) -> None:
        self.number = number
        self.state = state
        self.noun = noun


def available() -> str:
    """The forge CLI on PATH, or empty when there is none.

    shutil costs 4.7 ms to import and one call needs it, so it is imported
    here rather than at the top: every command that never asks the forge pays
    nothing for the question.
    """
    import shutil

    for tool in ("gh", "glab"):
        if shutil.which(tool):
            return tool
    return ""


def request_for(
    branch: str,
    repo: str | os.PathLike[str] | None = None,
) -> Request | None:
    """The newest request whose head is this branch, or None.

    None covers every way of not knowing: no CLI, not authenticated, no
    remote, no request. The caller treats that as "the forge said nothing"
    rather than as "no".
    """
    import json

    tool = available()
    if not tool:
        return None

    if tool == "gh":
        argv = [
            "gh",
            "pr",
            "list",
            "--head",
            branch,
            "--state",
            "all",
            "--limit",
            "1",
            "--json",
            "number,state",
        ]
        noun = "pull request"
    else:
        argv = [
            "glab",
            "mr",
            "list",
            "--source-branch",
            branch,
            "--all",
            "--output",
            "json",
        ]
        noun = "merge request"

    options.log.append(tuple(argv))
    if options.verbose:
        import shlex
        import sys

        print("+ " + shlex.join(argv), file=sys.stderr)

    try:
        proc = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            check=False,
            cwd=os.fspath(repo) if repo else None,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0:
        return None

    try:
        rows = json.loads(proc.stdout or "[]")
    except json.JSONDecodeError:
        return None
    if not rows:
        return None

    row = rows[0]
    number = row.get("number") or row.get("iid") or 0
    state = str(row.get("state", "")).upper()
    return Request(int(number), state, noun)
