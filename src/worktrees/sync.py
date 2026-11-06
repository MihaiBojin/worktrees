"""What `gw sync` records: which remote it fetched, and when.

One JSON file in the git common directory, so every worktree of a
repository reads the same one:

    {"remote": {"name": "origin", "url": "github.com/org/repo"},
     "refs": "2026-11-06T12:00:00Z", "forge": null}

`refs` is when the fetch and `remote set-head` last succeeded. `forge` is
when the forge was last asked, and null until something asks it. The URL is
the one `repo.short_url` writes, so no credential reaches the file.
"""

from __future__ import annotations

import json
import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from . import repo as R

NAME = "git-worktrees-sync.json"


def _path() -> Path | None:
    common = R.common_dir().out.strip()
    return Path(common) / NAME if common else None


def _url(remote: str) -> str:
    return dict(R.remote_urls()).get(remote, "")


def now() -> str:
    """This moment in UTC, as RFC 3339 with a Z."""
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def write(remote: str, refs: str) -> None:
    """Replace the file whole: a reader sees the old one or the new one."""
    path = _path()
    if path is None:
        return
    record = {
        "remote": {"name": remote, "url": _url(remote)},
        "refs": refs,
        "forge": None,
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


def read() -> dict | None:
    """The last sync, or None when there is none to trust.

    None for a missing or malformed file, for a repository with no default
    remote, and for a file that names a different remote or URL than the
    default remote has now: refs fetched from somewhere else say nothing
    about this one.
    """
    path = _path()
    remote = R.remote()
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
    ):
        return None
    if record.get("remote") != {"name": remote, "url": _url(remote)}:
        return None
    return record
