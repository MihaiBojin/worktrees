"""One authorized PR per merge, through the CLI and a stand-in GitHub API."""

from __future__ import annotations

import json
import re
import shlex
import subprocess
from pathlib import Path

import pytest
from conftest import argv
from test_branches import cli
from test_ci import BASE, HEAD, MERGE, _pr
from test_sync import origin

PR = "repos/owner/repo/pulls/7"
STACK = "repos/owner/repo/stacks/5"
ASYNC = PR + "/merge-async"
UUID = "630b9d5e-3f2a-4f7e-8b0c-2d5f9a8c1e42"
OPERATION = ASYNC + "/" + UUID
PARENT = "repos/owner/repo/pulls/6"
COMPARE = f"repos/owner/repo/compare/{MERGE}...{HEAD}"
REWRITE = "d" * 40


def pull(**extra) -> dict:
    return {
        "number": 7,
        "html_url": "https://github.com/owner/repo/pull/7",
        "state": "open",
        "draft": False,
        "merged": False,
        "head": {"ref": "feature", "sha": HEAD},
        "base": {"ref": "main", "repo": {"full_name": "owner/repo"}},
        **extra,
    }


def stack_info(**extra) -> dict:
    return {"id": 100, "number": 5, "position": 1, "size": 2, **extra}


def member(number=7, sha=HEAD, **extra) -> dict:
    return {
        "number": number,
        "state": "open",
        "draft": False,
        "merged_at": None,
        "head": {"ref": "feature", "sha": sha},
        **extra,
    }


def stack(members=None, **extra) -> dict:
    return {
        "id": 100,
        "number": 5,
        "base": {"ref": "main"},
        "pull_requests": members or [member(), member(8, BASE)],
        **extra,
    }


def pending(**extra) -> dict:
    return {
        "status": "pending",
        "details": {
            "uuid": UUID,
            "expected_head_sha": HEAD,
            "merge_method": "squash",
            "merge_action": "default",
            "bypass_rules": False,
            **extra,
        },
    }


def merged() -> dict:
    return {"status": "merged", "details": {"sha": MERGE}}


@pytest.fixture
def github(world, network):
    origin(world)
    network.answer({"$view": _pr(), "$api": {PR: pull(), ASYNC: merged()}})
    return network


def run(world, github, *args, setup="", **env) -> subprocess.CompletedProcess[str]:
    command = argv("gw", "merge", "7", "--match-head-commit", HEAD, "--json", *args)
    command[2] = (
        "from worktrees import pr_merge; pr_merge.STACK_WAIT = 0; " + setup + command[2]
    )
    return subprocess.run(
        command,
        cwd=world.repo,
        env=github.env(**env),
        text=True,
        capture_output=True,
        stdin=subprocess.DEVNULL,
    )


def writes(github) -> list[list[str]]:
    return [c for c in github.network_calls() if "PUT" in c]


def answer(github, **routes) -> None:
    github.answer({"$view": _pr(), "$api": {PR: pull(), ASYNC: merged(), **routes}})


def test_merge_pins_head_and_preserves_message_bytes(world, github) -> None:
    body = "Two paragraphs.\n\nCo-Authored-By: Claude\nCodex-Session: abc\n\n"
    path = world.root / "body with spaces.txt"
    path.write_text(body)
    proc = run(
        world,
        github,
        "--subject",
        "A title (#7)",
        "--body-file",
        str(path),
        GH_REPO="else/where",
        WORKTREES_SYNC_TTL="0",
    )
    assert proc.returncode == 0, proc.stderr
    result = json.loads(proc.stdout)
    assert result["status"] == "merged"
    assert result["sha"] == MERGE
    assert result["pr"]["number"] == 7
    assert set(result) >= {"head", "lastSync"}
    calls = writes(github)
    assert len(calls) == 1
    assert calls[0][:3] == ["gh", "api", ASYNC]
    fields = [
        calls[0][i + 1] for i, word in enumerate(calls[0]) if word == "--raw-field"
    ]
    assert set(fields) == {
        "sha=" + HEAD,
        "merge_method=squash",
        "merge_action=default",
        "commit_title=A title (#7)",
        "commit_message=" + body,
    }
    assert calls[0][calls[0].index("--hostname") + 1] == "github.com"
    assert not any(c[:2] == ["git", "fetch"] for c in github.network_calls())


def test_native_bottom_pr_merges_without_merging_the_child(world, github) -> None:
    answer(github, **{PR: pull(stack=stack_info()), STACK: stack()})
    proc = run(world, github)
    assert proc.returncode == 0, proc.stderr
    assert len(writes(github)) == 1
    assert writes(github)[0][2] == ASYNC


def test_open_parent_blocks_before_submission(world, github) -> None:
    answer(
        github,
        **{
            PR: pull(stack=stack_info(position=2)),
            STACK: stack([member(6, BASE), member()]),
        },
    )
    proc = run(world, github)
    assert proc.returncode == 3
    assert "#6" in proc.stderr
    assert not writes(github)


def test_merged_parent_allows_next_pr(world, github) -> None:
    answer(
        github,
        **{
            PARENT: pull(number=6, state="closed", merged=True, merge_commit_sha=MERGE),
            COMPARE: {"behind_by": 0},
            PR: pull(stack=stack_info(position=2)),
            STACK: stack(
                [
                    member(6, BASE, state="closed", merged_at="2026-11-12T12:00:00Z"),
                    member(),
                ]
            ),
        },
    )
    proc = run(world, github)
    assert proc.returncode == 0, proc.stderr


@pytest.mark.parametrize(
    "changed",
    [
        pull(head={"sha": BASE, "ref": "feature"}),
        pull(base={"ref": "other", "repo": {"full_name": "owner/repo"}}),
    ],
)
def test_changed_pr_refuses_before_submission(world, github, changed) -> None:
    answer(github, **{PR: {"$sequence": [pull(), changed]}})
    proc = run(world, github)
    assert proc.returncode == 3
    assert not writes(github)


@pytest.mark.parametrize(
    "snapshot",
    [
        _pr(headRefOid=BASE),
        _pr(state="CLOSED"),
        _pr(isDraft=True),
        _pr(mergeStateStatus="UNKNOWN"),
        _pr(statusCheckRollup=[]),
    ],
)
def test_requires_ready_ci_at_the_supplied_head(world, github, snapshot) -> None:
    github.answer({"$view": snapshot, "$api": {PR: pull(), ASYNC: merged()}})
    proc = run(world, github)
    assert proc.returncode == 3
    assert not writes(github)


@pytest.mark.parametrize(
    "response,expected,code",
    [
        (merged(), "merged", 0),
        ({"status": "enqueued", "details": {"message": "Queued"}}, "enqueued", 0),
        (
            {"status": "failed", "details": {"message": "Required review missing"}},
            "failed",
            1,
        ),
    ],
)
@pytest.mark.parametrize("poll", [False, True])
def test_each_terminal_outcome(world, github, response, expected, code, poll) -> None:
    answer(
        github,
        **{
            ASYNC: {"$http": 202, "$body": pending()} if poll else response,
            OPERATION: response,
        },
    )
    proc = run(world, github)
    assert proc.returncode == code, proc.stderr
    assert json.loads(proc.stdout)["status"] == expected
    assert len(writes(github)) == 1
    if expected == "failed":
        assert "Required review missing" in proc.stderr


def test_existing_matching_request_is_followed(world, github) -> None:
    answer(github, **{ASYNC: {"$http": 409, "$body": pending()}, OPERATION: merged()})
    proc = run(world, github)
    assert proc.returncode == 0, proc.stderr
    assert json.loads(proc.stdout)["uuid"] == UUID
    assert len(writes(github)) == 1


@pytest.mark.parametrize(
    "change",
    [
        {"expected_head_sha": BASE},
        {"merge_method": "merge"},
        {"merge_action": "direct_merge"},
        {"bypass_rules": True},
    ],
)
def test_existing_request_with_other_options_is_not_adopted(
    world, github, change
) -> None:
    answer(
        github,
        **{ASYNC: {"$http": 409, "$body": pending(**change)}, OPERATION: merged()},
    )
    proc = run(world, github)
    assert proc.returncode == 3
    assert "existing" in proc.stderr.lower()
    assert not any(OPERATION in c for c in github.network_calls())


@pytest.mark.parametrize(
    "route,response",
    [
        (PR, {"$http": 403, "$body": {"message": "Forbidden"}}),
        (ASYNC, {"status": "new-state", "details": {}}),
        (ASYNC, pending(uuid="../../elsewhere")),
        (ASYNC, {"$http": 403, "$body": {"message": "Forbidden"}}),
        (OPERATION, {"status": "new-state", "details": {}}),
    ],
)
def test_unreadable_or_unrecognised_answer_refuses_without_retry(
    world, github, route, response
) -> None:
    answer(
        github,
        **{
            ASYNC: {"$http": 202, "$body": pending()},
            OPERATION: merged(),
            route: response,
        },
    )
    proc = run(world, github)
    assert proc.returncode == 3
    assert proc.stderr
    assert len(writes(github)) <= 1


def test_offline_refuses_without_network(world, github) -> None:
    proc = run(world, github, WORKTREES_OFFLINE="1")
    assert proc.returncode == 3
    assert not github.network_calls()


@pytest.mark.parametrize(
    "args",
    [
        [],
        ["7"],
        ["7", "--match-head-commit", "short"],
        *[
            ["7", flag, "--match-head-commit", HEAD]
            for flag in ("--squash", "--merge", "--rebase")
        ],
        ["7", "--admin"],
        ["7", "--delete-branch"],
    ],
)
def test_invalid_merge_arguments_do_not_reach_github(world, github, args) -> None:
    proc = cli(world, "merge", *args, env=github.env())
    assert proc.returncode == 2
    assert not github.network_calls()


@pytest.mark.parametrize(
    "row",
    [
        pull(base={"repo": None}),
        pull(stack={}),
        pull(stack=stack_info(id=None)),
        pull(stack=False),
    ],
)
def test_malformed_pull_refuses_without_a_traceback(world, github, row) -> None:
    answer(github, **{PR: row, STACK: stack()})
    proc = run(world, github)
    assert proc.returncode == 3
    assert "Traceback" not in proc.stderr
    assert not writes(github)


@pytest.mark.parametrize(
    "row",
    [
        stack([member(head=None), member(8, BASE)]),
        stack(base=None),
        stack([member(), member()]),
        stack([member(6, BASE), member(8, BASE)]),
    ],
)
def test_malformed_stack_refuses_without_a_traceback(world, github, row) -> None:
    answer(github, **{PR: pull(stack=stack_info()), STACK: row})
    proc = run(world, github)
    assert proc.returncode == 3
    assert "Traceback" not in proc.stderr
    assert not writes(github)


def test_stack_change_during_reads_refuses(world, github) -> None:
    answer(
        github,
        **{
            PR: pull(stack=stack_info()),
            STACK: {"$sequence": [stack(), stack([member(), member(9, BASE)])]},
        },
    )
    proc = run(world, github)
    assert proc.returncode == 3
    assert not writes(github)


def test_wait_has_a_deadline_and_keeps_pending_distinct_from_queued() -> None:
    from worktrees import pr_merge

    clock = [0.0]
    reads = []

    def reader() -> dict:
        reads.append(clock[0])
        return pending()

    def sleep(seconds: float) -> None:
        clock[0] += seconds
        assert clock[0] <= 60

    result = pr_merge.wait(reader, now=lambda: clock[0], sleep=sleep)
    assert result["status"] == "pending"
    assert result["details"]["uuid"] == UUID
    assert len(reads) > 1
    assert clock[0] == 60


def test_wait_stops_when_the_operation_enters_the_queue() -> None:
    from worktrees import pr_merge

    rows = iter([pending(), {"status": "enqueued", "details": {}}])
    sleeps: list[float] = []
    result = pr_merge.wait(lambda: next(rows), now=lambda: 0, sleep=sleeps.append)
    assert result["status"] == "enqueued"
    assert len(sleeps) == 1


def test_ship_it_command_executes_a_native_stack_merge(world, github) -> None:
    skill = Path(__file__).resolve().parents[1] / "plugin/skills/ship-it/SKILL.md"
    command = re.search(r"^gw merge NUMBER .+$", skill.read_text(), re.M)
    assert command, "ship-it must supply a runnable gw merge command to both clients"
    body = world.root / "message.md"
    body.write_text("Co-Authored-By: Claude\nCodex-Session: abc\n")
    values = {
        "NUMBER": "7",
        "HEAD_SHA": HEAD,
        "SUBJECT": "Fix parser (#7)",
        "FILE": str(body),
    }
    args = [values.get(word, word) for word in shlex.split(command[0])[1:]]
    answer(github, **{PR: pull(stack=stack_info()), STACK: stack()})
    proc = cli(world, *args, env=github.env())
    assert proc.returncode == 0, proc.stderr
    assert json.loads(proc.stdout)["status"] == "merged"
    assert len(writes(github)) == 1


def test_existing_request_cannot_verify_a_custom_commit_message(world, github) -> None:
    answer(github, **{ASYNC: {"$http": 409, "$body": pending()}, OPERATION: merged()})
    proc = run(world, github, "--subject", "Prepared (#7)")
    assert proc.returncode == 3
    assert "message" in proc.stderr
    assert not any(OPERATION in c for c in github.network_calls())


def test_repository_identity_ignores_github_name_casing(world, github) -> None:
    answer(
        github, **{PR: pull(base={"ref": "main", "repo": {"full_name": "Owner/Repo"}})}
    )
    proc = run(world, github)
    assert proc.returncode == 0, proc.stderr
    assert len(writes(github)) == 1


def test_pending_deadline_keeps_the_operation_identity(world, github) -> None:
    answer(
        github,
        **{ASYNC: {"$http": 202, "$body": pending()}, OPERATION: pending()},
    )
    command = argv("gw", "merge", "7", "--match-head-commit", HEAD, "--json")
    command[2] = "from worktrees import pr_merge; pr_merge.WAIT = 0; " + command[2]
    proc = subprocess.run(
        command,
        cwd=world.repo,
        env=github.env(),
        text=True,
        capture_output=True,
        stdin=subprocess.DEVNULL,
    )
    assert proc.returncode == 1, proc.stderr
    result = json.loads(proc.stdout)
    assert result["status"] == "pending"
    assert result["uuid"] == UUID
    assert "may still complete" in result["message"]
    assert len(writes(github)) == 1


def test_lost_operation_response_reports_uncertainty_without_resubmission(
    world, github
) -> None:
    answer(
        github,
        **{ASYNC: {"$http": 202, "$body": pending()}, OPERATION: 1},
    )
    proc = run(world, github)
    assert proc.returncode == 3
    assert UUID in proc.stderr
    assert "may still complete" in proc.stderr
    assert len(writes(github)) == 1


@pytest.mark.parametrize("poll", [False, True])
def test_http_400_failed_is_an_outcome(world, github, poll) -> None:
    failure = {
        "$http": 400,
        "$body": {
            "status": "failed",
            "details": {"message": "Required review missing"},
        },
    }
    answer(
        github,
        **{
            ASYNC: {"$http": 202, "$body": pending()} if poll else failure,
            OPERATION: failure,
        },
    )
    proc = run(world, github)
    assert proc.returncode == 1, proc.stderr
    result = json.loads(proc.stdout)
    assert result["status"] == "failed"
    assert result["message"] == "Required review missing"
    assert len(writes(github)) == 1


def test_merge_submits_without_json(world, github) -> None:
    proc = cli(world, "merge", "7", "--match-head-commit", HEAD, env=github.env())
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == f"#7: merged {MERGE}"
    assert "merge_method=squash" in writes(github)[0]


def settled_stack(sha=HEAD) -> dict:
    return stack(
        [
            member(6, BASE, state="closed", merged_at="2026-11-12T12:00:00Z"),
            member(sha=sha),
        ]
    )


def stack_answer(github, **routes) -> None:
    answer(
        github,
        **{
            PR: pull(stack=stack_info(position=2)),
            STACK: settled_stack(),
            PARENT: pull(number=6, state="closed", merged=True, merge_commit_sha=MERGE),
            COMPARE: {"behind_by": 0},
            **routes,
        },
    )


@pytest.mark.parametrize("unsettled", ["parent", "base", "containment"])
def test_stack_waits_until_each_condition_settles(world, github, unsettled) -> None:
    routes = {
        "parent": {
            STACK: {"$sequence": [stack([member(6, BASE), member()]), settled_stack()]}
        },
        "base": {
            PR: {
                "$sequence": [
                    pull(
                        stack=stack_info(position=2),
                        base={"ref": "parent", "repo": {"full_name": "owner/repo"}},
                    ),
                    pull(stack=stack_info(position=2)),
                ]
            }
        },
        "containment": {COMPARE: {"$sequence": [{"behind_by": 1}, {"behind_by": 0}]}},
    }
    stack_answer(github, **routes[unsettled])
    proc = run(
        world, github, setup="pr_merge.STACK_WAIT = 600; pr_merge.STACK_INTERVAL = 0; "
    )
    assert proc.returncode == 0, proc.stderr
    calls = github.network_calls()
    assert len([c for c in calls if STACK in c]) >= 3
    parent_calls = [c for c in calls if PARENT in c]
    assert parent_calls
    assert all("--header" not in c for c in parent_calls)
    assert len(writes(github)) == 1


@pytest.mark.parametrize(
    "unsettled,reason",
    [
        ("parent", "#6"),
        ("base", "base branch"),
        ("containment", MERGE),
    ],
)
def test_stack_timeout_names_the_unmet_condition(
    world, github, unsettled, reason
) -> None:
    routes = {
        "parent": {STACK: stack([member(6, BASE), member()])},
        "base": {
            PR: pull(
                stack=stack_info(position=2),
                base={"ref": "parent", "repo": {"full_name": "owner/repo"}},
            )
        },
        "containment": {COMPARE: {"behind_by": 1}},
    }
    stack_answer(github, **routes[unsettled])
    proc = run(world, github)
    assert proc.returncode == 3
    assert "timed out" in proc.stderr.lower()
    assert reason in proc.stderr
    assert not writes(github)


def test_stack_rewrite_returns_the_new_head_without_submitting(world, github) -> None:
    rewritten = pull(
        stack=stack_info(position=2), head={"sha": REWRITE, "ref": "feature"}
    )
    stack_answer(
        github,
        **{
            PR: {"$sequence": [pull(stack=stack_info(position=2)), rewritten]},
            STACK: {"$sequence": [settled_stack(), settled_stack(REWRITE)]},
            COMPARE: {"behind_by": 1},
            f"repos/owner/repo/compare/{MERGE}...{REWRITE}": {"behind_by": 0},
        },
    )
    proc = run(
        world, github, setup="pr_merge.STACK_WAIT = 600; pr_merge.STACK_INTERVAL = 0; "
    )
    assert proc.returncode == 4, proc.stderr
    result = json.loads(proc.stdout)
    assert result["status"] == "head-moved"
    assert result["sha"] == REWRITE
    assert result["pr"]["headSha"] == REWRITE
    assert result["pr"]["baseRef"] == "main"
    assert result["uuid"] is None
    assert REWRITE in proc.stderr
    assert not writes(github)


def test_submit_rechecks_containment(world, github) -> None:
    stack_answer(
        github, **{COMPARE: {"$sequence": [{"behind_by": 0}, {"behind_by": 1}]}}
    )
    proc = run(world, github)
    assert proc.returncode == 3
    assert MERGE in proc.stderr
    assert not writes(github)


def test_latest_merged_parent_is_the_containment_base(world, github) -> None:
    stack_answer(
        github,
        **{
            PR: pull(stack=stack_info(position=3, size=3)),
            STACK: stack(
                [
                    member(6, BASE, state="closed", merged_at="2026-11-12T12:01:00Z"),
                    member(5, BASE, state="closed", merged_at="2026-11-12T12:00:00Z"),
                    member(),
                ]
            ),
        },
    )
    proc = run(world, github)
    assert proc.returncode == 0, proc.stderr
    assert len(writes(github)) == 1
    assert any(COMPARE in c for c in github.network_calls())


def test_stack_wait_has_a_ten_minute_deadline() -> None:
    from worktrees import pr_merge
    from worktrees.errors import Refused

    clock = [0.0]
    reads = []

    def reader():
        reads.append(clock[0])
        return pr_merge.Scope("main", HEAD, {}, "#7 waits for #6 below it to merge")

    def sleep(seconds):
        assert seconds == 5
        clock[0] += seconds
        assert clock[0] <= 600

    with pytest.raises(Refused, match="#6"):
        pr_merge.wait_stack(reader, now=lambda: clock[0], sleep=sleep)
    assert clock[0] == 600
    assert len(reads) == 121


def test_stack_wait_returns_a_settled_read_without_another_sleep() -> None:
    from worktrees import pr_merge

    settled = pr_merge.Scope("main", HEAD, {})
    rows = iter([pr_merge.Scope("parent", HEAD, {}, "waiting for base"), settled])
    sleeps: list[float] = []
    result = pr_merge.wait_stack(lambda: next(rows), now=lambda: 0, sleep=sleeps.append)
    assert result == settled
    assert sleeps == [5]


@pytest.mark.parametrize("behind", [0, 1])
def test_parents_merged_in_the_same_second_both_need_containment(
    world, github, behind
) -> None:
    earlier = "e" * 40
    stack_answer(
        github,
        **{
            PR: pull(stack=stack_info(position=3, size=3)),
            STACK: stack(
                [
                    member(5, BASE, state="closed", merged_at="2026-11-12T12:00:00Z"),
                    member(6, BASE, state="closed", merged_at="2026-11-12T12:00:00Z"),
                    member(),
                ]
            ),
            "repos/owner/repo/pulls/5": pull(
                number=5, state="closed", merged=True, merge_commit_sha=earlier
            ),
            f"repos/owner/repo/compare/{earlier}...{HEAD}": {"behind_by": 0},
            COMPARE: {"behind_by": behind},
        },
    )
    proc = run(world, github)
    if behind:
        assert proc.returncode == 3
        assert MERGE in proc.stderr
        assert not writes(github)
    else:
        assert proc.returncode == 0, proc.stderr
        assert len(writes(github)) == 1
    assert any(COMPARE in c for c in github.network_calls())
