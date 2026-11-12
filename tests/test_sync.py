"""Sync runs when a command is due one, is shared by worktrees, and stops offline."""

from __future__ import annotations

import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path

import pytest
from conftest import argv, online
from test_branches import cli, local_branch


def origin(world, host="github.com", name="origin") -> None:
    world.git("remote", "add", name, f"git@{host}:owner/repo.git")
    world.git("update-ref", f"refs/remotes/{name}/main", "main")


def listing(world, network, entry="gw", at=None) -> dict:
    """What the cache says as it stands, read offline so nothing syncs first."""
    proc = subprocess.run(
        argv(entry, *(["list"] if entry == "gw" else []), "--json"),
        cwd=at or world.repo,
        env=network.env(WORKTREES_OFFLINE="1"),
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


def cache_path(world) -> Path:
    return world.repo / ".git" / "git-worktrees-sync.json"


def first_parent_proof(
    world, network, provider, names=("feature",), *, fork=False
) -> dict:
    from test_branch_forge import evidence

    for name in names:
        local_branch(world, name, path=name)
        world.git("merge", "--quiet", "--ff-only", name)
    origin(world, provider + ".com")
    responses: dict = {}
    api = {
        "projects/7": {
            "id": 7,
            "path_with_namespace": "owner/repo",
            "web_url": "https://gitlab.com/owner/repo",
        }
    }
    for name in names:
        row = evidence(world, provider)
        if provider == "github":
            row.update(headRefName=name, headRefOid=world.git("rev-parse", name))
            if fork:
                row.update(
                    isCrossRepository=True,
                    headRepository={"nameWithOwner": "fork/repo"},
                )
                api["repos/fork/repo"] = {
                    "full_name": "fork/repo",
                    "html_url": "https://github.com/fork/repo",
                }
        else:
            row.update(source_branch=name, sha=world.git("rev-parse", name))
            if fork:
                row["source_project_id"] = 8
                api["projects/8"] = {
                    "id": 8,
                    "path_with_namespace": "fork/repo",
                    "web_url": "https://gitlab.com/fork/repo",
                }
        responses[name] = [row]
    responses["$api"] = api
    network.answer(responses)
    proc = cli(world, "sync", env=network.env())
    assert proc.returncode == 0, proc.stderr
    rows = {
        r["branch"]: r for r in listing(world, network, "gw branch status")["verdicts"]
    }
    for name in names:
        assert rows[name]["verdict"] == "remove", rows[name]
        assert rows[name]["proof"] == "forge", rows[name]
    return responses


def test_json_envelope_preserves_worktrees(world, network):
    data = listing(world, network)
    assert isinstance(data, dict)
    assert data["lastSync"] == {"refs": None, "forge": None}
    assert data["head"] == {"ref": "refs/heads/main", "source": "inferred"}
    assert data["worktrees"][0]["path"] == str(world.repo)


@pytest.mark.parametrize("entry", ["gw", "gw branch status"])
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
    if case == "neither" and entry == "gw branch status":
        assert all(row["verdict"] == "unknown" for row in data["verdicts"])


@pytest.mark.parametrize("entry", ["gw", "gw branch status"])
def test_clone_head_becomes_synced_remote_head(world, network, entry):
    origin(world)
    world.git("symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/main")
    assert listing(world, network, entry)["head"] == {
        "ref": "refs/remotes/origin/main",
        "source": "clone",
    }
    proc = cli(world, "sync", env=network.env())
    assert proc.returncode == 0, proc.stderr
    data = listing(world, network, entry)
    assert data["head"] == {"ref": "refs/remotes/origin/main", "source": "remote"}
    for timestamp in data["lastSync"].values():
        assert timestamp.endswith("Z")
        assert datetime.fromisoformat(timestamp.replace("Z", "+00:00")).tzinfo == UTC


@pytest.mark.parametrize(
    "names, configured, expected",
    [
        ([], None, []),
        (["upstream"], None, ["upstream"]),
        (["origin", "upstream"], "upstream", ["upstream", "origin"]),
        (["origin", "upstream"], None, ["origin", "upstream"]),
    ],
)
def test_sync_fetches_every_remote_default_first(
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
        ["git", "fetch", "--prune", "--tags", "--no-prune-tags", "origin"],
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
    if failure == "fetch":
        assert now == cached
    elif failure == "head":
        # The fetch went through, so its time is recorded; only HEAD is stale.
        assert "git remote set-head origin --auto" in proc.stderr
        assert now["lastSync"]["refs"] != cached["lastSync"]["refs"]
        assert now["lastSync"]["forge"] != cached["lastSync"]["forge"]
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
    "entry, args",
    [("gw add", ["new"]), ("gw branch create", ["new"]), ("gw fast-forward", [])],
)
def test_branch_creation_refuses_unresolved_inferred_head(world, network, entry, args):
    world.git("branch", "-m", "main", "feature")
    proc = cli(world, *args, entry=entry, env=network.env())
    assert proc.returncode != 0
    assert "No main or master branch." in proc.stderr
    assert not network.network_calls()


ORDINARY = [
    ("gw", ["prune", "--json"]),
    ("gw prune", ["--yes", "--json"]),
    ("gw branch status", ["--json"]),
    ("gw branch create", ["new", "--json"]),
    ("gw branch prune", ["--dry-run", "--json"]),
    ("gw add", ["new", "--json"]),
    ("gw remove", ["no-such-worktree", "--yes"]),
    ("gw fast-forward", ["--json"]),
]


@pytest.mark.parametrize(
    "entry, args", [*ORDINARY, ("gw", ["--json"]), ("gw list", ["--json"])]
)
def test_offline_commands_never_use_network(world, network, entry, args):
    origin(world)
    world.git("switch", "-qc", "feature")
    world.commit("work")
    proc = cli(world, *args, entry=entry, env=network.env(WORKTREES_OFFLINE="1"))
    assert not network.network_calls(), (proc.stderr, network.network_calls())
    if proc.returncode == 0 and "--json" in args:
        data = json.loads(proc.stdout)
        assert "lastSync" in data and "head" in data


@pytest.mark.parametrize("entry, args", ORDINARY)
def test_ordinary_commands_sync_first_when_due(world, network, entry, args):
    origin(world)
    world.git("switch", "-qc", "feature")
    world.commit("work")
    proc = cli(world, *args, entry=entry, env=network.env())
    fetches = [call for call in network.network_calls() if call[1:2] == ["fetch"]]
    assert fetches == [
        ["git", "fetch", "--prune", "--tags", "--no-prune-tags", "origin"]
    ], proc.stderr
    if proc.returncode == 0 and "--json" in args:
        # Nothing the sync said reached stdout.
        assert json.loads(proc.stdout)["lastSync"]["refs"]


@pytest.mark.parametrize(
    "entry, args",
    [("gw list", ["--json"]), ("gw list", ["--table"]), ("gw", ["l", "--json"])],
)
def test_list_never_syncs_first(world, network, entry, args):
    """It reads the worktree records and nothing a fetch brings in, so a due
    sync waits for a command that assesses."""
    origin(world)
    world.git("switch", "-qc", "feature")
    world.commit("work")
    proc = cli(world, *args, entry=entry, env=network.env())
    assert proc.returncode == 0, proc.stderr
    assert not network.network_calls(), (proc.stderr, network.network_calls())
    assert not proc.stderr
    if "--json" in args:
        data = json.loads(proc.stdout)
        assert data["lastSync"] == {"refs": None, "forge": None}
        assert "head" in data


def _cache(world, refs: str | None) -> None:
    """A cache for origin whose forge time is years old, to show it is unread."""
    cache_path(world).write_text(
        json.dumps(
            {
                "remote": {"name": "origin", "url": "git@github.com:owner/repo.git"},
                "lastSync": {"refs": refs, "forge": "2020-01-01T00:00:00Z"},
                "branches": {},
            }
        )
    )


@pytest.mark.parametrize(
    "age, ttl, due",
    [
        (59, None, False),
        (60, None, True),
        (59, "", False),
        (3599, "3600", False),
        (3600, "3600", True),
        (0, "0", True),
    ],
)
def test_sync_is_due_once_the_refs_are_ttl_seconds_old(
    world, monkeypatch, age, ttl, due
):
    from worktrees import sync

    origin(world)
    _cache(world, "2026-01-01T00:00:00Z")
    monkeypatch.delenv("WORKTREES_OFFLINE")
    if ttl is not None:
        monkeypatch.setenv("WORKTREES_SYNC_TTL", ttl)
    stamp = datetime(2026, 1, 1, tzinfo=UTC).timestamp()
    monkeypatch.setattr(sync.time, "time", lambda: stamp + age)
    assert sync.due() is due


@pytest.mark.parametrize("case", ["never", "offline", "no-remote", "no-default"])
def test_sync_is_due_never_offline_or_without_a_default_remote(
    world, monkeypatch, case
):
    from worktrees import sync

    monkeypatch.delenv("WORKTREES_OFFLINE")
    if case != "no-remote":
        origin(world)
    if case == "offline":
        monkeypatch.setenv("WORKTREES_OFFLINE", "yes")
    if case == "no-default":
        origin(world, name="upstream")
    assert sync.due() is (case == "never")


@pytest.mark.parametrize("value", ["soon", "-1", "1.5", " 60", "\u0663"])
def test_a_ttl_that_is_not_whole_seconds_is_a_usage_error(world, network, value):
    origin(world)
    proc = cli(
        world, "--json", entry="gw prune", env=network.env(WORKTREES_SYNC_TTL=value)
    )
    assert proc.returncode == 2, proc.stderr
    assert f"WORKTREES_SYNC_TTL takes whole seconds, not {value!r}" in proc.stderr
    assert not proc.stdout
    assert not network.network_calls()


@pytest.mark.parametrize("args", [[], ["--json"], ["-q"]])
def test_sync_refuses_offline(world, network, args):
    origin(world)
    proc = cli(world, "sync", *args, env=network.env(WORKTREES_OFFLINE="1"))
    assert proc.returncode == 3
    assert proc.stdout == ""
    assert "gw sync uses the network, and WORKTREES_OFFLINE is set" in proc.stderr
    assert not network.network_calls()
    assert not cache_path(world).exists()


def test_an_empty_offline_value_is_unset(world, network):
    origin(world)
    proc = cli(world, "sync", env=network.env(WORKTREES_OFFLINE=""))
    assert proc.returncode == 0, proc.stderr
    assert network.network_calls()


def test_offline_notices_name_no_command(world, network):
    origin(world)
    world.git("symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/missing")
    proc = cli(world, entry="gw prune", env=network.env(WORKTREES_OFFLINE="1"))
    assert proc.returncode == 0, proc.stderr
    assert (
        "Never synced: verdicts use refs from the clone or last fetch "
        "and no forge evidence\n"
    ) in proc.stderr
    assert "Head: missing, which does not resolve (clone)\n" in proc.stderr
    assert "gw sync" not in proc.stderr
    offline = network.env(WORKTREES_OFFLINE="1")
    created = cli(world, "new", entry="gw branch create", env=offline)
    assert created.returncode == 1
    assert "The head ref does not resolve\n" in created.stderr


def test_a_failed_sync_first_warns_and_the_command_carries_on(world, network):
    origin(world)
    _cache(world, "2020-01-01T00:00:00Z")
    unreachable = network.env(WT_FETCH_CODE="128")
    proc = cli(world, entry="gw prune", env=unreachable)
    assert proc.returncode == 0, proc.stderr
    assert "origin could not be fetched; previous sync evidence retained" in (
        proc.stderr
    )
    assert "ago. Run: gw sync" in proc.stderr
    data = cli(world, "--json", entry="gw prune", env=unreachable)
    assert data.returncode == 0, data.stderr
    assert json.loads(data.stdout)["lastSync"]["refs"] == "2020-01-01T00:00:00Z"
    assert "could not be fetched" not in data.stderr


def test_a_sync_first_leaves_the_destination_alone(world, network):
    origin(world)
    proc = cli(world, "new", entry="gw add", env=network.env())
    assert proc.returncode == 0, proc.stderr
    assert any(call[1:2] == ["fetch"] for call in network.network_calls())
    assert proc.stdout.count("\n") == 1
    assert Path(proc.stdout.removesuffix("\n")).is_dir()


def test_a_sync_first_is_traced_under_verbose(world, network):
    origin(world)
    proc = cli(world, "-v", "--json", entry="gw prune", env=network.env())
    assert proc.returncode == 0, proc.stderr
    assert "git fetch --prune --tags --no-prune-tags origin" in proc.stderr
    json.loads(proc.stdout)


@pytest.mark.parametrize(
    "entry",
    [
        "gw",
        "gw prune",
        "gw branch status",
        "gw branch prune",
        "gw add",
        "gw branch create",
        "gw fast-forward",
        "gw remove",
    ],
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
@pytest.mark.parametrize("components", ["equal", "older-forge", "refs-only"])
def test_stale_notice_boundary_and_units(
    world, network, monkeypatch, capsys, seconds, text, components
):
    from worktrees import cli as C
    from worktrees import sync

    origin(world, "code.example.net" if components == "refs-only" else "github.com")
    assert cli(world, "sync", env=network.env()).returncode == 0
    cached = json.loads(cache_path(world).read_text())
    cached["lastSync"] = {
        "refs": "2026-01-01T00:05:00Z"
        if components == "older-forge"
        else "2026-01-01T00:00:00Z",
        "forge": None if components == "refs-only" else "2026-01-01T00:00:00Z",
    }
    cache_path(world).write_text(json.dumps(cached))
    stamp = datetime(2026, 1, 1, tzinfo=UTC).timestamp()
    monkeypatch.setattr(sync.time, "time", lambda: stamp + seconds)
    assert C.gw(["branch", "status"]) == 0
    error = capsys.readouterr().err
    if text:
        # Offline, so the line points at no command that would refuse.
        assert f"Last sync: {text} ago\n" in error and "gw sync" not in error
        assert error.count("Last sync:") == 1
    else:
        assert "Last sync:" not in error
    assert C.gw(["prune", "-q"]) == 0
    assert "Last sync:" not in capsys.readouterr().err
    assert C.gw(["branch", "status", "--json"]) == 0
    assert "Last sync:" not in capsys.readouterr().err


@pytest.mark.parametrize("quiet", [False, True])
def test_never_synced_survives_quiet_and_json_and_inferred_yields(
    world, network, quiet
):
    """A caller acting on these verdicts needs to hear that no sync is behind them.

    Online, that is a repository whose first sync failed.
    """
    origin(world)
    unreachable = network.env(WT_FETCH_CODE="128")
    proc = cli(world, *(["-q"] if quiet else []), entry="gw prune", env=unreachable)
    assert proc.returncode == 0, proc.stderr
    never = (
        "Never synced: verdicts use refs from the clone or last fetch "
        "and no forge evidence. Run: gw sync"
    )
    assert proc.stderr.count(never) == 1
    assert ("Head: main (inferred)" in proc.stderr) is not quiet
    data = cli(world, "branch", "status", "--json", env=unreachable)
    json.loads(data.stdout)
    assert data.stderr.count(never) == 1 and "Head:" not in data.stderr
    proc = cli(world, "sync", *(["-q"] if quiet else []), env=network.env())
    assert "Never synced:" not in proc.stderr
    assert ("Head: main (remote)" in proc.stderr) is not quiet, proc.stderr


def test_repository_without_remote_reports_only_inferred_head(world, network):
    proc = cli(world, "branch", "status", env=network.env())
    assert "Head: main (inferred)" in proc.stderr
    assert "Never synced:" not in proc.stderr and "Last sync:" not in proc.stderr
    proc = cli(world, "sync", env=network.env())
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout == "No remote to sync.\n"
    assert not network.network_calls()


@pytest.mark.parametrize("provider", ["github", "gitlab"])
@pytest.mark.parametrize("identity", ["verified", "missing", "mismatch"])
@pytest.mark.parametrize("enterprise", [False, True])
def test_fork_proof_requires_authoritative_source_identity(
    world, network, provider, identity, enterprise
):
    from test_branch_forge import evidence

    local_branch(world)
    world.commit("feature", "later edits\n")
    host = provider + (".example.com" if enterprise else ".com")
    origin(world, host)
    row = evidence(world, provider)
    url_field = "url" if provider == "github" else "web_url"
    row[url_field] = row[url_field].replace(provider + ".com", host)
    api: dict[str, dict] = {}
    if provider == "github":
        row["isCrossRepository"] = True
        row["headRepository"] = {"nameWithOwner": "fork/repo"}
        endpoint = "repos/fork/repo"
        answer: dict = {
            "full_name": "fork/repo" if identity == "verified" else "other/repo",
            "html_url": f"https://{host}/fork/repo",
        }
    else:
        row["source_project_id"] = 8
        endpoint = "projects/8"
        source_host = host if identity == "verified" else "other.example"
        answer = {
            "id": 8,
            "path_with_namespace": "fork/repo",
            "web_url": f"https://{source_host}/fork/repo",
        }
        api["projects/7"] = {
            "id": 7,
            "path_with_namespace": "owner/repo",
            "web_url": f"https://{host}/owner/repo",
        }
    if identity != "missing":
        api[endpoint] = answer
    network.answer({"feature": [row], "$api": api})
    proc = cli(world, "sync", env=network.env())
    assert proc.returncode == (1 if identity == "missing" else 0), proc.stderr
    data = listing(world, network, "gw branch status")
    feature = next(row for row in data["verdicts"] if row["branch"] == "feature")
    assert (feature["verdict"] == "remove") is (identity == "verified"), feature
    if identity == "verified":
        assert feature["request"]["source_repository"] == f"{host}/fork/repo"
        assert feature["request"]["repository"] == f"{host}/owner/repo"
    else:
        tip = world.git("rev-parse", "feature")
        denied = cli(
            world, "feature", "--yes", entry="gw branch prune", env=network.env()
        )
        assert denied.returncode != 0, denied.stderr
        assert world.git("rev-parse", "feature") == tip
    tool = "gh" if provider == "github" else "glab"
    calls = network.network_calls()
    assert [tool, "auth", "status", "--hostname", host] in calls
    queries = [
        call
        for call in calls
        if call[:3] == [tool, "pr" if provider == "github" else "mr", "list"]
    ]
    assert queries
    assert all(
        call[call.index("--repo") + 1]
        == (
            f"{host}/owner/repo"
            if provider == "github"
            else f"https://{host}/owner/repo"
        )
        for call in queries
    )
    assert [tool, "api", endpoint, "--hostname", host] in calls
    if provider == "gitlab":
        assert [tool, "api", "projects/7", "--hostname", host] in calls
    assert not any(
        "branches" in arg for call in network.network_calls() for arg in call
    )


@pytest.mark.parametrize(
    "field,value",
    [
        ("id", 9),
        ("id", True),
        ("web_url", "https://gitlab.com/other/repo"),
        ("path_with_namespace", "other/repo"),
        ("path_with_namespace", None),
        ("web_url", None),
    ],
)
def test_gitlab_source_lookup_must_agree_with_identity(world, network, field, value):
    from test_branch_forge import evidence

    tip = local_branch(world)
    world.commit("feature", "later edits\n")
    origin(world, "gitlab.com")
    source = {
        "id": 8,
        "path_with_namespace": "fork/repo",
        "web_url": "https://gitlab.com/fork/repo",
        field: value,
    }
    network.answer(
        {
            "feature": [evidence(world, "gitlab", source_project_id=8)],
            "$api": {
                "projects/7": {
                    "id": 7,
                    "path_with_namespace": "owner/repo",
                    "web_url": "https://gitlab.com/owner/repo",
                },
                "projects/8": source,
            },
        }
    )
    proc = cli(world, "sync", env=network.env())
    assert proc.returncode == 0, proc.stderr
    row = next(
        r
        for r in listing(world, network, "gw branch status")["verdicts"]
        if r["branch"] == "feature"
    )
    assert row["verdict"] == "keep", row
    denied = cli(world, "feature", "--yes", entry="gw branch prune", env=network.env())
    assert denied.returncode == 3, denied.stderr
    assert world.git("rev-parse", "feature") == tip


@pytest.mark.parametrize(
    "entry, args", [("gw add", ["new"]), ("gw branch create", ["new"])]
)
def test_dangling_clone_head_refuses_branch_creation_with_sync_hint(
    world, network, entry, args
):
    origin(world)
    world.git("symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/missing")
    assert listing(world, network)["head"] == {"ref": None, "source": "clone"}
    proc = cli(world, *args, entry=entry, env=network.env(WT_FETCH_CODE="128"))
    assert proc.returncode != 0
    assert "The head ref does not resolve. Run: gw sync" in proc.stderr
    assert [call[1] for call in network.network_calls()] == ["fetch"]


@pytest.mark.parametrize(
    "entry, args",
    [
        ("gw prune", ["--yes"]),
        ("gw remove", ["linked", "--yes", "--keep-branch"]),
        ("gw branch prune", ["feature", "--yes"]),
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
        for row in listing(world, network, "gw branch status")["verdicts"]
        if row["branch"] == "feature"
    )
    assert control["verdict"] == "remove", control
    cached = json.loads(cache_path(world).read_text())
    cached["branches"]["feature"]["request"]["source_repository"] = identity
    cache_path(world).write_text(json.dumps(cached))
    row = next(
        row
        for row in listing(world, network, "gw branch status")["verdicts"]
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
        for row in listing(world, network, "gw branch status")["verdicts"]
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
    data = listing(world, network, "gw branch status")
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


def test_inferred_head_notice_paints_both_branch_names_on_stderr(world, network):
    import os
    import pty

    world.git("branch", "master")
    parent, child = pty.openpty()
    proc = subprocess.Popen(
        argv("gw prune"),
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


@pytest.mark.parametrize("provider", ["github", "gitlab"])
def test_new_branch_does_not_inherit_cached_request(world, network, provider):
    first_parent_proof(world, network, provider)
    world.git("branch", "new-name", "feature")
    before = network.network_calls()
    rows = {
        r["branch"]: r for r in listing(world, network, "gw branch status")["verdicts"]
    }
    assert rows["feature"]["verdict"] == "remove"
    assert rows["feature"]["proof"] == "forge"
    assert rows["new-name"]["verdict"] == "empty"
    assert rows["new-name"]["request"] is None
    assert "new-name" not in json.loads(cache_path(world).read_text())["branches"]
    assert network.network_calls() == before


@pytest.mark.parametrize("provider", ["github", "gitlab"])
@pytest.mark.parametrize("failure", ["fetch", "missing", "auth"])
def test_sync_failure_retains_positive_cached_proof(world, network, provider, failure):
    first_parent_proof(world, network, provider)
    cached = json.loads(cache_path(world).read_text())
    cached["lastSync"] = {
        "refs": "2020-01-01T00:00:00Z",
        "forge": "2020-01-02T00:00:00Z",
    }
    cache_path(world).write_text(json.dumps(cached))
    old_bytes = cache_path(world).read_bytes()
    before = len(network.network_calls())
    env = network.env()
    if failure == "fetch":
        env["WT_FETCH_CODE"] = "128"
    elif failure == "auth":
        env["WT_AUTH_CODE"] = "1"
    else:
        (network.bin / ("gh" if provider == "github" else "glab")).unlink()
        env["PATH"] = str(network.bin)
    proc = cli(world, "sync", env=env)
    assert proc.returncode == 1, proc.stderr
    now = json.loads(cache_path(world).read_text())
    if failure == "fetch":
        assert cache_path(world).read_bytes() == old_bytes
        assert network.network_calls()[before:] == [
            ["git", "fetch", "--prune", "--tags", "--no-prune-tags", "origin"]
        ]
    else:
        assert now["lastSync"]["refs"] != cached["lastSync"]["refs"]
        assert now["lastSync"]["forge"] == cached["lastSync"]["forge"]
        assert now["branches"]["feature"] == cached["branches"]["feature"]
    before_assessment = network.network_calls()
    row = next(
        r
        for r in listing(world, network, "gw branch status")["verdicts"]
        if r["branch"] == "feature"
    )
    assert row["verdict"] == "remove", row
    assert row["proof"] == "forge", row
    assert network.network_calls() == before_assessment


@pytest.mark.parametrize("provider", ["github", "gitlab"])
@pytest.mark.parametrize("total", [False, True])
def test_failed_queries_invalidate_positive_proof(world, network, provider, total):
    responses = first_parent_proof(world, network, provider, ("feature", "second"))
    cached = json.loads(cache_path(world).read_text())
    cached["lastSync"] = {
        "refs": "2020-01-01T00:00:00Z",
        "forge": "2020-01-02T00:00:00Z",
    }
    cache_path(world).write_text(json.dumps(cached))
    responses["feature"] = 1
    if total:
        responses.update(second=1, main=1)
    network.answer(responses)
    proc = cli(world, "sync", env=network.env())
    assert proc.returncode == 1, proc.stderr
    now = json.loads(cache_path(world).read_text())
    assert now["lastSync"]["refs"] != cached["lastSync"]["refs"]
    assert now["lastSync"]["forge"] == cached["lastSync"]["forge"]
    assert "feature" not in now["branches"]
    if total:
        assert now["branches"] == {}
    else:
        assert now["branches"]["second"]["request"] is not None
    before = network.network_calls()
    rows = {
        r["branch"]: r for r in listing(world, network, "gw branch status")["verdicts"]
    }
    assert rows["feature"]["verdict"] == "empty", rows["feature"]
    assert rows["feature"]["request"] is None
    assert rows["second"]["verdict"] == ("empty" if total else "remove")
    assert network.network_calls() == before


@pytest.mark.parametrize("provider", ["github", "gitlab"])
def test_failed_fork_lookup_invalidates_previous_proof(world, network, provider):
    responses = first_parent_proof(world, network, provider, fork=True)
    cached = json.loads(cache_path(world).read_text())
    cached["lastSync"] = {
        "refs": "2020-01-01T00:00:00Z",
        "forge": "2020-01-02T00:00:00Z",
    }
    cache_path(world).write_text(json.dumps(cached))
    endpoint = "repos/fork/repo" if provider == "github" else "projects/8"
    del responses["$api"][endpoint]
    network.answer(responses)
    proc = cli(world, "sync", env=network.env())
    assert proc.returncode == 1, proc.stderr
    now = json.loads(cache_path(world).read_text())
    assert now["lastSync"]["refs"] != cached["lastSync"]["refs"]
    assert now["lastSync"]["forge"] == cached["lastSync"]["forge"]
    assert "feature" not in now["branches"]
    before = network.network_calls()
    row = next(
        r
        for r in listing(world, network, "gw branch status")["verdicts"]
        if r["branch"] == "feature"
    )
    assert row["verdict"] == "empty", row
    assert row["request"] is None
    assert network.network_calls() == before


@pytest.mark.parametrize("provider", ["github", "gitlab"])
@pytest.mark.parametrize("entry", ["gw prune", "gw remove", "gw branch prune"])
def test_successful_cleanup_rechecks_offline(world, network, provider, entry):
    seed = world.merged_branch("seed")
    world.git("remote", "add", "unselected", "git@github.com:unselected/repo.git")
    world.git("config", "checkout.defaultRemote", "origin")
    first_parent_proof(world, network, provider, fork=True)
    world.git("branch", "local", seed)
    paths = {}
    if entry != "gw branch prune":
        for name in ("feature", "local"):
            paths[name] = world.root / name
            world.git("worktree", "add", "--quiet", str(paths[name]), name)
    before = network.network_calls()
    call_count = len(network.calls())
    preview = cli(
        world,
        "--json",
        entry="gw prune" if entry != "gw branch prune" else "gw branch status",
        env=network.env(),
    )
    assert preview.returncode == 0, preview.stderr
    rows = {r["branch"]: r for r in json.loads(preview.stdout)["verdicts"]}
    assert rows["feature"]["proof"] == "forge"
    assert rows["local"]["verdict"] == "remove"
    assert rows["local"]["proof"] == "ancestry"
    assert "local" not in json.loads(cache_path(world).read_text())["branches"]
    selections = (
        [("feature", "local")]
        if entry == "gw branch prune"
        else [("feature",), ("local",)]
        if entry == "gw remove"
        else [()]
    )
    for selection in selections:
        proc = cli(world, *selection, "--yes", "--json", entry=entry, env=network.env())
        assert proc.returncode == 0, proc.stderr
        data = json.loads(proc.stdout)
        if entry == "gw remove":
            assert data["outcome"]["worktree_removed"]
            assert data["outcome"]["branch_removed"]
        else:
            assert data["removed"]
    remaining = world.git("branch", "--format=%(refname:short)").splitlines()
    assert "feature" not in remaining and "local" not in remaining
    assert all(not path.exists() for path in paths.values())
    actual_calls = network.calls()[call_count:]
    for name in ("feature", "local"):
        assert any(
            call[-5:]
            == [
                "update-ref",
                "--no-deref",
                "-d",
                f"refs/heads/{name}",
                rows[name]["sha"],
            ]
            for call in actual_calls
        )
    if paths:
        for path in paths.values():
            assert any(
                call[-4:] == ["worktree", "remove", "--", str(path)]
                for call in actual_calls
            )
    assert network.network_calls() == before
    # Sync fetches every remote, and asks the forge about the default one only.
    asked = [call for call in before if call[0] in ("gh", "glab")]
    assert asked
    assert not any("unselected" in argument for call in asked for argument in call)


@pytest.mark.parametrize(
    "entry,args",
    [
        ("gw", ["--complete"]),
        ("gw", ["branch", "--complete"]),
        ("gw", ["version"]),
        ("gw", ["list", "--complete"]),
        ("gw remove", ["--complete"]),
        ("gw branch status", ["--complete"]),
        ("gw branch prune", ["--complete"]),
    ],
)
def test_metadata_commands_remain_offline(world, network, entry, args):
    origin(world)
    world.merged_worktree("candidate")
    world.merged_branch("loose")
    proc = cli(world, *args, entry=entry, env=network.env())
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout
    assert not network.network_calls()


def test_every_command_help_remains_offline(world, network):
    from worktrees.cli import command_rows

    origin(world)
    for name, _ in command_rows():
        proc = cli(world, *name.split(), "--help", env=network.env())
        assert proc.returncode == 0, (name, proc.stderr)
        assert "usage:" in proc.stdout + proc.stderr
    assert not network.network_calls()


@pytest.mark.parametrize("head_source", ["inferred", "clone"])
def test_unresolved_head_marks_real_preview_rows_unknown(world, network, head_source):
    attached = world.worktree("linked")
    detached = world.worktree("detached")
    world.git("checkout", "--detach", at=detached)
    world.git("branch", "loose")
    if head_source == "clone":
        origin(world)
        world.git(
            "symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/missing"
        )
    world.git("branch", "-m", "main", "feature")
    refs = world.git("for-each-ref", "--format=%(refname) %(objectname)")
    records = world.git("worktree", "list", "--porcelain")
    preview = cli(
        world,
        "--json",
        "--delete",
        "all",
        entry="gw prune",
        env=network.env(WORKTREES_OFFLINE="1"),
    )
    assert preview.returncode == 0, preview.stderr
    data = json.loads(preview.stdout)
    assert data["head"] == {"ref": None, "source": head_source}
    assert {r["path"] for r in data["verdicts"]} == {
        str(world.repo),
        str(attached),
        str(detached),
    }
    assert all(
        r["verdict"] == "unknown" and not r["eligible"] for r in data["verdicts"]
    )
    loose = listing(world, network, "gw branch status")
    assert {r["branch"] for r in loose["verdicts"]} == {"loose", "detached"}
    assert all(
        r["verdict"] == "unknown" and not r["eligible"] for r in loose["verdicts"]
    )
    assert world.git("for-each-ref", "--format=%(refname) %(objectname)") == refs
    assert world.git("worktree", "list", "--porcelain") == records
    assert all(
        (path / "README").read_text() == "x"
        for path in (world.repo, attached, detached)
    )
    assert not cache_path(world).exists()
    assert not network.network_calls()
    assert not any(
        argument in ("update-ref", "remove", "prune")
        for call in network.calls()
        for argument in call[1:]
    )


@pytest.mark.parametrize("state", ["missing", "deleted", "plain"])
def test_the_head_line_names_an_unresolved_remote_head(world, network, state):
    """A head line never reads `Head:  (remote)` with nothing in the gap."""
    origin(world)
    if state == "missing":
        world.git(
            "symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/missing"
        )
        wanted = "Head: missing, which does not resolve (clone). Run: gw sync"
        # A sync would repair it, so this is one whose fetch failed.
        env = network.env(WT_FETCH_CODE="128")
    else:
        assert cli(world, "sync", env=network.env()).returncode == 0
        world.git("update-ref", "--no-deref", "-d", "refs/remotes/origin/HEAD")
        if state == "plain":
            # A plain ref at two branches names neither.
            world.git("update-ref", "refs/remotes/origin/release", "main")
            world.git("update-ref", "--no-deref", "refs/remotes/origin/HEAD", "main")
        wanted = "Head: unresolved (remote). Run: gw sync"
        env = network.env()
    proc = cli(world, entry="gw prune", env=env)
    assert proc.returncode == 0, proc.stderr
    assert wanted in proc.stderr


def _real_origin(world) -> Path:
    """A local bare remote, and a second clone that pushes to it.

    Fetch is real here, so what Git prints is what sync reads.
    """
    remote = world.root / "remote.git"
    world.git("clone", "--quiet", "--bare", str(world.repo), str(remote))
    world.git("remote", "add", "origin", remote.as_uri())
    world.git("fetch", "--quiet", "origin")
    pusher = world.root / "pusher"
    world.git("clone", "--quiet", remote.as_uri(), str(pusher), at=world.root)
    return pusher


def _raw(world, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args], cwd=world.repo, capture_output=True, text=True, check=False
    )


@pytest.mark.parametrize("output", ["full", "compact"])
def test_a_moved_tag_is_a_warning_and_the_sync_is_recorded(world, output):
    world.git("config", "fetch.output", output)
    pusher = _real_origin(world)
    world.git("tag", "v1")
    world.git("push", "--quiet", "origin", "v1")
    local_v1 = world.git("rev-parse", "v1")
    world.commit("moved", at=pusher)
    world.git("tag", "--force", "v1", at=pusher)
    world.git("push", "--quiet", "--force", "origin", "main", "v1", at=pusher)
    # Git alone refuses the tag and exits 1, with the branch already moved.
    raw = _raw(world, "fetch", "--tags", "origin")
    assert raw.returncode == 1, raw.stderr
    assert "would clobber existing tag" in raw.stderr
    assert world.git("rev-parse", "origin/main") == world.git(
        "rev-parse", "main", at=pusher
    )
    proc = cli(world, "sync", env=online(world))
    assert proc.returncode == 0, proc.stderr
    assert "origin moved v1; the local tags are unchanged" in proc.stderr
    assert "git fetch --tags --force origin" in proc.stderr
    assert world.git("rev-parse", "v1") == local_v1
    assert json.loads(cache_path(world).read_text())["lastSync"]["refs"]


def test_any_other_refused_ref_fails_the_sync(world):
    pusher = _real_origin(world)
    # Without the leading +, Git refuses a remote branch that was rewritten.
    world.git(
        "config",
        "--replace-all",
        "remote.origin.fetch",
        "refs/heads/*:refs/remotes/origin/*",
    )
    assert cli(world, "sync", env=online(world)).returncode == 0
    before = cache_path(world).read_bytes()
    world.git("commit", "--quiet", "--amend", "-m", "rewritten", at=pusher)
    world.git("push", "--quiet", "--force", "origin", "main", at=pusher)
    proc = cli(world, "sync", env=online(world))
    assert proc.returncode == 1, proc.stderr
    assert "origin could not be fetched; previous sync evidence retained" in (
        proc.stderr
    )
    assert cache_path(world).read_bytes() == before


def test_fetch_prune_tags_config_never_deletes_a_local_tag(world):
    _real_origin(world)
    world.git("config", "fetch.pruneTags", "true")
    world.git("tag", "mine")
    # Git alone deletes every local tag the remote lacks.
    raw = _raw(world, "fetch", "--prune", "--tags", "origin")
    assert raw.returncode == 0, raw.stderr
    assert world.git("tag", "--list", "mine") == ""
    world.git("tag", "mine")
    proc = cli(world, "sync", env=online(world))
    assert proc.returncode == 0, proc.stderr
    assert world.git("tag", "--list", "mine") == "mine"


def test_a_mirror_fetch_deletes_local_branches_so_sync_refuses_it(world, network):
    # Git alone, in a mirror: the prune deletes a branch the remote lacks.
    remote = world.root / "remote.git"
    world.git("clone", "--quiet", "--bare", str(world.repo), str(remote))
    mirror = world.root / "mirror.git"
    world.git(
        "clone", "--quiet", "--mirror", remote.as_uri(), str(mirror), at=world.root
    )
    world.git("branch", "loose", "main", at=mirror)
    world.git("fetch", "--quiet", "--prune", "origin", at=mirror)
    assert world.git("branch", "--list", "loose", at=mirror) == ""

    origin(world)
    world.git("config", "--replace-all", "remote.origin.fetch", "+refs/*:refs/*")
    world.git("branch", "loose")
    proc = cli(world, "sync", env=network.env())
    assert proc.returncode == 1
    assert "remote.origin.fetch writes refs/*, outside refs/remotes/origin/" in (
        proc.stderr
    )
    assert (
        "git config --replace-all remote.origin.fetch "
        "'+refs/heads/*:refs/remotes/origin/*'" in proc.stderr
    )
    assert not network.network_calls()
    assert world.git("branch", "--list", "loose").strip() == "loose"


@pytest.mark.parametrize(
    "name, specs, wanted",
    [
        ("upstream", ["+refs/heads/*:refs/heads/*"], "writes refs/heads/*"),
        ("upstream", ["+refs/heads/*:refs/remotes/origin/*"], "outside"),
        ("origin", [], "updates nothing in refs/remotes/origin/"),
        ("origin", ["refs/heads/main"], "updates nothing in refs/remotes/origin/"),
    ],
)
def test_every_fetched_remote_writes_only_its_own_tracking_refs(
    world, network, name, specs, wanted
):
    origin(world)
    origin(world, name="upstream")
    world.git("config", "checkout.defaultRemote", "origin")
    world.git("config", "--unset-all", f"remote.{name}.fetch")
    for spec in specs:
        world.git("config", "--add", f"remote.{name}.fetch", spec)
    proc = cli(world, "sync", env=network.env())
    assert proc.returncode == 1
    assert f"remote.{name}.fetch" in proc.stderr and wanted in proc.stderr
    assert not network.network_calls()


def test_negative_refspecs_and_skipped_remotes_are_not_refused(world, network):
    origin(world)
    origin(world, name="archive")
    world.git("config", "checkout.defaultRemote", "origin")
    world.git("config", "--add", "remote.origin.fetch", "^refs/heads/wip/*")
    world.git("config", "remote.archive.skipFetchAll", "true")
    world.git("config", "--replace-all", "remote.archive.fetch", "+refs/*:refs/*")
    proc = cli(world, "sync", env=network.env())
    assert proc.returncode == 0, proc.stderr
    fetches = [call[-1] for call in network.network_calls() if call[1:2] == ["fetch"]]
    assert fetches == ["origin"]


def test_the_default_remote_is_fetched_even_when_it_skips_fetch_all(world, network):
    origin(world)
    world.git("config", "remote.origin.skipFetchAll", "true")
    assert cli(world, "sync", env=network.env()).returncode == 0
    fetches = [call[-1] for call in network.network_calls() if call[1:2] == ["fetch"]]
    assert fetches == ["origin"]


def test_a_failed_other_remote_still_records_the_default_remotes_sync(world, network):
    origin(world)
    origin(world, name="upstream")
    world.git("config", "checkout.defaultRemote", "origin")
    network.intercept(
        "    if command[:1] == ['fetch'] and command[-1] == 'upstream':\n"
        "        sys.exit(128)\n"
    )
    proc = cli(world, "sync", env=network.env())
    assert proc.returncode == 1
    assert "upstream could not be fetched" in proc.stderr
    assert "previous sync evidence retained" not in proc.stderr
    assert json.loads(cache_path(world).read_text())["lastSync"]["refs"]


def test_progress_cuts_every_row_to_fit_the_terminal() -> None:
    from worktrees import progress

    rows = progress.frame(["a" * 50, "short"], "*", 20).split("\n")
    assert rows == ["* " + "a" * 17, "* short"]
