"""Turning data into the text a terminal shows.

Colour where something will render it, and columns that line up.

Colour is off until `setup()` decides, per stream. stdout carries the data
`cd $(gwa x)` and `jq` read, so a redirect, a pipe, a non-empty `NO_COLOR` or
`TERM=dumb` all mean the bytes go out exactly as they would have without this
module.

Non-empty is the no-color.org rule, and the reason `supported` tests the value
rather than the key: `NO_COLOR=` unsets the request instead of making it.
"""

from __future__ import annotations

import os
import sys
from typing import IO

RESET = "\033[0m"
BOLD = "\033[1m"
DIM = "\033[2m"
RED = "\033[31m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
BLUE = "\033[34m"


def supported(stream: IO[str]) -> bool:
    """Would colour on this stream reach something that renders it?"""
    if os.environ.get("NO_COLOR"):
        return False
    if os.environ.get("TERM", "") == "dumb":
        return False
    try:
        return stream.isatty()
    except (AttributeError, ValueError):
        return False


class Ink:
    """Wrap text in a code, or hand it back untouched when colour is off."""

    def __init__(self, on: bool = False) -> None:
        self.on = on

    def __call__(self, text: str, code: str) -> str:
        return f"{code}{text}{RESET}" if self.on and code and text else text


out = Ink()
err = Ink()


def setup() -> None:
    """Decide once, per stream, before anything prints."""
    out.on = supported(sys.stdout)
    err.on = supported(sys.stderr)


class Cell:
    """One cell. The width comes from the text, the code paints it after."""

    __slots__ = ("code", "text")

    text: str
    code: str

    def __init__(self, text: str, code: str = "") -> None:
        self.text = text
        self.code = code


Row = tuple["str | Cell", ...]


def table(rows: list[Row]) -> str:
    """Columns wide enough for their content, the last one unpadded.

    Padding runs on the text and painting after it. The other order counts an
    escape sequence as width, and every column under a coloured cell sits
    crooked by exactly the length of the code.
    """
    if not rows:
        return ""
    grid = [[c if isinstance(c, Cell) else Cell(c) for c in row] for row in rows]
    widths = [max(len(r[i].text) for r in grid) for i in range(len(grid[0]))]
    lines = []
    for row in grid:
        cells = [out(c.text.ljust(widths[i]), c.code) for i, c in enumerate(row[:-1])]
        lines.append("  ".join([*cells, out(row[-1].text, row[-1].code)]).rstrip())
    return "\n".join(lines)
