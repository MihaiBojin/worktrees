"""`gw preflight` reads each part of a repository's setup and changes none.

The forge is the stand-in from conftest: `WT_AUTH_CODE` answers
`gh auth status` and `$api` answers `gh api`. Nothing reaches the network.
"""

from __future__ import annotations

import json
import tomllib
from pathlib import Path

import pytest
from test_branches import cli
from test_sync import origin

from worktrees import __version__, preflight

ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / "plugin" / "skills" / "preflight"
REPO = "repos/owner/repo"
SQUASHED = {
    "allow_squash_merge": True,
    "squash_merge_commit_title": "PR_TITLE",
    "squash_merge_commit_message": "PR_BODY",
}
NAMES = ["forge-cli", "squash-defaults", "claude-code", "codex", "shell-plugin"]


def _set_up(world) -> None:
    """Every file the project needs, as the skill writes them."""
    (world.repo / ".claude").mkdir(exist_ok=True)
    (world.repo / ".claude" / "settings.json").write_text(
        (SKILL / "project.json").read_text()
    )
    (world.repo / ".codex").mkdir(exist_ok=True)
    (world.repo / ".codex" / "config.toml").write_text(
        (SKILL / "config.toml").read_text()
    )


def _env(network, **extra: str) -> dict[str, str]:
    return network.env(WORKTREES_PLUGIN_VERSION=__version__, **extra)


def _run(world, env, code: int) -> dict[str, dict]:
    proc = cli(world, "preflight", "--json", env=env)
    assert proc.returncode == code, proc.stderr
    out = json.loads(proc.stdout)
    assert {"head", "lastSync"} <= set(out)
    assert [c["name"] for c in out["checks"]] == NAMES
    for check in out["checks"]:
        assert set(check) == {"name", "status", "detail", "fix"}
        assert (check["fix"] is None) == (check["status"] in ("ok", "skipped"))
    return {c["name"]: c for c in out["checks"]}


@pytest.fixture
def github(world, network):
    origin(world)
    _set_up(world)
    network.answer({"$api": {REPO: SQUASHED}})
    return network


def test_a_repository_set_up_passes_every_check(world, github) -> None:
    checks = _run(world, _env(github), 0)
    assert {c["status"] for c in checks.values()} == {"ok"}
    assert checks["forge-cli"]["detail"] == "gh is signed in to github.com"


def test_it_never_syncs_first_and_reads_the_repository_once(world, github) -> None:
    _run(world, _env(github), 0)
    assert github.network_calls() == [
        ["gh", "auth", "status", "--hostname", "github.com"],
        ["gh", "api", REPO],
    ]


def test_each_check_prints_one_line_and_its_fix(world, github) -> None:
    github.answer({"$api": {REPO: {**SQUASHED, "squash_merge_commit_title": "x"}}})
    proc = cli(world, "preflight", env=_env(github))
    assert proc.returncode == 1, proc.stderr
    assert not proc.stdout
    lines = proc.stderr.splitlines()
    assert [line.split(":")[0] for line in lines if ": " in line][:2] == [
        "forge-cli",
        "squash-defaults",
    ]
    assert (
        "gh api -X PATCH repos/owner/repo -f squash_merge_commit_title=PR_TITLE "
        "-f squash_merge_commit_message=PR_BODY"
    ) in lines


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("squash_merge_commit_title", "COMMIT_OR_PR_TITLE"),
        ("squash_merge_commit_message", "COMMIT_MESSAGES"),
    ],
)
def test_another_squash_default_is_wrong_and_names_the_patch(
    world, github, field, value
) -> None:
    github.answer({"$api": {REPO: {**SQUASHED, field: value}}})
    check = _run(world, _env(github), 1)["squash-defaults"]
    assert check["status"] == "wrong"
    assert f'{field} is "{value}"' in check["detail"]
    assert check["fix"] == (
        "gh api -X PATCH repos/owner/repo -f squash_merge_commit_title=PR_TITLE "
        "-f squash_merge_commit_message=PR_BODY"
    )


def test_squash_merging_turned_off_is_turned_on_by_the_patch(world, github) -> None:
    github.answer({"$api": {REPO: {**SQUASHED, "allow_squash_merge": False}}})
    check = _run(world, _env(github), 1)["squash-defaults"]
    assert check["status"] == "wrong"
    assert check["fix"].startswith(
        "gh api -X PATCH repos/owner/repo -F allow_squash_merge=true -f "
    )


def test_settings_github_shows_no_one_but_admins_are_skipped(world, github) -> None:
    github.answer({"$api": {REPO: {"allow_squash_merge": True}}})
    check = _run(world, _env(github), 0)["squash-defaults"]
    assert check["status"] == "skipped"
    assert "admins" in check["detail"]


def test_an_unreadable_repository_is_skipped(world, github) -> None:
    github.answer({"$api": {REPO: 1}})
    assert _run(world, _env(github), 0)["squash-defaults"]["status"] == "skipped"


def test_a_cli_signed_out_is_missing_and_skips_the_squash_check(world, github) -> None:
    checks = _run(world, _env(github, WT_AUTH_CODE="1"), 1)
    assert checks["forge-cli"]["status"] == "missing"
    assert checks["forge-cli"]["fix"] == "gh auth login --hostname github.com"
    assert "or the host is unreachable" in checks["forge-cli"]["detail"]
    assert checks["squash-defaults"]["status"] == "skipped"
    assert ["gh", "api", REPO] not in github.network_calls()


def test_offline_skips_both_forge_checks_without_a_call(world, github) -> None:
    checks = _run(world, _env(github, WORKTREES_OFFLINE="1"), 0)
    assert checks["forge-cli"]["status"] == "skipped"
    assert checks["squash-defaults"]["status"] == "skipped"
    assert not github.network_calls()


def test_a_cli_not_installed_is_missing(world, monkeypatch) -> None:
    from worktrees import branch_forge

    origin(world)
    monkeypatch.chdir(world.repo)
    monkeypatch.setattr(branch_forge, "available", lambda tool: False)
    check, target = preflight._forge_cli()
    assert check["status"] == "missing"
    assert check["fix"] == "install gh: https://cli.github.com"
    assert target == ("gh", "github.com", "owner/repo")


def test_gitlab_needs_glab_and_no_squash_defaults(world, network) -> None:
    origin(world, host="gitlab.com")
    _set_up(world)
    checks = _run(world, _env(network), 0)
    assert checks["forge-cli"]["detail"] == "glab is signed in to gitlab.com"
    assert checks["squash-defaults"]["status"] == "skipped"
    assert network.network_calls() == [
        ["glab", "auth", "status", "--hostname", "gitlab.com"]
    ]


@pytest.mark.parametrize("remote", ["another host", "none"])
def test_no_forge_skips_both_forge_checks(world, network, remote) -> None:
    if remote == "another host":
        origin(world, host="example.com")
    _set_up(world)
    checks = _run(world, _env(network), 0)
    assert checks["forge-cli"]["status"] == "skipped"
    assert checks["squash-defaults"]["status"] == "skipped"
    assert not network.network_calls()


CLAUDE = json.loads((SKILL / "project.json").read_text())


@pytest.mark.parametrize(
    ("settings", "status"),
    [
        (None, "missing"),
        ("{", "wrong"),
        ([], "wrong"),
        ({"permissions": {"allow": []}}, "missing"),
        ({"enabledPlugins": CLAUDE["enabledPlugins"]}, "missing"),
        ({"extraKnownMarketplaces": CLAUDE["extraKnownMarketplaces"]}, "missing"),
        ({**CLAUDE, "enabledPlugins": {"worktrees@mihaibojin": False}}, "wrong"),
        (
            {
                **CLAUDE,
                "extraKnownMarketplaces": {
                    "mihaibojin": {"source": {"source": "github", "repo": "x/y"}}
                },
            },
            "wrong",
        ),
        (
            {
                "additionalMarketplaces": CLAUDE["extraKnownMarketplaces"],
                "enabledPlugins": CLAUDE["enabledPlugins"],
            },
            "ok",
        ),
        ({"model": "x", **CLAUDE}, "ok"),
    ],
)
def test_claude_code_settings(world, network, settings, status) -> None:
    _set_up(world)
    path = world.repo / ".claude" / "settings.json"
    if settings is None:
        path.unlink()
    else:
        path.write_text(settings if isinstance(settings, str) else json.dumps(settings))
    check = _run(world, _env(network), 0 if status == "ok" else 1)["claude-code"]
    assert check["status"] == status
    if status != "ok":
        assert check["fix"] == f"{path} needs:\n{json.dumps(CLAUDE, indent=2)}"


CODEX = (SKILL / "config.toml").read_text()


@pytest.mark.parametrize(
    ("config", "status"),
    [
        (None, "missing"),
        ("[marketplaces", "wrong"),
        ('model = "x"\n', "missing"),
        (CODEX.split("\n\n")[0], "missing"),
        (CODEX.split("\n\n")[1], "missing"),
        (CODEX.replace("enabled = true", "enabled = false"), "wrong"),
        (CODEX.replace("MihaiBojin/agent-plugins", "x/y"), "wrong"),
        ('model = "x"\n\n' + CODEX, "ok"),
    ],
)
def test_codex_config(world, network, config, status) -> None:
    _set_up(world)
    path = world.repo / ".codex" / "config.toml"
    if config is None:
        path.unlink()
    else:
        path.write_text(config)
    check = _run(world, _env(network), 0 if status == "ok" else 1)["codex"]
    assert check["status"] == status
    if status != "ok":
        assert check["fix"] == f"{path} needs:\n{CODEX}".rstrip("\n")


@pytest.mark.parametrize(
    ("version", "status", "fix"),
    [
        ("", "missing", "https://github.com/MihaiBojin/worktrees#install"),
        ("0.0.1", "wrong", f"Pin it to v{__version__}:"),
        ("999.0.0", "wrong", "Upgrade them with `uv tool upgrade git-worktrees`."),
    ],
)
def test_the_shell_plugin_release(world, network, version, status, fix) -> None:
    _set_up(world)
    if __version__.count(".") != 2 and status == "wrong":
        pytest.skip("a source tree has no release to compare")
    env = network.env(WORKTREES_PLUGIN_VERSION=version)
    check = _run(world, env, 1)["shell-plugin"]
    assert check["status"] == status
    assert check["fix"].startswith(fix)


def test_outside_a_repository_it_refuses(world, network, tmp_path) -> None:
    import subprocess

    from conftest import argv

    outside = tmp_path / "outside"
    outside.mkdir()
    proc = subprocess.run(
        argv("gw", "preflight"),
        cwd=outside,
        env=_env(network),
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 3, proc.stderr
    assert "run it inside one" in proc.stderr


def test_the_skill_writes_what_the_command_checks() -> None:
    """Three copies of one setup: the CLI's, and the two files the skill
    copies from."""
    assert CLAUDE == preflight.CLAUDE_SETTINGS
    assert CODEX == preflight.CODEX_CONFIG
    assert tomllib.loads(CODEX) == {
        "marketplaces": {
            "mihaibojin": {
                "source_type": "git",
                "source": preflight.CODEX_SOURCE,
            }
        },
        "plugins": {"worktrees@mihaibojin": {"enabled": True}},
    }
