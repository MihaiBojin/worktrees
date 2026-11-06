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
MAGENTA = "\033[35m"
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
    """One cell. The width comes from the text, the code paints it after.

    `Cell.of` builds one from pieces, each painted its own way, for a cell
    such as `gwa NAME [BASE]` whose command and arguments differ.
    """

    __slots__ = ("code", "parts", "text")

    text: str
    code: str
    parts: tuple[tuple[str, str], ...]

    def __init__(self, text: str, code: str = "") -> None:
        self.text = text
        self.code = code
        self.parts = ()

    @classmethod
    def of(cls, *parts: tuple[str, str]) -> Cell:
        cell = cls("".join(text for text, _ in parts))
        cell.parts = parts
        return cell

    def painted(self, width: int, ink: Ink | None = None) -> str:
        ink = ink or out
        if not self.parts:
            return ink(self.text.ljust(width), self.code)
        pad = " " * (width - len(self.text))
        return "".join(ink(text, code) for text, code in self.parts) + pad


Row = tuple["str | Cell", ...]


def table(rows: list[Row], ink: Ink | None = None) -> str:
    """Columns wide enough for their content, the last one unpadded.

    Painted for stdout unless `ink` is `err`, for a table bound for stderr.

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
        cells = [c.painted(widths[i], ink) for i, c in enumerate(row[:-1])]
        lines.append("  ".join([*cells, row[-1].painted(0, ink)]).rstrip())
    return "\n".join(lines)


# --------------------------------------------------------------------------
# messages on stderr
# --------------------------------------------------------------------------


# What each verdict prints in, in `gwp` and `gwbs` alike, and in the
# counts under them. A branch is the same bold magenta it is on stderr.
VERDICT = {"remove": GREEN, "keep": BLUE, "unknown": YELLOW}
BRANCH = BOLD + MAGENTA
VERDICTS = tuple(VERDICT)


def counted(paint: Ink, rows: list) -> str:
    """`2 eligible, 1 kept, 0 unclear`, each number in its verdict's colour,
    painted by `out` or `err` for the stream it goes to.

    `rows` carry `verdict` and `eligible`. A protected `remove` row is kept,
    so the first number is what the command would remove.
    """
    eligible = sum(1 for v in rows if v.eligible)
    unclear = sum(1 for v in rows if v.verdict == "unknown")
    return ", ".join(
        (
            f"{paint(str(eligible), VERDICT['remove'])} eligible",
            f"{paint(str(len(rows) - eligible - unclear), VERDICT['keep'])} kept",
            f"{paint(str(unclear), VERDICT['unknown'])} unclear",
        )
    )


def branch(name: str) -> str:
    """A local branch, in a message bound for stderr."""
    return err(name, BRANCH)


def ref(name: str) -> str:
    """A remote, or a ref a branch is measured against: origin/main."""
    return err(name, BLUE)


def command(text: str) -> str:
    """A command line quoted in a message: bold, as one piece, in whatever
    colour the line around it has."""
    return err(text, BOLD)


def restore(name: str, sha: str) -> str:
    """`git branch NAME SHA`, the line that puts a branch back: quoted to
    paste, and painted the way a table paints a branch and a sha."""
    return f"{command('git branch')} {branch(shlex.quote(name))} {err(sha, YELLOW)}"


def ask(question: str, hint: str) -> str:
    """A prompt on stderr: the question bold, the answers it takes dim."""
    return f"{err(question, BOLD)} {err(hint, DIM)} "


# Text `branch`, `ref` or `command` already painted, left as it is; then a
# quoted command, a path, a flag, or a sha with at least one digit, so a word
# like `deadbeef` in prose stays prose. Quoted before flag: a flag inside one
# stays part of the command it belongs to. A path is a word that starts with
# `/`, `~/`, `./` or `../`, dim as in a table, and taken whole before the flag
# and sha passes, which would otherwise find both inside it. Commands and flags
# are bold rather than a colour, so they read as part of the line they sit
# in: red in a failure.
_TOKEN = re.compile(
    r"(?P<painted>\033\[[\d;]*m.*?\033\[0m)"
    r"|(?P<quoted>'[^'\n]+')"
    r"|(?P<path>(?<![^\s(])(?:~|\.{1,2})?/[^\s'\"),;:]*)"
    r"|(?P<flag>(?<![\w-])--?[A-Za-z][\w-]*)"
    r"|(?P<sha>\b(?=[0-9a-f]*\d)[0-9a-f]{7,40}\b)"
)
_TOKEN_CODE = {"quoted": BOLD, "path": DIM, "flag": BOLD, "sha": YELLOW}


def _tokens(text: str) -> str:
    def paint(m: re.Match[str]) -> str:
        if m.lastgroup == "painted":
            return m.group()
        return err(m.group(), _TOKEN_CODE[m.lastgroup or ""])

    return _TOKEN.sub(paint, text)


def _message(
    symbol: str, code: str, text: str, base: str = "", detail: str = DIM
) -> str:
    """The symbol, the first line as the headline in `base`, any further
    lines in `detail`.

    A painted name inside the headline ends with a reset, so `base` is put
    back after each one; without that, the words after a branch name lose
    the line's colour.

    Plain text when stderr is not a terminal: the symbol is decoration, and a
    log or a pipe gets the words as they were.
    """
    if not err.on:
        return text
    head, *rest = text.split("\n")
    head = _tokens(head)
    if base:
        head = base + head.replace(RESET, RESET + base) + RESET
    lines = [f"{err(symbol, BOLD + code)} {head}"]
    lines += [f"  {err(line, detail)}" for line in rest]
    return "\n".join(lines)


def fail(text: str) -> str:
    """Why nothing was done. Every line of it is red: it is the error."""
    return _message("✗", RED, text, base=RED, detail=RED)


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
