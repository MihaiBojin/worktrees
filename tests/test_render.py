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
