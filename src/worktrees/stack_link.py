"""Link open GitHub pull requests into a native stack, bottom first."""

from __future__ import annotations

from itertools import pairwise

from . import branch_forge, forge
from . import repo as R
from .errors import Refused
from .pr_merge import _pull, _request, answer


def _member_of(row: dict) -> int:
    """The number of the stack a pull request is in, or 0."""
    info = row.get("stack")
    if info is None:
        return 0
    if not isinstance(info, dict) or type(info.get("number")) is not int:
        raise Refused(f"#{row['number']} has unreadable stack metadata")
    return info["number"]


def _members(repository: str, number: int) -> list[int]:
    _, stack = _request(repository, f"stacks/{number}")
    return _numbers(stack, number)


def _numbers(stack: dict, number: int | None = None) -> list[int]:
    members = stack.get("pull_requests")
    if (
        type(stack.get("number")) is not int
        or (number is not None and stack["number"] != number)
        or not isinstance(members, list)
        or not all(
            isinstance(p, dict) and type(p.get("number")) is int for p in members
        )
    ):
        raise Refused("GitHub returned a stack it could not describe")
    return [p["number"] for p in members]


def _post(repository: str, path: str, numbers: list[int], expected: int) -> dict:
    """POST an ordered list of pull requests, as GitHub's stack endpoints take
    it: `-F` sends each number as a JSON integer, and `[]` makes them an array."""
    host, project = repository.split("/", 1)
    argv = [
        "gh",
        "api",
        f"repos/{project}/{path}",
        "--hostname",
        host,
        "--include",
        "--method",
        "POST",
        "--header",
        "X-GitHub-Api-Version: 2026-03-10",
    ]
    for number in numbers:
        argv.extend(["--field", f"pull_requests[]={number}"])
    proc = forge._run(argv)
    if proc is None:
        raise Refused(f"could not read GitHub's answer for {path}")
    code, row = answer(proc, path)
    if code != expected or proc.returncode:
        reasons = [str(row.get("message") or f"GitHub HTTP {code}")]
        for error in row.get("errors") or []:
            text = error.get("message") if isinstance(error, dict) else error
            if isinstance(text, str) and text:
                reasons.append(text)
        raise Refused(f"GitHub refused {path}: " + "; ".join(reasons))
    return row


def link(numbers: list[int]) -> dict:
    """Create a stack of `numbers`, or add the ones above its top to the stack
    the first ones are already in; nothing when all of them are linked."""
    selected = branch_forge.provider(R.remote())
    if selected is None or selected[0] != "gh":
        raise Refused("gw stack-link supports GitHub pull requests")
    repository = selected[1]
    rows = [_pull(repository, number) for number in numbers]
    stacks = [_member_of(row) for row in rows]

    linked = 0
    while linked < len(stacks) and stacks[linked]:
        linked += 1
    current: list[int] = []
    if linked:
        stack = stacks[0]
        current = _members(repository, stack)
        if stacks == [stack] * len(stacks) and _run_of(numbers, current):
            return {
                "stack": stack,
                "pull_requests": current,
                "created": False,
                "added": [],
            }

    for row in rows:
        if row.get("state") != "open":
            raise Refused(f"#{row['number']} is closed; only open pull requests stack")
    for at, (number, other) in enumerate(zip(numbers, stacks, strict=True)):
        if at < linked and other != stacks[0]:
            raise Refused(f"#{number} is in stack {other}, not stack {stacks[0]}")
        if at > linked and other:
            raise Refused(
                f"#{number} is in stack {other}, above #{numbers[linked]}, "
                "which is in none"
            )
    if linked and current[-linked:] != numbers[:linked]:
        raise Refused(
            f"#{numbers[linked - 1]} is not the top of stack {stacks[0]}; "
            f"its top is #{current[-1]}, and pull requests join a stack only there"
        )

    if not linked:
        default = rows[0]["base"]["repo"].get("default_branch")
        if rows[0]["base"].get("ref") != default:
            raise Refused(
                f"#{numbers[0]} targets {rows[0]['base'].get('ref')}, not the "
                f"default branch {default}, so it cannot be a stack's bottom"
            )
    for below, row in pairwise(rows[max(linked - 1, 0) :]):
        head = below["head"].get("ref")
        if row["base"].get("ref") != head:
            raise Refused(
                f"#{row['number']} targets {row['base'].get('ref')}, not {head}, "
                f"the branch of #{below['number']} below it"
            )

    added = numbers[linked:]
    if linked:
        result = _post(repository, f"stacks/{stacks[0]}/add", added, 200)
        members = _numbers(result, stacks[0])
        verified = members[-len(numbers) :] == numbers
    else:
        result = _post(repository, "stacks", numbers, 201)
        members = _numbers(result)
        verified = members == numbers
    if not verified:
        raise Refused(
            f"GitHub answered with stack {result['number']} holding "
            + " ".join(f"#{n}" for n in members)
            + "; read it before linking again"
        )
    return {
        "stack": result["number"],
        "pull_requests": members,
        "created": not linked,
        "added": added,
    }


def _run_of(numbers: list[int], members: list[int]) -> bool:
    """`numbers` stand in `members` one after another, in that order."""
    for start in range(len(members) - len(numbers) + 1):
        if members[start : start + len(numbers)] == numbers:
            return True
    return False
