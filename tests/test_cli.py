"""Drive the entry point. Exec is the only way env, cwd, both streams and the
exit code stay per-test."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

SRC = str(Path(__file__).resolve().parents[1] / "src")


def gwp(world, *args: str, at: Path | None = None) -> subprocess.CompletedProcess[str]:
    from conftest import ENV

    return subprocess.run(
        [sys.executable, "-c", "from worktrees.cli import gwp; raise SystemExit(gwp())", *args],
        cwd=str(at or world.repo),
        capture_output=True,
        text=True,
        env={
            "PATH": "/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin",
            "PYTHONPATH": SRC,
            "HOME": str(world.root),
            "GIT_CONFIG_GLOBAL": str(world.root / "gitconfig"),
            "GIT_CONFIG_NOSYSTEM": "1",
            **ENV,
        },
    )


def test_a_clean_repository_has_nothing_to_prune(world) -> None:
    p = gwp(world, "--no-fetch")
    assert p.returncode == 0, p.stderr
    assert "nothing to prune" in p.stdout


def test_it_removes_nothing_without_yes(world) -> None:
    wt = world.worktree("done")
    p = gwp(world, "--no-fetch")
    assert p.returncode == 0, p.stderr
    assert "go" in p.stdout
    assert "pass --yes" in p.stderr
    assert wt.exists(), "the worktree was removed without --yes"


def test_yes_removes_the_checkout_and_the_branch(world) -> None:
    wt = world.worktree("done")
    p = gwp(world, "--no-fetch", "--yes")
    assert p.returncode == 0, p.stderr + p.stdout
    assert not wt.exists()
    assert "done" not in world.git("branch", "--format=%(refname:short)").split()
    # The restore line is printed before anything goes.
    assert "restore with: git branch done " in p.stdout
    # And the empty directories it left behind are gone, up to the root.
    assert not (world.parent / ".worktrees" / "done").exists()


def test_yes_does_not_touch_unknown(world) -> None:
    wt = world.worktree("mystery")
    world.commit("m.txt", "m\n", at=wt)
    p = gwp(world, "--no-fetch", "--yes")
    assert p.returncode == 0, p.stderr
    assert "unknown" in p.stdout
    assert "unclear, and --yes does not touch these: mystery" in p.stderr
    assert wt.exists(), "an unclear worktree was removed"
    assert world.git("rev-parse", "--verify", "refs/heads/mystery")


def test_a_squash_merged_branch_survives_branch_d(world) -> None:
    """The checkout goes; the branch stays, because -d refuses it."""
    wt = world.worktree("squashed")
    world.commit("f.txt", "one\n", at=wt)
    (world.repo / "f.txt").write_text("one\n")
    world.git("add", "--", "f.txt")
    world.git("commit", "--quiet", "-m", "squash")

    p = gwp(world, "--no-fetch", "--yes")
    assert p.returncode == 0, p.stderr + p.stdout
    assert not wt.exists()
    assert world.git("rev-parse", "--verify", "refs/heads/squashed")
    assert "branch squashed kept" in p.stdout


def test_dry_run_is_refused_by_name(world) -> None:
    p = gwp(world, "--dry-run")
    assert p.returncode == 2
    assert "there is no --dry-run" in p.stderr


def test_json_is_data_on_stdout(world) -> None:
    world.worktree("done")
    p = gwp(world, "--no-fetch", "--json")
    assert p.returncode == 0, p.stderr
    payload = json.loads(p.stdout)
    assert payload["head"] == "refs/heads/main"
    assert [v["verdict"] for v in payload["verdicts"]] == ["go"]
    assert payload["verdicts"][0]["branch"] == "done"


def test_explain_lists_every_command_and_runs_none(world) -> None:
    p = gwp(world, "--explain")
    assert p.returncode == 0, p.stderr
    assert "git worktree list --porcelain -z" in p.stdout
    assert "git branch -d -- <branch>" in p.stdout
    assert "git branch -D" not in p.stdout


def test_verbose_prints_the_argv(world) -> None:
    world.worktree("done")
    p = gwp(world, "--no-fetch", "--verbose")
    assert p.returncode == 0, p.stderr
    # The canary: a run that never reached git would print nothing here, and an
    # empty stream is indistinguishable from a refusal that worked.
    assert "+ git worktree list --porcelain -z" in p.stderr
    assert "--no-optional-locks status --porcelain" in p.stderr
    assert "--is-ancestor" in p.stderr


def test_no_fetch_says_the_refs_may_be_stale(world) -> None:
    p = gwp(world, "--no-fetch")
    assert "refs may be stale" in p.stderr


def test_branch_narrows_to_one(world) -> None:
    world.worktree("one")
    world.worktree("two")
    p = gwp(world, "--no-fetch", "--branch", "two", "--json")
    payload = json.loads(p.stdout)
    assert [v["branch"] for v in payload["verdicts"]] == ["two"]


def test_you_cannot_prune_the_worktree_you_stand_in(world) -> None:
    wt = world.worktree("here")
    p = gwp(world, "--no-fetch", "--yes", at=wt)
    assert p.returncode == 0, p.stderr
    assert "you are standing in it" in p.stdout
    assert wt.exists()


def worktrees(world, *args: str) -> subprocess.CompletedProcess[str]:
    from conftest import ENV

    return subprocess.run(
        [
            sys.executable,
            "-c",
            "from worktrees.cli import main; raise SystemExit(main())",
            *args,
        ],
        cwd=str(world.repo),
        capture_output=True,
        text=True,
        env={
            "PATH": "/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin",
            "PYTHONPATH": SRC,
            "HOME": str(world.root),
            "GIT_CONFIG_GLOBAL": str(world.root / "gitconfig"),
            "GIT_CONFIG_NOSYSTEM": "1",
            **ENV,
        },
    )


def test_the_cli_name_takes_a_subcommand(world) -> None:
    world.worktree("done")
    p = worktrees(world, "prune", "--no-fetch", "--json")
    assert p.returncode == 0, p.stderr
    assert json.loads(p.stdout)["verdicts"][0]["branch"] == "done"


def test_the_cli_name_with_no_argument_prints_help(world) -> None:
    p = worktrees(world)
    assert p.returncode == 0
    assert "usage: worktrees" in p.stdout


def test_a_bare_flag_means_prune(world) -> None:
    p = worktrees(world, "--explain")
    assert p.returncode == 0, p.stderr
    assert "git worktree list --porcelain -z" in p.stdout
