"""Drive the entry points. Exec is the only way env, cwd, both streams and the
exit code stay per-test."""

from __future__ import annotations

import json
import os
import pty
import re
import select
import shlex
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest
from conftest import argv, env_for

from worktrees import cli, prune

SRC = str(Path(__file__).resolve().parents[1] / "src")


def run(world, entry: str, *args: str, at: Path | None = None):
    return subprocess.run(
        argv(entry, *args),
        cwd=str(at or world.repo),
        capture_output=True,
        text=True,
        env=env_for(world),
    )


def run_with_a_terminal_on_stdout(
    world, entry: str, *args: str, env_extra: dict[str, str] | None = None
) -> tuple[int, str]:
    """A real tty on stdout, so the colour decision is the live one."""
    parent, child = pty.openpty()
    proc = subprocess.Popen(
        argv(entry, *args),
        cwd=str(world.repo),
        stdin=subprocess.DEVNULL,
        stdout=child,
        stderr=subprocess.DEVNULL,
        text=True,
        env={**env_for(world), "TERM": "xterm-256color", **(env_extra or {})},
    )
    os.close(child)
    chunks: list[bytes] = []
    while True:
        try:
            data = os.read(parent, 65536)
        except OSError:  # the child closed its end
            break
        if not data:
            break
        chunks.append(data)
    proc.wait()
    os.close(parent)
    # A pty turns every \n into \r\n on the way out.
    return proc.returncode, b"".join(chunks).decode().replace("\r\n", "\n")


def run_on_a_terminal_at(
    world, at: Path, entry: str, *args: str, answer: str
) -> tuple[int, str]:
    """The same, from a directory that is not the main checkout."""
    return _on_a_terminal(world, at, entry, *args, answer=answer)


def run_on_a_terminal(world, entry: str, *args: str, answer: str) -> tuple[int, str]:
    """The same, with a real tty on stdin, so the prompt is reachable."""
    return _on_a_terminal(world, world.repo, entry, *args, answer=answer)


def _on_a_terminal(
    world, at: Path, entry: str, *args: str, answer: str
) -> tuple[int, str]:
    parent, child = pty.openpty()
    proc = subprocess.Popen(
        argv(entry, *args),
        cwd=str(at),
        stdin=child,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        env=env_for(world),
    )
    os.close(child)
    os.write(parent, answer.encode())
    out = proc.stdout.read() if proc.stdout else ""
    proc.wait()
    os.close(parent)
    return proc.returncode, out


# --------------------------------------------------------------------------
# gwp previews without removing
# --------------------------------------------------------------------------


@pytest.mark.parametrize("flag", ["--yes", "-y"])
def test_confirmation_flags_approve_removal_without_a_terminal(world, flag) -> None:
    wt = world.merged_worktree("done")
    p = run(world, "gw prune", flag)
    assert p.returncode == 0, p.stderr
    assert not wt.exists()


def test_status_leaves_a_removable_worktree_alone(world) -> None:
    wt = world.merged_worktree("done")
    p = run(world, "gw prune")
    assert p.returncode == 0, p.stderr
    assert "remove" in p.stdout
    assert "1 remove, 1 empty, 0 keep, 0 unknown; 1 eligible" in p.stdout
    assert "gw prune --yes removes the 1 marked removable" in p.stderr
    assert wt.exists()
    assert world.git("rev-parse", "--verify", "refs/heads/done")


def test_status_says_when_there_is_nothing(world) -> None:
    """The main checkout is still a row, so the table shows what
    `git worktree list` shows and the hint says what is missing."""
    p = run(world, "gw prune")
    assert p.returncode == 0, p.stderr
    assert "it is the main checkout" in p.stdout
    assert "no worktrees besides the main checkout; gw add NAME makes one" in p.stderr


def test_status_json_is_data_on_stdout(world) -> None:
    world.merged_worktree("done")
    p = run(world, "gw prune", "--json")
    assert p.returncode == 0, p.stderr
    payload = json.loads(p.stdout)
    assert payload["head"] == {"ref": "refs/heads/main", "source": "inferred"}
    rows = {v["branch"]: v for v in payload["verdicts"]}
    assert rows["done"]["verdict"] == "remove"
    assert "it is the main checkout, and never removable" in rows["main"]["why"]
    assert payload["stale"] == []


def test_json_carries_the_ignored_count_as_a_number(world) -> None:
    """A caller deciding whether to pass --delete ignored-files wants the count, not
    a sentence it has to find the count inside."""
    wt = world.worktree("holds-secrets")
    world.repo.joinpath(".gitignore").write_text(".env\n")
    world.git("add", ".gitignore")
    world.git("commit", "--quiet", "-m", "ignore")
    world.git("merge", "--quiet", "--ff-only", "main", at=wt)
    wt.joinpath(".env").write_text("S=1\n")

    p = run(world, "gw prune", "--json")
    assert p.returncode == 0, p.stderr
    rows = {v["branch"]: v for v in json.loads(p.stdout)["verdicts"]}
    row = rows["holds-secrets"]
    assert row["ignored"] == 1
    assert row["verdict"] == "empty"
    assert not row["eligible"]
    assert rows["main"]["ignored"] == 0
    # and the sentence still says it, for the person reading the table
    assert "1 ignored path(s)" in row["why"]


def test_json_carries_the_sha_the_verdict_was_formed_against(world) -> None:
    wt = world.merged_worktree("done")
    head = world.git("rev-parse", "HEAD", at=wt)
    p = run(world, "gw prune", "--json")
    assert p.returncode == 0, p.stderr
    row = next(v for v in json.loads(p.stdout)["verdicts"] if v["branch"] == "done")
    assert row["sha"] == head
    assert len(row["sha"]) == 40


def test_status_names_a_stale_record_rather_than_clearing_it(world) -> None:
    """`git worktree prune` mutates, so a read-only command may not call it."""
    wt = world.worktree("gone")
    import shutil

    shutil.rmtree(wt)
    p = run(world, "gw prune")
    assert p.returncode == 0, p.stderr
    assert "1 stale record(s)" in p.stderr
    # Still listed by git, because nothing pruned it.
    assert "gone" in world.git("worktree", "list", "--porcelain")


def test_status_counts_the_worktrees_belonging_to_a_sibling(world) -> None:
    """One `.worktrees` root serves every repository beside it, and nothing
    else says so: the directories there are not all this repository's."""
    world.worktree("mine")
    world.sibling("other", "theirs")
    p = run(world, "gw prune")
    assert p.returncode == 0, p.stderr
    root = world.parent / ".worktrees"
    assert f"1 worktree(s) under {root} belong to another repository" in p.stderr
    assert "theirs" not in p.stdout


def test_status_says_nothing_when_the_root_is_all_ours(world) -> None:
    world.worktree("mine")
    p = run(world, "gw prune")
    assert p.returncode == 0, p.stderr
    assert "another repository" not in p.stderr


def test_json_carries_the_neighbour_count_as_a_number(world) -> None:
    world.worktree("mine")
    p = run(world, "gw prune", "--json")
    assert json.loads(p.stdout)["neighbours"] == 0
    world.sibling("other", "theirs")
    p = run(world, "gw prune", "--json")
    assert json.loads(p.stdout)["neighbours"] == 1


# --------------------------------------------------------------------------
# gwp removes exactly the eligible preview rows
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "extra",
    [
        (),
        (
            "--delete",
            "ignored-files",
        ),
    ],
)
def test_prune_removes_exactly_what_status_marks(world, extra: tuple[str, ...]) -> None:
    """The invariant. Same flags in, same set out."""
    (world.repo / ".gitignore").write_text(".env\n")
    world.git("add", "--", ".gitignore")
    world.git("commit", "--quiet", "-m", "ignore")

    world.merged_worktree("finished")
    holds = world.merged_worktree("holds-env")
    (holds / ".env").write_text("SECRET=1\n")
    busy = world.worktree("busy")
    (busy / "scratch.txt").write_text("wip\n")
    mystery = world.worktree("mystery")
    world.commit("m.txt", "m\n", at=mystery)

    p = run(world, "gw prune", "--json", *extra)
    marked = sorted(
        v["branch"] for v in json.loads(p.stdout)["verdicts"] if v["eligible"]
    )

    p = run(world, "gw prune", "--yes", "--json", *extra)
    assert p.returncode == 0, p.stderr
    removed = sorted(json.loads(p.stdout)["removed"])

    assert removed == marked
    assert "finished" in removed
    assert ("holds-env" in removed) == bool(extra)
    for kept in ("busy", "mystery"):
        assert kept not in removed


def test_prune_asks_before_removing(world) -> None:
    wt = world.merged_worktree("done")
    code, out = run_on_a_terminal(world, "gw prune", answer="y\n")
    assert code == 0, out
    assert "remove 1 worktree(s)? [y/N]" in out
    assert "restore with: git branch done " in out
    assert not wt.exists()


def test_answering_no_removes_nothing(world) -> None:
    wt = world.merged_worktree("done")
    code, out = run_on_a_terminal(world, "gw prune", answer="n\n")
    assert code == 0, out
    assert "nothing removed" in out
    assert wt.exists()
    assert world.git("rev-parse", "--verify", "refs/heads/done")


def test_bare_enter_removes_nothing(world) -> None:
    """[y/N]: the capital is the default, and it has to be true."""
    wt = world.merged_worktree("done")
    code, out = run_on_a_terminal(world, "gw prune", answer="\n")
    assert code == 0, out
    assert "nothing removed" in out
    assert wt.exists()


def test_without_a_terminal_prune_assesses_without_prompting(world) -> None:
    wt = world.merged_worktree("done")
    p = run(world, "gw prune")
    assert p.returncode == 0, p.stderr
    assert "VERDICT" in p.stdout and "done" in p.stdout
    assert "[y/N]" not in p.stderr
    assert "git branch done " not in p.stderr
    assert wt.exists()


def test_no_terminal_is_one_exit_code(world) -> None:
    """One condition, one number. 2 is already argparse's and _Stop's."""
    world.merged_worktree("a")
    world.merged_worktree("b")
    p = run(world, "gw remove")
    assert p.returncode == 3, p.stderr
    assert "terminal" in p.stderr


@pytest.mark.parametrize(
    ("answer", "expected"),
    [("y", True), ("Y", True), ("yes", True), ("n", False), ("", False), ("q", False)],
)
def test_confirm_takes_yes_and_nothing_else(answer: str, expected: bool) -> None:
    assert prune.confirm(1, ask=lambda _: answer) is expected


def test_prune_deletes_the_branch_and_the_empty_parents(world) -> None:
    wt = world.merged_worktree("done")
    p = run(world, "gw prune", "--yes")
    assert p.returncode == 0, p.stderr
    assert not wt.exists()
    assert "done" not in world.git("branch", "--format=%(refname:short)").split()
    assert not (world.parent / ".worktrees" / "done").exists()
    assert p.stdout.splitlines()[-1] == "removed 1 worktree(s): done"
    assert p.stdout.count("VERDICT") == 1
    assert str(world.repo) in p.stdout and str(wt) in p.stdout


def test_a_squash_merged_branch_goes_with_its_checkout(world) -> None:
    """The proof `-d` cannot read is the proof this deletes on."""
    wt = world.worktree("squashed")
    world.commit("f.txt", "one\n", at=wt)
    (world.repo / "f.txt").write_text("one\n")
    world.git("add", "--", "f.txt")
    world.git("commit", "--quiet", "-m", "squash")

    p = run(world, "gw prune", "--yes")
    assert p.returncode == 0, p.stderr
    assert not wt.exists()
    assert "squashed" not in world.git("branch", "--format=%(refname:short)").split()
    assert "kept" not in p.stderr
    assert "restore with: git branch squashed " in p.stderr


def test_prune_clears_a_stale_record(world) -> None:
    wt = world.worktree("gone")
    import shutil

    shutil.rmtree(wt)
    p = run(world, "gw prune", "--yes")
    assert p.returncode == 0, p.stderr
    assert "gone" not in world.git("worktree", "list", "--porcelain")


def test_declined_terminal_prune_still_clears_a_stale_record(world) -> None:
    """Stale records go before assessment; the answer governs removal alone."""
    import shutil

    kept = world.merged_worktree("done")
    shutil.rmtree(world.worktree("gone"))
    code, out = run_on_a_terminal(world, "gw prune", answer="n\n")
    assert code == 0, out
    assert "nothing removed" in out
    records = world.git("worktree", "list", "--porcelain")
    assert "gone" not in records
    assert kept.is_dir() and world.git("rev-parse", "--verify", "refs/heads/done")


def test_prune_says_so_when_there_is_nothing(world) -> None:
    p = run(world, "gw prune", "--yes")
    assert p.returncode == 0, p.stderr
    assert "it is the main checkout" in p.stdout
    assert "no worktrees besides the main checkout; gw add NAME makes one" in p.stderr


def test_prune_prints_the_reason_rather_than_naming_the_command(world) -> None:
    """Each worktree carries the reason that prevents its removal."""
    world.worktree("busy")
    (world.parent / ".worktrees" / "busy" / "repo" / "wip").write_text("x")
    p = run(world, "gw prune", "--yes")
    assert p.returncode == 0, p.stderr
    assert "busy" in p.stdout
    assert "it has uncommitted changes" in p.stdout
    assert (
        "nothing to remove; 0 remove, 2 empty, 0 keep, 0 unknown; 0 eligible"
        in p.stdout
    )
    # The temporary root can hold the letters, as pytest-xdist's popen-gw0 does.
    assert "gw" not in p.stdout.replace(str(world.root), "")


def test_version_is_a_command_and_not_a_flag(world) -> None:
    for entry in ("gw", "gw prune", "main"):
        p = run(world, entry, "--version")
        assert p.returncode == 2
        wanted = "--version is not a command" if entry != "gw prune" else "unrecognized"
        assert wanted in p.stderr


def test_prune_metadata_reports_without_removing(world) -> None:
    wt = world.merged_worktree("done")
    for entry, prefix in (("gw", ("prune",)), ("gw prune", ())):
        p = run(world, entry, *prefix)
        assert p.returncode == 0, p.stderr
        assert "done" in p.stdout
        assert wt.exists()


def test_explain_lists_every_command_and_runs_none(world) -> None:
    p = run(world, "gw", "--explain")
    assert p.returncode == 0, p.stderr
    assert "git worktree list --porcelain -z" in p.stdout
    assert "git update-ref --no-deref -d $ref $sha" in p.stdout
    assert "git branch -D" not in p.stdout


def test_verbose_prints_the_argv(world) -> None:
    world.merged_worktree("done")
    p = run(world, "gw prune", "--verbose", "--yes")
    assert p.returncode == 0, p.stderr
    # The canary: a run that never reached git would print nothing here, and an
    # empty stream is indistinguishable from a refusal that worked.
    assert "+ git worktree list --porcelain -z" in p.stderr
    assert "--no-optional-locks status --porcelain" in p.stderr
    assert "--is-ancestor" in p.stderr


def test_status_never_issues_a_mutating_command(world, network) -> None:
    """A report issues only read-only Git commands."""
    world.merged_worktree("done")
    from test_branches import cli as invoke

    p = invoke(world, "list", env=network.env())
    assert p.returncode == 0, p.stderr
    trace = "\n".join(
        "+ " + shlex.join(call) for call in network.calls() if call[0] == "git"
    )
    _assert_read_only_trace(trace)


def _assert_read_only_trace(stderr: str) -> None:
    issued = [line for line in stderr.splitlines() if line.startswith("+ git ")]
    assert issued
    valued_options = ("-C", "-c", "--git-dir", "--work-tree", "--namespace")
    for line in issued:
        argv = shlex.split(line.removeprefix("+ "))
        start = 1
        while start < len(argv) and argv[start].startswith("-"):
            start += 2 if argv[start] in valued_options else 1
        command = argv[start:]
        assert command and command[0] not in ("checkout", "update-ref"), line
        assert command[:2] not in (["worktree", "prune"], ["worktree", "remove"]), line


@pytest.mark.parametrize(
    ("trace", "mutating"),
    [
        ("+ git checkout main", True),
        ("+ git -C '/path with spaces' checkout feature", True),
        ("+ git -C /repo -c color.ui=false checkout feature", True),
        ("+ git -C /repo worktree prune", True),
        ("+ git -C /repo worktree remove /worktree", True),
        ("+ git -C /repo update-ref refs/heads/feature HEAD", True),
        ("+ git config --get checkout.defaultRemote", False),
        ("+ git -C /repo config --get checkout.defaultRemote", False),
        ("+ git --no-optional-locks status --porcelain", False),
        ("+ git -C /repo show refs/heads/checkout", False),
    ],
)
def test_read_only_trace_assertion_checks_git_subcommands(trace, mutating) -> None:
    if mutating:
        with pytest.raises(AssertionError):
            _assert_read_only_trace(trace)
    else:
        _assert_read_only_trace(trace)


def test_you_cannot_prune_the_worktree_you_stand_in(world) -> None:
    wt = world.worktree("here")
    p = run(world, "gw prune", at=wt)
    assert p.returncode == 0, p.stderr
    assert "you are standing in it" in p.stdout
    assert wt.exists()


# --------------------------------------------------------------------------
# the CLI name
# --------------------------------------------------------------------------


def test_the_cli_name_takes_a_subcommand(world) -> None:
    world.merged_worktree("done")
    p = run(world, "main", "prune", "--json")
    assert p.returncode == 0, p.stderr
    assert "done" in {v["branch"] for v in json.loads(p.stdout)["verdicts"]}


def test_the_cli_name_with_no_argument_prints_the_guide(world) -> None:
    world.worktree("quokka")
    p = run(world, "main")
    assert p.returncode == 0, p.stderr
    assert "COMMAND" in p.stdout and "quokka" not in p.stdout


def test_the_cli_name_explains_every_command(world) -> None:
    p = run(world, "main", "--explain")
    assert p.returncode == 0, p.stderr
    assert "git worktree list --porcelain -z" in p.stdout


@pytest.mark.parametrize("entry", ["gw", "main"])
def test_a_mistyped_command_is_a_usage_error(world, entry) -> None:
    p = run(world, entry, "verison", "extra")
    assert p.returncode == 2
    assert "verison is not a command" in p.stderr
    p = run(world, entry, "list", "verison", "extra")
    assert p.returncode == 2
    assert "unrecognized arguments: extra" in p.stderr


# --------------------------------------------------------------------------
# shorthands, and the table they come from
# --------------------------------------------------------------------------


@pytest.mark.parametrize("spelling", ["status", "st", "s"])
def test_retired_status_spellings_select_worktree_metadata_through_list(
    world, spelling: str
) -> None:
    wt = world.worktree(spelling)
    assert run(world, "main", spelling, "--json").returncode == 2
    p = run(world, "main", "list", spelling, "--json")
    assert p.returncode == 0, p.stderr
    assert [row["path"] for row in json.loads(p.stdout)["worktrees"]] == [str(wt)]
    assert wt.exists()


@pytest.mark.parametrize(
    ("spelling", "canonical"),
    [
        ("p", "prune"),
        ("a", "add"),
        ("rm", "remove"),
        ("rot", "rotate"),
    ],
)
def test_a_shorthand_names_the_command_it_stands_for(
    spelling: str, canonical: str
) -> None:
    from worktrees import cli

    assert cli._CANONICAL[spelling] == canonical


def test_no_two_commands_answer_to_the_same_name() -> None:
    """A dict comprehension would keep the last one and leave a command
    reachable under a name that runs a different one."""
    from worktrees import cli

    assert cli._canonical()


def test_gw_and_worktrees_are_the_only_console_scripts() -> None:
    """Every other name is a shell plugin shortcut that calls gw."""
    import tomllib

    root = Path(__file__).resolve().parents[1]
    scripts = tomllib.loads((root / "pyproject.toml").read_text())["project"]["scripts"]
    assert set(scripts) == {"gw", "worktrees"}


def test_shortcut_names_are_unique_and_distinct_from_gw() -> None:
    from worktrees import cli

    names = [
        name for path, c in cli.command_rows() for name, _ in cli.shortcuts(path, c)
    ]
    assert len(names) == len(set(names))
    assert not set(names) & {"gw", "worktrees", *cli._CANONICAL}


# --------------------------------------------------------------------------
# root help
# --------------------------------------------------------------------------


@pytest.mark.parametrize("entry", ["gw", "main"])
def test_help_names_every_command_and_every_shorthand(world, entry: str) -> None:
    args = ("--help",)
    p = run(world, entry, *args)
    assert p.returncode == 0, p.stderr
    from worktrees import cli

    lines = p.stdout.splitlines()
    for name, command in cli.command_rows():
        assert f"gw {name}" in p.stdout, name
        for alias in command.aliases:
            assert alias in p.stdout, alias
        # Each shortcut on its own line under its command, with what it runs.
        at = next(i for i, ln in enumerate(lines) if ln.startswith(f"gw {name}"))
        for offset, (short, words) in enumerate(cli.shortcuts(name, command), 1):
            assert lines[at + offset].split() == [short, *words], lines[at + offset]


def test_help_lists_commands_and_not_how_the_shell_reaches_them(world) -> None:
    p = run(world, "gw", "--help")
    assert p.returncode == 0, p.stderr
    assert "change the directory" not in p.stdout
    assert "$PATH" not in p.stdout


def test_top_level_help_is_available_without_assessment(world) -> None:
    bare = run(world, "main", "--help", at=world.root)
    assert bare.returncode == 0, bare.stderr
    assert "prune" in bare.stdout and "sync" in bare.stdout


def test_help_stays_inside_eighty_columns(world) -> None:
    """It is read at a prompt, next to the commands it describes."""
    p = run(world, "gw", "--help")
    assert p.returncode == 0, p.stderr
    assert [ln for ln in p.stdout.splitlines() if len(ln) > 80] == []


# --------------------------------------------------------------------------
# colour
# --------------------------------------------------------------------------


def test_colour_reaches_a_terminal(world) -> None:
    world.merged_worktree("done")
    code, out = run_with_a_terminal_on_stdout(world, "gw prune")
    assert code == 0, out
    assert "[32mremove" in out


def test_a_pipe_gets_no_colour(world) -> None:
    world.merged_worktree("done")
    p = run(world, "gw prune")
    assert p.returncode == 0, p.stderr
    assert "[" not in p.stdout


def test_json_is_never_coloured_even_on_a_terminal(world) -> None:
    """stdout carries what jq parses and what `cd $(gwa x)` reads."""
    world.merged_worktree("done")
    code, out = run_with_a_terminal_on_stdout(world, "gw prune", "--json")
    assert code == 0, out
    assert "[" not in out
    assert "done" in {v["branch"] for v in json.loads(out)["verdicts"]}


def test_no_color_turns_it_off_on_a_terminal(world) -> None:
    world.merged_worktree("done")
    code, out = run_with_a_terminal_on_stdout(
        world, "gw prune", env_extra={"NO_COLOR": "1"}
    )
    assert code == 0, out
    assert "[" not in out


# --------------------------------------------------------------------------
# an unreachable remote is answered, not refused
# --------------------------------------------------------------------------


def _unreachable(world) -> None:
    world.git("remote", "add", "origin", str(world.root / "nowhere.git"))


def test_an_unreachable_remote_does_not_block_branch_creation(world) -> None:
    """An offline machine gets a branch off whatever it last saw."""
    _unreachable(world)
    world.git("update-ref", "refs/remotes/origin/main", "main")
    p = run(world, "gw branch create", "spike")
    assert p.returncode == 0, p.stderr
    assert "Never synced:" in p.stderr
    assert "spike from main" in p.stdout
    assert world.git("rev-parse", "--verify", "refs/heads/spike")


def test_an_unreachable_remote_does_not_block_worktree_creation(world) -> None:
    _unreachable(world)
    world.git("update-ref", "refs/remotes/origin/main", "main")
    p = run(world, "gw add", "fix-parser")
    assert p.returncode == 0, p.stderr
    assert "Never synced:" in p.stderr
    assert (world.parent / ".worktrees" / "fix-parser" / "repo").is_dir()


def test_an_unreachable_remote_does_not_block_status(world) -> None:
    _unreachable(world)
    world.merged_worktree("done")
    p = run(world, "gw prune")
    assert p.returncode == 0, p.stderr
    assert "Never synced:" in p.stderr
    assert "done" in p.stdout


def test_explain_names_every_refusal_the_guard_holds(world) -> None:
    """The footer is generated from the rules, so the seventh is in it."""
    p = run(world, "gw", "--explain")
    assert p.returncode == 0, p.stderr
    # textwrap fills the footer, so compare against one line of it.
    flat = " ".join(p.stdout.split())
    for named in (
        "reset --hard",
        "a forced checkout or switch",
        "clean -f",
        "push --force",
        "worktree remove --force",
        "branch -D",
        "an update-ref delete that names no full sha",
    ):
        assert named in flat, named


def test_remove_takes_no_forge_like_status_and_prune(world) -> None:
    world.merged_worktree("done")
    assert "--no-forge" not in run(world, "gw remove", "--help").stderr
    p = run(world, "gw remove", "done", "-y")
    assert p.returncode == 0, p.stderr


# --------------------------------------------------------------------------
# a stash outlives the branch it names
# --------------------------------------------------------------------------


def test_branch_stashes_prevent_pruning(world) -> None:
    """refs/stash pins its own commits, so the entry survives. Its subject
    does not: it goes on naming a branch that is gone.
    """
    wt = world.worktree("wip")
    world.commit("f.txt", "one\n", at=wt)
    (wt / "f.txt").write_text("half-finished\n")
    world.git("stash", "push", "--quiet", "-m", "half-finished", at=wt)

    (world.repo / "f.txt").write_text("one\n")
    world.git("add", "--", "f.txt")
    world.git("commit", "--quiet", "-m", "squash of wip")

    p = run(world, "gw prune", "-y")
    assert p.returncode == 0, p.stderr
    assert "its branch has stashes" in p.stdout
    assert wt.exists()
    # And it did outlive it.
    assert world.git("stash", "list")
    assert "wip" in world.git("branch", "--format=%(refname:short)").split()


def test_a_stash_on_another_branch_is_not_named(world) -> None:
    wt = world.worktree("wip")
    world.commit("f.txt", "one\n", at=wt)
    (world.repo / "other.txt").write_text("x\n")
    world.git("add", "--", "other.txt")
    world.git("stash", "push", "--quiet", "-m", "elsewhere")

    (world.repo / "f.txt").write_text("one\n")
    world.git("add", "--", "f.txt")
    world.git("commit", "--quiet", "-m", "squash of wip")

    p = run(world, "gw prune", "-y")
    assert p.returncode == 0, p.stderr
    assert "was made on this branch" not in p.stderr


def test_list_with_no_query_prompts_on_a_terminal(world) -> None:
    """One other worktree, and it still asks, because nothing said which.

    The prompt is the whole point: `gw list` with no argument means show me the
    options, and one option is still an option rather than a destination.
    """
    world.worktree("one")
    code, out = run_on_a_terminal(world, "gw", "list", answer="\r")
    assert code == 0, out
    assert "which? [type to filter, up/down and enter, esc to cancel]" in out
    assert "one" in out


def test_list_with_no_query_takes_esc_as_cancelled(world) -> None:
    world.worktree("one")
    code, out = run_on_a_terminal(world, "gw", "list", answer="\x1b")
    assert code == 0, out
    assert "nothing picked" in out


def test_list_moves_with_the_arrow_keys_and_picks_on_enter(world) -> None:
    world.worktree("one")
    two = world.worktree("two")
    code, out = run_on_a_terminal(world, "gw", "list", answer="\x1b[B\x1b[B\r")
    assert code == 0, out
    assert out.rstrip().endswith(str(two)), out


@pytest.mark.parametrize(
    "entry,args,prompt,table",
    [
        (entry, (*prefix, *flags), prompt, command == "prune")
        for command, flags, prompt in (
            ("list", (), "which? [type to filter, up/down and enter, esc to cancel] "),
            (
                "remove",
                (),
                "which? [type to filter, up/down and enter, esc to cancel] ",
            ),
            (
                "remove",
                ("one",),
                "remove 1 worktree(s)? [y/N] ",
            ),
            ("prune", (), "remove 2 worktree(s)? [y/N] "),
        )
        for entry, prefix in (
            (entry, (name,))
            for entry in ("gw", "main")
            for name in (command, *cli._COMMANDS[command].aliases)
        )
    ],
)
def test_ctrl_c_at_every_prompt(world, entry, args, prompt, table) -> None:
    """SIGINT cancels selection and confirmation without changing any worktree."""
    paths = [world.merged_worktree(name) for name in ("one", "two")]
    before = world.git("worktree", "list", "--porcelain")
    refs = world.git("show-ref")
    parent, child = pty.openpty()
    try:
        with subprocess.Popen(
            argv(entry, *args),
            cwd=world.repo,
            stdin=child,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env_for(world),
        ) as proc:
            try:
                assert proc.stderr is not None
                seen = b""
                deadline = time.monotonic() + 15
                while prompt.encode() not in seen:
                    remaining = deadline - time.monotonic()
                    assert remaining > 0, seen.decode()
                    ready, _, _ = select.select([proc.stderr], [], [], remaining)
                    assert ready, seen.decode()
                    chunk = os.read(proc.stderr.fileno(), 65536)
                    assert chunk, seen.decode()
                    seen += chunk
                proc.send_signal(signal.SIGINT)
                out, err = proc.communicate(timeout=15)
            finally:
                if proc.poll() is None:
                    proc.kill()
                    proc.communicate()
    finally:
        os.close(child)
        os.close(parent)
    stderr = (seen + err).decode()
    assert "Traceback" not in stderr, stderr
    assert "KeyboardInterrupt" not in stderr, stderr
    assert proc.returncode == 130, stderr
    if table:
        assert out.count(b"VERDICT") == 1
        assert b"ELIGIBLE" in out
        assert all(str(path).encode() in out for path in (world.repo, *paths))
    else:
        assert out == b""
    assert stderr.endswith(prompt + "\n"), stderr
    assert world.git("worktree", "list", "--porcelain") == before
    assert world.git("show-ref") == refs
    assert all((path / "README").exists() for path in paths)


def test_status_lists_the_main_checkout_and_never_proposes_it(world) -> None:
    """A listing that leaves it out shows one row where `git worktree list`
    shows two, which reads as though something went missing."""
    world.merged_worktree("done")
    p = run(world, "gw prune")
    assert p.returncode == 0, p.stderr
    assert "it is the main checkout, and never removable" in p.stdout
    assert "ELIGIBLE" in p.stdout
    assert "1 remove, 1 empty, 0 keep, 0 unknown; 1 eligible" in p.stdout
    rows = {
        v["branch"]: v
        for v in json.loads(run(world, "gw prune", "--json").stdout)["verdicts"]
    }
    assert set(rows) == {"main", "done"}
    assert rows["main"]["verdict"] == "empty"
    assert not rows["main"]["eligible"]


def test_remove_is_never_offered_the_main_checkout(world) -> None:
    """`gwr` takes the same list as its candidates, and `git worktree remove`
    answers `fatal: '<path>' is a main working tree` whatever flag it is
    given, so offering it would propose something certain to be refused.
    """
    world.merged_worktree("done")
    p = run(world, "gw remove", "--complete")
    assert p.returncode == 0, p.stderr
    assert "main" not in p.stdout
    assert "done" in p.stdout


def test_list_shows_the_worktree_you_are_standing_in_unnumbered(world) -> None:
    """Marked, above the ones you can go to, and carrying no number: it is
    where you are rather than somewhere to go."""
    wt = world.worktree("cleanup")
    code, out = run_on_a_terminal_at(world, wt, "gw", "list", answer="\x1b")
    assert code == 0, out
    assert re.search(r"^\s+WORKTREE\s+BRANCH\s+PATH$", out, re.M), out
    assert re.search(r"^\*\s+cleanup\s+cleanup\s", out, re.M), out
    assert re.search(r"^>\s+repo\s+main\s", out, re.M), out
    assert "which? [type to filter, up/down and enter, esc to cancel]" in out


def test_remove_starts_on_the_worktree_you_stand_in_and_lists_main_unnumbered(
    world,
) -> None:
    world.worktree("one")
    two = world.worktree("two")
    code, out = run_on_a_terminal_at(world, two, "gw remove", answer="\x1b")
    assert code == 0, out
    assert re.search(r"^ {3}one\s+one\s", out, re.M), out
    assert re.search(r"^>  two\s+two\s", out, re.M), out
    assert re.search(r"^ {3}repo\s+main\s", out, re.M), out
    assert "nothing picked" in out


def test_remove_confirms_permitted_ignored_files_on_a_terminal(world) -> None:
    """Every permitted ignored path is disclosed before confirmation."""
    world.commit(".gitignore", ".env\n")
    wt = world.merged_worktree("holds")
    (wt / ".env").write_text("SECRET=1\n")

    code, out = run_on_a_terminal(
        world, "gw remove", "holds", "--delete", "ignored-files", answer="n\n"
    )
    assert code == 0, out
    assert "deleting ignored, unrecoverable: .env" in out
    assert "nothing removed" in out
    assert wt.exists()

    code, out = run_on_a_terminal(
        world, "gw remove", "holds", "--delete", "ignored-files", answer="y\n"
    )
    assert code == 0, out
    assert not wt.exists()


def test_remove_asks_before_removing_an_unfinished_worktree_and_keeps_the_branch(
    world,
) -> None:
    wt = world.worktree("busy")
    world.commit("b.txt", "b\n", at=wt)
    code, out = run_on_a_terminal(
        world, "gw remove", "busy", "--keep-branch", answer="n\n"
    )
    assert code == 0, out
    assert "branch and its stashes retained" in out
    assert wt.exists()

    code, out = run_on_a_terminal(
        world, "gw remove", "busy", "--keep-branch", answer="y\n"
    )
    assert code == 0, out
    assert not wt.exists()
    assert world.git("rev-parse", "--verify", "refs/heads/busy")


def test_list_marks_main_when_you_stand_in_it(world) -> None:
    world.worktree("one")
    code, out = run_on_a_terminal(world, "gw", "list", answer="\x1b")
    assert code == 0, out
    assert re.search(r"^>\s+repo\s+main \*\s", out, re.M), out
    assert re.search(r"^ {3}one\s+one\s", out, re.M), out


def test_esc_widens_a_query_to_every_worktree_and_a_second_cancels(world) -> None:
    """The query is the filter the list opens with, so main, which `one`
    does not match, comes back on the first esc still marked as here."""
    world.worktree("one-a")
    world.worktree("one-b")
    code, out = run_on_a_terminal(world, "gw", "list", "one", answer="\x1b\x1b")
    assert code == 0, out
    first, widened = out.split("esc to clear] one", 1)
    assert not re.search(r"^.\s+repo\s+main", first, re.M), out
    assert re.search(r"^>\s+repo\s+main \*\s", widened, re.M), out
    assert "nothing picked" in widened


def test_remove_rejects_a_partial_name_without_opening_the_picker(world) -> None:
    one = world.worktree("one")
    two = world.worktree("two")
    code, out = run_on_a_terminal(world, "gw remove", "o", answer="\x1b")
    assert code == 1, out
    assert "no worktree matches" in out
    assert one.exists() and two.exists()


@pytest.mark.parametrize(
    "args",
    [
        ("list", "--list"),
        ("list", "-l"),
        ("list", "-t"),
        ("prune", "--list"),
        ("p", "--list"),
    ],
)
def test_the_table_flag_is_only_its_long_name(world, args) -> None:
    p = run(world, "gw", *args)
    assert p.returncode == 2, p.stderr
    assert not p.stdout


def test_list_is_never_coloured_even_on_a_terminal(world) -> None:
    """--table, like --json, is what a script reads."""
    world.worktree("one")
    code, out = run_with_a_terminal_on_stdout(world, "gw", "list", "--table")
    assert code == 0, out
    assert "WORKTREE" in out
    assert "\x1b[" not in out


@pytest.mark.parametrize(
    ("url", "short"),
    [
        ("git@github.com:org/repo.git", "github.com/org/repo"),
        ("git@github.com:org/repo", "github.com/org/repo"),
        ("ssh://git@github.com:22/org/repo.git", "github.com/org/repo"),
        ("https://github.com/org/repo.git", "github.com/org/repo"),
        ("https://user:secret@GitHub.com/org/repo/", "github.com/org/repo"),
        (
            "http://gitlab.example.com/group/sub/repo",
            "gitlab.example.com/group/sub/repo",
        ),
        ("file:///srv/git/repo.git", "/srv/git/repo"),
        ("/srv/git/repo.git", "/srv/git/repo"),
        ("../upstream.git", "../upstream"),
    ],
)
def test_a_remote_url_is_shown_as_host_and_path(url, short) -> None:
    from worktrees import repo as R

    assert R.short_url(url) == short


def test_list_names_each_remote_above_the_picker(world) -> None:
    """One line per remote, shortened, and never a credential."""
    world.git("remote", "add", "origin", "git@github.com:org/repo.git")
    world.git("remote", "add", "upstream", "https://me:tok@github.com/up/repo.git")
    world.worktree("one")
    code, out = run_on_a_terminal(world, "gw", "list", answer="\x1b")
    assert code == 0, out
    head = [
        line for line in out.splitlines() if "origin:" in line or "upstream:" in line
    ]
    assert head[0].endswith("origin: github.com/org/repo")
    assert head[1].endswith("upstream: github.com/up/repo")
    assert "tok" not in out


def test_list_for_a_script_names_no_remote(world) -> None:
    world.git("remote", "add", "origin", "git@github.com:org/repo.git")
    world.worktree("one")
    p = run(world, "gw", "list", "--table")
    assert p.returncode == 0, p.stderr
    assert "github.com" not in p.stdout + p.stderr


def test_status_only_shows_the_verdicts_asked_for(world) -> None:
    """The table narrows; the counts still cover every row."""
    world.merged_worktree("done")
    whole = run(world, "gw prune")
    assert whole.returncode == 0, whole.stderr
    assert "empty" in whole.stdout

    p = run(world, "gw prune", "--only", "remove")
    assert p.returncode == 0, p.stderr
    table, _, counts = p.stdout.partition("\n\n")
    assert "empty" not in table
    assert counts.strip() == whole.stdout.partition("\n\n")[2].strip()


def test_status_only_narrows_json_too(world) -> None:
    world.merged_worktree("done")
    p = run(world, "gw prune", "--json", "--only", "empty")
    assert p.returncode == 0, p.stderr
    verdicts = [v["verdict"] for v in json.loads(p.stdout)["verdicts"]]
    assert verdicts and set(verdicts) == {"empty"}


def test_only_takes_no_verdict_that_does_not_exist(world) -> None:
    p = run(world, "gw prune", "--only", "maybe")
    assert p.returncode == 2
    assert "invalid choice" in p.stderr


@pytest.mark.parametrize("entry", ["gw", "gw branch status"])
def test_status_rejects_retired_refresh_alias(entry) -> None:
    import argparse

    p = argparse.ArgumentParser()
    if entry == "gw":
        cli._add_prune_flags(p)
    else:
        cli._add_branch_status_flags(p)
    with pytest.raises(SystemExit):
        p.parse_args(["-f"])


@pytest.mark.parametrize("entry", ["gw prune", "gw branch prune"])
def test_the_commands_that_remove_take_no_f(entry) -> None:
    """On them -f would read as forcing a removal."""
    import argparse

    p = argparse.ArgumentParser()
    if entry == "gw prune":
        cli._add_prune_flags(p)
    else:
        cli._add_branch_delete_flags(p)
    with pytest.raises(SystemExit):
        p.parse_args(["-f"])


def test_list_orders_worktrees_by_when_they_were_made(world) -> None:
    """Main first, then oldest to newest, whatever order git lists them in."""
    paths = [world.worktree(name) for name in ("one", "two", "three")]
    # Make the last one git lists the oldest and the first the newest.
    for age, path in zip((300, 200, 100), reversed(paths), strict=True):
        admin = Path((path / ".git").read_text().strip().removeprefix("gitdir: "))
        old = time.time() - age
        os.utime(admin / "commondir", (old, old))

    p = run(world, "gw", "list", "--json")
    assert p.returncode == 0, p.stderr
    listed = [w["path"] for w in json.loads(p.stdout)["worktrees"]]
    assert listed == [str(world.repo), *map(str, reversed(paths))]


@pytest.mark.parametrize("entry", ["gw prune", "gw remove"])
def test_status_and_remove_list_in_the_same_order_as_list(world, entry) -> None:
    """One order everywhere: oldest to newest after the main checkout."""
    paths = [world.worktree(name) for name in ("one", "two", "three")]
    for age, path in zip((300, 200, 100), reversed(paths), strict=True):
        admin = Path((path / ".git").read_text().strip().removeprefix("gitdir: "))
        old = time.time() - age
        os.utime(admin / "commondir", (old, old))

    args = ["--json"]
    if entry == "gw remove":
        code, out = run_on_a_terminal(world, entry, answer="\x1b")
        assert code == 0, out
        names = ("one", "two", "three")
        shown = [
            n for ln in out.splitlines() for n in names if f"/.worktrees/{n}/" in ln
        ]
        assert shown == ["three", "two", "one"]
        return
    p = run(world, entry, *args)
    assert p.returncode == 0, p.stderr
    listed = [v["branch"] for v in json.loads(p.stdout)["verdicts"]]
    assert [b for b in listed if b in ("one", "two", "three")] == [
        "three",
        "two",
        "one",
    ]


@pytest.mark.skipif(sys.version_info < (3, 14), reason="argparse colours from 3.14")
@pytest.mark.parametrize("args", [("--help",), ()])
def test_help_is_coloured_on_a_terminal_when_stdout_is_captured(world, args) -> None:
    """What a shell function sees: stdout captured, stderr on the terminal."""
    parent, child = pty.openpty()
    proc = subprocess.Popen(
        argv("gw add", *args),
        cwd=str(world.repo),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=child,
        env={**env_for(world), "TERM": "xterm-256color"},
    )
    os.close(child)
    chunks: list[bytes] = []
    while True:
        try:
            data = os.read(parent, 65536)
        except OSError:  # the child closed its end
            break
        if not data:
            break
        chunks.append(data)
    stdout = proc.stdout.read() if proc.stdout else b""
    proc.wait()
    os.close(parent)
    assert stdout == b""
    # argparse paints its heading, which only reaches a stream it sees as a tty.
    assert "\x1b[1;34musage: " in b"".join(chunks).decode()


@pytest.mark.parametrize("flag", ["-v", "--verbose"])
@pytest.mark.parametrize(
    "entry, args",
    [
        ("gw", ()),
        ("gw branch status", ()),
        ("gw", ("branch", "status")),
        ("gw", ("list", "main")),
        ("gw", ("list", "main")),
    ],
)
def test_verbose_refusal_precedes_repository_preflight(
    world, network, entry, args, flag
):
    from test_branches import cli as invoke

    world.git("config", "remote.origin.promisor", "true")
    p = invoke(world, *args, flag, entry=entry, env=network.env())
    assert p.returncode == 2, p.stderr
    assert not p.stdout
    assert "verbose" in p.stderr or "-v" in p.stderr
    assert not network.calls()


@pytest.mark.parametrize("flag", ["-q", "--quiet"])
@pytest.mark.parametrize("args", [(), ("main",), ("--table",), ("--json",)])
def test_list_rejects_quiet_before_repository_preflight(world, network, flag, args):
    from test_branches import cli as invoke

    p = invoke(world, *args, flag, entry="gw", env=network.env())
    assert p.returncode == 2, p.stderr
    assert not network.calls()


@pytest.mark.parametrize("flags", [("-q", "-v"), ("--verbose", "--quiet")])
@pytest.mark.parametrize(
    "entry, args",
    [
        ("gw", ("sync",)),
        ("gw prune", ("--yes",)),
        ("gw remove", ("topic", "--yes")),
        ("gw branch prune", ("topic", "--yes")),
        ("gw add", ("new",)),
        ("gw branch create", ("new",)),
        ("gw rotate", ()),
        ("gw", ("branch", "prune", "topic", "--yes")),
        ("gw", ("branch", "create", "new")),
        ("gw", ("p", "--yes")),
        ("gw", ("a", "new")),
        ("gw", ("rm", "topic", "--yes")),
        ("gw", ("rot",)),
    ],
)
def test_output_flags_conflict_before_repository_preflight(
    world, network, entry, args, flags
):
    from test_branches import cli as invoke

    path = world.merged_worktree("topic")
    tip = world.git("rev-parse", "topic")
    world.git("config", "remote.origin.promisor", "true")
    p = invoke(world, *args, *flags, entry=entry, env=network.env())
    assert p.returncode == 2, p.stderr
    assert "quiet" in p.stderr and "verbose" in p.stderr
    assert not p.stdout
    assert not network.calls()
    assert path.exists()
    assert world.git("rev-parse", "topic") == tip


@pytest.mark.parametrize("flag", ["-v", "--verbose"])
def test_sync_verbose_traces_git_and_forge_to_stderr_with_json(world, network, flag):
    from test_branches import cli as invoke
    from test_sync import origin

    origin(world)
    world.worktree("topic")
    p = invoke(world, "sync", "--json", flag, env=network.env())
    assert p.returncode == 0, p.stderr
    assert json.loads(p.stdout)["lastSync"]["refs"]
    assert "+ git fetch" in p.stderr
    assert "+ gh auth status" in p.stderr
    assert "+ gh pr list" in p.stderr
    assert any(call[:2] == ["git", "fetch"] for call in network.calls())
    assert any(call[:3] == ["gh", "pr", "list"] for call in network.calls())


@pytest.mark.parametrize("flag", ["-q", "--quiet", "-v", "--verbose"])
@pytest.mark.parametrize(
    "entry",
    [
        "gw add",
        "gw remove",
        "gw branch prune",
        "gw branch create",
        "gw rotate",
        "gw prune",
    ],
)
def test_mutating_commands_accept_each_output_flag_without_assessment(
    world, network, entry, flag
):
    from test_branches import cli as invoke

    p = invoke(world, flag, "--explain", entry=entry, env=network.env())
    assert p.returncode == 0, p.stderr
    assert "git worktree list" in p.stdout
    assert not network.calls()


@pytest.mark.parametrize("flag", ["-q", "--quiet"])
@pytest.mark.parametrize(
    "entry, args",
    [("gw", ("prune",)), ("gw prune", ()), ("gw branch status", ())],
)
def test_quiet_keeps_verdicts_without_summary_or_notices(world, entry, args, flag):
    path = world.merged_worktree("topic")
    if entry == "gw branch status":
        world.git("worktree", "remove", str(path))
    p = run(world, entry, *args, flag)
    assert p.returncode == 0, p.stderr
    assert "topic" in p.stdout and "remove" in p.stdout
    assert not re.search(r"\d+ eligible", p.stdout)
    assert "Head:" not in p.stderr


@pytest.mark.parametrize("flag", ["-q", "--quiet"])
def test_quiet_keeps_destination_and_errors(world, flag):
    p = run(world, "gw add", "topic", flag)
    assert p.returncode == 0, p.stderr
    assert p.stdout.strip() == str(world.parent / ".worktrees" / "topic" / "repo")
    assert "Head:" not in p.stderr
    assert "on a new branch" not in p.stderr
    failure = run(world, "gw remove", "missing", flag)
    assert failure.returncode != 0
    assert "no worktree matches" in failure.stderr


@pytest.mark.parametrize("flag", ["-q", "--quiet"])
def test_quiet_sync_suppresses_success_summary(world, network, flag):
    from test_branches import cli as invoke

    p = invoke(world, "sync", flag, env=network.env())
    assert p.returncode == 0, p.stderr
    assert not p.stdout


@pytest.mark.parametrize("flag", ["-q", "--quiet"])
def test_quiet_omits_stale_age_warning(world, network, flag):
    from test_branches import cli as invoke
    from test_sync import cache_path, origin

    origin(world)
    assert invoke(world, "sync", env=network.env()).returncode == 0
    cached = json.loads(cache_path(world).read_text())
    cached["lastSync"] = {
        "refs": "2020-01-01T00:00:00Z",
        "forge": "2020-01-01T00:00:00Z",
    }
    cache_path(world).write_text(json.dumps(cached))
    normal = invoke(world, entry="gw prune", env=network.env())
    quiet = invoke(world, flag, entry="gw prune", env=network.env())
    assert normal.returncode == quiet.returncode == 0
    assert "Last sync:" in normal.stderr
    assert "Last sync:" not in quiet.stderr
    assert "BRANCH" in quiet.stdout


@pytest.mark.parametrize("flag", ["-q", "--quiet"])
def test_quiet_omits_progress_on_stderr_terminal(world, flag):
    outputs = []
    for args in ([], [flag]):
        parent, child = pty.openpty()
        try:
            p = subprocess.Popen(
                argv("gw prune", *args),
                cwd=world.repo,
                env={**env_for(world), "TERM": "xterm"},
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=child,
            )
            os.close(child)
            child = -1
            output = bytearray()
            while True:
                try:
                    chunk = os.read(parent, 65536)
                except OSError:
                    break
                if not chunk:
                    break
                output.extend(chunk)
            assert p.wait() == 0
            outputs.append(bytes(output))
        finally:
            if child >= 0:
                os.close(child)
            os.close(parent)
    assert b"\r\x1b[J" in outputs[0] and b"Head:" in outputs[0]
    # Nothing is wrong here, so quiet leaves the terminal untouched.
    assert outputs[1] == b""


@pytest.mark.parametrize("quiet", [False, True])
def test_prune_shows_full_assessment_before_confirmation(world, quiet):
    finished = world.merged_worktree("finished")
    empty = world.worktree("empty")
    refs = world.git("for-each-ref", "--format=%(refname) %(objectname)")
    parent, child = pty.openpty()
    proc = subprocess.Popen(
        argv("gw prune", *(["--quiet"] if quiet else [])),
        cwd=world.repo,
        env=env_for(world),
        stdin=child,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    os.close(child)
    assert proc.stdout is not None and proc.stderr is not None
    disclosure = b""
    try:
        deadline = time.monotonic() + 30
        while b"[y/N]" not in disclosure and time.monotonic() < deadline:
            ready, _, _ = select.select([proc.stderr], [], [], 1)
            if ready:
                chunk = os.read(proc.stderr.fileno(), 65536)
                if not chunk:
                    break
                disclosure += chunk
        assert b"[y/N]" in disclosure, disclosure.decode()
        assert str(finished).encode() in disclosure
        assert b"git branch finished " in disclosure
        ready, _, _ = select.select([proc.stdout], [], [], 5)
        assert ready, "the assessment table must be visible before answering"
        table = os.read(proc.stdout.fileno(), 65536)
        assert b"VERDICT" in table and b"ELIGIBLE" in table
        for path in (world.repo, finished, empty):
            assert str(path).encode() in table, table.decode()
        assert b"remove" in table and b"empty" in table
        os.write(parent, b"n\n")
        stdout, stderr = proc.communicate(timeout=30)
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()
        os.close(parent)
    assert proc.returncode == 0, (disclosure + stderr).decode()
    assert (table + stdout).count(b"VERDICT") == 1
    assert world.git("for-each-ref", "--format=%(refname) %(objectname)") == refs
    assert finished.exists() and empty.exists() and world.repo.exists()


@pytest.mark.parametrize("quiet", [False, True])
def test_prune_json_success_is_one_result_object(world, quiet):
    finished = world.merged_worktree("finished")
    empty = world.worktree("empty")
    proc = run(world, "gw prune", "--yes", "--json", *(["--quiet"] if quiet else []))
    assert proc.returncode == 0, proc.stderr
    result = json.loads(proc.stdout)
    assert isinstance(result, dict)
    assert result["removed"] == ["finished"]
    assert "head" in result and "lastSync" in result
    assert "VERDICT" not in proc.stdout
    assert str(finished) in proc.stderr and "git branch finished " in proc.stderr
    assert not finished.exists()
    assert empty.exists() and world.repo.exists()


@pytest.mark.parametrize("flags", [("--table",), ("--table", "--yes")])
def test_prune_list_stdout_terminal_has_no_colour(world, flags):
    finished = world.merged_worktree("finished")
    code, out = run_with_a_terminal_on_stdout(world, "gw prune", *flags)
    assert code == 0, out
    assert "VERDICT" in out and str(finished) in out
    assert "\033[" not in out
    assert finished.exists() == ("--yes" not in flags)


def test_prune_only_assesses_on_terminal(world):
    finished = world.merged_worktree("finished")
    code, out = run_on_a_terminal(world, "gw prune", "--only", "remove", answer="y\n")
    assert code == 0, out
    assert str(finished) in out and "VERDICT" in out
    assert "[y/N]" not in out
    assert "git branch finished " not in out
    assert finished.exists()


@pytest.mark.parametrize("mode", [(), ("--json",), ("--table",), ("--only", "remove")])
@pytest.mark.parametrize("flag", ["-v", "--verbose"])
def test_prune_verbose_assessment_keeps_metadata_and_state(world, network, mode, flag):
    from test_branches import cli as invoke

    path = world.merged_worktree("done")
    result = invoke(world, *mode, flag, entry="gw prune", env=network.env())
    assert result.returncode == 0, result.stderr
    assert "git " in result.stderr
    assert "[y/N]" not in result.stderr
    assert path.exists() and world.git("rev-parse", "done")
    assert not network.network_calls()
    if "--json" in mode:
        assert any(v["eligible"] for v in json.loads(result.stdout)["verdicts"])
    else:
        assert "VERDICT" in result.stdout and str(path) in result.stdout


@pytest.mark.parametrize("entry,prefix", [("gw prune", ()), ("gw", ("prune",))])
def test_prune_rejects_retired_dry_run_before_preflight(world, network, entry, prefix):
    from test_branches import cli as invoke

    result = invoke(world, *prefix, "--dry-run", entry=entry, env=network.env())
    assert result.returncode == 2
    assert "--dry-run" in result.stderr
    assert not network.calls()


@pytest.mark.parametrize("mode", ["--json", "--table"])
@pytest.mark.parametrize("approved", [False, True])
def test_prune_metadata_on_terminal_uses_explicit_approval(world, mode, approved):
    from test_assessment import snapshot

    world.commit(".gitignore", "*.secret\n")
    finished = world.merged_worktree("finished")
    blocked = world.merged_worktree("blocked")
    (blocked / "kept.secret").write_bytes(b"ignored\x00bytes\n")
    (blocked / "README").write_text("pending")
    world.git("stash", "push", "-qm", "pending", at=blocked)
    gone = world.worktree("gone")
    import shutil

    shutil.rmtree(gone)
    cache = world.repo / ".git" / "git-worktrees-sync.json"
    cache.write_text('{"sentinel": true}\n')
    refs = world.git("show-ref")
    records = world.git("worktree", "list", "--porcelain")
    stashes = world.git("stash", "list", "--format=%H")
    repo_before = snapshot(world.repo)
    finished_before = snapshot(finished)
    blocked_before = snapshot(blocked)
    code, out = run_on_a_terminal(
        world, "gw prune", mode, *(["--yes"] if approved else []), answer="y\n"
    )
    assert code == 0, out
    assert "[y/N]" not in out
    assert snapshot(blocked) == blocked_before
    assert world.git("stash", "list", "--format=%H") == stashes
    assert cache.read_bytes() == b'{"sentinel": true}\n'
    if approved:
        assert not finished.exists()
        assert "restore with: git branch finished " in out
    else:
        assert snapshot(finished) == finished_before
        assert snapshot(world.repo) == repo_before
        assert world.git("show-ref") == refs
        assert world.git("worktree", "list", "--porcelain") == records
        # The disclosure, not the JSON row's `undo`, which carries the same words.
        assert "restore with: git branch finished " not in out
    if mode == "--table":
        assert "VERDICT" in out and str(finished) in out
    else:
        if approved:
            out = out[out.index("{\n") :]
        result = json.loads(out)
        row = next(v for v in result["verdicts"] if v["branch"] == "finished")
        assert row["verdict"] == "remove" and row["eligible"]
        if approved:
            assert result["removed"] == ["finished"]
        else:
            assert str(gone) in result["stale"]
