"""-q and -v, per command, as the matrix in the design sets them."""

from __future__ import annotations

import json
import re

import pytest
from test_cli import run
from test_sync import fake, forge_remote, synced

# Each command, as typed, and whether it takes -q and -v.
MATRIX = {
    ("gw", "sync"): (True, True),
    ("gwp",): (True, True),
    ("gwr",): (True, True),
    ("gwbd",): (True, True),
    ("gwa",): (True, True),
    ("gwbc",): (True, True),
    ("gwrot",): (True, True),
    ("gwbs",): (True, False),
    ("gw", "list"): (False, False),
}


def _help(world, argv: tuple[str, ...]) -> str:
    p = run(world, *argv, "-h")
    assert p.returncode == 0, p.stderr
    return p.stdout + p.stderr


@pytest.mark.parametrize("argv", sorted(MATRIX))
def test_each_command_takes_the_flags_the_matrix_gives_it(world, argv) -> None:
    quiet, verbose = MATRIX[argv]
    text = _help(world, argv)
    assert bool(re.search(r"-q, --quiet", text)) is quiet, text
    assert bool(re.search(r"-v, --verbose", text)) is verbose, text


@pytest.mark.parametrize(
    "argv",
    [
        ("gw", "list", "-q"),
        ("gw", "list", "--verbose"),
        ("gw", "-q"),
        ("gw", "-v"),
        ("gw", "main", "--quiet"),
        ("gwbs", "-v"),
        ("gwbs", "--verbose"),
    ],
)
def test_a_flag_outside_the_matrix_is_a_usage_error(world, argv) -> None:
    p = run(world, *argv)
    assert p.returncode == 2, p.stderr
    assert "unrecognized arguments" in p.stderr


@pytest.mark.parametrize(
    "argv",
    [
        ("gwp", "--yes"),
        ("gwr", "done", "--yes"),
        ("gwbd", "--all", "--yes"),
        ("gw", "sync"),
        ("gwa", "new"),
        ("gwbc", "new"),
    ],
)
@pytest.mark.parametrize("flags", [("-q", "-v"), ("--verbose", "--quiet")])
def test_quiet_and_verbose_together_change_nothing(world, argv, flags) -> None:
    wt = world.finished("done")
    world.finished_branch("loose")
    before = world.git("for-each-ref", "--format=%(refname) %(objectname)")
    p = run(world, *argv, *flags)
    assert p.returncode == 2, p.stderr
    assert "not allowed with argument" in p.stderr
    assert "Head:" not in p.stderr
    assert wt.exists()
    assert not (world.parent / ".worktrees" / "new").exists()
    assert world.git("for-each-ref", "--format=%(refname) %(objectname)") == before


def test_verbose_traces_git_and_leaves_json_whole(world) -> None:
    world.finished("done")
    p = run(world, "gwp", "--json", "-v")
    assert p.returncode == 0, p.stderr
    assert json.loads(p.stdout)["verdicts"]
    assert "git " in p.stderr and "status --porcelain" in p.stderr


def test_verbose_traces_the_forge_in_gw_sync(world) -> None:
    forge_remote(world)
    world.git("branch", "feature")
    p = synced(world, fake(world, "github", {}), "-v")
    assert p.returncode == 0, p.stderr
    assert re.search(r"^\+ gh pr list", p.stderr, re.M), p.stderr
    assert "git " in p.stderr and "fetch" in p.stderr


def test_quiet_keeps_results_and_errors(world) -> None:
    wt = world.finished("done")
    p = run(world, "gwr", "done", "--yes", "-q")
    assert p.returncode == 0, p.stderr
    assert "restore with: git branch done " in p.stderr, "the disclosure stays"
    assert not wt.exists()
    p = run(world, "gwr", "gone", "--yes", "-q")
    assert p.returncode == 1
    assert "no worktree matches" in p.stderr


def test_quiet_drops_gwps_summary_notes(world) -> None:
    p = run(world, "gwp", "-q")
    assert p.returncode == 0, p.stderr
    assert "nothing to remove" not in p.stderr
    world.finished("done")
    p = run(world, "gwp", "-q")
    assert p.returncode == 0, p.stderr
    assert "would be removed" not in p.stderr
    p = run(world, "gwp")
    assert "1 would be removed; gwp --yes removes them" in p.stderr


def test_quiet_keeps_the_answer_to_a_declined_prompt(world) -> None:
    from test_cli import run_on_a_terminal

    wt = world.finished("done")
    code, out = run_on_a_terminal(world, "gwp", "-q", answer="n\n")
    assert code == 0, out
    assert "nothing removed" in out
    assert wt.exists()
