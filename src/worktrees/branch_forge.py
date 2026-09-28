"""Request evidence tied to a source tip and its destination repository."""

from __future__ import annotations

import re
import shutil
from dataclasses import asdict, dataclass
from urllib.parse import urlsplit

from . import forge
from . import repo as R


@dataclass
class Request:
    number: int
    state: str
    noun: str
    url: str
    repository: str
    source_branch: str
    source_sha: str
    target_branch: str
    merge_sha: str
    merged_at: str
    same_repository: bool

    def data(self) -> dict:
        return asdict(self)

    def unverified_reason(self, branch: str, sha: str, head_branch: str) -> str:
        if self.source_branch != branch or self.source_sha != sha:
            return "the request does not identify this branch tip"
        if not self.same_repository:
            return "the request's source repository does not match this remote"
        if self.target_branch != head_branch:
            return "the request targeted a different branch"
        if not self.merged_at or not re.fullmatch(
            r"(?:[0-9a-f]{40}|[0-9a-f]{64})", self.merge_sha
        ):
            return "the request has no verified merge commit"
        return ""


def _repository(url: str) -> tuple[str, str]:
    if "://" not in url:
        if ":" not in url:
            return "", ""
        host, path = url.split(":", 1)
        host = host.rsplit("@", 1)[-1]
    else:
        parsed = urlsplit(url)
        host, path = parsed.hostname or "", parsed.path
    return host.lower(), path.strip("/").removesuffix(".git")


def _text(row: dict, key: str) -> str:
    value = row.get(key)
    return value if isinstance(value, str) else ""


def request_for(branch: str, remote: str) -> Request | None:
    host, path = _repository(R.config_get(f"remote.{remote}.url").out.strip())
    if not host or not path or "/" not in path:
        return None
    if host == "github.com" or host.startswith("github."):
        tool = "gh"
    elif host == "gitlab.com" or host.startswith("gitlab."):
        tool = "glab"
    else:
        return None
    if not shutil.which(tool):
        return None
    repository = f"{host}/{path}"
    if tool == "gh":
        argv = [
            tool,
            "pr",
            "list",
            "--repo",
            repository,
            "--head",
            branch,
            "--state",
            "all",
            "--limit",
            "1",
            "--json",
            "number,state,url,headRefName,headRefOid,baseRefName,mergeCommit,"
            "mergedAt,headRepository,isCrossRepository",
        ]
    else:
        argv = [
            tool,
            "mr",
            "list",
            "--repo",
            f"https://{repository}",
            "--source-branch",
            branch,
            "--all",
            "--per-page",
            "1",
            "--order",
            "created_at",
            "--sort",
            "desc",
            "--output",
            "json",
        ]
    rows = forge.query(argv)
    if not rows:
        return None
    row = rows[0]
    number = row.get("number" if tool == "gh" else "iid")
    if not isinstance(number, int) or isinstance(number, bool) or number <= 0:
        return None
    state = _text(row, "state").upper()
    if state == "OPENED":
        state = "OPEN"
    if tool == "gh":
        source = row.get("headRepository") or {}
        commit = row.get("mergeCommit") or {}
        if not isinstance(source, dict) or not isinstance(commit, dict):
            return None
        return Request(
            number,
            state,
            "pull request",
            _text(row, "url"),
            repository,
            _text(row, "headRefName"),
            _text(row, "headRefOid"),
            _text(row, "baseRefName"),
            _text(commit, "oid"),
            _text(row, "mergedAt"),
            row.get("isCrossRepository") is False
            and _text(source, "nameWithOwner").lower() == path.lower(),
        )
    source_id, target_id = row.get("source_project_id"), row.get("target_project_id")
    return Request(
        number,
        state,
        "merge request",
        _text(row, "web_url"),
        repository,
        _text(row, "source_branch"),
        _text(row, "sha"),
        _text(row, "target_branch"),
        _text(row, "merge_commit_sha") or _text(row, "squash_commit_sha"),
        _text(row, "merged_at"),
        type(source_id) is int
        and type(target_id) is int
        and source_id > 0
        and source_id == target_id,
    )
