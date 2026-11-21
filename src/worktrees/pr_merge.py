"""Merge GitHub pull requests at checked heads, a native stack in one call.

GitHub's asynchronous merge of a stacked pull request merges every open one
below it too, so a stack lands in one call: the named pull request's head is
pinned by `sha`, and gw reads the head and CI of each one below it first.
"""

from __future__ import annotations

import json
import re
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass
from urllib.parse import quote

from . import branch_forge, ci, forge, sync
from . import repo as R
from .errors import Refused
from .git import git

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
        f"repos/{project}/{path}".rstrip("/"),
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
    code, row = answer(proc, path)
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


def answer(proc: subprocess.CompletedProcess[str], path: str) -> tuple[int, dict]:
    """The HTTP status and JSON object a `gh api --include` call printed."""
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
    return int(status[1]), row


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
    # The stack's open members below and above the pull request, bottom
    # first, each with the head GitHub reported for it.
    lower: tuple[tuple[int, str], ...] = ()
    above: tuple[tuple[int, str], ...] = ()


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
        _unfinished_base(repository, number, base, row)
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
    below = members[:index]
    count = 0
    while count < len(below) and below[count].get("merged_at"):
        count += 1
    finished, open_below = below[:count], below[count:]
    for parent in finished:
        if parent.get("state") != "closed" or not isinstance(parent["merged_at"], str):
            raise Refused(f"#{parent['number']} has no valid merge time")
    for parent in open_below:
        if (
            parent.get("state") != "open"
            or parent.get("merged_at")
            or not ci._SHA.fullmatch(str(parent["head"].get("sha")))
        ):
            raise Refused(f"#{parent['number']} below #{number} is not open")
    lower = tuple((p["number"], p["head"]["sha"]) for p in open_below)
    above = tuple(
        (p["number"], p["head"]["sha"])
        for p in members[index + 1 :]
        if p.get("state") == "open" and ci._SHA.fullmatch(str(p["head"].get("sha")))
    )
    # The lowest open member is the one a merge below it retargets and
    # rewrites, so it is the one whose base and head must have settled.
    bottom, bottom_sha, bottom_base = number, sha, base
    if lower:
        bottom, bottom_sha = lower[0]
        lowest = _pull(repository, bottom)
        bottom_base = lowest["base"].get("ref")
        if lowest["head"].get("sha") != bottom_sha:
            raise Refused(f"#{number} stack changed while it was read")

    def scope(waiting: str = "") -> Scope:
        return Scope(base, sha, stack, waiting, lower, above)

    if stack["base"]["ref"] != bottom_base:
        return scope(f"#{bottom} base is not yet the stack's base branch")
    latest = max((p["merged_at"] for p in finished), default=None)
    for parent in finished:
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
        _, comparison = _request(repository, f"compare/{commit}...{bottom_sha}")
        behind = comparison.get("behind_by")
        if type(behind) is not int or behind < 0:
            raise Refused(f"could not verify #{bottom} contains {commit}")
        if behind:
            return scope(
                f"#{bottom} head does not yet contain "
                f"#{parent['number']}'s merge {commit}"
            )
    return scope()


def _unfinished_base(repository: str, number: int, base: str, row: dict) -> None:
    """Refuse a pull request whose base is a branch a merged one left behind.

    GitHub moves a stacked pull request's base only when the merged branch
    is deleted. On a branch that still stands exactly where its own pull
    request merged it, this one would merge into it and never reach the
    default branch. A base that has moved on since is somebody's branch.
    """
    default = row["base"]["repo"].get("default_branch")
    if not isinstance(default, str) or not default or base == default:
        return
    host, project = repository.split("/", 1)
    _, branch = _request(repository, f"branches/{quote(base, safe='/')}")
    tip = (
        branch.get("commit", {}).get("sha")
        if isinstance(branch.get("commit"), dict)
        else None
    )
    owner = project.split("/", 1)[0]
    listed = forge.query(
        [
            "gh",
            "api",
            f"repos/{project}/pulls?head={quote(f'{owner}:{base}', safe='')}"
            "&state=closed&per_page=100",
            "--hostname",
            host,
        ]
    )
    if not isinstance(tip, str) or listed is None:
        raise Refused(f"could not read what merged from #{number}'s base {base}")
    for parent in listed:
        head = parent.get("head")
        if (
            parent.get("merged_at")
            and isinstance(head, dict)
            and head.get("sha") == tip
        ):
            source = row["head"].get("ref")
            raise Refused(
                f"#{number} targets {base}, which #{parent.get('number')} merged "
                f"into {default}; merged there, it would never reach {default}.\n"
                f"Run: gw fast-forward, in the checkout of {source}, which "
                f"moves its base to {default}"
            )


def wait_stack(
    reader: Callable[[], Scope],
    now: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> Scope:
    """Wait up to ten minutes for the retarget and head rewrite that a
    merge below the lowest open member brings."""
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
    # A review with no checks at all has no CI to wait for: the caller's
    # request to merge is the decision.
    if snapshot["stale"] or (
        snapshot["checks"] and snapshot["checksState"] != "passed"
    ):
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


def _snapshot(number: int) -> dict:
    try:
        return ci.read(number)
    except ci.Unreadable as exc:
        raise Refused(str(exc)) from None


def _squash_settings(repository: str, lower: tuple[tuple[int, str], ...]) -> list[str]:
    """A warning when the members merged with the named one will not take
    their own title and body: GitHub builds their messages from the
    repository's squash settings."""
    names = ", ".join(f"#{n}" for n, _ in lower)
    try:
        _, row = _request(repository, "", version=None)
    except Refused:
        row = {}
    title = row.get("squash_merge_commit_title")
    body = row.get("squash_merge_commit_message")
    if title == "PR_TITLE" and body == "PR_BODY":
        return []
    if not isinstance(title, str) or not isinstance(body, str):
        found = "GitHub did not report the repository's squash settings"
    else:
        found = f"the repository's squash settings are {title} and {body}"
    return [
        f"the squash commits of {names} will use GitHub's default message, "
        f"not their title and body: {found}.\n"
        "Run: gw preflight, which prints the command that sets them"
    ]


def _left(numbers: list[int], heads: dict[int, str]) -> tuple[list[dict], dict]:
    """Each open pull request still to merge, as GitHub reports it now, and
    the snapshots read."""
    rows: list[dict] = []
    snapshots: dict[int, dict] = {}
    for n in numbers:
        try:
            snapshot = ci.read(n)
        except ci.Unreadable:
            rows.append(
                {
                    "number": n,
                    "headSha": heads.get(n, ""),
                    "baseRef": "",
                    "mergeStateStatus": "UNKNOWN",
                    "checksState": "unknown",
                }
            )
            continue
        snapshots[n] = snapshot
        pr = snapshot["pr"]
        rows.append(
            {
                "number": n,
                "headSha": pr["headSha"],
                "baseRef": pr["baseRef"],
                "mergeStateStatus": pr["mergeStateStatus"],
                "checksState": snapshot["checksState"],
            }
        )
    return rows, snapshots


@git("update-ref refs/heads/$branch $new $old", ok=(0, 128), mutates=True)
def move_branch(branch: str, new: str, old: str) -> None:
    """Move a branch to `new` only while it stands at `old`."""


def _branches(
    repository: str,
    above: tuple[tuple[int, str], ...],
    snapshots: dict[int, dict],
    warnings: list[str],
) -> list[dict]:
    """Bring each local branch of the stack above the merge to the copy
    GitHub rewrote, where nothing would be lost.

    A branch moves only while it stands at the head GitHub had before the
    merge, so a commit made here and never pushed keeps it where it is. One
    checked out in a worktree is left to `gw fast-forward` there, which
    takes the rewritten copy with the checkout.
    """
    remote = R.remote()
    failures, notices = sync.fetch()
    warnings.extend(notices)
    if failures:
        warnings.extend(failures)
        warnings.append("the stack's local branches were not moved")
        return []
    project = repository.split("/", 1)[1].lower()
    held = {wt.branch: wt.path for wt in R.worktrees() if wt.branch}
    rows: list[dict] = []
    for number, before in above:
        snapshot = snapshots.get(number)
        if snapshot is None or snapshot["pr"]["source"].lower() != project:
            continue
        branch = snapshot["pr"]["headRef"]
        local = R.local_sha(f"refs/heads/{branch}").value
        after = R.local_sha(f"refs/remotes/{remote}/{branch}").value
        if not local or local == after:
            continue
        row = {
            "number": number,
            "branch": branch,
            "result": "kept",
            "from": local,
            "to": after,
            "path": "",
        }
        if branch in held:
            row.update(result="checked-out", path=held[branch])
        elif local == before and (not after or after == before):
            row["result"] = "not-rewritten"
        elif local == before and move_branch(branch, after, before):
            row["result"] = "moved"
        rows.append(row)
    return rows


def _landed(
    repository: str, number: int, member: int, head: str, warnings: list[str]
) -> tuple[bool, str | None]:
    """Whether a member below `number` merged with it, and its merge commit.

    A head other than the one gw checked is a warning, not a failure: the
    merge has already happened.
    """
    try:
        # The default API version exposes the squash commit; 2026-03-10 omits it.
        row = _pull(repository, member, version=None)
    except Refused as exc:
        warnings.append(f"#{member} could not be read after the merge: {exc}")
        return True, None
    if row.get("merged") is not True:
        warnings.append(
            f"#{member} below #{number} is not merged yet.\n"
            f"Run: gh pr view {member} --json state,mergeCommit,mergedAt"
        )
        return False, None
    landed = row["head"].get("sha")
    if landed != head:
        warnings.append(
            f"#{member} merged at {landed}, not at {head}, the head whose CI gw read"
        )
    commit = row.get("merge_commit_sha")
    return True, commit if isinstance(commit, str) and ci._SHA.fullmatch(
        commit
    ) else None


def merge(number: int, sha: str, subject: str | None, body: str | None) -> dict:
    """Submit one merge after rechecking its head, CI and stack membership.

    In a native stack the merge carries every open member below the named
    one, so each of those is read and checked first.
    """
    snapshot = _snapshot(number)
    pr = snapshot["pr"]
    repository = pr["repository"]
    selected = branch_forge.provider(R.remote())
    if selected is None or selected[0] != "gh":
        raise Refused("gw merge supports GitHub pull requests")
    scope = wait_stack(lambda: _scope(repository, number))
    lower = [n for n, _ in scope.lower]
    above = [n for n, _ in scope.above]
    heads = dict(scope.lower + scope.above)
    if scope.sha != sha:
        if scope.stack is None:
            raise Refused(f"#{number} head changed from {sha}; read its CI again")
        heads[number] = scope.sha
        remaining, _ = _left([*lower, number, *above], heads)
        return {
            "status": "head-moved",
            "pr": {**pr, "headSha": scope.sha, "baseRef": scope.base},
            "sha": scope.sha,
            "uuid": None,
            "message": (
                f"#{number} head moved to {scope.sha}; merge not submitted. "
                "Read its CI again."
            ),
            "merged": [],
            "remaining": remaining,
            "branches": [],
            "warnings": [],
        }
    snapshot = _snapshot(number)
    pr = snapshot["pr"]
    _ready(snapshot, sha)
    for member, head in scope.lower:
        _ready(_snapshot(member), head)
    warnings = _squash_settings(repository, scope.lower) if scope.lower else []
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
    done: list[dict] = []
    left = [*lower, number, *above]
    if status == "merged":
        left = [*above]
        late: list[int] = []
        for member, head in scope.lower:
            landed, commit = _landed(repository, number, member, head, warnings)
            if landed:
                done.append({"number": member, "sha": commit})
            else:
                late.append(member)
        done.append({"number": number, "sha": details["sha"]})
        left = [*late, *above]
    remaining, snapshots = _left(left, heads)
    branches = (
        _branches(repository, scope.above, snapshots, warnings)
        if status == "merged" and scope.above
        else []
    )
    return {
        "status": status,
        "pr": pr,
        "sha": details.get("sha"),
        "uuid": uuid or None,
        "message": message,
        "merged": done,
        "remaining": remaining,
        "branches": branches,
        "warnings": warnings,
    }


# --------------------------------------------------------------------------
# several pull requests
# --------------------------------------------------------------------------

_FOOTER = re.compile(r"\s*🤖 Generated with .*")
_SESSION = re.compile(r"\s*https://claude\.ai/code/session_\S+\s*")
_RESOLUTION = re.compile(
    r"<details>\s*<summary>Auto-resolution</summary>.*?</details>"
    r"[ \t]*(?:\n(?:[ \t]*\n)*|$)",
    re.S,
)
_TRAILER = re.compile(r"[A-Za-z0-9][A-Za-z0-9-]*: .+")


def message(number: int, title: str, body: str, trailers: list[str]) -> tuple[str, str]:
    """The squash subject and body GitHub would have written, without what
    belongs only on the review page, and with each trailer the commits
    carry, once."""
    subject = title if title.endswith(f"(#{number})") else f"{title} (#{number})"
    text = _RESOLUTION.sub("", body.replace("\r\n", "\n"))
    kept = [
        line
        for line in text.split("\n")
        if not _FOOTER.fullmatch(line) and not _SESSION.fullmatch(line)
    ]
    text = "\n".join(kept).strip()
    present = {line.strip() for line in text.split("\n")}
    extra = [t for t in trailers if t not in present]
    if extra:
        last = text.rsplit("\n\n", 1)[-1].split("\n") if text else []
        joint = "\n" if last and all(_TRAILER.fullmatch(x) for x in last) else "\n\n"
        text = (text + joint if text else "") + "\n".join(extra)
    return subject, text + "\n" if text else ""


def _targets(repository: str, numbers: list[int]) -> list[tuple[int, list[int]]]:
    """One merge call per native stack, on its highest member named, placed
    where the stack's first named member is; every other number its own.
    Each target comes with the named numbers its call covers."""
    stacks: dict[int, list[tuple[int, int]]] = {}
    order: list[int | tuple[str, int]] = []
    for n in numbers:
        info = _pull(repository, n).get("stack")
        if (
            isinstance(info, dict)
            and type(info.get("number")) is int
            and type(info.get("position")) is int
        ):
            if info["number"] not in stacks:
                order.append(("stack", info["number"]))
            stacks.setdefault(info["number"], []).append((info["position"], n))
        else:
            order.append(n)
    targets: list[tuple[int, list[int]]] = []
    for entry in order:
        if isinstance(entry, int):
            targets.append((entry, []))
            continue
        named = sorted(stacks[entry[1]])
        targets.append((named[-1][1], [n for _, n in named[:-1]]))
    return targets


def ship(numbers: list[int]) -> list[dict]:
    """Merge each named pull request in the order given, building each
    message from its title, body and trailers. The first one that does not
    merge stops the run, and nothing after it is submitted.

    Each result carries the `number` and `head` it was asked to merge.
    """
    selected = branch_forge.provider(R.remote())
    if selected is None or selected[0] != "gh":
        raise Refused("gw merge supports GitHub pull requests")
    repository = selected[1]
    targets = _targets(repository, numbers)
    results: list[dict] = []
    for i, (number, covered) in enumerate(targets):
        head = ""
        pr: dict = {"number": number}
        try:
            snapshot = _snapshot(number)
            pr = snapshot["pr"]
            head = pr["headSha"]
            trailers = pr["trailers"]
            if trailers is None:
                raise Refused(
                    f"#{number} commit list is incomplete, so its trailers "
                    "cannot be read; merge it alone with --subject and --body-file"
                )
            row = _pull(repository, number)
            title = row.get("title")
            body = row.get("body") or ""
            if not isinstance(title, str) or not isinstance(body, str):
                raise Refused(f"#{number} has no readable title and body")
            subject, text = message(number, title, body, trailers)
            result = merge(number, head, subject, text)
        except Refused as exc:
            remaining, _ = _left([*covered, number], {})
            result = {
                "status": "refused",
                "pr": pr,
                "sha": None,
                "uuid": None,
                "message": str(exc),
                "merged": [],
                "remaining": remaining,
                "branches": [],
                "warnings": [],
            }
        results.append({"number": number, "head": head, **result})
        if result["status"] != "merged":
            later = [n for target, c in targets[i + 1 :] for n in (*c, target)]
            rows, _ = _left(later, {})
            results[-1]["remaining"].extend(rows)
            break
    return results
