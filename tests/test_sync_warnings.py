"""The line a head-reading command prints when the last `gw sync` is
missing or old, and the question a never-synced deletion asks."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest
from test_cli import run, run_on_a_terminal
from test_sync import NAME, bare, common, sync

from worktrees import sync as S
from worktrees.sync import NEVER

AT = datetime(2026, 11, 6, 12, 0, 0, tzinfo=UTC)


def at(seconds: float) -> str:
    return (AT - timedelta(seconds=seconds)).strftime("%Y-%m-%dT%H:%M:%SZ")


# --------------------------------------------------------------------------
# the age
# --------------------------------------------------------------------------


def test_ten_minutes_is_fresh_and_a_second_more_is_stale() -> None:
    assert S.stale({"refs": at(600), "forge": None}, AT) == ""
    assert (
        S.stale({"refs": at(601), "forge": None}, AT)
        == "Last synced 10 minutes ago. Run: gw sync"
    )


@pytest.mark.parametrize(
    ("seconds", "said"),
    [
        (59 * 60, "59 minutes"),
        (60 * 60, "1 hour"),
        (23 * 3600 + 59 * 60, "23 hours"),
        (24 * 3600, "1 day"),
        (3 * 24 * 3600, "3 days"),
    ],
)
def test_the_age_is_in_minutes_hours_or_days(seconds, said) -> None:
    assert S.stale({"refs": at(seconds), "forge": None}, AT) == (
        f"Last synced {said} ago. Run: gw sync"
    )


def test_the_older_of_refs_and_forge_decides() -> None:
    assert S.stale({"refs": at(0), "forge": at(7200)}, AT).startswith(
        "Last synced 2 hours"
    )
    assert S.stale({"refs": at(7200), "forge": at(0)}, AT).startswith(
        "Last synced 2 hours"
    )
    assert S.stale({"refs": at(0), "forge": None}, AT) == ""


def test_a_time_that_does_not_parse_says_nothing() -> None:
    assert S.stale({"refs": "x", "forge": None}, AT) == ""


# --------------------------------------------------------------------------
# never synced
# --------------------------------------------------------------------------

HEADED = [
    ("gwp", "--yes"),
    ("gwr", "nothing-here"),
    ("gwbs",),
    ("gwbd", "--dry-run"),
    ("gwa", "spike"),
    ("gwbc", "spike"),
    ("gwrot",),
]


@pytest.mark.parametrize("argv", HEADED, ids=lambda a: a[0])
def test_every_head_reading_command_says_it_was_never_synced(world, argv) -> None:
    bare(world)
    if argv[0] == "gwrot":
        world.git("switch", "--quiet", "-c", "spike-1")
    p = run(world, *argv)
    lines = p.stderr.splitlines()
    assert NEVER in lines, p.stderr
    assert NEVER not in p.stdout, p.stdout
    assert lines.index(NEVER) < next(
        i for i, line in enumerate(lines) if line.startswith("Head: ")
    ), p.stderr


@pytest.mark.parametrize("argv", [("gwbs",), ("gwbc", "spike")], ids=lambda a: a[0])
def test_the_never_synced_line_comes_before_an_unresolved_head(world, argv) -> None:
    bare(world)
    world.git("symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/gone")
    p = run(world, *argv)
    assert p.returncode == 1, p.stderr
    lines = p.stderr.splitlines()
    assert lines[0] == NEVER, p.stderr
    assert "origin/gone does not resolve" in p.stderr
    assert not any(line.startswith("Head: ") for line in lines), p.stderr


def test_quiet_keeps_the_never_synced_line(world) -> None:
    bare(world)
    p = run(world, "gwbs", "-q")
    assert p.returncode == 0, p.stderr
    assert NEVER in p.stderr.splitlines()


def test_a_record_with_no_refs_time_is_never_synced(world) -> None:
    bare(world)
    assert sync(world).returncode == 0
    path = common(world) / NAME
    path.write_text(json.dumps({**json.loads(path.read_text()), "refs": None}))
    assert NEVER in run(world, "gwbs").stderr.splitlines()


@pytest.mark.parametrize(
    "argv", [("gwp", "--json"), ("gwbs", "--json"), ("gw", "list")]
)
def test_json_and_gw_list_print_no_sync_line(world, argv) -> None:
    bare(world)
    p = run(world, *argv)
    assert p.returncode == 0, p.stderr
    assert "synced" not in p.stderr.lower(), p.stderr


def test_gw_sync_prints_no_sync_line(world) -> None:
    world.git("remote", "add", "origin", str(world.root / "missing.git"))
    p = sync(world)
    assert p.returncode != 0
    assert NEVER not in p.stderr


def test_no_remote_prints_no_sync_line(world) -> None:
    p = run(world, "gwbs")
    assert p.returncode == 0, p.stderr
    assert "synced" not in p.stderr.lower(), p.stderr


def test_several_remotes_without_a_default_print_no_sync_line(world) -> None:
    bare(world)
    bare(world, "upstream")
    p = run(world, "gwbs")
    assert "synced" not in p.stderr.lower(), p.stderr


# --------------------------------------------------------------------------
# stale
# --------------------------------------------------------------------------


def synced_ago(world, seconds: int) -> None:
    """A real sync, then its times moved back."""
    bare(world)
    assert sync(world).returncode == 0
    path = common(world) / NAME
    record = json.loads(path.read_text())
    when = (datetime.now(UTC) - timedelta(seconds=seconds)).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )
    path.write_text(json.dumps({**record, "refs": when}))


def test_an_old_sync_is_named_with_its_age(world) -> None:
    synced_ago(world, 2 * 3600 + 30)
    p = run(world, "gwbc", "spike")
    assert p.returncode == 0, p.stderr
    assert "Last synced 2 hours ago. Run: gw sync" in p.stderr.splitlines()
    assert "Last synced" not in p.stdout, p.stdout


def test_quiet_drops_the_stale_line(world) -> None:
    synced_ago(world, 3 * 24 * 3600)
    p = run(world, "gwbs", "-q")
    assert p.returncode == 0, p.stderr
    assert "Last synced" not in p.stderr


def test_a_fresh_sync_prints_no_sync_line(world) -> None:
    bare(world)
    assert sync(world).returncode == 0
    p = run(world, "gwbs")
    assert "synced" not in p.stderr.lower(), p.stderr


# --------------------------------------------------------------------------
# the deletion disclosure and its question
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "argv", [("gwp", "--yes"), ("gwr", "done", "--yes"), ("gwbd", "done", "--yes")]
)
def test_the_plan_repeats_the_never_synced_line(world, argv) -> None:
    bare(world)
    if argv[0] == "gwbd":
        world.git("branch", "done")
    else:
        world.worktree("done")
    p = run(world, *argv)
    assert p.returncode == 0, p.stderr
    assert p.stderr.splitlines().count(NEVER) == 2, p.stderr
    assert NEVER not in p.stdout, p.stdout


@pytest.mark.parametrize("argv", [("gwp",), ("gwr", "done"), ("gwbd", "done")])
def test_a_never_synced_deletion_asks_so(world, argv) -> None:
    bare(world)
    if argv[0] == "gwbd":
        world.git("branch", "done")
    else:
        wt = world.worktree("done")
    code, out = run_on_a_terminal(world, *argv, answer="n\n")
    assert code == 0, out
    assert "Never synced. Delete anyway? [y/N]" in out
    assert world.git("rev-parse", "--verify", "refs/heads/done")
    if argv[0] != "gwbd":
        assert wt.exists()


def test_a_synced_deletion_asks_the_usual_question(world) -> None:
    bare(world)
    assert sync(world).returncode == 0
    world.worktree("done")
    code, out = run_on_a_terminal(world, "gwp", answer="n\n")
    assert code == 0, out
    assert "remove 1 worktree(s)? [y/N]" in out
    assert "Never synced" not in out
