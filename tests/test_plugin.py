"""The agent plugin in `plugin/`: its manifests agree, and the jq filters its
skill runs read the JSON `gwp` and `gwbs` print."""

from __future__ import annotations

import json
import re
import shlex
import subprocess
from pathlib import Path

import pytest
from conftest import argv, env_for

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


@pytest.mark.parametrize("path", [PLUGIN / "commands" / "prune.md", SKILL])
def test_the_plugin_preapproves_reads_and_sync_alone(path: Path) -> None:
    """`gw prune --yes` removes worktrees, so no pattern may cover it; each
    removal goes through a command the user is asked to approve."""
    tools = re.findall(r"^  - (.+)$", path.read_text().split("---")[1], re.M)
    prefixes = [m.group(1) for t in tools if (m := re.fullmatch(r"Bash\((.+):\*\)", t))]
    for removal in ("gw prune --yes", "gw p --yes"):
        assert not [p for p in prefixes if removal.startswith(p)], removal
    assert {
        "Bash(gw sync)",
        "Bash(gw list --json)",
        "Bash(gw list --list)",
        "Bash(gw prune --json)",
        "Bash(jq:*)",
    } <= set(tools)


def _filter(command: str) -> str:
    """Read the actual assessment pipeline from the installed skill."""
    found = re.search(
        rf"^{command} --json \| jq -c '(.*?)'\n```",
        SKILL.read_text(),
        re.S | re.M,
    )
    assert found, f"the skill's {command} pipeline is missing"
    return found.group(1)


def _jq(program: str, piped: str) -> dict:
    proc = subprocess.run(
        ["jq", "-c", program], input=piped, capture_output=True, text=True, check=True
    )
    return json.loads(proc.stdout)


def _json(world, entry: str, *args: str, cwd: Path | None = None) -> str:
    proc = subprocess.run(
        argv(entry, *args, "--json"),
        cwd=cwd or world.repo,
        env=env_for(world),
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stderr
    return proc.stdout


def test_filters_preserve_exact_targets_permissions_and_restore_commands(world) -> None:
    finished = world.worktree("finished'quoted")
    world.commit("finished-file", at=finished)
    world.git("merge", "--quiet", "--squash", "finished'quoted")
    world.git("commit", "--quiet", "-m", "squashed finish")
    exact = world.root / "checkout with spaces and 'quotes'"
    world.git("worktree", "move", str(finished), str(exact))
    empty = world.worktree("empty")
    blocked = world.merged_worktree("stash-blocked")
    (blocked / "README").write_text("stash me")
    world.git("stash", "push", "-m", "user's saved work", at=blocked)
    world.merged_branch("loose'quoted")
    world.git("branch", "loose-empty")
    current = world.merged_worktree("current")
    unfinished = world.worktree("unfinished")
    world.commit("unfinished-file", at=unfinished)
    (unfinished / "README").write_text("stash me too")
    world.git("stash", "push", "-m", "unfinished work", at=unfinished)

    raw = json.loads(_json(world, "gw prune", cwd=current))
    out = _jq(_filter("gw prune"), json.dumps(raw))
    assert out["head"] == raw["head"]
    assert out["lastSync"] == raw["lastSync"]
    assert [row["branch"] for row in out["worktrees"]] == ["finished'quoted"]
    row = out["worktrees"][0]
    assert row == next(v for v in raw["verdicts"] if v["path"] == str(exact))
    assert row["path"] == str(exact)
    assert row["proof"] == "squash"
    assert not row["protections"] and not row["stashes"]
    # The main checkout is empty too, but no permission makes it removable.
    assert [v["path"] for v in out["empty"]] == [str(empty)]
    # A kept branch with a stash is kept for its commits, not its stash.
    assert [v["branch"] for v in out["stash_blocked"]] == ["stash-blocked"]
    assert out["stash_blocked"][0]["stashes"]
    assert not any(v["path"] == str(current) for v in out["worktrees"])

    raw_branches = json.loads(_json(world, "gw branch status"))
    branches = _jq(_filter("gw branch status"), json.dumps(raw_branches))
    assert branches["head"] == raw_branches["head"]
    assert branches["lastSync"] == raw_branches["lastSync"]
    assert [v["branch"] for v in branches["branches"]] == ["loose'quoted"]
    assert branches["branches"][0] == next(
        v for v in raw_branches["verdicts"] if v["branch"] == "loose'quoted"
    )
    undo = branches["branches"][0]
    assert shlex.split(undo["undo"]) == ["git", "branch", "loose'quoted", undo["sha"]]
    assert [v["branch"] for v in branches["empty"]] == ["loose-empty"]
    assert not branches["stash_blocked"]
    assert all(not v["paths"] for v in branches["branches"])


def test_filters_keep_ignored_and_stash_permissions_distinct(world) -> None:
    ignored = world.merged_worktree("ignored")
    (ignored / ".gitignore").write_text("secret\n")
    world.git("add", ".gitignore", at=ignored)
    world.git("commit", "--quiet", "-m", "ignore", at=ignored)
    world.git("merge", "--quiet", "--no-ff", "-m", "merged ignore", "ignored")
    (ignored / "secret").write_text("ignored content")
    stashed = world.merged_worktree("loose-stashed")
    (stashed / "README").write_text("saved")
    world.git("stash", "push", "-m", "saved 'subject'", at=stashed)
    world.git("worktree", "remove", str(stashed))
    world.worktree("empty")

    out = _jq(_filter("gw prune"), _json(world, "gw prune"))
    assert not out["worktrees"]
    assert [v["branch"] for v in out["ignored"]] == ["ignored"]
    assert out["ignored"][0]["ignored"] == 1
    permitted = _jq(
        _filter("gw prune"),
        _json(world, "gw prune", "--delete", "ignored-files"),
    )
    assert [v["branch"] for v in permitted["worktrees"]] == ["ignored"]
    assert not permitted["worktrees"][0]["protections"]
    assert any(v["branch"] == "empty" and not v["eligible"] for v in permitted["empty"])
    branches = _jq(_filter("gw branch status"), _json(world, "gw branch status"))
    assert not branches["branches"]
    assert [v["branch"] for v in branches["stash_blocked"]] == ["loose-stashed"]
    assert branches["stash_blocked"][0]["stashes"]
    assert branches["stash_blocked"][0]["undo"].startswith("git branch loose-stashed ")


def test_branch_filter_preserves_sync_and_empty_rows(world) -> None:
    world.merged_branch("finished")
    world.git("branch", "empty")
    raw = json.loads(_json(world, "gw branch status"))
    out = _jq(_filter("gw branch status"), json.dumps(raw))
    assert out["head"] == raw["head"]
    assert out["lastSync"] == raw["lastSync"]
    assert [v["branch"] for v in out["empty"]] == ["empty"]
    assert out["branches"] == [v for v in raw["verdicts"] if v["eligible"]]
