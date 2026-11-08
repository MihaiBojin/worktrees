"""Branch assessment and deletion through the installed command interfaces."""

from __future__ import annotations

import json
import shlex
import subprocess

import pytest
from conftest import argv, env_for


def cli(world, *args: str, entry: str = "gw", env=None):
    return subprocess.run(
        argv(entry, *args),
        cwd=world.repo,
        env=env or env_for(world),
        capture_output=True,
        text=True,
        stdin=subprocess.DEVNULL,
    )


def local_branch(world, name="feature", path="feature", body="branch\n") -> str:
    world.git("checkout", "-qb", name)
    sha = world.commit(path, body)
    world.git("checkout", "-q", "main")
    return sha


def status(world, *names) -> dict:
    p = cli(world, "branch", "status", *names, "--json")
    assert p.returncode == 0, p.stderr
    return json.loads(p.stdout)


def test_reimported_content_is_removable_without_a_worktree_or_upstream(world):
    sha = local_branch(world)
    (world.repo / "feature").write_text("branch\n")
    world.commit("unrelated", "other work\n")
    world.git("add", "feature")
    world.git("commit", "--amend", "--no-edit", "--quiet")
    assert world.git("cherry", "main", "feature").startswith("+")
    old = cli(world, "prune", "--json")
    assert "feature" not in {r["branch"] for r in json.loads(old.stdout)["verdicts"]}
    rows = status(world, "feature")["verdicts"]
    assert len(rows) == 1
    assert rows[0]["sha"] == sha
    assert rows[0]["verdict"] == "remove"
    assert rows[0]["proof"] == "content"


def test_branch_status_lists_only_loose_branches(world):
    world.worktree("occupied")
    world.merged_branch("done")
    rows = {r["branch"]: r for r in status(world)["verdicts"]}
    assert set(rows) == {"done"}
    assert rows["done"]["verdict"] == "remove"
    assert rows["done"]["eligible"]


@pytest.mark.parametrize("entry", ["gw branch status", "gw branch prune"])
def test_console_aliases_work(world, entry):
    world.merged_branch("done")
    args = ["done", "--json"]
    if entry == "gw branch prune":
        args += ["--dry-run"]
    p = cli(world, *args, entry=entry)
    assert p.returncode == 0, p.stderr
    assert json.loads(p.stdout)["verdicts"][0]["verdict"] == "remove"


@pytest.mark.parametrize("where", ["current", "linked"])
def test_a_named_checked_out_branch_is_never_deleted(world, where):
    """Proved finished is not enough: the branch a checkout stands on stays."""
    if where == "linked":
        world.merged_worktree("done")
    else:
        world.merged_branch("done")
        world.git("checkout", "-q", "done")
    sha = world.git("rev-parse", "refs/heads/done")
    p = cli(world, "done", "--yes", "--delete", "all", entry="gw branch prune")
    assert p.returncode == 3, p.stderr
    assert "checked out in a worktree" in p.stderr
    assert world.git("rev-parse", "refs/heads/done") == sha


def test_delete_help_names_every_permission_value(world):
    p = cli(world, "-h", entry="gw branch prune")
    assert p.returncode == 0, p.stderr
    text = " ".join((p.stdout + p.stderr).split())
    assert "empty branches or stashes; all permits both" in text


def test_multiple_exact_names_do_not_select_a_prefix(world):
    for name in ("one", "two", "one-more"):
        world.merged_branch(name)
    assert [r["branch"] for r in status(world, "one", "two")["verdicts"]] == [
        "one",
        "two",
    ]


def test_unknown_name_prevents_any_deletion(world):
    world.merged_branch("done")
    p = cli(
        world,
        "branch",
        "prune",
        "done",
        "missing",
        "--yes",
    )
    assert p.returncode == 2
    assert world.git("rev-parse", "refs/heads/done")


@pytest.mark.parametrize("name", ["done", "a'b", "$(id)"])
def test_delete_prints_an_executable_undo_command(world, name):
    world.merged_branch(name)
    sha = world.git("rev-parse", f"refs/heads/{name}")
    p = cli(world, "branch", "prune", name, "--yes", "--json")
    assert p.returncode == 0, p.stderr
    result = json.loads(p.stdout)
    assert result["removed"] == [name]
    row = result["verdicts"][0]
    assert row["undo"] == f"git branch {shlex.quote(name)} {sha}"
    assert row["undo"] in p.stderr
    assert not world.git("for-each-ref", "--format=%(refname)", f"refs/heads/{name}")
    subprocess.run(shlex.split(row["undo"]), cwd=world.repo, check=True)
    assert world.git("rev-parse", f"refs/heads/{name}") == sha


@pytest.mark.parametrize("args", [("--dry-run",), ()])
def test_a_pipe_cannot_delete_without_yes(world, args):
    world.merged_branch("done")
    p = cli(world, "branch", "prune", "done", *args)
    assert p.returncode == (0 if args else 3), p.stderr
    assert world.git("rev-parse", "refs/heads/done")


def test_no_arguments_requires_a_selector_even_with_yes(world):
    world.merged_branch("done")
    p = cli(world, "branch", "prune", "--yes")
    assert p.returncode == 3
    assert "terminal" in p.stderr
    assert world.git("rev-parse", "refs/heads/done")


def test_unmerged_branch_is_not_deleted(world):
    sha = local_branch(world)
    p = cli(
        world,
        "branch",
        "prune",
        "feature",
        "--yes",
        "--json",
    )
    assert p.returncode == 3, p.stderr
    assert json.loads(p.stdout)["removed"] == []
    assert world.git("rev-parse", "feature") == sha
    assert "feature kept: " in p.stderr, "exit 3 with nothing said"


@pytest.mark.parametrize("change", ["different", "mode", "deleted", "renamed"])
def test_content_proof_requires_every_touched_path_to_match(world, change):
    local_branch(world)
    if change == "different":
        world.commit("feature", "other\n")
    else:
        world.commit("feature", "branch\n")
        world.git("commit", "--amend", "--quiet", "-m", "reimport")
        if change == "mode":
            world.git("update-index", "--chmod=+x", "feature")
        elif change == "deleted":
            world.git("rm", "feature")
        else:
            world.git("mv", "feature", "renamed")
        world.commit("unrelated", "x\n")
    # Call the content probe directly: a historical patch can independently
    # prove a squash merge even when later commits change the path again.
    from worktrees.merged import content_matches

    assert not content_matches(
        world.git("rev-parse", "feature"), world.git("rev-parse", "main")
    )


def test_content_proof_accepts_matching_deletions_and_unusual_paths(world):
    world.commit("removed", "old\n")
    world.git("checkout", "-qb", "feature")
    world.git("rm", "removed")
    world.commit("a\nb\tc", "new\n")
    world.git("checkout", "-q", "main")
    world.git("rm", "removed")
    (world.repo / "a\nb\tc").write_text("new\n")
    world.git("add", ".")
    world.commit("unrelated", "x\n")
    assert status(world, "feature")["verdicts"][0]["proof"] == "content"


@pytest.mark.parametrize("answer", ["1\ny\n", "1 2\ny\n", "\n", "1\nn\n", "9\n"])
def test_selector_requires_a_selection_then_confirmation(world, answer):
    from test_cli import run_on_a_terminal

    world.merged_branch("one")
    world.merged_branch("two")
    code, out = run_on_a_terminal(world, "gw branch prune", answer=answer)
    assert code == (3 if answer == "9\n" else 0), out
    names = world.git(
        "for-each-ref", "--format=%(refname:short)", "refs/heads"
    ).splitlines()
    assert ("one" not in names) == (answer in ("1\ny\n", "1 2\ny\n"))
    assert ("two" not in names) == (answer == "1 2\ny\n")


def test_all_deletes_only_proven_branches(world):
    world.merged_branch("done")
    local_branch(world)
    p = cli(
        world,
        "branch",
        "prune",
        "--all",
        "--yes",
        "--json",
    )
    assert p.returncode == 0, p.stderr
    assert json.loads(p.stdout)["removed"] == ["done"]
    assert world.git("rev-parse", "feature")


@pytest.mark.parametrize("changed", ["tip", "head", "checkout"])
def test_deletion_rechecks_the_approved_state(world, changed):
    from worktrees import branches
    from worktrees.git import Refused

    world.merged_branch("done")
    head = world.git("rev-parse", "main")
    row = branches.assess(["done"], head, "main", "", False)[0]
    if changed == "checkout":
        world.git("worktree", "add", str(world.root / "occupied"), "done")
    else:
        new = world.commit("new", "new\n")
        if changed == "tip":
            world.git("update-ref", "refs/heads/done", new)
            world.git("update-ref", "refs/heads/main", head)
    with pytest.raises(Refused):
        branches.delete(row, "refs/heads/main", head, "main", "", False)
    assert world.git("rev-parse", "done")


def test_completion_lists_names_without_querying_the_forge(world):
    world.merged_branch("one")
    for entry in ("gw branch status", "gw branch prune"):
        p = cli(world, "--complete", entry=entry)
        assert p.returncode == 0, p.stderr
        assert {line.split("\t")[0] for line in p.stdout.splitlines()} == {
            "main",
            "one",
        }


def test_long_name_and_branch_help(world):
    p = cli(world, "branch", "--help", entry="main")
    assert p.returncode == 0
    assert "status" in p.stdout and "prune" in p.stdout
    p = cli(
        world,
        "branch",
        "status",
        "main",
        "--json",
        entry="main",
    )
    assert p.returncode == 0, p.stderr
    assert json.loads(p.stdout)["verdicts"] == []


def test_ref_deletion_never_follows_a_changed_symbolic_ref(world):
    from worktrees import prune

    world.merged_branch("done")
    sha = world.git("rev-parse", "main")
    world.git("symbolic-ref", "refs/heads/done", "refs/heads/main")
    prune.ref_delete("refs/heads/done", sha)
    assert world.git("rev-parse", "refs/heads/main") == sha


def test_unrelated_history_is_unknown_without_an_operational_error(world):
    world.git("checkout", "--orphan", "unrelated")
    world.commit("README", "unrelated history\n")
    world.git("checkout", "main")
    row = status(world, "unrelated")["verdicts"][0]
    assert row["verdict"] == "unknown"
    assert not row["error"]
    assert "no common history" in row["why"]


def test_json_reports_only_successful_deletions(world, monkeypatch, capsys):
    from worktrees import cli as C
    from worktrees import prune
    from worktrees.git import GitError

    for name in ("one", "two"):
        world.merged_branch(name)
    original = prune.ref_delete

    def fail_one(ref, sha, **kwargs):
        if ref == "refs/heads/one":
            raise GitError("cannot lock ref")
        return original(ref, sha, **kwargs)

    monkeypatch.setattr(prune, "ref_delete", fail_one)
    code = C.gw(["branch", "prune", "one", "two", "--yes", "--json"])
    out = capsys.readouterr()
    assert code == 1
    result = json.loads(out.out)
    assert result["removed"] == ["two"]
    assert result["failed"] == [{"branch": "one", "why": "cannot lock ref"}]
    assert world.git("rev-parse", "one")


def test_successful_deletion_is_reported_to_a_person(world):
    world.merged_branch("done")
    p = cli(world, "branch", "prune", "done", "--yes")
    assert p.returncode == 0, p.stderr
    assert "deleted 1 branch: done" in p.stdout


def test_the_selector_and_the_plan_name_each_verdict(world):
    """keep and unknown are different reasons a row cannot be picked."""
    from test_cli import run_on_a_terminal

    world.merged_branch("done")
    code, out = run_on_a_terminal(world, "gw branch prune", answer="1\nn\n")
    assert code == 0, out
    rows = {
        ln.split()[2]: ln.split()[1]
        for ln in out.splitlines()
        if ln[:3].strip() in ("-", "1")
    }
    assert rows == {"done": "remove"}
    assert "VERDICT" in out.split("deleting 1 branch:")[1]


def test_the_spinner_line_names_each_branch_and_how_far_along(world, monkeypatch):
    """--all can mean dozens of branches, each a round trip to the forge."""
    from worktrees import branches, progress

    for name in ("one", "two"):
        world.merged_branch(name)
    said: list[str] = []
    monkeypatch.setattr(progress, "say", said.append)
    head = world.git("rev-parse", "HEAD")
    branches.assess([], head, "main", "", ask_forge=False)
    assert "one: checking completion evidence (2 of 3)" in said
    assert "two: checking completion evidence (3 of 3)" in said


def test_version_prints_the_repository_head(world):
    p = cli(world, "version")
    assert p.returncode == 0, p.stderr
    assert "Head: main (inferred)" in p.stderr


def test_remove_help_describes_removing_a_worktree(world):
    p = cli(world, "remove", "--help")
    assert p.returncode == 0, p.stderr
    assert "remove a worktree" in p.stderr
