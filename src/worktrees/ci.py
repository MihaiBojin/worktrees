"""Read one pull request or merge request and its checks from the forge.

The second command that uses the network, after `gw sync`. It writes
nothing: no ref, no cache entry, no file.

GitHub answers identity and checks in one `gh pr view`, so the two cannot
describe different heads. GitLab needs several calls, so the merge request is
read again afterwards and a change between the reads marks the snapshot
stale.
"""

from __future__ import annotations

import os
import re
import time
from collections.abc import Callable

from . import branch_forge, forge
from . import repo as R

_SHA = re.compile(r"(?:[0-9a-fA-F]{40}|[0-9a-fA-F]{64})")

# --wait gives up after half an hour and asks every thirty seconds. Checks
# start a few seconds after a push, so for the first two minutes an empty
# list means "not yet" rather than "none".
WAIT = 30 * 60
INTERVAL = 30
GRACE = 2 * 60

_GITHUB_FIELDS = (
    "number,url,state,isDraft,headRefOid,headRefName,baseRefName,"
    "headRepository,headRepositoryOwner,isCrossRepository,mergeable,"
    "mergeStateStatus,statusCheckRollup"
)
_CONCLUSION = {
    "SUCCESS": "pass",
    "NEUTRAL": "pass",
    "SKIPPED": "skipping",
    "FAILURE": "fail",
    "TIMED_OUT": "fail",
    "STARTUP_FAILURE": "fail",
    "ACTION_REQUIRED": "fail",
    "CANCELLED": "cancel",
}
_UNFINISHED = ("QUEUED", "IN_PROGRESS", "WAITING", "PENDING", "REQUESTED")
_STATUS = {
    "SUCCESS": "pass",
    "PENDING": "pending",
    "EXPECTED": "pending",
    "FAILURE": "fail",
    "ERROR": "fail",
}
_GITLAB_PENDING = (
    "created",
    "waiting_for_resource",
    "preparing",
    "pending",
    "running",
    "scheduled",
    "canceling",
    "waiting_for_callback",
)


class Unreadable(Exception):
    """The forge gave no snapshot. The message is the whole answer."""


def state(checks: list[dict]) -> str:
    """One word for a list of checks: failed, blocked, pending or passed."""
    buckets = [check["bucket"] for check in checks]
    if not buckets:
        return "unknown"
    if any(b in ("fail", "cancel") for b in buckets):
        return "failed"
    if "blocked" in buckets:
        return "blocked"
    if "pending" in buckets:
        return "pending"
    if all(b in ("pass", "skipping") for b in buckets):
        return "passed"
    return "unknown"


def read(number: int | None = None, repo: str | os.PathLike[str] | None = None) -> dict:
    """The review for `number`, or for the current branch, and its checks."""
    remote = R.remote(repo)
    if not remote:
        raise Unreadable(
            "no default remote to read a review from. "
            "Run: git config checkout.defaultRemote <name>"
        )
    selected = branch_forge.provider(remote, repo)
    if selected is None:
        raise Unreadable(f"{remote} is not on a GitHub or GitLab host gw recognises")
    tool, repository = selected
    if not branch_forge.available(tool):
        raise Unreadable(f"{tool} is not installed; it reads {repository}")
    branch = ""
    if number is None:
        branch = R.current_branch(repo=repo).value
        if not branch:
            raise Unreadable("HEAD is detached. Run: gw ci NUMBER")
    selector = str(number) if number is not None else branch
    if tool == "gh":
        snapshot = _github(selector, repository, repo)
    else:
        snapshot = _gitlab(selector, repository, repo)
    pr = snapshot["pr"]
    if number is not None and pr["number"] != number:
        raise Unreadable(f"the forge returned #{pr['number']} for #{number}")
    if branch and pr["headRef"] != branch:
        raise Unreadable(f"the forge returned #{pr['number']} for another branch")
    return snapshot


def wait(
    reader: Callable[[], dict],
    now: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> dict:
    """Read until the checks settle or WAIT runs out; return the last read."""
    start = now()
    while True:
        snapshot = reader()
        elapsed = now() - start
        unsettled = (
            snapshot["stale"]
            or snapshot["checksState"] == "pending"
            or (not snapshot["checks"] and elapsed < GRACE)
        )
        if not unsettled or elapsed + INTERVAL > WAIT:
            return snapshot
        sleep(INTERVAL)


# --------------------------------------------------------------------------
# GitHub
# --------------------------------------------------------------------------


def _github(selector: str, repository: str, repo) -> dict:
    row = forge.document(
        ["gh", "pr", "view", selector, "--repo", repository, "--json", _GITHUB_FIELDS],
        repo,
    )
    if row is None:
        raise Unreadable(f"could not read pull request {selector} from {repository}")
    number = row.get("number")
    if (
        type(number) is not int
        or not isinstance(row.get("url"), str)
        or not isinstance(row.get("state"), str)
        or not _SHA.fullmatch(_text(row, "headRefOid"))
        or not isinstance(row.get("headRefName"), str)
        or not isinstance(row.get("baseRefName"), str)
        or not isinstance(row.get("statusCheckRollup") or [], list)
    ):
        raise Unreadable(f"pull request {selector} came back without its identity")
    owner = (row.get("headRepositoryOwner") or {}).get("login") or ""
    name = (row.get("headRepository") or {}).get("name") or ""
    checks = [_github_check(c) for c in _latest(row.get("statusCheckRollup") or [])]
    return {
        "pr": {
            "number": number,
            "url": row["url"],
            "state": row["state"],
            "draft": bool(row.get("isDraft")),
            "headSha": row["headRefOid"],
            "headRef": row["headRefName"],
            "baseRef": row["baseRefName"],
            "repository": repository,
            "source": f"{owner}/{name}" if owner and name else "",
            "mergeable": _text(row, "mergeable") or "UNKNOWN",
            "mergeStateStatus": _text(row, "mergeStateStatus") or "UNKNOWN",
        },
        "checksState": state(checks),
        "stale": False,
        "checks": checks,
        "pipeline": None,
    }


def _latest(rollup: list) -> list[dict]:
    """One row per check, the newest run of each.

    A re-run adds a run under the same workflow and name rather than
    replacing it. One not yet started has no start time and is the newest,
    so a queued re-run supersedes the failure it repeats.
    """
    newest: dict[tuple[str, str, str], dict] = {}
    for check in rollup:
        if not isinstance(check, dict):
            continue
        key = (
            _text(check, "__typename"),
            _text(check, "workflowName"),
            _text(check, "name") or _text(check, "context"),
        )
        seen = newest.get(key)
        if seen is None or _started(check) >= _started(seen):
            newest[key] = check
    return list(newest.values())


def _started(check: dict) -> str:
    started = _text(check, "startedAt")
    return "~" if not started or started.startswith("0001-") else started


def _github_check(check: dict) -> dict:
    if _text(check, "__typename") == "StatusContext" or (
        "context" in check and "status" not in check
    ):
        native = _text(check, "state").upper()
        return {
            "name": _text(check, "context") or "status",
            "workflow": "",
            "state": native,
            "bucket": _STATUS.get(native, "unknown"),
            "link": _text(check, "targetUrl"),
        }
    status = _text(check, "status").upper()
    conclusion = _text(check, "conclusion").upper()
    if status != "COMPLETED":
        bucket = "pending" if status in _UNFINISHED else "unknown"
    else:
        bucket = _CONCLUSION.get(conclusion, "unknown")
    return {
        "name": _text(check, "name") or "check",
        "workflow": _text(check, "workflowName"),
        "state": conclusion if status == "COMPLETED" else status,
        "bucket": bucket,
        "link": _text(check, "detailsUrl"),
    }


# --------------------------------------------------------------------------
# GitLab
# --------------------------------------------------------------------------


def _gitlab(selector: str, repository: str, repo) -> dict:
    host = repository.split("/", 1)[0]
    before = _gitlab_review(selector, repository, repo)
    pipeline_row = before.pop("_pipeline")
    checks: list[dict] = []
    pipeline = None
    stale = False
    if pipeline_row:
        pipeline, checks = _gitlab_pipeline(before, pipeline_row, host, repo)
        stale = pipeline["association"] == "stale"
    after = _gitlab_review(str(before["number"]), repository, repo)
    after_pipeline = after.pop("_pipeline")
    if after != before or _pipeline_identity(after_pipeline) != _pipeline_identity(
        pipeline_row
    ):
        stale = True
    del after["_target"]
    return {
        "pr": after,
        "checksState": "unknown" if stale else state(checks),
        "stale": stale,
        "checks": checks,
        "pipeline": pipeline,
    }


def _gitlab_review(selector: str, repository: str, repo) -> dict:
    row = forge.document(
        [
            "glab",
            "mr",
            "view",
            selector,
            "--repo",
            f"https://{repository}",
            "--output",
            "json",
        ],
        repo,
    )
    if row is None:
        raise Unreadable(f"could not read merge request {selector} from {repository}")
    number = row.get("iid")
    head = _text(row, "sha") or _text(row.get("diff_refs") or {}, "head_sha")
    if (
        type(number) is not int
        or not isinstance(row.get("web_url"), str)
        or not isinstance(row.get("state"), str)
        or not _SHA.fullmatch(head)
        or not isinstance(row.get("source_branch"), str)
        or not isinstance(row.get("target_branch"), str)
    ):
        raise Unreadable(f"merge request {selector} came back without its identity")
    native = _text(row, "state").lower()
    detailed = _text(row, "detailed_merge_status") or _text(row, "merge_status")
    if row.get("has_conflicts"):
        mergeable = "CONFLICTING"
    elif detailed == "mergeable":
        mergeable = "MERGEABLE"
    else:
        mergeable = "UNKNOWN"
    return {
        "number": number,
        "url": row["web_url"],
        "state": {"opened": "OPEN", "locked": "OPEN"}.get(native, native.upper()),
        "draft": bool(row.get("draft") or row.get("work_in_progress")),
        "headSha": head,
        "headRef": row["source_branch"],
        "baseRef": row["target_branch"],
        "repository": repository,
        "source": str(row.get("source_project_id") or ""),
        "mergeable": mergeable,
        "mergeStateStatus": detailed.upper() or "UNKNOWN",
        "_pipeline": row.get("head_pipeline") or None,
        "_target": row.get("target_project_id"),
    }


def _pipeline_identity(row: dict | None) -> tuple:
    row = row or {}
    return tuple(row.get(key) for key in ("id", "project_id", "sha", "status"))


def _gitlab_pipeline(
    review: dict, head_pipeline: dict, host: str, repo
) -> tuple[dict, list[dict]]:
    """The MR's head pipeline, read from the project that ran it."""
    pid, project = head_pipeline.get("id"), head_pipeline.get("project_id")
    if type(pid) is not int or type(project) is not int:
        raise Unreadable("the merge request's head pipeline has no valid IDs")
    pipeline = forge.document(_api(f"projects/{project}/pipelines/{pid}", host), repo)
    if (
        pipeline is None
        or pipeline.get("id") != pid
        or pipeline.get("project_id") != project
        or not _SHA.fullmatch(_text(pipeline, "sha"))
        or not isinstance(pipeline.get("status"), str)
    ):
        raise Unreadable(f"could not read GitLab pipeline {project}/{pid}")

    association = "head"
    if pipeline["sha"] != review["headSha"]:
        association = "stale"
        merge_ref = f"refs/merge-requests/{review['number']}/merge"
        if _text(pipeline, "ref") == merge_ref and _merges_current(
            review, pipeline["sha"], project, host, repo
        ):
            association = "merge-result"

    jobs = forge.pages(
        [
            *_api(f"projects/{project}/pipelines/{pid}/jobs?per_page=100", host),
            "--paginate",
        ],
        repo,
    )
    if jobs is None:
        raise Unreadable(f"could not read jobs for GitLab pipeline {project}/{pid}")
    summary = {
        "id": pid,
        "project_id": project,
        "sha": pipeline["sha"],
        "ref": _text(pipeline, "ref"),
        "status": pipeline["status"],
        "link": _text(pipeline, "web_url"),
        "association": association,
    }
    checks = [_gitlab_check(pipeline, "pipeline")] + [
        _gitlab_check(job, "job") for job in jobs
    ]
    return summary, checks


def _api(path: str, host: str) -> list[str]:
    return ["glab", "api", path, "--hostname", host]


def _merges_current(review: dict, sha: str, project: int, host: str, repo) -> bool:
    """A merged-result pipeline tests a generated commit. It counts only when
    that commit's parents are the MR's head and its target's current tip."""
    from urllib.parse import quote

    commit = forge.document(
        _api(f"projects/{project}/repository/commits/{sha}", host), repo
    )
    target = review.get("_target")
    if commit is None or type(target) is not int:
        return False
    base = quote(review["baseRef"], safe="")
    branch = forge.document(
        _api(f"projects/{target}/repository/branches/{base}", host), repo
    )
    tip = _text((branch or {}).get("commit") or {}, "id")
    parents = commit.get("parent_ids")
    return (
        commit.get("id") == sha
        and isinstance(parents, list)
        and review["headSha"] in parents
        and bool(_SHA.fullmatch(tip))
        and tip in parents
    )


def _gitlab_check(row: dict, fallback: str) -> dict:
    native = _text(row, "status")
    if native == "success":
        bucket = "pass"
    elif native == "skipped" or (
        row.get("allow_failure") is True and native in ("failed", "manual")
    ):
        bucket = "skipping"
    elif native == "failed":
        bucket = "fail"
    elif native == "canceled":
        bucket = "cancel"
    elif native == "manual":
        bucket = "blocked"
    elif native in _GITLAB_PENDING:
        bucket = "pending"
    else:
        bucket = "unknown"
    return {
        "name": _text(row, "name") or fallback,
        "workflow": _text(row, "stage"),
        "state": native,
        "bucket": bucket,
        "link": _text(row, "web_url"),
    }


def _text(row: dict, key: str) -> str:
    value = row.get(key) if isinstance(row, dict) else None
    return value if isinstance(value, str) else ""
