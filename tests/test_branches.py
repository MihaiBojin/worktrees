"""Branch assessment and deletion through the installed command interfaces."""

from __future__ import annotations

import json
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from conftest import env_for


def cli(world, *args: str, entry: str = "gw", env=None):
    return subprocess.run(
        [
            sys.executable,
            "-c",
            f"from worktrees.cli import {entry}; raise SystemExit({entry}())",
            *args,
        ],
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
    assert "feature" not in {v["branch"] for v in json.loads(old.stdout)["verdicts"]}
    rows = status(world, "feature")["verdicts"]
    assert len(rows) == 1
    assert rows[0]["sha"] == sha
    assert rows[0]["verdict"] == "remove"
    assert rows[0]["proof"] == "content"


def reimport(world) -> None:
    """The branch's change lands on the head branch inside a larger commit."""
    (world.repo / "feature").write_text("branch\n")
    world.commit("unrelated", "other work\n")
    world.git("add", "feature")
    world.git("commit", "--amend", "--no-edit", "--quiet")


def worktree_status(world) -> dict:
    p = cli(world, "prune", "--json")
    assert p.returncode == 0, p.stderr
    return {r["branch"]: r for r in json.loads(p.stdout)["verdicts"]}


def test_content_alone_settles_a_worktree(world):
    wt = world.worktree("feature")
    world.commit("feature", "branch\n", at=wt)
    reimport(world)
    # The squash probe compares one combined patch with each commit, and the
    # head branch carries this change beside others: it says not merged.
    assert world.git("cherry", "main", "feature").startswith("+")
    row = worktree_status(world)["feature"]
    assert row["verdict"] == "remove"
    assert row["why"] == "every changed path matches the head branch"


def test_a_never_pushed_unmerged_worktree_is_kept(world):
    """It shares history with the head branch and has work the head lacks.
    Whether it was ever pushed is not part of the proof."""
    wt = world.worktree("feature")
    world.commit("feature", "work\n", at=wt)
    row = worktree_status(world)["feature"]
    assert row["verdict"] == "keep"
    assert row["why"] == "no proof that its change is in the head branch"


@pytest.mark.parametrize("shape", ["ancestry", "squash", "content", "unmerged"])
def test_a_worktree_and_a_loose_branch_share_one_proof(world, shape):
    wt = world.worktree("feature")
    if shape != "ancestry":
        world.commit("feature", "branch\n", at=wt)
    if shape == "squash":
        (world.repo / "feature").write_text("branch\n")
        world.git("add", "feature")
        world.git("commit", "--quiet", "-m", "squash of feature")
    elif shape == "content":
        reimport(world)
    world.git("branch", "loose", "feature")
    worktree = worktree_status(world)["feature"]
    branch = status(world, "loose")["verdicts"][0]
    assert branch["proof"] == ("" if shape == "unmerged" else shape)
    assert (worktree["verdict"], worktree["why"]) == (branch["verdict"], branch["why"])
    assert worktree["sha"] == branch["sha"]


def failing_git(world, subcommand: str) -> dict[str, str]:
    """An env whose git fails `subcommand` the way a broken repository does."""
    env = env_for(world)
    real = shutil.which("git", path=env["PATH"])
    bin_dir = world.root / "failing-bin"
    bin_dir.mkdir(exist_ok=True)
    exe = bin_dir / "git"
    exe.write_text(
        "#!/bin/sh\n"
        'for a in "$@"; do\n'
        f'  [ "$a" = {subcommand} ] && {{ echo "fatal: simulated" >&2; exit 129; }}\n'
        "done\n"
        f'exec {real} "$@"\n'
    )
    exe.chmod(0o755)
    env["PATH"] = f"{bin_dir}:{env['PATH']}"
    return env


def unmerged_worktree(world) -> Path:
    wt = world.worktree("feature")
    world.commit("feature", "work\n", at=wt)
    return wt


@pytest.mark.parametrize("subcommand", ["cherry", "status"])
def test_a_git_failure_in_a_worktree_is_an_error(world, subcommand):
    unmerged_worktree(world)
    env = failing_git(world, subcommand)
    p = cli(world, "prune", "--json", env=env)
    assert p.returncode == 1, p.stderr
    row = {r["branch"]: r for r in json.loads(p.stdout)["verdicts"]}["feature"]
    assert row["verdict"] == "unknown"
    assert row["why"].endswith(" exited 129: fatal: simulated"), row["why"]


def test_gwp_reports_a_git_failure_and_removes_nothing_it_could_not_judge(world):
    wt = unmerged_worktree(world)
    p = cli(
        world,
        "--yes",
        entry="gwp",
        env=failing_git(world, "cherry"),
    )
    assert p.returncode == 1, p.stderr
    assert wt.exists()


def test_gwr_refuses_a_worktree_it_could_not_judge(world):
    """--force removes an unfinished checkout, not one nothing could judge."""
    wt = unmerged_worktree(world)
    p = cli(
        world,
        "feature",
        "--yes",
        "--force",
        entry="gwr",
        env=failing_git(world, "cherry"),
    )
    assert p.returncode == 1, p.stderr
    assert "exited 129: fatal: simulated" in p.stderr
    assert wt.exists()


def test_a_git_failure_in_a_branch_is_an_error(world):
    local_branch(world)
    env = failing_git(world, "cherry")
    p = cli(world, "branch", "status", "--json", env=env)
    assert p.returncode == 1, p.stderr
    row = json.loads(p.stdout)["verdicts"][0]
    assert (row["verdict"], row["error"]) == ("unknown", True)
    assert row["why"].endswith(" exited 129: fatal: simulated"), row["why"]


def test_branch_status_lists_checked_out_branches_as_kept(world):
    world.worktree("occupied")
    world.git("branch", "done")
    rows = {r["branch"]: r for r in status(world)["verdicts"]}
    assert set(rows) == {"main", "occupied", "done"}
    assert rows["main"]["verdict"] == "keep"
    assert rows["occupied"]["verdict"] == "keep"
    assert rows["occupied"]["paths"]
    assert rows["done"]["verdict"] == "remove"


@pytest.mark.parametrize("entry", ["gwbs", "gwbd"])
def test_console_aliases_work(world, entry):
    world.git("branch", "done")
    args = ["done", "--json"]
    if entry == "gwbd":
        args += ["--dry-run"]
    p = cli(world, *args, entry=entry)
    assert p.returncode == 0, p.stderr
    assert json.loads(p.stdout)["verdicts"][0]["verdict"] == "remove"


def test_multiple_exact_names_do_not_select_a_prefix(world):
    for name in ("one", "two", "one-more"):
        world.git("branch", name)
    assert [r["branch"] for r in status(world, "one", "two")["verdicts"]] == [
        "one",
        "two",
    ]


def test_unknown_name_prevents_any_deletion(world):
    world.git("branch", "done")
    p = cli(
        world,
        "branch",
        "delete",
        "done",
        "missing",
        "--yes",
    )
    assert p.returncode == 2
    assert world.git("rev-parse", "refs/heads/done")


@pytest.mark.parametrize("name", ["done", "a'b", "$(id)"])
def test_delete_prints_an_executable_undo_command(world, name):
    world.git("branch", name)
    sha = world.git("rev-parse", f"refs/heads/{name}")
    p = cli(world, "branch", "delete", name, "--yes", "--json")
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
    world.git("branch", "done")
    p = cli(world, "branch", "delete", "done", *args)
    assert p.returncode == (0 if args else 3), p.stderr
    assert world.git("rev-parse", "refs/heads/done")


def test_no_arguments_requires_a_selector_even_with_yes(world):
    world.git("branch", "done")
    p = cli(world, "branch", "delete", "--yes")
    assert p.returncode == 3
    assert "terminal" in p.stderr
    assert world.git("rev-parse", "refs/heads/done")


def test_unmerged_branch_is_not_deleted(world):
    sha = local_branch(world)
    p = cli(
        world,
        "branch",
        "delete",
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

    world.git("branch", "one")
    world.git("branch", "two")
    code, out = run_on_a_terminal(world, "gwbd", answer=answer)
    assert code == (3 if answer == "9\n" else 0), out
    names = world.git(
        "for-each-ref", "--format=%(refname:short)", "refs/heads"
    ).splitlines()
    assert ("one" not in names) == (answer in ("1\ny\n", "1 2\ny\n"))
    assert ("two" not in names) == (answer == "1 2\ny\n")


def test_all_deletes_only_proven_branches(world):
    world.git("branch", "done")
    local_branch(world)
    p = cli(
        world,
        "branch",
        "delete",
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

    world.git("branch", "done")
    head = world.git("rev-parse", "main")
    row = branches.assess(["done"], head, "main")[0]
    if changed == "checkout":
        world.git("worktree", "add", str(world.root / "occupied"), "done")
    else:
        new = world.commit("new", "new\n")
        if changed == "tip":
            world.git("update-ref", "refs/heads/done", new)
            world.git("update-ref", "refs/heads/main", head)
    with pytest.raises(Refused):
        branches.delete(row, "refs/heads/main", head, "main")
    assert world.git("rev-parse", "done")


def test_completion_lists_names_without_querying_the_forge(world):
    world.git("branch", "one")
    for entry in ("gwbs", "gwbd"):
        p = cli(world, "--complete", entry=entry)
        assert p.returncode == 0, p.stderr
        assert {line.split("\t")[0] for line in p.stdout.splitlines()} == {
            "main",
            "one",
        }


def test_long_name_and_branch_help(world):
    p = cli(world, "branch", "--help", entry="main")
    assert p.returncode == 0
    assert "status" in p.stdout and "delete" in p.stdout
    p = cli(
        world,
        "branch",
        "status",
        "main",
        "--json",
        entry="main",
    )
    assert p.returncode == 0, p.stderr
    assert json.loads(p.stdout)["verdicts"][0]["verdict"] == "keep"


def test_ref_deletion_never_follows_a_changed_symbolic_ref(world):
    from worktrees import prune

    world.git("branch", "done")
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
        world.git("branch", name)
    original = prune.ref_delete

    def fail_one(ref, sha, **kwargs):
        if ref == "refs/heads/one":
            raise GitError("cannot lock ref")
        return original(ref, sha, **kwargs)

    monkeypatch.setattr(prune, "ref_delete", fail_one)
    code = C.gwbd(["one", "two", "--yes", "--json"])
    out = capsys.readouterr()
    assert code == 1
    result = json.loads(out.out)
    assert result["removed"] == ["two"]
    assert result["failed"] == [{"branch": "one", "why": "cannot lock ref"}]
    assert world.git("rev-parse", "one")


def test_successful_deletion_is_reported_to_a_person(world):
    world.git("branch", "done")
    p = cli(world, "branch", "delete", "done", "--yes")
    assert p.returncode == 0, p.stderr
    assert "deleted 1 branch: done" in p.stdout


def test_the_selector_and_the_plan_name_each_verdict(world):
    """keep and unknown are different reasons a row cannot be picked."""
    from test_cli import run_on_a_terminal

    world.git("branch", "done")
    code, out = run_on_a_terminal(world, "gwbd", answer="1\nn\n")
    assert code == 0, out
    rows = {
        ln.split()[2]: ln.split()[1]
        for ln in out.splitlines()
        if ln[:3].strip() in ("-", "1")
    }
    assert rows == {"main": "keep", "done": "remove"}
    assert "VERDICT" in out.split("deleting 1 branch:")[1]


def test_the_spinner_line_names_each_branch_and_how_far_along(world, monkeypatch):
    """--all can mean dozens of branches."""
    from worktrees import branches, progress

    for name in ("one", "two"):
        world.git("branch", name)
    said: list[str] = []
    monkeypatch.setattr(progress, "say", said.append)
    head = world.git("rev-parse", "HEAD")
    branches.assess([], head, "main")
    assert "one: is it merged into main? (2 of 3)" in said
    assert "two: is it merged into main? (3 of 3)" in said
