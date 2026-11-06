"""What `gw sync` records: which remote it fetched, when, and what the
forge said about each local branch tip.

One JSON file in the git common directory, so every worktree of a
repository reads the same one:

    {"remote": {"name": "origin", "url": "github.com/org/repo"},
     "refs": "2026-11-06T12:00:00Z", "forge": "2026-11-06T12:00:01Z",
     "evidence": {"feature": {"sha": "<the tip asked about>",
                              "request": {"number": 17, ...}},
                  "spike": {"sha": "...", "request": null}}}

`refs` is when the fetch and `remote set-head` last succeeded. `forge` is
when the forge last answered for every branch, and null when the remote's
host is not a known forge. A `request` of null means the forge has no
request from that branch. The URL is the one `repo.short_url` writes, so no
credential reaches the file.
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path

from . import branch_forge, branches, forge, render
from . import repo as R

NAME = "git-worktrees-sync.json"


def _path(repo: str | os.PathLike[str] | None = None) -> Path | None:
    common = R.common_dir(repo=repo).out.strip()
    return Path(common) / NAME if common else None


def _url(remote: str, repo: str | os.PathLike[str] | None = None) -> str:
    return dict(R.remote_urls(repo=repo)).get(remote, "")


def now() -> str:
    """This moment in UTC, as RFC 3339 with a Z."""
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def write(remote: str, refs: str, asked: str | None, evidence: dict) -> None:
    """Replace the file whole: a reader sees the old one or the new one."""
    path = _path()
    if path is None:
        return
    record = {
        "remote": {"name": remote, "url": _url(remote)},
        "refs": refs,
        "forge": asked,
        "evidence": evidence,
    }
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{NAME}.")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(record, f, indent=2)
            f.write("\n")
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def read(repo: str | os.PathLike[str] | None = None) -> dict | None:
    """The last sync, or None when there is none to trust.

    None for a missing or malformed file, for a repository with no default
    remote, and for a file that names a different remote or URL than the
    default remote has now: refs fetched from somewhere else say nothing
    about this one.
    """
    path = _path(repo)
    remote = R.remote(repo=repo)
    if path is None or not remote:
        return None
    try:
        record = json.loads(path.read_text())
    except (OSError, ValueError):
        return None
    if (
        not isinstance(record, dict)
        or not isinstance(record.get("refs"), str)
        or not isinstance(record.get("forge"), (str, type(None)))
        or not isinstance(record.get("evidence", {}), dict)
    ):
        return None
    if record.get("remote") != {"name": remote, "url": _url(remote, repo)}:
        return None
    return record


def ask(remote: str, previous: dict | None) -> tuple[str | None, dict, list[str]]:
    """Ask the forge about every local branch tip.

    Returns the `forge` time and the evidence to record, and one line for
    each thing that went unanswered. When nothing answered, because the CLI
    is missing or every question failed, the previous sync's time and
    evidence stand. When only some failed, the rest are recorded, the
    failed branches carry no evidence, and the time stays where it was.
    """
    tool = branch_forge.tool_for(remote)
    if not tool:
        return None, {}, []
    kept = previous or {}
    asked, evidence = kept.get("forge"), kept.get("evidence", {})
    if not shutil.which(tool):
        return asked, evidence, [f"{tool} is not installed, so the forge was not asked"]
    tips = {
        name: sha for name, (sha, symref) in branches.local_refs().items() if not symref
    }

    def one(name: str) -> tuple[str, dict | None, str]:
        try:
            request = branch_forge.request_for(name, remote)
        except forge.Unanswered as exc:
            return name, None, str(exc)
        return name, request.data() if request else None, ""

    # Each answer is a round trip; threads overlap the waits.
    with ThreadPoolExecutor(max_workers=8) as pool:
        answers = list(pool.map(one, sorted(tips)))
    failed = [
        f"the forge did not answer for {render.branch(name)}: {why}"
        for name, _, why in answers
        if why
    ]
    if failed and len(failed) == len(answers):
        return asked, evidence, failed
    found = {
        name: {"sha": tips[name], "request": request}
        for name, request, why in answers
        if not why
    }
    return (asked if failed else now()), found, failed
