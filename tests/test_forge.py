"""Offline worktree assessment accepts only verified, source-tip sync evidence."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from test_branch_forge import evidence
from test_branches import cli
from test_sync import cache_path, origin

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


def worktree_status(world, network) -> dict:
    proc = cli(world, "--json", entry="gws", env=network.env())
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
        import sys

        proc = subprocess.run(
            [
                sys.executable,
                "-c",
                "from worktrees.cli import gws; raise SystemExit(gws())",
                "--json",
            ],
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
    assert row["verdict"] == "keep"
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
    assert worktree_status(world, network)["verdict"] != "remove"
    assert wt.exists()


def test_explicit_sync_is_the_only_forge_query_for_repeated_assessment(world, network):
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
