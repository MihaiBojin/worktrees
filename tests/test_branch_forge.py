"""Forge evidence comes from the last `gw sync`, and must identify this
branch tip and a merge into its head."""

from __future__ import annotations

import json
import subprocess
import sys

import pytest
from conftest import env_for
from test_branches import cli, local_branch

NAME = "git-worktrees-sync.json"


def evidence(world, provider, **changes) -> dict:
    """One row as `gh` or `glab` prints it, for the tip of `feature`."""
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


def merged(world, branch: str = "feature", **changes) -> dict:
    """The evidence `gw sync` records for a request that merged `branch`'s
    tip into main, with `changes` applied to the request."""
    tip = world.git("rev-parse", branch)
    request = {
        "number": 17,
        "state": "MERGED",
        "noun": "pull request",
        "url": "https://github.com/owner/repo/pull/17",
        "repository": "github.com/owner/repo",
        "source_branch": branch,
        "source_sha": tip,
        "target_branch": "main",
        "merge_sha": world.git("rev-parse", "main"),
        "merged_at": "2026-09-28T00:00:00Z",
        "same_repository": True,
    }
    return {branch: {"sha": tip, "request": {**request, **changes}}}


def synced(world, evidence: dict) -> None:
    """Write the file `gw sync` writes, as though it had just run."""
    subprocess.run(
        [
            sys.executable,
            "-c",
            "import json, sys; from worktrees import sync; t = sync.now(); "
            "sync.write('origin', t, t, json.loads(sys.argv[1]))",
            json.dumps(evidence),
        ],
        cwd=world.repo,
        env=env_for(world),
        check=True,
    )


@pytest.fixture
def forge_world(world):
    """A loose branch whose change content cannot settle: main edited the
    same path further, the shape a branch merged as part of a stack leaves."""
    local_branch(world)
    world.commit("feature", "later edits\n")
    world.git("remote", "add", "origin", "git@github.com:owner/repo.git")
    world.git("update-ref", "refs/remotes/origin/main", "main")
    return world


def assess(world) -> dict:
    p = cli(world, "branch", "status", "feature", "--no-fetch", "--json")
    assert p.returncode == 0, p.stderr
    return json.loads(p.stdout)["verdicts"][0]


def test_without_a_sync_git_alone_decides(forge_world):
    assert assess(forge_world)["verdict"] == "keep"


def test_a_synced_merged_request_proves_the_branch(forge_world):
    world = forge_world
    synced(world, merged(world))
    row = assess(world)
    assert row["verdict"] == "remove", row
    assert row["proof"] == "forge"
    assert row["why"] == "pull request #17 merged this tip into the head branch"
    assert row["request"]["number"] == 17
    assert row["request"]["source_sha"] == world.git("rev-parse", "feature")


def test_a_moved_tip_has_no_evidence(forge_world):
    world = forge_world
    synced(world, merged(world))
    world.git("checkout", "-q", "feature")
    world.commit("after", "a\n")
    world.git("checkout", "-q", "main")
    row = assess(world)
    assert (row["verdict"], row["proof"], row["request"]) == ("keep", "", None)


@pytest.mark.parametrize(
    ("changes", "why"),
    [
        (
            {"target_branch": "release"},
            "the request targeted a different branch",
        ),
        (
            {"source_branch": "other"},
            "the request does not identify this branch tip",
        ),
        (
            {"same_repository": False},
            "the request's source repository does not match this remote",
        ),
        (
            {"merged_at": ""},
            "the request has no verified merge commit",
        ),
        (
            {"merge_sha": "0" * 40},
            "the request's merge commit is not available locally",
        ),
    ],
    ids=["target", "source", "fork", "unmerged-at", "merge-missing"],
)
def test_a_mismatched_request_proves_nothing(forge_world, changes, why):
    world = forge_world
    synced(world, merged(world, **changes))
    row = assess(world)
    assert (row["verdict"], row["why"]) == ("unknown", why), row


def test_a_merge_commit_outside_the_head_proves_nothing(forge_world):
    world = forge_world
    world.git("checkout", "-qb", "elsewhere")
    outside = world.commit("elsewhere", "e\n")
    world.git("checkout", "-q", "main")
    synced(world, merged(world, merge_sha=outside))
    row = assess(world)
    assert (row["verdict"], row["why"]) == (
        "unknown",
        "the request's merge commit is not in the head branch",
    )


@pytest.mark.parametrize(
    "changes",
    [{"number": "17"}, {"same_repository": 1}, {"merge_sha": None}, {"extra": 1}],
)
def test_a_malformed_entry_proves_nothing(forge_world, changes):
    world = forge_world
    synced(world, merged(world, **changes))
    row = assess(world)
    assert (row["verdict"], row["request"]) == ("keep", None), row


@pytest.mark.parametrize("text", ["", "{", "[]", '{"remote": 1}'])
def test_a_malformed_file_proves_nothing(forge_world, text):
    world = forge_world
    synced(world, merged(world))
    (world.repo / ".git" / NAME).write_text(text)
    assert assess(world)["verdict"] == "keep"


def test_a_file_for_another_remote_url_proves_nothing(forge_world):
    world = forge_world
    synced(world, merged(world))
    assert assess(world)["verdict"] == "remove", "the control"
    world.git("remote", "set-url", "origin", "git@github.com:someone/else.git")
    assert assess(world)["verdict"] == "keep"


@pytest.mark.parametrize("state", ["CLOSED", "OPEN"])
def test_closed_or_open_requests_do_not_authorize_deletion(forge_world, state):
    world = forge_world
    synced(world, merged(world, state=state))
    result = assess(world)
    assert result["verdict"] == "keep", result
    assert result["why"] == f"pull request #17 is {state.lower()}, not merged"
    assert result["request"]["state"] == state


def test_a_rejected_request_does_not_block_content_proof(forge_world):
    world = forge_world
    world.commit("feature", "branch\n")
    world.git("update-ref", "refs/remotes/origin/main", "main")
    for changes in ({"state": "CLOSED"}, {"target_branch": "release"}):
        synced(world, merged(world, **changes))
        result = assess(world)
        assert result["verdict"] == "remove", result
        assert result["proof"] in ("squash", "content")


def test_delete_removes_a_branch_the_sync_proved(forge_world):
    world = forge_world
    synced(world, merged(world))
    p = cli(world, "branch", "delete", "feature", "--yes", "--no-fetch", "--json")
    assert p.returncode == 0, p.stderr
    assert json.loads(p.stdout)["removed"] == ["feature"]


def test_delete_keeps_a_branch_the_sync_did_not_prove(forge_world):
    world = forge_world
    synced(world, merged(world, same_repository=False))
    p = cli(world, "branch", "delete", "feature", "--yes", "--no-fetch", "--json")
    assert p.returncode == 3, p.stderr
    assert json.loads(p.stdout)["removed"] == []
    assert world.git("rev-parse", "feature")
