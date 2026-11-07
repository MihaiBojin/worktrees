"""A branch's stashes hold it back, and the disclosure says how to restore each."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from conftest import env_for
from test_cli import run

PROOF = "its tip is an ancestor of the head branch"


def stash(world, at: Path, message: str = "") -> str:
    """Stash a change to README in `at`, with `-m message` when given."""
    at.joinpath("README").write_text(f"{message or 'wip'}\n")
    world.git("stash", "push", "--quiet", *(["-m", message] if message else []), at=at)
    return world.git("rev-parse", "stash@{0}")


def rows(world, entry: str = "gwp") -> dict[str, dict]:
    p = run(world, entry, "--json")
    assert p.returncode == 0, p.stderr
    return {v["branch"] or "(detached)": v for v in json.loads(p.stdout)["verdicts"]}


def test_a_stash_on_the_branch_clears_eligible(world) -> None:
    wt = world.finished("wip")
    named = stash(world, wt, "half-finished")
    plain = stash(world, wt)
    row = rows(world)["wip"]
    assert (row["verdict"], row["eligible"]) == ("remove", False)
    assert (
        row["why"]
        == f"{PROOF}; 2 stash(es) belong to its branch, and need --delete stashes"
    )
    assert row["stashes"] == [plain, named]
    assert world.git("stash", "list", "--format=%gs").splitlines() == [
        "WIP on wip: " + world.git("log", "-1", "--format=%h %s", "wip"),
        "On wip: half-finished",
    ]


def test_a_stash_named_for_another_branch_does_not_belong(world) -> None:
    world.finished("wip")
    stash(world, world.repo, "on main")
    row = rows(world)["wip"]
    assert (row["eligible"], row["stashes"]) == (True, [])


def test_a_stash_from_a_commit_the_branch_left_does_not_belong(world) -> None:
    wt = world.finished("wip")
    world.commit("later", at=wt)
    stash(world, wt, "on the later commit")
    world.git("reset", "--quiet", "--hard", "HEAD~1", at=wt)
    row = rows(world)["wip"]
    assert (row["eligible"], row["stashes"]) == (True, [])


def test_a_stash_made_on_a_detached_head_belongs_to_no_branch(world) -> None:
    wt = world.parent / ".worktrees" / "loose" / world.repo.name
    world.git("worktree", "add", "--quiet", "--detach", str(wt), "main")
    stash(world, wt, "detached")
    row = rows(world)["(detached)"]
    assert (row["eligible"], row["stashes"]) == (True, [])


def test_gwp_yes_keeps_a_branch_with_stashes(world) -> None:
    wt = world.finished("wip")
    stash(world, wt, "half")
    p = run(world, "gwp", "--yes", "--json")
    assert p.returncode == 0, p.stderr
    assert json.loads(p.stdout)["removed"] == []
    assert wt.exists()


def test_a_loose_branch_with_stashes_is_kept_by_gwbd(world) -> None:
    wt = world.finished("wip")
    sha = stash(world, wt, "half")
    world.git("worktree", "remove", str(wt))
    row = rows(world, "gwbs")["wip"]
    assert (row["verdict"], row["eligible"]) == ("remove", False)
    assert row["stashes"] == [sha]
    p = run(world, "gwbd", "wip", "--yes")
    assert p.returncode == 3
    assert "wip kept: " in p.stderr and "1 stash(es) belong to its branch" in p.stderr
    assert world.git("branch", "--list", "wip")


def test_gwr_force_keeps_the_branch_and_its_stashes_and_says_how_to_restore(
    world,
) -> None:
    wt = world.finished("wip")
    sha = stash(world, wt, "half finished")
    p = run(world, "gwr", "wip", "--yes", "--force")
    assert p.returncode == 0, p.stderr
    restore = f"git stash store -m 'On wip: half finished' {sha}"
    assert restore in p.stderr
    assert not wt.exists()
    assert world.git("branch", "--list", "wip")
    assert world.git("rev-parse", "stash@{0}") == sha

    # The line does what it says once the entry is gone.
    world.git("stash", "drop", "--quiet")
    subprocess.run(restore, shell=True, check=True, cwd=world.repo, env=env_for(world))
    assert (
        world.git("stash", "list", "--format=%H %gs") == f"{sha} On wip: half finished"
    )


def test_gwbd_refuses_a_branch_that_gained_a_stash_after_assessment(world) -> None:
    from worktrees import branches
    from worktrees.git import Refused

    world.finished_branch("done")
    head = world.git("rev-parse", "main")
    [row] = branches.assess(["done"], head, "main")
    assert row.eligible
    world.git("switch", "--quiet", "done")
    stash(world, world.repo, "late")
    world.git("switch", "--quiet", "main")
    with pytest.raises(Refused, match="stashes changed"):
        branches.delete(row, "refs/heads/main", head, "main")
    assert world.git("branch", "--list", "done")


# --------------------------------------------------------------------------
# --delete stashes drops them, after the branch and by sha
# --------------------------------------------------------------------------


def stash_shas(world) -> list[str]:
    return world.git("stash", "list", "--format=%H").splitlines()


@pytest.mark.parametrize("value", ["stashes", "all"])
def test_gwp_drops_the_branchs_stashes_after_its_branch(world, value) -> None:
    wt = world.finished("wip")
    sha = stash(world, wt, "half")
    other = stash(world, world.repo, "on main")
    p = run(world, "gwp", "--yes", "--delete", value)
    assert p.returncode == 0, p.stderr
    assert "dropping stash On wip: half, restore with: git stash store" in p.stderr
    assert "deleted branch wip" in p.stderr
    assert "dropped stash On wip: half" in p.stderr
    assert not wt.exists()
    assert stash_shas(world) == [other]
    assert sha not in stash_shas(world)


def test_gwr_drops_them_with_permission(world) -> None:
    wt = world.finished("wip")
    stash(world, wt, "half")
    p = run(world, "gwr", "wip", "--yes", "--delete", "stashes")
    assert p.returncode == 0, p.stderr
    assert stash_shas(world) == []


@pytest.mark.parametrize("value", ["stashes", "all"])
def test_gwbd_drops_them_with_permission(world, value) -> None:
    wt = world.finished("wip")
    sha = stash(world, wt, "half")
    world.git("worktree", "remove", str(wt))
    p = run(world, "gwbd", "wip", "--yes", "--delete", value)
    assert p.returncode == 0, p.stderr
    assert f"git stash store -m 'On wip: half' {sha}" in p.stderr
    assert world.git("branch", "--list", "wip") == ""
    assert stash_shas(world) == []


def test_a_failed_ref_deletion_keeps_the_stashes(world, monkeypatch, capsys) -> None:
    from worktrees import cli, prune
    from worktrees.git import GitError

    wt = world.finished("wip")
    sha = stash(world, wt, "half")

    def refuse(*args, **kwargs):
        raise GitError("cannot lock ref")

    monkeypatch.setattr(prune, "branch_delete", refuse)
    assert cli.gwp(["--yes", "--delete", "stashes"]) == 1
    assert "branch wip not deleted" in capsys.readouterr().err
    assert stash_shas(world) == [sha]


def test_the_highest_index_goes_first_and_others_stay(world) -> None:
    from worktrees.git import options

    wt = world.finished("wip")
    older = stash(world, wt, "one")
    other = stash(world, world.repo, "on main")
    newer = stash(world, wt, "two")
    assert stash_shas(world) == [newer, other, older]
    from worktrees import cli

    assert cli.gwp(["--yes", "--delete", "stashes"]) == 0
    drops = [e[-1] for e in options.log if "drop" in e]
    assert drops == ["stash@{2}", "stash@{0}"]
    assert stash_shas(world) == [other]


def test_a_stash_already_gone_is_skipped(world, capsys) -> None:
    from worktrees import prune
    from worktrees import repo as R

    sha = stash(world, world.repo, "gone")
    world.git("stash", "drop", "--quiet")
    said: list[str] = []
    gone = R.Stash(sha, "On main: gone", world.git("rev-parse", "HEAD"))
    assert prune.drop_stashes([gone], said.append)
    assert said == ["stash On main: gone is already gone"]


def _racing_git(world, race: str) -> dict[str, str]:
    """A git that runs `race` against the stash list once, just before the
    first real `stash drop`, the way another worktree's process could."""
    real = shutil.which("git")
    bin_dir = world.root / "racing-bin"
    bin_dir.mkdir()
    marker = world.root / "raced"
    script = bin_dir / "git"
    script.write_text(
        "#!/bin/sh\n"
        'case " $* " in *" stash drop "*)\n'
        f'  if [ ! -e "{marker}" ]; then\n'
        f'    touch "{marker}"\n'
        f'    "{real}" -C "{world.repo}" {race}\n'
        "  fi;;\n"
        "esac\n"
        f'exec "{real}" "$@"\n'
    )
    script.chmod(0o755)
    env = env_for(world)
    return {**env, "PATH": f"{bin_dir}{os.pathsep}{env['PATH']}"}


@pytest.mark.parametrize("at", [0, 1], ids=["newest", "below-another"])
def test_a_stash_list_that_changes_mid_drop_loses_nothing(world, at) -> None:
    intruder = stash(world, world.repo, "intruder")
    world.git("stash", "drop", "--quiet")
    wt = world.finished("wip")
    if at:
        mine = stash(world, wt, "mine")
        above = stash(world, world.repo, "above")
    else:
        above = stash(world, world.repo, "above")
        mine = stash(world, wt, "mine")
    p = subprocess.run(
        [
            sys.executable,
            "-c",
            "from worktrees.cli import gwp; raise SystemExit(gwp())",
            "--yes",
            "--delete",
            "stashes",
        ],
        cwd=str(world.repo),
        capture_output=True,
        text=True,
        env=_racing_git(world, f'stash store -m "On other: intruder" {intruder}'),
    )
    assert p.returncode == 1, p.stderr
    assert "stash On wip: mine not dropped: the stash list changed" in p.stderr
    # Every entry is still there: the one dropped by mistake came back.
    assert sorted(stash_shas(world)) == sorted([intruder, mine, above])
    # A mistaken drop of an entry the first listing held takes its subject
    # from there; one it did not hold takes its commit's subject.
    intruded = "On main: intruder" if at == 0 else "On other: intruder"
    subjects = world.git("stash", "list", "--format=%gs").splitlines()
    assert sorted(subjects) == sorted([intruded, "On wip: mine", "On main: above"])


def test_a_drop_git_refuses_is_reported_and_the_sweep_goes_on(world) -> None:
    """The target leaves the list between the listing and the drop, so the
    index read a moment ago names nothing and git refuses the drop."""
    wt = world.finished("wip")
    stash(world, wt, "mine")
    two = world.finished("two")
    p = subprocess.run(
        [
            sys.executable,
            "-c",
            "from worktrees.cli import gwp; raise SystemExit(gwp())",
            "--yes",
            "--delete",
            "stashes",
        ],
        cwd=str(world.repo),
        capture_output=True,
        text=True,
        env=_racing_git(world, "stash drop --quiet"),
    )
    assert p.returncode == 1, p.stderr
    assert "stash On wip: mine not dropped: " in p.stderr
    assert "Traceback" not in p.stderr
    assert not wt.exists() and not two.exists()
    assert "removed 2 worktree(s): wip, two" in p.stdout


def test_a_put_back_git_refuses_prints_the_restore_command(world, monkeypatch) -> None:
    from worktrees import prune
    from worktrees import repo as R
    from worktrees.git import GitError, Run

    mine = stash(world, world.repo, "mine")
    other = stash(world, world.repo, "other")

    def drop(selector, **kwargs):
        # The entry above went in its place.
        world.git("stash", "drop", "--quiet", "stash@{0}")
        return Run(0, f"Dropped stash@{{0}} ({other})\n", "")

    def refuse(subject, sha, **kwargs):
        raise GitError("git stash store exited 1:\nerror: cannot lock ref")

    monkeypatch.setattr(prune, "stash_drop", drop)
    monkeypatch.setattr(prune, "stash_store", refuse)
    said: list[str] = []
    target = R.Stash(mine, "On main: mine", world.git("rev-parse", "HEAD"))
    assert not prune.drop_stashes([target], said.append)
    assert said[-1].endswith(
        f"restore it with: git stash store -m 'On main: other' {other}"
    )
    assert (
        "could not be put back: git stash store exited 1: error: cannot lock ref"
        in (said[-1])
    )
