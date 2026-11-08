"""`gw ci` reads one review and its checks, and nothing else.

The forge is the stand-in from conftest: `$view` answers `gh pr view` and
`glab mr view`, `$api` answers `glab api`. Nothing reaches the network.
"""

from __future__ import annotations

import json

import pytest
from test_branches import cli
from test_sync import origin

from worktrees import ci

HEAD = "a" * 40
BASE = "b" * 40
MERGE = "c" * 40


def _check(name="tests", status="COMPLETED", conclusion="SUCCESS", **extra) -> dict:
    return {
        "__typename": "CheckRun",
        "name": name,
        "workflowName": "CI",
        "status": status,
        "conclusion": conclusion,
        "startedAt": "2026-11-08T10:00:00Z",
        "detailsUrl": f"https://github.com/owner/repo/actions/runs/12/job/{name}",
        **extra,
    }


def _pr(**extra) -> dict:
    return {
        "number": 7,
        "url": "https://github.com/owner/repo/pull/7",
        "state": "OPEN",
        "isDraft": False,
        "headRefName": "feature",
        "baseRefName": "main",
        "headRefOid": HEAD,
        "headRepository": {"name": "repo"},
        "headRepositoryOwner": {"login": "fork"},
        "isCrossRepository": True,
        "mergeable": "MERGEABLE",
        "mergeStateStatus": "CLEAN",
        "statusCheckRollup": [_check()],
        **extra,
    }


def _read(world, network, *args: str, code: int = 0) -> dict:
    proc = cli(world, "ci", *args, "--json", env=network.env())
    assert proc.returncode == code, proc.stderr
    return json.loads(proc.stdout) if code == 0 else {}


@pytest.fixture
def github(world, network):
    origin(world)
    world.git("switch", "-qc", "feature")
    network.answer({"$view": _pr()})
    return network


def test_reads_identity_and_checks_without_touching_git(world, github) -> None:
    before = world.git("rev-parse", "HEAD")
    proc = cli(world, "ci", "7", "--json", env=github.env())
    assert proc.returncode == 0, proc.stderr
    assert not proc.stderr
    out = json.loads(proc.stdout)
    assert out["checksState"] == "passed"
    assert out["stale"] is False
    assert out["pr"]["headSha"] == HEAD
    assert out["pr"]["source"] == "fork/repo"
    assert out["pr"]["repository"] == "github.com/owner/repo"
    assert out["checks"] == [
        {
            "name": "tests",
            "workflow": "CI",
            "state": "SUCCESS",
            "bucket": "pass",
            "link": "https://github.com/owner/repo/actions/runs/12/job/tests",
        }
    ]
    assert set(out) >= {"head", "lastSync"}
    assert world.git("rev-parse", "HEAD") == before
    assert not world.git("status", "--porcelain")
    calls = [c for c in github.network_calls() if c[0] == "gh"]
    assert calls == [
        [
            "gh",
            "pr",
            "view",
            "7",
            "--repo",
            "github.com/owner/repo",
            "--json",
            ci._GITHUB_FIELDS,
        ]
    ]


def test_reads_the_current_branch_without_the_callers_repo_redirect(
    world, github
) -> None:
    proc = cli(world, "ci", "--json", env=github.env(GH_REPO="github.com/else/where"))
    assert proc.returncode == 0, proc.stderr
    call = next(c for c in github.network_calls() if c[0] == "gh")
    assert call[3:6] == ["feature", "--repo", "github.com/owner/repo"]


@pytest.mark.parametrize(
    ("check", "expected"),
    [
        (_check(status="IN_PROGRESS", conclusion=""), "pending"),
        (_check(status="QUEUED", conclusion=""), "pending"),
        (_check(conclusion="FAILURE"), "failed"),
        (_check(conclusion="TIMED_OUT"), "failed"),
        (_check(conclusion="CANCELLED"), "failed"),
        (_check(conclusion="STALE"), "unknown"),
        (_check(status="SOMETHING_NEW", conclusion=""), "unknown"),
    ],
)
def test_each_check_run_state_reads_as_one_word(world, github, check, expected) -> None:
    github.answer({"$view": _pr(statusCheckRollup=[check])})
    assert _read(world, github, "7")["checksState"] == expected


def test_neutral_and_skipped_pass_and_keep_their_native_state(world, github) -> None:
    github.answer(
        {
            "$view": _pr(
                mergeStateStatus="BLOCKED",
                statusCheckRollup=[
                    _check("optional", conclusion="NEUTRAL"),
                    _check("paths", conclusion="SKIPPED"),
                ],
            )
        }
    )
    out = _read(world, github, "7")
    assert out["checksState"] == "passed"
    assert out["pr"]["mergeStateStatus"] == "BLOCKED"
    assert [c["state"] for c in out["checks"]] == ["NEUTRAL", "SKIPPED"]
    assert [c["bucket"] for c in out["checks"]] == ["pass", "skipping"]


def test_commit_statuses_read_beside_check_runs(world, github) -> None:
    status = {
        "__typename": "StatusContext",
        "context": "ci/legacy",
        "state": "PENDING",
        "targetUrl": "https://ci.example/7",
    }
    github.answer({"$view": _pr(statusCheckRollup=[_check(), status])})
    out = _read(world, github, "7")
    assert out["checksState"] == "pending"
    assert out["checks"][1] == {
        "name": "ci/legacy",
        "workflow": "",
        "state": "PENDING",
        "bucket": "pending",
        "link": "https://ci.example/7",
    }


def test_a_rerun_replaces_the_run_it_repeats(world, github) -> None:
    old = _check(conclusion="FAILURE", startedAt="2026-11-08T09:00:00Z")
    new = _check(conclusion="SUCCESS", startedAt="2026-11-08T10:00:00Z")
    github.answer({"$view": _pr(statusCheckRollup=[new, old])})
    out = _read(world, github, "7")
    assert out["checksState"] == "passed"
    assert len(out["checks"]) == 1

    queued = _check(status="QUEUED", conclusion="", startedAt=None)
    github.answer({"$view": _pr(statusCheckRollup=[old, queued])})
    assert _read(world, github, "7")["checksState"] == "pending"


def test_one_name_in_two_workflows_is_two_checks(world, github) -> None:
    github.answer(
        {
            "$view": _pr(
                statusCheckRollup=[
                    _check(),
                    _check(workflowName="Nightly", conclusion="FAILURE"),
                ]
            )
        }
    )
    out = _read(world, github, "7")
    assert out["checksState"] == "failed"
    assert len(out["checks"]) == 2


def test_no_checks_is_unknown_rather_than_passed(world, github) -> None:
    github.answer({"$view": _pr(statusCheckRollup=[])})
    out = _read(world, github, "7")
    assert out["checksState"] == "unknown"
    assert out["checks"] == []


@pytest.mark.parametrize(
    "answer",
    [4, {"message": "access denied"}, _pr(headRefOid=None), _pr(number=8)],
)
def test_no_snapshot_without_a_whole_identity(world, github, answer) -> None:
    github.answer({"$view": answer})
    proc = cli(world, "ci", "7", "--json", env=github.env())
    assert proc.returncode == 1
    assert not proc.stdout
    assert proc.stderr


def test_a_review_for_another_branch_is_not_this_branchs(world, github) -> None:
    github.answer({"$view": _pr(headRefName="other")})
    proc = cli(world, "ci", "--json", env=github.env())
    assert proc.returncode == 1
    assert "another branch" in proc.stderr


def test_detached_head_needs_a_number(world, github) -> None:
    world.git("checkout", "-q", "--detach")
    proc = cli(world, "ci", env=github.env())
    assert proc.returncode == 1
    assert "gw ci NUMBER" in proc.stderr
    assert not github.network_calls()


@pytest.mark.parametrize("args", [["7", "8"], ["7oops"], ["0"]])
def test_a_malformed_number_is_a_usage_error(world, github, args) -> None:
    assert cli(world, "ci", *args, env=github.env()).returncode == 2
    assert not github.network_calls()


def test_no_recognised_forge_is_an_answer_not_a_guess(world, network) -> None:
    origin(world, host="example.com")
    proc = cli(world, "ci", "7", env=network.env())
    assert proc.returncode == 1
    assert "GitHub or GitLab" in proc.stderr
    assert not network.network_calls()


def test_the_table_puts_failures_first(world, github) -> None:
    github.answer(
        {
            "$view": _pr(
                statusCheckRollup=[
                    _check("tests"),
                    _check("lint", conclusion="FAILURE"),
                ]
            )
        }
    )
    proc = cli(world, "ci", "7", env=github.env())
    assert proc.returncode == 0, proc.stderr
    lines = proc.stdout.splitlines()
    assert lines[0].split() == ["STATE", "CHECK", "LINK"]
    assert lines[1].split()[:4] == ["fail", "CI", "/", "lint"]
    assert lines[-1].endswith("checks failed")


# --------------------------------------------------------------------------
# GitLab
# --------------------------------------------------------------------------


def _mr(**extra) -> dict:
    return {
        "iid": 7,
        "web_url": "https://gitlab.com/owner/repo/-/merge_requests/7",
        "state": "opened",
        "source_branch": "feature",
        "target_branch": "main",
        "sha": HEAD,
        "source_project_id": 22,
        "target_project_id": 11,
        "detailed_merge_status": "mergeable",
        "head_pipeline": {"id": 90, "project_id": 22, "sha": HEAD, "status": "success"},
        **extra,
    }


def _pipeline(**extra) -> dict:
    return {
        "id": 90,
        "project_id": 22,
        "sha": HEAD,
        "ref": "feature",
        "status": "success",
        "web_url": "https://gitlab.com/fork/repo/-/pipelines/90",
        **extra,
    }


PIPELINE = "projects/22/pipelines/90"
JOBS = "projects/22/pipelines/90/jobs?per_page=100"


@pytest.fixture
def gitlab(world, network):
    origin(world, host="gitlab.com")
    world.git("switch", "-qc", "feature")
    network.answer(
        {
            "$view": _mr(),
            "$api": {
                PIPELINE: _pipeline(),
                JOBS: [{"id": 91, "name": "tests", "status": "success"}],
            },
        }
    )
    return network


def test_gitlab_reads_jobs_from_the_pipeline_project_across_pages(
    world, gitlab
) -> None:
    gitlab.answer(
        {
            "$view": _mr(),
            "$api": {
                PIPELINE: _pipeline(),
                JOBS: {
                    "$pages": [
                        [{"id": 91, "name": "tests", "status": "success"}],
                        [{"id": 92, "name": "deploy", "status": "failed"}],
                    ]
                },
            },
        }
    )
    out = _read(world, gitlab, "7")
    assert out["checksState"] == "failed"
    assert len(out["checks"]) == 3
    assert out["pipeline"]["project_id"] == 22
    assert out["pipeline"]["association"] == "head"
    api = [c for c in gitlab.network_calls() if c[:2] == ["glab", "api"]]
    assert ["glab", "api", JOBS, "--hostname", "gitlab.com", "--paginate"] in api
    assert not any("projects/11/pipelines" in c[2] for c in api)
    assert out["pr"]["state"] == "OPEN"
    assert out["pr"]["mergeable"] == "MERGEABLE"


@pytest.mark.parametrize(
    ("job", "expected"),
    [
        ({"status": "manual"}, "blocked"),
        ({"status": "manual", "allow_failure": True}, "passed"),
        ({"status": "failed", "allow_failure": True}, "passed"),
        ({"status": "running"}, "pending"),
        ({"status": "canceled"}, "failed"),
    ],
)
def test_gitlab_job_states(world, gitlab, job, expected) -> None:
    gitlab.answer(
        {
            "$view": _mr(),
            "$api": {PIPELINE: _pipeline(), JOBS: [{"id": 91, "name": "x", **job}]},
        }
    )
    assert _read(world, gitlab, "7")["checksState"] == expected


def test_a_running_gitlab_pipeline_is_pending_whatever_its_jobs_say(
    world, gitlab
) -> None:
    gitlab.answer(
        {
            "$view": _mr(),
            "$api": {
                PIPELINE: _pipeline(status="running"),
                JOBS: [{"id": 91, "name": "tests", "status": "success"}],
            },
        }
    )
    assert _read(world, gitlab, "7")["checksState"] == "pending"


def test_a_gitlab_mr_without_a_pipeline_is_unknown(world, gitlab) -> None:
    gitlab.answer({"$view": _mr(head_pipeline=None)})
    out = _read(world, gitlab, "7")
    assert out["checksState"] == "unknown"
    assert out["pipeline"] is None
    assert not [c for c in gitlab.network_calls() if c[:2] == ["glab", "api"]]


def test_a_pipeline_for_an_older_head_cannot_report_green(world, gitlab) -> None:
    gitlab.answer(
        {
            "$view": _mr(),
            "$api": {
                PIPELINE: _pipeline(sha=BASE),
                JOBS: [{"id": 91, "name": "tests", "status": "success"}],
            },
        }
    )
    out = _read(world, gitlab, "7")
    assert out["stale"] is True
    assert out["checksState"] == "unknown"


def test_a_merged_result_pipeline_counts_only_on_the_current_parents(
    world, gitlab
) -> None:
    records = {
        PIPELINE: _pipeline(sha=MERGE, ref="refs/merge-requests/7/merge"),
        JOBS: [{"id": 91, "name": "tests", "status": "success"}],
        f"projects/22/repository/commits/{MERGE}": {
            "id": MERGE,
            "parent_ids": [HEAD, BASE],
        },
        "projects/11/repository/branches/main": {"commit": {"id": BASE}},
    }
    gitlab.answer({"$view": _mr(), "$api": records})
    out = _read(world, gitlab, "7")
    assert out["checksState"] == "passed"
    assert out["pipeline"]["association"] == "merge-result"

    records["projects/11/repository/branches/main"] = {"commit": {"id": MERGE}}
    gitlab.answer({"$view": _mr(), "$api": records})
    out = _read(world, gitlab, "7")
    assert out["stale"] is True
    assert out["checksState"] == "unknown"


def test_a_pipeline_that_changes_during_the_read_is_stale(world, gitlab) -> None:
    moved = _mr(
        head_pipeline={"id": 99, "project_id": 22, "sha": HEAD, "status": "running"}
    )
    gitlab.answer(
        {
            "$view": [_mr(), moved],
            "$api": {
                PIPELINE: _pipeline(),
                JOBS: [{"id": 91, "name": "tests", "status": "success"}],
            },
        }
    )
    out = _read(world, gitlab, "7")
    assert out["stale"] is True
    assert out["checksState"] == "unknown"


@pytest.mark.parametrize(
    "records",
    [
        {PIPELINE: _pipeline(id=91), JOBS: []},
        {PIPELINE: _pipeline(), JOBS: {"message": "forbidden"}},
        {PIPELINE: _pipeline(), JOBS: 1},
    ],
)
def test_gitlab_pipeline_and_job_errors_give_no_snapshot(
    world, gitlab, records
) -> None:
    gitlab.answer({"$view": _mr(), "$api": records})
    proc = cli(world, "ci", "7", "--json", env=gitlab.env())
    assert proc.returncode == 1
    assert not proc.stdout


# --------------------------------------------------------------------------
# --wait
# --------------------------------------------------------------------------


def _snapshot(checks_state: str, checks: int = 1, stale: bool = False) -> dict:
    return {
        "checksState": checks_state,
        "stale": stale,
        "checks": [{"bucket": "x"}] * checks,
    }


class _Clock:
    def __init__(self) -> None:
        self.at = 0.0
        self.slept: list[float] = []

    def now(self) -> float:
        return self.at

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.at += seconds


def _waited(answers: list[dict]) -> tuple[dict, _Clock]:
    clock = _Clock()
    queue = list(answers)

    def reader() -> dict:
        return queue.pop(0) if len(queue) > 1 else queue[0]

    return ci.wait(reader, now=clock.now, sleep=clock.sleep), clock


def test_wait_reads_until_nothing_is_running() -> None:
    out, clock = _waited(
        [_snapshot("pending"), _snapshot("pending"), _snapshot("failed")]
    )
    assert out["checksState"] == "failed"
    assert clock.slept == [ci.INTERVAL, ci.INTERVAL]


def test_wait_reads_a_stale_snapshot_again() -> None:
    out, clock = _waited([_snapshot("unknown", stale=True), _snapshot("passed")])
    assert out["checksState"] == "passed"
    assert len(clock.slept) == 1


def test_wait_gives_checks_time_to_appear_and_no_longer() -> None:
    out, clock = _waited([_snapshot("unknown", checks=0)])
    assert out["checksState"] == "unknown"
    assert clock.at >= ci.GRACE
    assert clock.at < ci.GRACE + ci.INTERVAL


def test_wait_stops_at_its_ceiling_with_the_last_read() -> None:
    out, clock = _waited([_snapshot("pending")])
    assert out["checksState"] == "pending"
    assert clock.at <= ci.WAIT


def test_wait_does_not_sleep_on_a_settled_read() -> None:
    out, clock = _waited([_snapshot("passed")])
    assert out["checksState"] == "passed"
    assert not clock.slept
