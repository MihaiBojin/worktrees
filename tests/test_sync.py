"""`gw sync`: one fetch of the default remote, and the file that records it."""

from __future__ import annotations

import json
import re
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

from conftest import env_for

NAME = "git-worktrees-sync.json"


def sync(world, *args: str, at: Path | None = None, **env: str):
    return subprocess.run(
        [
            sys.executable,
            "-c",
            "from worktrees.cli import gw; raise SystemExit(gw())",
            "sync",
            *args,
        ],
        cwd=str(at or world.repo),
        capture_output=True,
        text=True,
        env={**env_for(world), **env},
    )


def read(world, at: Path | None = None):
    """What the reader returns, asked from inside the repository."""
    p = subprocess.run(
        [
            sys.executable,
            "-c",
            "import json; from worktrees import sync; print(json.dumps(sync.read()))",
        ],
        cwd=str(at or world.repo),
        capture_output=True,
        text=True,
        env=env_for(world),
        check=True,
    )
    return json.loads(p.stdout)


def bare(world, name: str = "origin") -> Path:
    """A bare repository on disk holding main, added as remote `name`."""
    path = world.root / f"{name}.git"
    world.git("init", "--quiet", "--bare", "-b", "main", str(path))
    world.git("remote", "add", name, str(path))
    world.git("push", "--quiet", name, "main")
    return path


def common(world) -> Path:
    return world.repo / ".git"


def leftovers(world) -> list[Path]:
    return [p for p in common(world).iterdir() if p.name.startswith(f".{NAME}.")]


def test_sync_fetches_sets_head_and_records_the_remote(world) -> None:
    path = bare(world)
    # Nine hours east of UTC, so a local time would be off by more than the
    # few seconds allowed below.
    p = sync(world, TZ="Asia/Tokyo")
    assert p.returncode == 0, p.stderr
    assert p.stdout == ""
    assert p.stderr.strip() == "synced origin; head is origin/main"
    assert (
        world.git("symbolic-ref", "refs/remotes/origin/HEAD")
        == "refs/remotes/origin/main"
    )
    record = json.loads((common(world) / NAME).read_text())
    assert record["remote"] == {"name": "origin", "url": str(path).removesuffix(".git")}
    assert record["forge"] is None
    assert re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ", record["refs"])
    refs = datetime.strptime(record["refs"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
    assert abs((datetime.now(UTC) - refs).total_seconds()) < 60
    assert read(world) == record
    assert leftovers(world) == []


def test_quiet_says_nothing_and_verbose_traces_git(world) -> None:
    bare(world)
    assert sync(world, "-q").stderr == ""
    p = sync(world, "-v")
    assert re.search(r"^\+ git .*fetch --prune origin$", p.stderr, re.M), p.stderr
    assert re.search(r"^\+ git .*remote set-head origin --auto$", p.stderr, re.M)


def test_an_unreachable_remote_fails_and_leaves_the_file(world) -> None:
    bare(world)
    assert sync(world).returncode == 0
    before = (common(world) / NAME).read_text()
    world.git("remote", "set-url", "origin", str(world.root / "gone.git"))
    p = sync(world, "-q")
    assert p.returncode == 1
    assert "origin could not be synced" in p.stderr
    assert "gone.git" in p.stderr  # git's own reason
    assert (common(world) / NAME).read_text() == before
    assert leftovers(world) == []


def test_a_remote_with_no_branch_fails_and_writes_nothing(world) -> None:
    empty = world.root / "empty.git"
    world.git("init", "--quiet", "--bare", "-b", "main", str(empty))
    world.git("remote", "add", "origin", str(empty))
    p = sync(world)
    assert p.returncode == 1
    assert "origin could not be synced" in p.stderr
    assert "Cannot determine remote HEAD" in p.stderr  # git's own reason
    assert not (common(world) / NAME).exists()


def test_no_remote_is_a_note_and_writes_nothing(world) -> None:
    p = sync(world)
    assert p.returncode == 0, p.stderr
    assert "no remote" in p.stderr
    assert not (common(world) / NAME).exists()
    assert read(world) is None


def test_several_remotes_without_the_setting_refuse(world) -> None:
    bare(world, "origin")
    bare(world, "upstream")
    p = sync(world)
    assert p.returncode == 1
    assert "origin and upstream" in p.stderr
    assert "git config checkout.defaultRemote <name>" in p.stderr
    assert "fetch" not in sync(world, "-v").stderr
    assert not (common(world) / NAME).exists()


def test_a_default_remote_that_is_not_a_remote_is_named(world) -> None:
    bare(world, "origin")
    bare(world, "upstream")
    world.git("config", "checkout.defaultRemote", "nope")
    p = sync(world)
    assert p.returncode == 1
    assert "checkout.defaultRemote names nope, which is none of them" in p.stderr
    assert not (common(world) / NAME).exists()


def test_several_remotes_with_the_setting_sync_that_one(world) -> None:
    bare(world, "origin")
    bare(world, "upstream")
    world.git("config", "checkout.defaultRemote", "upstream")
    p = sync(world, "-v")
    assert p.returncode == 0, p.stderr
    assert "fetch --prune upstream" in p.stderr
    assert "fetch --prune origin" not in p.stderr
    assert read(world)["remote"]["name"] == "upstream"


def test_the_file_is_shared_by_every_worktree(world) -> None:
    bare(world)
    linked = world.worktree("feature")
    p = sync(world, at=linked)
    assert p.returncode == 0, p.stderr
    assert (common(world) / NAME).is_file()
    assert read(world) == read(world, at=linked) is not None


def test_a_malformed_file_reads_as_never_synced_and_is_replaced(world) -> None:
    path = bare(world)
    # The right remote, so these are refused for their types and not for it.
    remote = {"name": "origin", "url": str(path).removesuffix(".git")}
    for junk in (
        "{",
        "[]",
        '{"refs": 3}',
        '{"refs": "x", "forge": null}',
        json.dumps({"remote": remote, "refs": 3, "forge": None}),
        json.dumps({"remote": remote, "refs": "x", "forge": 3}),
    ):
        (common(world) / NAME).write_text(junk)
        assert read(world) is None, junk
    (common(world) / NAME).write_text(
        json.dumps({"remote": remote, "refs": "x", "forge": None})
    )
    assert read(world) is not None
    assert sync(world).returncode == 0
    assert read(world) is not None


def test_a_changed_remote_url_reads_as_never_synced(world) -> None:
    bare(world)
    assert sync(world).returncode == 0
    other = world.root / "elsewhere.git"
    world.git("clone", "--quiet", "--bare", str(world.root / "origin.git"), str(other))
    world.git("remote", "set-url", "origin", str(other))
    assert read(world) is None
    assert sync(world).returncode == 0
    assert read(world)["remote"]["url"] == str(other).removesuffix(".git")


def test_a_renamed_remote_reads_as_never_synced(world) -> None:
    bare(world)
    assert sync(world).returncode == 0
    world.git("remote", "rename", "origin", "upstream")
    assert read(world) is None


def test_sync_help_says_what_it_does(world) -> None:
    p = sync(world, "--help")
    assert p.returncode == 0
    assert "fetch the default remote and record when" in p.stdout + p.stderr


def test_sync_takes_no_json_and_the_help_says_so(world) -> None:
    assert sync(world, "--json").returncode == 2
    listed = subprocess.run(
        [
            sys.executable,
            "-c",
            "from worktrees.cli import gw; raise SystemExit(gw())",
            "-h",
        ],
        cwd=str(world.repo),
        capture_output=True,
        text=True,
        env=env_for(world),
    )
    assert "gw sync takes no --json" in listed.stderr
