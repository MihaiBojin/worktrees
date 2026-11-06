"""Forge evidence must identify this branch tip and a merge into its head."""

from __future__ import annotations

import json

import pytest
from test_branches import cli, local_branch


def evidence(world, provider, **changes) -> dict:
    tip = world.git("rev-parse", "feature")
    head = world.git("rev-parse", "main")
    if provider == "github":
        row = {
            "number": 17,
            "state": "MERGED",
            "url": "https://github.com/owner/repo/pull/17",
            "headRefName": "feature",
            "headRefOid": tip,
            "baseRefName": "main",
            "mergedAt": "2026-09-28T00:00:00Z",
            "mergeCommit": {"oid": head},
            "headRepository": {"nameWithOwner": "owner/repo"},
            "isCrossRepository": False,
        }
    else:
        row = {
            "iid": 17,
            "state": "merged",
            "web_url": "https://gitlab.com/owner/repo/-/merge_requests/17",
            "source_branch": "feature",
            "sha": tip,
            "target_branch": "main",
            "merged_at": "2026-09-28T00:00:00Z",
            "merge_commit_sha": head,
            "squash_commit_sha": None,
            "source_project_id": 7,
            "target_project_id": 7,
        }
    return {**row, **changes}


def stub(world, provider, payload, network) -> dict[str, str]:
    network.answer(
        {
            "feature": payload,
            "$api": {
                "projects/7": {
                    "id": 7,
                    "path_with_namespace": "owner/repo",
                    "web_url": "https://gitlab.com/owner/repo",
                }
            },
        }
    )
    env = network.env(GH_REPO="wrong/repo", GITLAB_REPO="wrong/repo")
    cli(world, "sync", env=env)
    return env


@pytest.fixture(params=["github", "gitlab"])
def forge_world(world, network, request):
    provider = request.param
    local_branch(world)
    world.commit("feature", "later edits\n")
    world.git("remote", "add", "origin", f"git@{provider}.com:owner/repo.git")
    world.git("update-ref", "refs/remotes/origin/main", "main")
    return world, provider, network


def assess(world, provider, row, network) -> dict:
    env = stub(world, provider, [row], network)
    p = cli(world, "branch", "status", "feature", "--json", env=env)
    assert p.returncode == 0, p.stderr
    return json.loads(p.stdout)["verdicts"][0]


def test_merged_request_matches_source_tip_and_reachable_merge(forge_world):
    world, provider, network = forge_world
    row = assess(world, provider, evidence(world, provider), network)
    assert row["verdict"] == "remove", row
    assert row["proof"] == "forge"
    assert row["request"]["number"] == 17
    assert row["request"]["source_sha"] == world.git("rev-parse", "feature")


@pytest.mark.parametrize(
    "mismatch", ["tip", "target", "source", "fork", "merge", "missing"]
)
def test_unverified_merge_cannot_authorize_deletion(forge_world, mismatch):
    world, provider, network = forge_world
    row = evidence(world, provider)
    fields = {
        "github": {
            "tip": "headRefOid",
            "target": "baseRefName",
            "source": "headRefName",
            "fork": "headRepository",
            "merge": "mergeCommit",
            "missing": "mergedAt",
        },
        "gitlab": {
            "tip": "sha",
            "target": "target_branch",
            "source": "source_branch",
            "fork": "source_project_id",
            "merge": "merge_commit_sha",
            "missing": "merged_at",
        },
    }
    value: object = "0" * 40 if mismatch in ("tip", "merge") else "different"
    if mismatch == "fork":
        value = {"nameWithOwner": "other/repo"} if provider == "github" else 999
    if mismatch == "merge" and provider == "github":
        value = {"oid": value}
    if mismatch == "missing":
        value = None
    row[fields[provider][mismatch]] = value
    result = assess(world, provider, row, network)
    assert result["verdict"] != "remove", result


@pytest.mark.parametrize("state", ["closed", "open"])
def test_closed_or_open_requests_do_not_authorize_deletion(forge_world, state):
    world, provider, network = forge_world
    row = evidence(
        world, provider, state=state.upper() if provider == "github" else state
    )
    result = assess(world, provider, row, network)
    assert result["verdict"] == "keep", result
    assert result["request"]["state"] == state.upper()


def test_closed_request_does_not_block_independent_content_proof(forge_world):
    world, provider, network = forge_world
    world.commit("feature", "branch\n")
    world.git("update-ref", "refs/remotes/origin/main", "main")
    result = assess(world, provider, evidence(world, provider, state="CLOSED"), network)
    assert result["verdict"] == "remove", result
    assert result["proof"] in ("squash", "content")


@pytest.mark.parametrize("payload", [None, {}, [None], [{"number": "invalid"}]])
def test_malformed_forge_data_keeps_the_branch(forge_world, payload):
    world, provider, network = forge_world
    env = stub(world, provider, payload, network)
    p = cli(world, "branch", "delete", "feature", "--yes", "--json", env=env)
    assert p.returncode == 3, p.stderr
    assert json.loads(p.stdout)["removed"] == []
    assert world.git("rev-parse", "feature")


@pytest.mark.parametrize(
    ("source", "target"),
    [(True, True), (1, True), (7, 7.0), (None, None), (0, 7), (-1, 7), (7, 0), (7, -1)],
)
def test_gitlab_requires_integer_project_ids(world, network, source, target):
    local_branch(world)
    world.commit("feature", "later edits\n")
    world.git("remote", "add", "origin", "git@gitlab.com:owner/repo.git")
    world.git("update-ref", "refs/remotes/origin/main", "main")
    row = evidence(world, "gitlab", source_project_id=source, target_project_id=target)
    assert assess(world, "gitlab", row, network)["verdict"] != "remove"


@pytest.mark.parametrize("local_proof", [False, True])
@pytest.mark.parametrize(
    "provider,changes",
    [
        ("github", {"headRepository": None}),
        ("github", {"headRepository": "owner/repo"}),
        ("github", {"headRepository": ["owner/repo"]}),
        ("github", {"headRepository": {"nameWithOwner": None}}),
        ("github", {"headRepository": {"nameWithOwner": 17}}),
        ("github", {"url": "https://github.com/other/repo/pull/17"}),
        ("gitlab", {"source_project_id": None}),
        ("gitlab", {"source_project_id": "7"}),
        ("gitlab", {"source_project_id": True}),
        ("gitlab", {"source_project_id": 7.0}),
        ("gitlab", {"source_project_id": 0}),
        ("gitlab", {"source_project_id": -1}),
        ("gitlab", {"target_project_id": 0}),
        ("gitlab", {"target_project_id": -1}),
    ],
)
def test_invalid_provider_identity_cannot_supply_proof(
    world, network, provider, changes, local_proof
):
    tip = local_branch(world)
    if local_proof:
        world.commit("unrelated", "independent mainline commit\n")
    world.commit("feature", "branch\n" if local_proof else "later edits\n")
    world.git("remote", "add", "origin", f"git@{provider}.com:owner/repo.git")
    world.git("update-ref", "refs/remotes/origin/main", "main")
    row = evidence(world, provider, **changes)
    env = stub(world, provider, [row], network)
    before = network.network_calls()
    result = cli(world, "feature", "--json", entry="gwbs", env=env)
    assert result.returncode == 0, result.stderr
    assessed = json.loads(result.stdout)["verdicts"][0]
    assert assessed["verdict"] == ("remove" if local_proof else "keep"), assessed
    assert assessed["proof"] != "forge"
    if local_proof:
        assert world.git("rev-parse", "main") != tip
        assert assessed["proof"] in ("squash", "content"), assessed
    deleted = cli(world, "feature", "--yes", "--json", entry="gwbd", env=env)
    if local_proof:
        assert deleted.returncode == 0, deleted.stderr
        assert json.loads(deleted.stdout)["removed"] == ["feature"]
    else:
        assert deleted.returncode == 3, deleted.stderr
        assert json.loads(deleted.stdout)["removed"] == []
        assert world.git("rev-parse", "feature") == tip
    assert network.network_calls() == before
