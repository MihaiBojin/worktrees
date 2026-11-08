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
OFFLINE = "WORKTREES_OFFLINE"
TTL = "WORKTREES_SYNC_TTL"
_TTL = 60
NEVER = (
    "Never synced: verdicts use refs from the clone or last fetch and no forge evidence"
)


def offline() -> bool:
    """Whether every command stays off the network. An empty value is unset."""
    return bool(os.environ.get(OFFLINE))


def ttl() -> int:
    """How many seconds one sync serves the commands that run after it."""
    value = os.environ.get(TTL, "")
    if not value:
        return _TTL
    if not re.fullmatch(r"[0-9]+", value):
        raise ValueError(f"{TTL} takes whole seconds, not {value!r}")
    return int(value)


def hint(text: str) -> str:
    """The text, and the command that refreshes evidence while there is one."""
    return text if offline() else f"{text}. Run: gw sync"


def _empty() -> dict:
    return {"lastSync": {"refs": None, "forge": None}, "branches": {}}


def path(repo: str | os.PathLike[str] | None = None) -> Path | None:
    common = R.shared_dir(repo)
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


def _seconds(value: str) -> float:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()


def due(repo: str | os.PathLike[str] | None = None) -> bool:
    """Whether a command syncs before it reads remote evidence.

    Never offline, and never without a default remote, since sync records
    nothing then. Otherwise when the refs were last synced TTL seconds ago or
    longer, or never. The forge time is left out: a forge that keeps failing
    would sync before every command.
    """
    if offline():
        return False
    window = ttl()
    if not R.remote(repo):
        return False
    refs = read(repo)["lastSync"]["refs"]
    return refs is None or time.time() - _seconds(refs) >= window


def envelope(repo: str | os.PathLike[str] | None = None) -> dict:
    return {"lastSync": read(repo)["lastSync"], "head": R.head(repo).data()}


def freshness(
    quiet: bool = False, repo: str | os.PathLike[str] | None = None
) -> list[str]:
    """Never synced, or synced too long ago; quiet keeps only never synced.

    A repository without a remote is the local case and has neither.
    """
    if not R.remotes(repo=repo).lines:
        return []
    times = read(repo)["lastSync"]
    if not times["refs"]:
        return [hint(NEVER)]
    if quiet:
        return []
    oldest = min(_seconds(value) for value in times.values() if value)
    age = max(0, time.time() - oldest)
    if age <= _FRESH:
        return []
    amount, unit = (
        (int(age // 86400), "day")
        if age >= 86400
        else (int(age // 3600), "hour")
        if age >= 3600
        else (int(age // 60), "minute")
    )
    return [hint(f"Last sync: {amount} {unit}{'s' if amount != 1 else ''} ago")]


def notices(
    quiet: bool = False, repo: str | os.PathLike[str] | None = None
) -> list[str]:
    """The local head source and the age of the available remote evidence."""
    lines = freshness(quiet, repo)
    head = R.head(repo)
    if head.source == "inferred" or not quiet:
        lines.append(head_line(head))
    return lines


def head_line(head: R.Head) -> str:
    """Which branch the assessment measures against, and where that came from."""
    if head.ref is None:
        if head.source == "inferred":
            return "Head: unresolved (inferred; no main or master branch)"
        # A deleted origin/HEAD, or a plain one at several branches, names none.
        named = (
            f"{render.branch(head.branch)}, which does not resolve"
            if head.branch
            else "unresolved"
        )
        return hint(f"Head: {named} ({head.source})")
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


# Git refuses to move a tag that already exists here, exits 1, and has
# updated everything else by then. Compact output writes `*` after the arrow,
# so the tag's name is the one before it.
_MOVED_TAG = re.compile(
    r" ! \[rejected\] +(\S+) +-> \S+ +\(would clobber existing tag\)$"
)


def _fetch(remote: str, repo: str | os.PathLike[str] | None) -> tuple[bool, list[str]]:
    """Whether the fetch went through, and the tags it left where they were.

    A moved tag is the one refusal that still counts as fetched; any other
    refused ref, or an unreachable remote, is a failed fetch.
    """
    result = R.fetch(remote, repo=repo)
    if not result:
        refused = [line for line in result.err.splitlines() if line.startswith(" ! ")]
        tags = [m[1] for line in refused if (m := _MOVED_TAG.match(line))]
        return result.code == 1 and bool(tags) and len(tags) == len(refused), tags
    return True, []


def _refspec_refusal(
    names: list[str], default: str, repo: str | os.PathLike[str] | None
) -> str:
    """Why a remote's configured fetch is unsafe to run, or empty.

    `git clone --mirror` fetches `+refs/*:refs/*`. With --prune that deletes
    every local branch the remote lacks, a linked worktree's included, at
    exit 0. A remote may write only its own remote-tracking refs.
    """
    for name in names:
        own = f"refs/remotes/{name}/"
        fix = f"git config --replace-all remote.{name}.fetch '+refs/heads/*:{own}*'"
        targets = [
            spec.removeprefix("+").partition(":")[2]
            for spec in R.config_all(f"remote.{name}.fetch", repo=repo).lines
            if not spec.startswith("^")
        ]
        for target in targets:
            if target and not target.startswith(own):
                return f"remote.{name}.fetch writes {target}, outside {own}. Run: {fix}"
        if name == default and not any(targets):
            return f"remote.{name}.fetch updates nothing in {own}. Run: {fix}"
    return ""


def run(
    repo: str | os.PathLike[str] | None = None,
) -> tuple[int, list[str], list[str]]:
    """Fetch every remote, then record the default remote's head and evidence.

    Returns the exit code, the failures and the warnings.
    """
    names = R.remotes(repo=repo).lines
    remote = R.remote(repo)
    if not names:
        return 0, [], []
    # The remotes `git fetch --all` reaches, the default one first and always.
    fetched = ([remote] if remote else []) + [
        name
        for name in names
        if name != remote
        and R.config_bool(f"remote.{name}.skipFetchAll", repo=repo).value != "true"
    ]
    refusal = _refspec_refusal(fetched, remote, repo)
    if refusal:
        return 1, [refusal], []
    previous = read(repo)
    failures: list[str] = []
    warnings: list[str] = []
    default_fetched = True
    for name in fetched:
        progress.say(f"fetching {name}")
        succeeded, tags = _fetch(name, repo)
        if tags:
            warnings.append(
                f"{name} moved {', '.join(tags)}; the local tags are unchanged. "
                f"Run: git fetch --tags --force {name}"
            )
        if not succeeded and name == remote:
            default_fetched = False
            failures.append(
                f"{name} could not be fetched; previous sync evidence retained"
            )
        elif not succeeded:
            failures.append(f"{name} could not be fetched")
    if not remote:
        failures.append(
            f"No default remote: {', '.join(names)}. "
            "Run: git config checkout.defaultRemote <name>"
        )
        return 1, failures, warnings
    if not default_fetched:
        return 1, failures, warnings
    progress.say(f"{remote}: reading its default branch")
    if not R.set_head_auto(remote, repo=repo):
        failures.append(
            f"{remote}/HEAD could not be updated. Run: git remote set-head "
            f"{remote} --auto"
        )
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
    if provider is None:
        cached["lastSync"]["forge"] = None
    elif not branch_forge.available(provider[0]) or not forge.authenticated(
        provider[0], provider[1].split("/", 1)[0], repo
    ):
        cached["branches"] = previous["branches"]
        failures.append(
            f"{provider[0]} is unavailable or unauthenticated; "
            "previous forge evidence retained"
        )
    else:
        tips = [line.split("\0") for line in R.local_tips(repo=repo).lines]
        progress.say(f"reading forge evidence for {len(tips)} branches")

        def query(numbered: tuple[int, list[str]]):
            number, (branch, sha) = numbered
            progress.say(f"{branch}: reading forge evidence ({number} of {len(tips)})")
            return branch, sha, branch_forge.fetch_request(branch, remote, repo)

        # Imported here, since every command reads this module for its notices.
        from concurrent.futures import ThreadPoolExecutor

        with ThreadPoolExecutor(max_workers=8) as pool:
            answers = list(pool.map(query, enumerate(tips, 1)))
        failed = []
        for branch, sha, (succeeded, request) in answers:
            if succeeded:
                cached["branches"][branch] = {
                    "sha": sha,
                    "request": request.data() if request else None,
                }
            else:
                failed.append(branch)
        if failed:
            failures.append(
                f"Forge queries failed for: {', '.join(failed)}; "
                "forge sync time unchanged"
            )
        else:
            cached["lastSync"]["forge"] = now
    try:
        _write(cached, repo)
    except OSError as exc:
        return 1, [*failures, f"cannot save sync evidence: {exc}"], warnings
    return (1 if failures else 0), failures, warnings
