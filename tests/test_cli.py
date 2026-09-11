"""Drive the entry points. Exec is the only way env, cwd, both streams and the
exit code stay per-test."""

from __future__ import annotations

import json
import os
import pty
import subprocess
import sys
from pathlib import Path

import pytest

from worktrees import prune

SRC = str(Path(__file__).resolve().parents[1] / "src")


def _env(world) -> dict[str, str]:
    from conftest import ENV

    return {
        "PATH": "/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin",
        "PYTHONPATH": SRC,
        "HOME": str(world.root),
        "GIT_CONFIG_GLOBAL": str(world.root / "gitconfig"),
        "GIT_CONFIG_NOSYSTEM": "1",
        **ENV,
    }


def run(world, entry: str, *args: str, at: Path | None = None):
    return subprocess.run(
        [
            sys.executable,
            "-c",
            f"from worktrees.cli import {entry}; raise SystemExit({entry}())",
            *args,
        ],
        cwd=str(at or world.repo),
        capture_output=True,
        text=True,
        env=_env(world),
    )


def run_on_a_terminal(world, entry: str, *args: str, answer: str) -> tuple[int, str]:
    """The same, with a real tty on stdin, so the prompt is reachable."""
    parent, child = pty.openpty()
    proc = subprocess.Popen(
        [
            sys.executable,
            "-c",
            f"from worktrees.cli import {entry}; raise SystemExit({entry}())",
            *args,
        ],
        cwd=str(world.repo),
        stdin=child,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        env=_env(world),
    )
    os.close(child)
    os.write(parent, answer.encode())
    out = proc.stdout.read() if proc.stdout else ""
    proc.wait()
    os.close(parent)
    return proc.returncode, out


# --------------------------------------------------------------------------
# gws reports and never removes
# --------------------------------------------------------------------------


def test_status_has_no_flag_that_removes(world) -> None:
    p = run(world, "gws", "--help")
    assert p.returncode == 0
    for flag in ("--yes", "-y"):
        assert flag not in p.stdout, f"gws offers {flag}"
    p = run(world, "gws", "--yes")
    assert p.returncode == 2
    assert "unrecognized arguments" in p.stderr


def test_status_leaves_a_removable_worktree_alone(world) -> None:
    wt = world.worktree("done")
    p = run(world, "gws", "--no-fetch")
    assert p.returncode == 0, p.stderr
    assert "remove" in p.stdout
    assert "1 removable, 0 kept, 0 unclear" in p.stdout
    assert "gwp removes the 1 marked removable" in p.stderr
    assert wt.exists()
    assert world.git("rev-parse", "--verify", "refs/heads/done")


def test_status_says_when_there_is_nothing(world) -> None:
    p = run(world, "gws", "--no-fetch")
    assert p.returncode == 0, p.stderr
    assert "no worktrees besides the main checkout" in p.stdout


def test_status_json_is_data_on_stdout(world) -> None:
    world.worktree("done")
    p = run(world, "gws", "--no-fetch", "--json")
    assert p.returncode == 0, p.stderr
    payload = json.loads(p.stdout)
    assert payload["head"] == "refs/heads/main"
    assert payload["verdicts"][0]["verdict"] == "remove"
    assert payload["stale"] == []


def test_status_names_a_stale_record_rather_than_clearing_it(world) -> None:
    """`git worktree prune` mutates, so a read-only command may not call it."""
    wt = world.worktree("gone")
    import shutil

    shutil.rmtree(wt)
    p = run(world, "gws", "--no-fetch")
    assert p.returncode == 0, p.stderr
    assert "1 stale record(s)" in p.stderr
    # Still listed by git, because nothing pruned it.
    assert "gone" in world.git("worktree", "list", "--porcelain")


# --------------------------------------------------------------------------
# gwp removes exactly what gws marks removable
# --------------------------------------------------------------------------


@pytest.mark.parametrize("extra", [(), ("--delete-ignored",)])
def test_prune_removes_exactly_what_status_marks(world, extra: tuple[str, ...]) -> None:
    """The invariant. Same flags in, same set out."""
    (world.repo / ".gitignore").write_text(".env\n")
    world.git("add", "--", ".gitignore")
    world.git("commit", "--quiet", "-m", "ignore")

    world.worktree("finished")
    holds = world.worktree("holds-env")
    (holds / ".env").write_text("SECRET=1\n")
    busy = world.worktree("busy")
    (busy / "scratch.txt").write_text("wip\n")
    mystery = world.worktree("mystery")
    world.commit("m.txt", "m\n", at=mystery)

    p = run(world, "gws", "--no-fetch", "--json", *extra)
    marked = sorted(
        v["branch"]
        for v in json.loads(p.stdout)["verdicts"]
        if v["verdict"] == "remove"
    )

    p = run(world, "gwp", "--no-fetch", "--yes", "--json", *extra)
    assert p.returncode == 0, p.stderr
    removed = sorted(json.loads(p.stdout)["removed"])

    assert removed == marked
    assert "finished" in removed
    assert ("holds-env" in removed) == bool(extra)
    for kept in ("busy", "mystery"):
        assert kept not in removed


def test_prune_asks_before_removing(world) -> None:
    wt = world.worktree("done")
    code, out = run_on_a_terminal(world, "gwp", "--no-fetch", answer="y\n")
    assert code == 0, out
    assert "remove 1 worktree(s)? [y/N]" in out
    assert "restore with: git branch done " in out
    assert not wt.exists()


def test_answering_no_removes_nothing(world) -> None:
    wt = world.worktree("done")
    code, out = run_on_a_terminal(world, "gwp", "--no-fetch", answer="n\n")
    assert code == 0, out
    assert "nothing removed" in out
    assert wt.exists()
    assert world.git("rev-parse", "--verify", "refs/heads/done")


def test_bare_enter_removes_nothing(world) -> None:
    """[y/N]: the capital is the default, and it has to be true."""
    wt = world.worktree("done")
    code, out = run_on_a_terminal(world, "gwp", "--no-fetch", answer="\n")
    assert code == 0, out
    assert "nothing removed" in out
    assert wt.exists()


def test_without_a_terminal_it_refuses_rather_than_blocking(world) -> None:
    """An agent or a pipe reaches this and would otherwise hang forever."""
    wt = world.worktree("done")
    p = run(world, "gwp", "--no-fetch")
    assert p.returncode == 2
    assert "not a terminal" in p.stderr
    assert "pass --yes" in p.stderr
    assert wt.exists()


@pytest.mark.parametrize(
    ("answer", "expected"),
    [("y", True), ("Y", True), ("yes", True), ("n", False), ("", False), ("q", False)],
)
def test_confirm_takes_yes_and_nothing_else(answer: str, expected: bool) -> None:
    assert prune.confirm(1, ask=lambda _: answer) is expected


def test_prune_deletes_the_branch_and_the_empty_parents(world) -> None:
    wt = world.worktree("done")
    p = run(world, "gwp", "--no-fetch", "--yes")
    assert p.returncode == 0, p.stderr
    assert not wt.exists()
    assert "done" not in world.git("branch", "--format=%(refname:short)").split()
    assert not (world.parent / ".worktrees" / "done").exists()


def test_a_squash_merged_branch_survives_branch_d(world) -> None:
    """The checkout goes; the branch stays, because -d refuses it."""
    wt = world.worktree("squashed")
    world.commit("f.txt", "one\n", at=wt)
    (world.repo / "f.txt").write_text("one\n")
    world.git("add", "--", "f.txt")
    world.git("commit", "--quiet", "-m", "squash")

    p = run(world, "gwp", "--no-fetch", "--yes")
    assert p.returncode == 0, p.stderr
    assert not wt.exists()
    assert world.git("rev-parse", "--verify", "refs/heads/squashed")
    assert "branch squashed kept" in p.stderr


def test_prune_clears_a_stale_record(world) -> None:
    wt = world.worktree("gone")
    import shutil

    shutil.rmtree(wt)
    p = run(world, "gwp", "--no-fetch", "--yes")
    assert p.returncode == 0, p.stderr
    assert "gone" not in world.git("worktree", "list", "--porcelain")


def test_prune_says_so_when_there_is_nothing(world) -> None:
    p = run(world, "gwp", "--no-fetch", "--yes")
    assert p.returncode == 0, p.stderr
    assert "nothing to remove; gws says why" in p.stdout


def test_dry_run_is_refused_by_name(world) -> None:
    for entry in ("gws", "gwp"):
        p = run(world, entry, "--dry-run")
        assert p.returncode == 2
        assert "there is no --dry-run" in p.stderr


def test_explain_lists_every_command_and_runs_none(world) -> None:
    p = run(world, "gws", "--explain")
    assert p.returncode == 0, p.stderr
    assert "git worktree list --porcelain -z" in p.stdout
    assert "git branch -d -- $branch" in p.stdout
    assert "git branch -D" not in p.stdout


def test_verbose_prints_the_argv(world) -> None:
    world.worktree("done")
    p = run(world, "gws", "--no-fetch", "--verbose")
    assert p.returncode == 0, p.stderr
    # The canary: a run that never reached git would print nothing here, and an
    # empty stream is indistinguishable from a refusal that worked.
    assert "+ git worktree list --porcelain -z" in p.stderr
    assert "--no-optional-locks status --porcelain" in p.stderr
    assert "--is-ancestor" in p.stderr


def test_status_never_issues_a_mutating_command(world) -> None:
    """--no-fetch, so the one mutation a report is allowed does not run."""
    world.worktree("done")
    p = run(world, "gws", "--no-fetch", "--verbose")
    issued = [ln for ln in p.stderr.splitlines() if ln.startswith("+ git")]
    assert issued
    for line in issued:
        for forbidden in ("worktree prune", "worktree remove", "branch -d", "checkout"):
            assert forbidden not in line, line


def test_you_cannot_prune_the_worktree_you_stand_in(world) -> None:
    wt = world.worktree("here")
    p = run(world, "gws", "--no-fetch", at=wt)
    assert p.returncode == 0, p.stderr
    assert "you are standing in it" in p.stdout
    assert wt.exists()


# --------------------------------------------------------------------------
# the CLI name
# --------------------------------------------------------------------------


def test_the_cli_name_takes_a_subcommand(world) -> None:
    world.worktree("done")
    p = run(world, "main", "status", "--no-fetch", "--json")
    assert p.returncode == 0, p.stderr
    assert json.loads(p.stdout)["verdicts"][0]["branch"] == "done"


def test_the_cli_name_with_no_argument_prints_help(world) -> None:
    p = run(world, "main")
    assert p.returncode == 0
    assert "usage: worktrees" in p.stdout
    for name in ("status", "prune", "new-branch"):
        assert name in p.stdout


def test_a_bare_flag_means_status(world) -> None:
    p = run(world, "main", "--explain")
    assert p.returncode == 0, p.stderr
    assert "git worktree list --porcelain -z" in p.stdout
