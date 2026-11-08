"""Navigation entry points and worktree operations."""

from __future__ import annotations

import io
import json
import os
import re
import subprocess
import sys
import termios
import time
import tty
from pathlib import Path

import pytest
from conftest import argv, env_for
from test_cli import run_on_a_terminal, run_on_a_terminal_at

from worktrees import layout, pick
from worktrees import worktree as W
from worktrees.git import GitError
from worktrees.new_branch import Refusal
from worktrees.repo import Worktree

SRC = str(Path(__file__).resolve().parents[1] / "src")


def run(world, entry: str, *args: str, at: Path | None = None):
    return subprocess.run(
        argv(entry, *args),
        cwd=str(at or world.repo),
        capture_output=True,
        text=True,
        env=env_for(world),
    )


# --------------------------------------------------------------------------
# the derived layout
# --------------------------------------------------------------------------


def test_the_destination_is_derived_not_configured(world) -> None:
    """Two tools cannot disagree about a location neither can be told."""
    main = str(world.repo)
    assert (
        layout.destination("fix-parser", main)
        == world.parent / ".worktrees" / "fix-parser" / "repo"
    )


def test_a_branch_with_slashes_becomes_directories(world) -> None:
    """The repository name goes last, so feat/oauth cannot collide with feat."""
    main = str(world.repo)
    assert layout.destination("feat/oauth", main) == (
        world.parent / ".worktrees" / "feat" / "oauth" / "repo"
    )


# --------------------------------------------------------------------------
# gwa
# --------------------------------------------------------------------------


def test_add_prints_only_the_destination(world) -> None:
    p = run(world, "gw add", "fix-parser")
    assert p.returncode == 0, p.stderr
    dest = world.parent / ".worktrees" / "fix-parser" / "repo"
    assert p.stdout.strip() == str(dest)
    assert dest.is_dir()
    assert world.git("rev-parse", "--abbrev-ref", "HEAD", at=dest) == "fix-parser"


def test_add_uses_a_branch_that_already_exists(world) -> None:
    """-b would refuse it, so the checkout is made without one."""
    world.git("branch", "already", "main")
    landed = W.add("already")
    assert landed.created is False
    assert Path(landed.path).is_dir()


def test_add_refuses_a_branch_already_checked_out(world) -> None:
    world.worktree("taken")
    with pytest.raises(Refusal, match="already checked out"):
        W.add("taken")


def test_add_refuses_a_directory_another_repository_owns(world) -> None:
    """`git worktree list` returns empty for somebody else's checkout, which
    reads exactly like "nothing is there"."""
    other = world.root / "other"
    other.mkdir()
    subprocess.run(["git", "init", "--quiet", "-b", "main", str(other)], check=True)
    dest = layout.destination("borrowed", str(world.repo))
    dest.parent.mkdir(parents=True)
    subprocess.run(
        ["git", "-C", str(other), "worktree", "add", "--quiet", "-b", "x", str(dest)],
        check=True,
        capture_output=True,
    )
    with pytest.raises(Refusal, match="belongs to"):
        W.add("borrowed")


@pytest.mark.parametrize("name", ["has space", "-leading", "a..b"])
def test_add_refuses_a_name_git_rejects(world, name: str) -> None:
    with pytest.raises(Refusal, match="not a valid branch name"):
        W.add(name)


def test_add_with_no_name_names_what_was_typed(world) -> None:
    p = run(world, "gw add")
    assert p.returncode == 2
    assert p.stderr.startswith("gw add needs NAME\n\nusage: gw add ")
    assert p.stdout == ""


@pytest.mark.parametrize("binary", ["gw add", "gw remove"])
def test_a_command_that_lands_prints_its_help_on_stderr(world, binary: str) -> None:
    """Its shell function captures stdout to find where to go."""
    p = run(world, binary, "--help")
    assert p.returncode == 0
    assert p.stdout == ""
    assert p.stderr.startswith(f"usage: {binary} ")


def test_a_command_that_stays_prints_its_help_on_stdout(world) -> None:
    p = run(world, "gw", "--help")
    assert "COMMAND" in p.stdout and "gw list" in p.stdout
    assert p.stderr == ""


# --------------------------------------------------------------------------
# gw list
# --------------------------------------------------------------------------


def test_list_prints_the_selection_alone(world) -> None:
    world.worktree("fix-parser")
    code, out = run_on_a_terminal(world, "gw", "list", "parse", answer="")
    assert code == 0, out
    assert out.rstrip().endswith(
        str(world.parent / ".worktrees" / "fix-parser" / "repo")
    )


def test_list_offers_the_main_checkout(world) -> None:
    """It is where a finished branch leaves you, so it is a destination."""
    world.worktree("one")
    p = run(world, "gw", "list", "--json")
    payload = json.loads(p.stdout)["worktrees"]
    assert [w["branch"] for w in payload] == ["main", "one"]
    assert [w["main"] for w in payload] == [True, False]


def test_list_says_so_rather_than_landing_you_where_you_are(world) -> None:
    """The bug this replaced: one candidate, standing in it, no output at all."""
    wt = world.worktree("one")
    code, out = run_on_a_terminal_at(world, wt, "gw", "list", "one", answer="")
    assert code == 0, out
    assert "already in one" in out


def test_nonterminal_list_includes_main_and_one_other_worktree(world) -> None:
    world.worktree("one")
    p = run(world, "gw", "list")
    assert p.returncode == 0, p.stderr
    assert "WORKTREE" in p.stdout
    assert str(world.repo) in p.stdout
    assert "/one/repo" in p.stdout


def test_list_offers_main_to_somebody_standing_in_it(world) -> None:
    """From a subdirectory of the main checkout, `gw list main` is the way back."""
    world.worktree("one")
    (world.repo / "sub").mkdir()
    code, out = run_on_a_terminal_at(
        world, world.repo / "sub", "gw", "list", "main", answer=""
    )
    assert code == 0, out
    assert out.rstrip().endswith(str(world.repo))


def test_list_with_a_query_still_takes_the_one_match_outright(world) -> None:
    world.worktree("one")
    code, out = run_on_a_terminal(world, "gw", "list", "one", answer="")
    assert code == 0, out
    assert out.strip().endswith("/one/repo")


def test_list_marks_the_one_you_stand_in(world) -> None:
    wt = world.worktree("one")
    p = run(world, "gw", "list", "--table", at=wt)
    assert p.returncode == 0, p.stderr
    head, *body = p.stdout.splitlines()
    assert head.split() == ["WORKTREE", "BRANCH", "PATH"]
    marks = {ln[3:].split()[1]: ln[0] for ln in body}
    assert marks == {"main": " ", "one": "*"}


def test_the_main_checkout_says_so_without_saying_it_twice(world) -> None:
    world.git("branch", "--move", "main", "trunk")
    world.worktree("one", base="trunk")
    p = run(world, "gw", "list", "--table")
    assert "trunk (main)" in p.stdout


def test_nonterminal_list_includes_the_only_main_checkout(world) -> None:
    p = run(world, "gw", "list")
    assert p.returncode == 0, p.stderr
    assert "WORKTREE" in p.stdout and str(world.repo) in p.stdout


def test_nonterminal_list_prints_every_worktree_without_prompting(world) -> None:
    """An agent or a pipe reaching a prompt would hang."""
    world.worktree("one")
    world.worktree("two")
    p = run(world, "gw", "list")
    assert p.returncode == 0, p.stderr
    assert "WORKTREE" in p.stdout
    assert "/one/repo" in p.stdout and "/two/repo" in p.stdout


def test_one_match_takes_it_with_no_prompt(world) -> None:
    world.worktree("one")
    world.worktree("two")
    code, out = run_on_a_terminal(world, "gw", "list", "two", answer="")
    assert code == 0, out
    assert out.strip().endswith("/two/repo")


def test_the_picker_prefers_a_substring_to_a_subsequence() -> None:
    """Typing more of a name never moves it down the list."""
    rows = [
        Worktree("/w/add-tests", "a", "add-tests", frozenset()),
        Worktree("/w/toast", "a", "toast", frozenset()),
    ]
    assert [w.branch for w in pick.matches("tst", rows)] == ["add-tests", "toast"]
    assert [w.branch for w in pick.matches("toast", rows)] == ["toast"]


def test_the_pick_moves_with_the_arrows_and_cancels_on_esc() -> None:
    rows = [
        Worktree("/w/a", "a", "a", frozenset()),
        Worktree("/w/b", "a", "b", frozenset()),
    ]
    assert pick.choose(rows, keys=iter([pick.CANCEL])) is None
    assert pick.choose(rows, keys=iter([pick.PICK])) == rows[0]
    assert pick.choose(rows, keys=iter([pick.DOWN, pick.PICK])) == rows[1]
    assert pick.choose(rows, keys=iter([pick.UP, pick.PICK])) == rows[1]
    assert pick.choose(rows, keys=iter([pick.PICK]), at=rows[1]) == rows[1]


def test_typing_filters_and_esc_clears_before_it_cancels() -> None:
    """Digits are typed like any letter: names carry them, and a key that
    picked a row would jump while somebody spells `release-0.6.2`."""
    rows = [
        Worktree("/w/main", "a", "main", frozenset(), main=True),
        Worktree("/w/.worktrees/rel-062/repo", "a", "release-0.6.2", frozenset()),
        Worktree("/w/.worktrees/fix-parser/repo", "a", "fix-parser", frozenset()),
    ]
    assert pick.choose(rows, keys=iter([*"06", pick.PICK])) == rows[1]
    assert pick.choose(rows, keys=iter([*"fxp", pick.PICK])) == rows[2]
    # The worktree's own name is searched as well as its branch.
    assert pick.choose(rows, keys=iter([*"rl62", pick.PICK])) == rows[1]
    assert pick.choose(rows, keys=iter([*"fix", pick.ESC, pick.PICK])) == rows[0]
    assert pick.choose(rows, keys=iter([*"fix", pick.ESC, pick.ESC])) is None
    assert (
        pick.choose(rows, keys=iter(["x", pick.ERASE, pick.DOWN, pick.PICK]))
        == (rows[1])
    )
    # Nothing matches: enter and the arrows have nothing to act on.
    keys = iter([*"zz", pick.PICK, pick.DOWN, pick.ERASE, pick.ERASE, pick.PICK])
    assert pick.choose(rows, keys=keys) == rows[0]
    assert pick.choose(rows, keys=iter([*"zz", pick.CANCEL])) is None


def test_the_list_opens_on_its_query_and_esc_widens_it() -> None:
    rows = [
        Worktree("/w/main", "a", "main", frozenset(), main=True),
        Worktree("/w/fix-one", "a", "fix-one", frozenset()),
        Worktree("/w/fix-two", "a", "fix-two", frozenset()),
    ]
    assert pick.choose(rows, keys=iter([pick.PICK]), query="fix") == rows[1]
    assert pick.choose(rows, keys=iter([pick.ESC, pick.PICK]), query="fix") == rows[0]
    assert pick.choose(rows, keys=iter([]), query="fix-two") == rows[2]


def test_shown_rows_are_filtered_and_never_picked() -> None:
    rows = [
        Worktree("/w/one", "a", "one", frozenset()),
        Worktree("/w/two", "a", "two", frozenset()),
    ]
    here = Worktree("/w/here", "a", "here", frozenset())
    keys = iter([*"here", pick.PICK, pick.ESC, pick.PICK])
    assert pick.choose(rows, keys=keys, shown=[("*", here)]) == rows[0]


def test_keys_names_escape_sequences_and_types_the_rest() -> None:
    """Two escapes written together are two, and the `~` that ends the
    delete key is not typed into the filter."""
    parent, child = os.openpty()
    tty.setcbreak(child, termios.TCSANOW)
    try:
        os.write(parent, b"a1\x1b[B\x1bOA\x1b[3~\x7f\x1b\x1b\r\x04")
        keys = pick._keys(child)
        got = [next(keys) for _ in range(9)]
    finally:
        os.close(parent)
        os.close(child)
    assert got == [
        "a",
        "1",
        pick.DOWN,
        pick.UP,
        pick.ERASE,
        pick.ESC,
        pick.ESC,
        pick.PICK,
        pick.CANCEL,
    ]


def test_a_worktree_is_named_by_its_directory_under_the_root() -> None:
    assert layout.name_of("/g/.worktrees/fix-parser/repo") == "fix-parser"
    assert layout.name_of("/g/.worktrees/feat/oauth/repo") == "feat/oauth"
    assert layout.name_of("/g/repo") == "repo"


def test_an_exact_name_or_path_is_that_worktree_alone() -> None:
    """`gwr one` with `one-more` beside it is not a question."""
    rows = [
        Worktree("/w/one", "a", "one", frozenset()),
        Worktree("/w/one-more", "a", "one-more", frozenset()),
    ]
    assert pick.matches("one", rows) == [rows[0]]
    assert pick.matches("/w/one", rows) == [rows[0]]
    assert pick.matches("on", rows) == rows


class _Tty(io.StringIO):
    def isatty(self) -> bool:
        return True


@pytest.mark.parametrize("term,redraws", [("xterm", True), ("dumb", False)])
def test_the_pick_redraws_in_place_only_where_the_cursor_moves(
    monkeypatch, term, redraws
) -> None:
    rows = [
        Worktree("/w/a", "a", "a", frozenset()),
        Worktree("/w/b", "a", "b", frozenset()),
    ]
    err = _Tty()
    monkeypatch.setattr(sys, "stderr", err)
    monkeypatch.setenv("TERM", term)
    assert pick.choose(rows, keys=iter([pick.DOWN, pick.PICK])) == rows[1]
    # The header and both rows.
    assert ("\033[3A" in err.getvalue()) is redraws
    assert ("\033[" in err.getvalue()) is redraws


def test_a_redrawn_list_fits_the_screen_and_follows_the_highlight(
    monkeypatch,
) -> None:
    """A row that wraps, or a frame taller than the screen, takes more lines
    than the climb counts, and every redraw after it lands crooked."""
    rows = [Worktree(f"/w/{n}", "a", n, frozenset()) for n in ("a", "b", "c", "d")]
    err = _Tty()
    err.fileno = lambda: 2  # type: ignore[method-assign]
    monkeypatch.setattr(sys, "stderr", err)
    monkeypatch.setenv("TERM", "xterm")
    monkeypatch.setattr(os, "get_terminal_size", lambda fd: os.terminal_size((12, 4)))
    assert pick.choose(rows, keys=iter([pick.UP, pick.PICK])) == rows[3]
    last = re.sub(r"\033\[[0-9;]*m", "", err.getvalue().rsplit("\033[J", 1)[1])
    frame = last.split("\n")[:-2]
    assert len(frame) == 3, last
    assert all(len(text) <= 11 for text in frame), frame
    assert [text[3:].split()[0] for text in frame[1:]] == ["c", "d"], frame
    assert frame[2].startswith(">"), frame


# --------------------------------------------------------------------------
# gwr
# --------------------------------------------------------------------------


def test_remove_prints_nothing_when_you_were_not_standing_in_it(world) -> None:
    """Empty stdout means stay put."""
    wt = world.merged_worktree("done")
    p = run(world, "gw remove", "done", "--yes")
    assert p.returncode == 0, p.stderr
    assert p.stdout.strip() == ""
    assert not wt.exists()
    assert "done" not in world.git("branch", "--format=%(refname:short)").split()


def test_remove_prints_the_main_checkout_when_you_were(world) -> None:
    wt = world.merged_worktree("done")
    from test_cli import run_on_a_terminal_at

    code, output = run_on_a_terminal_at(
        world, wt, "gw remove", "done", "--yes", answer="y\n"
    )
    assert code == 0, output
    assert output.rstrip().endswith(str(world.repo))
    assert not wt.exists()


def test_remove_refuses_an_unfinished_branch(world) -> None:
    wt = world.worktree("busy")
    world.commit("b.txt", "b\n", at=wt)
    p = run(world, "gw remove", "busy", "--yes")
    assert p.returncode == 1
    assert "kept" in p.stderr
    assert "--keep-branch" in p.stderr
    assert wt.exists()


def test_a_finished_branch_held_by_ignored_files_is_not_called_unfinished(
    world,
) -> None:
    """The branch landed. The directory is what --delete ignored-files answers for."""
    (world.repo / ".gitignore").write_text(".env\n")
    world.git("add", "--", ".gitignore")
    world.git("commit", "--quiet", "-m", "ignore")
    wt = world.merged_worktree("holds")
    (wt / ".env").write_text("SECRET=1\n")

    p = run(world, "gw remove", "holds", "--yes")
    assert p.returncode == 1
    assert "ignored path(s)" in p.stderr
    assert "is not finished" not in p.stderr
    assert "--delete ignored-files" in p.stderr
    assert wt.exists()

    p = run(world, "gw remove", "holds", "--yes", "--delete", "ignored-files")
    assert p.returncode == 0, p.stderr
    assert "deleting ignored, unrecoverable: .env" in p.stderr
    assert not wt.exists()


def test_keep_branch_names_the_ignored_files_it_is_about_to_delete(world) -> None:
    """`git worktree remove` takes them whatever flag got it here."""
    (world.repo / ".gitignore").write_text(".env\n")
    world.git("add", "--", ".gitignore")
    world.git("commit", "--quiet", "-m", "ignore")
    wt = world.worktree("busy")
    (wt / ".env").write_text("SECRET=1\n")
    world.commit("b.txt", "b\n", at=wt)

    p = run(
        world,
        "gw remove",
        "busy",
        "--yes",
        "--keep-branch",
        "--delete",
        "ignored-files",
    )
    assert p.returncode == 0, p.stderr
    assert "deleting ignored, unrecoverable: .env" in p.stderr
    assert not wt.exists()


def test_keep_branch_removes_the_checkout_and_keeps_the_branch(world) -> None:
    """The worktree was in the way. The work was not."""
    wt = world.worktree("busy")
    world.commit("b.txt", "b\n", at=wt)
    p = run(world, "gw remove", "busy", "--yes", "--keep-branch")
    assert p.returncode == 0, p.stderr
    assert not wt.exists()
    assert world.git("rev-parse", "--verify", "refs/heads/busy")


def test_remove_prints_the_restore_line_once(world) -> None:
    world.merged_worktree("done")
    p = run(world, "gw remove", "done", "--yes")
    assert p.stderr.count("restore with: git branch done ") == 1


def test_a_loose_match_ignores_the_path(world) -> None:
    """Every path holds `.worktrees`, which supplies a t, a w and an o to any
    query that wants them, so `two` matched `one` through its own directory.
    """
    rows = [
        Worktree("/home/x/.worktrees/one/repo", "a", "one", frozenset()),
        Worktree("/home/x/.worktrees/two/repo", "a", "two", frozenset()),
    ]
    assert [w.branch for w in pick.matches("two", rows)] == ["two"]
    # A literal substring of a path still finds it.
    assert [w.branch for w in pick.matches("/one/", rows)] == ["one"]


def test_git_refuses_a_branch_under_an_existing_one(world) -> None:
    """The raw behaviour: refs are files, so `feat` blocks `feat/oauth`."""
    world.git("branch", "feat", "main")
    dest = layout.destination("feat/oauth", str(world.repo))
    dest.parent.mkdir(parents=True)
    p = subprocess.run(
        [
            "git",
            "-C",
            str(world.repo),
            "worktree",
            "add",
            "-b",
            "feat/oauth",
            str(dest),
        ],
        capture_output=True,
        text=True,
    )
    assert p.returncode != 0
    assert "cannot lock ref" in p.stderr
    assert dest.parent.is_dir(), "git leaves the directory it was handed"


def test_add_names_the_directory_a_refusal_left_behind(world) -> None:
    """The directories go in before git is asked, so a refusal strands them."""
    world.worktree("feat")
    said: list[str] = []
    with pytest.raises(GitError):
        W.add("feat/oauth", warn=said.append)

    left = layout.worktrees_root(str(world.repo)) / "feat" / "oauth"
    assert left.is_dir()
    assert any(str(left) in line and "empty" in line for line in said), said


def test_add_names_the_root_when_it_made_the_whole_tree(world) -> None:
    """Nothing was there, so everything it made is worth naming."""
    world.git("branch", "feat", "main")
    said: list[str] = []
    with pytest.raises(GitError):
        W.add("feat/oauth", warn=said.append)

    root = layout.worktrees_root(str(world.repo))
    assert any(str(root) in line and "empty" in line for line in said), said


def test_add_says_nothing_when_the_parents_were_already_there(world) -> None:
    """Only what this call created is worth reporting."""
    world.git("branch", "feat", "main")
    (layout.worktrees_root(str(world.repo)) / "feat" / "oauth").mkdir(parents=True)
    said: list[str] = []
    with pytest.raises(GitError):
        W.add("feat/oauth", warn=said.append)
    assert not [line for line in said if "empty" in line], said


# --------------------------------------------------------------------------
# the worktrees root is shared between repositories side by side
# --------------------------------------------------------------------------


def test_a_siblings_worktree_is_counted_and_ours_is_not(world) -> None:
    ours = world.worktree("mine")
    world.sibling("other", "theirs")
    assert layout.neighbours(str(world.repo), [str(world.repo), str(ours)]) == 1


def test_an_empty_leftover_directory_is_not_a_neighbour(world) -> None:
    """A directory holding no `.git` is nobody's worktree."""
    ours = world.worktree("mine")
    (world.parent / ".worktrees" / "leftover").mkdir()
    assert layout.neighbours(str(world.repo), [str(world.repo), str(ours)]) == 0


def test_a_neighbour_is_found_under_a_branch_name_with_slashes(world) -> None:
    """The walk stops descending at the first `.git`, and a slash nests."""
    ours = world.worktree("mine")
    world.sibling("other", "feat/oauth")
    assert layout.neighbours(str(world.repo), [str(world.repo), str(ours)]) == 1


def test_counting_the_neighbours_asks_git_nothing(world) -> None:
    """`gw` counts them on every run, so it walks rather than spawning."""
    from worktrees.git import options

    ours = world.worktree("mine")
    world.sibling("other", "theirs")
    before = len(options.log)
    assert layout.neighbours(str(world.repo), [str(world.repo), str(ours)]) == 1
    assert len(options.log) == before


def test_no_worktrees_root_is_no_neighbours(world) -> None:
    assert layout.neighbours(str(world.repo), [str(world.repo)]) == 0


def test_the_spinner_says_what_is_being_checked_and_clears_its_line(
    monkeypatch,
) -> None:
    from worktrees import progress

    err = _Tty()
    monkeypatch.setattr(sys, "stderr", err)
    monkeypatch.setenv("TERM", "xterm")
    with progress.running():
        progress.say("one: reading its status")
        time.sleep(0.35)
    out = err.getvalue()
    assert "one: reading its status" in out
    assert out.endswith("\r\033[2K")


def test_the_spinner_stays_off_where_it_cannot_clear_a_line(monkeypatch) -> None:
    from worktrees import progress

    err = _Tty()
    monkeypatch.setattr(sys, "stderr", err)
    monkeypatch.setenv("TERM", "dumb")
    with progress.running():
        progress.say("x")
        time.sleep(0.25)
    assert err.getvalue() == ""
