"""The shell completions against the flags the CLI actually parses.

A completion file is a set of claims about an interface, and it is the kind
that rots silently: nothing fails when a flag is renamed, the candidate just
stops being offered. So the flags are read back out of both shells' files and
diffed against `--help` in both directions.
"""

from __future__ import annotations

import os
import pty
import re
import select
import shlex
import shutil
import subprocess
import sys
import time
from collections.abc import Callable
from pathlib import Path

import pytest
from conftest import PATH, World, argv, env_for

from worktrees.cli import _CANONICAL, command_rows, shortcuts

ROOT = Path(__file__).resolve().parents[1]
FISH = ROOT / "completions"
FISH_GW = FISH / "gw.fish"
SRC = str(ROOT / "src")
ZSH = ROOT / "zsh" / "plugins" / "worktrees" / "completions"
ZSH_GW = ZSH / "_gw"

PATHS = [path for path, _ in command_rows()]
SHORTCUTS = {
    name: words for path, c in command_rows() for name, words in shortcuts(path, c)
}


def _help(entry: str) -> str:
    p = subprocess.run(
        argv(entry, "--help"),
        capture_output=True,
        text=True,
        env={"PATH": PATH, "PYTHONPATH": SRC, "COLUMNS": "200"},
    )
    # A command that changes directory prints its help on stderr.
    return p.stdout + p.stderr


def _parsed_flags(entry: str) -> set[str]:
    """Every long flag `entry --help` lists, minus the hidden ones."""
    return set(re.findall(r"(--[a-z][a-z-]+)", _help(entry)))


def _parsed_options(entry: str) -> set[str]:
    """Every flag `entry --help` lists, short ones included."""
    return _parsed_flags(entry) | set(
        re.findall(r"(?:^|\s)(-[a-z])(?:,|\s)", _help(entry))
    )


def _fish_flags(path: str) -> set[str]:
    """The long flags gw.fish offers under `gw PATH`."""
    first, *rest = path.split()
    condition = (
        f"'__worktrees_on_branch {rest[0]}'" if rest else f"'__worktrees_on {first}[ ']"
    )
    return {
        f"--{flag}"
        for line in FISH_GW.read_text().splitlines()
        if re.search(condition, line)
        for flag in re.findall(r"\s-l\s+([a-z][a-z-]*)", line)
    }


def _zsh_flags(path: str) -> set[str]:
    """The long flags _gw's function for `gw PATH` offers."""
    name = "__worktrees_" + path.replace(" ", "_")
    found = re.search(rf"^{name}\(\) \{{\n(.*?)^\}}", ZSH_GW.read_text(), re.S | re.M)
    assert found, f"_gw has no {name}"
    body = found.group(1)
    # A leading `*` is zsh for an option that may be given more than once.
    flags = set(re.findall(r"'\*?(--[a-z][a-z-]+)\[", body))
    flags |= set(re.findall(r"\{(?:-[a-z],)?(--[a-z][a-z-]+)\}", body))
    return flags


@pytest.mark.parametrize("path", PATHS)
def test_fish_offers_exactly_the_flags_each_command_parses(path: str) -> None:
    assert _fish_flags(path) == _parsed_flags(f"gw {path}")


@pytest.mark.parametrize("path", PATHS)
def test_zsh_offers_exactly_the_flags_each_command_parses(path: str) -> None:
    assert _zsh_flags(path) == _parsed_flags(f"gw {path}")


def test_zsh_registers_gw_and_every_shortcut() -> None:
    compdef = ZSH_GW.read_text().splitlines()[0]
    assert compdef.startswith("#compdef ")
    assert set(compdef.split()[1:]) == {"gw", "worktrees", *SHORTCUTS}


def test_zsh_completes_each_shortcut_as_the_words_it_runs() -> None:
    """fish reads a shortcut's `--wraps`; zsh has this table, so it is held to
    the one `gw --help` prints."""
    rewrites = {
        name.strip("'"): words.split()
        for name, words in re.findall(
            r"^\s*('?[\w!]+'?)\) fixed=\(([^)]*)\) ;;$", ZSH_GW.read_text(), re.M
        )
    }
    assert rewrites == {name: words[1:] for name, words in SHORTCUTS.items()}


def test_complete_lists_every_command_and_shorthand(world) -> None:
    p = subprocess.run(
        argv("main", "--complete"),
        cwd=str(world.repo),
        capture_output=True,
        text=True,
        env=env_for(world),
    )
    assert p.returncode == 0, p.stderr
    offered = {line.split("\t")[0] for line in p.stdout.splitlines()}
    assert offered == set(_CANONICAL)
    assert all("\t" in line for line in p.stdout.splitlines())


def _complete(entry: str, world, at=None) -> list[str]:
    p = subprocess.run(
        argv(entry, "--complete"),
        cwd=str(at or world.repo),
        capture_output=True,
        text=True,
        env=env_for(world),
    )
    assert p.returncode == 0, p.stderr
    return [line.split("\t")[0] for line in p.stdout.splitlines()]


def test_complete_lists_the_worktree_you_are_standing_in(world) -> None:
    """The shell narrows; a completion that hides a candidate is worse."""
    here = world.worktree("fix-parser")
    assert set(_complete("gw list", world, at=here)) == {"main", "fix-parser"}


def test_list_offers_the_main_checkout_and_remove_does_not(world) -> None:
    """assess skips the main checkout, so remove can never act on it."""
    world.worktree("one")
    offered_by_list = _complete("gw list", world)
    offered_by_remove = _complete("gw remove", world)

    assert any("(main)" in name or name == "main" for name in offered_by_list)
    assert not [n for n in offered_by_remove if "(main)" in n]
    assert "one" in offered_by_list
    assert offered_by_remove == ["one"]


def test_remove_offers_nothing_it_then_refuses_to_match(world) -> None:
    """Every candidate reaches remove's own picker, or the completion lies.

    Driven through the entry point rather than compared against a second
    copy of the filter: a candidate that cannot be matched is the defect,
    and "no worktree matches" is how it shows.
    """
    world.worktree("one")
    world.worktree("two")
    for name in _complete("gw remove", world):
        p = subprocess.run(
            argv("gw remove", name),
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


@pytest.mark.parametrize("entry", ["gw list", "gw remove"])
def test_a_path_holding_a_newline_is_not_offered(world, entry: str) -> None:
    """Both shells split records on the newline, so such a row arrives as
    two and its tail becomes a candidate of its own."""
    _at(world, world.parent / ".worktrees" / "fine" / "repo", "fine")
    _at(world, world.root / "we\nird", "odd")

    p = subprocess.run(
        argv(entry, "--complete"),
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
    rows = _complete("gw list", world)
    assert "tabbed" in rows


@pytest.mark.skipif(not shutil.which("zsh"), reason="zsh is not installed")
@pytest.mark.parametrize("prefix", ["gw branch prune --dry", "gbwp --dry"])
def test_zsh_completes_branch_flags_without_errors(world, prefix) -> None:
    bin_dir = _completion_binaries(world)
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
        os.write(parent, (prefix + "\t").encode())
        output = read_until(b"--dry-run")
        assert b"bad option" not in output, output
        assert b"command not found" not in output, output
    finally:
        process.kill()
        process.wait(timeout=5)
        os.close(parent)


def _completion_binaries(world: World) -> Path:
    bin_dir = world.root / "completion-bin"
    bin_dir.mkdir(exist_ok=True)
    wrapper = bin_dir / "gw"
    wrapper.write_text(
        f"#!{sys.executable}\nfrom worktrees.cli import gw\nraise SystemExit(gw())\n"
    )
    wrapper.chmod(0o755)
    return bin_dir


def _fish_complete(world: World, prefix: str) -> set[str]:
    fish = shutil.which("fish")
    if not fish:
        pytest.fail("fish is required for completion verification")
    bin_dir = _completion_binaries(world)
    # The functions too: a shortcut's completions come from its `--wraps`.
    sources = [*FISH.glob("*.fish"), *(ROOT / "functions").glob("*.fish")]
    setup = f"set fish_complete_path {FISH}; " + "; ".join(
        f"source {shlex.quote(str(path))}" for path in sources
    )
    process = subprocess.run(
        [fish, "--no-config", "-c", f"{setup}; complete -C {shlex.quote(prefix)}"],
        env={**env_for(world), "PATH": f"{bin_dir}:{PATH}"},
        cwd=world.repo,
        capture_output=True,
        text=True,
    )
    assert process.returncode == 0, process.stderr
    assert process.stderr == ""
    return {line.split("\t")[0] for line in process.stdout.splitlines()}


# What is typed, and the command whose --help says what it should offer.
COMMAND_COMPLETIONS = [
    ("gw version", "gw version"),
    ("gw v", "gw version"),
    ("gw sync", "main sync"),
    ("gw prune", "gw prune"),
    ("gw p", "gw prune"),
    ("gw prune --list", "gw prune"),
    ("gw add", "gw add"),
    ("gw a", "gw add"),
    ("gw list", "gw list"),
    ("gw list sync", "gw list"),
    ("gw l", "gw list"),
    ("gw remove", "gw remove"),
    ("gw rm", "gw remove"),
    ("gw rotate", "gw rotate"),
    ("gw rot", "gw rotate"),
    ("gw branch create", "gw branch create"),
    ("gw branch status", "gw branch status"),
    ("gw branch prune", "gw branch prune"),
    ("gwp --list", "gw prune"),
    *((name, " ".join(words)) for name, words in SHORTCUTS.items()),
]
# Options zsh still offers once given, and the short spelling it drops with
# a long one.
REPEATABLE = {"--delete", "--only"}
SHORT = {"--yes": "-y", "--quiet": "-q", "--verbose": "-v", "--help": "-h"}


@pytest.mark.parametrize("complete", ["fish", "zsh"])
def test_bare_gw_completes_commands_and_never_worktrees(world, complete):
    """A worktree is reached through `gw list`, so the first word is a command."""
    world.worktree("topic")
    run = _fish_complete if complete == "fish" else _zsh_complete
    words = run(world, "gw ")
    assert {"list", "sync", "prune"} <= words
    assert "topic" not in words
    if complete == "fish":
        assert _fish_complete(world, "gw -") == {"-h", "--help"}


@pytest.mark.parametrize("prefix,entry", COMMAND_COMPLETIONS)
def test_fish_dispatches_each_command_to_its_own_flags(world, prefix, entry):
    wanted = _parsed_options(entry)
    offered = {
        word for word in _fish_complete(world, prefix + " -") if word.startswith("-")
    }
    assert offered == wanted


@pytest.mark.parametrize(
    "prefix,values",
    [
        ("gwp --delete", {"empty", "ignored-files", "stashes", "all"}),
        ("gwr! --delete", {"empty", "ignored-files", "stashes", "all"}),
        ("gbwp --delete", {"empty", "stashes", "all"}),
        ("gwp --only", {"remove", "empty", "keep", "unknown"}),
        ("gwbs --only", {"remove", "empty", "keep", "unknown"}),
        ("gw prune --delete", {"empty", "ignored-files", "stashes", "all"}),
        ("gw rm --delete", {"empty", "ignored-files", "stashes", "all"}),
        ("gw branch prune --delete", {"empty", "stashes", "all"}),
        ("gw branch status --only", {"remove", "empty", "keep", "unknown"}),
    ],
)
def test_fish_offers_the_complete_option_value_set(world, prefix, values):
    assert _fish_complete(world, prefix + " ") == values


def _zsh_complete(world: World, prefix: str) -> set[str]:
    zsh = shutil.which("zsh")
    if not zsh:
        pytest.fail("zsh is required for completion verification")
    # The same wrappers give both shells the installed command interface.
    _completion_binaries(world)
    matches = world.root / "zsh-matches"
    ready = world.root / "zsh-ready"
    env = {
        **env_for(world),
        "PATH": f"{world.root / 'completion-bin'}:{PATH}",
        "TERM": "xterm",
        "ZDOTDIR": str(world.root),
    }
    parent, child = pty.openpty()
    process = subprocess.Popen(
        [zsh, "-f"], stdin=child, stdout=child, stderr=child, cwd=world.repo, env=env
    )
    os.close(child)
    output = bytearray()

    def read_until(predicate: Callable[[], bool]) -> None:
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            if select.select([parent], [], [], 0.05)[0]:
                output.extend(os.read(parent, 65536))
            if predicate():
                return
        pytest.fail(f"zsh completion did not finish: {bytes(output)!r}")

    try:
        setup = (
            f"fpath=({ZSH} $fpath); autoload -Uz compinit; compinit -D -u; "
            "PS1='REA''DY> '; "
            'compadd() { local -a captured; builtin compadd -O captured "$@"; '
            "(( $#captured )) && print -rl -- $captured >> "
            f"{shlex.quote(str(matches))}; "
            'builtin compadd "$@"; }; '
            "_task_complete() { _main_complete; "
            f"print done > {shlex.quote(str(ready))}; "
            "}; zle -C task-complete complete-word _task_complete; "
            "bindkey '^I' task-complete\n"
        )
        os.write(parent, setup.encode())
        read_until(lambda: b"READY> " in output)
        output.clear()
        os.write(parent, (prefix + "\t").encode())
        read_until(ready.exists)
        assert b"bad option" not in output, output
        assert b"command not found" not in output, output
        return set(matches.read_text().splitlines()) if matches.exists() else set()
    finally:
        process.kill()
        process.wait(timeout=5)
        os.close(parent)


@pytest.mark.parametrize("prefix,entry", COMMAND_COMPLETIONS)
def test_zsh_dispatches_each_command_to_its_own_flags(world, prefix, entry):
    """zsh offers an option once, so one already on the line, typed or fixed
    by a shortcut, is not offered again."""
    given = {w for w in (*prefix.split(), *entry.split()) if w.startswith("-")}
    given -= REPEATABLE
    wanted = _parsed_options(entry) - given - {SHORT[g] for g in given if g in SHORT}
    offered = {
        word for word in _zsh_complete(world, prefix + " -") if word.startswith("-")
    }
    assert offered == wanted


@pytest.mark.parametrize(
    "prefix,values",
    [
        ("gwp --delete", {"empty", "ignored-files", "stashes", "all"}),
        ("gwr! --delete", {"empty", "ignored-files", "stashes", "all"}),
        ("gbwp --delete", {"empty", "stashes", "all"}),
        ("gwp --only", {"remove", "empty", "keep", "unknown"}),
        ("gwbs --only", {"remove", "empty", "keep", "unknown"}),
        ("gw prune --delete", {"empty", "ignored-files", "stashes", "all"}),
        ("gw rm --delete", {"empty", "ignored-files", "stashes", "all"}),
        ("gw branch prune --delete", {"empty", "stashes", "all"}),
        ("gw branch status --only", {"remove", "empty", "keep", "unknown"}),
    ],
)
def test_zsh_offers_the_complete_option_value_set(world, prefix, values):
    assert _zsh_complete(world, prefix + " ") == values


# What each form offers before a flag: worktrees, branch actions or branch
# names, all from the CLI's own --complete.
CANDIDATES = [
    ("gw list ", {"main", "one"}),
    ("gw l ", {"main", "one"}),
    ("gwl ", {"main", "one"}),
    ("gw remove ", {"one"}),
    ("gwr ", {"one"}),
    ("gwr! ", {"one"}),
    ("gw branch ", {"create", "prune", "status"}),
    ("gw branch status ", {"done", "main", "one"}),
    ("gwbs ", {"done", "main", "one"}),
    ("gbwp ", {"done", "main", "one"}),
    ("gwa ", set()),
]


@pytest.mark.parametrize("shell", ["fish", "zsh"])
@pytest.mark.parametrize("prefix,wanted", CANDIDATES)
def test_each_form_offers_its_commands_candidates(world, shell, prefix, wanted):
    world.worktree("one")
    world.merged_branch("done")
    run = _fish_complete if shell == "fish" else _zsh_complete
    assert {w for w in run(world, prefix) if not w.startswith("-")} == wanted
