"""Request evidence tied to a source tip and its destination repository."""

from __future__ import annotations

import os
import re
import shutil
from dataclasses import asdict, dataclass
from urllib.parse import quote, urlsplit

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
    source_repository: str
    source_verified: bool
    verified_source_repository: str
    verified_target_repository: str

    def data(self) -> dict:
        return asdict(self)

    def unverified_reason(self, branch: str, sha: str, head_branch: str) -> str:
        if self.source_branch != branch or self.source_sha != sha:
            return "the request does not identify this branch tip"
        if not self.source_verified or not self.source_repository:
            return "the request's source repository is unverified"
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


def provider(
    remote: str, repo: str | os.PathLike[str] | None = None
) -> tuple[str, str] | None:
    host, path = _repository(R.config_get(f"remote.{remote}.url", repo=repo).value)
    if not host or not path or "/" not in path:
        return None
    if host == "github.com" or host.startswith("github."):
        return "gh", f"{host}/{path}"
    if host == "gitlab.com" or host.startswith("gitlab."):
        return "glab", f"{host}/{path}"
    return None


def available(tool: str) -> bool:
    return bool(shutil.which(tool))


def request_for(
    branch: str, remote: str, sha: str = "", repo: str | os.PathLike[str] | None = None
) -> Request | None:
    """Read source-tip evidence from the shared sync cache, without a query."""
    from . import sync

    if not sha:
        sha = R.local_sha(f"refs/heads/{branch}", repo=repo).out.strip()
    entry = sync.read(repo)["branches"].get(branch)
    if not isinstance(entry, dict) or entry.get("sha") != sha:
        return None
    data = entry.get("request")
    if not isinstance(data, dict):
        return None
    try:
        request = Request(**data)
    except TypeError:
        return None
    if (
        type(request.number) is not int
        or request.number <= 0
        or type(request.source_verified) is not bool
        or any(
            not isinstance(getattr(request, key), str)
            for key in (
                "state",
                "noun",
                "url",
                "repository",
                "source_branch",
                "source_sha",
                "target_branch",
                "merge_sha",
                "merged_at",
                "source_repository",
                "verified_source_repository",
                "verified_target_repository",
            )
        )
    ):
        return None
    if not identified(request, remote, repo):
        return None
    if request.source_branch != branch or request.source_sha != sha:
        return None
    return request


def identified(
    request: Request, remote: str, repo: str | os.PathLike[str] | None = None
) -> bool:
    """The request is on the default remote's forge, from a source verified there."""
    selected = provider(remote, repo)
    if selected is None or request.repository.lower() != selected[1].lower():
        return False
    source_host, _, source_path = request.source_repository.partition("/")
    target_host = selected[1].split("/", 1)[0]
    return not (
        request.source_repository.lower() != request.verified_source_repository.lower()
        or request.repository.lower() != request.verified_target_repository.lower()
        or source_host.lower() != target_host.lower()
        or not re.fullmatch(r"[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)+", source_path)
        or any(part in (".", "..") for part in source_path.split("/"))
    )


def fetch_request(
    branch: str,
    remote: str,
    repo: str | os.PathLike[str] | None = None,
    merged: bool = False,
) -> tuple[bool, Request | None]:
    """Query one branch's newest request and verify its repository identities.

    Sync asks for the newest in any state. `merged` asks for the newest merged
    one, which a newer request opened from the same branch would otherwise
    hide.
    """
    selected = provider(remote, repo)
    if selected is None:
        return False, None
    tool, repository = selected
    host, path = repository.split("/", 1)
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
            "merged" if merged else "all",
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
            "--merged" if merged else "--all",
            "--per-page",
            "1",
            "--order",
            "created_at",
            "--sort",
            "desc",
            "--output",
            "json",
        ]
    rows = forge.query(argv, repo)
    if rows is None:
        return False, None
    if not rows:
        return True, None
    row = rows[0]
    number = row.get("number" if tool == "gh" else "iid")
    if not isinstance(number, int) or isinstance(number, bool) or number <= 0:
        return False, None
    state = _text(row, "state").upper()
    if state == "OPENED":
        state = "OPEN"
    if tool == "gh":
        source = row.get("headRepository") or {}
        commit = row.get("mergeCommit") or {}
        if not isinstance(source, dict) or not isinstance(commit, dict):
            return False, None
        source_path = _text(source, "nameWithOwner")
        same = (
            row.get("isCrossRepository") is False
            and source_path.lower() == path.lower()
        )
        verified = same
        verified_source = repository if same else ""
        if (
            not same
            and row.get("isCrossRepository") is True
            and re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", source_path)
        ):
            detail = forge.document(
                ["gh", "api", f"repos/{source_path}", "--hostname", host], repo
            )
            if detail is None:
                return False, None
            if detail:
                actual_host, actual_path = _repository(_text(detail, "html_url"))
                verified = (
                    actual_host == host
                    and actual_path.lower() == source_path.lower()
                    and _text(detail, "full_name").lower() == source_path.lower()
                )
                verified_source = f"{actual_host}/{actual_path}" if verified else ""
        target_host, target_path = _repository(_text(row, "url").split("/pull/", 1)[0])
        target_repository = f"{target_host}/{target_path}"
        return True, Request(
            number,
            state,
            "pull request",
            _text(row, "url"),
            target_repository,
            _text(row, "headRefName"),
            _text(row, "headRefOid"),
            _text(row, "baseRefName"),
            _text(commit, "oid"),
            _text(row, "mergedAt"),
            f"{host}/{source_path}" if source_path else "",
            verified,
            verified_source,
            repository,
        )
    source_id, target_id = row.get("source_project_id"), row.get("target_project_id")
    valid_ids = (
        type(source_id) is int
        and type(target_id) is int
        and source_id > 0
        and target_id > 0
    )
    source_repository = ""
    verified_source = ""
    verified_target = ""
    verified = False
    if valid_ids:
        target = forge.document(
            ["glab", "api", f"projects/{target_id}", "--hostname", host], repo
        )
        source = (
            target
            if source_id == target_id
            else forge.document(
                ["glab", "api", f"projects/{source_id}", "--hostname", host], repo
            )
        )
        if source is None or target is None:
            return False, None
        if (
            source
            and target
            and type(source.get("id")) is int
            and source.get("id") == source_id
            and type(target.get("id")) is int
            and target.get("id") == target_id
        ):
            source_host, source_path = _repository(_text(source, "web_url"))
            target_host, target_path = _repository(_text(target, "web_url"))
            source_repository = f"{source_host}/{source_path}"
            verified = (
                source_host == host
                and source_path == _text(source, "path_with_namespace")
                and bool(re.fullmatch(r"[A-Za-z0-9_./-]+", source_path))
                and target_path == _text(target, "path_with_namespace")
                and f"{target_host}/{target_path}".lower() == repository.lower()
            )
            if verified:
                verified_source = source_repository
                verified_target = repository
    target_host, target_path = _repository(
        _text(row, "web_url").split("/-/merge_requests/", 1)[0]
    )
    return True, Request(
        number,
        state,
        "merge request",
        _text(row, "web_url"),
        f"{target_host}/{target_path}",
        _text(row, "source_branch"),
        _text(row, "sha"),
        _text(row, "target_branch"),
        _text(row, "merge_commit_sha") or _text(row, "squash_commit_sha"),
        _text(row, "merged_at"),
        source_repository,
        verified,
        verified_source,
        verified_target,
    )


@dataclass(frozen=True)
class Carried:
    """A merged request that carried a commit, and every commit it carried."""

    number: int
    noun: str
    source_branch: str
    target_branch: str
    merge_sha: str
    commits: frozenset[str]


def carried_by(
    sha: str, remote: str, repo: str | os.PathLike[str] | None = None
) -> tuple[bool, list[Carried]]:
    """The merged requests into the default remote's repository that carried
    `sha`, and the commits each carried.

    For a branch stacked on another whose request squash-merged: the commits
    the two share are in the head branch in a form neither ancestry nor
    content can recognise once the parent gained commits of its own, and the
    parent's request names them. False when the forge could not be asked; no
    forge this reads is an empty answer.

    Each request's commits come from one page of 100, by choice: a parent
    seldom drifts further from the branch stacked on it, and a longer list
    that misses the shared commits leaves a rebase that stops on them.
    """
    selected = provider(remote, repo)
    if selected is None:
        return True, []
    tool, repository = selected
    host, path = repository.split("/", 1)
    if tool == "gh":
        rows = forge.query(
            ["gh", "api", f"repos/{path}/commits/{sha}/pulls", "--hostname", host],
            repo,
        )
        if rows is None:
            return False, []
        found = []
        for row in rows:
            number, base, head = row.get("number"), row.get("base"), row.get("head")
            if not isinstance(base, dict) or not isinstance(head, dict):
                continue
            target = base.get("repo")
            target_path = _text(target, "full_name") if isinstance(target, dict) else ""
            if (
                type(number) is not int
                or number <= 0
                or not _text(row, "merged_at")
                or target_path.lower() != path.lower()
            ):
                continue
            commits = forge.query(
                [
                    "gh",
                    "api",
                    f"repos/{path}/pulls/{number}/commits?per_page=100",
                    "--hostname",
                    host,
                ],
                repo,
            )
            if commits is None:
                return False, []
            found.append(
                Carried(
                    number,
                    "pull request",
                    _text(head, "ref"),
                    _text(base, "ref"),
                    _text(row, "merge_commit_sha"),
                    frozenset(_text(commit, "sha") for commit in commits),
                )
            )
        return True, found
    project = quote(path, safe="")
    rows = forge.query(
        [
            "glab",
            "api",
            f"projects/{project}/repository/commits/{sha}/merge_requests",
            "--hostname",
            host,
        ],
        repo,
    )
    if rows is None:
        return False, []
    found = []
    for row in rows:
        number = row.get("iid")
        target_host, target_path = _repository(
            _text(row, "web_url").split("/-/merge_requests/", 1)[0]
        )
        if (
            type(number) is not int
            or number <= 0
            or _text(row, "state") != "merged"
            or f"{target_host}/{target_path}".lower() != repository.lower()
        ):
            continue
        commits = forge.query(
            [
                "glab",
                "api",
                f"projects/{project}/merge_requests/{number}/commits?per_page=100",
                "--hostname",
                host,
            ],
            repo,
        )
        if commits is None:
            return False, []
        found.append(
            Carried(
                number,
                "merge request",
                _text(row, "source_branch"),
                _text(row, "target_branch"),
                _text(row, "merge_commit_sha") or _text(row, "squash_commit_sha"),
                frozenset(_text(commit, "id") for commit in commits),
            )
        )
    return True, found
