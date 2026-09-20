"""The shell completions against the flags the CLI actually parses.

A completion file is a set of claims about an interface, and it is the kind
that rots silently: nothing fails when a flag is renamed, the candidate just
stops being offered. So the flags are read back out of both shells' files and
diffed against `--help` in both directions.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import pytest
from conftest import PATH, env_for

from worktrees.cli import _CANONICAL, _COMMANDS

ROOT = Path(__file__).resolve().parents[1]
SRC = str(ROOT / "src")
FISH = ROOT / "completions"
ZSH = ROOT / "zsh" / "plugins" / "worktrees" / "completions"

BINARIES = sorted(c.binary for c in _COMMANDS.values() if c.binary)


def _help(entry: str) -> str:
    p = subprocess.run(
        [
            sys.executable,
            "-c",
            f"from worktrees.cli import {entry}; raise SystemExit({entry}())",
            "--help",
        ],
        capture_output=True,
        text=True,
        env={"PATH": PATH, "PYTHONPATH": SRC, "COLUMNS": "200"},
    )
    return p.stdout


def _parsed_flags(entry: str) -> set[str]:
    """Every long flag `entry --help` lists, minus the hidden ones."""
    return set(re.findall(r"(--[a-z][a-z-]+)", _help(entry)))


def _fish_flags(binary: str) -> set[str]:
    text = (FISH / f"{binary}.fish").read_text()
    return {f"--{m}" for m in re.findall(r"\s-l\s+([a-z][a-z-]*)", text)}


def _zsh_flags(binary: str) -> set[str]:
    text = (ZSH / f"_{binary}").read_text()
    found = set(re.findall(r"'(--[a-z][a-z-]+)\[", text))
    found |= set(re.findall(r"\{(?:-[a-z],)?(--[a-z][a-z-]+)\}", text))
    return found


@pytest.mark.parametrize("binary", BINARIES)
def test_every_binary_has_a_completion_in_both_shells(binary: str) -> None:
    assert (FISH / f"{binary}.fish").is_file()
    assert (ZSH / f"_{binary}").is_file()


def test_gw_itself_has_one_in_both_shells() -> None:
    assert (FISH / "gw.fish").is_file()
    assert (ZSH / "_gw").is_file()


@pytest.mark.parametrize("binary", BINARIES)
def test_fish_offers_exactly_the_flags_the_command_parses(binary: str) -> None:
    entry = binary if binary != "gw" else "main"
    assert _fish_flags(binary) == _parsed_flags(entry)


@pytest.mark.parametrize("binary", BINARIES)
def test_zsh_offers_exactly_the_flags_the_command_parses(binary: str) -> None:
    entry = binary if binary != "gw" else "main"
    assert _zsh_flags(binary) == _parsed_flags(entry)


def test_complete_lists_every_command_and_shorthand(world) -> None:
    p = subprocess.run(
        [
            sys.executable,
            "-c",
            "from worktrees.cli import main; raise SystemExit(main())",
            "--complete",
        ],
        cwd=str(world.repo),
        capture_output=True,
        text=True,
        env=env_for(world),
    )
    assert p.returncode == 0, p.stderr
    offered = {line.split("\t")[0] for line in p.stdout.splitlines()}
    assert offered == set(_CANONICAL)
    assert all("\t" in line for line in p.stdout.splitlines())


def test_complete_lists_the_worktree_you_are_standing_in(world) -> None:
    """The shell narrows; a completion that hides a candidate is worse."""
    here = world.worktree("fix-parser")
    p = subprocess.run(
        [
            sys.executable,
            "-c",
            "from worktrees.cli import gwl; raise SystemExit(gwl())",
            "--complete",
        ],
        cwd=str(here),
        capture_output=True,
        text=True,
        env=env_for(world),
    )
    assert p.returncode == 0, p.stderr
    offered = {line.split("\t")[0] for line in p.stdout.splitlines()}
    assert offered == {"main", "fix-parser"}


def _complete(entry: str, world, at=None) -> list[str]:
    p = subprocess.run(
        [
            sys.executable,
            "-c",
            f"from worktrees.cli import {entry}; raise SystemExit({entry}())",
            "--complete",
        ],
        cwd=str(at or world.repo),
        capture_output=True,
        text=True,
        env=env_for(world),
    )
    assert p.returncode == 0, p.stderr
    return [line.split("\t")[0] for line in p.stdout.splitlines()]


def test_gwl_offers_the_main_checkout_and_gwr_does_not(world) -> None:
    """assess skips the main checkout, so gwr can never act on it."""
    world.worktree("one")
    offered_by_gwl = _complete("gwl", world)
    offered_by_gwr = _complete("gwr", world)

    assert any("(main)" in name or name == "main" for name in offered_by_gwl)
    assert not [n for n in offered_by_gwr if "(main)" in n]
    assert "one" in offered_by_gwl
    assert offered_by_gwr == ["one"]


def test_gwr_offers_nothing_it_then_refuses_to_match(world) -> None:
    """Every candidate reaches gwr's own picker, or the completion lies.

    Driven through the entry point rather than compared against a second
    copy of the filter: a candidate that cannot be matched is the defect,
    and "no worktree matches" is how it shows.
    """
    world.worktree("one")
    world.worktree("two")
    for name in _complete("gwr", world):
        p = subprocess.run(
            [
                sys.executable,
                "-c",
                "from worktrees.cli import gwr; raise SystemExit(gwr())",
                "--no-fetch",
                name,
            ],
            cwd=str(world.repo),
            capture_output=True,
            text=True,
            env=env_for(world),
            stdin=subprocess.DEVNULL,
        )
        assert "no worktree matches" not in p.stderr, (name, p.stderr)
