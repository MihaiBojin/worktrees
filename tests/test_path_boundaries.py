"""Git path data keeps whitespace distinct from its output terminator."""

from __future__ import annotations

import json
import math
import os
import subprocess
import sys

import pytest
from test_cli import run
from test_sync import origin

from worktrees import cli, layout, prune, sync
from worktrees import repo as R


@pytest.mark.parametrize("suffix", [" ", "\t", "\ninside ", "\r\ninside "])
def test_current_whitespace_root_is_protected_from_pruning(world, suffix):
    original = world.merged_worktree("topic")
    target = world.root / ("linked" + suffix)
    world.git("worktree", "move", str(original), str(target))
    p = run(world, "gwp", "--dry-run", "--json", at=target)
    assert p.returncode == 0, p.stderr
    row = next(
        row for row in json.loads(p.stdout)["verdicts"] if row["branch"] == "topic"
    )
    assert row["path"] == str(target)
    assert not row["eligible"]
    assert "you are standing in it" in row["protections"]
    p = run(world, "gwp", "--yes", "--delete", "all", at=target)
    assert p.returncode == 0, p.stderr
    assert target.exists()
    assert world.git("rev-parse", "topic")


@pytest.mark.parametrize("suffix", [" ", "\t", "\ninside ", "\r\ninside "])
@pytest.mark.parametrize("keep", [False, True])
def test_nonterminal_current_whitespace_root_is_not_removed(world, suffix, keep):
    original = world.merged_worktree("topic")
    target = world.root / ("linked" + suffix)
    world.git("worktree", "move", str(original), str(target))
    p = run(
        world,
        "gwr",
        str(target),
        "--yes",
        *(["--keep-branch"] if keep else []),
        at=target,
    )
    assert p.returncode == 3, p.stderr
    assert "second confirmation" in p.stderr
    assert target.exists()
    assert world.git("rev-parse", "topic")


@pytest.mark.parametrize("suffix", [" ", "\t", "\ninside ", "\r\ninside "])
def test_approved_whitespace_root_leaves_before_removal(
    world, monkeypatch, capsys, suffix
):
    original = world.merged_worktree("topic")
    target = world.root / ("linked" + suffix)
    world.git("worktree", "move", str(original), str(target))
    monkeypatch.chdir(target)
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    prompts = []

    def approve(question):
        prompts.append(question)
        return "yes"

    monkeypatch.setattr(prune, "_prompt", approve)
    original_remove = prune.worktree_remove

    def remove_from_main(*args, **kwargs):
        assert os.getcwd() == str(world.repo)
        return original_remove(*args, **kwargs)

    monkeypatch.setattr(prune, "worktree_remove", remove_from_main)
    assert cli.gwr([str(target), "--yes", "--json"]) == 0
    assert len(prompts) == 1
    assert str(target) in prompts[0]
    assert not target.exists()
    assert json.loads(capsys.readouterr().out)["path"] == str(world.repo)


@pytest.mark.parametrize("suffix", [" ", "\t", "\n", "\r\n"])
def test_listing_marks_exact_current_root(world, suffix):
    original = world.worktree("topic")
    target = world.root / ("linked" + suffix)
    world.git("worktree", "move", str(original), str(target))
    result = run(world, "gwl", "--json", at=target)
    assert result.returncode == 0, result.stderr
    row = next(
        row
        for row in json.loads(result.stdout)["worktrees"]
        if row["branch"] == "topic"
    )
    assert row["path"] == str(target)
    assert row["here"]


@pytest.mark.parametrize("suffix", [" ", "\t", "\n", "\r\n"])
def test_move_uses_exact_current_root(world, suffix):
    original = world.worktree("topic")
    target = world.root / ("linked" + suffix)
    world.git("worktree", "move", str(original), str(target))
    result = run(world, "gwm", "renamed", "--json", at=target)
    assert result.returncode == 0, result.stderr
    destination = world.root / ".worktrees" / "renamed" / "repo"
    assert json.loads(result.stdout)["path"] == str(destination)
    assert destination.exists() and not target.exists()
    assert world.git("rev-parse", "renamed")


@pytest.mark.parametrize("suffix", [" ", "\t", "\ninside ", "\r\ninside "])
def test_main_whitespace_root_is_the_exact_departure_destination(
    world, monkeypatch, capsys, suffix
):
    target = world.root / ("main" + suffix)
    monkeypatch.chdir(world.root)
    world.repo.rename(target)
    world.repo = target
    monkeypatch.chdir(target)
    linked = world.merged_worktree("topic")
    monkeypatch.chdir(linked)
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(prune, "_prompt", lambda _: "yes")
    assert cli.gwr([str(linked), "--yes"]) == 0
    assert capsys.readouterr().out == str(target) + "\n"
    assert os.getcwd() == str(target)
    assert sync.path() == target / ".git" / "git-worktrees-sync.json"


@pytest.mark.parametrize("suffix", [" ", "\t", "\ninside ", "\r\ninside "])
def test_common_directory_cache_keeps_exact_path(world, network, suffix):
    gitdir = world.root / ("gitdb" + suffix)
    world.git("init", "--separate-git-dir", str(gitdir), str(world.repo))
    origin(world)
    p = subprocess.run(
        [
            sys.executable,
            "-c",
            "from worktrees.cli import gw; raise SystemExit(gw())",
            "sync",
        ],
        cwd=world.repo,
        env=network.env(),
        capture_output=True,
        text=True,
    )
    assert p.returncode == 0, p.stderr
    expected = gitdir / "git-worktrees-sync.json"
    assert sync.path() == expected
    assert expected.exists()
    assert sync.read()["lastSync"]["refs"]
    assert not (world.root / "gitdb" / "git-worktrees-sync.json").exists()


def test_common_directory_ownership_does_not_collapse_spaces(world):
    ours = world.root / "gitdb"
    theirs = world.root / "gitdb "
    other = world.root / "other"
    world.git("init", "--separate-git-dir", str(ours), str(world.repo))
    world.git("init", "--separate-git-dir", str(theirs), str(other))
    world.commit("README", at=other)
    assert layout.owner_of(str(other)) == str(world.root)


@pytest.mark.parametrize("name", ["topic ", "topic\t", "topic\r\nadmin"])
def test_git_pointer_preserves_admin_path(world, name):
    path = world.worktree("topic")
    pointer = path / ".git"
    old = pointer.read_bytes().decode().removesuffix("\n").removeprefix("gitdir: ")
    from pathlib import Path

    admin = Path(old)
    renamed = admin.with_name(name)
    admin.rename(renamed)
    pointer.write_bytes(f"gitdir: {renamed}\n".encode())
    rows = R.worktrees()
    row = next(row for row in rows if row.branch == "topic")
    assert math.isfinite(R.created_at(row))
    assert R.created_at(row) == (renamed / "commondir").stat().st_mtime


@pytest.mark.parametrize("suffix", [" ", "\t", "\n", "\r\n"])
def test_remote_url_whitespace_invalidates_cache(world, network, suffix):
    origin(world)
    p = subprocess.run(
        [
            sys.executable,
            "-c",
            "from worktrees.cli import gw; raise SystemExit(gw())",
            "sync",
        ],
        cwd=world.repo,
        env=network.env(),
        capture_output=True,
        text=True,
    )
    assert p.returncode == 0, p.stderr
    url = "git@github.com:owner/repo.git"
    world.git("config", "remote.origin.url", url + suffix)
    assert sync.read()["lastSync"] == {"refs": None, "forge": None}
    p = subprocess.run(
        [
            sys.executable,
            "-c",
            "from worktrees.cli import gw; raise SystemExit(gw())",
            "sync",
        ],
        cwd=world.repo,
        env=network.env(),
        capture_output=True,
        text=True,
    )
    assert p.returncode == 0, p.stderr
    cache = sync.path()
    assert cache is not None
    assert json.loads(cache.read_text())["remote"]["url"] == url + suffix
    assert sync.read()["lastSync"]["refs"]


@pytest.mark.parametrize("suffix", ["\n", "\r\n"])
@pytest.mark.parametrize("entry", ["gwp", "gwr"])
def test_current_root_ending_lf_is_never_removed_without_departure(
    world, suffix, entry
):
    original = world.merged_worktree("topic")
    target = world.root / ("linked" + suffix)
    world.git("worktree", "move", str(original), str(target))
    args = [] if entry == "gwp" else [str(target)]
    p = run(world, entry, *args, "--yes", "--delete", "all", at=target)
    assert p.returncode != 0 or entry == "gwp"
    assert target.exists()
    assert world.git("rev-parse", "topic")


@pytest.mark.parametrize("suffix", [" ", "\t", "\ninside ", "\r\ninside "])
def test_real_terminal_confirms_current_whitespace_root(world, suffix):
    from test_cli import run_on_a_terminal_at

    original = world.merged_worktree("topic")
    target = world.root / ("linked" + suffix)
    world.git("worktree", "move", str(original), str(target))
    code, output = run_on_a_terminal_at(
        world, target, "gwr", str(target), "--yes", answer="n\n"
    )
    assert code == 0, output
    assert "remove current worktree" in output
    assert target.exists()
    code, output = run_on_a_terminal_at(
        world, target, "gwr", str(target), "--yes", answer="y\n"
    )
    assert code == 0, output
    assert output.endswith(str(world.repo) + "\n")
    assert not target.exists()
