"""Forge evidence must identify this branch tip and a merge into its head."""

from __future__ import annotations

import json

import pytest
from conftest import env_for
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


def stub(world, provider, payload) -> dict[str, str]:
    bin_dir = world.root / "forge-bin"
    bin_dir.mkdir(exist_ok=True)
    tool = "gh" if provider == "github" else "glab"
    target = f"{provider}.com/owner/repo"
    if provider == "gitlab":
        target = "https://" + target
    exe = bin_dir / tool
    # A wrong repository selection or an inherited redirect must not produce
    # the response that could authorize a deletion.
    exe.write_text(
        "#!/bin/sh\n"
        'test -z "${GH_REPO-}${GITLAB_REPO-}" || exit 8\n'
        f'case " $* " in *" --repo {target} "*) ;; *) exit 9;; esac\n'
        f"cat <<'JSON'\n{json.dumps(payload)}\nJSON\n"
    )
    exe.chmod(0o755)
    env = env_for(world)
    env["PATH"] = f"{bin_dir}:{env['PATH']}"
    env["GH_REPO"] = env["GITLAB_REPO"] = "wrong/repo"
    return env


@pytest.fixture(params=["github", "gitlab"])
def forge_world(world, request):
    provider = request.param
    local_branch(world)
    world.commit("feature", "later edits\n")
    world.git("remote", "add", "origin", f"git@{provider}.com:owner/repo.git")
    world.git("update-ref", "refs/remotes/origin/main", "main")
    return world, provider


def assess(world, provider, row) -> dict:
    env = stub(world, provider, [row])
    p = cli(world, "branch", "status", "feature", "--no-fetch", "--json", env=env)
    assert p.returncode == 0, p.stderr
    return json.loads(p.stdout)["verdicts"][0]


def test_merged_request_matches_source_tip_and_reachable_merge(forge_world):
    world, provider = forge_world
    row = assess(world, provider, evidence(world, provider))
    assert row["verdict"] == "remove", row
    assert row["proof"] == "forge"
    assert row["request"]["number"] == 17
    assert row["request"]["source_sha"] == world.git("rev-parse", "feature")


@pytest.mark.parametrize(
    "mismatch", ["tip", "target", "source", "fork", "merge", "missing"]
)
def test_unverified_merge_cannot_authorize_deletion(forge_world, mismatch):
    world, provider = forge_world
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
    result = assess(world, provider, row)
    assert result["verdict"] != "remove", result


@pytest.mark.parametrize("state", ["closed", "open"])
def test_closed_or_open_requests_do_not_authorize_deletion(forge_world, state):
    world, provider = forge_world
    row = evidence(
        world, provider, state=state.upper() if provider == "github" else state
    )
    result = assess(world, provider, row)
    assert result["verdict"] == "keep", result
    assert result["request"]["state"] == state.upper()


def test_closed_request_does_not_block_independent_content_proof(forge_world):
    world, provider = forge_world
    world.commit("feature", "branch\n")
    world.git("update-ref", "refs/remotes/origin/main", "main")
    result = assess(world, provider, evidence(world, provider, state="CLOSED"))
    assert result["verdict"] == "remove", result
    assert result["proof"] in ("squash", "content")


@pytest.mark.parametrize("payload", [None, {}, [None], [{"number": "invalid"}]])
def test_malformed_forge_data_keeps_the_branch(forge_world, payload):
    world, provider = forge_world
    env = stub(world, provider, payload)
    p = cli(
        world, "branch", "delete", "feature", "--yes", "--no-fetch", "--json", env=env
    )
    assert p.returncode == 3, p.stderr
    assert json.loads(p.stdout)["removed"] == []
    assert world.git("rev-parse", "feature")


@pytest.mark.parametrize(
    ("source", "target"), [(True, True), (1, True), (7, 7.0), (None, None)]
)
def test_gitlab_requires_integer_project_ids(world, source, target):
    local_branch(world)
    world.commit("feature", "later edits\n")
    world.git("remote", "add", "origin", "git@gitlab.com:owner/repo.git")
    world.git("update-ref", "refs/remotes/origin/main", "main")
    row = evidence(world, "gitlab", source_project_id=source, target_project_id=target)
    assert assess(world, "gitlab", row)["verdict"] != "remove"
