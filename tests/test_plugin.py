"""The agent plugin in `plugin/`: its manifests agree, and the jq filters its
skill runs read the JSON `gwp` and `gwbs` print today, and the removal it
names removes."""

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


def _filter(entry: str) -> str:
    """The jq program the skill pipes `entry --json` into, as written."""
    found = re.search(
        rf"^{entry} --json \| jq -c '(.*?)'\n```",
        SKILL.read_text(),
        re.S | re.M,
    )
    assert found, f"the skill's {entry} command is not where this looks"
    return found.group(1)


def _jq(program: str, piped: str, head: str = "") -> dict:
    """Runs the program with `HEAD_SHA` written over, as the skill says."""
    program = program.replace("HEAD_SHA", head)
    proc = subprocess.run(
        ["jq", "-c", program], input=piped, capture_output=True, text=True, check=True
    )
    return json.loads(proc.stdout)


def _run(world, entry: str, *args: str) -> subprocess.CompletedProcess[str]:
    """The binary as an agent runs it: no terminal on any stream."""
    return subprocess.run(
        [
            sys.executable,
            "-c",
            f"from worktrees.cli import {entry}; raise SystemExit({entry}())",
            *args,
        ],
        cwd=world.repo,
        env=env_for(world),
        capture_output=True,
        text=True,
        stdin=subprocess.DEVNULL,
    )


def _json(world, entry: str) -> str:
    proc = _run(world, entry, "--json")
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

    branches = _jq(_filter("gwbs"), _json(world, "gwbs"))
    out = _jq(_filter("gwp"), _json(world, "gwp"), branches["head_sha"])

    worktrees = {row["branch"]: row for row in out["worktrees"]}
    assert set(worktrees) == {"squashed", "started"}
    assert worktrees["squashed"]["at_head"] is False
    assert worktrees["started"]["at_head"] is True
    assert worktrees["squashed"]["path"] == str(squashed)
    assert (out["kept"], out["unknown"], out["ignored"]) == (1, 0, [])

    # Checked out in a worktree, so gwbs keeps it and only gwp may take it.
    assert [row["branch"] for row in branches["branches"]] == ["loose"]
    assert branches["branches"][0]["proof"] == "squash"
    assert branches["branches"][0]["undo"].startswith("git branch loose ")
    assert (branches["kept"], branches["unknown"]) == (3, 0)


def test_the_skill_removes_only_the_chosen_worktree_by_its_path(world) -> None:
    """`command gwr 'PATH' --yes`, with the path the filter printed, removes
    that worktree and its branch without a terminal to ask at, and nothing
    else, even when run again after it is gone."""
    assert "`command gwr 'PATH' --yes 2>&1`" in SKILL.read_text()
    for name in ("done", "done-more"):
        wt = world.worktree(name)
        world.commit(f"{name}-file", at=wt)
        world.git("merge", "--quiet", "--squash", name)
        world.git("commit", "--quiet", "-m", f"{name} (#1)")
    head = world.git("rev-parse", "HEAD")

    out = _jq(_filter("gwp"), _json(world, "gwp"), head)
    rows = {row["branch"]: row for row in out["worktrees"]}
    assert set(rows) == {"done", "done-more"}
    chosen = rows["done"]["path"]
    proc = _run(world, "gwr", chosen, "--yes")
    assert proc.returncode == 0, proc.stderr
    assert "restore with: git branch done " in proc.stderr
    assert "removed done" in proc.stderr
    assert not Path(chosen).exists()
    assert world.git("branch", "--list", "done") == ""

    again = _run(world, "gwr", chosen, "--yes")
    assert again.returncode == 1
    assert Path(rows["done-more"]["path"]).exists()
    assert world.git("branch", "--list", "done-more") != ""
