"""Refresh remote evidence once, then share it through the Git common directory."""

from __future__ import annotations

import json
import os
import re
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path

from . import branch_forge, forge, progress, render
from . import repo as R

_CACHE = "git-worktrees-sync.json"
_FRESH = 10 * 60
NEVER = (
    "Never synced: verdicts use refs from the clone or last fetch and no forge "
    "evidence. Run: gw sync"
)


def _empty() -> dict:
    return {"lastSync": {"refs": None, "forge": None}, "branches": {}}


def path(repo: str | os.PathLike[str] | None = None) -> Path | None:
    common = R.common_dir(repo=repo).value
    return Path(common) / _CACHE if common else None


def _timestamp(value: object) -> str | None:
    if not isinstance(value, str) or not re.fullmatch(
        r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z", value
    ):
        return None
    try:
        datetime.fromisoformat(value.removesuffix("Z") + "+00:00")
    except ValueError:
        return None
    return value


def read(repo: str | os.PathLike[str] | None = None) -> dict:
    """Read matching evidence; malformed data has no authority."""
    remote = R.remote(repo)
    if not remote:
        return _empty()
    cache_path = path(repo)
    try:
        cached = json.loads(cache_path.read_text()) if cache_path else None
    except (OSError, ValueError):
        return _empty()
    identity = {
        "name": remote,
        "url": R.config_get(f"remote.{remote}.url", repo=repo).value,
    }
    if not isinstance(cached, dict) or cached.get("remote") != identity:
        return _empty()
    times = cached.get("lastSync")
    entries = cached.get("branches")
    if not isinstance(times, dict) or not isinstance(entries, dict):
        return _empty()
    valid_times = {key: _timestamp(times.get(key)) for key in ("refs", "forge")}
    valid_entries = valid_times["refs"] is not None and all(
        times.get(key) is None or valid_times[key] is not None
        for key in ("refs", "forge")
    )
    return {
        "remote": identity,
        "lastSync": valid_times,
        "branches": entries if valid_entries else {},
    }


def envelope(repo: str | os.PathLike[str] | None = None) -> dict:
    return {"lastSync": read(repo)["lastSync"], "head": R.head(repo).data()}


def notices(
    quiet: bool = False, repo: str | os.PathLike[str] | None = None
) -> list[str]:
    """The local head source and the age of the available remote evidence."""
    lines = []
    cached = read(repo)
    times = cached["lastSync"]
    if R.remotes(repo=repo).lines:
        if not times["refs"]:
            lines.append(NEVER)
        elif not quiet:
            applicable = [value for value in times.values() if value]
            oldest = min(
                datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
                for value in applicable
            )
            age = max(0, time.time() - oldest)
            if age > _FRESH:
                amount, unit = (
                    (int(age // 86400), "day")
                    if age >= 86400
                    else (int(age // 3600), "hour")
                    if age >= 3600
                    else (int(age // 60), "minute")
                )
                lines.append(
                    f"Last sync: {amount} {unit}{'s' if amount != 1 else ''} ago. "
                    "Run: gw sync"
                )
    head = R.head(repo)
    if head.source == "inferred" or not quiet:
        lines.append(head_line(head))
    return lines


def head_line(head: R.Head) -> str:
    """Which branch the assessment measures against, and where that came from."""
    if head.ref is None:
        if head.source == "inferred":
            return "Head: unresolved (inferred; no main or master branch)"
        # A deleted or non-symbolic origin/HEAD names no branch to show.
        named = (
            f"{render.branch(head.branch)}, which does not resolve"
            if head.branch
            else "unresolved"
        )
        return f"Head: {named} ({head.source}). Run: gw sync"
    if head.source == "inferred":
        other_name = render.branch("master" if head.branch == "main" else "main")
        other = f"; {other_name} also exists" if head.other else ""
        return f"Head: {render.branch(head.branch)} (inferred{other})"
    return f"Head: {render.branch(head.branch)} ({head.source})"


def _write(cached: dict, repo: str | os.PathLike[str] | None) -> None:
    cache_path = path(repo)
    if cache_path is None:
        raise OSError("not inside a git repository")
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", dir=cache_path.parent, prefix=_CACHE + ".", delete=False
        ) as stream:
            temporary = Path(stream.name)
            json.dump(cached, stream, indent=2, sort_keys=True)
            stream.write("\n")
        os.replace(temporary, cache_path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def run(repo: str | os.PathLike[str] | None = None) -> tuple[int, list[str]]:
    """Fetch the default remote and replace the shared evidence file atomically."""
    names = R.remotes(repo=repo).lines
    remote = R.remote(repo)
    if not names:
        return 0, []
    if not remote:
        return 1, [
            f"No default remote: {', '.join(names)}. "
            "Run: git config checkout.defaultRemote <name>"
        ]
    previous = read(repo)
    progress.say(f"fetching {remote}")
    if not R.fetch(remote, repo=repo):
        return 1, [f"{remote} could not be fetched; previous sync evidence retained"]
    if not R.set_head_auto(remote, repo=repo):
        return 1, [f"{remote}/HEAD could not be updated. Run: gw sync"]
    now = (
        datetime.fromtimestamp(time.time(), UTC)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z")
    )
    cached = {
        "remote": {
            "name": remote,
            "url": R.config_get(f"remote.{remote}.url", repo=repo).value,
        },
        "lastSync": {**previous["lastSync"], "refs": now},
        "branches": {},
    }
    provider = branch_forge.provider(remote, repo)
    messages = []
    code = 0
    if provider is None:
        cached["lastSync"]["forge"] = None
    elif not branch_forge.available(provider[0]) or not forge.authenticated(
        provider[0], provider[1].split("/", 1)[0], repo
    ):
        cached["branches"] = previous["branches"]
        messages.append(
            f"{provider[0]} is unavailable or unauthenticated; "
            "previous forge evidence retained"
        )
        code = 1
    else:
        tips = R.local_tips(repo=repo).lines
        failed = []
        for line in tips:
            branch, sha = line.split("\0")
            progress.say(f"{branch}: reading forge evidence")
            succeeded, request = branch_forge.fetch_request(branch, remote, repo)
            if succeeded:
                cached["branches"][branch] = {
                    "sha": sha,
                    "request": request.data() if request else None,
                }
            else:
                failed.append(branch)
        if failed:
            code = 1
            messages.append(
                f"Forge queries failed for: {', '.join(failed)}; "
                "forge sync time unchanged"
            )
        else:
            cached["lastSync"]["forge"] = now
    try:
        _write(cached, repo)
    except OSError as exc:
        return 1, [f"cannot save sync evidence: {exc}"]
    return code, messages
