"""The agent plugin in `plugin/`: its manifests agree, and the jq filter its
skill runs reads the JSON `gws` and `gwbs` print today."""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

from conftest import env_for

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "plugin"
SKILL = PLUGIN / "skills" / "worktrees" / "SKILL.md"

# The fields the Agent Plugins v1 manifest names, in the order
# releasetools/agent-plugins writes them.
PORTABLE = (
    "name",
    "version",
    "description",
    "author",
    "homepage",
    "repository",
    "license",
    "keywords",
)
SCHEMA = "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json"


def _manifest(relative: str) -> dict:
    return json.loads((PLUGIN / relative).read_text())


def test_the_two_client_manifests_describe_one_plugin() -> None:
    claude = _manifest(".claude-plugin/plugin.json")
    codex = _manifest(".codex-plugin/plugin.json")
    assert {k: claude[k] for k in PORTABLE} == {k: codex[k] for k in PORTABLE}
    assert codex["skills"] == "./skills/"
    assert codex["commands"] == "./commands/"


def test_the_portable_manifest_is_the_claude_one_rewritten() -> None:
    """Byte for byte what agent-plugins' sync writes, which its validator
    compares."""
    claude = _manifest(".claude-plugin/plugin.json")
    wanted = {"$schema": SCHEMA, **{k: claude[k] for k in PORTABLE}}
    text = (PLUGIN / "plugin.json").read_text()
    assert text == json.dumps(wanted, indent=2, ensure_ascii=False) + "\n"


def _filter() -> str:
    """The jq program in the skill, exactly as an agent runs it."""
    found = re.search(r"\| jq -cs '(.*?)'\n```", SKILL.read_text(), re.S)
    assert found, "the skill's assessment command is not where this looks"
    return found.group(1)


def _json(world, entry: str) -> str:
    proc = subprocess.run(
        [
            sys.executable,
            "-c",
            f"from worktrees.cli import {entry}; raise SystemExit({entry}())",
            "--no-fetch",
            "--no-forge",
            "--json",
        ],
        cwd=world.repo,
        env=env_for(world),
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stderr
    return proc.stdout


def test_the_skill_filter_keeps_what_the_user_decides_on(world) -> None:
    squashed = world.worktree("squashed")
    world.commit("squashed-file", at=squashed)
    world.git("checkout", "-qb", "loose")
    world.commit("loose-file")
    world.git("checkout", "-q", "main")
    for branch in ("squashed", "loose"):
        world.git("merge", "--quiet", "--squash", branch)
        world.git("commit", "--quiet", "-m", f"{branch} (#1)")
    world.worktree("started")

    piped = _json(world, "gws") + _json(world, "gwbs")
    proc = subprocess.run(
        ["jq", "-cs", _filter()],
        input=piped,
        capture_output=True,
        text=True,
        check=True,
    )
    out = json.loads(proc.stdout)

    worktrees = {row["branch"]: row for row in out["worktrees"]}
    assert set(worktrees) == {"squashed", "started"}
    assert worktrees["squashed"]["at_head"] is False
    assert worktrees["started"]["at_head"] is True
    assert worktrees["squashed"]["path"] == str(squashed)

    # Checked out in a worktree, so gwbs keeps it and only gwp may take it.
    assert [row["branch"] for row in out["branches"]] == ["loose"]
    assert out["branches"][0]["proof"] == "squash"
    assert out["branches"][0]["undo"].startswith("git branch loose ")

    assert out["kept"] == {"worktrees": 1, "branches": 3}
    assert out["unknown"] == {"worktrees": 0, "branches": 0}
    assert out["ignored"] == []
