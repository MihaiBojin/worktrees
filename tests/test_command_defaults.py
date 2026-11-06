"""Default listing, navigation and the root command guide."""

import json
import os

import pytest
from conftest import env_for
from test_cli import run
from test_shims import _cli_binaries, _drive, _fake


@pytest.mark.parametrize("prefix", [(), ("list",)])
@pytest.mark.parametrize("suffix", [(), ("--list",), ("--json",)])
def test_default_listing_returns_all_matches_without_a_terminal(world, prefix, suffix):
    targets = [world.worktree(name) for name in ("fix-one", "fix-two")]
    p = run(world, "gw", *prefix, "fix", *suffix)
    assert p.returncode == 0, p.stderr
    if "--json" in suffix:
        payload = json.loads(p.stdout)
        assert set(payload) == {"head", "lastSync", "worktrees"}
        assert [row["branch"] for row in payload["worktrees"]] == ["fix-one", "fix-two"]
    else:
        assert "WORKTREE" in p.stdout
        assert all(str(path) in p.stdout for path in targets)
    assert "which?" not in p.stderr


@pytest.mark.parametrize("args", [(), ("list",)])
def test_default_without_a_query_lists_the_main_checkout(world, args):
    p = run(world, "gw", *args)
    assert p.returncode == 0, p.stderr
    assert "WORKTREE" in p.stdout and str(world.repo) in p.stdout
    assert "VERDICT" not in p.stdout


@pytest.mark.parametrize("name", ["l", "ls", "help", "h"])
def test_retired_command_names_are_worktree_queries(world, name):
    target = world.worktree(name)
    p = run(world, "gw", name, "--json")
    assert p.returncode == 0, p.stderr
    assert [row["path"] for row in json.loads(p.stdout)["worktrees"]] == [str(target)]


@pytest.mark.parametrize("flag", ["--yes", "--dry-run", "--delete", "--only"])
def test_default_listing_rejects_pruning_flags(world, flag):
    p = run(world, "gw", flag)
    assert p.returncode == 2
    assert "unrecognized arguments" in p.stderr


@pytest.mark.parametrize("entry", ["gw", "main"])
@pytest.mark.parametrize("flag", ["-h", "--help"])
def test_root_help_prints_the_full_guide_outside_a_repository(world, entry, flag):
    p = run(world, entry, flag, at=world.root)
    assert p.returncode == 0, p.stderr
    assert "COMMAND" in p.stdout and "GW" in p.stdout and "DOES" in p.stdout
    assert "gw list is the default" in p.stdout
    assert "gw list [QUERY]" in p.stdout
    assert "gwp!!" in p.stdout and "NO_COLOR" in p.stdout
    assert "gwl" not in p.stdout and "gwh" not in p.stdout
    assert "gw help" not in p.stdout


@pytest.mark.parametrize("shell", ["fish", "zsh"])
@pytest.mark.parametrize(
    "words,answer", [([], b"2"), (["list", "topic"], b""), (["topic", "--"], b"")]
)
def test_terminal_default_and_explicit_listing_change_directory(
    world, shell, words, answer
):
    target = world.worktree("topic")
    p, cwd = _drive(
        shell,
        _cli_binaries(world),
        ["gw", *words],
        world.repo,
        env_for(world),
        answer=answer,
    )
    assert p.returncode == 0, p.stderr
    assert p.stdout == b""
    assert cwd == os.fsencode(target.resolve())


@pytest.mark.parametrize("shell", ["fish", "zsh"])
@pytest.mark.parametrize(
    "words",
    [["topic"], ["list", "topic"], ["topic", "--json"], ["list", "topic", "--list"]],
)
def test_nonterminal_listing_keeps_the_callers_directory(world, shell, words):
    target = world.worktree("topic")
    p, cwd = _drive(
        shell, _cli_binaries(world), ["gw", *words], world.repo, env_for(world)
    )
    assert p.returncode == 0, p.stderr
    assert os.fsencode(target) in p.stdout
    assert p.stdout != os.fsencode(target) + b"\n"
    assert cwd == os.fsencode(world.repo.resolve())


def test_explicit_list_help_lists_its_flags_outside_a_repository(world):
    p = run(world, "gw", "list", "-h", at=world.root)
    assert p.returncode == 0, p.stderr
    assert "--list" in p.stderr and "--json" in p.stderr
    assert "--delete" not in p.stderr


@pytest.mark.parametrize("shell", ["fish", "zsh"])
@pytest.mark.parametrize("words", [["--json"], ["list", "--list"], ["topic", "-l"]])
def test_terminal_metadata_never_treats_directory_text_as_a_destination(
    tmp_path, shell, words
):
    target = tmp_path / "existing\n"
    target.mkdir()
    payload = os.fsencode(target) + b"\n"
    binary = (
        "#!/usr/bin/env python3\nimport os, sys\n"
        "if sys.argv[1:] == ['--complete']: print('list\\tlist')\n"
        f"else: os.write(1, {payload!r})\n"
    )
    bin_dir = _fake(tmp_path, "gw", binary)
    p, cwd = _drive(shell, bin_dir, ["gw", *words], tmp_path, answer=b"")
    assert p.returncode == 0, p.stderr
    assert p.stdout == payload
    assert cwd == os.fsencode(tmp_path.resolve())


@pytest.mark.parametrize(
    "args",
    [
        ["-h"],
        ["--help"],
        ["list", "--help"],
        ["prune", "--help"],
        ["branch", "status", "--help"],
    ],
)
def test_help_never_runs_repository_or_forge_commands(world, network, args):
    from test_branches import cli

    p = cli(world, *args, env=network.env())
    assert p.returncode == 0, p.stderr
    assert not network.calls()


@pytest.mark.parametrize("shell", ["fish", "zsh"])
@pytest.mark.parametrize("words", [[], ["main"], ["list"], ["list", "main"]])
def test_terminal_default_list_selects_sole_main(world, shell, words):
    subdirectory = world.repo / "nested"
    subdirectory.mkdir()
    query = "main" in words
    p, cwd = _drive(
        shell,
        _cli_binaries(world),
        ["gw", *words],
        subdirectory,
        env_for(world),
        answer=b"" if query else b"1",
    )
    assert p.returncode == 0, p.stderr
    assert p.stdout == b""
    assert cwd == os.fsencode(world.repo.resolve())
    if query:
        assert b"which?" not in p.stderr
    else:
        assert b"which?" in p.stderr
        assert b"1-1" in p.stderr
        assert os.fsencode(world.repo) in p.stderr


@pytest.mark.parametrize("shell", ["fish", "zsh"])
@pytest.mark.parametrize("words", [["--json"], ["list", "--list"]])
def test_terminal_metadata_preserves_subdirectory_in_sole_main(world, shell, words):
    subdirectory = world.repo / "nested"
    subdirectory.mkdir()
    p, cwd = _drive(
        shell,
        _cli_binaries(world),
        ["gw", *words],
        subdirectory,
        env_for(world),
        answer=b"",
    )
    assert p.returncode == 0, p.stderr
    assert os.fsencode(world.repo) in p.stdout
    assert b"which?" not in p.stderr
    assert cwd == os.fsencode(subdirectory.resolve())
