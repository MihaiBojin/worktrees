"""Synchronization is explicit, shared by worktrees, and safe to read offline."""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest
from test_branches import cli, local_branch


def origin(world, host="github.com", name="origin") -> None:
    world.git("remote", "add", name, f"git@{host}:owner/repo.git")
    world.git("update-ref", f"refs/remotes/{name}/main", "main")


def listing(world, network, entry="gwl", at=None) -> dict:
    proc = subprocess.run(
        [
            sys.executable,
            "-c",
            f"from worktrees.cli import {entry}; raise SystemExit({entry}())",
            "--json",
        ],
        cwd=at or world.repo,
        env=network.env(),
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


def cache_path(world) -> Path:
    return world.repo / ".git" / "git-worktrees-sync.json"


def test_json_envelope_preserves_worktrees(world, network):
    data = listing(world, network)
    assert isinstance(data, dict)
    assert data["lastSync"] == {"refs": None, "forge": None}
    assert data["head"] == {"ref": "refs/heads/main", "source": "inferred"}
    assert data["worktrees"][0]["path"] == str(world.repo)


@pytest.mark.parametrize("entry", ["gwl", "gwbs"])
@pytest.mark.parametrize(
    "case, expected",
    [
        ("main", "main"),
        ("master", "master"),
        ("main-newer", "main"),
        ("master-newer", "master"),
        ("tie", "main"),
        ("neither", None),
    ],
)
def test_head_inference_uses_local_committer_dates(
    world, network, monkeypatch, entry, case, expected
):
    if case == "master":
        world.git("branch", "-m", "main", "master")
    elif case == "neither":
        world.git("branch", "-m", "main", "feature")
    elif case in ("tie", "main-newer", "master-newer"):
        world.git("branch", "master")
        if case != "tie":
            branch = case.removesuffix("-newer")
            world.git("switch", "-q", branch)
            monkeypatch.setenv("GIT_COMMITTER_DATE", "@1800000000 +0000")
            world.commit("newer")
    data = listing(world, network, entry)
    assert data["head"] == {
        "ref": f"refs/heads/{expected}" if expected else None,
        "source": "inferred",
    }
    if case == "neither" and entry == "gwbs":
        assert all(row["verdict"] == "unknown" for row in data["verdicts"])


@pytest.mark.parametrize("entry", ["gwl", "gwbs"])
def test_clone_head_becomes_synced_remote_head(world, network, entry):
    origin(world)
    world.git("symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/main")
    assert listing(world, network, entry)["head"] == {
        "ref": "refs/remotes/origin/HEAD",
        "source": "clone",
    }
    proc = cli(world, "sync", env=network.env())
    assert proc.returncode == 0, proc.stderr
    data = listing(world, network, entry)
    assert data["head"] == {"ref": "refs/remotes/origin/HEAD", "source": "remote"}
    for timestamp in data["lastSync"].values():
        assert timestamp.endswith("Z")
        assert datetime.fromisoformat(timestamp.replace("Z", "+00:00")).tzinfo == UTC


@pytest.mark.parametrize(
    "names, configured, expected",
    [
        ([], None, []),
        (["upstream"], None, ["upstream"]),
        (["origin", "upstream"], "upstream", ["upstream"]),
        (["origin", "upstream"], None, []),
    ],
)
def test_sync_selects_only_the_default_remote(
    world, network, names, configured, expected
):
    for name in names:
        origin(world, name=name)
    if len(names) > 1 and not configured:
        world.git("config", "branch.main.remote", names[0])
        world.git("config", "remote.pushDefault", names[0])
    if configured:
        world.git("config", "checkout.defaultRemote", configured)
    proc = cli(world, "sync", env=network.env())
    assert proc.returncode == (1 if len(names) > 1 and not configured else 0), (
        proc.stderr
    )
    fetches = [call[-1] for call in network.network_calls() if call[1:2] == ["fetch"]]
    assert fetches == expected
    if len(names) > 1 and not configured:
        assert "origin" in proc.stderr and "upstream" in proc.stderr
        assert "git config checkout.defaultRemote <name>" in proc.stderr
    if not names:
        assert listing(world, network)["lastSync"] == {"refs": None, "forge": None}


def test_sync_queries_every_tip_including_checked_out_branches(world, network):
    origin(world)
    world.worktree("checked")
    local_branch(world, "loose")
    proc = cli(world, "sync", env=network.env())
    assert proc.returncode == 0, proc.stderr
    calls = network.network_calls()
    assert calls[:2] == [
        ["git", "fetch", "--prune", "origin"],
        ["git", "remote", "set-head", "origin", "--auto"],
    ]
    queries = [
        call for call in calls if call[0] == "gh" and call[1:3] == ["pr", "list"]
    ]
    assert {call[call.index("--head") + 1] for call in queries} == {
        "main",
        "checked",
        "loose",
    }
    assert all(
        call[call.index("--repo") + 1] == "github.com/owner/repo" for call in queries
    )
    cached = json.loads(cache_path(world).read_text())
    assert cached["remote"] == {
        "name": "origin",
        "url": "git@github.com:owner/repo.git",
    }
    assert all(entry["request"] is None for entry in cached["branches"].values())


@pytest.mark.parametrize("provider", ["github", "gitlab"])
@pytest.mark.parametrize("failure", ["fetch", "forge", "branch", "missing", "head"])
def test_failed_sync_components_keep_previous_times(world, network, failure, provider):
    origin(world, provider + ".com")
    local_branch(world)
    assert cli(world, "sync", env=network.env()).returncode == 0
    cached = json.loads(cache_path(world).read_text())
    cached["lastSync"] = {
        "refs": "2020-01-01T00:00:00Z",
        "forge": "2020-01-02T00:00:00Z",
    }
    cache_path(world).write_text(json.dumps(cached))
    env = network.env()
    if failure == "fetch":
        env["WT_FETCH_CODE"] = "128"
    elif failure == "forge":
        env["WT_FORGE_CODE"] = "1"
    elif failure == "head":
        env["WT_HEAD_CODE"] = "128"
    elif failure == "branch":
        network.answer({"feature": 1})
    else:
        (network.bin / ("gh" if provider == "github" else "glab")).unlink()
        env["PATH"] = str(network.bin)
    proc = cli(world, "sync", env=env)
    assert proc.returncode != 0
    now = json.loads(cache_path(world).read_text())
    if failure in ("fetch", "head"):
        assert now == cached
    else:
        assert now["lastSync"]["refs"] != cached["lastSync"]["refs"]
        assert now["lastSync"]["forge"] == cached["lastSync"]["forge"]
    if failure == "branch":
        assert "feature" not in now["branches"]


def test_non_forge_host_has_no_forge_time(world, network):
    origin(world, "code.example.net")
    proc = cli(world, "sync", env=network.env())
    assert proc.returncode == 0, proc.stderr
    data = listing(world, network)
    assert data["lastSync"]["refs"] is not None
    assert data["lastSync"]["forge"] is None
    assert not any(call[0] in ("gh", "glab") for call in network.network_calls())


@pytest.mark.parametrize("change", ["name", "url", "multiple"])
def test_remote_identity_change_invalidates_cache(world, network, change):
    origin(world)
    assert cli(world, "sync", env=network.env()).returncode == 0
    if change == "name":
        world.git("remote", "rename", "origin", "renamed")
    elif change == "url":
        world.git("remote", "set-url", "origin", "git@github.com:other/repo.git")
    else:
        origin(world, name="other")
    assert listing(world, network)["lastSync"] == {"refs": None, "forge": None}


def test_worktrees_share_one_sync_cache(world, network):
    origin(world)
    linked = world.worktree("linked")
    assert cli(world, "sync", env=network.env()).returncode == 0
    assert (
        listing(world, network, at=linked)["lastSync"]
        == listing(world, network)["lastSync"]
    )


@pytest.mark.parametrize("payload", ["{", "[]", "null", '{"lastSync": 1}'])
def test_malformed_cache_is_never_evidence(world, network, payload):
    origin(world)
    cache_path(world).write_text(payload)
    assert listing(world, network)["lastSync"] == {"refs": None, "forge": None}


@pytest.mark.parametrize(
    "entry, args", [("gwa", ["new"]), ("gwbc", ["new"]), ("gwrot", [])]
)
def test_branch_creation_refuses_unresolved_inferred_head(world, network, entry, args):
    world.git("branch", "-m", "main", "feature")
    proc = cli(world, *args, entry=entry, env=network.env())
    assert proc.returncode != 0
    assert "No main or master branch." in proc.stderr
    assert not network.network_calls()


@pytest.mark.parametrize(
    "entry, args",
    [
        ("gws", ["--json"]),
        ("gwp", ["--yes", "--json"]),
        ("gwbs", ["--json"]),
        ("gwbc", ["new", "--json"]),
        ("gwm", ["renamed", "--json"]),
        ("gwbd", ["--dry-run", "--json"]),
        ("gwa", ["new", "--json"]),
        ("gwl", ["--json"]),
        ("gwr", ["no-such-worktree", "--yes"]),
        ("gwrot", ["--json"]),
    ],
)
def test_ordinary_commands_never_use_network(world, network, entry, args):
    origin(world)
    world.git("switch", "-qc", "feature")
    world.commit("work")
    proc = cli(world, *args, entry=entry, env=network.env())
    assert not network.network_calls(), (proc.stderr, network.network_calls())
    if proc.returncode == 0 and "--json" in args:
        data = json.loads(proc.stdout)
        assert "lastSync" in data and "head" in data


@pytest.mark.parametrize(
    "entry", ["gws", "gwp", "gwbs", "gwbd", "gwa", "gwbc", "gwrot", "gwr"]
)
@pytest.mark.parametrize("flag", ["--no-fetch", "--no-forge", "--force-refresh"])
def test_network_flags_are_usage_errors(world, network, entry, flag):
    proc = cli(world, flag, entry=entry, env=network.env())
    assert proc.returncode == 2
    assert not network.network_calls()


@pytest.mark.parametrize(
    "seconds, text",
    [(600, None), (601, "10 minutes"), (7200, "2 hours"), (172800, "2 days")],
)
def test_stale_notice_boundary_and_units(
    world, network, monkeypatch, capsys, seconds, text
):
    from worktrees import cli as C
    from worktrees import sync

    origin(world)
    assert cli(world, "sync", env=network.env()).returncode == 0
    cached = json.loads(cache_path(world).read_text())
    cached["lastSync"] = {
        "refs": "2026-01-01T00:00:00Z",
        "forge": "2026-01-01T00:00:00Z",
    }
    cache_path(world).write_text(json.dumps(cached))
    stamp = datetime(2026, 1, 1, tzinfo=UTC).timestamp()
    monkeypatch.setattr(sync.time, "time", lambda: stamp + seconds)
    assert C.gws([]) == 0
    error = capsys.readouterr().err
    if text:
        assert text in error and "gw sync" in error
    else:
        assert "Last sync:" not in error
    assert C.gws(["-q"]) == 0
    assert "Last sync:" not in capsys.readouterr().err
    assert C.gws(["--json"]) == 0
    assert "Last sync:" not in capsys.readouterr().err


@pytest.mark.parametrize("quiet", [False, True])
def test_never_synced_and_inferred_notices_survive_quiet(world, network, quiet):
    origin(world)
    proc = cli(world, *(["-q"] if quiet else []), entry="gws", env=network.env())
    assert proc.returncode == 0, proc.stderr
    assert (
        "Never synced: verdicts use refs from the clone or last fetch "
        "and no forge evidence. Run: gw sync" in proc.stderr
    )
    assert "Head: main (inferred)" in proc.stderr
    data = cli(world, "--json", entry="gws", env=network.env())
    assert "Never synced:" not in data.stderr and "Head:" not in data.stderr
    proc = cli(world, "sync", env=network.env())
    assert "Never synced:" not in proc.stderr and "Head:" not in proc.stderr


def test_repository_without_remote_reports_only_inferred_head(world, network):
    proc = cli(world, entry="gws", env=network.env())
    assert "Head: main (inferred)" in proc.stderr
    assert "Never synced:" not in proc.stderr and "Last sync:" not in proc.stderr


@pytest.mark.parametrize("provider", ["github", "gitlab"])
@pytest.mark.parametrize("identity", ["verified", "missing", "mismatch"])
def test_fork_proof_requires_authoritative_source_identity(
    world, network, provider, identity
):
    from test_branch_forge import evidence

    local_branch(world)
    world.commit("feature", "later edits\n")
    origin(world, provider + ".com")
    row = evidence(world, provider)
    api: dict[str, dict] = {}
    if provider == "github":
        row["isCrossRepository"] = True
        row["headRepository"] = {"nameWithOwner": "fork/repo"}
        endpoint = "repos/fork/repo"
        answer: dict = {
            "full_name": "fork/repo" if identity == "verified" else "other/repo",
            "html_url": "https://github.com/fork/repo",
        }
    else:
        row["source_project_id"] = 8
        endpoint = "projects/8"
        source_host = "gitlab.com" if identity == "verified" else "other.example"
        answer = {
            "id": 8,
            "path_with_namespace": "fork/repo",
            "web_url": f"https://{source_host}/fork/repo",
        }
        api["projects/7"] = {
            "id": 7,
            "path_with_namespace": "owner/repo",
            "web_url": "https://gitlab.com/owner/repo",
        }
    if identity != "missing":
        api[endpoint] = answer
    network.answer({"feature": [row], "$api": api})
    proc = cli(world, "sync", env=network.env())
    assert proc.returncode == (1 if identity == "missing" else 0), proc.stderr
    data = listing(world, network, "gwbs")
    feature = next(row for row in data["verdicts"] if row["branch"] == "feature")
    assert (feature["verdict"] == "remove") is (identity == "verified"), feature
    if identity == "verified":
        assert feature["request"]["source_repository"] == f"{provider}.com/fork/repo"
        assert feature["request"]["repository"] == f"{provider}.com/owner/repo"
    assert not any(
        "branches" in arg for call in network.network_calls() for arg in call
    )


@pytest.mark.parametrize(
    "entry, args", [("gwa", ["new"]), ("gwbc", ["new"]), ("gwrot", [])]
)
def test_dangling_clone_head_refuses_branch_creation_with_sync_hint(
    world, network, entry, args
):
    origin(world)
    world.git("symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/missing")
    assert listing(world, network)["head"] == {"ref": None, "source": "clone"}
    proc = cli(world, *args, entry=entry, env=network.env())
    assert proc.returncode != 0
    assert "gw sync" in proc.stderr
    assert not network.network_calls()


@pytest.mark.parametrize(
    "entry, args",
    [
        ("gwp", ["--yes"]),
        ("gwr", ["linked", "--yes", "--force"]),
        ("gwbd", ["feature", "--yes"]),
    ],
)
def test_unresolved_head_refuses_removal_without_mutation(world, network, entry, args):
    linked = world.worktree("linked")
    world.git("branch", "-m", "main", "feature")
    proc = cli(world, *args, entry=entry, env=network.env())
    assert proc.returncode != 0
    assert "No main or master branch." in proc.stderr
    assert linked.exists()
    assert world.git("rev-parse", "refs/heads/feature")
    mutations = {"fetch", "set-head", "prune", "remove", "update-ref"}
    assert not any(set(call[1:3]) & mutations for call in network.calls())


def test_total_branch_query_failure_removes_previous_proof(world, network):
    origin(world)
    local_branch(world)
    assert cli(world, "sync", env=network.env()).returncode == 0
    network.answer({"main": 1, "feature": 1})
    proc = cli(world, "sync", env=network.env())
    assert proc.returncode != 0
    assert json.loads(cache_path(world).read_text())["branches"] == {}


@pytest.mark.parametrize(
    "identity", ["github.com/other/repo", "other.example/owner/repo"]
)
def test_cache_source_identity_must_match_verification(world, network, identity):
    from test_branch_forge import evidence

    origin(world)
    local_branch(world)
    world.commit("feature", "later edits\n")
    world.git("update-ref", "refs/remotes/origin/main", "main")
    network.answer({"feature": [evidence(world, "github")]})
    assert cli(world, "sync", env=network.env()).returncode == 0
    control = next(
        row
        for row in listing(world, network, "gwbs")["verdicts"]
        if row["branch"] == "feature"
    )
    assert control["verdict"] == "remove", control
    cached = json.loads(cache_path(world).read_text())
    cached["branches"]["feature"]["request"]["source_repository"] = identity
    cache_path(world).write_text(json.dumps(cached))
    row = next(
        row
        for row in listing(world, network, "gwbs")["verdicts"]
        if row["branch"] == "feature"
    )
    assert row["verdict"] != "remove", row


def test_gitlab_project_id_must_belong_to_selected_repository(world, network):
    from test_branch_forge import evidence

    local_branch(world)
    world.commit("feature", "later edits\n")
    origin(world, "gitlab.com")
    row = evidence(world, "gitlab")
    network.answer(
        {
            "feature": [row],
            "$api": {
                "projects/7": {
                    "id": 7,
                    "path_with_namespace": "other/repo",
                    "web_url": "https://gitlab.com/other/repo",
                }
            },
        }
    )
    assert cli(world, "sync", env=network.env()).returncode == 0
    result = next(
        row
        for row in listing(world, network, "gwbs")["verdicts"]
        if row["branch"] == "feature"
    )
    assert result["verdict"] != "remove", result


@pytest.mark.parametrize("component", ["refs", "forge"])
@pytest.mark.parametrize(
    "invalid", ["2026-01-01Z", "2026-01-01 00:00:00Z", "not-a-date"]
)
def test_malformed_sync_timestamp_cannot_supply_proof(
    world, network, component, invalid
):
    from test_branch_forge import evidence

    local_branch(world)
    world.commit("feature", "later edits\n")
    origin(world)
    network.answer({"feature": [evidence(world, "github")]})
    assert cli(world, "sync", env=network.env()).returncode == 0
    cached = json.loads(cache_path(world).read_text())
    cached["lastSync"][component] = invalid
    cache_path(world).write_text(json.dumps(cached))
    data = listing(world, network, "gwbs")
    assert data["lastSync"][component] is None
    row = next(row for row in data["verdicts"] if row["branch"] == "feature")
    assert row["verdict"] != "remove", row


def test_sync_replaces_the_cache_file_without_leaving_partial_files(world, network):
    origin(world)
    assert cli(world, "sync", env=network.env()).returncode == 0
    previous_inode = cache_path(world).stat().st_ino
    assert cli(world, "sync", env=network.env()).returncode == 0
    assert cache_path(world).stat().st_ino != previous_inode
    assert json.loads(cache_path(world).read_text())["lastSync"]["refs"]
    assert not list(cache_path(world).parent.glob("git-worktrees-sync.json.*"))


def test_remote_main_and_local_trunk_do_not_replace_head_inference(world, network):
    origin(world)
    world.git("branch", "-m", "main", "trunk")
    assert listing(world, network)["head"] == {"ref": None, "source": "inferred"}


def test_move_refuses_unresolved_head_without_renaming(world, network):
    linked = world.worktree("linked")
    world.git("branch", "-m", "main", "feature")
    before = world.git("worktree", "list", "--porcelain")
    proc = subprocess.run(
        [
            sys.executable,
            "-c",
            "from worktrees.cli import gwm; raise SystemExit(gwm())",
            "renamed",
            "--json",
        ],
        cwd=linked,
        env=network.env(),
        capture_output=True,
        text=True,
    )
    assert proc.returncode != 0
    assert "No main or master branch." in proc.stderr
    assert world.git("worktree", "list", "--porcelain") == before
    assert world.git("rev-parse", "refs/heads/linked")
    assert linked.exists()
    assert not (linked.parent.parent / "renamed" / world.repo.name).exists()


def test_inferred_head_notice_paints_both_branch_names_on_stderr(world, network):
    import os
    import pty

    world.git("branch", "master")
    parent, child = pty.openpty()
    proc = subprocess.Popen(
        [
            sys.executable,
            "-c",
            "from worktrees.cli import gws; raise SystemExit(gws())",
            "-q",
        ],
        cwd=world.repo,
        env=network.env(TERM="xterm"),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=child,
    )
    os.close(child)
    chunks = []
    try:
        while True:
            try:
                chunk = os.read(parent, 65536)
            except OSError:
                break
            if not chunk:
                break
            chunks.append(chunk)
    finally:
        proc.wait(timeout=10)
        os.close(parent)
    assert proc.returncode == 0
    output = b"".join(chunks)
    assert b"\x1b[1m\x1b[35mmain\x1b[0m" in output
    assert b"\x1b[1m\x1b[35mmaster\x1b[0m" in output


@pytest.mark.parametrize("operation", ["fetch", "set-head"])
def test_network_wrapper_intercepts_git_repository_prefix(world, network, operation):
    world.git("remote", "add", "origin", str(world.root / "unreachable.git"))
    world.git("update-ref", "refs/remotes/origin/main", "main")
    args = (
        ["fetch", "--prune", "origin"]
        if operation == "fetch"
        else ["remote", "set-head", "origin", "--auto"]
    )
    proc = subprocess.run(
        ["git", "-C", str(world.repo), *args],
        cwd=world.root,
        env=network.env(),
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stderr
    assert network.network_calls() == [["git", "-C", str(world.repo), *args]]
    if operation == "set-head":
        assert (
            world.git("symbolic-ref", "refs/remotes/origin/HEAD")
            == "refs/remotes/origin/main"
        )
