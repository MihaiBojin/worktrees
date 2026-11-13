"""Merge one GitHub pull request at a checked head, respecting its queue."""

from __future__ import annotations

import json
import re
import time
from collections.abc import Callable
from dataclasses import dataclass

from . import branch_forge, ci, forge
from . import repo as R
from .errors import Refused

WAIT = 60
INTERVAL = 2
STACK_WAIT = 10 * 60
STACK_INTERVAL = 5
_UUID = re.compile(r"[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}")


def _request(
    repository: str,
    path: str,
    payload: dict | None = None,
    *,
    version: str | None = "2026-03-10",
) -> tuple[int, dict]:
    host, project = repository.split("/", 1)
    argv = [
        "gh",
        "api",
        f"repos/{project}/{path}",
        "--hostname",
        host,
        "--include",
        "--method",
        "GET" if payload is None else "PUT",
    ]
    if version is not None:
        argv.extend(["--header", f"X-GitHub-Api-Version: {version}"])
    for key, value in (payload or {}).items():
        argv.extend(["--raw-field", f"{key}={value}"])
    proc = forge._run(argv)
    if proc is None:
        raise Refused(f"could not read GitHub's answer for {path}")
    headers, separator, body = proc.stdout.partition("\n\n")
    status = re.match(r"HTTP/\S+ (\d{3})\b", headers)
    try:
        row = json.loads(body) if separator else None
    except ValueError:
        row = None
    if status is None or not isinstance(row, dict):
        raise Refused(
            proc.stderr.strip() or f"GitHub returned no valid answer for {path}"
        )
    code = int(status[1])
    failed = code == 400 and row.get("status") == "failed" and "/merge-async" in path
    if not failed and (
        code not in (200, 202, 409) or (proc.returncode and code != 409)
    ):
        raise Refused(
            str(row.get("message") or proc.stderr.strip() or f"GitHub HTTP {code}")
        )
    if payload is None and code != 200 and not failed:
        raise Refused(f"GitHub HTTP {code} while reading {path}")
    return code, row


def _pull(repository: str, number: int, *, version: str | None = "2026-03-10") -> dict:
    _, row = _request(repository, f"pulls/{number}", version=version)
    if (
        row.get("number") != number
        or not isinstance(row.get("head"), dict)
        or not isinstance(row.get("base"), dict)
        or not isinstance(row["base"].get("repo"), dict)
        or not isinstance(row["base"]["repo"].get("full_name"), str)
        or row["base"]["repo"]["full_name"].lower()
        != repository.split("/", 1)[1].lower()
    ):
        raise Refused(f"GitHub returned another pull request for #{number}")
    return row


@dataclass(frozen=True)
class Scope:
    base: str
    sha: str
    stack: dict | None
    waiting: str = ""


def _scope(repository: str, number: int) -> Scope:
    row = _pull(repository, number)
    if row.get("state") != "open" or row.get("draft") is not False:
        raise Refused(f"#{number} is not an open, ready pull request")
    sha = row["head"].get("sha")
    if not isinstance(sha, str) or not ci._SHA.fullmatch(sha):
        raise Refused(f"#{number} has no valid head SHA")
    base = row["base"].get("ref")
    if not isinstance(base, str) or not base:
        raise Refused(f"#{number} has no base branch")
    info = row.get("stack")
    if info is None:
        return Scope(base, sha, None)
    if not isinstance(info, dict) or any(
        type(info.get(key)) is not int or info[key] < 1
        for key in ("id", "number", "position", "size")
    ):
        raise Refused(f"#{number} has unreadable stack metadata")
    _, stack = _request(repository, f"stacks/{info['number']}")
    members = stack.get("pull_requests")
    if (
        stack.get("id") != info.get("id")
        or stack.get("number") != info["number"]
        or not isinstance(members, list)
        or not isinstance(stack.get("base"), dict)
        or not isinstance(stack["base"].get("ref"), str)
        or not stack["base"]["ref"]
        or len(members) != info.get("size")
        or not all(
            isinstance(p, dict)
            and type(p.get("number")) is int
            and isinstance(p.get("head"), dict)
            for p in members
        )
    ):
        raise Refused(f"#{number} stack membership could not be verified")
    numbers = [p["number"] for p in members]
    if len(set(numbers)) != len(numbers) or number not in numbers:
        raise Refused(f"#{number} stack membership could not be verified")
    index = numbers.index(number)
    if (
        info.get("position") != index + 1
        or members[index].get("head", {}).get("sha") != sha
    ):
        raise Refused(f"#{number} stack changed while it was read")
    for parent in members[:index]:
        if parent.get("state") != "closed" or not parent.get("merged_at"):
            return Scope(
                base,
                sha,
                stack,
                f"#{number} waits for #{parent['number']} below it to merge",
            )
        if not isinstance(parent["merged_at"], str):
            raise Refused(f"#{parent['number']} has no valid merge time")
    if stack.get("base", {}).get("ref") != base:
        return Scope(
            base, sha, stack, f"#{number} base is not yet the stack's base branch"
        )
    latest = max((p["merged_at"] for p in members[:index]), default=None)
    for parent in members[:index]:
        if parent["merged_at"] != latest:
            continue
        # The default API version exposes the squash commit; 2026-03-10 omits it.
        merged = _pull(repository, parent["number"], version=None)
        commit = merged.get("merge_commit_sha")
        if (
            merged.get("state") != "closed"
            or merged.get("merged") is not True
            or not isinstance(commit, str)
            or not ci._SHA.fullmatch(commit)
        ):
            raise Refused(f"#{parent['number']} has no verified merge commit")
        _, comparison = _request(repository, f"compare/{commit}...{sha}")
        behind = comparison.get("behind_by")
        if type(behind) is not int or behind < 0:
            raise Refused(f"could not verify #{number} contains {commit}")
        if behind:
            return Scope(
                base,
                sha,
                stack,
                f"#{number} head does not yet contain "
                f"#{parent['number']}'s merge {commit}",
            )
    return Scope(base, sha, stack)


def wait_stack(
    reader: Callable[[], Scope],
    now: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> Scope:
    """Wait up to ten minutes for parents, retargeting and the head rewrite."""
    deadline = now() + STACK_WAIT
    while True:
        scope = reader()
        if not scope.waiting:
            return scope
        if now() + STACK_INTERVAL > deadline:
            raise Refused(f"Timed out: {scope.waiting}")
        sleep(STACK_INTERVAL)


def _ready(snapshot: dict, sha: str) -> None:
    pr = snapshot["pr"]
    if pr["headSha"] != sha:
        raise Refused(f"#{pr['number']} head changed from {sha}; read its CI again")
    if pr["state"] != "OPEN" or pr["draft"]:
        raise Refused(f"#{pr['number']} is not an open, ready pull request")
    if snapshot["stale"] or snapshot["checksState"] != "passed":
        raise Refused(
            f"#{pr['number']} checks are {snapshot['checksState']}; merge not submitted"
        )
    if pr["mergeStateStatus"] not in ("CLEAN", "HAS_HOOKS", "UNSTABLE"):
        raise Refused(f"#{pr['number']} merge state is {pr['mergeStateStatus']}")


def _outcome(row: dict) -> tuple[str, dict]:
    status = row.get("status")
    details = row.get("details")
    if status not in ("pending", "merged", "enqueued", "failed"):
        raise Refused(f"GitHub returned unknown merge status {status!r}")
    if not isinstance(details, dict):
        raise Refused(f"GitHub returned no details for merge status {status!r}")
    if status == "merged" and not ci._SHA.fullmatch(str(details.get("sha", ""))):
        raise Refused("GitHub reported a merge without a commit SHA")
    return status, details


def _pending(details: dict, sha: str) -> str:
    uuid = details.get("uuid")
    if not isinstance(uuid, str) or not _UUID.fullmatch(uuid):
        raise Refused("GitHub returned no valid merge request UUID")
    if (
        details.get("expected_head_sha") != sha
        or details.get("merge_method") != "squash"
        or details.get("merge_action") != "default"
        or details.get("bypass_rules", False) is not False
    ):
        raise Refused(
            f"existing merge request {uuid} has different options; "
            "read the PR before retrying"
        )
    return uuid


def wait(
    reader: Callable[[], dict],
    now: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> dict:
    """Read one operation until it settles or the one-minute wait expires."""
    deadline = now() + WAIT
    while True:
        row = reader()
        status, _ = _outcome(row)
        if status != "pending" or now() + INTERVAL > deadline:
            return row
        sleep(INTERVAL)


def merge(number: int, sha: str, subject: str | None, body: str | None) -> dict:
    """Submit one merge after rechecking its head, CI and stack membership."""
    try:
        snapshot = ci.read(number)
    except ci.Unreadable as exc:
        raise Refused(str(exc)) from None
    pr = snapshot["pr"]
    repository = pr["repository"]
    selected = branch_forge.provider(R.remote())
    if selected is None or selected[0] != "gh":
        raise Refused("gw merge supports GitHub pull requests")
    scope = wait_stack(lambda: _scope(repository, number))
    if scope.sha != sha:
        if scope.stack is None:
            raise Refused(f"#{number} head changed from {sha}; read its CI again")
        return {
            "status": "head-moved",
            "pr": {**pr, "headSha": scope.sha, "baseRef": scope.base},
            "sha": scope.sha,
            "uuid": None,
            "message": (
                f"#{number} head moved to {scope.sha}; merge not submitted. "
                "Read its CI again."
            ),
        }
    try:
        snapshot = ci.read(number)
    except ci.Unreadable as exc:
        raise Refused(str(exc)) from None
    pr = snapshot["pr"]
    _ready(snapshot, sha)
    checked = _scope(repository, number)
    if checked.waiting:
        raise Refused(checked.waiting)
    if scope.base != pr["baseRef"] or checked != scope:
        raise Refused(f"#{number} changed while it was read; read its CI again")
    payload = {"sha": sha, "merge_method": "squash", "merge_action": "default"}
    if subject is not None:
        payload["commit_title"] = subject
    if body is not None:
        payload["commit_message"] = body
    path = f"pulls/{number}/merge-async"
    uuid = ""
    try:
        code, row = _request(repository, path, payload)
        status, details = _outcome(row)
        if code == 409 and status != "pending":
            raise Refused("existing merge request has no options to verify")
        if code == 409 and (subject is not None or body is not None):
            raise Refused(
                "existing merge request does not expose its commit message; "
                "it cannot be verified"
            )
        if status == "pending":
            uuid = _pending(details, sha)

            def reader() -> dict:
                _, response = _request(repository, f"{path}/{uuid}")
                state, data = _outcome(response)
                if state == "pending" and _pending(data, sha) != uuid:
                    raise Refused("GitHub returned another merge request UUID")
                return response

            row = wait(reader)
            status, details = _outcome(row)
    except Refused as exc:
        operation = f" (request {uuid})" if uuid else ""
        raise Refused(
            f"{exc}\nThe merge of #{number}{operation} may still complete. "
            "Read the pull request before submitting another merge."
        ) from None
    message = str(details.get("message") or "")
    if status == "pending":
        message = (
            "Merge still pending after the polling window; it may still complete. "
            "Read the PR before retrying."
        )
    return {
        "status": status,
        "pr": pr,
        "sha": details.get("sha"),
        "uuid": uuid or None,
        "message": message,
    }
