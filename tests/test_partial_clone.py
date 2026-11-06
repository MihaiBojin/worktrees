"""Offline assessments refuse implicit retrieval of missing promisor objects."""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

import pytest
from conftest import World, env_for
from test_branches import cli


def partial_clone(world) -> World:
    source = world.worktree("feature")
    world.commit("feature", "missing blob\n", at=source)
    remote = world.root / "remote.git"
    world.git("clone", "--quiet", "--bare", str(world.repo), str(remote))
    world.git("config", "uploadpack.allowFilter", "true", at=remote)
    clone = world.root / "partial"
    world.git(
        "clone",
        "--quiet",
        "--filter=blob:none",
        "--no-checkout",
        remote.as_uri(),
        str(clone),
    )
    partial = World(world.root)
    partial.repo = clone
    partial.git("branch", "feature", "origin/feature")
    partial.git(
        "worktree",
        "add",
        "--quiet",
        "--no-checkout",
        str(world.root / "feature"),
        "feature",
    )
    return partial


def git_snapshot(repo: Path) -> dict:
    return {
        str(path.relative_to(repo)): (
            path.read_bytes(),
            path.stat().st_mtime_ns,
            path.stat().st_mode,
        )
        for path in (repo / ".git").rglob("*")
        if path.is_file()
    }


def trace_children(path: Path) -> list[list[str]]:
    return [
        event["argv"]
        for event in (json.loads(line) for line in path.read_text().splitlines())
        if event.get("event") == "child_start"
    ]


@pytest.mark.parametrize(
    "entry,args", [("gw", ()), ("gwp", ("--dry-run",)), ("gwbs", ())]
)
def test_partial_clone_assessment_never_fetches_or_writes(world, entry, args):
    partial = partial_clone(world)
    if entry == "gwbs":
        partial.git("branch", "loose", "origin/feature")
    trace = world.root / "assessment-trace.jsonl"
    before = git_snapshot(partial.repo)
    result = cli(
        partial,
        *args,
        "--json",
        entry=entry,
        env={
            **env_for(partial),
            "GIT_NO_LAZY_FETCH": "0",
            "GIT_TRACE2_EVENT": str(trace),
        },
    )
    assert result.returncode != 0, result.stdout
    assert git_snapshot(partial.repo) == before
    assert not any(
        "fetch" in child or any("upload-pack" in arg for arg in child)
        for child in trace_children(trace)
    )


def git_version_stub(world, version: str) -> Path:
    directory = world.root / "git-version-bin"
    directory.mkdir()
    real_git = shutil.which("git")
    binary = directory / "git"
    binary.write_text(
        "#!/usr/bin/env python3\n"
        "import os, sys\n"
        f"real_git = {real_git!r}\n"
        "if sys.argv[1:] == ['--version']:\n"
        f"    print('git version {version}')\n"
        "    raise SystemExit(0)\n"
        "os.execv(real_git, [real_git, *sys.argv[1:]])\n"
    )
    binary.chmod(0o755)
    return directory


@pytest.mark.parametrize(
    "version",
    [
        "2.39.3",
        "2.40.1",
        "2.41.0",
        "2.42.1",
        "2.43.0",
        "2.43.3",
        "2.44.0",
        "2.45.0",
        "unknown",
    ],
)
def test_unsafe_git_partial_clone_refuses_before_object_commands(world, version):
    partial = partial_clone(world)
    stub = git_version_stub(world, version)
    trace = world.root / "unsupported-trace.jsonl"
    before = git_snapshot(partial.repo)
    env = env_for(partial)
    result = cli(
        partial,
        "--json",
        env={
            **env,
            "PATH": f"{stub}{os.pathsep}{env['PATH']}",
            "GIT_TRACE2_EVENT": str(trace),
            "GIT_NO_LAZY_FETCH": "0",
        },
    )
    assert result.returncode != 0, result.stdout
    assert "partial clone" in result.stderr.lower()
    assert "Git" in result.stderr
    assert git_snapshot(partial.repo) == before
    events = [json.loads(line) for line in trace.read_text().splitlines()]
    commands = [event["name"] for event in events if event.get("event") == "cmd_name"]
    assert commands and set(commands) <= {"config"}, commands
    assert not any("fetch" in child for child in trace_children(trace))


@pytest.mark.parametrize(
    "version",
    [
        "2.39.4",
        "2.40.2",
        "2.41.1",
        "2.42.2",
        "2.43.4",
        "2.44.1",
        "2.45.1",
        "2.46.0",
        "2.55.0",
    ],
)
def test_patched_git_partial_clone_uses_local_objects_only(world, version):
    partial = partial_clone(world)
    stub = git_version_stub(world, version)
    env = env_for(partial)
    trace = world.root / "patched-trace.jsonl"
    before = git_snapshot(partial.repo)
    result = cli(
        partial,
        "--json",
        env={
            **env,
            "PATH": f"{stub}{os.pathsep}{env['PATH']}",
            "GIT_TRACE2_EVENT": str(trace),
            "GIT_NO_LAZY_FETCH": "0",
        },
    )
    assert result.returncode != 0, result.stdout
    assert "partial clone requires" not in result.stderr.lower()
    assert git_snapshot(partial.repo) == before
    assert not any("fetch" in child for child in trace_children(trace))


def test_old_git_full_clone_remains_usable(world):
    world.merged_branch("done")
    stub = git_version_stub(world, "2.43.0")
    env = env_for(world)
    result = cli(
        world, "--json", env={**env, "PATH": f"{stub}{os.pathsep}{env['PATH']}"}
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["verdicts"]


def test_explicit_sync_still_fetches_a_real_local_remote(world):
    remote = world.root / "remote.git"
    world.git("clone", "--quiet", "--bare", str(world.repo), str(remote))
    world.git("remote", "add", "origin", remote.as_uri())
    trace = world.root / "sync-trace.jsonl"
    result = cli(
        world,
        "sync",
        "--json",
        env={
            **env_for(world),
            "GIT_TRACE2_EVENT": str(trace),
            "GIT_NO_LAZY_FETCH": "0",
        },
    )
    assert result.returncode == 0, result.stderr
    assert world.git("rev-parse", "refs/remotes/origin/main") == world.git(
        "rev-parse", "main"
    )
    assert json.loads(result.stdout)["lastSync"]["refs"]
    assert any(
        any("upload-pack" in arg for arg in child) for child in trace_children(trace)
    )


@pytest.mark.parametrize(
    "key,value",
    [
        ("remote.origin.promisor", "true"),
        ("remote.origin.partialCloneFilter", "blob:none"),
        ("extensions.partialClone", "origin"),
    ],
)
def test_each_partial_clone_configuration_requires_patched_git(world, key, value):
    partial = partial_clone(world)
    partial.git("config", "--unset-all", "remote.origin.promisor")
    partial.git("config", "--unset-all", "remote.origin.partialCloneFilter")
    partial.git("config", key, value)
    stub = git_version_stub(world, "2.43.0")
    env = env_for(partial)
    result = cli(
        partial, "--json", env={**env, "PATH": f"{stub}{os.pathsep}{env['PATH']}"}
    )
    assert result.returncode == 3, result.stderr
    assert "partial clone requires Git" in result.stderr


@pytest.mark.parametrize("value", ["false", "no", "off", "0", ""])
def test_false_promisor_flag_does_not_restrict_full_clones(world, value):
    world.git("config", "remote.origin.promisor", value)
    stub = git_version_stub(world, "2.43.0")
    env = env_for(world)
    result = cli(
        world, "--json", env={**env, "PATH": f"{stub}{os.pathsep}{env['PATH']}"}
    )
    assert result.returncode == 0, result.stderr


def test_partial_clone_usage_errors_precede_git_capability_checks(world):
    partial = partial_clone(world)
    stub = git_version_stub(world, "2.43.0")
    env = env_for(partial)
    result = cli(
        partial,
        "--only",
        "empty",
        "--yes",
        entry="gwp",
        env={**env, "PATH": f"{stub}{os.pathsep}{env['PATH']}"},
    )
    assert result.returncode == 2, result.stderr
    assert "--only requires --dry-run" in result.stderr
    assert "partial clone requires" not in result.stderr


@pytest.mark.parametrize(
    "args", [("--help",), ("version",), ("--complete",), ("branch", "--complete")]
)
def test_partial_clone_metadata_commands_do_not_require_patched_git(world, args):
    partial = partial_clone(world)
    stub = git_version_stub(world, "2.43.0")
    env = env_for(partial)
    result = cli(
        partial, *args, env={**env, "PATH": f"{stub}{os.pathsep}{env['PATH']}"}
    )
    assert result.returncode == 0, result.stderr
    assert "partial clone requires" not in result.stderr


def test_command_environment_cannot_enable_lazy_fetch(world, monkeypatch):
    from worktrees.git import git

    partial = partial_clone(world)
    blob = world.git("rev-parse", "feature:feature")
    trace = world.root / "object-trace.jsonl"
    monkeypatch.setenv("GIT_TRACE2_EVENT", str(trace))
    monkeypatch.setenv("GIT_NO_LAZY_FETCH", "0")

    @git("cat-file -e $sha", ok=(0, 1), env={"GIT_NO_LAZY_FETCH": "0"})
    def has_object(sha: str) -> None:
        """Check local availability without retrieving a missing object."""

    before = git_snapshot(partial.repo)
    assert has_object(blob, repo=partial.repo).code == 1
    assert git_snapshot(partial.repo) == before
    assert not any("fetch" in child for child in trace_children(trace))


def test_explicit_sync_is_allowed_on_older_partial_clones(world):
    partial = partial_clone(world)
    stub = git_version_stub(world, "2.43.0")
    env = env_for(partial)
    trace = world.root / "old-sync-trace.jsonl"
    result = cli(
        partial,
        "sync",
        "--json",
        env={
            **env,
            "PATH": f"{stub}{os.pathsep}{env['PATH']}",
            "GIT_TRACE2_EVENT": str(trace),
        },
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["lastSync"]["refs"]
    assert any(
        any("upload-pack" in arg for arg in child) for child in trace_children(trace)
    )
