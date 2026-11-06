"""The shell functions, and the rule that keeps them honest.

A function of a given name beats a binary of that name in every shell, and
in fish it wins inside `fish -c` too. So a shim carrying any logic is logic
a script cannot reach. These assert it carries none.
"""

from __future__ import annotations

import os
import pty
import re
import shlex
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path
from typing import NoReturn

import pytest
from conftest import World, env_for

ROOT = Path(__file__).resolve().parents[1]
CD_COMMANDS = ("gw", "gwa", "gwm", "gwr")
# A shortcut calls a command with its flags fixed, and nothing else.
SHORTCUTS = {
    "gwr!": "gwr --delete ignored-files",
    "gwp!": "gwp --yes",
    "gwp!!": "gwp --delete all --yes",
}


def scripts() -> dict[str, str]:
    return tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["scripts"]


def test_every_shim_names_a_console_script() -> None:
    """A shim left behind after its binary was renamed shadows nothing and
    reports nothing; it just stops working."""
    declared = scripts()
    for f in sorted((ROOT / "functions").glob("*.fish")):
        assert f.stem in declared or f.stem in SHORTCUTS, (
            f"{f.name} has no console script"
        )
    for f in sorted((ROOT / "zsh/plugins/worktrees/functions").iterdir()):
        assert f.name in declared or f.name in SHORTCUTS, f"{f} has no console script"


def test_the_two_dialects_ship_the_same_names() -> None:
    fish = {f.stem for f in (ROOT / "functions").glob("*.fish")}
    zsh = {f.name for f in (ROOT / "zsh/plugins/worktrees/functions").iterdir()}
    assert fish == zsh == set(CD_COMMANDS) | set(SHORTCUTS)


@pytest.mark.parametrize("name", SHORTCUTS)
def test_a_shortcut_is_one_call_to_a_command(name: str) -> None:
    """By its plain name, not `command`, so a shortcut to a shim still lands
    where the binary says. Anything more is logic, and logic belongs in the
    CLI."""
    call = SHORTCUTS[name]
    assert call.split()[0] in scripts()
    assert name not in scripts()
    for path, line in (
        (ROOT / "functions" / f"{name}.fish", f"{call} $argv"),
        (ROOT / "zsh/plugins/worktrees/functions" / name, f'{call} "$@"'),
    ):
        code = [
            stripped
            for stripped in (raw.strip() for raw in path.read_text().splitlines())
            if stripped
            and not stripped.startswith("#")
            and not stripped.startswith("function ")
            and stripped != "end"
        ]
        assert code == [line], path


@pytest.mark.parametrize("name", CD_COMMANDS)
def test_a_shim_calls_its_own_binary_and_nothing_else(name: str) -> None:
    """`command` is what stops the function calling itself. Without it the
    shim recurses until the shell gives up."""
    for path in (
        ROOT / "functions" / f"{name}.fish",
        ROOT / "zsh/plugins/worktrees/functions" / name,
    ):
        body = path.read_text()
        assert f"command {name}" in body, path
        for other in CD_COMMANDS:
            if other != name:
                assert not re.search(rf"command {other}\b", body), (
                    f"{path} calls {other}"
                )


@pytest.mark.parametrize("name", CD_COMMANDS)
def test_adapters_stay_small(name: str) -> None:
    """Allow exact output capture and CLI dispatch without hiding operations."""
    for path in (
        ROOT / "functions" / f"{name}.fish",
        ROOT / "zsh/plugins/worktrees/functions" / name,
    ):
        code = [
            line
            for line in path.read_text().splitlines()
            if line.strip() and not line.strip().startswith("#")
        ]
        limit = 25 if name == "gw" else 18
        assert len(code) <= limit, f"{path} is {len(code)} lines"


def test_no_shim_exists_for_a_command_that_does_not_cd() -> None:
    """Only navigation commands and shortcuts need shell functions."""
    shimmed = {f.stem for f in (ROOT / "functions").glob("*.fish")}
    for name in ("gwp", "gwbc", "gwrot", "worktrees"):
        assert name not in shimmed


# --------------------------------------------------------------------------
# driven, in the shells themselves
# --------------------------------------------------------------------------


def _fake(tmp_path: Path, name: str, script: str) -> Path:
    """A stand-in binary, so the shim is what is under test."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    exe = bin_dir / name
    exe.write_text(script)
    exe.chmod(0o755)
    return bin_dir


def _missing(shell: str) -> NoReturn:  # pragma: no cover
    """Skip locally, fail in CI.

    A skip is invisible in `pytest -q`, so a runner without the shell went
    green without the shim ever running. A checkout on a machine with one
    shell and not the other still skips.
    """
    if os.environ.get("CI"):
        pytest.fail(f"{shell} is not installed, and CI has to run its shim")
    pytest.skip(f"{shell} is not installed")


def _run_fish(bin_dir: Path, line: str) -> tuple[int, str]:
    fish = shutil.which("fish")
    if not fish:  # pragma: no cover
        _missing("fish")
    env = {**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}"}
    proc = subprocess.run(
        [
            fish,
            "--no-config",
            "-c",
            f"source {ROOT / 'functions' / 'gwa.fish'}; {line}",
        ],
        capture_output=True,
        text=True,
        env=env,
    )
    return proc.returncode, proc.stdout + proc.stderr


def test_the_fish_shim_lands_the_caller_in_the_printed_directory(tmp_path) -> None:
    target = tmp_path / "landing"
    target.mkdir()
    bin_dir = _fake(tmp_path, "gwa", f'#!/bin/sh\nprintf "%s\\n" "{target}"\n')
    code, out = _run_fish(bin_dir, "gwa >/dev/null; pwd")
    assert code == 0, out
    assert str(target.resolve()) in out


def test_the_fish_shim_keeps_a_path_holding_a_newline_whole(tmp_path) -> None:
    """fish splits command substitution on newlines. `string collect` is why
    this arrives as one directory rather than two."""
    target = tmp_path / "we\nird"
    target.mkdir()
    bin_dir = _fake(tmp_path, "gwa", f'#!/bin/sh\nprintf "%s\\n" "{target}"\n')
    code, out = _run_fish(bin_dir, "gwa; test -d . ; and echo LANDED")
    assert code == 0, out
    assert "LANDED" in out


def test_the_fish_shim_returns_the_binarys_exit_code(tmp_path) -> None:
    """The pipeline's own $status belongs to `string collect`, which returns
    1 when it collected nothing. $pipestatus[1] is the command's."""
    bin_dir = _fake(tmp_path, "gwa", '#!/bin/sh\necho "went wrong" >&2\nexit 3\n')
    _, out = _run_fish(bin_dir, "gwa; echo code=$status")
    assert "code=3" in out, out


def test_the_fish_shim_stays_put_on_empty_output(tmp_path) -> None:
    """gwr prints nothing when the caller should not move."""
    bin_dir = _fake(tmp_path, "gwa", "#!/bin/sh\nexit 0\n")
    code, out = _run_fish(bin_dir, "cd /; gwa; pwd")
    assert code == 0, out
    assert out.strip().endswith("/")


def test_the_fish_shortcut_lands_where_gwr_says_with_the_flag(tmp_path) -> None:
    """stdout goes to /dev/null, so only `pwd` can print the target: a
    shortcut that skipped the shim would print it and stay put."""
    target = tmp_path / "landing-bang"
    target.mkdir()
    seen = tmp_path / "argv"
    bin_dir = _fake(
        tmp_path, "gwr", f'#!/bin/sh\necho "$@" > "{seen}"\nprintf "%s\\n" "{target}"\n'
    )
    fns = ROOT / "functions"
    code, out = _run_fish(
        bin_dir,
        f"source {fns / 'gwr.fish'}; source '{fns / 'gwr!.fish'}'; "
        "gwr! x >/dev/null; pwd",
    )
    assert code == 0, out
    assert str(target.resolve()) in out
    assert seen.read_text().split() == ["--delete", "ignored-files", "x"]


def _run_zsh(bin_dir: Path, line: str) -> tuple[int, str]:
    zsh = shutil.which("zsh")
    if not zsh:  # pragma: no cover
        _missing("zsh")
    env = {**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}"}
    fns = ROOT / "zsh/plugins/worktrees/functions"
    proc = subprocess.run(
        [
            zsh,
            "-f",
            "-c",
            f"fpath=({fns} $fpath); autoload -Uz gwa gwr 'gwr!' 'gwp!'; {line}",
        ],
        capture_output=True,
        text=True,
        env=env,
    )
    return proc.returncode, proc.stdout + proc.stderr


def test_the_zsh_shim_lands_the_caller_in_the_printed_directory(tmp_path) -> None:
    target = tmp_path / "landing-zsh"
    target.mkdir()
    bin_dir = _fake(tmp_path, "gwa", f'#!/bin/sh\nprintf "%s\\n" "{target}"\n')
    code, out = _run_zsh(bin_dir, "gwa >/dev/null; pwd")
    assert code == 0, out
    assert str(target.resolve()) in out


def test_the_zsh_shortcut_lands_where_gwr_says_with_the_flag(tmp_path) -> None:
    target = tmp_path / "landing-bang-zsh"
    target.mkdir()
    seen = tmp_path / "argv"
    bin_dir = _fake(
        tmp_path, "gwr", f'#!/bin/sh\necho "$@" > "{seen}"\nprintf "%s\\n" "{target}"\n'
    )
    code, out = _run_zsh(bin_dir, "gwr! x >/dev/null; pwd")
    assert code == 0, out
    assert str(target.resolve()) in out
    assert seen.read_text().split() == ["--delete", "ignored-files", "x"]


@pytest.mark.parametrize("shell", ["fish", "zsh"])
def test_gwp_bang_runs_gwp_with_its_flags_and_its_exit_code(tmp_path, shell) -> None:
    seen = tmp_path / "argv"
    bin_dir = _fake(tmp_path, "gwp", f'#!/bin/sh\necho "$@" > "{seen}"\nexit 4\n')
    if shell == "fish":
        _, out = _run_fish(
            bin_dir,
            f"source '{ROOT / 'functions' / 'gwp!.fish'}'; "
            "gwp! --branch x; echo code=$status",
        )
    else:
        _, out = _run_zsh(bin_dir, "gwp! --branch x; echo code=$?")
    assert "code=4" in out, out
    assert seen.read_text().split() == [
        "--yes",
        "--branch",
        "x",
    ]


def test_the_zsh_shim_returns_the_binarys_exit_code(tmp_path) -> None:
    bin_dir = _fake(tmp_path, "gwa", "#!/bin/sh\nexit 3\n")
    code, _ = _run_zsh(bin_dir, "gwa")
    assert code == 3


def test_the_zsh_shim_keeps_a_path_holding_a_newline_whole(tmp_path) -> None:
    """The sentinel preserves the line feeds inside a destination."""
    target = tmp_path / "we\nird-zsh"
    target.mkdir()
    bin_dir = _fake(tmp_path, "gwa", f'#!/bin/sh\nprintf "%s\\n" "{target}"\n')
    code, out = _run_zsh(bin_dir, "gwa; test -d . && echo LANDED")
    assert code == 0, out
    assert "LANDED" in out


def test_a_real_terminal_is_not_required(tmp_path) -> None:
    """The shim is a pipe around the binary either way; this drives it on a
    pty so the interactive path is exercised too."""
    fish = shutil.which("fish")
    if not fish:  # pragma: no cover
        _missing("fish")
    target = tmp_path / "pty-landing"
    target.mkdir()
    bin_dir = _fake(tmp_path, "gwa", f'#!/bin/sh\nprintf "%s\\n" "{target}"\n')

    pid, fd = pty.fork()
    if pid == 0:  # pragma: no cover
        os.environ["PATH"] = f"{bin_dir}:{os.environ['PATH']}"
        os.environ["TERM"] = "dumb"
        os.execv(
            fish,
            [
                fish,
                "--no-config",
                "-c",
                f"source {ROOT / 'functions' / 'gwa.fish'}; gwa; pwd",
            ],
        )
    out = b""
    while True:
        try:
            chunk = os.read(fd, 65536)
        except OSError:
            break
        if not chunk:
            break
        out += chunk
    os.waitpid(pid, 0)
    assert str(target.resolve()) in out.decode(errors="replace")


def test_the_fish_shim_prints_output_that_is_not_a_directory(tmp_path) -> None:
    """`--json` and `--list` print on the same stream a destination arrives
    on. The shim cannot tell them apart by looking, so it asks whether what
    it has is somewhere to go, and prints it when it is not.
    """
    payload = '{"path": "/nowhere", "branch": "x"}'
    bin_dir = _fake(tmp_path, "gwa", f"#!/bin/sh\nprintf '%s\\n' '{payload}'\n")
    code, out = _run_fish(bin_dir, "cd /; gwa --json; pwd")
    assert code == 0, out
    assert out.splitlines() == [payload, "/"], out


def test_the_fish_shim_prints_a_multi_line_listing_whole(tmp_path) -> None:
    listing = "* main   /one\\n  other  /two"
    bin_dir = _fake(tmp_path, "gwa", f"#!/bin/sh\nprintf '%b\\n' '{listing}'\n")
    code, out = _run_fish(bin_dir, "cd /; gwa --list; pwd")
    assert code == 0, out
    assert out.splitlines() == ["* main   /one", "  other  /two", "/"], out


def test_the_zsh_shim_prints_output_that_is_not_a_directory(tmp_path) -> None:
    payload = '{"path": "/nowhere", "branch": "x"}'
    bin_dir = _fake(tmp_path, "gwa", f"#!/bin/sh\nprintf '%s\\n' '{payload}'\n")
    code, out = _run_zsh(bin_dir, "cd /; gwa --json; pwd")
    assert code == 0, out
    assert out.splitlines() == [payload, "/"], out


@pytest.mark.parametrize(
    ("shell", "plugin"),
    [
        ("fish", ROOT / "conf.d" / "worktrees.fish"),
        ("zsh", ROOT / "zsh/plugins/worktrees/worktrees.plugin.zsh"),
    ],
)
def test_each_plugin_tells_the_binary_its_release(shell: str, plugin: Path) -> None:
    """Through the environment, so only an exported variable arrives: a fish
    `set -g` or a bare zsh assignment stays in the shell, and the binary then
    reports no plugin at all."""
    binary = shutil.which(shell)
    if not binary:  # pragma: no cover
        _missing(shell)
    gw = shlex.join(
        [
            sys.executable,
            "-c",
            "from worktrees.cli import gw; raise SystemExit(gw())",
            "version",
        ]
    )
    no_config = "--no-config" if shell == "fish" else "-f"
    env = {k: v for k, v in os.environ.items() if k != "WORKTREES_PLUGIN_VERSION"}
    proc = subprocess.run(
        [binary, no_config, "-c", f"source {shlex.quote(str(plugin))}; {gw}"],
        capture_output=True,
        text=True,
        env=env,
    )
    assert proc.returncode == 0, proc.stderr
    assert "the same release as these commands" in proc.stderr, proc.stderr


# --------------------------------------------------------------------------
# a line printed to be pasted lands in whichever shell the reader has
# --------------------------------------------------------------------------


@pytest.mark.parametrize("shell", ["fish", "zsh"])
def test_a_restore_line_means_the_same_in_every_shell(world, shell: str) -> None:
    """`shlex.quote` writes POSIX single quotes, which fish reads literally
    too, so `gwp` prints one line rather than one per shell."""
    from worktrees import prune

    name = "weird>pwned"  # unquoted, the shell redirects and git gets `weird`
    world.git("branch", name, "main")
    sha = world.git("rev-parse", f"refs/heads/{name}")
    line = prune.restore_line(name)
    world.git("branch", "--delete", "--force", name)

    binary = shutil.which(shell)
    if not binary:  # pragma: no cover
        _missing(shell)
    no_config = "--no-config" if shell == "fish" else "-f"
    argv = [binary, no_config, "-c", line]
    proc = subprocess.run(argv, cwd=world.repo, capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    assert world.git("rev-parse", f"refs/heads/{name}") == sha
    assert not (world.repo / "pwned").exists()


# Navigation uses the real CLI; stand-in binaries isolate output transport.
def _drive(
    shell: str,
    bin_dir: Path,
    words: list[str],
    at: Path,
    env: dict[str, str] | None = None,
    answer: bytes | None = None,
) -> tuple[subprocess.CompletedProcess[bytes], bytes]:
    binary = shutil.which(shell)
    if not binary:
        _missing(shell)
    cwd_file = bin_dir.parent / "shell-cwd"
    if shell == "fish":
        setup = "; ".join(
            f"source {shlex.quote(str(path))}"
            for path in sorted((ROOT / "functions").glob("*.fish"))
        )
        save = "set -l code $status"
        finish = "exit $code"
        no_config = "--no-config"
    else:
        fns = ROOT / "zsh/plugins/worktrees/functions"
        setup = f"fpath=({fns} $fpath); autoload -Uz " + shlex.join(
            [path.name for path in sorted(fns.iterdir())]
        )
        save = "local code=$?"
        finish = "exit $code"
        no_config = "-f"
    line = (
        f"{setup}; {shlex.join(words)}; {save}; "
        f"printf '%s' \"$PWD\" > {shlex.quote(str(cwd_file))}; {finish}"
    )
    terminal = pty.openpty() if answer is not None else None
    if terminal:
        os.write(terminal[0], answer or b"")
    try:
        process = subprocess.run(
            [binary, no_config, "-c", line],
            cwd=at,
            env={
                **(env or os.environ),
                "PATH": f"{bin_dir}:{(env or os.environ)['PATH']}",
            },
            stdin=terminal[1] if terminal else subprocess.DEVNULL,
            capture_output=True,
        )
    finally:
        if terminal:
            os.close(terminal[0])
            os.close(terminal[1])
    return process, cwd_file.read_bytes()


def _cli_binaries(world: World) -> Path:
    bin_dir = world.root / "bin"
    bin_dir.mkdir()
    for name in ("gw", "gwr"):
        binary = bin_dir / name
        binary.write_text(
            f"#!{sys.executable}\nfrom worktrees.cli import {name}\n"
            f"raise SystemExit({name}())\n"
        )
        binary.chmod(0o755)
    return bin_dir


@pytest.mark.parametrize("shell", ["fish", "zsh"])
@pytest.mark.parametrize("query", ["fix-parser", "fxpr", "path"])
def test_gw_navigates_through_list_matching(world, shell, query):
    target = world.root / "path with spaces "
    world.git("worktree", "add", "-b", "fix-parser", str(target), "main")
    words = ["gw", str(target) if query == "path" else query]
    process, cwd = _drive(
        shell, _cli_binaries(world), words, world.repo, env_for(world), answer=b""
    )
    assert process.returncode == 0, process.stderr
    assert process.stdout == b""
    assert cwd == os.fsencode(target.resolve())


@pytest.mark.parametrize("shell", ["fish", "zsh"])
@pytest.mark.parametrize("name", ["version", "v"])
def test_gw_subcommands_take_precedence_and_list_is_the_escape(world, shell, name):
    target = world.worktree(name)
    bin_dir = _cli_binaries(world)
    process, cwd = _drive(shell, bin_dir, ["gw", name], world.repo, env_for(world))
    assert process.returncode == 0, process.stderr
    assert process.stdout
    assert cwd == os.fsencode(world.repo.resolve())
    process, cwd = _drive(
        shell, bin_dir, ["gw", "list", name], world.repo, env_for(world), answer=b""
    )
    assert process.returncode == 0, process.stderr
    assert cwd == os.fsencode(target.resolve())


@pytest.mark.parametrize("shell", ["fish", "zsh"])
@pytest.mark.parametrize("entry", ["gw"])
def test_ambiguous_navigation_lists_matching_paths_without_a_terminal(
    world, shell, entry
):
    targets = [world.worktree(name) for name in ("fix-one", "fix-two")]
    process, cwd = _drive(
        shell, _cli_binaries(world), [entry, "fix"], world.repo, env_for(world)
    )
    assert process.returncode == 0, process.stderr
    assert b"WORKTREE" in process.stdout
    assert all(os.fsencode(path.resolve()) in process.stdout for path in targets)
    assert cwd == os.fsencode(world.repo.resolve())


@pytest.mark.parametrize("shell", ["fish", "zsh"])
def test_gw_missing_name_does_not_move(world, shell):
    world.worktree("one")
    process, cwd = _drive(
        shell, _cli_binaries(world), ["gw", "absent"], world.repo, env_for(world)
    )
    assert process.returncode == 1, process.stderr
    assert b"no worktree matches" in process.stderr
    assert cwd == os.fsencode(world.repo.resolve())


@pytest.mark.parametrize("shell", ["fish", "zsh"])
@pytest.mark.parametrize("name", ["gw", "gwr", "gwa", "gwm"])
@pytest.mark.parametrize("suffix", [" space \t", "line\nbreak", "final\n"])
def test_navigation_preserves_exact_destination_bytes(tmp_path, shell, name, suffix):
    target = tmp_path / suffix
    target.mkdir()
    binary = (
        f"#!{sys.executable}\nimport os, sys\n"
        "if sys.argv[1:] == ['--complete']:\n"
        "    print('version\\tversion')\n"
        "else:\n"
        f"    os.write(1, {os.fsencode(target.resolve())!r} + b'\\n')\n"
    )
    bin_dir = _fake(tmp_path, name, binary)
    process, cwd = _drive(shell, bin_dir, [name, "target"], tmp_path, answer=b"")
    assert process.returncode == 0, process.stderr
    assert process.stdout == b""
    assert cwd == os.fsencode(target.resolve())


@pytest.mark.parametrize("shell", ["fish", "zsh"])
def test_gwr_lands_after_partial_failure_and_keeps_the_exit_code(tmp_path, shell):
    target = tmp_path / "main\n"
    target.mkdir()
    bin_dir = _fake(
        tmp_path,
        "gwr",
        f"#!/bin/sh\nprintf '%s\\n' {shlex.quote(str(target))}\nexit 4\n",
    )
    process, cwd = _drive(shell, bin_dir, ["gwr", "current"], tmp_path)
    assert process.returncode == 4, process.stderr
    assert process.stdout == b""
    assert cwd == os.fsencode(target.resolve())


@pytest.mark.parametrize("shell", ["fish", "zsh"])
@pytest.mark.parametrize("name", ["gw", "gwr", "gwa", "gwm"])
def test_adapter_preserves_data_output_on_failure(tmp_path, shell, name):
    payload = b'{"error": "cleanup failed"}\n\n'
    bin_dir = _fake(
        tmp_path,
        name,
        f"#!{sys.executable}\nimport os\nos.write(1, {payload!r})\n"
        "raise SystemExit(4)\n",
    )
    process, cwd = _drive(shell, bin_dir, [name, "--json"], tmp_path)
    assert process.returncode == 4, process.stderr
    assert process.stdout == payload
    assert cwd == os.fsencode(tmp_path.resolve())


@pytest.mark.parametrize("shell", ["fish", "zsh"])
@pytest.mark.parametrize(
    "words",
    [
        ["--help"],
        ["--json"],
        ["--complete"],
        ["ls"],
        ["custom"],
    ],
)
def test_gw_only_navigates_for_one_name_outside_the_cli_command_list(
    tmp_path, shell, words
):
    target = tmp_path / "target"
    target.mkdir()
    binary = (
        f"#!{sys.executable}\nimport sys\n"
        "if sys.argv[1:] == ['--complete']:\n"
        "    print('ls\\tlist\\ncustom\\tcustom')\n"
        f"else:\n    print({str(target)!r})\n"
    )
    bin_dir = _fake(tmp_path, "gw", binary)
    process, cwd = _drive(shell, bin_dir, ["gw", *words], tmp_path)
    assert process.returncode == 0, process.stderr
    assert process.stdout
    assert cwd == os.fsencode(tmp_path.resolve())


@pytest.mark.parametrize("shell", ["fish", "zsh"])
def test_gwp_double_bang_forwards_arguments_and_failure(tmp_path, shell):
    bin_dir = _fake(tmp_path, "gwp", '#!/bin/sh\nprintf "%s\\n" "$@"\nexit 4\n')
    process, _ = _drive(
        shell, bin_dir, ["gwp!!", "--only", "empty", "two words"], tmp_path
    )
    assert process.returncode == 4, process.stderr
    assert process.stdout.splitlines() == [
        b"--delete",
        b"all",
        b"--yes",
        b"--only",
        b"empty",
        b"two words",
    ]


@pytest.mark.parametrize("shell", ["fish", "zsh"])
@pytest.mark.parametrize("entry", ["gw"])
def test_navigation_reaches_a_real_main_path_ending_in_newline(world, shell, entry):
    target = world.root / "main\n"
    world.repo.rename(target)
    world.repo = target
    linked = world.worktree("topic")
    process, cwd = _drive(
        shell, _cli_binaries(world), [entry, "main"], linked, env_for(world), answer=b""
    )
    assert process.returncode == 0, process.stderr
    assert process.stdout == b""
    assert cwd == os.fsencode(target.resolve())


@pytest.mark.parametrize("shell", ["fish", "zsh"])
@pytest.mark.parametrize(
    "words",
    [
        [],
        ["--json"],
        ["--help"],
        ["--complete"],
        ["list", "one"],
        ["list", "--json"],
    ],
)
def test_real_gw_data_and_subcommands_keep_the_callers_directory(world, shell, words):
    target = world.worktree("one")
    process, cwd = _drive(
        shell, _cli_binaries(world), ["gw", *words], world.repo, env_for(world)
    )
    assert process.returncode == 0, process.stderr
    assert process.stdout or words == ["--help"]
    assert cwd == os.fsencode(world.repo.resolve())
    if words == ["list", "one"]:
        assert b"WORKTREE" in process.stdout and os.fsencode(target) in process.stdout


@pytest.mark.parametrize("shell", ["fish", "zsh"])
def test_gwp_double_bang_help_names_every_deletion_category(world, shell):
    bin_dir = _cli_binaries(world)
    _fake(
        world.root,
        "gwp",
        f"#!{sys.executable}\nfrom worktrees.cli import gwp\nraise SystemExit(gwp())\n",
    )
    process, cwd = _drive(
        shell, bin_dir, ["gwp!!", "--help"], world.repo, env_for(world)
    )
    assert process.returncode == 0, process.stderr
    assert (
        b"gwp!! deletes empty branches, ignored files and branch stashes"
        in process.stdout
    )
    assert cwd == os.fsencode(world.repo.resolve())


@pytest.mark.parametrize("shell", ["fish", "zsh"])
@pytest.mark.parametrize("entry", ["gw", "gwr"])
def test_json_after_the_option_separator_is_a_worktree_name(world, shell, entry):
    target = world.root / ".worktrees" / "--json" / "repo"
    target.parent.mkdir(parents=True)
    world.git("worktree", "add", "-b", "topic", str(target), "main")
    words = (
        ["gw", "list", "--", "--json"]
        if entry == "gw"
        else ["gwr", "--yes", "--delete", "empty", "--", "--json"]
    )
    process, cwd = _drive(
        shell,
        _cli_binaries(world),
        words,
        world.repo if entry == "gw" else target,
        env_for(world),
        answer=b"" if entry == "gw" else b"y\n",
    )
    assert process.returncode == 0, process.stderr
    assert process.stdout == b""
    assert cwd == os.fsencode((target if entry == "gw" else world.repo).resolve())
    assert target.exists() == (entry == "gw")


@pytest.mark.parametrize("shell", ["fish", "zsh"])
def test_real_gwr_json_does_not_move_the_caller(world, shell):
    target = world.worktree("topic")
    process, cwd = _drive(
        shell,
        _cli_binaries(world),
        ["gwr", "--json", "--keep-branch", "--yes", "topic"],
        world.repo,
        env_for(world),
    )
    assert process.returncode == 0, process.stderr
    assert process.stdout.startswith(b"{")
    assert cwd == os.fsencode(world.repo.resolve())
    assert not target.exists()


@pytest.mark.parametrize("shell", ["fish", "zsh"])
def test_ambiguous_gw_navigation_uses_the_list_picker(world, shell):
    world.worktree("fix-one")
    target = world.worktree("fix-two")
    process, cwd = _drive(
        shell,
        _cli_binaries(world),
        ["gw", "fix"],
        world.repo,
        env_for(world),
        answer=b"2",
    )
    assert process.returncode == 0, process.stderr
    assert process.stdout == b""
    assert b"which?" in process.stderr
    assert cwd == os.fsencode(target.resolve())


@pytest.mark.parametrize("shell", ["fish", "zsh"])
def test_json_is_forwarded_when_a_relative_directory_has_its_name(world, shell):
    bin_dir = _cli_binaries(world)
    payload = subprocess.check_output(
        [str(bin_dir / "gw"), "--json"], cwd=world.repo, env=env_for(world)
    )
    target = world.repo / os.fsdecode(payload.removesuffix(b"\n"))
    target.mkdir(parents=True)
    assert payload == subprocess.check_output(
        [str(bin_dir / "gw"), "--json"], cwd=world.repo, env=env_for(world)
    )
    process, cwd = _drive(shell, bin_dir, ["gw", "--json"], world.repo, env_for(world))
    assert process.returncode == 0, process.stderr
    assert process.stdout == payload
    assert cwd == os.fsencode(world.repo.resolve())
