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
import re
import shlex
import sys
from collections.abc import Sequence
from typing import IO

RESET = "\033[0m"
BOLD = "\033[1m"
DIM = "\033[2m"
REVERSE = "\033[7m"
RED = "\033[31m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
BLUE = "\033[34m"
CYAN = "\033[36m"


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


# --------------------------------------------------------------------------
# messages on stderr
# --------------------------------------------------------------------------

# A quoted command, a flag, or a sha with at least one digit, so a word like
# `deadbeef` in prose stays prose. Quoted first: a flag inside one stays part
# of the command it belongs to.
_TOKEN = re.compile(
    r"(?P<quoted>'[^'\n]+')"
    r"|(?P<flag>(?<![\w-])--?[A-Za-z][\w-]*)"
    r"|(?P<sha>\b(?=[0-9a-f]*\d)[0-9a-f]{7,40}\b)"
)
_TOKEN_CODE = {"quoted": CYAN, "flag": CYAN, "sha": YELLOW}


def _tokens(text: str) -> str:
    return _TOKEN.sub(lambda m: err(m.group(), _TOKEN_CODE[m.lastgroup or ""]), text)


def _message(symbol: str, code: str, text: str) -> str:
    """The symbol, the first line as the headline, any further lines dim.

    Plain text when stderr is not a terminal: the symbol is decoration, and a
    log or a pipe gets the words as they were.
    """
    if not err.on:
        return text
    head, *rest = text.split("\n")
    lines = [f"{err(symbol, BOLD + code)} {_tokens(head)}"]
    lines += [f"  {err(line, DIM)}" for line in rest]
    return "\n".join(lines)


def fail(text: str) -> str:
    """Why nothing was done."""
    return _message("✗", RED, text)


def warn(text: str) -> str:
    """Something done differently from how it was asked, or might be stale."""
    return _message("!", YELLOW, text)


def note(text: str) -> str:
    """What the command is doing on the caller's behalf."""
    return _message("→", CYAN, text)


# Enough of `git worktree list` or a forge answer to see its shape.
_TRACE_LINES = 20


def trace(
    argv: Sequence[str], code: int, stdout: str, stderr: str, failed: bool
) -> str:
    """One command for -v: the line that pastes back, what it printed, its
    exit code, and a blank line before the next.

    The exit code is shown when it is not 0 or when nothing was printed,
    since for `show-ref --quiet` and its kind the code is the whole answer.
    """
    # NUL-separated output (`-z`) reads one record to a line.
    printed = [ln for ln in stdout.replace("\0", "\n").splitlines() if ln]
    errors = [ln for ln in stderr.splitlines() if ln]
    lines = [f"{err('+', DIM)} {err(shlex.join(argv), BOLD)}"]
    shown = printed[:_TRACE_LINES]
    lines += [f"  {err(ln, DIM)}" for ln in shown]
    if len(printed) > len(shown):
        lines.append(f"  {err(f'... {len(printed) - len(shown)} more', DIM)}")
    lines += [f"  {err(ln, RED if failed else DIM)}" for ln in errors]
    if code or not (printed or errors):
        lines.append(f"  {err(f'exit {code}', RED if failed else DIM)}")
    return "\n".join(lines) + "\n"
