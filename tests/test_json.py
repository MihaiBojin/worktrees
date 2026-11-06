"""`lastSync` and `head`, on every `--json` result."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from test_cli import run
from test_head import cloned_then_gone, no_main_or_master, synced_then_gone
from test_sync import bare, fake, forge_remote, record, sync, synced

TIME = r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ"


def every_json_command(world) -> list[tuple[tuple[str, ...], Path]]:
    """One run of each command that takes --json, and where to run it."""
    world.worktree("feature")
    moving = world.worktree("moving")
    rotating = world.worktree("rotating")
    world.commit("r.txt", at=rotating)
    # In this order: the last three remove or delete what they find finished.
    return [
        (("gws",), world.repo),
        (("gwbs",), world.repo),
        (("gw", "list"), world.repo),
        (("gwa", "spike"), world.repo),
        (("gwm", "moved"), moving),
        (("gwrot",), rotating),
        (("gwbc", "started"), world.repo),
        (("gwr", "feature", "--yes"), world.repo),
        (("gwbd", "--all", "--yes"), world.repo),
        (("gwp", "--yes"), world.repo),
    ]


def envelope(world, argv: tuple[str, ...], at: Path | None = None) -> dict:
    p = run(world, *argv, "--json", at=at)
    return json.loads(p.stdout)


def test_never_synced_json_parses_with_null_times(world) -> None:
    bare(world)
    for argv, at in every_json_command(world):
        p = run(world, *argv, "--json", at=at)
        assert p.returncode == 0, (argv, p.stderr)
        out = json.loads(p.stdout)
        assert out["lastSync"] == {"refs": None, "forge": None}, argv
        assert out["head"] == {"ref": "refs/heads/main", "source": "inferred"}, argv


def test_a_path_on_stdout_is_alone_when_never_synced(world) -> None:
    bare(world)
    p = run(world, "gwa", "spike")
    assert p.returncode == 0, p.stderr
    assert len(p.stdout.splitlines()) == 1
    assert Path(p.stdout.strip()).is_dir()


def test_no_remote_has_null_times(world) -> None:
    out = envelope(world, ("gwbs",))
    assert out["lastSync"] == {"refs": None, "forge": None}
    assert out["head"] == {"ref": "refs/heads/main", "source": "inferred"}


def test_a_sync_gives_the_refs_time_and_the_remote_head(world) -> None:
    bare(world)
    assert sync(world).returncode == 0
    refs = record(world)["refs"]
    assert re.fullmatch(TIME, refs)
    for argv, at in every_json_command(world):
        out = envelope(world, argv, at=at)
        # A bare repository on disk is no forge, so there is no forge time.
        assert out["lastSync"] == {"refs": refs, "forge": None}, argv
        assert out["head"] == {
            "ref": "refs/remotes/origin/main",
            "source": "remote",
        }, argv


def test_the_other_payloads_carry_both_fields(world) -> None:
    """A caught-up head branch, a prune with nothing to remove and a dry run
    each print their own payload."""
    bare(world)
    assert sync(world).returncode == 0
    refs = record(world)["refs"]
    world.git("switch", "--quiet", "--create", "ahead", "main")
    world.git("update-ref", "refs/remotes/origin/main", world.commit("r.txt"))
    world.git("switch", "--quiet", "main")
    world.git("branch", "done")
    expected = {
        ("gwrot",): {"branch", "at", "previous_sha", "sha"},
        ("gwp", "--yes"): {"removed", "failed"},
        ("gwbd", "--all", "--dry-run"): {
            "head_sha",
            "verdicts",
            "removed",
            "failed",
            "dry_run",
        },
    }
    for argv, keys in expected.items():
        out = envelope(world, argv)
        assert set(out) == {"lastSync", "head", *keys}, argv
        assert out["lastSync"] == {"refs": refs, "forge": None}, argv
        assert out["head"] == {
            "ref": "refs/remotes/origin/main",
            "source": "remote",
        }, argv


def test_a_forge_that_answered_gives_the_forge_time(world) -> None:
    forge_remote(world)
    assert synced(world, fake(world, "github", {})).returncode == 0
    file = record(world)
    assert re.fullmatch(TIME, file["forge"])
    out = envelope(world, ("gws",))
    assert out["lastSync"] == {"refs": file["refs"], "forge": file["forge"]}


def test_a_clone_head_says_clone(world) -> None:
    clone = world.root / "clone"
    world.git("clone", "--quiet", str(world.repo), str(clone))
    out = envelope(world, ("gwbs",), at=clone)
    assert out["lastSync"] == {"refs": None, "forge": None}
    assert out["head"] == {"ref": "refs/remotes/origin/main", "source": "clone"}


@pytest.mark.parametrize(
    ("setup", "source"),
    [
        (no_main_or_master, "inferred"),
        (synced_then_gone, "remote"),
        (cloned_then_gone, "clone"),
    ],
    ids=["inferred", "synced", "cloned"],
)
@pytest.mark.parametrize("argv", [("gws",), ("gwbs",), ("gw", "list")])
def test_an_unresolved_head_has_a_null_ref(world, setup, source, argv) -> None:
    setup(world)
    assert envelope(world, argv)["head"] == {"ref": None, "source": source}


def test_each_command_keeps_its_own_fields(world) -> None:
    keys = {
        "gws": {"stale", "neighbours", "verdicts"},
        "gwp": {"stale", "neighbours", "verdicts", "removed", "failed"},
        "gwr": {"removed", "path"},
        "gwbs": {"head_sha", "verdicts"},
        "gwbd": {"head_sha", "verdicts", "removed", "failed"},
        "gwa": {"path", "branch", "created"},
        "gwm": {"path", "branch", "created"},
        "gwbc": {"branch", "base", "sha"},
        "gwrot": {"branch", "stem", "base", "sha", "previous", "previous_sha"},
        "gw": {"worktrees"},
    }
    for argv, at in every_json_command(world):
        out = envelope(world, argv, at=at)
        assert set(out) == {"lastSync", "head", *keys[argv[0]]}, argv


def test_list_json_is_an_object_holding_the_worktrees(world) -> None:
    wt = world.worktree("feature")
    out = envelope(world, ("gw", "list"))
    assert [w["path"] for w in out["worktrees"]] == [str(world.repo), str(wt)]
    assert out["worktrees"][1] == {
        "branch": "feature",
        "path": str(wt),
        "main": False,
        "here": False,
        "flags": [],
    }
