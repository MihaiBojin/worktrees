"""The shell functions, and the rule that keeps them honest.

A function of a given name beats a binary of that name in every shell, and
in fish it wins inside `fish -c` too. So a shim carrying any logic is logic
a script cannot reach. These assert it carries none.
"""

from __future__ import annotations

import os
import pty
import shutil
import subprocess
import tomllib
from pathlib import Path
from typing import NoReturn

import pytest

ROOT = Path(__file__).resolve().parents[1]
CD_COMMANDS = ("gwa", "gwl", "gwm", "gwr")


def scripts() -> dict[str, str]:
    return tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["scripts"]


def test_every_shim_names_a_console_script() -> None:
    """A shim left behind after its binary was renamed shadows nothing and
    reports nothing; it just stops working."""
    declared = scripts()
    for f in sorted((ROOT / "functions").glob("*.fish")):
        assert f.stem in declared, f"{f.name} has no console script"
    for f in sorted((ROOT / "zsh/plugins/worktrees/functions").iterdir()):
        assert f.name in declared, f"{f} has no console script"


def test_the_two_dialects_ship_the_same_names() -> None:
    fish = {f.stem for f in (ROOT / "functions").glob("*.fish")}
    zsh = {f.name for f in (ROOT / "zsh/plugins/worktrees/functions").iterdir()}
    assert fish == zsh == set(CD_COMMANDS)


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
                assert f"command {other}" not in body, f"{path} calls {other}"


@pytest.mark.parametrize("name", CD_COMMANDS)
def test_a_shim_is_ten_lines_of_shell_or_fewer(name: str) -> None:
    """Whatever grows past that is logic, and logic belongs in the CLI."""
    for path in (
        ROOT / "functions" / f"{name}.fish",
        ROOT / "zsh/plugins/worktrees/functions" / name,
    ):
        code = [
            line
            for line in path.read_text().splitlines()
            if line.strip() and not line.strip().startswith("#")
        ]
        assert len(code) <= 10, f"{path} is {len(code)} lines"


def test_no_shim_exists_for_a_command_that_does_not_cd() -> None:
    """gws, gwp, gwnb and gwrot answer the same from a prompt and a script,
    and a function would make those two different programs."""
    shimmed = {f.stem for f in (ROOT / "functions").glob("*.fish")}
    for name in ("gws", "gwp", "gwnb", "gwrot", "gw", "worktrees"):
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
    code, out = _run_fish(bin_dir, "gwa; pwd")
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


def _run_zsh(bin_dir: Path, line: str) -> tuple[int, str]:
    zsh = shutil.which("zsh")
    if not zsh:  # pragma: no cover
        _missing("zsh")
    env = {**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}"}
    fns = ROOT / "zsh/plugins/worktrees/functions"
    proc = subprocess.run(
        [zsh, "-f", "-c", f"fpath=({fns} $fpath); autoload -Uz gwa; {line}"],
        capture_output=True,
        text=True,
        env=env,
    )
    return proc.returncode, proc.stdout + proc.stderr


def test_the_zsh_shim_lands_the_caller_in_the_printed_directory(tmp_path) -> None:
    target = tmp_path / "landing-zsh"
    target.mkdir()
    bin_dir = _fake(tmp_path, "gwa", f'#!/bin/sh\nprintf "%s\\n" "{target}"\n')
    code, out = _run_zsh(bin_dir, "gwa; pwd")
    assert code == 0, out
    assert str(target.resolve()) in out


def test_the_zsh_shim_returns_the_binarys_exit_code(tmp_path) -> None:
    bin_dir = _fake(tmp_path, "gwa", "#!/bin/sh\nexit 3\n")
    code, _ = _run_zsh(bin_dir, "gwa")
    assert code == 3


def test_the_zsh_shim_keeps_a_path_holding_a_newline_whole(tmp_path) -> None:
    """`$(...)` strips only trailing newlines, so zsh needs no collect step."""
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
