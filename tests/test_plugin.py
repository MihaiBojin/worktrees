"""The agent plugin in `plugin/`: its manifests agree, no skill pre-approves
a command that changes something it should ask about, and the jq filters the
prune skill runs read the JSON `gwp` and `gwbs` print."""

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
SKILL = PLUGIN / "skills" / "prune" / "SKILL.md"
SKILLS = sorted(PLUGIN.glob("skills/*/SKILL.md"))

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
    assert not (PLUGIN / "commands").exists()


def test_the_portable_manifest_is_the_claude_one_rewritten() -> None:
    """Byte for byte what agent-plugins' sync writes, which its validator
    compares."""
    claude = _manifest(".claude-plugin/plugin.json")
    wanted = {"$schema": SCHEMA, **{k: claude[k] for k in PORTABLE}}
    text = (PLUGIN / "plugin.json").read_text()
    assert text == json.dumps(wanted, indent=2, ensure_ascii=False) + "\n"


def _frontmatter(path: Path) -> str:
    return path.read_text().split("---")[1]


def _tools(path: Path) -> list[str]:
    return re.findall(r"^  - (.+)$", _frontmatter(path), re.M)


# What a skill runs only with the user's say-so, or the harness's: removal,
# merging, pushing, rerunning CI, moving a checkout. `gw sync` and
# `gw branch create` are pre-approved; neither loses work.
MUTATIONS = (
    "gw prune --yes",
    "gw p --yes",
    "gw remove x --yes",
    "command gw remove x --yes",
    "gw branch prune x --yes",
    "command gw branch prune x --yes",
    "gw add x",
    "command gw add x",
    "gw fast-forward --json",
    "command gw fast-forward --json",
    "gh pr merge 1",
    "gh pr update-branch 1",
    "gh run rerun 1",
    "glab mr merge 1",
    "git push",
    "git commit",
    "git merge --ff-only x",
    "git switch x",
    "git symbolic-ref HEAD refs/heads/x",
    "git update-ref refs/heads/x HEAD",
    "git branch -m x",
)


def test_every_skill_is_named_for_its_directory() -> None:
    assert [p.parent.name for p in SKILLS] == [
        "ci-checks",
        "describe-pr",
        "fast-forward",
        "merge",
        "new-branch",
        "prune",
    ]
    for path in SKILLS:
        front = _frontmatter(path)
        assert re.search(rf"^name: {path.parent.name}$", front, re.M), path
        assert re.search(r"^description: ", front, re.M), path


@pytest.mark.parametrize("path", SKILLS, ids=lambda p: p.parent.name)
def test_no_skill_preapproves_a_mutation(path: Path) -> None:
    for tool in _tools(path):
        found = re.fullmatch(r"Bash\((.+?)(:\*)?\)", tool)
        if not found:
            continue
        prefix, wildcard = found.groups()
        for mutation in MUTATIONS:
            # Its own command is the fast-forward skill's job, and
            # test_fast_forward_preapproves_the_conflict_loop_alone holds
            # the rest of its list.
            if path.parent.name == "fast-forward" and "fast-forward" in mutation:
                continue
            covered = mutation.startswith(prefix) if wildcard else mutation == prefix
            assert not covered, (tool, mutation)


def test_only_describe_pr_edits_a_review() -> None:
    """The user asked for descriptions to be corrected without a prompt."""
    for path in SKILLS:
        edits = [
            t
            for t in _tools(path)
            if t.startswith(("Bash(gh pr edit", "Bash(glab mr update"))
        ]
        assert bool(edits) == (path.parent.name == "describe-pr"), path


@pytest.mark.parametrize("path", sorted(PLUGIN.glob("skills/**/*.md")), ids=str)
def test_every_link_between_skills_resolves(path: Path) -> None:
    for target in re.findall(r"\]\(([^)#:]+\.md)\)", path.read_text()):
        assert (path.parent / target).is_file(), (path, target)


def test_the_prune_skill_preapproves_reads_and_sync_alone() -> None:
    assert {
        "Bash(gw sync)",
        "Bash(gw list --json)",
        "Bash(gw list --table)",
        "Bash(gw prune --json)",
        "Bash(jq:*)",
    } <= set(_tools(SKILL))


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
    assert shlex.split(row["undo"]) == ["git", "branch", "finished'quoted", row["sha"]]
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


FAST_FORWARD = PLUGIN / "skills" / "fast-forward" / "SKILL.md"


def _covers(tools: list[str], command: str) -> bool:
    """Would one of these allowed-tools patterns run `command` unasked?"""
    for tool in tools:
        found = re.fullmatch(r"Bash\((.+?)(:\*)?\)", tool)
        if found and (
            command == found.group(1)
            or (found.group(2) and command.startswith(found.group(1)))
        ):
            return True
    return False


def test_fast_forward_preapproves_the_conflict_loop_alone() -> None:
    """The loop the issue asks for runs unasked; anything that drops a
    commit, discards a resolution or leaves the checkout does not."""
    tools = _tools(FAST_FORWARD)
    for wanted in (
        "gw fast-forward --json",
        "git add README",
        "GIT_EDITOR=true git rebase --continue",
    ):
        assert _covers(tools, wanted), wanted
    for refused in (
        "git rebase --skip",
        "git rebase --abort",
        "git rebase main",
        "git reset --hard",
        "git checkout --theirs README",
        "git stash",
        "git switch main",
        "git push",
        "gw sync",
    ):
        assert not _covers(tools, refused), refused


def test_the_skill_names_the_fields_fast_forward_prints(world) -> None:
    """Every field and result the skill reads is one the CLI prints."""
    from worktrees import fast_forward

    listed = FAST_FORWARD.read_text().split("The result's fields:")[1]
    listed = listed.split("\n\n")[1]
    bullets = [line.removeprefix("- ").split(": ", 1) for line in listed.splitlines()]
    fields = {name for names, _ in bullets for name in re.findall(r"`(\w+)`", names)}
    results = set(re.findall(r"`([a-z-]+)`", bullets[0][1]))

    world.git("switch", "--quiet", "--create", "fix-parser")
    world.commit("README", "branch\n")
    world.git("switch", "--quiet", "main")
    world.commit("README", "main\n")
    world.git("switch", "--quiet", "fix-parser")
    proc = subprocess.run(
        argv("gw fast-forward", "--json"),
        cwd=world.repo,
        env=env_for(world),
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 1, proc.stderr
    printed = json.loads(proc.stdout)
    assert fields == set(printed) - {"head", "lastSync"}
    assert results == {
        fast_forward.CURRENT,
        fast_forward.FAST_FORWARDED,
        fast_forward.REBASED,
        fast_forward.ROTATED,
        fast_forward.CONFLICT,
    }


def _skill_filter(skill: str, command: str, nth: int = 0) -> tuple[list[str], str]:
    """The flags and program the skill's `nth` pipe of `command`'s JSON uses."""
    found = re.findall(
        rf"^{re.escape(command)} \| jq ((?:-[cr] ?|--arg \w+ '\w+' ?)+)'(.*?)'$",
        (PLUGIN / "skills" / skill / "SKILL.md").read_text(),
        re.S | re.M,
    )
    assert len(found) > nth, f"the {skill} skill's {command} pipeline is missing"
    flags, program = found[nth]
    return shlex.split(flags), program


def _run_jq(flags: list[str], program: str, piped: str, **args: str) -> str:
    for name, value in args.items():
        at = flags.index(name)
        flags = [*flags[: at + 1], value, *flags[at + 2 :]]
    proc = subprocess.run(
        ["jq", *flags, program], input=piped, capture_output=True, text=True, check=True
    )
    return proc.stdout


def test_merge_finds_the_checkout_holding_the_head_branch(world) -> None:
    from test_sync import origin

    world.worktree("feature")
    raw = json.loads(_json(world, "gw list"))
    flags, program = _skill_filter("merge", "gw list --json", nth=1)
    out = json.loads(_run_jq(flags, program, json.dumps(raw)))
    main = next(row for row in raw["worktrees"] if row["main"])
    assert out == {"branch": "main", "path": main["path"]}

    origin(world)
    world.git("symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/main")
    world.git("switch", "-q", "--detach", at=Path(main["path"]))
    raw = json.loads(_json(world, "gw list"))
    assert raw["head"]["ref"] == "refs/remotes/origin/main"
    out = json.loads(_run_jq(flags, program, json.dumps(raw)))
    assert out == {"branch": "main", "path": None}


def test_merge_finds_the_worktree_holding_a_branch(world) -> None:
    world.worktree("feat'quoted")
    world.worktree("other")
    raw = _json(world, "gw list")
    flags, program = _skill_filter("merge", "gw list --json")
    out = _run_jq(flags, program, raw, b="feat'quoted")
    rows = json.loads(raw)["worktrees"]
    assert out.splitlines() == [r["path"] for r in rows if r["branch"] == "feat'quoted"]
    assert len(out.splitlines()) == 1


def test_ci_projection_keeps_the_failures_and_the_head(world, network) -> None:
    from test_ci import _check, _pr
    from test_sync import origin

    origin(world)
    world.git("switch", "-qc", "feature")
    network.answer(
        {
            "$view": _pr(
                number=42,
                statusCheckRollup=[
                    _check("tests"),
                    _check("lint", conclusion="FAILURE"),
                ],
            )
        }
    )
    proc = subprocess.run(
        argv("gw ci-checks", "42", "--json"),
        cwd=world.repo,
        env=network.env(),
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stderr
    flags, program = _skill_filter("ci-checks", "gw ci-checks 42 --json")
    out = json.loads(_run_jq(flags, program, proc.stdout))
    raw = json.loads(proc.stdout)
    assert out["pr"] == {
        "number": raw["pr"]["number"],
        "headSha": raw["pr"]["headSha"],
        "mergeStateStatus": raw["pr"]["mergeStateStatus"],
    }
    assert out["checksState"] == "failed"
    assert out["stale"] is False
    assert out["failing"] == [
        {"name": "lint", "workflow": "CI", "link": raw["checks"][1]["link"]}
    ]
