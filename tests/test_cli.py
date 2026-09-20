"""Drive the entry points. Exec is the only way env, cwd, both streams and the
exit code stay per-test."""

from __future__ import annotations

import json
import os
import pty
import subprocess
import sys
from pathlib import Path

import pytest
from conftest import env_for

from worktrees import prune

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


def run_on_a_terminal(world, entry: str, *args: str, answer: str) -> tuple[int, str]:
    """The same, with a real tty on stdin, so the prompt is reachable."""
    parent, child = pty.openpty()
    proc = subprocess.Popen(
        [
            sys.executable,
            "-c",
            f"from worktrees.cli import {entry}; raise SystemExit({entry}())",
            *args,
        ],
        cwd=str(world.repo),
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
# gws reports and never removes
# --------------------------------------------------------------------------


def test_status_has_no_flag_that_removes(world) -> None:
    p = run(world, "gws", "--help")
    assert p.returncode == 0
    for flag in ("--yes", "-y"):
        assert flag not in p.stdout, f"gws offers {flag}"
    p = run(world, "gws", "--yes")
    assert p.returncode == 2
    assert "unrecognized arguments" in p.stderr


def test_status_leaves_a_removable_worktree_alone(world) -> None:
    wt = world.worktree("done")
    p = run(world, "gws", "--no-fetch")
    assert p.returncode == 0, p.stderr
    assert "remove" in p.stdout
    assert "1 removable, 0 kept, 0 unclear" in p.stdout
    assert "gwp removes the 1 marked removable" in p.stderr
    assert wt.exists()
    assert world.git("rev-parse", "--verify", "refs/heads/done")


def test_status_says_when_there_is_nothing(world) -> None:
    p = run(world, "gws", "--no-fetch")
    assert p.returncode == 0, p.stderr
    assert "no worktrees besides the main checkout" in p.stdout


def test_status_json_is_data_on_stdout(world) -> None:
    world.worktree("done")
    p = run(world, "gws", "--no-fetch", "--json")
    assert p.returncode == 0, p.stderr
    payload = json.loads(p.stdout)
    assert payload["head"] == "refs/heads/main"
    assert payload["verdicts"][0]["verdict"] == "remove"
    assert payload["stale"] == []


def test_json_carries_the_ignored_count_as_a_number(world) -> None:
    """A caller deciding whether to pass --delete-ignored wants the count, not
    a sentence it has to find the count inside."""
    wt = world.worktree("holds-secrets")
    world.repo.joinpath(".gitignore").write_text(".env\n")
    world.git("add", ".gitignore")
    world.git("commit", "--quiet", "-m", "ignore")
    world.git("merge", "--quiet", "--ff-only", "main", at=wt)
    wt.joinpath(".env").write_text("S=1\n")

    p = run(world, "gws", "--no-fetch", "--no-forge", "--json")
    assert p.returncode == 0, p.stderr
    row = json.loads(p.stdout)["verdicts"][0]
    assert row["ignored"] == 1
    assert row["verdict"] == "keep"
    # and the sentence still says it, for the person reading the table
    assert "1 ignored path(s)" in row["why"]


def test_json_carries_the_sha_the_verdict_was_formed_against(world) -> None:
    wt = world.worktree("done")
    head = world.git("rev-parse", "HEAD", at=wt)
    p = run(world, "gws", "--no-fetch", "--no-forge", "--json")
    assert p.returncode == 0, p.stderr
    row = json.loads(p.stdout)["verdicts"][0]
    assert row["sha"] == head
    assert len(row["sha"]) == 40


def test_status_names_a_stale_record_rather_than_clearing_it(world) -> None:
    """`git worktree prune` mutates, so a read-only command may not call it."""
    wt = world.worktree("gone")
    import shutil

    shutil.rmtree(wt)
    p = run(world, "gws", "--no-fetch")
    assert p.returncode == 0, p.stderr
    assert "1 stale record(s)" in p.stderr
    # Still listed by git, because nothing pruned it.
    assert "gone" in world.git("worktree", "list", "--porcelain")


# --------------------------------------------------------------------------
# gwp removes exactly what gws marks removable
# --------------------------------------------------------------------------


@pytest.mark.parametrize("extra", [(), ("--delete-ignored",)])
def test_prune_removes_exactly_what_status_marks(world, extra: tuple[str, ...]) -> None:
    """The invariant. Same flags in, same set out."""
    (world.repo / ".gitignore").write_text(".env\n")
    world.git("add", "--", ".gitignore")
    world.git("commit", "--quiet", "-m", "ignore")

    world.worktree("finished")
    holds = world.worktree("holds-env")
    (holds / ".env").write_text("SECRET=1\n")
    busy = world.worktree("busy")
    (busy / "scratch.txt").write_text("wip\n")
    mystery = world.worktree("mystery")
    world.commit("m.txt", "m\n", at=mystery)

    p = run(world, "gws", "--no-fetch", "--json", *extra)
    marked = sorted(
        v["branch"]
        for v in json.loads(p.stdout)["verdicts"]
        if v["verdict"] == "remove"
    )

    p = run(world, "gwp", "--no-fetch", "--yes", "--json", *extra)
    assert p.returncode == 0, p.stderr
    removed = sorted(json.loads(p.stdout)["removed"])

    assert removed == marked
    assert "finished" in removed
    assert ("holds-env" in removed) == bool(extra)
    for kept in ("busy", "mystery"):
        assert kept not in removed


def test_prune_asks_before_removing(world) -> None:
    wt = world.worktree("done")
    code, out = run_on_a_terminal(world, "gwp", "--no-fetch", answer="y\n")
    assert code == 0, out
    assert "remove 1 worktree(s)? [y/N]" in out
    assert "restore with: git branch done " in out
    assert not wt.exists()


def test_answering_no_removes_nothing(world) -> None:
    wt = world.worktree("done")
    code, out = run_on_a_terminal(world, "gwp", "--no-fetch", answer="n\n")
    assert code == 0, out
    assert "nothing removed" in out
    assert wt.exists()
    assert world.git("rev-parse", "--verify", "refs/heads/done")


def test_bare_enter_removes_nothing(world) -> None:
    """[y/N]: the capital is the default, and it has to be true."""
    wt = world.worktree("done")
    code, out = run_on_a_terminal(world, "gwp", "--no-fetch", answer="\n")
    assert code == 0, out
    assert "nothing removed" in out
    assert wt.exists()


def test_without_a_terminal_it_refuses_rather_than_blocking(world) -> None:
    """An agent or a pipe reaches this and would otherwise hang forever."""
    wt = world.worktree("done")
    p = run(world, "gwp", "--no-fetch")
    assert p.returncode == 3
    assert "not a terminal" in p.stderr
    assert "pass --yes" in p.stderr
    assert wt.exists()


def test_no_terminal_is_one_exit_code(world) -> None:
    """One condition, one number. 2 is already argparse's and _Stop's."""
    world.worktree("a")
    world.worktree("b")
    for entry, args in (
        ("gwp", ("--no-fetch",)),
        ("gwl", ()),
        ("gwr", ("--no-fetch",)),
    ):
        p = run(world, entry, *args)
        assert p.returncode == 3, f"{entry} exited {p.returncode}: {p.stderr}"
        assert "terminal" in p.stderr


@pytest.mark.parametrize(
    ("answer", "expected"),
    [("y", True), ("Y", True), ("yes", True), ("n", False), ("", False), ("q", False)],
)
def test_confirm_takes_yes_and_nothing_else(answer: str, expected: bool) -> None:
    assert prune.confirm(1, ask=lambda _: answer) is expected


def test_prune_deletes_the_branch_and_the_empty_parents(world) -> None:
    wt = world.worktree("done")
    p = run(world, "gwp", "--no-fetch", "--yes")
    assert p.returncode == 0, p.stderr
    assert not wt.exists()
    assert "done" not in world.git("branch", "--format=%(refname:short)").split()
    assert not (world.parent / ".worktrees" / "done").exists()


def test_a_squash_merged_branch_goes_with_its_checkout(world) -> None:
    """The proof `-d` cannot read is the proof this deletes on."""
    wt = world.worktree("squashed")
    world.commit("f.txt", "one\n", at=wt)
    (world.repo / "f.txt").write_text("one\n")
    world.git("add", "--", "f.txt")
    world.git("commit", "--quiet", "-m", "squash")

    p = run(world, "gwp", "--no-fetch", "--yes")
    assert p.returncode == 0, p.stderr
    assert not wt.exists()
    assert "squashed" not in world.git("branch", "--format=%(refname:short)").split()
    assert "kept" not in p.stderr
    assert "restore with: git branch squashed " in p.stderr


def test_prune_clears_a_stale_record(world) -> None:
    wt = world.worktree("gone")
    import shutil

    shutil.rmtree(wt)
    p = run(world, "gwp", "--no-fetch", "--yes")
    assert p.returncode == 0, p.stderr
    assert "gone" not in world.git("worktree", "list", "--porcelain")


def test_prune_says_so_when_there_is_nothing(world) -> None:
    p = run(world, "gwp", "--no-fetch", "--yes")
    assert p.returncode == 0, p.stderr
    assert "no worktrees besides the main checkout" in p.stdout


def test_prune_prints_the_reason_rather_than_naming_the_command(world) -> None:
    """The bug this replaced: `nothing to remove; gws says why`, and a
    second command to type before you learn why."""
    world.worktree("busy")
    (world.parent / ".worktrees" / "busy" / "repo" / "wip").write_text("x")
    p = run(world, "gwp", "--no-fetch", "--yes")
    assert p.returncode == 0, p.stderr
    assert "busy" in p.stdout
    assert "it has uncommitted changes" in p.stdout
    assert "nothing to remove; 0 removable, 1 kept, 0 unclear" in p.stdout
    assert "gws" not in p.stdout


def test_version_is_a_command_and_not_a_flag(world) -> None:
    for entry in ("gws", "gw", "main"):
        p = run(world, entry, "--version")
        assert p.returncode == 2
        assert "unrecognized arguments: --version" in p.stderr


def test_dry_run_is_refused_by_name(world) -> None:
    for entry in ("gws", "gwp"):
        p = run(world, entry, "--dry-run")
        assert p.returncode == 2
        assert "there is no --dry-run" in p.stderr


def test_explain_lists_every_command_and_runs_none(world) -> None:
    p = run(world, "gws", "--explain")
    assert p.returncode == 0, p.stderr
    assert "git worktree list --porcelain -z" in p.stdout
    assert "git update-ref -d $ref $sha" in p.stdout
    assert "git branch -D" not in p.stdout


def test_verbose_prints_the_argv(world) -> None:
    world.worktree("done")
    p = run(world, "gws", "--no-fetch", "--verbose")
    assert p.returncode == 0, p.stderr
    # The canary: a run that never reached git would print nothing here, and an
    # empty stream is indistinguishable from a refusal that worked.
    assert "+ git worktree list --porcelain -z" in p.stderr
    assert "--no-optional-locks status --porcelain" in p.stderr
    assert "--is-ancestor" in p.stderr


def test_status_never_issues_a_mutating_command(world) -> None:
    """--no-fetch, so the one mutation a report is allowed does not run."""
    world.worktree("done")
    p = run(world, "gws", "--no-fetch", "--verbose")
    issued = [ln for ln in p.stderr.splitlines() if ln.startswith("+ git")]
    assert issued
    for line in issued:
        forbidden = ("worktree prune", "worktree remove", "update-ref", "checkout")
        for bad in forbidden:
            assert bad not in line, line


def test_you_cannot_prune_the_worktree_you_stand_in(world) -> None:
    wt = world.worktree("here")
    p = run(world, "gws", "--no-fetch", at=wt)
    assert p.returncode == 0, p.stderr
    assert "you are standing in it" in p.stdout
    assert wt.exists()


# --------------------------------------------------------------------------
# the CLI name
# --------------------------------------------------------------------------


def test_the_cli_name_takes_a_subcommand(world) -> None:
    world.worktree("done")
    p = run(world, "main", "status", "--no-fetch", "--json")
    assert p.returncode == 0, p.stderr
    assert json.loads(p.stdout)["verdicts"][0]["branch"] == "done"


def test_the_cli_name_with_no_argument_prints_help(world) -> None:
    p = run(world, "main")
    assert p.returncode == 0
    for name in ("status", "prune", "new-branch"):
        assert name in p.stdout


def test_a_bare_flag_means_status(world) -> None:
    p = run(world, "main", "--explain")
    assert p.returncode == 0, p.stderr
    assert "git worktree list --porcelain -z" in p.stdout


def test_an_unknown_command_names_the_closest_one(world) -> None:
    """argparse answers this with a usage line and twenty-two choices, and
    the name worth reading arrives last."""
    p = run(world, "gw", "verison")
    assert p.returncode == 2
    assert p.stderr.splitlines() == [
        "gw: there is no command 'verison'",
        "the closest is: gw version",
        "gw help lists every command",
    ]


def test_an_unknown_command_near_nothing_still_points_at_help(world) -> None:
    p = run(world, "main", "xyzzy")
    assert p.returncode == 2
    assert p.stderr.splitlines() == [
        "worktrees: there is no command 'xyzzy'",
        "worktrees help lists every command",
    ]


# --------------------------------------------------------------------------
# shorthands, and the table they come from
# --------------------------------------------------------------------------


@pytest.mark.parametrize("spelling", ["status", "st", "s"])
def test_every_shorthand_reaches_the_same_command(world, spelling: str) -> None:
    world.worktree("done")
    p = run(world, "main", spelling, "--no-fetch", "--json")
    assert p.returncode == 0, p.stderr
    assert json.loads(p.stdout)["verdicts"][0]["branch"] == "done"


@pytest.mark.parametrize(
    ("spelling", "canonical"),
    [
        ("p", "prune"),
        ("a", "add"),
        ("l", "list"),
        ("ls", "list"),
        ("m", "move"),
        ("mv", "move"),
        ("rm", "remove"),
        ("nb", "new-branch"),
        ("new", "new-branch"),
        ("rot", "rotate"),
        ("h", "help"),
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
    declared = {c.binary for c in cli._COMMANDS.values() if c.binary}
    assert declared <= set(scripts)
    # The two that take a subcommand are the only ones with no row of their own.
    assert set(scripts) - declared == {"gw", "worktrees"}
    # and version is the only row that installs nothing.
    assert [n for n, c in cli._COMMANDS.items() if not c.binary] == ["version"]


# --------------------------------------------------------------------------
# gwh
# --------------------------------------------------------------------------


@pytest.mark.parametrize("entry", ["gwh", "main"])
def test_help_names_every_command_and_every_shorthand(world, entry: str) -> None:
    args = () if entry == "gwh" else ("help",)
    p = run(world, entry, *args)
    assert p.returncode == 0, p.stderr
    from worktrees import cli

    for name, command in cli._COMMANDS.items():
        assert command.binary in p.stdout, name
        assert f"gw {name}" in p.stdout, name
        for alias in command.aliases:
            assert alias in p.stdout, alias


def test_the_cli_with_no_argument_prints_that_same_help(world) -> None:
    bare = run(world, "main").stdout
    asked = run(world, "main", "help").stdout
    assert bare == asked
    assert "gws" in bare and "gw status (s, st)" in bare


@pytest.mark.parametrize("flag", ["--json", "-q", "-v", "--explain"])
def test_help_promises_no_flag_the_program_refuses(world, flag: str) -> None:
    """gwh declares no flags, so the footer has to carve it out by name. The
    footer derives that from the table rather than asserting it in prose."""
    p = run(world, "gwh", flag)
    assert p.returncode == 2
    assert "unrecognized arguments" in p.stderr

    listed = run(world, "gwh")
    assert "work on every command but gwh" in listed.stdout
    # and the flags it does claim are taken by a command that has a row
    assert run(world, "gws", "--no-fetch", flag).returncode == 0


def test_help_stays_inside_eighty_columns(world) -> None:
    """It is read at a prompt, next to the commands it describes."""
    p = run(world, "gwh")
    assert p.returncode == 0, p.stderr
    assert [ln for ln in p.stdout.splitlines() if len(ln) > 80] == []


# --------------------------------------------------------------------------
# colour
# --------------------------------------------------------------------------


def test_colour_reaches_a_terminal(world) -> None:
    world.worktree("done")
    code, out = run_with_a_terminal_on_stdout(world, "gws", "--no-fetch")
    assert code == 0, out
    assert "[32mremove" in out


def test_a_pipe_gets_no_colour(world) -> None:
    world.worktree("done")
    p = run(world, "gws", "--no-fetch")
    assert p.returncode == 0, p.stderr
    assert "[" not in p.stdout


def test_json_is_never_coloured_even_on_a_terminal(world) -> None:
    """stdout carries what jq parses and what `cd $(gwa x)` reads."""
    world.worktree("done")
    code, out = run_with_a_terminal_on_stdout(world, "gws", "--no-fetch", "--json")
    assert code == 0, out
    assert "[" not in out
    assert json.loads(out)["verdicts"][0]["branch"] == "done"


def test_no_color_turns_it_off_on_a_terminal(world) -> None:
    world.worktree("done")
    code, out = run_with_a_terminal_on_stdout(
        world, "gws", "--no-fetch", env_extra={"NO_COLOR": "1"}
    )
    assert code == 0, out
    assert "[" not in out


# --------------------------------------------------------------------------
# an unreachable remote is answered, not refused
# --------------------------------------------------------------------------


def _unreachable(world) -> None:
    world.git("remote", "add", "origin", str(world.root / "nowhere.git"))


def test_a_failed_fetch_still_makes_the_branch(world) -> None:
    """An offline machine gets a branch off whatever it last saw."""
    _unreachable(world)
    world.git("update-ref", "refs/remotes/origin/main", "main")
    p = run(world, "gwnb", "spike")
    assert p.returncode == 0, p.stderr
    assert "could not be fetched" in p.stderr
    assert "spike from origin/main" in p.stdout
    assert world.git("rev-parse", "--verify", "refs/heads/spike")


def test_a_failed_fetch_still_makes_the_worktree(world) -> None:
    _unreachable(world)
    world.git("update-ref", "refs/remotes/origin/main", "main")
    p = run(world, "gwa", "fix-parser")
    assert p.returncode == 0, p.stderr
    assert "could not be fetched" in p.stderr
    assert (world.parent / ".worktrees" / "fix-parser" / "repo").is_dir()


def test_a_failed_fetch_still_answers_status(world) -> None:
    _unreachable(world)
    world.worktree("done")
    p = run(world, "gws")
    assert p.returncode == 0, p.stderr
    assert "could not be fetched" in p.stderr
    assert "done" in p.stdout


def test_explain_names_every_refusal_the_guard_holds(world) -> None:
    """The footer is generated from the rules, so the seventh is in it."""
    p = run(world, "gws", "--explain")
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
    world.worktree("done")
    assert "--no-forge" in run(world, "gwr", "--help").stdout
    p = run(world, "gwr", "--no-fetch", "--no-forge", "done", "-y")
    assert p.returncode == 0, p.stderr


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

    p = run(world, "gwp", "--no-fetch", "--no-forge", "-y")
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

    p = run(world, "gwp", "--no-fetch", "--no-forge", "-y")
    assert p.returncode == 0, p.stderr
    assert "was made on this branch" not in p.stderr


def test_list_with_no_query_prompts_on_a_terminal(world) -> None:
    """One other worktree, and it still asks, because nothing said which.

    The prompt is the whole point: `gwl` with no argument means show me the
    options, and one option is still an option rather than a destination.
    """
    world.worktree("one")
    code, out = run_on_a_terminal(world, "gwl", answer="1\n")
    assert code == 0, out
    assert "which? [1-1, or blank to cancel]" in out
    assert "one" in out


def test_list_with_no_query_takes_blank_as_cancelled(world) -> None:
    world.worktree("one")
    code, out = run_on_a_terminal(world, "gwl", answer="\n")
    assert code == 0, out
    assert "nothing picked" in out
