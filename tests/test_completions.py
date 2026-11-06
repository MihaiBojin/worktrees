"""The shell completions against the flags the CLI actually parses.

A completion file is a set of claims about an interface, and it is the kind
that rots silently: nothing fails when a flag is renamed, the candidate just
stops being offered. So the flags are read back out of both shells' files and
diffed against `--help` in both directions.
"""

from __future__ import annotations

import itertools
import os
import pty
import re
import select
import shlex
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest
from conftest import PATH, env_for

from worktrees.cli import _CANONICAL, command_rows

ROOT = Path(__file__).resolve().parents[1]
SRC = str(ROOT / "src")
FISH = ROOT / "completions"
ZSH = ROOT / "zsh" / "plugins" / "worktrees" / "completions"

BINARIES = sorted(c.binary for _, c in command_rows() if c.binary)


def _help(entry: str, *args: str) -> str:
    p = subprocess.run(
        [
            sys.executable,
            "-c",
            f"from worktrees.cli import {entry}; raise SystemExit({entry}())",
            *args,
            "--help",
        ],
        capture_output=True,
        text=True,
        env={"PATH": PATH, "PYTHONPATH": SRC, "COLUMNS": "200"},
    )
    # A command that changes directory prints its help on stderr.
    return p.stdout + p.stderr


def _parsed_flags(entry: str, *args: str) -> set[str]:
    """Every long flag `entry --help` lists, minus the hidden ones."""
    return set(re.findall(r"(--[a-z][a-z-]+)", _help(entry, *args)))


def _fish_flags(binary: str) -> set[str]:
    text = (FISH / f"{binary}.fish").read_text()
    return {f"--{m}" for m in re.findall(r"\s-l\s+([a-z][a-z-]*)", text)}


def _zsh_flags(binary: str) -> set[str]:
    text = (ZSH / f"_{binary}").read_text()
    # A leading `*` is zsh for an option that may be given more than once.
    found = set(re.findall(r"'\*?(--[a-z][a-z-]+)\[", text))
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


def test_fish_offers_exactly_the_flags_gw_parses() -> None:
    """`gw list`'s, since `gw --json` is `gw list --json`, and the branch
    commands' under `gw branch`."""
    assert _fish_flags("gw") == (
        _parsed_flags("main", "list")
        | _parsed_flags("main", "branch", "status")
        | _parsed_flags("main", "branch", "delete")
    )


def test_zsh_offers_exactly_the_flags_gw_parses() -> None:
    """`gw list`'s. Under `gw branch` zsh calls `_gwbs` and `_gwbd`, which
    are held to their own binaries' flags above."""
    assert _zsh_flags("gw") == _parsed_flags("main", "list")


def _fish_conditions(flag: str) -> list[str]:
    """The `-n` condition of every `complete -c gw` line offering `flag`."""
    text = (FISH / "gw.fish").read_text().replace("\\\n", " ")
    found = []
    for line in text.splitlines():
        words = shlex.split(line, comments=True)
        if words[:3] == ["complete", "-c", "gw"] and f"--{flag}" in [
            f"--{b}" for a, b in itertools.pairwise(words) if a == "-l"
        ]:
            found.append(words[words.index("-n") + 1] if "-n" in words else "")
    return found


def test_fish_offers_list_only_at_the_root_and_after_gw_list() -> None:
    conditions = _fish_conditions("list")
    assert conditions, "no --list"
    for c in conditions:
        assert "branch" not in c, c
        assert "__fish_use_subcommand" in c or "from list" in c, c


def test_fish_offers_only_after_gw_branch_status() -> None:
    conditions = _fish_conditions("only")
    assert conditions, "no --only"
    for c in conditions:
        assert "from branch" in c and "from status" in c and "delete" not in c, c


def test_zsh_offers_list_only_at_the_root_and_after_gw_list() -> None:
    """The root `_arguments` block, or a line guarded by `== list`."""
    lines = (ZSH / "_gw").read_text().splitlines()
    root = next(i for i, x in enumerate(lines) if "_arguments -C \\" in x)
    end = next(i for i in range(root, len(lines)) if not lines[i].endswith("\\"))
    at = [i for i, x in enumerate(lines) if "--list" in x]
    assert at, "no --list"
    for i in at:
        assert root < i <= end or "== list ]]" in lines[i - 1], lines[i]


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
            "from worktrees.cli import main; raise SystemExit(main())",
            "list",
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


def _complete(entry: str, world, *args: str, at=None) -> list[str]:
    p = subprocess.run(
        [
            sys.executable,
            "-c",
            f"from worktrees.cli import {entry}; raise SystemExit({entry}())",
            *args,
            "--complete",
        ],
        cwd=str(at or world.repo),
        capture_output=True,
        text=True,
        env=env_for(world),
    )
    assert p.returncode == 0, p.stderr
    return [line.split("\t")[0] for line in p.stdout.splitlines()]


def test_gw_list_offers_the_main_checkout_and_gwr_does_not(world) -> None:
    """assess skips the main checkout, so gwr can never act on it."""
    world.worktree("one")
    offered_by_list = _complete("main", world, "list")
    offered_by_gwr = _complete("gwr", world)

    assert any("(main)" in name or name == "main" for name in offered_by_list)
    assert not [n for n in offered_by_gwr if "(main)" in n]
    assert "one" in offered_by_list
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
                name,
            ],
            cwd=str(world.repo),
            capture_output=True,
            text=True,
            env=env_for(world),
            stdin=subprocess.DEVNULL,
        )
        assert "no worktree matches" not in p.stderr, (name, p.stderr)


# --------------------------------------------------------------------------
# one row a line, whatever the filesystem allows in a path
# --------------------------------------------------------------------------


def _at(world, path: Path, branch: str) -> None:
    """A worktree somewhere `gwa` would never put one."""
    path.parent.mkdir(parents=True, exist_ok=True)
    world.git("worktree", "add", "--quiet", "-b", branch, str(path), "main")


@pytest.mark.parametrize("argv", [("main", "list"), ("gwr",)])
def test_a_path_holding_a_newline_is_not_offered(world, argv) -> None:
    """Both shells split records on the newline, so such a row arrives as
    two and its tail becomes a candidate of its own."""
    _at(world, world.parent / ".worktrees" / "fine" / "repo", "fine")
    _at(world, world.root / "we\nird", "odd")

    p = subprocess.run(
        [
            sys.executable,
            "-c",
            f"from worktrees.cli import {argv[0]}; raise SystemExit({argv[0]}())",
            *argv[1:],
            "--complete",
        ],
        cwd=str(world.repo),
        capture_output=True,
        text=True,
        env=env_for(world),
    )
    assert p.returncode == 0, p.stderr
    rows = p.stdout.splitlines()
    assert all("\t" in row for row in rows), rows
    assert "fine" in [row.split("\t")[0] for row in rows]
    assert "odd" not in [row.split("\t")[0] for row in rows]
    assert "ird" not in p.stdout


def test_a_path_holding_a_tab_is_still_offered(world) -> None:
    """A row is split at its first tab, so a later one is description."""
    _at(world, world.root / "ta\tbbed", "tabbed")
    rows = _complete("main", world, "list")
    assert "tabbed" in rows


@pytest.mark.skipif(not shutil.which("zsh"), reason="zsh is not installed")
@pytest.mark.parametrize(
    ("prefix", "needle", "absent"),
    [
        ("gw branch delete --dry", b"--dry-run", None),
        ("gwbd --dry", b"--dry-run", None),
        ("gw --li", b"pick none", None),
        ("gw list --li", b"pick none", None),
        ("gw fix-p", b"fix-parser", None),
        ("gw list fix-p", b"fix-parser", None),
        ("gw prune --", b"--json", None),
        # The last candidate in the list, so the whole list has arrived.
        ("gw sync --", b"--verbose", b"--json"),
    ],
)
def test_zsh_completes_gw_without_errors(world, prefix, needle, absent) -> None:
    world.worktree("fix-parser")
    bin_dir = world.root / "bin"
    bin_dir.mkdir()
    for entry in ("gw", "gwbs", "gwbd"):
        wrapper = bin_dir / entry
        wrapper.write_text(
            f"#!{sys.executable}\nfrom worktrees.cli import {entry}\n"
            f"raise SystemExit({entry}())\n"
        )
        wrapper.chmod(0o755)
    env = env_for(world)
    env.update(PATH=f"{bin_dir}:{env['PATH']}", TERM="xterm", ZDOTDIR=str(world.root))
    parent, child = pty.openpty()
    process = subprocess.Popen(
        ["zsh", "-f"],
        stdin=child,
        stdout=child,
        stderr=child,
        cwd=world.repo,
        env=env,
    )
    os.close(child)

    def read_until(needle: bytes) -> bytes:
        output = bytearray()
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            if select.select([parent], [], [], 0.1)[0]:
                output.extend(os.read(parent, 65536))
                if needle in output:
                    return bytes(output)
        pytest.fail(f"zsh did not print {needle!r}: {bytes(output)!r}")

    try:
        # Trust the test's completion sources even when CI directory
        # permissions would make compinit ask for confirmation.
        setup = (
            f"fpath=({ZSH} $fpath); autoload -Uz compinit; "
            "compinit -D -u; PS1='REA''DY> '\n"
        )
        os.write(parent, setup.encode())
        read_until(b"READY> ")
        # ctrl-d lists the candidates with their descriptions, where a tab
        # would insert the rest of one and leave only a suffix to look for.
        os.write(parent, (prefix + "\x04").encode())
        output = read_until(needle)
        if absent:
            assert absent not in output, output
        assert b"bad option" not in output, output
        assert b"command not found" not in output, output
    finally:
        process.kill()
        process.wait(timeout=5)
        os.close(parent)


@pytest.mark.skipif(not shutil.which("fish"), reason="fish is not installed")
def test_fish_offers_no_json_after_gw_sync() -> None:
    def offered(prefix: str) -> list[str]:
        p = subprocess.run(
            [
                "fish",
                "--no-config",
                "-c",
                f"source {FISH / 'gw.fish'}; complete -C '{prefix}'",
            ],
            capture_output=True,
            text=True,
            env={"PATH": PATH},
        )
        assert p.returncode == 0, p.stderr
        return [line.split("\t")[0] for line in p.stdout.splitlines()]

    assert "--json" not in offered("gw sync --")
    assert "--explain" in offered("gw sync --")
    assert "--json" in offered("gw prune --")


@pytest.mark.skipif(not shutil.which("fish"), reason="fish is not installed")
@pytest.mark.parametrize("prefix", ["gw fix-p", "gw list fix-p"])
def test_fish_completes_a_worktree_after_gw_and_gw_list(world, prefix) -> None:
    world.worktree("fix-parser")
    bin_dir = world.root / "bin"
    bin_dir.mkdir()
    wrapper = bin_dir / "gw"
    wrapper.write_text(
        f"#!{sys.executable}\nfrom worktrees.cli import gw\nraise SystemExit(gw())\n"
    )
    wrapper.chmod(0o755)
    env = env_for(world)
    env["PATH"] = f"{bin_dir}:{env['PATH']}"
    p = subprocess.run(
        [
            "fish",
            "--no-config",
            "-c",
            f"source {FISH / 'gw.fish'}; complete -C '{prefix}'",
        ],
        cwd=world.repo,
        capture_output=True,
        text=True,
        env=env,
    )
    assert p.returncode == 0, p.stderr
    assert [line.split("\t")[0] for line in p.stdout.splitlines()] == ["fix-parser"]
