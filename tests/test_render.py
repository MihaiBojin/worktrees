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
    text = "x is at origin/main (1e177cf)\nnothing to do (--no-fetch)"
    assert render.fail(text) == text
    assert render.warn(text) == text
    assert render.note(text) == text


def test_a_failure_leads_with_a_symbol_and_dims_its_detail(stderr_colour) -> None:
    first, second = render.fail("x is at origin/main (1e177cf)\nwhy").split("\n")
    assert first.startswith(f"{render.BOLD}{render.RED}✗{render.RESET} x is at")
    assert f"{render.YELLOW}1e177cf{render.RESET}" in first
    assert second == f"  {render.DIM}why{render.RESET}"


@pytest.mark.parametrize(
    ("text", "painted"),
    [
        ("pass --force-refresh", "--force-refresh"),
        ("pass -f", "-f"),
        ("'worktrees new-branch --x' names one", "'worktrees new-branch --x'"),
    ],
)
def test_flags_and_quoted_commands_are_cyan(stderr_colour, text, painted) -> None:
    assert f"{render.CYAN}{painted}{render.RESET}" in render.warn(text)


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
