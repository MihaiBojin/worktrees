"""Drive the entry points. Exec is the only way env, cwd, both streams and the
exit code stay per-test."""

from __future__ import annotations

import json
import os
import pty
import re
import select
import signal
import subprocess
import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from conftest import env_for

from worktrees import cli, prune
from worktrees.sync import NEVER

SRC = str(Path(__file__).resolve().parents[1] / "src")


def run(world, entry: str, *args: str, at: Path | None = None):
    return subprocess.run(
        [
            sys.executable,
            "-c",
            f"from worktrees.cli import {entry}; raise SystemExit({entry}())",
            *args,
        ],
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
        [
            sys.executable,
            "-c",
            f"from worktrees.cli import {entry}; raise SystemExit({entry}())",
            *args,
        ],
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
        [
            sys.executable,
            "-c",
            f"from worktrees.cli import {entry}; raise SystemExit({entry}())",
            *args,
        ],
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
# gwp without a terminal reports and never removes
# --------------------------------------------------------------------------


def test_prune_takes_no_branch(world) -> None:
    """One target goes through gwr."""
    world.worktree("done")
    p = run(world, "gwp", "--branch", "done", "--yes")
    assert p.returncode == 2
    assert "unrecognized arguments: --branch" in p.stderr
    assert (world.parent / ".worktrees" / "done" / "repo").is_dir()


def test_status_leaves_a_removable_worktree_alone(world) -> None:
    wt = world.finished("done")
    p = run(world, "gwp")
    assert p.returncode == 0, p.stderr
    assert "remove" in p.stdout
    assert "1 eligible, 1 kept, 0 unclear" in p.stdout  # the main checkout
    assert "1 would be removed; gwp --yes removes them" in p.stderr
    assert wt.exists()
    assert world.git("rev-parse", "--verify", "refs/heads/done")


def test_status_says_when_there_is_nothing(world) -> None:
    """The main checkout is still a row, so the table shows what
    `git worktree list` shows and the hint says what is missing."""
    p = run(world, "gwp")
    assert p.returncode == 0, p.stderr
    assert "it is the main checkout" in p.stdout
    assert "no worktrees besides the main checkout; gwa NAME makes one" in p.stderr


def test_status_json_is_data_on_stdout(world) -> None:
    world.finished("done")
    p = run(world, "gwp", "--json")
    assert p.returncode == 0, p.stderr
    payload = json.loads(p.stdout)
    assert payload["head"] == {"ref": "refs/heads/main", "source": "inferred"}
    rows = {v["branch"]: v for v in payload["verdicts"]}
    assert rows["done"]["verdict"] == "remove"
    assert rows["main"]["why"] == "it is the main checkout, and never removable"
    assert payload["stale"] == []


def test_json_carries_the_ignored_count_as_a_number(world) -> None:
    """A caller deciding whether to pass --delete ignored-files wants the count, not
    a sentence it has to find the count inside."""
    _ignoring_env(world, "holds-secrets")

    p = run(world, "gwp", "--json")
    assert p.returncode == 0, p.stderr
    rows = {v["branch"]: v for v in json.loads(p.stdout)["verdicts"]}
    row = rows["holds-secrets"]
    assert row["ignored"] == 1
    assert (row["verdict"], row["eligible"]) == ("remove", False)
    assert rows["main"]["ignored"] == 0
    # and the sentence still says it, for the person reading the table
    assert "1 ignored path(s)" in row["why"]


def test_json_carries_the_sha_the_verdict_was_formed_against(world) -> None:
    wt = world.worktree("done")
    head = world.git("rev-parse", "HEAD", at=wt)
    p = run(world, "gwp", "--json")
    assert p.returncode == 0, p.stderr
    row = json.loads(p.stdout)["verdicts"][0]
    assert row["sha"] == head
    assert len(row["sha"]) == 40


def test_status_names_a_stale_record_rather_than_clearing_it(world) -> None:
    """`git worktree prune` mutates, so a read-only command may not call it."""
    wt = world.worktree("gone")
    import shutil

    shutil.rmtree(wt)
    p = run(world, "gwp")
    assert p.returncode == 0, p.stderr
    assert "1 stale record(s)" in p.stderr
    # Still listed by git, because nothing pruned it.
    assert "gone" in world.git("worktree", "list", "--porcelain")


def test_status_counts_the_worktrees_belonging_to_a_sibling(world) -> None:
    """One `.worktrees` root serves every repository beside it, and nothing
    else says so: the directories there are not all this repository's."""
    world.worktree("mine")
    world.sibling("other", "theirs")
    p = run(world, "gwp")
    assert p.returncode == 0, p.stderr
    root = world.parent / ".worktrees"
    assert f"1 worktree(s) under {root} belong to another repository" in p.stderr
    assert "theirs" not in p.stdout


def test_status_says_nothing_when_the_root_is_all_ours(world) -> None:
    world.worktree("mine")
    p = run(world, "gwp")
    assert p.returncode == 0, p.stderr
    assert "another repository" not in p.stderr


def test_json_carries_the_neighbour_count_as_a_number(world) -> None:
    world.worktree("mine")
    p = run(world, "gwp", "--json")
    assert json.loads(p.stdout)["neighbours"] == 0
    world.sibling("other", "theirs")
    p = run(world, "gwp", "--json")
    assert json.loads(p.stdout)["neighbours"] == 1


# --------------------------------------------------------------------------
# gwp --yes removes exactly what gwp marks removable
# --------------------------------------------------------------------------


@pytest.mark.parametrize("extra", [(), ("--delete", "ignored-files")])
def test_prune_removes_exactly_what_status_marks(world, extra: tuple[str, ...]) -> None:
    """The invariant. Same flags in, same set out."""
    (world.repo / ".gitignore").write_text(".env\n")
    world.git("add", "--", ".gitignore")
    world.git("commit", "--quiet", "-m", "ignore")

    world.finished("finished")
    holds = world.finished("holds-env")
    (holds / ".env").write_text("SECRET=1\n")
    busy = world.finished("busy")
    (busy / "scratch.txt").write_text("wip\n")
    mystery = world.finished("mystery")
    world.commit("m.txt", "m\n", at=mystery)

    p = run(world, "gwp", "--json", *extra)
    assert p.returncode == 0, p.stderr
    marked = sorted(
        v["branch"] for v in json.loads(p.stdout)["verdicts"] if v["eligible"]
    )

    p = run(world, "gwp", "--yes", "--json", *extra)
    assert p.returncode == 0, p.stderr
    removed = sorted(json.loads(p.stdout)["removed"])

    assert removed == marked
    assert "finished" in removed
    assert ("holds-env" in removed) == bool(extra)
    for kept in ("busy", "mystery"):
        assert kept not in removed


def test_prune_asks_before_removing(world) -> None:
    wt = world.finished("done")
    code, out = run_on_a_terminal(world, "gwp", answer="y\n")
    assert code == 0, out
    assert "remove 1 worktree(s)? [y/N]" in out
    assert "restore with: git branch done " in out
    assert not wt.exists()


def test_answering_no_removes_nothing(world) -> None:
    wt = world.finished("done")
    code, out = run_on_a_terminal(world, "gwp", answer="n\n")
    assert code == 0, out
    assert "nothing removed" in out
    assert wt.exists()
    assert world.git("rev-parse", "--verify", "refs/heads/done")


def test_bare_enter_removes_nothing(world) -> None:
    """[y/N]: the capital is the default, and it has to be true."""
    wt = world.finished("done")
    code, out = run_on_a_terminal(world, "gwp", answer="\n")
    assert code == 0, out
    assert "nothing removed" in out
    assert wt.exists()


def test_without_a_terminal_it_reports_and_removes_nothing(world) -> None:
    """An agent or a pipe gets the assessment, and nothing blocks on a prompt."""
    wt = world.finished("done")
    p = run(world, "gwp")
    assert p.returncode == 0, p.stderr
    assert "done" in p.stdout and "remove" in p.stdout
    assert "1 eligible, 1 kept, 0 unclear" in p.stdout
    assert "1 would be removed; gwp --yes removes them" in p.stderr
    assert "[y/N]" not in p.stderr
    assert "restore with" not in p.stderr
    assert wt.exists()


def test_gwr_without_a_terminal_refuses_at_exit_3(world) -> None:
    """One condition, one number. 2 is already argparse's and _Stop's."""
    world.worktree("a")
    world.worktree("b")
    p = run(world, "gwr")
    assert p.returncode == 3, p.stderr
    assert "terminal" in p.stderr


def test_remove_without_a_terminal_says_to_narrow_the_query(world) -> None:
    """gwr has no --list, and --json still needs one worktree."""
    world.worktree("a")
    world.worktree("b")
    p = run(world, "gwr")
    assert p.returncode == 3, p.stderr
    assert "2 worktrees to choose from and this is not a terminal" in p.stderr
    assert "narrow the query to one" in p.stderr
    assert "--list" not in p.stderr


@pytest.mark.parametrize(
    ("answer", "expected"),
    [("y", True), ("Y", True), ("yes", True), ("n", False), ("", False), ("q", False)],
)
def test_confirm_takes_yes_and_nothing_else(answer: str, expected: bool) -> None:
    assert prune.confirm(1, ask=lambda _: answer) is expected


def test_prune_deletes_the_branch_and_the_empty_parents(world) -> None:
    wt = world.finished("done")
    p = run(world, "gwp", "--yes")
    assert p.returncode == 0, p.stderr
    assert not wt.exists()
    assert "done" not in world.git("branch", "--format=%(refname:short)").split()
    assert not (world.parent / ".worktrees" / "done").exists()
    assert p.stdout.splitlines()[-1] == "removed 1 worktree(s): done"


def test_a_squash_merged_branch_goes_with_its_checkout(world) -> None:
    """The proof `-d` cannot read is the proof this deletes on."""
    wt = world.worktree("squashed")
    world.commit("f.txt", "one\n", at=wt)
    (world.repo / "f.txt").write_text("one\n")
    world.git("add", "--", "f.txt")
    world.git("commit", "--quiet", "-m", "squash")

    p = run(world, "gwp", "--yes")
    assert p.returncode == 0, p.stderr
    assert not wt.exists()
    assert "squashed" not in world.git("branch", "--format=%(refname:short)").split()
    assert "kept" not in p.stderr
    assert "restore with: git branch squashed " in p.stderr


def test_prune_clears_a_stale_record_when_it_removes(world) -> None:
    world.finished("done")
    wt = world.finished("gone")
    import shutil

    shutil.rmtree(wt)
    p = run(world, "gwp", "--yes")
    assert p.returncode == 0, p.stderr
    assert "gone" not in world.git("worktree", "list", "--porcelain")


def test_prune_leaves_a_stale_record_when_it_removes_nothing(world) -> None:
    wt = world.worktree("gone")
    import shutil

    shutil.rmtree(wt)
    p = run(world, "gwp", "--yes")
    assert p.returncode == 0, p.stderr
    assert "nothing to remove" in p.stderr
    assert "gone" in world.git("worktree", "list", "--porcelain")


def test_prune_says_so_when_there_is_nothing(world) -> None:
    p = run(world, "gwp", "--yes")
    assert p.returncode == 0, p.stderr
    assert "it is the main checkout" in p.stdout
    assert "no worktrees besides the main checkout; gwa NAME makes one" in p.stderr


def test_prune_prints_the_reason_rather_than_naming_the_command(world) -> None:
    """Why each one stays is in the table, not behind another command."""
    world.worktree("busy")
    (world.parent / ".worktrees" / "busy" / "repo" / "wip").write_text("x")
    p = run(world, "gwp", "--yes")
    assert p.returncode == 0, p.stderr
    assert "busy" in p.stdout
    assert "it has uncommitted changes" in p.stdout
    assert "0 eligible, 2 kept, 0 unclear" in p.stdout
    assert "nothing to remove" in p.stderr


def test_version_is_a_command_and_not_a_flag(world) -> None:
    for entry in ("gws", "gw", "main"):
        p = run(world, entry, "--version")
        assert p.returncode == 2
        assert "unrecognized arguments: --version" in p.stderr


def test_prune_refuses_dry_run_by_name(world) -> None:
    for argv in (("gwp",), ("gw", "prune"), ("gw", "p")):
        p = run(world, *argv, "--dry-run")
        assert p.returncode == 2
        assert "there is no --dry-run" in p.stderr
        assert "gwp reports without a terminal and asks at one" in p.stderr


@pytest.mark.parametrize("argv", [("gws",), ("gw", "sync"), ("gwr", "x")])
def test_other_commands_refuse_dry_run_as_an_unknown_flag(world, argv) -> None:
    p = run(world, *argv, "--dry-run")
    assert p.returncode == 2
    assert "unrecognized arguments: --dry-run" in p.stderr
    assert "gwp" not in p.stderr


# --------------------------------------------------------------------------
# gwp reports first, then asks only at a terminal
# --------------------------------------------------------------------------


def _snapshot(world) -> tuple[str, str, list[str]]:
    """Everything a removal or a `worktree prune` would change."""
    return (
        world.git("worktree", "list", "--porcelain"),
        world.git("for-each-ref"),
        sorted(str(p.relative_to(world.root)) for p in world.root.rglob("*")),
    )


@pytest.mark.parametrize(
    ("args", "terminal"),
    [
        ((), False),
        (("--json",), False),
        (("--list",), False),
        (("-q",), False),
        (("--yes",), False),
        (("--json",), True),
        (("--list",), True),
        ((), True),
    ],
    ids=[
        "plain",
        "json",
        "list",
        "quiet",
        "yes-nothing",
        "tty-json",
        "tty-list",
        "tty-no",
    ],
)
def test_a_gwp_that_removes_nothing_writes_nothing(world, args, terminal) -> None:
    """A stale record present, which a `git worktree prune` would clear."""
    import shutil

    if args != ("--yes",):
        world.worktree("done")
    shutil.rmtree(world.worktree("gone"))
    before = _snapshot(world)
    if terminal:
        # "y" for the modes that must not ask: a misplaced prompt removes.
        code, out = run_on_a_terminal(
            world, "gwp", *args, answer="n\n" if not args else "y\n"
        )
        assert code == 0, out
    else:
        p = run(world, "gwp", *args)
        assert p.returncode == 0, p.stderr
    assert _snapshot(world) == before


def test_gwp_prints_the_table_before_it_asks(world) -> None:
    world.finished("done")
    code, out = run_on_a_terminal(world, "gwp", answer="n\n")
    assert code == 0, out
    assert "nothing removed" in out
    assert out.index("1 eligible, 1 kept, 0 unclear") < out.index("restore with")
    assert out.index("restore with") < out.index("remove 1 worktree(s)? [y/N]")


def test_gwp_yes_at_a_terminal_does_not_ask(world) -> None:
    wt = world.finished("done")
    code, out = run_on_a_terminal(world, "gwp", "--yes", answer="n\n")
    assert code == 0, out
    assert "[y/N]" not in out
    assert "restore with: git branch done " in out
    assert not wt.exists()


@pytest.mark.parametrize("flag", ["--json", "--list"])
def test_gwp_at_a_terminal_with_a_script_flag_does_not_ask(world, flag) -> None:
    wt = world.finished("done")
    code, out = run_on_a_terminal(world, "gwp", flag, answer="y\n")
    assert code == 0, out
    assert "[y/N]" not in out
    assert ("1 would be removed; gwp --yes removes them" in out) == (flag == "--list")
    assert wt.exists()


@pytest.mark.parametrize("flag", [(), ("--json",), ("--list",)])
def test_gwp_yes_removes_in_every_mode(world, flag) -> None:
    wt = world.finished("done")
    p = run(world, "gwp", "--yes", *flag)
    assert p.returncode == 0, p.stderr
    assert "restore with: git branch done " in p.stderr
    assert not wt.exists()


def test_gwp_json_with_yes_adds_what_it_removed(world) -> None:
    world.finished("done")
    assessed = json.loads(run(world, "gwp", "--json").stdout)
    p = run(world, "gwp", "--json", "--yes")
    assert p.returncode == 0, p.stderr
    out = json.loads(p.stdout)
    assert out == {**assessed, "removed": ["done"], "failed": 0}


def test_gwp_list_is_never_coloured(world) -> None:
    world.worktree("done")
    world.worktree("busy")
    (world.parent / ".worktrees" / "busy" / "repo" / "wip").write_text("x")
    for args in (("--list",), ("--list", "--yes")):
        code, out = run_with_a_terminal_on_stdout(world, "gwp", *args)
        assert code == 0, out
        assert "\x1b" not in out, (args, out)
        assert "busy" in out
    code, out = run_with_a_terminal_on_stdout(world, "gwp")
    assert "\x1b" in out, "the control: the same stdout is painted without --list"


def test_gwp_list_puts_the_table_alone_on_stdout(world) -> None:
    world.finished("done")
    listed = run(world, "gwp", "--list")
    assert len(listed.stdout.splitlines()) == 3, listed.stdout
    assert "1 eligible, 1 kept, 0 unclear" in listed.stderr
    p = run(world, "gwp", "--list", "--yes")
    assert p.returncode == 0, p.stderr
    assert p.stdout == listed.stdout
    assert "1 eligible, 1 kept, 0 unclear" in p.stderr
    assert "removed 1 worktree(s): done" in p.stderr


def test_gwp_only_help_says_the_targets_stay(world) -> None:
    p = run(world, "gwp", "--help")
    assert "what is removed stays the same" in " ".join(p.stdout.split())


def test_gwp_only_narrows_the_table_and_not_the_targets(world) -> None:
    wt = world.finished("done")
    world.commit("wip", at=world.finished("busy"))
    p = run(world, "gwp", "--only", "keep", "--yes")
    assert p.returncode == 0, p.stderr
    table_rows = p.stdout.split("removed")[0]
    assert "busy" in table_rows
    assert "/.worktrees/done/" not in table_rows
    assert "restore with: git branch done " in p.stderr
    assert not wt.exists()


def test_gwp_only_narrows_the_json_rows(world) -> None:
    """By verdict, so a protected `remove` row is among them."""
    world.finished("done")
    world.finished("busy")
    world.commit("wip", at=world.finished("mine"))
    (world.parent / ".worktrees" / "busy" / "repo" / "wip").write_text("x")
    p = run(world, "gwp", "--only", "remove", "--json")
    assert p.returncode == 0, p.stderr
    rows = [(v["branch"], v["eligible"]) for v in json.loads(p.stdout)["verdicts"]]
    assert rows == [("done", True), ("busy", False)]
    # The data is the answer; a hint naming `gwp --yes` is not for a script.
    assert "would be removed" not in p.stderr


def test_gwp_with_nothing_eligible_says_so(world) -> None:
    world.worktree("busy")
    (world.parent / ".worktrees" / "busy" / "repo" / "wip").write_text("x")
    code, out = run_on_a_terminal(world, "gwp", answer="y\n")
    assert code == 0, out
    assert "it has uncommitted changes" in out
    assert "nothing to remove" in out
    assert "[y/N]" not in out


def test_explain_lists_every_command_and_runs_none(world) -> None:
    p = run(world, "gwp", "--explain")
    assert p.returncode == 0, p.stderr
    assert "git worktree list --porcelain -z" in p.stdout
    assert "git update-ref --no-deref -d $ref $sha" in p.stdout
    assert "git branch -D" not in p.stdout


def test_verbose_prints_the_argv(world) -> None:
    world.finished("done")
    p = run(world, "gwp", "--verbose")
    assert p.returncode == 0, p.stderr
    # The canary: a run that never reached git would print nothing here, and an
    # empty stream is indistinguishable from a refusal that worked.
    assert "+ git worktree list --porcelain -z" in p.stderr
    assert "--no-optional-locks status --porcelain" in p.stderr
    assert "--is-ancestor" in p.stderr


def test_status_never_issues_a_mutating_command(world) -> None:
    """Not even a fetch: a report writes nothing."""
    world.worktree("done")
    p = run(world, "gwp", "--verbose")
    issued = [ln for ln in p.stderr.splitlines() if ln.startswith("+ git")]
    assert issued
    for line in issued:
        forbidden = (
            "fetch",
            "worktree prune",
            "worktree remove",
            "update-ref",
            "checkout",
        )
        for bad in forbidden:
            assert bad not in line, line


def test_you_cannot_prune_the_worktree_you_stand_in(world) -> None:
    wt = world.worktree("here")
    p = run(world, "gwp", at=wt)
    assert p.returncode == 0, p.stderr
    assert "you are standing in it" in p.stdout
    assert wt.exists()


# --------------------------------------------------------------------------
# the CLI name
# --------------------------------------------------------------------------


def test_the_cli_name_takes_a_subcommand(world) -> None:
    world.worktree("done")
    p = run(world, "main", "prune", "--json")
    assert p.returncode == 0, p.stderr
    assert "done" in {v["branch"] for v in json.loads(p.stdout)["verdicts"]}


@pytest.mark.parametrize(
    "args", [(), ("one",), ("--json",), ("--list",), ("one", "--list")]
)
@pytest.mark.parametrize("entry", ["gw", "main"])
def test_anything_but_a_command_runs_list(world, entry: str, args) -> None:
    """The query and every flag go to `gw list`, nothing at all included."""
    world.worktree("one")
    world.worktree("two")
    p = run(world, entry, *args)
    listed = run(world, entry, "list", *args)
    assert p.returncode == listed.returncode == 0, p.stderr
    assert p.stdout == listed.stdout
    assert p.stderr == listed.stderr


def test_a_command_beats_a_worktree_of_the_same_name(world) -> None:
    world.worktree("prune")
    p = run(world, "gw", "prune", "--json")
    assert p.returncode == 0, p.stderr
    assert "verdicts" in json.loads(p.stdout)
    listed = json.loads(run(world, "gw", "list", "prune", "--json").stdout)
    assert [w["branch"] for w in listed["worktrees"]] == ["prune"]


def test_a_query_near_a_command_names_it(world) -> None:
    """A mistyped command reaches `gw list` as a query."""
    p = run(world, "gw", "verison")
    assert p.returncode == 1
    assert p.stdout == ""
    assert p.stderr.splitlines() == [
        "no worktree matches",
        "the closest command is: gw version",
        "gw -h lists every command",
    ]


def test_a_query_near_a_command_names_it_under_either_name(world) -> None:
    p = run(world, "main", "list", "prnue")
    assert p.returncode == 1
    assert p.stderr.splitlines() == [
        "no worktree matches",
        "the closest command is: worktrees prune",
        "worktrees -h lists every command",
    ]


@pytest.mark.parametrize(("typed", "near"), [("help", "--help"), ("ls", "list")])
def test_a_removed_name_is_a_query(world, typed: str, near: str) -> None:
    p = run(world, "gw", typed)
    assert p.returncode == 1
    assert p.stdout == ""
    assert p.stderr.splitlines() == [
        "no worktree matches",
        f"the closest command is: gw {near}",
        "gw -h lists every command",
    ]


# --------------------------------------------------------------------------
# shorthands, and the table they come from
# --------------------------------------------------------------------------


@pytest.mark.parametrize("spelling", ["prune", "p"])
def test_every_shorthand_reaches_the_same_command(world, spelling: str) -> None:
    world.worktree("done")
    p = run(world, "main", spelling, "--json")
    assert p.returncode == 0, p.stderr
    assert "done" in {v["branch"] for v in json.loads(p.stdout)["verdicts"]}


@pytest.mark.parametrize("typed", ["status", "st", "s"])
def test_the_status_names_are_worktree_queries(world, typed: str) -> None:
    world.worktree(typed)
    world.worktree("other")
    p = run(world, "gw", typed, "--json")
    listed = run(world, "gw", "list", typed, "--json")
    assert p.returncode == listed.returncode == 0, p.stderr
    assert p.stdout == listed.stdout
    assert [w["branch"] for w in json.loads(p.stdout)["worktrees"]] == [typed]


@pytest.mark.parametrize(
    ("spelling", "canonical"),
    [
        ("p", "prune"),
        ("a", "add"),
        ("m", "move"),
        ("mv", "move"),
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


def test_every_command_is_installed_under_its_own_name() -> None:
    """The help table names a binary per row; pyproject is what ships them."""
    import tomllib

    from worktrees import cli

    root = Path(__file__).resolve().parents[1]
    scripts = tomllib.loads((root / "pyproject.toml").read_text())["project"]["scripts"]
    declared = {c.binary for _, c in cli.command_rows() if c.binary}
    assert declared <= set(scripts)
    # The two that take a subcommand are the only ones with no row of their own.
    assert set(scripts) - declared == {"gw", "worktrees"}
    # and list (`gw` alone runs it) and version install nothing.
    assert [n for n, c in cli.command_rows() if not c.binary] == [
        "list",
        "version",
    ]


# --------------------------------------------------------------------------
# gw -h
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("entry", "flag"),
    [("gw", "-h"), ("gw", "--help"), ("main", "-h"), ("main", "--help")],
)
def test_help_names_every_command_and_every_shorthand(
    world, tmp_path: Path, entry: str, flag: str
) -> None:
    """Read outside a repository too, so it asks git nothing."""
    p = run(world, entry, flag, at=tmp_path)
    assert p.returncode == 0, p.stderr
    from worktrees import cli

    for name, command in cli.command_rows():
        assert command.binary in p.stderr, name
        assert f"gw {name}" in p.stderr, name
        for alias in command.aliases:
            assert alias in p.stderr, alias
    assert "shell plugin" in p.stderr
    assert "gw <command> --help" in p.stderr


def test_help_runs_no_git(world, tmp_path: Path) -> None:
    """A PATH with no git on it, so any git the help spawned would fail."""
    p = subprocess.run(
        [
            sys.executable,
            "-c",
            "from worktrees.cli import gw; raise SystemExit(gw())",
            "-h",
        ],
        cwd=str(tmp_path),
        capture_output=True,
        text=True,
        env={**env_for(world), "PATH": str(tmp_path)},
    )
    assert p.returncode == 0, p.stderr
    assert "gw version (v)" in p.stderr


@pytest.mark.parametrize("entry", ["gw", "main"])
def test_help_goes_to_stderr_since_gw_changes_directory(world, entry) -> None:
    """`gw`'s shell function captures stdout to find where to go."""
    p = run(world, entry, "-h")
    assert p.stdout == ""
    assert "gw with no subcommand runs gw list." in p.stderr
    assert "gwa, gw, gwm and gwr change the directory" in p.stderr
    assert "gw [QUERY]       gw list " in p.stderr


@pytest.mark.parametrize(
    "argv", [("gws", "-h"), ("gwbs", "--help"), ("gw", "prune", "-h")]
)
def test_every_other_command_keeps_its_own_help(world, argv: tuple[str, ...]) -> None:
    p = run(world, *argv)
    assert p.returncode == 0, p.stderr
    assert p.stdout.startswith("usage: ")


@pytest.mark.parametrize("flag", ["--json", "-q", "-v", "--explain"])
def test_help_promises_no_flag_the_program_refuses(world, flag: str) -> None:
    """gw version declares no flags, so the footer has to carve it out by
    name. The footer derives that from the table rather than asserting it in
    prose."""
    p = run(world, "gw", "version", flag)
    assert p.returncode == 2
    assert "unrecognized arguments" in p.stderr

    listed = run(world, "gw", "-h")
    assert "work on every command but gw version" in listed.stderr
    # and the flags it does claim are taken by a command that has a row
    assert run(world, "gwp", flag).returncode == 0


def test_help_stays_inside_eighty_columns(world) -> None:
    """It is read at a prompt, next to the commands it describes."""
    p = run(world, "gw", "-h")
    assert p.returncode == 0, p.stderr
    assert [ln for ln in p.stderr.splitlines() if len(ln) > 80] == []


# --------------------------------------------------------------------------
# colour
# --------------------------------------------------------------------------


def test_colour_reaches_a_terminal(world) -> None:
    world.finished("done")
    code, out = run_with_a_terminal_on_stdout(world, "gwp")
    assert code == 0, out
    assert "[32mremove" in out


def test_a_pipe_gets_no_colour(world) -> None:
    world.worktree("done")
    p = run(world, "gwp")
    assert p.returncode == 0, p.stderr
    assert "[" not in p.stdout


def test_json_is_never_coloured_even_on_a_terminal(world) -> None:
    """stdout carries what jq parses and what `cd $(gwa x)` reads."""
    world.worktree("done")
    code, out = run_with_a_terminal_on_stdout(world, "gwp", "--json")
    assert code == 0, out
    assert "[" not in out
    assert "done" in {v["branch"] for v in json.loads(out)["verdicts"]}


def test_no_color_turns_it_off_on_a_terminal(world) -> None:
    world.worktree("done")
    code, out = run_with_a_terminal_on_stdout(world, "gwp", env_extra={"NO_COLOR": "1"})
    assert code == 0, out
    assert "[" not in out


# --------------------------------------------------------------------------
# an unreachable remote is never asked
# --------------------------------------------------------------------------


def _unreachable(world) -> None:
    world.git("remote", "add", "origin", str(world.root / "nowhere.git"))


def test_an_unreachable_remote_still_makes_the_branch(world) -> None:
    """Off the refs already here, saying only that nothing has synced."""
    _unreachable(world)
    world.git("update-ref", "refs/remotes/origin/main", "main")
    world.git("symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/main")
    p = run(world, "gwbc", "spike")
    assert p.returncode == 0, p.stderr
    assert p.stderr == f"{NEVER}\nHead: origin/main (clone)\n"
    assert "spike from origin/main" in p.stdout
    assert world.git("rev-parse", "--verify", "refs/heads/spike")


def test_an_unreachable_remote_still_makes_the_worktree(world) -> None:
    _unreachable(world)
    world.git("update-ref", "refs/remotes/origin/main", "main")
    world.git("symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/main")
    p = run(world, "gwa", "fix-parser")
    assert p.returncode == 0, p.stderr
    assert "fetch" not in p.stderr.replace(NEVER, "")
    assert (world.parent / ".worktrees" / "fix-parser" / "repo").is_dir()


def test_an_unreachable_remote_still_answers_status(world) -> None:
    _unreachable(world)
    world.worktree("done")
    p = run(world, "gwp")
    assert p.returncode == 0, p.stderr
    assert "fetch" not in p.stderr.replace(NEVER, "")
    assert "done" in p.stdout


# --------------------------------------------------------------------------
# the default remote
# --------------------------------------------------------------------------


def _remotes(world, *names: str) -> None:
    """Remotes that cannot be reached, each with a main of its own and a
    HEAD naming it, the way a clone leaves one."""
    for name in names:
        world.git("remote", "add", name, str(world.root / f"{name}.git"))
        world.git("update-ref", f"refs/remotes/{name}/main", "main")
        world.git(
            "symbolic-ref", f"refs/remotes/{name}/HEAD", f"refs/remotes/{name}/main"
        )


def test_checkout_default_remote_picks_among_several(world) -> None:
    _remotes(world, "origin", "a", "b")
    world.git("config", "checkout.defaultRemote", "b")
    p = run(world, "gwbc", "spike")
    assert p.returncode == 0, p.stderr
    assert "spike from b/main at" in p.stdout


def test_the_only_remote_is_the_default(world) -> None:
    _remotes(world, "upstream")
    p = run(world, "gwbc", "spike")
    assert p.returncode == 0, p.stderr
    assert "spike from upstream/main at" in p.stdout


def test_several_remotes_without_the_setting_are_no_remote(world) -> None:
    """Not origin, and not the branch's own remote: nothing says which."""
    _remotes(world, "origin", "upstream")
    world.git("config", "branch.main.remote", "origin")
    p = run(world, "gwbc", "spike")
    assert p.returncode == 0, p.stderr
    assert "spike from main at" in p.stdout


@pytest.mark.parametrize(
    ("names", "base"),
    [(("origin", "upstream"), "main"), (("upstream",), "upstream/main")],
)
def test_a_default_remote_that_does_not_exist_is_ignored(world, names, base) -> None:
    _remotes(world, *names)
    world.git("config", "checkout.defaultRemote", "gone")
    p = run(world, "gwbc", "spike")
    assert p.returncode == 0, p.stderr
    assert f"spike from {base} at" in p.stdout


def test_explain_names_every_refusal_the_guard_holds(world) -> None:
    """The footer is generated from the rules, so the seventh is in it."""
    p = run(world, "gwp", "--explain")
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


# --------------------------------------------------------------------------
# a stash outlives the branch it names
# --------------------------------------------------------------------------


def test_a_removal_names_the_stash_made_on_the_branch(world) -> None:
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

    p = run(world, "gwp", "-y")
    assert p.returncode == 0, p.stderr
    assert "stash@{0} was made on this branch and outlives it" in p.stderr
    assert "git stash branch <new> stash@{0}" in p.stderr
    # And it did outlive it.
    assert world.git("stash", "list")
    assert "wip" not in world.git("branch", "--format=%(refname:short)").split()


def test_a_stash_on_another_branch_is_not_named(world) -> None:
    wt = world.worktree("wip")
    world.commit("f.txt", "one\n", at=wt)
    (world.repo / "other.txt").write_text("x\n")
    world.git("add", "--", "other.txt")
    world.git("stash", "push", "--quiet", "-m", "elsewhere")

    (world.repo / "f.txt").write_text("one\n")
    world.git("add", "--", "f.txt")
    world.git("commit", "--quiet", "-m", "squash of wip")

    p = run(world, "gwp", "-y")
    assert p.returncode == 0, p.stderr
    assert "was made on this branch" not in p.stderr


def test_list_with_no_query_prompts_on_a_terminal(world) -> None:
    """One other worktree, and it still asks, because nothing said which.

    The prompt is the whole point: `gw` with no argument means show me the
    options, and one option is still an option rather than a destination.
    """
    world.worktree("one")
    code, out = run_on_a_terminal(world, "gw", answer="1\n")
    assert code == 0, out
    assert "which? [up/down and enter, 1-2, or esc to cancel]" in out
    assert "one" in out


def test_list_with_no_query_takes_esc_as_cancelled(world) -> None:
    world.worktree("one")
    code, out = run_on_a_terminal(world, "gw", answer="\x1b")
    assert code == 0, out
    assert "nothing picked" in out


def test_list_takes_one_match_outright_on_a_terminal(world) -> None:
    world.worktree("one")
    two = world.worktree("two")
    code, out = run_on_a_terminal(world, "gw", "two", answer="")
    assert code == 0, out
    assert "which?" not in out
    assert out.strip() == str(two), out


def test_list_says_so_rather_than_landing_you_where_you_are(world) -> None:
    """One candidate, standing in it: a note, and no path to cd into."""
    wt = world.worktree("one")
    code, out = run_on_a_terminal_at(world, wt, "gw", "one", answer="")
    assert code == 0, out
    assert "already in one" in out
    assert str(wt) not in out


def test_list_offers_main_to_somebody_standing_in_it(world) -> None:
    """From a subdirectory of the main checkout, `gw main` is the way back."""
    world.worktree("one")
    (world.repo / "sub").mkdir()
    code, out = run_on_a_terminal_at(world, world.repo / "sub", "gw", "main", answer="")
    assert code == 0, out
    assert out.strip() == str(world.repo), out


def test_list_on_a_terminal_in_a_repository_with_no_worktrees_says_so(world) -> None:
    code, out = run_on_a_terminal(world, "gw", answer="")
    assert code == 1, out
    assert "this is the only worktree" in out


def test_list_moves_with_the_arrow_keys_and_picks_on_enter(world) -> None:
    world.worktree("one")
    two = world.worktree("two")
    code, out = run_on_a_terminal(world, "gw", answer="\x1b[B\x1b[B\r")
    assert code == 0, out
    assert out.rstrip().endswith(str(two)), out


@pytest.mark.parametrize(
    "entry,args,prompt",
    [
        (entry, (*prefix, *flags), prompt)
        for command, flags, prompt in (
            ("list", (), "which? [up/down and enter, 1-3, or esc to cancel] "),
            (
                "remove",
                (),
                "which? [up/down and enter, 1-2, or esc to cancel] ",
            ),
            (
                "remove",
                ("one",),
                "remove 1 worktree(s)? [y/N] ",
            ),
            ("prune", (), "remove 2 worktree(s)? [y/N] "),
        )
        for entry, prefix in (
            (cli._COMMANDS[command].binary or "gw", ()),
            *(
                (entry, (name,))
                for entry in ("gw", "main")
                for name in (command, *cli._COMMANDS[command].aliases)
            ),
        )
    ],
)
def test_ctrl_c_at_every_prompt(world, entry, args, prompt) -> None:
    """SIGINT cancels selection and confirmation without changing any worktree."""
    paths = [world.finished(name) for name in ("one", "two")]
    before = world.git("worktree", "list", "--porcelain")
    refs = world.git("show-ref")
    parent, child = pty.openpty()
    try:
        with subprocess.Popen(
            [
                sys.executable,
                "-c",
                f"from worktrees.cli import {entry}; raise SystemExit({entry}())",
                *args,
            ],
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
    # gwp prints its assessment before it asks; nothing else prints a result.
    if prompt.startswith("remove 2"):
        assert b"2 eligible" in out and b"removed" not in out, out
    else:
        assert out == b""
    assert stderr.endswith(prompt + "\n"), stderr
    assert world.git("worktree", "list", "--porcelain") == before
    assert world.git("show-ref") == refs
    assert all((path / "README").exists() for path in paths)


def test_status_lists_the_main_checkout_and_never_proposes_it(world) -> None:
    """A listing that leaves it out shows one row where `git worktree list`
    shows two, which reads as though something went missing."""
    world.finished("done")
    p = run(world, "gwp")
    assert p.returncode == 0, p.stderr
    assert "it is the main checkout, and never removable" in p.stdout
    assert p.stdout.count("keep") == 1
    assert "1 eligible, 1 kept, 0 unclear" in p.stdout
    rows = {
        v["branch"]: v
        for v in json.loads(run(world, "gwp", "--json").stdout)["verdicts"]
    }
    assert set(rows) == {"main", "done"}
    assert rows["main"]["verdict"] == "keep"


def test_remove_is_never_offered_the_main_checkout(world) -> None:
    """`gwr` takes the same list as its candidates, and `git worktree remove`
    answers `fatal: '<path>' is a main working tree` whatever flag it is
    given, so offering it would propose something certain to be refused.
    """
    world.worktree("done")
    p = run(world, "gwr", "--complete")
    assert p.returncode == 0, p.stderr
    assert "main" not in p.stdout
    assert "done" in p.stdout


def test_list_shows_the_worktree_you_are_standing_in_unnumbered(world) -> None:
    """Marked, above the ones you can go to, and carrying no number: it is
    where you are rather than somewhere to go."""
    wt = world.worktree("cleanup")
    code, out = run_on_a_terminal_at(world, wt, "gw", answer="\x1b")
    assert code == 0, out
    assert re.search(r"^\s+#\s+WORKTREE\s+BRANCH\s+PATH$", out, re.M), out
    assert re.search(r"^  \*\s+cleanup\s+cleanup\s", out, re.M), out
    assert re.search(r"^> 1\s+repo\s+main\s", out, re.M), out
    assert "which? [up/down and enter, 1-1, or esc to cancel]" in out


def test_remove_starts_on_the_worktree_you_stand_in_and_lists_main_unnumbered(
    world,
) -> None:
    world.worktree("one")
    two = world.worktree("two")
    code, out = run_on_a_terminal_at(world, two, "gwr", answer="\x1b")
    assert code == 0, out
    assert "  1  one" in out
    assert "> 2  two" in out
    assert re.search(r"^ {5}repo\s+main\s", out, re.M), out
    assert "nothing picked" in out


def test_remove_asks_before_deleting_ignored_files_on_a_terminal(world) -> None:
    """The refusal becomes a question, and every ignored path is named first."""
    (world.repo / ".gitignore").write_text(".env\n")
    world.git("add", "--", ".gitignore")
    world.git("commit", "--quiet", "-m", "ignore")
    wt = world.finished("holds")
    (wt / ".env").write_text("SECRET=1\n")

    code, out = run_on_a_terminal(world, "gwr", "holds", answer="n\n")
    assert code == 0, out
    assert "holds 1 ignored path(s); they go with it" in out
    assert ".env" in out
    assert "nothing removed" in out
    assert wt.exists()

    code, out = run_on_a_terminal(world, "gwr", "holds", answer="y\n")
    assert code == 0, out
    assert not wt.exists()


def test_remove_asks_before_removing_an_unfinished_worktree_and_keeps_the_branch(
    world,
) -> None:
    wt = world.worktree("busy")
    world.commit("b.txt", "b\n", at=wt)
    code, out = run_on_a_terminal(world, "gwr", "busy", answer="n\n")
    assert code == 0, out
    assert "is not finished" in out
    assert "removing the worktree keeps the branch" in out
    assert wt.exists()

    code, out = run_on_a_terminal(world, "gwr", "busy", answer="y\n")
    assert code == 0, out
    assert not wt.exists()
    assert world.git("rev-parse", "--verify", "refs/heads/busy")


def test_list_marks_main_when_you_stand_in_it(world) -> None:
    world.worktree("one")
    code, out = run_on_a_terminal(world, "gw", answer="\x1b")
    assert code == 0, out
    assert re.search(r"^> 1\s+repo\s+main \*\s", out, re.M), out
    assert re.search(r"^  2\s+one\s+one\s", out, re.M), out


def _two_answering_to_x(world) -> None:
    """One worktree named x on branch a, one named b on branch x."""
    for name, branch in (("x", "a"), ("b", "x")):
        path = world.parent / ".worktrees" / name / world.repo.name
        world.git("worktree", "add", "--quiet", "-b", branch, str(path))


def test_remove_offers_only_the_exact_matches_on_a_terminal(world) -> None:
    _two_answering_to_x(world)
    world.worktree("x-more")
    code, out = run_on_a_terminal(world, "gwr", "x", answer="\x1b")
    assert code == 0, out
    assert re.search(r"^  \*\s+repo\s+main\s", out, re.M), out
    assert "1-2, or esc" in out
    assert "x-more" not in out
    assert "nothing picked" in out


def test_list_is_never_coloured_even_on_a_terminal(world) -> None:
    """--list, like --json, is what a script reads."""
    world.worktree("one")
    pid, fd = pty.fork()
    if pid == 0:
        os.chdir(world.repo)
        os.execve(
            sys.executable,
            [
                sys.executable,
                "-c",
                "from worktrees.cli import gw; raise SystemExit(gw())",
                "--list",
            ],
            {**env_for(world), "TERM": "xterm"},
        )
    out = b""
    while True:
        try:
            chunk = os.read(fd, 4096)
        except OSError:
            break
        if not chunk:
            break
        out += chunk
    os.waitpid(pid, 0)
    assert b"WORKTREE" in out
    assert b"\x1b[" not in out


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
    code, out = run_on_a_terminal(world, "gw", answer="\x1b")
    assert code == 0, out
    head = out.splitlines()
    assert head[0].endswith("origin: github.com/org/repo")
    assert head[1].endswith("upstream: github.com/up/repo")
    assert "tok" not in out


def test_list_for_a_script_names_no_remote(world) -> None:
    world.git("remote", "add", "origin", "git@github.com:org/repo.git")
    world.worktree("one")
    p = run(world, "gw", "--list")
    assert p.returncode == 0, p.stderr
    assert "github.com" not in p.stdout + p.stderr


def test_status_only_shows_the_verdicts_asked_for(world) -> None:
    """The table narrows; the counts still cover every row."""
    world.worktree("done")
    whole = run(world, "gwp")
    assert whole.returncode == 0, whole.stderr
    assert "keep" in whole.stdout

    p = run(world, "gwp", "--only", "remove")
    assert p.returncode == 0, p.stderr
    table, _, counts = p.stdout.partition("\n\n")
    assert "keep" not in table
    assert counts.strip() == whole.stdout.partition("\n\n")[2].strip()


def test_status_only_narrows_json_too(world) -> None:
    world.worktree("done")
    p = run(world, "gwp", "--json", "--only", "keep")
    assert p.returncode == 0, p.stderr
    verdicts = [v["verdict"] for v in json.loads(p.stdout)["verdicts"]]
    assert verdicts and set(verdicts) == {"keep"}


def test_only_takes_no_verdict_that_does_not_exist(world) -> None:
    p = run(world, "gwp", "--only", "maybe")
    assert p.returncode == 2
    assert "invalid choice" in p.stderr


@pytest.mark.parametrize("entry", ["gwp", "gwbd"])
def test_the_commands_that_remove_take_no_f(entry) -> None:
    """On them -f would read as forcing a removal."""
    import argparse

    p = argparse.ArgumentParser()
    if entry == "gwp":
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

    p = run(world, "gw", "--json")
    assert p.returncode == 0, p.stderr
    listed = [w["path"] for w in json.loads(p.stdout)["worktrees"]]
    assert listed == [str(world.repo), *map(str, reversed(paths))]


@pytest.mark.parametrize("entry", ["gwp", "gwr"])
def test_prune_and_remove_list_in_the_same_order_as_list(world, entry) -> None:
    """One order everywhere: oldest to newest after the main checkout."""
    paths = [world.worktree(name) for name in ("one", "two", "three")]
    for age, path in zip((300, 200, 100), reversed(paths), strict=True):
        admin = Path((path / ".git").read_text().strip().removeprefix("gitdir: "))
        old = time.time() - age
        os.utime(admin / "commondir", (old, old))

    args = ["--json"]
    if entry == "gwr":
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
        [
            sys.executable,
            "-c",
            "from worktrees.cli import gwa; raise SystemExit(gwa())",
            *args,
        ],
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


def test_gw_help_is_coloured_on_a_terminal_when_stdout_is_captured(world) -> None:
    """What `gw -h` meets inside its shell function: stdout captured, stderr
    on the terminal. Colour follows the stream the help reaches."""
    parent, child = pty.openpty()
    proc = subprocess.Popen(
        [
            sys.executable,
            "-c",
            "from worktrees.cli import gw; raise SystemExit(gw())",
            "-h",
        ],
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
    assert "\x1b[1mworktrees\x1b[0m" in b"".join(chunks).decode()


# --------------------------------------------------------------------------
# a protection clears eligible and leaves the verdict alone
# --------------------------------------------------------------------------


def _rows(world, *args: str, at: Path | None = None) -> dict[str, dict]:
    p = run(world, "gwp", "--json", *args, at=at)
    assert p.returncode == 0, p.stderr
    return {v["branch"] or "(detached)": v for v in json.loads(p.stdout)["verdicts"]}


def _ignoring_env(world, name: str) -> Path:
    """A finished worktree holding one ignored file, .env."""
    world.repo.joinpath(".gitignore").write_text(".env\n")
    world.git("add", ".gitignore")
    world.git("commit", "--quiet", "-m", "ignore")
    wt = world.finished(name)
    wt.joinpath(".env").write_text("S=1\n")
    return wt


PROOF = "its tip is an ancestor of the head branch"


def test_each_protection_keeps_the_verdict_and_clears_eligible(world) -> None:
    dirty = world.finished("dirty")
    dirty.joinpath("README").write_text("changed\n")
    world.finished("untracked").joinpath("new").write_text("x")
    locked = world.finished("locked")
    world.git("worktree", "lock", str(locked))
    _ignoring_env(world, "secrets")
    here = world.finished("here")
    world.finished("done")

    rows = _rows(world, at=here)
    blocks = {
        "dirty": "it has uncommitted changes",
        "untracked": "it has uncommitted changes",
        "locked": "it is locked",
        "secrets": "it holds 1 ignored path(s), and needs --delete ignored-files",
        "here": "you are standing in it",
    }
    for name, block in blocks.items():
        row = rows[name]
        assert (row["verdict"], row["eligible"]) == ("remove", False), row
        assert row["why"] == f"{PROOF}; {block}", row
    assert (rows["done"]["verdict"], rows["done"]["eligible"]) == ("remove", True)
    assert (rows["main"]["verdict"], rows["main"]["eligible"]) == ("keep", False)


def test_a_detached_worktree_is_protected_the_same_way(world) -> None:
    wt = world.parent / ".worktrees" / "loose" / world.repo.name
    world.git("worktree", "add", "--quiet", "--detach", str(wt), "main")
    wt.joinpath("wip").write_text("x")
    row = _rows(world)["(detached)"]
    assert (row["verdict"], row["eligible"]) == ("remove", False), row
    assert row["why"] == "its commit is reached by a ref; it has uncommitted changes"


@pytest.mark.parametrize(
    "delete",
    [
        ("--delete", "ignored-files"),
        ("--delete", "all"),
        ("--delete", "ignored-files", "--delete", "all"),
        ("--delete=all",),
    ],
)
def test_delete_ignored_files_makes_the_ignored_row_eligible(world, delete) -> None:
    _ignoring_env(world, "secrets")
    row = _rows(world, *delete)["secrets"]
    assert (row["verdict"], row["eligible"], row["why"]) == ("remove", True, PROOF)


def test_yes_grants_no_permission(world) -> None:
    wt = _ignoring_env(world, "secrets")
    p = run(world, "gwp", "--yes", "--json")
    assert p.returncode == 0, p.stderr
    assert json.loads(p.stdout)["removed"] == []
    assert wt.exists()


@pytest.mark.parametrize(
    "argv",
    [
        ("gwp", "--yes", "--delete", "bogus"),
        ("gwp", "--yes", "--delete-ignored"),
        ("gwr", "secrets", "--yes", "--delete", "bogus"),
        ("gwr", "secrets", "--yes", "--delete-ignored"),
        ("gwbs", "--delete", "all"),
        ("gwbd", "secrets", "--yes", "--delete", "ignored-files"),
    ],
)
def test_a_delete_value_a_command_does_not_take_is_a_usage_error(world, argv) -> None:
    wt = _ignoring_env(world, "secrets")
    p = run(world, *argv)
    assert p.returncode == 2, p.stderr
    assert "--delete" in p.stderr
    assert wt.exists()
    assert world.git("rev-parse", "--verify", "refs/heads/secrets")


def test_a_protection_on_an_unfinished_branch_adds_nothing_to_why(world) -> None:
    wt = world.worktree("busy")
    world.commit("work", at=wt)
    wt.joinpath("wip").write_text("x")
    row = _rows(world)["busy"]
    assert (row["verdict"], row["eligible"]) == ("keep", False)
    assert "uncommitted" not in row["why"]


def test_the_head_branch_worktree_is_not_judged(world) -> None:
    world.git("checkout", "--quiet", "--detach")
    wt = world.parent / ".worktrees" / "main" / world.repo.name
    world.git("worktree", "add", "--quiet", str(wt), "main")
    row = _rows(world)["main"]
    assert (row["verdict"], row["eligible"], row["why"]) == (
        "keep",
        False,
        "it is the head branch",
    )


def test_the_counts_count_eligible_rows(world) -> None:
    world.finished("done")
    world.finished("dirty").joinpath("wip").write_text("x")
    p = run(world, "gwp")
    assert p.returncode == 0, p.stderr
    assert "1 eligible, 2 kept, 0 unclear" in p.stdout
    assert "1 would be removed; gwp --yes removes them" in p.stderr


def test_prune_yes_removes_only_eligible_rows(world) -> None:
    done = world.finished("done")
    dirty = world.finished("dirty")
    dirty.joinpath("wip").write_text("x")
    locked = world.finished("locked")
    world.git("worktree", "lock", str(locked))

    p = run(world, "gwp", "--yes", "--json")
    assert p.returncode == 0, p.stderr
    assert json.loads(p.stdout)["removed"] == ["done"]
    assert not done.exists()
    assert dirty.exists() and locked.exists()
    assert world.git("rev-parse", "--verify", "refs/heads/dirty")
    assert world.git("rev-parse", "--verify", "refs/heads/locked")


def test_a_protected_remove_row_is_dimmed_on_a_terminal(world) -> None:
    world.finished("dirty").joinpath("wip").write_text("x")
    code, out = run_with_a_terminal_on_stdout(world, "gwp")
    assert code == 0, out
    line = next(ln for ln in out.splitlines() if "dirty" in ln)
    assert "\x1b[32mremove" in line, "the verdict keeps its colour"
    assert "\x1b[2m" + f"{PROOF}; it has uncommitted changes" in line


def test_a_locked_worktree_whose_directory_is_away_is_judged(world) -> None:
    """A lock is how git keeps a worktree on a drive that is not mounted, so
    its directory being gone is no error."""
    locked = world.finished("portable")
    world.git("worktree", "lock", str(locked))
    locked.rename(locked.with_name("unmounted"))
    row = _rows(world)["portable"]
    assert (row["verdict"], row["eligible"]) == ("remove", False), row
    assert row["why"] == f"{PROOF}; it is locked"


SUBMODULE = "it has an initialized submodule"


def _with_submodule(world) -> None:
    """main records a submodule from a local repository beside it."""
    lib = world.root / "lib"
    world.git("init", "--quiet", "-b", "main", str(lib))
    world.commit("lib.txt", at=lib)
    world.git(
        "-c", "protocol.file.allow=always", "submodule", "add", "--quiet", str(lib)
    )
    world.git("commit", "--quiet", "-m", "lib")


def _init_submodule(world, wt: Path) -> None:
    world.git(
        "-c",
        "protocol.file.allow=always",
        "submodule",
        "update",
        "--init",
        "--quiet",
        at=wt,
    )


def test_git_refuses_a_worktree_with_an_initialized_submodule(world) -> None:
    """The premise: deinit clears the checkout and git still refuses."""
    _with_submodule(world)
    wt = world.worktree("lib-work")
    _init_submodule(world, wt)
    world.git("submodule", "deinit", "--quiet", "--all", at=wt)
    proc = subprocess.run(
        ["git", "-C", str(world.repo), "worktree", "remove", str(wt)],
        capture_output=True,
        text=True,
    )
    assert proc.returncode != 0
    assert "containing submodules cannot be moved or removed" in proc.stderr


def test_an_initialized_submodule_clears_eligible(world) -> None:
    _with_submodule(world)
    attached = world.finished("lib-work")
    _init_submodule(world, attached)
    loose = world.parent / ".worktrees" / "loose" / world.repo.name
    world.git("worktree", "add", "--quiet", "--detach", str(loose), "main")
    _init_submodule(world, loose)
    world.finished("untouched")
    # Judged without the network: the submodule's origin is gone.
    for path in sorted((world.root / "lib").rglob("*"), reverse=True):
        path.rmdir() if path.is_dir() else path.unlink()

    rows = _rows(world)
    assert (rows["lib-work"]["verdict"], rows["lib-work"]["eligible"]) == (
        "remove",
        False,
    )
    assert rows["lib-work"]["why"] == f"{PROOF}; {SUBMODULE}"
    assert rows["(detached)"]["why"] == f"its commit is reached by a ref; {SUBMODULE}"
    assert not rows["(detached)"]["eligible"]
    # A submodule recorded but never initialized is no obstacle to git.
    assert rows["untouched"]["eligible"], rows["untouched"]


def test_a_deinitialized_submodule_still_clears_eligible(world) -> None:
    _with_submodule(world)
    wt = world.finished("lib-work")
    _init_submodule(world, wt)
    world.git("submodule", "deinit", "--quiet", "--all", at=wt)
    row = _rows(world)["lib-work"]
    assert (row["eligible"], row["why"]) == (False, f"{PROOF}; {SUBMODULE}")


@pytest.mark.parametrize("flags", [(), ("--force",), ("--force", "--yes")])
def test_remove_refuses_a_worktree_with_an_initialized_submodule(world, flags) -> None:
    _with_submodule(world)
    wt = world.worktree("lib-work")
    _init_submodule(world, wt)
    p = run(world, "gwr", "lib-work", *flags)
    assert p.returncode == 1, p.stderr
    assert f"lib-work cannot be removed: {SUBMODULE}" in p.stderr
    assert f"run git submodule deinit --all in {wt} and retry" in p.stderr
    assert wt.exists()
    assert world.git("rev-parse", "--verify", "refs/heads/lib-work")


def test_remove_refuses_an_unfinished_worktree_with_a_submodule_on_a_terminal(
    world,
) -> None:
    _with_submodule(world)
    wt = world.worktree("lib-work")
    world.commit("work", at=wt)
    _init_submodule(world, wt)
    code, out = run_on_a_terminal(world, "gwr", "lib-work", answer="y\n")
    assert code == 1, out
    assert "git submodule deinit --all" in out
    assert wt.exists()


# --------------------------------------------------------------------------
# a second look between the question and the removal
# --------------------------------------------------------------------------


def _ignore_env(world) -> None:
    world.repo.joinpath(".gitignore").write_text(".env\n")
    world.git("add", ".gitignore")
    world.git("commit", "--quiet", "-m", "ignore")


CHANGES: dict[str, tuple[Callable[[Any, Path], object], str]] = {
    "commit": (
        lambda world, wt: world.commit("late", at=wt),
        "it has moved to another commit",
    ),
    "switch": (
        lambda world, wt: world.git("switch", "--quiet", "-c", "elsewhere", at=wt),
        "it is on another branch now",
    ),
    "head": (
        lambda world, wt: world.commit("ahead"),
        "the head branch has moved",
    ),
    "dirty": (
        lambda world, wt: wt.joinpath("wip").write_text("x"),
        "its uncommitted changes changed",
    ),
    "lock": (
        lambda world, wt: world.git("worktree", "lock", str(wt)),
        "its lock changed",
    ),
    "ignored": (
        lambda world, wt: wt.joinpath(".env").write_text("S=1\n"),
        "its ignored files changed",
    ),
}


def _between(monkeypatch, change) -> None:
    """Make `change` after the disclosure, which is where the question
    stands, so under --yes it lands between that point and the removal."""
    original = prune.plan

    def plan(*args, **kwargs):
        never = original(*args, **kwargs)
        change()
        return never

    monkeypatch.setattr(prune, "plan", plan)


@pytest.mark.parametrize("entry", ["gwp", "gwr"])
@pytest.mark.parametrize("change", sorted(CHANGES))
def test_a_change_after_the_question_removes_nothing(
    world, monkeypatch, capsys, entry, change
) -> None:
    _ignore_env(world)
    wt = world.finished("done")
    make, why = CHANGES[change]
    _between(monkeypatch, lambda: make(world, wt))
    argv = ["--yes"] if entry == "gwp" else ["done", "--yes", "--delete", "all"]
    code = getattr(cli, entry)(argv)
    err = capsys.readouterr().err
    assert code == 1, err
    assert f"kept: {why}; nothing removed" in err
    assert wt.exists()
    assert world.git("rev-parse", "--verify", "refs/heads/done")


def test_a_target_no_longer_eligible_after_the_question_is_kept(
    world, monkeypatch, capsys
) -> None:
    """The facts hold still while the verdict moves: the one ref reaching a
    detached worktree's commit is deleted."""
    world.git("branch", "tmp")
    sha = world.commit("only-on-tmp", at=None)
    world.git("update-ref", "refs/heads/tmp", sha)
    world.git("reset", "--quiet", "--hard", "HEAD~1")
    wt = world.parent / ".worktrees" / "loose" / world.repo.name
    world.git("worktree", "add", "--quiet", "--detach", str(wt), "tmp")
    _between(monkeypatch, lambda: world.git("update-ref", "-d", "refs/heads/tmp"))
    code = cli.gwp(["--yes"])
    err = capsys.readouterr().err
    assert code == 1, err
    assert f"kept: it is not eligible now: no ref reaches {sha}" in err
    assert wt.exists()


def test_each_outcome_is_reported_on_its_own(world, capsys) -> None:
    wt = world.finished("done")
    code = cli.gwp(["--yes"])
    err = capsys.readouterr().err
    assert code == 0, err
    assert f"removed worktree {wt}" in err
    assert "deleted branch done" in err


def test_a_failed_ref_deletion_is_reported_as_failed(
    world, monkeypatch, capsys
) -> None:
    from worktrees.git import GitError

    wt = world.finished("done")

    def refuse(ref, sha, **kwargs):
        raise GitError("cannot lock ref")

    monkeypatch.setattr(prune, "ref_delete", refuse)
    code = cli.gwp(["--yes", "--json"])
    out = capsys.readouterr()
    assert code == 1, out.err
    result = json.loads(out.out)
    assert (result["removed"], result["failed"]) == (["done"], 1)
    assert "branch done not deleted: cannot lock ref" in out.err
    assert not wt.exists()
    assert world.git("rev-parse", "--verify", "refs/heads/done")


def test_git_refusing_the_worktree_skips_its_branch(world, monkeypatch, capsys) -> None:
    from worktrees.git import GitError

    wt = world.finished("done")

    def refuse(path, **kwargs):
        raise GitError("it is busy")

    monkeypatch.setattr(prune, "worktree_remove", refuse)
    code = cli.gwp(["--yes", "--json"])
    out = capsys.readouterr()
    assert code == 1, out.err
    assert json.loads(out.out)["removed"] == []
    assert f"worktree {wt} kept: it is busy" in out.err
    assert world.git("rev-parse", "--verify", "refs/heads/done")


def test_a_detached_worktree_goes_without_a_ref_deletion(world, capsys) -> None:
    from worktrees.git import options

    wt = world.parent / ".worktrees" / "loose" / world.repo.name
    world.git("worktree", "add", "--quiet", "--detach", str(wt), "main")
    code = cli.gwp(["--yes"])
    err = capsys.readouterr().err
    assert code == 0, err
    assert not wt.exists()
    assert not any("update-ref" in entry for entry in options.log)


def test_removal_never_forces(world, capsys) -> None:
    from worktrees.git import options

    world.finished("done")
    assert cli.gwp(["--yes"]) == 0
    removes = [" ".join(e) for e in options.log if "remove" in e]
    assert removes and not any("--force" in e for e in removes)
