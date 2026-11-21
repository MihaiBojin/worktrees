"""Whether gw and its agent plugin are set up in this repository.

Every check reads; none writes. Each answers with a status and, when the
status is not `ok`, the fix for a person or the preflight skill to apply.
"""

from __future__ import annotations

import json
import os
import shlex
from pathlib import Path

from . import branch_forge, forge, sync
from . import repo as R

OK, MISSING, WRONG, SKIPPED = "ok", "missing", "wrong", "skipped"

MARKETPLACE = "mihaibojin"
PLUGIN = f"worktrees@{MARKETPLACE}"
# The marketplace as Claude Code's `extraKnownMarketplaces` names a GitHub
# repository, and as `codex plugin marketplace add` writes it.
CLAUDE_SOURCE = {"source": "github", "repo": "MihaiBojin/agent-plugins"}
CODEX_SOURCE = "https://github.com/MihaiBojin/agent-plugins.git"
# plugin/skills/preflight/project.json and config.toml hold the same lines;
# tests/test_preflight.py holds the three to each other.
CLAUDE_SETTINGS = {
    "extraKnownMarketplaces": {MARKETPLACE: {"source": CLAUDE_SOURCE}},
    "enabledPlugins": {PLUGIN: True},
}
CODEX_CONFIG = (
    f"[marketplaces.{MARKETPLACE}]\n"
    'source_type = "git"\n'
    f'source = "{CODEX_SOURCE}"\n'
    "\n"
    f'[plugins."{PLUGIN}"]\n'
    "enabled = true\n"
)
# What `gw merge` needs GitHub to write when it squashes a stack's lower pull
# requests in one call: each one's reviewed title and body.
SQUASH = {
    "squash_merge_commit_title": "PR_TITLE",
    "squash_merge_commit_message": "PR_BODY",
}


def _check(name: str, status: str, detail: str, fix: str = "") -> dict:
    return {"name": name, "status": status, "detail": detail, "fix": fix or None}


def run(top: Path, plugin: tuple[str, bool]) -> list[dict]:
    """Every check, in the order a person fixes them."""
    forge_cli, target = _forge_cli()
    return [
        forge_cli,
        _squash(target, forge_cli["status"]),
        _claude(top / ".claude" / "settings.json"),
        _codex(top / ".codex" / "config.toml"),
        _shell(plugin),
    ]


def _forge_cli() -> tuple[dict, tuple[str, str, str] | None]:
    """Whether the forge's CLI is installed and signed in for the default
    remote's host, and the tool, host and `owner/repo` it reads."""
    name = "forge-cli"
    remote = R.remote()
    if not remote:
        return _check(name, SKIPPED, "no default remote"), None
    selected = branch_forge.provider(remote)
    if selected is None:
        return _check(name, SKIPPED, f"{remote} is not on GitHub or GitLab"), None
    tool, repository = selected
    host, path = repository.split("/", 1)
    target = (tool, host, path)
    if not branch_forge.available(tool):
        site = (
            "https://cli.github.com"
            if tool == "gh"
            else "https://gitlab.com/gitlab-org/cli"
        )
        return _check(
            name, MISSING, f"{tool} is not installed", f"install {tool}: {site}"
        ), target
    if sync.offline():
        detail = (
            f"{tool} is installed; {sync.OFFLINE} is set, so its sign-in is not checked"
        )
        return _check(name, SKIPPED, detail), target
    if not forge.authenticated(tool, host):
        fix = shlex.join([tool, "auth", "login", "--hostname", host])
        detail = (
            f"{tool} auth status --hostname {host} failed: not signed in, "
            "or the host is unreachable"
        )
        return _check(name, MISSING, detail, fix), target
    return _check(name, OK, f"{tool} is signed in to {host}"), target


def _squash(target: tuple[str, str, str] | None, signed_in: str) -> dict:
    """Whether GitHub writes a squash commit from the pull request's title
    and body."""
    name = "squash-defaults"
    if target is None:
        return _check(name, SKIPPED, "no GitHub remote")
    tool, host, path = target
    if tool != "gh":
        return _check(name, SKIPPED, "GitLab takes the squash message gw merge passes")
    if sync.offline():
        return _check(name, SKIPPED, f"{sync.OFFLINE} is set")
    if signed_in != OK:
        return _check(name, SKIPPED, "gh is not signed in")
    hostname = [] if host == "github.com" else ["--hostname", host]
    read = ["gh", "api", f"repos/{path}", *hostname]
    settings = forge.document(read)
    if settings is None:
        return _check(name, SKIPPED, f"{shlex.join(read)} failed")
    if any(key not in settings for key in ("allow_squash_merge", *SQUASH)):
        return _check(
            name, SKIPPED, f"GitHub shows {path}'s merge settings only to its admins"
        )
    wanted = {"allow_squash_merge": True, **SQUASH}
    wrong = [key for key, value in wanted.items() if settings[key] != value]
    if not wrong:
        return _check(
            name, OK, f"{path} squashes with each pull request's title and body"
        )
    fix = ["gh", "api", "-X", "PATCH", f"repos/{path}", *hostname]
    if "allow_squash_merge" in wrong:
        fix += ["-F", "allow_squash_merge=true"]
    for key, value in SQUASH.items():
        fix += ["-f", f"{key}={value}"]
    found = ", ".join(f"{key} is {json.dumps(settings[key])}" for key in wrong)
    return _check(
        name,
        WRONG,
        f"{path}: {found}; gw merge squashes a stack's lower pull requests "
        "with these settings",
        shlex.join(fix),
    )


def _claude(path: Path) -> dict:
    """Whether the project's Claude Code settings declare the marketplace and
    enable the plugin."""
    name = "claude-code"
    fix = f"{path} needs:\n{json.dumps(CLAUDE_SETTINGS, indent=2)}"
    try:
        settings = json.loads(path.read_text())
    except FileNotFoundError:
        return _check(name, MISSING, f"{path} does not exist", fix)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        return _check(name, WRONG, f"{path} does not parse: {exc}", fix)
    if not isinstance(settings, dict):
        return _check(name, WRONG, f"{path} is not a JSON object", fix)
    known = _table(settings, "extraKnownMarketplaces") or _table(
        settings, "additionalMarketplaces"
    )
    entry = known.get(MARKETPLACE)
    enabled = _table(settings, "enabledPlugins").get(PLUGIN)
    if entry is None and enabled is None:
        return _check(
            name, MISSING, f"{path} names neither {MARKETPLACE} nor {PLUGIN}", fix
        )
    problems = []
    if entry is None:
        problems.append(f"declares no {MARKETPLACE} marketplace")
    elif not _same_repo(entry):
        problems.append(f"declares {MARKETPLACE} from another source")
    if enabled is None:
        problems.append(f"does not enable {PLUGIN}")
    elif enabled is not True:
        problems.append(f"sets {PLUGIN} to {json.dumps(enabled)}")
    if problems:
        status = WRONG if entry is not None and enabled is not None else MISSING
        return _check(name, status, f"{path} " + " and ".join(problems), fix)
    return _check(
        name,
        OK,
        f"{path} enables {PLUGIN}; it loads in a trusted folder once "
        f"`claude plugin install {PLUGIN}` has installed it",
    )


def _table(settings: dict, key: str) -> dict:
    value = settings.get(key)
    return value if isinstance(value, dict) else {}


def _same_repo(entry: object) -> bool:
    source = entry.get("source") if isinstance(entry, dict) else None
    return (
        isinstance(source, dict)
        and source.get("source") == CLAUDE_SOURCE["source"]
        and str(source.get("repo", "")).lower() == CLAUDE_SOURCE["repo"].lower()
    )


def _codex(path: Path) -> dict:
    """Whether the project's Codex config declares the marketplace and
    enables the plugin."""
    import tomllib

    name = "codex"
    fix = f"{path} needs:\n{CODEX_CONFIG}".rstrip("\n")
    upgrade = f"codex plugin marketplace upgrade {MARKETPLACE}"
    try:
        with path.open("rb") as stream:
            config = tomllib.load(stream)
    except FileNotFoundError:
        return _check(name, MISSING, f"{path} does not exist", fix)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        return _check(name, WRONG, f"{path} does not parse: {exc}", fix)
    entry = _table(_table(config, "marketplaces"), MARKETPLACE) or None
    plugin = _table(_table(config, "plugins"), PLUGIN) or None
    if entry is None and plugin is None:
        return _check(
            name, MISSING, f"{path} names neither {MARKETPLACE} nor {PLUGIN}", fix
        )
    problems = []
    if entry is None:
        problems.append(f"declares no {MARKETPLACE} marketplace")
    elif entry.get("source_type") != "git" or entry.get("source") != CODEX_SOURCE:
        problems.append(f"declares {MARKETPLACE} from another source")
    if plugin is None:
        problems.append(f"does not enable {PLUGIN}")
    elif plugin.get("enabled") is not True:
        problems.append(f"does not set {PLUGIN} enabled = true")
    if problems:
        status = WRONG if entry is not None and plugin is not None else MISSING
        return _check(name, status, f"{path} " + " and ".join(problems), fix)
    return _check(
        name,
        OK,
        f"{path} enables {PLUGIN}; in a trusted project, {upgrade} installs it",
    )


def _shell(plugin: tuple[str, bool]) -> dict:
    """Whether the shell plugin here is the release these commands are."""
    text, same = plugin
    name = "shell-plugin"
    detail = " ".join(text.splitlines())
    if same:
        return _check(name, OK, detail)
    from .cli import PLUGIN_VERSION

    install = "https://github.com/MihaiBojin/worktrees#install"
    if not os.environ.get(PLUGIN_VERSION):
        return _check(name, MISSING, detail, install)
    first, _, rest = text.partition("\n")
    return _check(name, WRONG, first, rest or install)


def passed(checks: list[dict]) -> bool:
    return all(c["status"] in (OK, SKIPPED) for c in checks)
