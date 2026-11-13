"""Offline worktree assessment accepts only verified, source-tip sync evidence."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from conftest import argv
from test_branch_forge import evidence
from test_branches import cli
from test_sync import cache_path, first_parent_proof, origin

from worktrees import forge


def synced_worktree(world, network) -> Path:
    origin(world)
    wt = world.worktree("feature")
    world.commit("feature", "branch\n", at=wt)
    world.commit("feature", "later edits\n")
    world.git("update-ref", "refs/remotes/origin/main", "main")
    network.answer({"feature": [evidence(world, "github")]})
    proc = cli(world, "sync", env=network.env())
    assert proc.returncode == 0, proc.stderr
    return wt


def worktree_status(world, network, **env: str) -> dict:
    proc = cli(world, "--json", entry="gw prune", env=network.env(**env))
    assert proc.returncode == 0, proc.stderr
    return next(
        row for row in json.loads(proc.stdout)["verdicts"] if row["branch"] == "feature"
    )


def test_synced_request_proves_worktree_completion_without_an_upstream(world, network):
    synced_worktree(world, network)
    assert worktree_status(world, network)["verdict"] == "remove"


@pytest.mark.parametrize("protection", ["dirty", "ignored", "locked", "current"])
def test_cached_forge_proof_keeps_worktree_protections(world, network, protection):
    wt = synced_worktree(world, network)
    if protection == "dirty":
        (wt / "feature").write_text("unfinished\n")
    elif protection == "ignored":
        world.git("config", "core.excludesFile", str(world.root / "ignore"))
        (world.root / "ignore").write_text("cache\n")
        (wt / "cache").write_text("keep\n")
    elif protection == "locked":
        world.git("worktree", "lock", str(wt))
    if protection == "current":
        import subprocess

        proc = subprocess.run(
            argv("gw prune", "--json"),
            cwd=wt,
            env=network.env(),
            capture_output=True,
            text=True,
        )
        row = next(
            row
            for row in json.loads(proc.stdout)["verdicts"]
            if row["branch"] == "feature"
        )
    else:
        row = worktree_status(world, network)
    assert row["verdict"] == "remove"
    assert not row["eligible"]
    assert wt.exists()


@pytest.mark.parametrize("change", ["tip", "malformed", "source", "target", "merge"])
def test_invalid_cache_cannot_prove_worktree_completion(world, network, change):
    wt = synced_worktree(world, network)
    if change == "tip":
        world.commit("new-work", at=wt)
    elif change == "malformed":
        cache_path(world).write_text("{broken")
    else:
        cached = json.loads(cache_path(world).read_text())
        row = cached["branches"]["feature"]["request"]
        if change == "source":
            row["source_verified"] = False
        elif change == "target":
            row["repository"] = "github.com/other/repo"
        else:
            row["merge_sha"] = "0" * 40
        cache_path(world).write_text(json.dumps(cached))
    # Offline, since the sync a broken cache makes due would rebuild it.
    offline = {"WORKTREES_OFFLINE": "1"}
    assert worktree_status(world, network, **offline)["verdict"] != "remove"
    assert wt.exists()


def test_a_fresh_sync_serves_repeated_assessment(world, network):
    synced_worktree(world, network)
    calls = network.network_calls()
    for _ in range(2):
        assert worktree_status(world, network)["verdict"] == "remove"
    assert network.network_calls() == calls


@pytest.mark.parametrize("tool, variable", [("gh", "GH_REPO"), ("glab", "GITLAB_REPO")])
def test_forge_query_removes_repository_redirects(
    tmp_path, monkeypatch, tool, variable
):
    seen = tmp_path / "seen"
    executable = tmp_path / tool
    executable.write_text(
        f'#!/bin/sh\nprintf "%s" "${{{variable}-unset}}" > {seen}\nprintf "[]\\n"\n'
    )
    executable.chmod(0o755)
    monkeypatch.setenv("PATH", str(tmp_path))
    monkeypatch.setenv(variable, "someone/else")
    assert forge.query([tool, "query"]) == []
    assert seen.read_text() == "unset"


def test_forge_query_keeps_authentication_environment(tmp_path, monkeypatch):
    seen = tmp_path / "seen"
    executable = tmp_path / "gh"
    executable.write_text(
        f'#!/bin/sh\nprintf "%s" "$GH_TOKEN" > {seen}\nprintf "[]\\n"\n'
    )
    executable.chmod(0o755)
    monkeypatch.setenv("PATH", str(tmp_path))
    monkeypatch.setenv("GH_TOKEN", "kept")
    assert forge.query(["gh", "query"]) == []
    assert seen.read_text() == "kept"


@pytest.mark.parametrize("local_proof", [False, True])
@pytest.mark.parametrize(
    "field,value",
    [
        (None, []),
        (None, "request"),
        ("missing", "source_repository"),
        ("extra", True),
        ("number", True),
        ("number", "17"),
        ("number", 0),
        ("source_verified", "true"),
        ("source_sha", 17),
        ("merged_at", True),
        ("merge_sha", "short"),
        ("merge_sha", "g" * 40),
        ("merge_sha", "a" * 39),
        ("merge_sha", "a" * 41),
        ("merge_sha", "A" * 40),
        ("merge_sha", "a" * 40 + "\n"),
    ],
)
def test_malformed_cached_request_cannot_authorize_deletion(
    world, network, local_proof, field, value
):
    if local_proof:
        first_parent_proof(world, network, "github")
        wt = world.root / "feature"
        world.git("worktree", "add", "--quiet", str(wt), "feature")
    else:
        wt = synced_worktree(world, network)
    tip = world.git("rev-parse", "feature")
    cached = json.loads(cache_path(world).read_text())
    request = cached["branches"]["feature"]["request"]
    if field is None:
        cached["branches"]["feature"]["request"] = value
    elif field == "missing":
        request.pop(value)
    else:
        request[field] = value
    cache_path(world).write_text(json.dumps(cached))
    before = network.network_calls()
    offline = network.env(WORKTREES_OFFLINE="1")
    want = "empty" if local_proof else "unknown" if field == "merge_sha" else "keep"
    row = worktree_status(world, network, WORKTREES_OFFLINE="1")
    assert row["verdict"] == want, row
    assert row["proof"] != "forge", row
    assert not row["eligible"]
    denied = cli(world, str(wt), "--yes", "--json", entry="gw remove", env=offline)
    assert denied.returncode != 0, denied.stderr
    assert wt.exists()
    assert world.git("rev-parse", "feature") == tip
    world.git("worktree", "remove", str(wt))
    loose = cli(world, "feature", "--json", entry="gw branch status", env=offline)
    assert loose.returncode == 0, loose.stderr
    row = json.loads(loose.stdout)["verdicts"][0]
    assert row["verdict"] == want, row
    assert row["proof"] != "forge", row
    denied = cli(
        world, "feature", "--yes", "--json", entry="gw branch prune", env=offline
    )
    assert denied.returncode != 0, denied.stderr
    assert json.loads(denied.stdout)["removed"] == []
    assert world.git("rev-parse", "feature") == tip
    assert network.network_calls() == before
