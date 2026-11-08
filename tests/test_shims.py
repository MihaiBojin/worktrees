"""The shell functions, and the rule that keeps them honest.

A function of a given name beats a binary of that name in every shell, and
in fish it wins inside `fish -c` too. So a function carrying any logic is
logic a script cannot reach. `gw` carries one job, changing the caller's
directory, and every other function is a shortcut that calls it.
"""

from __future__ import annotations

import os
import pty
import re
import shlex
import shutil
import subprocess
import sys
from pathlib import Path
from typing import NoReturn

import pytest
from conftest import World, env_for

from worktrees.cli import _COMMANDS, command_rows, shortcuts

ROOT = Path(__file__).resolve().parents[1]
FISH = ROOT / "functions"
ZSH = ROOT / "zsh/plugins/worktrees/functions"
# Each shortcut and the words it runs, from the table `gw --help` prints.
SHORTCUTS = {
    name: words for path, c in command_rows() for name, words in shortcuts(path, c)
}
# The words after `gw` that land the caller somewhere.
LANDS = {word for path, c in command_rows() if c.lands for word in (path, *c.aliases)}
# Removal lands even when a later cleanup step failed.
REMOVES = {"remove", *_COMMANDS["remove"].aliases}


def test_every_function_is_gw_or_a_shortcut() -> None:
    """A function left behind after its shortcut was renamed shadows nothing
    and reports nothing; it just stops working."""
    fish = {f.stem for f in FISH.glob("*.fish")}
    zsh = {f.name for f in ZSH.iterdir()}
    assert fish == zsh == {"gw", *SHORTCUTS}


def test_the_zsh_plugin_autoloads_every_function() -> None:
    plugin = (ROOT / "zsh/plugins/worktrees/worktrees.plugin.zsh").read_text()
    line = next(ln for ln in plugin.splitlines() if ln.startswith("autoload -Uz "))
    assert set(shlex.split(line)[2:]) == {f.name for f in ZSH.iterdir()}


@pytest.mark.parametrize("name", SHORTCUTS)
def test_a_shortcut_is_one_call_to_the_gw_function(name: str) -> None:
    """By its plain name, not `command gw`, so a shortcut to a command that
    lands still lands. Anything more is logic, and logic belongs in the CLI.
    fish's `--wraps` hands it the completions of the words it runs."""
    run = " ".join(SHORTCUTS[name])
    assert (FISH / f"{name}.fish").read_text() == (
        f"function {name} --wraps '{run}' --description '{run}'\n    {run} $argv\nend\n"
    )
    assert (ZSH / name).read_text() == f'{run} "$@"\n'


def test_gw_calls_its_own_binary_and_nothing_else() -> None:
    """`command` is what stops the function calling itself. Without it the
    function recurses until the shell gives up."""
    for path in (FISH / "gw.fish", ZSH / "gw"):
        code = "\n".join(
            line
            for line in path.read_text().splitlines()
            if not line.strip().startswith("#")
        )
        assert re.findall(r"\bcommand (\S+)", code) == ["gw", "gw"], path


def test_gw_lands_for_exactly_the_commands_that_land() -> None:
    fish = (FISH / "gw.fish").read_text()
    zsh = (ZSH / "gw").read_text()
    fish_cases = re.findall(r"^\s*case (.+)$", fish, re.M)
    zsh_cases = re.findall(r"^\s*([a-z|]+)\) ", zsh, re.M)
    assert {w for case in fish_cases for w in case.split()} == LANDS
    assert {w for case in zsh_cases for w in case.split("|")} == LANDS
    after_failure = re.search(r'contains -- "\$argv\[1\]" (.+)$', fish, re.M)
    assert after_failure and set(after_failure.group(1).split()) == REMOVES
    assert set(re.findall(r"\*:(\w+)", zsh)) == REMOVES


def test_the_gw_function_stays_small() -> None:
    """Exact output capture and one switch on the subcommand, nothing else."""
    for path in (FISH / "gw.fish", ZSH / "gw"):
        code = [
            line
            for line in path.read_text().splitlines()
            if line.strip() and not line.strip().startswith("#")
        ]
        assert len(code) <= 30, f"{path} is {len(code)} lines"


# --------------------------------------------------------------------------
# driven, in the shells themselves
# --------------------------------------------------------------------------


def _fake(tmp_path: Path, name: str, script: str) -> Path:
    """A stand-in binary, so the function is what is under test."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    exe = bin_dir / name
    exe.write_text(script)
    exe.chmod(0o755)
    return bin_dir


def _missing(shell: str) -> NoReturn:  # pragma: no cover
    """Skip locally, fail in CI.

    A skip is invisible in `pytest -q`, so a runner without the shell went
    green without the function ever running. A checkout on a machine with one
    shell and not the other still skips.
    """
    if os.environ.get("CI"):
        pytest.fail(f"{shell} is not installed, and CI has to run its functions")
    pytest.skip(f"{shell} is not installed")


def _setup(shell: str) -> str:
    """Every function the plugin ships, defined the way the plugin does."""
    if shell == "fish":
        return "; ".join(
            f"source {shlex.quote(str(path))}" for path in sorted(FISH.glob("*.fish"))
        )
    return f"fpath=({ZSH} $fpath); autoload -Uz " + shlex.join(
        [path.name for path in sorted(ZSH.iterdir())]
    )


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
    save = "set -l code $status" if shell == "fish" else "local code=$?"
    no_config = "--no-config" if shell == "fish" else "-f"
    line = (
        f"{_setup(shell)}; {shlex.join(words)}; {save}; "
        f"printf '%s' \"$PWD\" > {shlex.quote(str(cwd_file))}; exit $code"
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
    binary = bin_dir / "gw"
    binary.write_text(
        f"#!{sys.executable}\nfrom worktrees.cli import gw\nraise SystemExit(gw())\n"
    )
    binary.chmod(0o755)
    return bin_dir


def _printing(tmp_path: Path, target: Path, code: int = 0) -> Path:
    """A gw that prints `target` as its destination and exits `code`."""
    return _fake(
        tmp_path,
        "gw",
        f"#!{sys.executable}\nimport os\n"
        f"os.write(1, {os.fsencode(target.resolve())!r} + b'\\n')\n"
        f"raise SystemExit({code})\n",
    )


@pytest.mark.parametrize("shell", ["fish", "zsh"])
@pytest.mark.parametrize("name", SHORTCUTS)
def test_a_shortcut_forwards_its_words_arguments_and_exit_code(tmp_path, shell, name):
    bin_dir = _fake(tmp_path, "gw", '#!/bin/sh\nprintf "%s\\n" "$@"\nexit 4\n')
    process, cwd = _drive(shell, bin_dir, [name, "--only", "two words"], tmp_path)
    assert process.returncode == 4, process.stderr
    assert process.stdout.decode().splitlines() == [
        *SHORTCUTS[name][1:],
        "--only",
        "two words",
    ]
    assert cwd == os.fsencode(tmp_path.resolve())


@pytest.mark.parametrize("shell", ["fish", "zsh"])
@pytest.mark.parametrize(
    "words",
    [
        ["gw", "list", "x"],
        ["gw", "add", "x"],
        ["gw", "a", "x"],
        ["gwa", "x"],
        ["gw", "remove", "x"],
        ["gw", "rm", "x"],
        ["gwr", "x"],
        ["gwr!", "x"],
    ],
)
@pytest.mark.parametrize("suffix", [" space \t", "line\nbreak", "final\n"])
def test_gw_lands_on_the_exact_destination_bytes(tmp_path, shell, words, suffix):
    """fish splits command substitution on newlines and zsh strips trailing
    ones; `string collect -N` and the sentinel are why this arrives whole."""
    target = tmp_path / suffix
    target.mkdir()
    bin_dir = _printing(tmp_path, target)
    process, cwd = _drive(shell, bin_dir, words, tmp_path, answer=b"")
    assert process.returncode == 0, process.stderr
    assert process.stdout == b""
    assert cwd == os.fsencode(target.resolve())


@pytest.mark.parametrize("shell", ["fish", "zsh"])
@pytest.mark.parametrize(
    "words", [["gwr", "current"], ["gw", "rm", "current"], ["gwr!", "current"]]
)
def test_remove_lands_after_partial_failure_and_keeps_the_exit_code(
    tmp_path, shell, words
):
    target = tmp_path / "main\n"
    target.mkdir()
    process, cwd = _drive(shell, _printing(tmp_path, target, 4), words, tmp_path)
    assert process.returncode == 4, process.stderr
    assert process.stdout == b""
    assert cwd == os.fsencode(target.resolve())


@pytest.mark.parametrize("shell", ["fish", "zsh"])
@pytest.mark.parametrize("words", [["gwa", "x"], ["gw", "add", "x"], ["gw", "list"]])
def test_add_and_list_stay_put_when_the_command_fails(tmp_path, shell, words):
    target = tmp_path / "landing"
    target.mkdir()
    bin_dir = _printing(tmp_path, target, 1)
    process, cwd = _drive(shell, bin_dir, words, tmp_path, answer=b"")
    assert process.returncode == 1, process.stderr
    assert process.stdout == os.fsencode(target.resolve()) + b"\n"
    assert cwd == os.fsencode(tmp_path.resolve())


@pytest.mark.parametrize("shell", ["fish", "zsh"])
def test_gw_stays_put_on_empty_output(tmp_path, shell):
    """remove prints nothing when the caller should not move."""
    bin_dir = _fake(tmp_path, "gw", "#!/bin/sh\nexit 0\n")
    process, cwd = _drive(shell, bin_dir, ["gwr", "x"], tmp_path)
    assert process.returncode == 0, process.stderr
    assert process.stdout == b""
    assert cwd == os.fsencode(tmp_path.resolve())


@pytest.mark.parametrize("shell", ["fish", "zsh"])
@pytest.mark.parametrize(
    "payload",
    [b'{"path": "/nowhere", "branch": "x"}\n', b"* main   /one\n  other  /two\n"],
)
def test_gw_prints_captured_output_that_is_not_a_directory(tmp_path, shell, payload):
    """The function cannot tell a destination from data by looking, so it
    asks whether what it has is somewhere to go, and prints it when not."""
    bin_dir = _fake(
        tmp_path, "gw", f"#!{sys.executable}\nimport os\nos.write(1, {payload!r})\n"
    )
    process, cwd = _drive(shell, bin_dir, ["gwa", "x"], tmp_path)
    assert process.returncode == 0, process.stderr
    assert process.stdout == payload
    assert cwd == os.fsencode(tmp_path.resolve())


@pytest.mark.parametrize("shell", ["fish", "zsh"])
@pytest.mark.parametrize(
    "words", [["gw", "list", "--json"], ["gwr", "--json"], ["gwa", "--json"]]
)
def test_data_output_passes_through_on_failure(tmp_path, shell, words):
    payload = b'{"error": "cleanup failed"}\n\n'
    bin_dir = _fake(
        tmp_path,
        "gw",
        f"#!{sys.executable}\nimport os\nos.write(1, {payload!r})\n"
        "raise SystemExit(4)\n",
    )
    process, cwd = _drive(shell, bin_dir, words, tmp_path)
    assert process.returncode == 4, process.stderr
    assert process.stdout == payload
    assert cwd == os.fsencode(tmp_path.resolve())


@pytest.mark.parametrize("shell", ["fish", "zsh"])
@pytest.mark.parametrize(
    "words",
    [
        [],
        ["--help"],
        ["--json"],
        ["--complete"],
        ["ls"],
        ["prune"],
        ["rotate"],
        ["version"],
        ["list", "x"],
        ["list", "--json"],
        ["add", "--json"],
        ["add", "x", "--help"],
        ["rm", "-h"],
        ["remove", "--complete"],
    ],
)
def test_gw_moves_only_for_a_command_that_lands(tmp_path, shell, words):
    """Without a terminal on stdin, `list` prints metadata rather than
    picking, so it is data too."""
    target = tmp_path / "target"
    target.mkdir()
    process, cwd = _drive(shell, _printing(tmp_path, target), ["gw", *words], tmp_path)
    assert process.returncode == 0, process.stderr
    assert process.stdout == os.fsencode(target.resolve()) + b"\n"
    assert cwd == os.fsencode(tmp_path.resolve())


def test_a_real_terminal_is_not_required(tmp_path) -> None:
    """The function is a pipe around the binary either way; this drives it
    on a pty so the interactive path is exercised too."""
    fish = shutil.which("fish")
    if not fish:  # pragma: no cover
        _missing("fish")
    target = tmp_path / "pty-landing"
    target.mkdir()
    bin_dir = _printing(tmp_path, target)

    # Popen rather than pty.fork: a pytest-xdist worker runs threads, and a
    # fork that goes on to run Python can deadlock on a lock one of them held.
    parent, child = pty.openpty()
    proc = subprocess.Popen(
        [
            fish,
            "--no-config",
            "-c",
            f"{_setup('fish')}; gwa x; pwd",
        ],
        stdin=child,
        stdout=child,
        stderr=child,
        env={**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}", "TERM": "dumb"},
    )
    os.close(child)
    out = b""
    while True:
        try:
            chunk = os.read(parent, 65536)
        except OSError:
            break
        if not chunk:
            break
        out += chunk
    proc.wait()
    os.close(parent)
    assert str(target.resolve()) in out.decode(errors="replace")


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
    too, so `gw prune` prints one line rather than one per shell."""
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


# --------------------------------------------------------------------------
# the real CLI behind the functions
# --------------------------------------------------------------------------


@pytest.mark.parametrize("shell", ["fish", "zsh"])
@pytest.mark.parametrize("query", ["fix-parser", "fxpr", "path"])
def test_gw_navigates_through_list_matching(world, shell, query):
    target = world.root / "path with spaces "
    world.git("worktree", "add", "-b", "fix-parser", str(target), "main")
    words = ["gw", "list", str(target) if query == "path" else query]
    process, cwd = _drive(
        shell, _cli_binaries(world), words, world.repo, env_for(world), answer=b""
    )
    assert process.returncode == 0, process.stderr
    assert process.stdout == b""
    assert cwd == os.fsencode(target.resolve())


@pytest.mark.parametrize("shell", ["fish", "zsh"])
@pytest.mark.parametrize("words", [["gw", "add"], ["gw", "a"], ["gwa"]])
def test_add_lands_in_the_new_worktree(world, shell, words):
    process, cwd = _drive(
        shell, _cli_binaries(world), [*words, "topic"], world.repo, env_for(world)
    )
    assert process.returncode == 0, process.stderr
    assert process.stdout == b""
    target = world.parent / ".worktrees" / "topic" / world.repo.name
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
def test_ambiguous_navigation_lists_matching_paths_without_a_terminal(world, shell):
    targets = [world.worktree(name) for name in ("fix-one", "fix-two")]
    process, cwd = _drive(
        shell, _cli_binaries(world), ["gw", "list", "fix"], world.repo, env_for(world)
    )
    assert process.returncode == 0, process.stderr
    assert b"WORKTREE" in process.stdout
    assert all(os.fsencode(path.resolve()) in process.stdout for path in targets)
    assert cwd == os.fsencode(world.repo.resolve())


@pytest.mark.parametrize("shell", ["fish", "zsh"])
def test_gw_missing_name_does_not_move(world, shell):
    world.worktree("one")
    process, cwd = _drive(
        shell,
        _cli_binaries(world),
        ["gw", "list", "absent"],
        world.repo,
        env_for(world),
    )
    assert process.returncode == 1, process.stderr
    assert b"no worktree matches" in process.stderr
    assert cwd == os.fsencode(world.repo.resolve())


@pytest.mark.parametrize("shell", ["fish", "zsh"])
def test_navigation_reaches_a_real_main_path_ending_in_newline(world, shell):
    target = world.root / "main\n"
    world.repo.rename(target)
    world.repo = target
    linked = world.worktree("topic")
    process, cwd = _drive(
        shell,
        _cli_binaries(world),
        ["gw", "list", "main"],
        linked,
        env_for(world),
        answer=b"",
    )
    assert process.returncode == 0, process.stderr
    assert process.stdout == b""
    assert cwd == os.fsencode(target.resolve())


@pytest.mark.parametrize("shell", ["fish", "zsh"])
@pytest.mark.parametrize(
    "words",
    [
        [],
        ["version"],
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
    assert process.stdout
    assert cwd == os.fsencode(world.repo.resolve())
    if words == ["list", "one"]:
        assert b"WORKTREE" in process.stdout and os.fsencode(target) in process.stdout


@pytest.mark.parametrize("shell", ["fish", "zsh"])
def test_a_shortcuts_help_names_what_it_runs(world, shell):
    process, cwd = _drive(
        shell, _cli_binaries(world), ["gwpa!", "--help"], world.repo, env_for(world)
    )
    assert process.returncode == 0, process.stderr
    assert b"gwpa! runs gw prune --delete all --yes" in b" ".join(
        process.stdout.split()
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
        ["gw", "list", "fix"],
        world.repo,
        env_for(world),
        answer=b"-two\r",
    )
    assert process.returncode == 0, process.stderr
    assert process.stdout == b""
    assert b"which?" in process.stderr
    assert cwd == os.fsencode(target.resolve())


@pytest.mark.parametrize("shell", ["fish", "zsh"])
def test_json_is_forwarded_when_a_relative_directory_has_its_name(world, shell):
    bin_dir = _cli_binaries(world)
    payload = subprocess.check_output(
        [str(bin_dir / "gw"), "list", "--json"], cwd=world.repo, env=env_for(world)
    )
    target = world.repo / os.fsdecode(payload.removesuffix(b"\n"))
    target.mkdir(parents=True)
    assert payload == subprocess.check_output(
        [str(bin_dir / "gw"), "list", "--json"], cwd=world.repo, env=env_for(world)
    )
    process, cwd = _drive(
        shell, bin_dir, ["gw", "list", "--json"], world.repo, env_for(world)
    )
    assert process.returncode == 0, process.stderr
    assert process.stdout == payload
    assert cwd == os.fsencode(world.repo.resolve())
