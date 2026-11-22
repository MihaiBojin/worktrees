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
from test_ci import BASE, HEAD, MERGE, _check, _pr
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
        "title": "Seven",
        "body": "Body.",
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


@pytest.mark.parametrize("subject", [None, "Custom (#7)"])
@pytest.mark.parametrize("body", [None, "", "Override body.\n\n"])
def test_single_merge_fills_omitted_message_fields(
    world, github, subject: str | None, body: str | None
) -> None:
    commits = [
        {
            "oid": HEAD,
            "messageHeadline": "Change",
            "messageBody": f"{TRAILER}\nCodex-Session: abc",
        }
    ]
    github.answer(
        {"$view": _pr(commits=commits), "$api": {PR: pull(), ASYNC: merged()}}
    )
    args = []
    if subject is not None:
        args.extend(["--subject", subject])
    if body is not None:
        path = world.root / "message.txt"
        path.write_text(body)
        args.extend(["--body-file", str(path)])
    proc = run(world, github, *args)
    assert proc.returncode == 0, proc.stderr
    calls = writes(github)
    assert len(calls) == 1
    expected_title = "Seven (#7)" if subject is None else "Custom (#7)"
    expected_body = (
        f"Body.\n\n{TRAILER}\nCodex-Session: abc\n" if body is None else body
    )
    assert "sha=" + HEAD in calls[0]
    assert "commit_title=" + expected_title in calls[0]
    assert "commit_message=" + expected_body in calls[0]


def test_automatic_body_refuses_incomplete_trailers(world, github) -> None:
    github.answer({"$view": _pr(commits=None), "$api": {PR: pull(), ASYNC: merged()}})
    proc = run(world, github)
    assert proc.returncode == 3
    assert "commit list is incomplete" in proc.stderr
    assert not writes(github)


def test_explicit_body_can_merge_with_incomplete_trailers(world, github) -> None:
    github.answer({"$view": _pr(commits=None), "$api": {PR: pull(), ASYNC: merged()}})
    path = world.root / "message.txt"
    path.write_text("Provided body.\n")
    proc = run(world, github, "--body-file", str(path))
    assert proc.returncode == 0, proc.stderr
    calls = writes(github)
    assert len(calls) == 1
    assert "commit_title=Seven (#7)" in calls[0]
    assert "commit_message=Provided body.\n" in calls[0]


def test_native_bottom_pr_merges_without_merging_the_child(world, github) -> None:
    answer(github, **{PR: pull(stack=stack_info()), STACK: stack()})
    proc = run(world, github)
    assert proc.returncode == 0, proc.stderr
    assert len(writes(github)) == 1
    assert writes(github)[0][2] == ASYNC


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
        _pr(statusCheckRollup=[_check(conclusion="FAILURE")]),
        _pr(statusCheckRollup=[_check(status="IN_PROGRESS", conclusion="")]),
    ],
)
def test_requires_ready_ci_at_the_supplied_head(world, github, snapshot) -> None:
    github.answer({"$view": snapshot, "$api": {PR: pull(), ASYNC: merged()}})
    proc = run(world, github)
    assert proc.returncode == 3
    assert not writes(github)


def test_a_review_without_checks_merges(world, github) -> None:
    github.answer(
        {"$view": _pr(statusCheckRollup=[]), "$api": {PR: pull(), ASYNC: merged()}}
    )
    proc = run(world, github)
    assert proc.returncode == 0, proc.stderr
    assert json.loads(proc.stdout)["status"] == "merged"
    assert writes(github)


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


def test_existing_request_cannot_verify_an_automatic_message(world, github) -> None:
    answer(github, **{ASYNC: {"$http": 409, "$body": pending()}, OPERATION: merged()})
    proc = run(world, github)
    assert proc.returncode == 3
    assert "message" in proc.stderr
    assert not any(OPERATION in c for c in github.network_calls())


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
        ["7", "7", "--match-head-commit", HEAD],
        ["7", "9", "--match-head-commit", HEAD],
        ["7", "9", "--subject", "Fix (#7)"],
        ["7", "9", "--body-file", "message.txt"],
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


@pytest.mark.parametrize("unsettled", ["base", "containment"])
def test_stack_waits_until_each_condition_settles(world, github, unsettled) -> None:
    routes = {
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
        ("base", "base branch"),
        ("containment", MERGE),
    ],
)
def test_stack_timeout_names_the_unmet_condition(
    world, github, unsettled, reason
) -> None:
    routes = {
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


# --------------------------------------------------------------------------
# a base left on a branch whose own pull request merged
# --------------------------------------------------------------------------

BRANCH = "repos/owner/repo/branches/parent"
FROM_PARENT = "repos/owner/repo/pulls?head=owner%3Aparent&state=closed&per_page=100"


def stale_base(github, tip: str = BASE, routes: dict | None = None) -> None:
    """#7 targets `parent`, a branch that stands at `tip`; #6 merged it at
    BASE into main."""
    repo = {"full_name": "owner/repo", "default_branch": "main"}
    github.answer(
        {
            "$view": {**_pr(), "baseRefName": "parent"},
            "$api": {
                PR: pull(base={"ref": "parent", "repo": repo}),
                ASYNC: merged(),
                BRANCH: {"name": "parent", "commit": {"sha": tip}},
                FROM_PARENT: [
                    {"number": 6, "merged_at": "2026-11-08T00:00:00Z"}
                    | {"head": {"ref": "parent", "sha": BASE}}
                ],
                **(routes or {}),
            },
        }
    )


def test_a_base_left_on_a_merged_branch_is_refused(world, github) -> None:
    stale_base(github)

    p = run(world, github)
    assert p.returncode == 3
    assert p.stdout == ""
    assert "#7 targets parent, which #6 merged into main" in p.stderr
    assert "gw fast-forward, in the checkout of feature" in p.stderr
    assert writes(github) == []


def test_a_base_that_moved_on_since_its_merge_is_somebodys_branch(
    world, github
) -> None:
    stale_base(github, tip=REWRITE)

    p = run(world, github)
    assert p.returncode == 0, p.stderr
    assert json.loads(p.stdout)["status"] == "merged"


def test_a_base_no_pull_request_merged_is_merged_into(world, github) -> None:
    stale_base(github, routes={FROM_PARENT: []})

    p = run(world, github)
    assert p.returncode == 0, p.stderr
    assert json.loads(p.stdout)["status"] == "merged"


def test_a_base_that_cannot_be_read_refuses(world, github) -> None:
    stale_base(github, routes={FROM_PARENT: 1})

    p = run(world, github)
    assert p.returncode == 3
    assert "could not read what merged from #7's base parent" in p.stderr
    assert writes(github) == []


# --------------------------------------------------------------------------
# a stack merged from the middle: #6, #7 and #8 open, #7 named
# --------------------------------------------------------------------------

SIX = "e" * 40
EIGHT = "f" * 40
SQUASH = "9" * 40
REPO = "repos/owner/repo"
SETTINGS = {
    "squash_merge_commit_title": "PR_TITLE",
    "squash_merge_commit_message": "PR_BODY",
}


def view(number: int, sha: str, **extra) -> dict:
    return _pr(
        number=number,
        url=f"https://github.com/owner/repo/pull/{number}",
        headRefOid=sha,
        **extra,
    )


def six(merged: bool = False, sha: str = SIX) -> dict:
    row = pull(number=6, head={"ref": "six", "sha": sha}, stack=stack_info(size=3))
    if merged:
        return {**row, "state": "closed", "merged": True, "merge_commit_sha": SQUASH}
    return row


def middle(github, reads: int = 2, eight: str = EIGHT, views=None, routes=None) -> None:
    """#7 between #6 and #8, all three open. GitHub answers #6 open for
    `reads` reads, then merged."""
    own = {"headRepositoryOwner": {"login": "owner"}, "isCrossRepository": False}
    github.answer(
        {
            "$views": {
                "6": view(6, SIX, headRefName="six"),
                "7": view(7, HEAD, baseRefName="six"),
                "8": view(8, eight, headRefName="eight", baseRefName="feature", **own),
                **(views or {}),
            },
            "$api": {
                PR: pull(
                    title="Seven",
                    stack=stack_info(position=2, size=3),
                    base={"ref": "six", "repo": {"full_name": "owner/repo"}},
                ),
                STACK: stack([member(6, SIX), member(), member(8, eight)]),
                PARENT: {"$sequence": [six()] * reads + [six(merged=True)]},
                REPO: SETTINGS,
                ASYNC: merged(),
                **(routes or {}),
            },
        }
    )


def test_a_pr_in_a_stack_merges_the_open_ones_below_it_in_one_call(
    world, github
) -> None:
    middle(github)
    proc = run(world, github)
    assert proc.returncode == 0, proc.stderr
    assert [c[2] for c in writes(github)] == [ASYNC]
    result = json.loads(proc.stdout)
    assert result["status"] == "merged"
    assert result["merged"] == [
        {"number": 6, "sha": SQUASH},
        {"number": 7, "sha": MERGE},
    ]
    assert result["remaining"] == [
        {
            "number": 8,
            "headSha": EIGHT,
            "baseRef": "feature",
            "mergeStateStatus": "CLEAN",
            "checksState": "passed",
        }
    ]
    assert result["warnings"] == []
    assert result["branches"] == []


def test_a_stack_merge_prints_each_pr_as_a_row(world, github) -> None:
    middle(github)
    proc = cli(world, "merge", "7", "--match-head-commit", HEAD, env=github.env())
    assert proc.returncode == 0, proc.stderr
    lines = proc.stdout.splitlines()
    assert lines[0] == f"#7: merged {MERGE}"
    assert lines[1].split() == [
        "PR",
        "STATE",
        "SHA",
        "BASE",
        "MERGE",
        "STATE",
        "CHECKS",
    ]
    assert lines[2].split() == ["#6", "merged", SQUASH[:7]]
    assert lines[3].split() == ["#7", "merged", MERGE[:7]]
    assert lines[4].split() == ["#8", "open", EIGHT[:7], "feature", "CLEAN", "passed"]


def test_a_pr_below_without_checks_merges_with_the_stack(world, github) -> None:
    middle(github, views={"6": view(6, SIX, statusCheckRollup=[])})
    proc = run(world, github)
    assert proc.returncode == 0, proc.stderr
    assert len(writes(github)) == 1


@pytest.mark.parametrize(
    "snapshot",
    [
        view(6, SIX, statusCheckRollup=[_check(conclusion="FAILURE")]),
        view(6, SIX, statusCheckRollup=[_check(status="QUEUED", conclusion="")]),
        view(6, SIX, isDraft=True),
        view(6, SIX, mergeStateStatus="BLOCKED"),
        view(6, BASE),
    ],
)
def test_a_pr_below_that_is_not_ready_refuses_the_stack(
    world, github, snapshot
) -> None:
    middle(github, views={"6": snapshot})
    proc = run(world, github)
    assert proc.returncode == 3
    assert "#6" in proc.stderr
    assert not writes(github)


def test_a_closed_pr_below_refuses_the_stack(world, github) -> None:
    closed = stack([member(6, SIX, state="closed"), member(), member(8, EIGHT)])
    middle(github, routes={STACK: closed})
    proc = run(world, github)
    assert proc.returncode == 3
    assert "#6 below #7 is not open" in proc.stderr
    assert not writes(github)


def test_a_pr_below_that_merged_at_another_head_is_a_warning(world, github) -> None:
    middle(github, routes={PARENT: {"$sequence": [six(), six(), six(True, REWRITE)]}})
    proc = run(world, github)
    assert proc.returncode == 0, proc.stderr
    assert f"#6 merged at {REWRITE}, not at {SIX}" in proc.stderr
    assert json.loads(proc.stdout)["warnings"]


@pytest.mark.parametrize(
    "settings",
    [
        {
            "squash_merge_commit_title": "COMMIT_OR_PR_TITLE",
            "squash_merge_commit_message": "COMMIT_MESSAGES",
        },
        {},
    ],
)
def test_squash_settings_other_than_title_and_body_are_a_warning(
    world, github, settings
) -> None:
    middle(github, routes={REPO: settings})
    proc = run(world, github)
    assert proc.returncode == 0, proc.stderr
    assert "squash commits of #6 will use GitHub's default message" in proc.stderr
    assert "gw preflight" in proc.stderr
    assert len(json.loads(proc.stdout)["warnings"]) == 1


def test_a_pr_with_nothing_open_below_reads_no_squash_settings(world, github) -> None:
    answer(github, **{PR: pull(stack=stack_info()), STACK: stack()})
    proc = run(world, github)
    assert proc.returncode == 0, proc.stderr
    assert not any(REPO in c for c in github.network_calls())


def branch_above(world, how: str) -> tuple[str, str, Path | None]:
    """`eight` where GitHub had it before the merge, with origin's copy
    already rewritten: the fetch the network fakes moves nothing."""
    before = world.git("rev-parse", "HEAD")
    tree = world.git("rev-parse", "HEAD^{tree}")
    after = world.git("commit-tree", tree, "-p", before, "-m", "rewritten")
    world.git("update-ref", "refs/remotes/origin/eight", after)
    path = None
    if how == "checked-out":
        path = world.worktree("eight", before)
    elif how == "kept":
        own = world.git("commit-tree", tree, "-p", before, "-m", "local")
        world.git("branch", "eight", own)
    else:
        world.git("branch", "eight", before)
    return before, after, path


def test_a_local_branch_above_moves_to_the_rewritten_copy(world, github) -> None:
    before, after, _ = branch_above(world, "moved")
    middle(github, eight=before)
    proc = run(world, github)
    assert proc.returncode == 0, proc.stderr
    assert world.git("rev-parse", "refs/heads/eight") == after
    assert json.loads(proc.stdout)["branches"] == [
        {
            "number": 8,
            "branch": "eight",
            "result": "moved",
            "from": before,
            "to": after,
            "path": "",
        }
    ]
    assert any(
        c[-4:] == ["update-ref", "refs/heads/eight", after, before]
        for c in github.calls()
    )
    assert any("fetch" in c for c in github.network_calls())


@pytest.mark.parametrize("how", ["checked-out", "kept"])
def test_a_local_branch_above_that_could_lose_work_stays(world, github, how) -> None:
    before, _, path = branch_above(world, how)
    tip = world.git("rev-parse", "refs/heads/eight")
    middle(github, eight=before)
    proc = run(world, github)
    assert proc.returncode == 0, proc.stderr
    assert world.git("rev-parse", "refs/heads/eight") == tip
    (row,) = json.loads(proc.stdout)["branches"]
    assert row["result"] == how
    assert not any("update-ref" in c for c in github.calls())
    if path is not None:
        assert row["path"] == str(path)
        assert f"Run: gw fast-forward, in {path}" in proc.stderr


# --------------------------------------------------------------------------
# several numbers
# --------------------------------------------------------------------------

NINE = "1" * 40
TEN = "2" * 40
TRAILER = "Co-Authored-By: Claude <noreply@anthropic.com>"


def several(github, nine_merge=None, views=None) -> None:
    def pr_at(number: int, sha: str, title: str, body: str = "") -> dict:
        return pull(
            number=number,
            head={"ref": f"b{number}", "sha": sha},
            title=title,
            body=body,
        )

    commits = [{"oid": HEAD, "messageHeadline": "Change", "messageBody": TRAILER}]
    github.answer(
        {
            "$views": {
                "7": view(7, HEAD, commits=commits),
                "9": view(9, NINE),
                "10": view(10, TEN),
                **(views or {}),
            },
            "$api": {
                PR: pr_at(7, HEAD, "Seven", "Body.\n\n🤖 Generated with [Claude Code]"),
                ASYNC: merged(),
                "repos/owner/repo/pulls/9": pr_at(9, NINE, "Nine"),
                "repos/owner/repo/pulls/9/merge-async": nine_merge or merged(),
                "repos/owner/repo/pulls/10": pr_at(10, TEN, "Ten"),
                "repos/owner/repo/pulls/10/merge-async": merged(),
            },
        }
    )


def test_several_numbers_merge_in_order_with_messages_gw_builds(world, github) -> None:
    several(github)
    proc = cli(world, "merge", "7", "9", "10", "--json", env=github.env())
    assert proc.returncode == 0, proc.stderr
    calls = writes(github)
    assert [c[2] for c in calls] == [
        ASYNC,
        "repos/owner/repo/pulls/9/merge-async",
        "repos/owner/repo/pulls/10/merge-async",
    ]
    fields = [calls[0][i + 1] for i, w in enumerate(calls[0]) if w == "--raw-field"]
    assert set(fields) == {
        "sha=" + HEAD,
        "merge_method=squash",
        "merge_action=default",
        "commit_title=Seven (#7)",
        f"commit_message=Body.\n\n{TRAILER}\n",
    }
    assert "commit_title=Ten (#10)" in calls[2]
    result = json.loads(proc.stdout)
    assert result["status"] == "merged"
    assert [r["number"] for r in result["results"]] == [7, 9, 10]
    assert [m["number"] for m in result["merged"]] == [7, 9, 10]
    assert result["remaining"] == []
    assert set(result) >= {"head", "lastSync"}


def test_several_numbers_stop_at_the_first_that_does_not_merge(world, github) -> None:
    several(
        github,
        nine_merge={"status": "failed", "details": {"message": "Review missing"}},
    )
    proc = cli(world, "merge", "7", "9", "10", "--json", env=github.env())
    assert proc.returncode == 1
    assert [c[2] for c in writes(github)] == [
        ASYNC,
        "repos/owner/repo/pulls/9/merge-async",
    ]
    result = json.loads(proc.stdout)
    assert result["status"] == "failed"
    assert [r["status"] for r in result["results"]] == ["merged", "failed"]
    assert result["merged"] == [{"number": 7, "sha": MERGE}]
    assert [r["number"] for r in result["remaining"]] == [9, 10]
    assert "Review missing" in proc.stderr


def test_a_refusal_after_a_merge_still_reports_the_merge(world, github) -> None:
    several(github, views={"9": view(9, NINE, mergeStateStatus="DIRTY")})
    proc = cli(world, "merge", "7", "9", "10", "--json", env=github.env())
    assert proc.returncode == 3
    assert [c[2] for c in writes(github)] == [ASYNC]
    result = json.loads(proc.stdout)
    assert result["status"] == "refused"
    assert result["merged"] == [{"number": 7, "sha": MERGE}]
    assert result["results"][1]["message"] == "#9 merge state is DIRTY"
    assert [r["number"] for r in result["remaining"]] == [9, 10]
    assert "#9 merge state is DIRTY" in proc.stderr


def test_numbers_in_one_stack_merge_in_one_call_on_the_highest(world, github) -> None:
    middle(github, reads=3)
    proc = cli(world, "merge", "6", "7", "--json", env=github.env())
    assert proc.returncode == 0, proc.stderr
    assert [c[2] for c in writes(github)] == [ASYNC]
    result = json.loads(proc.stdout)
    assert [r["number"] for r in result["results"]] == [7]
    assert [m["number"] for m in result["merged"]] == [6, 7]
    assert "commit_title=Seven (#7)" in writes(github)[0]


def test_message_leaves_out_what_belongs_on_the_review_page() -> None:
    from worktrees import pr_merge

    body = (
        "Fixes the parser.\r\n\r\n"
        "<details>\n<summary>Auto-resolution</summary>\n\n| a |\n\n</details>\n\n"
        "```release-note\nFixed.\n```\n\n"
        "🤖 Generated with [Claude Code](https://claude.com/claude-code)\n\n"
        "https://claude.ai/code/session_01abc\n"
    )
    session = "Claude-Session: https://claude.ai/code/session_01abc"
    subject, text = pr_merge.message(7, "Fix parser", body, [TRAILER, session])
    assert subject == "Fix parser (#7)"
    assert text == (
        f"Fixes the parser.\n\n```release-note\nFixed.\n```\n\n{TRAILER}\n{session}\n"
    )


def test_message_keeps_trailers_already_in_the_body_once() -> None:
    from worktrees import pr_merge

    session = "Claude-Session: https://claude.ai/code/session_01abc"
    subject, text = pr_merge.message(
        7, "Fix parser (#7)", f"Body.\n\n{TRAILER}\n", [TRAILER, session]
    )
    assert subject == "Fix parser (#7)"
    assert text == f"Body.\n\n{TRAILER}\n{session}\n"
