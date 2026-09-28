"""What the forge says about a branch, when git cannot tell.

A branch merged as part of a stack is the case content cannot answer. Its
changes reach the head branch across several squashes, and the intermediate
state it holds differs from the final one in the same regions, so a stale
branch whose work is upstream and a branch with real work left look exactly
alike to a diff.

The forge is asked after the content checks. An existing upstream is checked
for unpushed commits. A merged request with a missing configured upstream
permits pruning, subject to the worktree protections.
"""

from __future__ import annotations

import contextlib
import json
import os
import subprocess
import threading
import time
from pathlib import Path

from .git import options


class Request:
    """A pull or merge request for a branch."""

    __slots__ = ("idle", "noun", "number", "state")

    number: int
    state: str  # MERGED, CLOSED, OPEN
    noun: str  # what that forge calls it
    # Nothing can merge it without somebody acting first: see `_idle`.
    idle: bool

    def __init__(self, number: int, state: str, noun: str, idle: bool = False) -> None:
        self.number = number
        self.state = state
        self.noun = noun
        self.idle = idle


# How long an answer is taken as read, in seconds. A merged or closed request
# stays that way, so its answer does not expire. An open one that nothing is
# about to merge, or no request on a branch that is only here, lasts ten
# minutes. No request on a pushed branch lasts a minute: a push is how a request
# gets opened, and one opened from here is usually the next thing that
# happens.
_IDLE_FOR = 10 * 60
_PUSHED_FOR = 60
# What `_ask` returns when the forge answered and there is no request, as
# against None for every way of not getting an answer at all.
_NONE = "NONE"
_lock = threading.Lock()


def _idle(row: dict[str, object]) -> bool:
    """Whether an open request can merge only after its author pushes.

    Not idle: auto-merge or merge-when-pipeline-succeeds set, a check still
    running, or a forge saying it is mergeable or cannot say. Idle: a draft,
    a conflict, or on GitLab a needed rebase. A push changes the sha the
    answer is kept under, so the answer lasts exactly as long as it is true.
    """
    if row.get("autoMergeRequest") or row.get("merge_when_pipeline_succeeds"):
        return False
    if "detailed_merge_status" in row:  # GitLab
        return row.get("detailed_merge_status") in (
            "draft_status",
            "conflict",
            "need_rebase",
        )
    checks = row.get("statusCheckRollup") or []
    if not isinstance(checks, list):
        return False
    for check in checks:
        if not isinstance(check, dict):
            return False
        # A check run reports `status`, a commit status reports `state`.
        if check.get("status") not in (None, "COMPLETED"):
            return False
        if check.get("state") in ("PENDING", "EXPECTED"):
            return False
    return bool(row.get("isDraft")) or row.get("mergeable") == "CONFLICTING"


def _cache_file(repo: str | os.PathLike[str] | None) -> Path | None:
    from . import repo as R

    common = R.common_dir(repo=repo).out.strip()
    return Path(common) / "git-worktrees-forge.json" if common else None


def _cached(branch: str, sha: str, path: Path) -> Request | None:
    try:
        entry = json.loads(path.read_text()).get(branch)
    except (OSError, ValueError, AttributeError):
        return None
    if not isinstance(entry, dict) or entry.get("sha") != sha or "for" not in entry:
        return None
    lasts = entry.get("for")
    if lasts is not None and time.time() - float(entry.get("at", 0)) >= lasts:
        return None
    return Request(int(entry["number"]), str(entry["state"]), str(entry["noun"]), True)


def _remember(
    branch: str, sha: str, request: Request, lasts: float | None, path: Path
) -> None:
    with _lock:
        try:
            entries = json.loads(path.read_text())
        except (OSError, ValueError):
            entries = {}
        if not isinstance(entries, dict):
            entries = {}
        entries[branch] = {
            "sha": sha,
            "number": request.number,
            "state": request.state,
            "noun": request.noun,
            "at": time.time(),
            "for": lasts,
        }
        # A cache that cannot be written costs a round trip, not an answer.
        with contextlib.suppress(OSError):
            path.write_text(json.dumps(entries, indent=1, sort_keys=True))


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


# `gh` reads `GH_REPO` and `glab` reads `GITLAB_REPO`, and either beats the
# directory the CLI is run in. A variable left in a shell would send every
# question to a repository nobody here consulted, and branch names repeat
# across repositories: a merged request found under the wrong one reads as
# proof and deletes a branch.
REDIRECTS = ("GH_REPO", "GITLAB_REPO")


def request_for(
    branch: str,
    repo: str | os.PathLike[str] | None = None,
    sha: str = "",
) -> Request | None:
    """The newest request whose head is this branch, or None.

    None covers every way of not knowing: no CLI, not authenticated, no
    remote, no request. The caller treats that as "the forge said nothing"
    rather than as "no".

    Given the branch's sha, an answer is kept in the repository's common
    directory under that sha for as long as `_lasts` says. --force-refresh
    asks anyway.
    """
    path = _cache_file(repo) if sha else None
    request = None
    if path is not None and not options.refresh:
        request = _cached(branch, sha, path)
    if request is None:
        request = _ask(branch, repo)
        if path is not None:
            lasts = _lasts(branch, request, repo)
            if request is not None and lasts != 0:
                _remember(branch, sha, request, lasts, path)
    return None if request is None or request.state == _NONE else request


def _lasts(
    branch: str, request: Request | None, repo: str | os.PathLike[str] | None
) -> float | None:
    """Seconds an answer is kept, None for good, and 0 for not at all."""
    if request is None:
        return 0
    if request.state in ("MERGED", "CLOSED"):
        return None
    if request.state == _NONE:
        from . import repo as R

        pushed = bool(R.upstream_of(branch, repo=repo).out.strip())
        return _PUSHED_FOR if pushed else _IDLE_FOR
    return _IDLE_FOR if request.idle else 0


def _ask(branch: str, repo: str | os.PathLike[str] | None) -> Request | None:
    """The forge's own answer, from its CLI."""
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
            "number,state,isDraft,mergeable,autoMergeRequest,statusCheckRollup",
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
            env={k: v for k, v in os.environ.items() if k not in REDIRECTS},
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
        return Request(0, _NONE, noun)

    row = rows[0]
    number = row.get("number") or row.get("iid") or 0
    state = str(row.get("state", "")).upper()
    # GitLab says `opened`; everything below compares against GitHub's words.
    state = "OPEN" if state == "OPENED" else state
    return Request(int(number), state, noun, state == "OPEN" and _idle(row))
