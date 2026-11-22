"""Columns that line up, and colour only where something renders it."""

from __future__ import annotations

import io

import pytest

from worktrees import render
from worktrees.render import Cell


class _Tty(io.StringIO):
    def isatty(self) -> bool:
        return True


def test_a_coloured_cell_does_not_widen_its_column() -> None:
    """Padding runs on the text. The other order counts an escape sequence as
    width and every column under it sits crooked by the length of the code."""
    render.out.on = True
    try:
        rows: list[render.Row] = [
            (Cell("a", render.RED), "wide-value"),
            (Cell("bbbb", render.GREEN), "x"),
        ]
        lines = render.table(rows).splitlines()
    finally:
        render.out.on = False
    assert lines[0].index("wide-value") == lines[1].index("x")
    assert "\033[31m" in lines[0] and "\033[32m" in lines[1]


def test_an_uncoloured_table_is_what_it_always_was() -> None:
    render.out.on = False
    assert render.table([("a", "b"), ("cc", "d")]) == "a   b\ncc  d"


def test_an_empty_last_cell_leaves_no_trailing_space() -> None:
    render.out.on = False
    assert render.table([("a", ""), ("bb", "c")]) == "a\nbb  c"


def test_a_cell_with_newlines_makes_its_row_that_many_lines_tall() -> None:
    """The column is as wide as its longest line, the cells beside it blank
    below their first, and each line painted on its own."""
    render.out.on = False
    rows: list[render.Row] = [
        ("NAME", "WHY", "PATH"),
        ("a", "first reason\nsecond", "/p/a"),
        ("bb", "one", "/p/bb"),
    ]
    assert render.table(rows) == (
        "NAME  WHY           PATH\n"
        "a     first reason  /p/a\n"
        "      second\n"
        "bb    one           /p/bb"
    )
    render.out.on = True
    try:
        lines = render.table([(Cell("x\ny", render.RED), "z")]).splitlines()
    finally:
        render.out.on = False
    assert lines == ["\033[31mx\033[0m  z", "\033[31my\033[0m"]


def test_paint_is_a_no_op_while_colour_is_off() -> None:
    assert render.Ink(False)("x", render.RED) == "x"
    assert render.Ink(True)("x", render.RED) == "\033[31mx\033[0m"
    assert render.Ink(True)("", render.RED) == ""


@pytest.mark.parametrize(
    ("env", "expected"),
    [
        ({}, True),
        ({"NO_COLOR": "1"}, False),
        ({"NO_COLOR": ""}, True),
        ({"TERM": "dumb"}, False),
        ({"TERM": "xterm-256color"}, True),
    ],
)
def test_the_environment_can_refuse_colour(
    monkeypatch: pytest.MonkeyPatch, env: dict[str, str], expected: bool
) -> None:
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.delenv("TERM", raising=False)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    assert render.supported(_Tty()) is expected


def test_a_stream_that_is_not_a_terminal_never_gets_colour(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """stdout carries the path `cd $(gwa x)` reads and the JSON jq parses."""
    monkeypatch.delenv("NO_COLOR", raising=False)
    assert render.supported(io.StringIO()) is False


# --------------------------------------------------------------------------
# messages on stderr
# --------------------------------------------------------------------------


@pytest.fixture
def stderr_colour():
    render.err.on = True
    yield
    render.err.on = False


def test_a_message_is_its_own_words_when_stderr_is_not_a_terminal() -> None:
    text = "x is at origin/main (1e177cf)\nnothing to do (--dry-run)"
    assert render.fail(text) == text
    assert render.warn(text) == text
    assert render.note(text) == text


def test_a_failure_leads_with_a_symbol_and_reddens_its_detail(stderr_colour) -> None:
    first, second = render.fail("x is at origin/main (1e177cf)\nwhy").split("\n")
    assert first.startswith(
        f"{render.BOLD}{render.RED}✗{render.RESET} {render.RED}x is at"
    )
    assert f"{render.YELLOW}1e177cf{render.RESET}" in first
    assert second == f"  {render.RED}why{render.RESET}"


def test_a_warning_leads_with_a_warning_sign(stderr_colour) -> None:
    assert render.warn("stale").startswith(
        f"{render.BOLD}{render.YELLOW}⚠{render.RESET} "
    )


def test_a_warning_dims_its_detail(stderr_colour) -> None:
    assert (
        render.warn("stale\nwhy").split("\n")[1] == f"  {render.DIM}why{render.RESET}"
    )


def test_detail_after_a_branch_name_stays_dim(stderr_colour) -> None:
    line = render.warn(f"held back:\n{render.branch('wip')}: why").split("\n")[1]
    assert line.endswith(f"{render.RESET}{render.DIM}: why{render.RESET}")


@pytest.mark.parametrize(
    ("text", "painted"),
    [
        ("pass --keep-branch", "--keep-branch"),
        ("pass -q", "-q"),
        ("'gwbc --x' names one", "'gwbc --x'"),
    ],
)
def test_flags_and_quoted_commands_are_bold(stderr_colour, text, painted) -> None:
    assert f"{render.BOLD}{painted}{render.RESET}" in render.warn(text)


@pytest.mark.parametrize("word", ["fix-parser-2026-09-10_001", "deadbeef", "x-y"])
def test_names_and_hyphenated_words_are_left_alone(stderr_colour, word) -> None:
    assert render.note(word).endswith(f" {word}")


def test_a_trace_shows_what_the_command_printed_then_a_blank_line() -> None:
    lines = render.trace(["git", "remote"], 0, "origin\n", "", failed=False)
    assert lines.split("\n") == ["+ git remote", "  origin", ""]


def test_a_trace_of_a_silent_command_shows_its_exit_code() -> None:
    """For show-ref --quiet the code is the whole answer."""
    lines = render.trace(["git", "show-ref"], 1, "", "", failed=False)
    assert lines.split("\n") == ["+ git show-ref", "  exit 1", ""]


def test_a_trace_of_a_failure_shows_its_error_and_code() -> None:
    lines = render.trace(["git", "switch"], 1, "", "error: no\n", failed=True)
    assert lines.split("\n") == ["+ git switch", "  error: no", "  exit 1", ""]


def test_a_long_trace_is_cut_and_says_how_much() -> None:
    out = "\0".join(f"line {n}" for n in range(25))
    lines = render.trace(["git", "x", "-z"], 0, out, "", failed=False).split("\n")
    assert lines[1] == "  line 0"
    assert lines[-2] == "  ... 5 more"


def test_branches_and_refs_keep_their_own_colour(stderr_colour) -> None:
    """Painted where the message is built; the sha and flag passes skip them."""
    name = "fix-abc1234"
    line = render.warn(
        f"{render.branch(name)} is already at {render.ref('origin/main')}"
    )
    assert line.endswith(
        f"{render.BOLD}{render.MAGENTA}{name}{render.RESET} is already at "
        f"{render.BLUE}origin/main{render.RESET}"
    )


def test_a_command_in_a_message_is_one_colour(stderr_colour) -> None:
    """Not cyan flags beside plain names."""
    cmd = "git switch --create x-2026-11-02_001 --no-track refs/remotes/origin/main"
    line = render.fail(f"{render.command(cmd)} exited 1:\nerror: no")
    first = line.split("\n")[0]
    assert f"{render.BOLD}{cmd}{render.RESET}{render.RED} exited 1:" in first


def test_a_failure_stays_red_after_a_painted_name(stderr_colour) -> None:
    """Each reset a name ends with puts the line's red back."""
    line = render.fail(f"{render.branch('x')} is gone")
    assert line.endswith(
        f"{render.BOLD}{render.MAGENTA}x{render.RESET}{render.RED} is gone"
        f"{render.RESET}"
    )


def test_a_restore_command_paints_its_branch_and_sha(stderr_colour) -> None:
    """The way back reads like the table above it, and pastes as it did."""
    import re

    from worktrees import branches

    v = branches.Verdict(branch="a b", sha="0" * 40, verdict="remove", evidence="")
    line = render.restore(v.branch, v.sha)
    assert render.branch("'a b'") in line
    assert f"{render.YELLOW}{'0' * 40}{render.RESET}" in line
    assert re.sub(r"\033\[[\d;]*m", "", line) == v.undo


@pytest.mark.parametrize(
    "path", ["/tmp/a-b/d72cd87d-b528/repo", "~/git/x", "./rel/-x", "../up"]
)
def test_a_path_in_a_message_is_dim_and_whole(stderr_colour, path) -> None:
    """Not a flag where a directory starts with a dash, nor a sha in a uuid."""
    line = render.fail(f"x is already checked out at {path}")
    assert f"{render.DIM}{path}{render.RESET}" in line
    assert render.YELLOW not in line


def test_a_ref_is_not_a_path(stderr_colour) -> None:
    assert render.DIM not in render.warn("behind origin/main by 2")


def test_a_prompt_is_a_bold_question_and_dim_answers(stderr_colour) -> None:
    assert render.ask("remove 1?", "[y/N]") == (
        f"{render.BOLD}remove 1?{render.RESET} {render.DIM}[y/N]{render.RESET} "
    )


HEAD = ("BRANCH", "VERDICT")
LONG = "pull request #182 was closed without merging this tip of the branch"


def test_records_wrap_prose_to_the_width_and_never_break_a_command() -> None:
    """A reason wraps to the terminal; a command is a Cell and stays whole,
    widening the box when it has to, so it pastes back."""
    from worktrees.render import Cell, records

    command = "    git show 54a199e1f797"
    drawn = records(HEAD, [(("topic", "keep"), [LONG, Cell(command)])], width=40)
    lines = drawn.splitlines()
    assert {len(line) for line in lines} == {40}
    assert lines[5:9] == [
        "| pull request #182 was closed without |",
        "|   merging this tip of the branch     |",
        "|     git show 54a199e1f797            |",
        "+--------------------------------------+",
    ]

    tight = records(HEAD, [(("topic", "keep"), [LONG, Cell(command)])], width=10)
    assert command in tight
    assert len({len(line) for line in tight.splitlines()}) == 1


def test_records_off_a_terminal_wrap_nothing() -> None:
    from worktrees.render import records

    drawn = records(HEAD, [(("topic", "keep"), [LONG])])
    assert f"| {LONG} |" in drawn.splitlines()


def test_records_join_columns_only_where_a_line_has_them() -> None:
    """Box characters when asked, the last column stretched to the box's
    edge, and a rule's joints matching the lines above and below it."""
    from worktrees.render import records

    drawn = records(
        HEAD, [(("one", "keep"), ["a longer reason than both"]), (("two", "keep"), [])]
    )
    assert drawn == (
        "+--------+------------------+\n"
        "| BRANCH | VERDICT          |\n"
        "+--------+------------------+\n"
        "| one    | keep             |\n"
        "+--------+------------------+\n"
        "| a longer reason than both |\n"
        "+--------+------------------+\n"
        "| two    | keep             |\n"
        "+--------+------------------+"
    )
    boxed = records(HEAD, [(("one", "keep"), ["why"])], lines=True).splitlines()
    assert boxed[0] == "┌────────┬─────────┐"
    assert boxed[2] == "├────────┼─────────┤"
    assert boxed[4] == "├────────┴─────────┤"
    assert boxed[-1] == "└──────────────────┘"


def test_a_reflog_fact_puts_each_command_on_a_line_of_its_own() -> None:
    from worktrees import prune

    fact = prune.reflog_fact([("a" * 40, "one"), ("b" * 40, "two")])
    shown = prune.details("merged", [fact], ("reflog",))
    assert [line if isinstance(line, str) else line.text for line in shown] == [
        "merged",
        "its reflog alone holds 2 commits the head branch lacks; see each with:",
        "    git show aaaaaaaaaaaa",
        "    git show bbbbbbbbbbbb",
        "needs: --delete reflog",
    ]
    # The JSON keeps the one line the prune skill reads.
    assert fact.text.endswith(
        "see each with git show aaaaaaaaaaaa and git show bbbbbbbbbbbb"
    )


@pytest.mark.parametrize(
    "text, room, kept, width",
    [
        ("abc", 2, "ab", 3),
        ("分支名", 4, "分支", 6),
        ("分支名", 5, "分支", 6),
        ("a分", 2, "a", 3),
        ("éx", 1, "é", 2),
        ("", 3, "", 0),
    ],
)
def test_text_is_cut_by_the_columns_it_takes(text, room, kept, width) -> None:
    """A wide character takes two columns and a combining mark none, so a
    line cut by `len` to the terminal's width wraps, and the redraw that
    counts lines leaves a stale one behind."""
    assert render.fit(text, room) == kept
    assert render.width(text) == width
