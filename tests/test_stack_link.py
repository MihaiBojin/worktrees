"""Open pull requests linked into a native stack, through a stand-in GitHub API."""

from __future__ import annotations

import json
import subprocess

import pytest
from conftest import argv
from test_sync import origin

STACKS = "repos/owner/repo/stacks"
STACK = STACKS + "/5"
ADD = STACK + "/add"


def pull(number: int, base: str, head: str, **extra) -> dict:
    return {
        "number": number,
        "state": "open",
        "draft": False,
        "head": {"ref": head, "sha": str(number) * 40},
        "base": {
            "ref": base,
            "repo": {"full_name": "owner/repo", "default_branch": "main"},
        },
        **extra,
    }


def inside(number: int, position: int, size: int) -> dict:
    return {"id": 100, "number": number, "position": position, "size": size}


def stack(*numbers: int, number: int = 5) -> dict:
    return {
        "id": 100,
        "number": number,
        "base": {"ref": "main"},
        "pull_requests": [{"number": n, "state": "open"} for n in numbers],
    }


# 7 on main, 8 on 7's branch, 9 on 8's.
PULLS = {
    7: pull(7, "main", "one"),
    8: pull(8, "one", "two"),
    9: pull(9, "two", "three"),
}


def routes(*changed: dict, **paths) -> dict:
    pulls = {**PULLS, **{row["number"]: row for row in changed}}
    return {
        "$api": {
            **{f"repos/owner/repo/pulls/{n}": row for n, row in pulls.items()},
            **paths,
        }
    }


@pytest.fixture
def github(world, network):
    origin(world)
    return network


def run(world, github, *args, **env) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        argv("gw", "stack-link", *args),
        cwd=world.repo,
        env=github.env(**env),
        text=True,
        capture_output=True,
        stdin=subprocess.DEVNULL,
    )


def posts(github) -> list[list[str]]:
    return [c for c in github.network_calls() if "POST" in c]


def fields(call: list[str]) -> list[str]:
    return [call[i + 1] for i, word in enumerate(call) if word == "--field"]


def test_unlinked_pull_requests_make_a_new_stack(world, github) -> None:
    github.answer(routes(**{STACKS: {"$http": 201, "$body": stack(7, 8, 9)}}))
    proc = run(world, github, "7", "8", "9", "--json")
    assert proc.returncode == 0, proc.stderr
    result = json.loads(proc.stdout)
    assert {k: result[k] for k in ("stack", "pull_requests", "created", "added")} == {
        "stack": 5,
        "pull_requests": [7, 8, 9],
        "created": True,
        "added": [7, 8, 9],
    }
    assert set(result) >= {"head", "lastSync"}
    [call] = posts(github)
    assert call[:3] == ["gh", "api", STACKS]
    assert fields(call) == [f"pull_requests[]={n}" for n in (7, 8, 9)]
    assert "X-GitHub-Api-Version: 2026-03-10" in call


def test_pull_requests_above_a_stack_are_added_on_top(world, github) -> None:
    github.answer(
        routes(
            pull(7, "main", "one", stack=inside(5, 1, 2)),
            pull(8, "one", "two", stack=inside(5, 2, 2)),
            **{STACK: stack(7, 8), ADD: stack(7, 8, 9)},
        )
    )
    proc = run(world, github, "8", "9")
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout == "stack 5: #7 #8 #9 (added #9)\n"
    [call] = posts(github)
    assert call[:3] == ["gh", "api", ADD]
    assert fields(call) == ["pull_requests[]=9"]


def test_a_linked_stack_is_left_as_it_is(world, github) -> None:
    github.answer(
        routes(
            pull(7, "main", "one", stack=inside(5, 1, 2), state="closed"),
            pull(8, "one", "two", stack=inside(5, 2, 2)),
            **{STACK: stack(7, 8)},
        )
    )
    proc = run(world, github, "7", "8", "--json")
    assert proc.returncode == 0, proc.stderr
    result = json.loads(proc.stdout)
    assert (result["created"], result["added"]) == (False, [])
    assert not posts(github)


def test_a_base_that_is_not_the_branch_below_refuses(world, github) -> None:
    github.answer(routes(pull(8, "main", "two")))
    proc = run(world, github, "7", "8")
    assert proc.returncode == 3
    assert "#8 targets main, not one, the branch of #7 below it" in proc.stderr
    assert not posts(github)


def test_a_bottom_off_the_default_branch_refuses(world, github) -> None:
    github.answer(routes(pull(7, "elsewhere", "one")))
    proc = run(world, github, "7", "8")
    assert proc.returncode == 3
    assert "not the default branch main" in proc.stderr
    assert not posts(github)


def test_a_pull_request_in_another_stack_refuses(world, github) -> None:
    github.answer(
        routes(
            pull(7, "main", "one", stack=inside(5, 1, 1)),
            pull(8, "one", "two", stack=inside(6, 1, 1)),
            **{STACK: stack(7)},
        )
    )
    proc = run(world, github, "7", "8")
    assert proc.returncode == 3
    assert "#8 is in stack 6, not stack 5" in proc.stderr
    assert not posts(github)


def test_a_stack_joined_below_its_top_refuses(world, github) -> None:
    github.answer(
        routes(
            pull(7, "main", "one", stack=inside(5, 1, 2)),
            **{STACK: stack(7, 8)},
        )
    )
    proc = run(world, github, "7", "9")
    assert proc.returncode == 3
    assert "#7 is not the top of stack 5; its top is #8" in proc.stderr
    assert not posts(github)


def test_a_closed_pull_request_refuses(world, github) -> None:
    github.answer(routes(pull(8, "one", "two", state="closed")))
    proc = run(world, github, "7", "8")
    assert proc.returncode == 3
    assert "#8 is closed" in proc.stderr
    assert not posts(github)


def test_github_refusing_the_stack_is_a_refusal(world, github) -> None:
    refused = {"message": "Validation Failed", "errors": ["cannot form a stack"]}
    github.answer(routes(**{STACKS: {"$http": 422, "$body": refused}}))
    proc = run(world, github, "7", "8")
    assert proc.returncode == 3
    assert "Validation Failed; cannot form a stack" in proc.stderr


def test_gitlab_refuses(world, network) -> None:
    origin(world, host="gitlab.com")
    proc = run(world, network, "7", "8")
    assert proc.returncode == 3
    assert "gw stack-link supports GitHub pull requests" in proc.stderr
    assert not network.network_calls()


def test_offline_refuses(world, github) -> None:
    proc = run(world, github, "7", "8", WORKTREES_OFFLINE="1")
    assert proc.returncode == 3
    assert "WORKTREES_OFFLINE" in proc.stderr
    assert not github.network_calls()


@pytest.mark.parametrize("numbers", [["7"], ["7", "8", "7"]])
def test_one_or_a_repeated_pull_request_is_a_usage_error(
    world, github, numbers
) -> None:
    proc = run(world, github, *numbers)
    assert proc.returncode == 2
    assert not github.network_calls()


def test_a_stacked_pull_request_above_an_unstacked_one_refuses(world, github) -> None:
    github.answer(routes(pull(8, "one", "two", stack=inside(6, 1, 1))))
    proc = run(world, github, "7", "8")
    assert proc.returncode == 3
    assert "#8 is in stack 6, above #7, which is in none" in proc.stderr
    assert not posts(github)
